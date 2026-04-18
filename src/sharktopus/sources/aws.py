"""AWS Open Data mirror — full-file download + local crop.

GFS is mirrored to the public S3 bucket ``noaa-gfs-bdp-pds`` (NOAA Big
Data Program). Anonymous HTTPS works — no AWS credentials, no ``boto3``
install, no egress billing for the requester.

The object path mirrors NOMADS: ``gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}``.

The AWS mirror keeps files much longer than NOMADS (~2 years), so this
source does not enforce a retention window — callers routinely reach
back months. ``SourceUnavailable`` is still raised on HTTP 404 (file
not yet staged / older than the bucket's retention).

Example
-------

>>> from sharktopus.sources import aws
>>> path = aws.fetch_step(
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

BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# Earliest day the NOAA BDP bucket is known to serve. Approximate; newer
# cycles are always available. Override the module attribute if you
# confirm a deeper history for your use case.
EARLIEST: datetime | None = datetime(2021, 2, 27, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None  # no rolling purge

# Default concurrency when a caller asks the batch orchestrator to run
# step downloads in parallel from this source. Tuned to CONVECT's
# production value (2) which we know does not trigger S3 throttling on
# a single-IP client. AWS publishes no hard per-IP rate limit, but
# anonymous callers get 403s under sustained >16 req/s; 2 workers keeps
# a comfortable margin while still halving wall time vs serial.
DEFAULT_MAX_WORKERS = 4

__all__ = [
    "BASE_URL",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "build_url",
    "fetch_step",
    "supports",
]


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if the AWS Open Data bucket plausibly has *date*."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def build_url(date: str, cycle: str, fxx: int, product: str = "pgrb2.0p25") -> str:
    """Return the public HTTPS URL of one GFS forecast file on the AWS mirror."""
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
    """Download one GFS forecast step from the AWS Open Data mirror.

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
