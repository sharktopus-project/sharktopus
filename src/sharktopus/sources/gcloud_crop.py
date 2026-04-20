"""GCloud cloud-side cropping via the ``sharktopus`` Cloud Run service.

This is the Cloud Run analogue of :mod:`sharktopus.sources.aws_crop`:
a small HTTPS POST fires the server, which fetches byte-ranges from
``gs://global-forecast-system``, runs wgrib2 crop, and returns the
cropped bytes. The client only transfers the already-cropped GRIB2 —
typically 50-500 KB instead of 500 MB.

Two delivery modes, selected server-side:

``inline``
    Cloud Run base64-encodes the cropped file and ships it in the
    JSON response body. Cloud Run allows up to 32 MB response bodies,
    so inline covers everything short of a continent-scale bbox.

``gcs``
    For very large crops, the server uploads to a short-lived
    object on a GCS bucket and returns a V4-signed GET URL. The
    client downloads from there and deletes the object afterwards
    (lifecycle policy on the bucket is the backstop).

Policy gates (all in :mod:`sharktopus.cloud.gcloud_quota`):

* ``SHARKTOPUS_LOCAL_CROP=true`` — skip cloud-crop entirely.
* Free tier exhausted + ``SHARKTOPUS_ACCEPT_CHARGES`` unset →
  :class:`SourceUnavailable` so the orchestrator falls back to
  :mod:`sharktopus.sources.gcloud` (full download + local crop).

Authentication: Cloud Run services default to requiring an ID token.
We sign the request with the caller's Application Default Credentials
(``gcloud auth application-default login``) when available, or fall
back to unauthenticated if the service was deployed with
``--allow-unauthenticated`` (``provision.py`` sets that by default so
a fresh-install user can invoke without extra setup).

The service URL is discovered in this order:

1. Explicit ``service_url=`` argument to :func:`fetch_step`.
2. ``SHARKTOPUS_GCLOUD_URL`` env var.
3. Cloud Run Admin API lookup for the default service name
   (``sharktopus-crop``) in the configured project/region.

Any failure in discovery → ``SourceUnavailable`` (the orchestrator
then falls back to the next priority entry).
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..cloud import gcloud_quota
from ..io import grib, paths
from .base import (
    SourceUnavailable,
    canonical_filename,
    stream_download,
    supports_date,
    validate_cycle,
    validate_date,
)

__all__ = [
    "DEFAULT_SERVICE_NAME",
    "DEFAULT_REGION",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "fetch_step",
    "have_credentials",
    "supports",
]


DEFAULT_SERVICE_NAME = "sharktopus-crop"
DEFAULT_REGION = "us-central1"

# Same rationale as aws_crop: Cloud Run scales out well, the client-side
# cap just bounds concurrent invocations this one process triggers. The
# GCS mirror prefers ≤ 4 parallel clients across a single IP.
DEFAULT_MAX_WORKERS = 4

# GCS mirror ``global-forecast-system`` tracks back to early 2021.
EARLIEST: datetime | None = datetime(2021, 1, 1, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if the Cloud Run crop service can serve *date*.

    Three checks: date window, a discoverable service URL, and that
    ``requests`` can actually be imported (lazy — we keep the package
    usable without it).
    """
    if not supports_date(date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now):
        return False
    return _have_requests() and (_service_url_from_env() is not None or have_credentials())


def _have_requests() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _import_requests():
    """Lazy-import requests with a SourceUnavailable if missing."""
    try:
        import requests
        return requests
    except ImportError as e:
        raise SourceUnavailable(
            "gcloud_crop requires requests (pip install 'sharktopus[gcloud]')"
        ) from e


def have_credentials() -> bool:
    """Return ``True`` if google-auth can resolve ADC right now.

    Optional: when the Cloud Run service is deployed with
    ``--allow-unauthenticated`` (our default), callers don't need
    credentials. This predicate is used for auto-priority only and
    should be truthy whenever we expect the source to succeed.
    """
    if _service_url_from_env():
        return True
    try:
        import google.auth  # noqa: F401
        from google.auth import default as _default  # noqa: F401
        return True
    except ImportError:
        return False


def _service_url_from_env() -> str | None:
    url = os.environ.get("SHARKTOPUS_GCLOUD_URL", "").strip()
    return url or None


def _discover_service_url(
    service_name: str, region: str, project: str | None,
) -> str | None:
    """Look up a Cloud Run service URL via the Admin API.

    Falls back to ``None`` if google-auth / google-cloud-run aren't
    installed or the service isn't reachable — the caller then raises
    :class:`SourceUnavailable` with context.
    """
    try:
        from google.auth import default as adc_default
        from google.cloud import run_v2
    except ImportError:
        return None

    try:
        creds, auto_project = adc_default()
        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or auto_project
        if not project:
            return None
        client = run_v2.ServicesClient(credentials=creds)
        name = f"projects/{project}/locations/{region}/services/{service_name}"
        svc = client.get_service(name=name)
        return svc.uri or None
    except Exception:
        return None


def _id_token_for(audience: str) -> str | None:
    """Mint an OIDC ID token for *audience* (= the Cloud Run URL).

    Cloud Run authenticated endpoints expect ``Authorization: Bearer
    <id_token>`` where the token is audience-scoped. Returns ``None``
    when no credential source works; the caller then sends the request
    unauthenticated, which only works against ``--allow-unauthenticated``
    services.

    Resolution order:

    1. ``SHARKTOPUS_GCLOUD_ID_TOKEN`` env var — explicit override for
       CI / containerized envs where neither ADC nor gcloud CLI is set.
    2. ``google.oauth2.id_token.fetch_id_token`` — works with the
       metadata server (GCE / Cloud Run / GKE) and with service-account
       ADC. This is the common production path.
    3. ``gcloud auth print-identity-token`` — required fallback for
       **user-type ADC** (`gcloud auth application-default login`),
       which the ``google-auth`` library cannot mint ID tokens for.
       Typical dev / laptop scenario.
    """
    import os
    override = os.environ.get("SHARKTOPUS_GCLOUD_ID_TOKEN")
    if override:
        return override.strip()

    try:
        import google.auth.transport.requests as gat
        from google.oauth2 import id_token as id_token_mod

        req = gat.Request()
        return id_token_mod.fetch_id_token(req, audience)
    except Exception:
        pass

    try:
        import shutil
        import subprocess
        gcloud = shutil.which("gcloud")
        if not gcloud:
            return None
        r = subprocess.run(
            [gcloud, "auth", "print-identity-token",
             f"--audiences={audience}"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            tok = r.stdout.strip()
            return tok or None
        # Older gcloud or user-creds that reject --audiences — retry without.
        r = subprocess.run(
            [gcloud, "auth", "print-identity-token"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            tok = r.stdout.strip()
            return tok or None
    except Exception:
        pass
    return None


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
    gcs_bucket: str | None,
    gcs_expires_s: int,
) -> dict:
    """Compose the JSON body the Cloud Run service expects.

    Shape mirrors :mod:`sharktopus.sources.aws_crop` so the two handlers
    can share validation helpers.
    """
    validate_cycle(cycle)
    validate_date(date)
    payload: dict = {
        "date": date,
        "cycle": cycle,
        "fxx": int(fxx),
        "product": product,
        "response_mode": response_mode,
        "gcs_expires_s": int(gcs_expires_s),
    }
    if bbox is not None:
        lon_w, lon_e, lat_s, lat_n = grib.expand_bbox(bbox, pad_lon=pad_lon, pad_lat=pad_lat)
        payload["bbox"] = {
            "lon_w": lon_w, "lon_e": lon_e,
            "lat_s": lat_s, "lat_n": lat_n,
        }
    if variables:
        payload["variables"] = list(variables)
    if levels:
        payload["levels"] = list(levels)
    if gcs_bucket:
        payload["gcs_bucket"] = gcs_bucket
    return payload


def _retain_gcs() -> bool:
    return os.environ.get("SHARKTOPUS_RETAIN_GCS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _materialize_response(body: dict, final: Path, *, timeout: float) -> None:
    """Write the crop bytes referenced by *body* to *final*.

    Handles both ``inline`` (base64 in response) and ``gcs`` (signed
    URL) modes. After a successful gcs download, deletes the object
    unless ``SHARKTOPUS_RETAIN_GCS=true``.
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

    if mode == "gcs":
        url = body.get("gcs_url")
        if not url:
            raise SourceUnavailable("gcs response missing 'gcs_url'")
        stream_download(url, part, timeout=timeout, max_retries=3)
        part.replace(final)
        if not _retain_gcs():
            _delete_gcs(body.get("gcs_bucket"), body.get("gcs_key"))
        return

    raise SourceUnavailable(f"unknown response mode: {mode!r}")


def _delete_gcs(bucket: str | None, key: str | None) -> None:
    """Best-effort delete of the Cloud Run scratch object."""
    if not bucket or not key:
        return
    try:
        from google.cloud import storage
        client = storage.Client()
        client.bucket(bucket).blob(key).delete()
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
    service_name: str = DEFAULT_SERVICE_NAME,
    region: str = DEFAULT_REGION,
    project: str | None = None,
    service_url: str | None = None,
    response_mode: str = "auto",
    gcs_bucket: str | None = None,
    gcs_expires_s: int = 24 * 3600,
    timeout: float = 900.0,
    verify: bool = True,
    wgrib2: str | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,  # noqa: ARG001 — API parity
    max_retries: int = 3,  # noqa: ARG001 — API parity
    retry_wait: float = 10.0,  # noqa: ARG001 — API parity
    deadline: float | None = None,  # noqa: ARG001 — enforced by service timeout
) -> Path:
    """POST to the Cloud Run service and materialise the cropped GRIB2.

    Quota-gated via :func:`sharktopus.cloud.gcloud_quota.can_use_cloud_crop`.
    Raises :class:`SourceUnavailable` when the free tier is exhausted
    and the user hasn't authorised paid spend — the batch orchestrator
    then falls back to :mod:`sharktopus.sources.gcloud` (full download
    + local crop, no Cloud Run cost).
    """
    allowed, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    if not allowed:
        raise SourceUnavailable(f"gcloud_crop policy gate: {reason}")

    requests = _import_requests()

    url = service_url or _service_url_from_env()
    if not url:
        url = _discover_service_url(service_name, region, project)
    if not url:
        raise SourceUnavailable(
            f"gcloud_crop could not resolve service URL for {service_name!r} "
            f"in {region!r}. Set SHARKTOPUS_GCLOUD_URL or ensure "
            f"`gcloud run services describe` would succeed."
        )

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
        gcs_bucket=gcs_bucket, gcs_expires_s=gcs_expires_s,
    )

    headers = {"Content-Type": "application/json"}
    token = _id_token_for(url)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    start = time.monotonic()
    duration_s: float | None = None
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        duration_s = time.monotonic() - start
    except requests.RequestException as e:
        _record_best_effort(None)
        raise SourceUnavailable(f"cloud run invoke failed: {e}") from e

    if resp.status_code >= 400:
        _record_best_effort(duration_s)
        body_preview = resp.text[:500] if resp.text else "<empty>"
        raise SourceUnavailable(
            f"cloud run error ({resp.status_code}): {body_preview}"
        )

    try:
        envelope = resp.json()
    except ValueError as e:
        _record_best_effort(duration_s)
        raise SourceUnavailable(f"cloud run returned non-JSON: {e}") from e

    inner = envelope.get("body") if isinstance(envelope.get("body"), dict) else envelope
    try:
        _materialize_response(inner, final, timeout=timeout)
    finally:
        # Prefer the duration the server reports (matches billing
        # resolution); fall back to our wall-clock measurement.
        billed_ms = inner.get("billed_duration_ms") if isinstance(inner, dict) else None
        if isinstance(billed_ms, (int, float)):
            duration_s = float(billed_ms) / 1000.0
        _record_best_effort(duration_s)

    _verify_or_raise(final, verify=verify, wgrib2=wgrib2)
    return final


def _record_best_effort(duration_s: float | None) -> None:
    """Persist an invocation against the quota counter; swallow errors."""
    try:
        gcloud_quota.record_invocation("gcloud", duration_s=duration_s)
    except Exception:
        pass


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
        raise SourceUnavailable(f"gcloud_crop output unparseable: {e}") from e
    if n <= 0:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable("gcloud_crop output has no records")
