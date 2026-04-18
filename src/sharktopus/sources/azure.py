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

from pathlib import Path

from .. import grib, paths
from ._common import download_and_crop
from .base import canonical_filename, validate_cycle, validate_date

CONTAINER = "gfs"
BASE_URL = f"https://noaagfs.blob.core.windows.net/{CONTAINER}"

# Azure Blob Storage default per-account IOPS ceiling is ~20k req/s,
# vastly above anything a single fetch will hit; the real limiter is
# our local disk + CPU while cropping. 4 parallel workers keeps the
# bandwidth saturated without starving the crop step.
DEFAULT_MAX_WORKERS = 4

__all__ = ["BASE_URL", "CONTAINER", "DEFAULT_MAX_WORKERS", "build_url", "fetch_step"]


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
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str = "wgrib2",
) -> Path:
    """Download one GFS forecast step from the Azure Blob mirror.

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
