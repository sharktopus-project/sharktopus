"""Google Cloud Storage mirror — full-file download + local crop.

GFS is mirrored to the public GCS bucket ``global-forecast-system``.
Anonymous HTTPS works — no ``google-cloud-storage`` dep, no credentials.

Object path mirrors NOMADS/AWS: ``gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}``.

Retention on the GCS mirror is long (no published TTL, empirically
multi-year), so no retention guard here. 404 → ``SourceUnavailable``.

Example
-------

>>> from sharktopus.sources import gcloud
>>> path = gcloud.fetch_step(
...     "20240121", "00", 6,
...     bbox=(-45, -40, -25, -20),
... )
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import grib, paths
from ._common import download_and_crop
from .base import canonical_filename, supports_date, validate_cycle, validate_date

BUCKET = "global-forecast-system"
BASE_URL = f"https://storage.googleapis.com/{BUCKET}"

# GCS mirror is the oldest cloud copy we've confirmed — its ``gfs.{date}``
# prefix goes back to early 2021. Approximate; adjust if you know better.
EARLIEST: datetime | None = datetime(2021, 1, 1, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None

# Conservative default to match CONVECT production. GCS XML API absorbs
# high concurrency, but anonymous traffic is billed to the bucket owner
# (NOAA) and heavy hammering risks being rate-limited. 4 workers stays
# well below the per-IP QPS at which we've seen 429s.
DEFAULT_MAX_WORKERS = 4

__all__ = [
    "BASE_URL",
    "BUCKET",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "build_url",
    "fetch_step",
    "supports",
]


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if the GCS mirror plausibly has *date*."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def build_url(date: str, cycle: str, fxx: int, product: str = "pgrb2.0p25") -> str:
    """Return the public HTTPS URL of one GFS forecast file on the GCS mirror."""
    validate_cycle(cycle)
    validate_date(date)
    fname = canonical_filename(cycle, fxx, product=product)
    return f"{BASE_URL}/gfs.{date}/{cycle}/atmos/{fname}"


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
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str | None = None,
) -> Path:
    """Download one GFS forecast step from the Google Cloud mirror.

    Parameters mirror :func:`sharktopus.sources.nomads.fetch_step`.
    """
    url = build_url(date, cycle, fxx, product=product)
    if dest is None:
        dest_dir = paths.output_dir(
            date=date, cycle=cycle, bbox=bbox, mode="fcst", root=root,
        )
    else:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / canonical_filename(cycle, fxx, product=product)

    return download_and_crop(
        url, final,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        verify=verify, wgrib2=wgrib2,
    )
