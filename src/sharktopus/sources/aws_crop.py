"""AWS cloud-side cropping via the ``sharktopus`` Lambda.

This is the fast path: the Lambda (deployed as the ``sharktopus``
function) fetches the byte-range GRIB2 from ``noaa-gfs-bdp-pds``,
crops to the requested bbox/variables/levels, and returns the
resulting file. The client only transfers the already-cropped bytes —
typically 50-500 KB instead of 500 MB.

Two delivery modes, selected automatically:

``inline``
    Lambda base64-encodes the cropped file and includes it in the
    invocation response (``body.b64``). No S3 round-trip. Synchronous
    Lambda responses are capped at ~6 MB of JSON (~4.5 MB binary); any
    crop smaller than that travels inline. This covers the typical
    "small nest" use case end-to-end.

``s3``
    For larger crops, Lambda uploads to a short-lived prefix on an
    internal S3 bucket, returns a presigned GET URL, and the client
    downloads from there. After verification the client deletes the
    object immediately (even if the bucket lifecycle would eventually
    purge it — we only retain when ``SHARKTOPUS_RETAIN_S3=true``).

Policy gates (all in :mod:`sharktopus.aws_quota`):

* ``SHARKTOPUS_LOCAL_CROP=true`` — skip cloud-crop entirely.
* Free tier exhausted + ``SHARKTOPUS_ACCEPT_CHARGES`` unset — raises
  :class:`~sharktopus.sources.base.SourceUnavailable` so the
  orchestrator falls back to :mod:`sharktopus.sources.aws` (byte-range
  + local crop, no Lambda call, no cost).
* ``SHARKTOPUS_ACCEPT_CHARGES=true`` + ``SHARKTOPUS_MAX_SPEND_USD=N``
  authorises paid usage up to $N this month.

Credentials come from the standard AWS resolution chain (env vars,
``~/.aws/credentials``, EC2 instance profile). No credentials →
``SourceUnavailable`` at invocation time — :mod:`sharktopus.batch`
filters this source out of the priority list automatically when
``available_sources()`` reports it as offline.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .. import aws_quota, grib, paths
from .base import (
    SourceUnavailable,
    canonical_filename,
    stream_download,
    supports_date,
    validate_cycle,
    validate_date,
)

__all__ = [
    "DEFAULT_LAMBDA_NAME",
    "DEFAULT_REGION",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "fetch_step",
    "have_credentials",
    "supports",
]


DEFAULT_LAMBDA_NAME = "sharktopus"
DEFAULT_REGION = "us-east-1"

# Concurrency for the batch orchestrator. Lambda itself scales out, so
# this bounds only how many concurrent invocations one client triggers.
# AWS default account concurrency is 1000; 4 parallel crops is a safe
# default that mirrors the other AWS source and leaves room for other
# users of the account.
DEFAULT_MAX_WORKERS = 4

# Mirror the plain aws source — same bucket, same coverage.
EARLIEST: datetime | None = datetime(2021, 2, 27, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if the AWS crop Lambda is reachable for *date*.

    Two conjoint checks — date window (same coverage as the plain
    :mod:`~sharktopus.sources.aws` mirror) **and** credential presence.
    The credential check lets :func:`sharktopus.batch.available_sources`
    drop this source from auto-priority on machines where AWS isn't
    configured, instead of burning one failed invocation per batch.
    """
    if not supports_date(date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now):
        return False
    return have_credentials()


def _import_boto3():
    """Lazy-import boto3 so the package stays usable without it.

    Raises :class:`SourceUnavailable` (not :class:`ImportError`) so the
    batch orchestrator treats a missing-boto3 install like any other
    unavailable mirror.
    """
    try:
        import boto3  # noqa: F401
        return boto3
    except ImportError as e:
        raise SourceUnavailable(
            "aws_crop requires boto3 (pip install 'sharktopus[aws]')"
        ) from e


def have_credentials() -> bool:
    """Return ``True`` if boto3 can resolve credentials right now.

    Used by :mod:`sharktopus.batch` to filter ``aws_crop`` out of the
    auto-priority list when the user hasn't configured AWS. Falsey on
    any failure (missing boto3, no credentials file, no env vars,
    expired session) — never raises.
    """
    try:
        boto3 = _import_boto3()
    except SourceUnavailable:
        return False
    try:
        session = boto3.Session()
        return session.get_credentials() is not None
    except Exception:
        return False


def _build_payload(
    date: str,
    cycle: str,
    fxx: int,
    *,
    bbox: grib.Bbox | None,
    pad_lon: float,
    pad_lat: float,
    variables: Sequence[str] | None,
    levels: Sequence[str] | None,
    product: str,
    response_mode: str,
    s3_bucket: str | None,
    s3_expires_s: int,
) -> dict:
    """Compose the JSON event payload the ``sharktopus`` Lambda expects."""
    validate_cycle(cycle)
    validate_date(date)
    payload: dict = {
        "date": date,
        "cycle": cycle,
        "fxx": int(fxx),
        "product": product,
        "response_mode": response_mode,
        "s3_expires_s": int(s3_expires_s),
    }
    if bbox is not None:
        expanded = grib.expand_bbox(bbox, pad_lon=pad_lon, pad_lat=pad_lat)
        lon_w, lon_e, lat_s, lat_n = expanded
        payload["bbox"] = {
            "lon_w": lon_w, "lon_e": lon_e,
            "lat_s": lat_s, "lat_n": lat_n,
        }
    if variables:
        payload["variables"] = list(variables)
    if levels:
        payload["levels"] = list(levels)
    if s3_bucket:
        payload["s3_bucket"] = s3_bucket
    return payload


def _retain_s3() -> bool:
    return os.environ.get("SHARKTOPUS_RETAIN_S3", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _materialize_response(
    body: dict,
    final: Path,
    *,
    region: str,
    timeout: float,
) -> None:
    """Write the crop bytes referenced by *body* to *final*.

    Handles both ``inline`` (base64 in response) and ``s3`` (presigned
    URL) modes. On ``s3`` mode, deletes the source object after the
    download succeeds unless ``SHARKTOPUS_RETAIN_S3=true``.
    """
    mode = body.get("mode", "inline")
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + ".part")
    if mode == "inline":
        raw = body.get("b64")
        if not raw:
            raise SourceUnavailable("inline response missing 'b64' payload")
        part.write_bytes(base64.b64decode(raw))
        part.replace(final)
        return
    if mode == "s3":
        url = body.get("s3_url")
        if not url:
            raise SourceUnavailable("s3 response missing 's3_url'")
        stream_download(url, part, timeout=timeout, max_retries=3)
        part.replace(final)
        if not _retain_s3():
            _delete_s3(body.get("s3_bucket"), body.get("s3_key"), region=region)
        return
    raise SourceUnavailable(f"unknown response mode: {mode!r}")


def _delete_s3(bucket: str | None, key: str | None, *, region: str) -> None:
    """Best-effort delete — silent on failure.

    Lifecycle rules on the output bucket are the backstop; this just
    keeps the prefix clean on the happy path.
    """
    if not bucket or not key:
        return
    try:
        boto3 = _import_boto3()
        s3 = boto3.client("s3", region_name=region)
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


def fetch_step(
    date: str,
    cycle: str,
    fxx: int,
    *,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    bbox: grib.Bbox | None = None,
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    product: str = "pgrb2.0p25",
    variables: Sequence[str] | None = None,
    levels: Sequence[str] | None = None,
    lambda_name: str = DEFAULT_LAMBDA_NAME,
    region: str = DEFAULT_REGION,
    response_mode: str = "auto",
    s3_bucket: str | None = None,
    s3_expires_s: int = 24 * 3600,
    timeout: float = 900.0,
    verify: bool = True,
    wgrib2: str | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,  # noqa: ARG001 — API parity
    max_retries: int = 3,  # noqa: ARG001 — API parity
    retry_wait: float = 10.0,  # noqa: ARG001 — API parity
    deadline: float | None = None,  # noqa: ARG001 — deadline enforced by Lambda timeout
) -> Path:
    """Invoke the ``sharktopus`` Lambda and materialise the cropped GRIB2.

    Quota-gated: consults :func:`sharktopus.aws_quota.can_use_cloud_crop`
    before invoking. Raises :class:`SourceUnavailable` when the free
    tier is exhausted and the user hasn't authorised paid spend —
    letting the batch orchestrator fall back to
    :mod:`sharktopus.sources.aws` (local crop, no Lambda cost).

    *response_mode* is ``"auto"`` (Lambda picks inline for small crops,
    s3 otherwise), ``"inline"`` (force inline, may fail on large crops),
    or ``"s3"`` (always upload, useful for very large areas).
    """
    allowed, reason = aws_quota.can_use_cloud_crop("aws")
    if not allowed:
        raise SourceUnavailable(f"aws_crop policy gate: {reason}")

    boto3 = _import_boto3()

    if dest is None:
        dest_dir = paths.output_dir(
            date=date, cycle=cycle, bbox=bbox, mode="fcst", root=root,
        )
    else:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / canonical_filename(cycle, fxx, product=product)

    payload = _build_payload(
        date, cycle, fxx,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        variables=variables, levels=levels,
        product=product, response_mode=response_mode,
        s3_bucket=s3_bucket, s3_expires_s=s3_expires_s,
    )

    try:
        from botocore.config import Config
        client = boto3.client(
            "lambda",
            region_name=region,
            config=Config(read_timeout=timeout, retries={"max_attempts": 2}),
        )
        resp = client.invoke(
            FunctionName=lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
    except Exception as e:
        raise SourceUnavailable(f"lambda invoke failed: {e}") from e

    raw = resp.get("Payload")
    body_bytes = raw.read() if hasattr(raw, "read") else (raw or b"")
    try:
        body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except json.JSONDecodeError as e:
        raise SourceUnavailable(f"lambda returned non-JSON: {e}") from e

    if resp.get("FunctionError") or body.get("statusCode", 200) >= 400:
        err = body.get("body") if isinstance(body.get("body"), dict) else body
        msg = err.get("error") if isinstance(err, dict) else str(err)
        _record_invocation_best_effort(resp, body, payload)
        raise SourceUnavailable(f"lambda error: {msg}")

    inner = body.get("body") if isinstance(body.get("body"), dict) else body
    try:
        _materialize_response(inner, final, region=region, timeout=timeout)
    finally:
        _record_invocation_best_effort(resp, body, payload)

    _verify_or_raise(final, verify=verify, wgrib2=wgrib2)
    return final


def _record_invocation_best_effort(resp: dict, body: dict, payload: dict) -> None:
    """Persist the invocation against the local quota counter.

    Pulls billed duration / memory from the Lambda response where
    possible; falls back to the running average kept in the quota
    state. Silent on any failure — we never want counter bookkeeping
    to break the crop.
    """
    duration_s: float | None = None
    memory_mb: int | None = None
    inner = body.get("body") if isinstance(body.get("body"), dict) else body
    if isinstance(inner, dict):
        billed_ms = inner.get("billed_duration_ms")
        if isinstance(billed_ms, (int, float)):
            duration_s = float(billed_ms) / 1000.0
        mem = inner.get("memory_mb")
        if isinstance(mem, (int, float)):
            memory_mb = int(mem)
    if duration_s is None:
        log_tail = resp.get("LogResult")
        duration_s = _parse_billed_duration_ms(log_tail) if log_tail else None
        if duration_s is not None:
            duration_s /= 1000.0
    try:
        aws_quota.record_invocation(
            "aws", duration_s=duration_s, memory_mb=memory_mb,
        )
    except Exception:
        pass


def _parse_billed_duration_ms(log_tail_b64: str) -> float | None:
    """Extract ``Billed Duration: N ms`` from a base64 Lambda log tail."""
    try:
        text = base64.b64decode(log_tail_b64).decode("utf-8", errors="replace")
    except Exception:
        return None
    for line in text.splitlines():
        if "Billed Duration" in line:
            # "REPORT RequestId: ... Billed Duration: 1234 ms ..."
            for token in line.split("\t"):
                token = token.strip()
                if token.startswith("Billed Duration:"):
                    try:
                        return float(token.split(":", 1)[1].strip().split()[0])
                    except (IndexError, ValueError):
                        return None
    return None


def _verify_or_raise(final: Path, *, verify: bool, wgrib2: str | None) -> None:
    if not verify or not grib.have_wgrib2(wgrib2):
        return
    try:
        n = grib.verify(final, wgrib2=wgrib2)
    except grib.GribError as e:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable(f"aws_crop output unparseable: {e}") from e
    if n <= 0:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable("aws_crop output has no records")
