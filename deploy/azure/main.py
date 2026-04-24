"""Container Apps entry point for server-side GFS byte-range + wgrib2 crop.

HTTP contract (matches ``sharktopus.sources.azure_crop._build_payload``)::

    {
      "date": "20260417",
      "cycle": "00",
      "fxx": 0,
      "product": "pgrb2.0p25",
      "response_mode": "auto" | "inline" | "blob",
      "blob_expires_s": 86400,
      "bbox": {"lon_w": -50, "lon_e": -40, "lat_s": -10, "lat_n": 0},
      "variables": ["TMP", ...],          # optional
      "levels":    ["surface", ...],      # optional
      "blob_container": "my-container"    # optional override
    }

Response envelope (shared with AWS/GCloud), inline case::

    {
      "statusCode": 200,
      "body": {
        "mode": "inline",
        "b64": "<base64 GRIB2>",
        "billed_duration_ms": 1234,
        "memory_mb": 2048
      }
    }

Large-crop case (``mode=blob``)::

    {
      "statusCode": 200,
      "body": {
        "mode": "blob",
        "blob_url": "https://<acct>.blob.core.windows.net/<c>/<k>?<SAS>",
        "storage_account": "...",
        "blob_container": "...",
        "blob_key": "...",
        "billed_duration_ms": 1234,
        "memory_mb": 2048
      }
    }

``response_mode="auto"`` picks inline when the cropped file is ≤ 20 MB
(Container Apps accepts 100 MB responses, but we keep the same safety
headroom as Cloud Run to leave room for base64 + JSON).

Data source: the public Azure Blob mirror ``noaagfs.blob.core.windows.net``
(NOAA Open Data on Azure), accessed anonymously — the container needs
no storage credentials to read.
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
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import (
        BlobSasPermissions,
        BlobServiceClient,
        generate_blob_sas,
    )
except ImportError:  # pragma: no cover — local-dev fallback
    DefaultAzureCredential = None  # type: ignore[assignment]
    BlobServiceClient = None  # type: ignore[assignment]
    generate_blob_sas = None  # type: ignore[assignment]
    BlobSasPermissions = None  # type: ignore[assignment]

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("sharktopus-azure")

SOURCE_ACCOUNT = "noaagfs"
SOURCE_CONTAINER = "gfs"
SOURCE_BASE_URL = f"https://{SOURCE_ACCOUNT}.blob.core.windows.net/{SOURCE_CONTAINER}"
DEFAULT_STORAGE_ACCOUNT = os.environ.get("SHARKTOPUS_AZURE_STORAGE_ACCOUNT", "")
DEFAULT_BLOB_CONTAINER = os.environ.get("SHARKTOPUS_AZURE_BLOB_CONTAINER", "")
DEFAULT_BLOB_PREFIX = os.environ.get("SHARKTOPUS_AZURE_BLOB_PREFIX", "crops/")
INLINE_SIZE_LIMIT = 20 * 1024 * 1024
DOWNLOAD_WORKERS = int(os.environ.get("SHARKTOPUS_DOWNLOAD_WORKERS", "16"))
MEMORY_MB = int(os.environ.get("SHARKTOPUS_MEMORY_MB", "2048"))

# GFS product codes this container is willing to serve. Defence in
# depth: the container points at the public Azure GFS mirror, so
# accepting any string as ``product`` would let a malicious payload
# construct unrelated keys. New models get their own Container App.
ALLOWED_PRODUCTS = frozenset({
    "pgrb2.0p25", "pgrb2b.0p25",
    "pgrb2.0p50", "pgrb2b.0p50",
    "pgrb2.1p00", "pgrb2b.1p00",
    "sfluxgrbf",
})


app = Flask(__name__)


@app.get("/")
def healthcheck():
    """Cheap liveness probe — Container Apps hits this before routing traffic."""
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
    """Return the Blob object prefix. Azure mirror uses the standard NOMADS layout."""
    return f"gfs.{date}/{cycle}/atmos/"


def _pick_ranges(obj_url: str, idx_url: str, *, variables, levels):
    """Parse .idx, pick byte ranges matching variable/level filters.

    Returns ``(ranges, total_size)`` with ranges as consolidated,
    sorted ``[(start, end), ...]`` tuples. Empty filter lists select
    everything.
    """
    with urlopen(idx_url, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

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

    Azure Blob honours the standard ``Range: bytes=<s>-<e>`` header.
    Parallel range GETs saturate egress far faster than single-threaded.
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
    """Upload to Blob Storage and return a SAS GET URL valid for *blob_expires_s*.

    SAS is delegated from a user-delegation key (managed identity) when
    available, falling back to the account key only if the Container App
    has been granted one. The default Container Apps deploy wires up a
    system-assigned managed identity with ``Storage Blob Data
    Contributor`` on the target account, so user-delegation is the
    expected path.
    """
    if BlobServiceClient is None or generate_blob_sas is None:
        raise RuntimeError("azure-storage-blob not installed in this image")

    account = event.get("storage_account") or DEFAULT_STORAGE_ACCOUNT
    if not account:
        raise RuntimeError(
            "blob mode requires storage_account in event or "
            "SHARKTOPUS_AZURE_STORAGE_ACCOUNT env var"
        )
    container = event.get("blob_container") or DEFAULT_BLOB_CONTAINER
    if not container:
        raise RuntimeError(
            "blob mode requires blob_container in event or "
            "SHARKTOPUS_AZURE_BLOB_CONTAINER env var"
        )
    expires_s = int(event.get("blob_expires_s", 86400))
    key = f"{DEFAULT_BLOB_PREFIX}{uuid.uuid4().hex}/{final_path.name}"

    cred = DefaultAzureCredential()
    account_url = f"https://{account}.blob.core.windows.net"
    svc = BlobServiceClient(account_url=account_url, credential=cred)
    blob = svc.get_blob_client(container, key)
    with final_path.open("rb") as fh:
        blob.upload_blob(fh, overwrite=True)

    start = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
    expiry = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=expires_s)
    udk = svc.get_user_delegation_key(start, expiry)
    sas = generate_blob_sas(
        account_name=account,
        container_name=container,
        blob_name=key,
        user_delegation_key=udk,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
        start=start,
    )
    url = f"{account_url}/{container}/{key}?{sas}"
    return {
        "mode": "blob",
        "blob_url": url,
        "storage_account": account,
        "blob_container": container,
        "blob_key": key,
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
