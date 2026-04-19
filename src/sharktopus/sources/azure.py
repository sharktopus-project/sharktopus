"""Azure Blob Storage mirror — full-file download + local crop.

NOAA's Azure mirror lives at
``https://noaagfs.blob.core.windows.net/gfs/``. Anonymous HTTPS works
— no ``azure-storage-blob`` dep, no SAS token.

Blob path mirrors NOMADS/AWS/GCS: ``gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}``.

Retention is published as "indefinite" on the NOAA Open Data portal,
so no retention guard here. 404 → ``SourceUnavailable``.

Example
-------

>>> from sharktopus.sources import azure
>>> path = azure.fetch_step(
...     "20240121", "00", 6,
...     bbox=(-45, -40, -25, -20),
... )
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from typing import Sequence

from .. import grib, paths
from ._common import download_and_crop, download_byte_ranges_and_crop
from .base import canonical_filename, supports_date, validate_cycle, validate_date

CONTAINER = "gfs"
BASE_URL = f"https://noaagfs.blob.core.windows.net/{CONTAINER}"

# NOAA's Azure mirror rolled out with the wider NOAA Open Data program
# in early 2021. Conservative lower bound; adjust if confirmed earlier.
EARLIEST: datetime | None = datetime(2021, 1, 1, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None

# Azure Blob Storage default per-account IOPS ceiling is ~20k req/s,
# vastly above anything a single fetch will hit; the real limiter is
# our local disk + CPU while cropping. 4 parallel workers keeps the
# bandwidth saturated without starving the crop step.
DEFAULT_MAX_WORKERS = 4

__all__ = [
    "BASE_URL",
    "CONTAINER",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "build_url",
    "fetch_step",
    "supports",
]


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if the Azure mirror plausibly has *date*."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def build_url(date: str, cycle: str, fxx: int, product: str = "pgrb2.0p25") -> str:
    """Return the public HTTPS URL of one GFS forecast file on Azure Blob."""
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
    variables: Sequence[str] | None = None,
    levels: Sequence[str] | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str | None = None,
    deadline: float | None = None,
) -> Path:
    """Download one GFS forecast step from the Azure Blob mirror.

    When *variables* and *levels* are both provided, switches to
    byte-range mode (fetches ``.idx``, downloads only requested records
    in parallel). Omit both for the full-file + local-crop recipe.
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

    if variables and levels:
        return download_byte_ranges_and_crop(
            url, final,
            variables=variables, levels=levels,
            bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
            max_workers=max_workers,
            timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
            verify=verify, wgrib2=wgrib2, deadline=deadline,
        )

    return download_and_crop(
        url, final,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        verify=verify, wgrib2=wgrib2, deadline=deadline,
    )
