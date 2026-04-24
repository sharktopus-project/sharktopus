"""Cloud Run entry point for server-side GFS byte-range + wgrib2 crop.

HTTP contract (matches ``sharktopus.sources.gcloud_crop._build_payload``):

Request (``POST /``, JSON body)::

    {
      "date": "20260417",
      "cycle": "00",
      "fxx": 0,
      "product": "pgrb2.0p25",
      "response_mode": "auto" | "inline" | "gcs",
      "gcs_expires_s": 86400,
      "bbox": {"lon_w": -50, "lon_e": -40, "lat_s": -10, "lat_n": 0},
      "variables": ["TMP", ...],         # optional
      "levels":    ["surface", ...],     # optional
      "gcs_bucket": "my-bucket"          # optional override
    }

Response (always the same envelope, mirrored from the AWS handler)::

    {
      "statusCode": 200,
      "body": {
        "mode": "inline",
        "b64": "<base64-encoded GRIB2>",
        "billed_duration_ms": 1234,
        "memory_mb": 2048
      }
    }

or for large crops::

    {
      "statusCode": 200,
      "body": {
        "mode": "gcs",
        "gcs_url": "https://storage.googleapis.com/...",
        "gcs_bucket": "...",
        "gcs_key": "...",
        "billed_duration_ms": 1234,
        "memory_mb": 2048
      }
    }

``response_mode="auto"`` picks inline when the cropped file is ≤ 20 MB
(Cloud Run's 32 MB response ceiling, minus headroom for base64 +
JSON envelope).

Data source: the public GCS bucket ``global-forecast-system`` (NOAA
mirror), accessed anonymously over HTTPS — no service account needed
to read.
"""

from __future__ import annotations

import base64
import datetime as _dt
import logging
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover — local-dev fallback
    storage = None

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("sharktopus-gcloud")

SOURCE_BUCKET = "global-forecast-system"
SOURCE_BASE_URL = f"https://storage.googleapis.com/{SOURCE_BUCKET}"
DEFAULT_GCS_BUCKET = os.environ.get("SHARKTOPUS_GCS_BUCKET", "")
DEFAULT_GCS_PREFIX = os.environ.get("SHARKTOPUS_GCS_PREFIX", "crops/")
INLINE_SIZE_LIMIT = 20 * 1024 * 1024
DOWNLOAD_WORKERS = int(os.environ.get("SHARKTOPUS_DOWNLOAD_WORKERS", "16"))
MEMORY_MB = int(os.environ.get("SHARKTOPUS_MEMORY_MB", "2048"))

# GFS product codes this service is willing to serve. Defence in depth:
# the service points at the public GFS bucket, so accepting any string
# as ``product`` would let a malicious payload construct unrelated keys.
# New models (HRRR, NAM, GEFS…) get their own Cloud Run service.
ALLOWED_PRODUCTS = frozenset({
    "pgrb2.0p25", "pgrb2b.0p25",
    "pgrb2.0p50", "pgrb2b.0p50",
    "pgrb2.1p00", "pgrb2b.1p00",
    "sfluxgrbf",
})


app = Flask(__name__)


@app.get("/")
def healthcheck():
    """Cheap liveness probe — Cloud Run hits this before routing traffic."""
    return jsonify({"status": "ok"}), 200


@app.post("/")
def crop_endpoint():
    """Entry point: parse event → delegate to :func:`_process` → envelope."""
    start_ns = time.monotonic_ns()
    event = request.get_json(silent=True) or {}
    try:
        result = _process(event)
    except ValueError as e:
        logger.warning("rejecting event: %s", e)
        return jsonify({
            "statusCode": 400,
            "body": {"error": str(e), "type": "ValueError"},
        }), 400
    except Exception as e:
        logger.exception("handler failed")
        return jsonify({
            "statusCode": 500,
            "body": {"error": str(e), "type": type(e).__name__},
        }), 500

    elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
    result["billed_duration_ms"] = int(elapsed_ms)
    result["memory_mb"] = MEMORY_MB
    return jsonify({"statusCode": 200, "body": result}), 200


def _process(event: dict) -> dict:
    """Core pipeline: validate → find key → range-download → crop → package."""
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

    prefix = _detect_prefix(date, cycle)
    key = f"{prefix}gfs.t{cycle}z.{product}.f{fxx:03d}"
    obj_url = f"{SOURCE_BASE_URL}/{key}"
    idx_url = f"{obj_url}.idx"

    with tempfile.TemporaryDirectory(prefix="shark_") as tmp:
        tmpd = Path(tmp)
        ranges, total_size = _pick_ranges(obj_url, idx_url, variables=variables, levels=levels)
        raw_path = tmpd / "raw.grib2"
        _download_ranges(obj_url, ranges, raw_path)
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
        return _upload_signed(final_path, event)


def _parse_required(event: dict) -> tuple[str, str, int]:
    """Validate ``date``/``cycle``/``fxx`` from the event."""
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


def _detect_prefix(date: str, cycle: str) -> str:
    """Return the GCS object prefix. Newer layout is the only one on GCS."""
    return f"gfs.{date}/{cycle}/atmos/"


def _pick_ranges(obj_url: str, idx_url: str, *, variables, levels):
    """Parse .idx, pick byte ranges matching variable/level filters.

    Returns ``(ranges, total_size)`` with ranges as consolidated,
    sorted ``[(start, end), ...]`` tuples. Empty filter lists select
    everything.
    """
    with urlopen(idx_url, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    # Use a 1-byte range GET to discover total size without a separate
    # library call (GCS returns Content-Range: bytes 0-0/<total>).
    head_req = Request(obj_url, method="GET", headers={"Range": "bytes=0-0"})
    with urlopen(head_req, timeout=30) as resp:
        cr = resp.headers.get("Content-Range", "")
    try:
        total_size = int(cr.rsplit("/", 1)[-1])
    except ValueError:
        raise RuntimeError(f"could not parse Content-Range header: {cr!r}")

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

    picks: list[tuple[int, int]] = []
    for i, (_, off, var, level) in enumerate(entries):
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


def _download_ranges(obj_url: str, ranges, raw_path: Path):
    """Fetch each byte range in parallel over HTTPS and concatenate.

    GCS XML API accepts the standard ``Range: bytes=<s>-<e>`` header.
    Parallel range GETs over HTTP/2 saturate the egress link much faster
    than a single-threaded pull.
    """
    parts: list[bytes | None] = [None] * len(ranges)

    def fetch(i: int) -> None:
        s, e = ranges[i]
        req = Request(obj_url, method="GET", headers={"Range": f"bytes={s}-{e}"})
        with urlopen(req, timeout=120) as resp:
            parts[i] = resp.read()

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        futs = [ex.submit(fetch, i) for i in range(len(ranges))]
        for f in as_completed(futs):
            f.result()

    with raw_path.open("wb") as out:
        for chunk in parts:
            if chunk:
                out.write(chunk)


def _crop(raw_path: Path, bbox: dict, tmpd: Path) -> Path:
    """Run ``wgrib2 -small_grib`` on *raw_path* with the given bbox."""
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


def _upload_signed(final_path: Path, event: dict) -> dict:
    """Upload to GCS and return a V4-signed GET URL valid for *gcs_expires_s*."""
    if storage is None:
        raise RuntimeError("google-cloud-storage not installed in this image")
    bucket_name = event.get("gcs_bucket") or DEFAULT_GCS_BUCKET
    if not bucket_name:
        raise RuntimeError(
            "gcs mode requires gcs_bucket in event or SHARKTOPUS_GCS_BUCKET env var"
        )
    expires = int(event.get("gcs_expires_s", 86400))
    key = f"{DEFAULT_GCS_PREFIX}{uuid.uuid4().hex}/{final_path.name}"

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(key)
    blob.upload_from_filename(str(final_path))
    url = blob.generate_signed_url(
        version="v4",
        method="GET",
        expiration=_dt.timedelta(seconds=expires),
    )
    return {
        "mode": "gcs",
        "gcs_url": url,
        "gcs_bucket": bucket_name,
        "gcs_key": key,
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
