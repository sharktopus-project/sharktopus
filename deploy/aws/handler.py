"""AWS Lambda handler for server-side GFS byte-range + wgrib2 crop.

Contract (matches ``sharktopus.sources.aws_crop._build_payload``):

Event shape (JSON)::

    {
      "date": "20260417",            # YYYYMMDD
      "cycle": "00",                  # HH
      "fxx": 0,                       # forecast hour
      "product": "pgrb2.0p25",
      "response_mode": "auto" | "inline" | "s3",
      "s3_expires_s": 86400,
      "bbox": {"lon_w": -50, "lon_e": -40, "lat_s": -10, "lat_n": 0},
      "variables": ["TMP", "UGRD", ...],   # optional filter
      "levels":    ["surface", ...],       # optional filter
      "s3_bucket": "my-bucket"             # optional override
    }

Response shape (always the API-Gateway-style envelope)::

    {
      "statusCode": 200,
      "body": {
        "mode": "inline",
        "b64": "<base64-encoded GRIB2>",
        "billed_duration_ms": 1234,
        "memory_mb": 512
      }
    }

or for large crops::

    {
      "statusCode": 200,
      "body": {
        "mode": "s3",
        "s3_url": "https://...",
        "s3_bucket": "...",
        "s3_key": "...",
        "billed_duration_ms": 1234,
        "memory_mb": 512
      }
    }

``response_mode="auto"`` picks inline when the cropped file is ≤ 4 MB
(Lambda's synchronous response payload caps at 6 MB JSON, ≈ 4.5 MB
binary once base64-encoded).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen

import boto3
from botocore import UNSIGNED
from botocore.client import Config

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SOURCE_BUCKET = "noaa-gfs-bdp-pds"
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")
DEFAULT_S3_BUCKET = os.environ.get("SHARKTOPUS_S3_BUCKET", "")
DEFAULT_S3_PREFIX = os.environ.get("SHARKTOPUS_S3_PREFIX", "crops/")
INLINE_SIZE_LIMIT = 4 * 1024 * 1024

# GFS product codes this handler is willing to serve. Defence in depth:
# this Lambda points at the public GFS bucket, so accepting any string as
# ``product`` would let a malicious payload point at unrelated keys. New
# models (HRRR, NAM, GEFS…) get their own Lambda + bucket + whitelist.
ALLOWED_PRODUCTS = frozenset({
    "pgrb2.0p25", "pgrb2b.0p25",
    "pgrb2.0p50", "pgrb2b.0p50",
    "pgrb2.1p00", "pgrb2b.1p00",
    "sfluxgrbf",
})

DOWNLOAD_WORKERS = int(os.environ.get("SHARKTOPUS_DOWNLOAD_WORKERS", "16"))


def lambda_handler(event, context):
    """Lambda entry point: delegate to :func:`_process` and wrap the response.

    Catches any exception from the pipeline and returns a 500 envelope
    rather than letting Lambda retry (which would double-bill).
    Measures wall time and attaches ``billed_duration_ms`` /
    ``memory_mb`` so callers can budget the free tier against their own
    telemetry.
    """
    start_ns = time.monotonic_ns()
    try:
        result = _process(event)
    except ValueError as e:
        logger.warning("rejecting event: %s", e)
        return {"statusCode": 400, "body": {"error": str(e), "type": "ValueError"}}
    except Exception as e:
        logger.exception("handler failed")
        return {"statusCode": 500, "body": {"error": str(e), "type": type(e).__name__}}

    elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
    result["billed_duration_ms"] = int(elapsed_ms)
    result["memory_mb"] = int(getattr(context, "memory_limit_in_mb", 0) or 0)
    return {"statusCode": 200, "body": result}


def _process(event: dict) -> dict:
    """Core pipeline: validate → find key → range-download → crop → package.

    Uses an unsigned S3 client against the public ``noaa-gfs-bdp-pds``
    bucket so no caller credentials leak into the source read path
    (the output path uses the Lambda's own role). Scratch files live
    under a ``TemporaryDirectory`` so /tmp is cleaned even on error.
    """
    date, cycle, fxx = _parse_required(event)
    product = event.get("product", "pgrb2.0p25")
    if product not in ALLOWED_PRODUCTS:
        raise ValueError(
            f"product {product!r} not allowed on this handler; "
            f"allowed: {sorted(ALLOWED_PRODUCTS)}"
        )
    response_mode = event.get("response_mode", "auto")
    variables = event.get("variables") or []
    levels = event.get("levels") or []
    bbox = event.get("bbox") or None

    s3_public = boto3.client(
        "s3",
        region_name=DEFAULT_REGION,
        config=Config(signature_version=UNSIGNED, retries={"max_attempts": 3}),
    )
    prefix = _detect_prefix(s3_public, date, cycle)
    key = f"{prefix}gfs.t{cycle}z.{product}.f{fxx:03d}"
    idx_url = f"https://{SOURCE_BUCKET}.s3.amazonaws.com/{key}.idx"

    with tempfile.TemporaryDirectory(prefix="shark_") as tmp:
        tmpd = Path(tmp)
        ranges, total_size = _pick_ranges(
            s3_public, key, idx_url, variables=variables, levels=levels,
        )
        raw_path = tmpd / "raw.grib2"
        _download_ranges(s3_public, key, ranges, raw_path)
        final_path = _crop(raw_path, bbox, tmpd) if bbox else raw_path
        size = final_path.stat().st_size
        logger.info(
            "crop result: %d bytes (input %d segments, raw %d bytes)",
            size, len(ranges), total_size,
        )

        use_inline = (response_mode == "inline") or (
            response_mode == "auto" and size <= INLINE_SIZE_LIMIT
        )
        if use_inline:
            return {
                "mode": "inline",
                "b64": base64.b64encode(final_path.read_bytes()).decode("ascii"),
            }
        return _upload_presign(final_path, event)


def _parse_required(event: dict) -> tuple[str, str, int]:
    """Validate ``date``/``cycle``/``fxx`` from the event.

    Fails fast with ``ValueError`` on the three shapes that routinely
    go wrong: non-digit date, bad YYYYMMDD length, cycle outside
    the 00/06/12/18 set. Everything else is left to downstream 404s
    (e.g. fxx that GFS doesn't publish for that cycle).
    """
    try:
        date = str(event["date"])
        cycle = str(event["cycle"]).zfill(2)
        fxx = int(event["fxx"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"missing/invalid required field: {e}")
    if len(date) != 8 or not date.isdigit():
        raise ValueError(f"date must be YYYYMMDD, got {date!r}")
    if cycle not in {"00", "06", "12", "18"}:
        raise ValueError(f"cycle must be 00/06/12/18, got {cycle!r}")
    return date, cycle, fxx


def _detect_prefix(s3, date: str, cycle: str) -> str:
    """Return the S3 key prefix for *date*/*cycle*, tolerating the legacy layout.

    Dates ≥ ~2021-03 live under ``gfs.YYYYMMDD/CC/atmos/``. Older dates
    use ``gfs.YYYYMMDD/CC/`` with no ``atmos`` subdir. A single
    ``list_objects_v2`` with MaxKeys=1 is enough to disambiguate.
    """
    atmos = f"gfs.{date}/{cycle}/atmos/"
    resp = s3.list_objects_v2(Bucket=SOURCE_BUCKET, Prefix=atmos, MaxKeys=1)
    if resp.get("Contents"):
        return atmos
    return f"gfs.{date}/{cycle}/"


def _pick_ranges(s3, key: str, idx_url: str, *, variables, levels):
    """Parse .idx, pick byte ranges matching variable/level filters.

    Returns (ranges, total_size) where ranges is [(start, end), ...]
    consolidated and sorted. An empty ``variables`` list selects all
    variables; same for ``levels``.
    """
    head = s3.head_object(Bucket=SOURCE_BUCKET, Key=key)
    total_size = int(head["ContentLength"])

    with urlopen(idx_url, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    var_set = set(variables) if variables else None
    lvl_set = set(levels) if levels else None

    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 5)
        if len(parts) < 6:
            continue
        rec_no = int(parts[0])
        offset = int(parts[1])
        var = parts[3]
        level = parts[4]
        entries.append((rec_no, offset, var, level))

    entries.sort(key=lambda e: e[0])
    offsets = {rec: off for rec, off, _, _ in entries}

    picks: list[tuple[int, int]] = []
    for i, (rec, off, var, level) in enumerate(entries):
        if var_set is not None and var not in var_set:
            continue
        if lvl_set is not None and level not in lvl_set:
            continue
        if i + 1 < len(entries):
            end = entries[i + 1][1] - 1
        else:
            end = total_size - 1
        picks.append((off, end))

    if not picks:
        raise ValueError("no records matched the variable/level filter")

    picks.sort()
    consolidated = [picks[0]]
    for s, e in picks[1:]:
        ps, pe = consolidated[-1]
        if s == pe + 1:
            consolidated[-1] = (ps, e)
        else:
            consolidated.append((s, e))
    logger.info(
        "filter matched %d records → %d byte segments (%d total bytes)",
        len(picks), len(consolidated),
        sum(e - s + 1 for s, e in consolidated),
    )
    return consolidated, total_size


def _download_ranges(s3, key: str, ranges, raw_path: Path):
    """Fetch each byte range in parallel and concatenate them into *raw_path*.

    S3 range-GETs are independent TCP connections; running them
    concurrently saturates the Lambda's egress link in a fraction of
    the single-threaded time. ``DOWNLOAD_WORKERS`` is tuned for the
    default 2048 MB memory class (which gets ~2 vCPU equivalents).
    """
    parts: list[bytes | None] = [None] * len(ranges)

    def fetch(i: int) -> None:
        s, e = ranges[i]
        resp = s3.get_object(Bucket=SOURCE_BUCKET, Key=key, Range=f"bytes={s}-{e}")
        parts[i] = resp["Body"].read()

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        futs = [ex.submit(fetch, i) for i in range(len(ranges))]
        for f in as_completed(futs):
            f.result()

    with raw_path.open("wb") as out:
        for chunk in parts:
            if chunk:
                out.write(chunk)


def _crop(raw_path: Path, bbox: dict, tmpd: Path) -> Path:
    """Run ``wgrib2 -small_grib`` on *raw_path* with the given bbox.

    ``-ncpu`` is set to the container's full CPU allocation (Lambda
    exposes more vCPU as you provision more memory). Returns the path
    to the cropped output under *tmpd*; caller removes the temp dir
    on handler exit.
    """
    lon_w = float(bbox["lon_w"])
    lon_e = float(bbox["lon_e"])
    lat_s = float(bbox["lat_s"])
    lat_n = float(bbox["lat_n"])
    out = tmpd / "cropped.grib2"
    cmd = [
        "wgrib2", str(raw_path),
        "-ncpu", str(os.cpu_count() or 1),
        "-small_grib", f"{lon_w}:{lon_e}", f"{lat_s}:{lat_n}",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"wgrib2 crop failed: {r.stderr.strip() or r.stdout.strip()}")
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("wgrib2 produced empty output")
    return out


def _upload_presign(final_path: Path, event: dict) -> dict:
    """Upload the cropped GRIB2 to S3 and return a presigned download URL.

    Used for crops ≥ ~4 MB where the inline base64 response would
    exceed Lambda's 6 MB synchronous payload ceiling. The key includes
    a ``uuid4`` segment so concurrent invocations can't collide; the
    7-day lifecycle rule on the bucket cleans up forgotten objects.
    """
    bucket = event.get("s3_bucket") or DEFAULT_S3_BUCKET
    if not bucket:
        raise RuntimeError(
            "s3 mode requires s3_bucket in event or SHARKTOPUS_S3_BUCKET env var"
        )
    expires = int(event.get("s3_expires_s", 86400))
    key = f"{DEFAULT_S3_PREFIX}{uuid.uuid4().hex}/{final_path.name}"
    s3 = boto3.client("s3", region_name=DEFAULT_REGION)
    s3.upload_file(str(final_path), bucket, key)
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires,
    )
    return {
        "mode": "s3",
        "s3_url": url,
        "s3_bucket": bucket,
        "s3_key": key,
    }
