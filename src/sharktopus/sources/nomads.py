"""NOMADS direct full-file download.

Fetches the complete ``pgrb2.0p25`` file from
``https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/`` and
optionally runs a local geographic crop via
:func:`sharktopus.grib.crop` afterwards.

NOMADS keeps roughly the last 10 days — older requests will raise
:class:`~sharktopus.sources.base.SourceUnavailable` before hitting the
network.

Example
-------

>>> from sharktopus.sources import nomads
>>> path = nomads.fetch_step(
...     "20240121", "00", 6,
...     dest="/tmp/gfs", bbox=(-45, -40, -25, -20),
... )
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typing import Sequence

from .. import grib, paths
from ._common import download_and_crop, download_byte_ranges_and_crop
from .base import (
    canonical_filename,
    check_retention,
    supports_date,
    validate_cycle,
    validate_date,
)

BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
RETENTION_DAYS = 10
EARLIEST: datetime | None = None  # rolling-window mirror, no fixed start

# NOAA's origin infrastructure rate-limits aggressive callers aggressively —
# we have observed 503s at 4+ concurrent connections from a single IP.
DEFAULT_MAX_WORKERS = 2

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
    """Return ``True`` if NOMADS can serve *date* given its rolling window."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def build_url(date: str, cycle: str, fxx: int, product: str = "pgrb2.0p25") -> str:
    """Return the public HTTPS URL of one GFS forecast file on NOMADS."""
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
) -> Path:
    """Download one GFS forecast step from NOMADS.

    Parameters
    ----------
    date : str
        Run date as ``YYYYMMDD`` (UTC).
    cycle : str
        Run cycle, one of ``"00"``, ``"06"``, ``"12"``, ``"18"``.
    fxx : int
        Forecast hour (0 for analysis).
    dest : path-like, optional
        Destination directory. Created if missing. When omitted, the
        file lands under
        ``<root>/fcst/<YYYYMMDDHH>/<bbox_tag>/`` — see
        :mod:`sharktopus.paths`.
    root : path-like, optional
        Override the root of the default convention. Ignored when *dest*
        is given. Falls through to ``$SHARKTOPUS_DATA`` and finally
        :data:`sharktopus.paths.DEFAULT_ROOT` (``~/.cache/sharktopus``).
    bbox : tuple, optional
        ``(lon_w, lon_e, lat_s, lat_n)``. When given, the full file is
        downloaded and then cropped locally with
        :func:`sharktopus.grib.crop`. The actual crop window is the
        *bbox* grown by ``pad_lon`` / ``pad_lat`` on each side so WPS /
        metgrid has a safe margin around the WRF outer domain. Without
        *bbox*, the original file is kept unmodified and the pads are
        ignored.
    pad_lon, pad_lat : float
        Buffer in degrees added to each side of *bbox* before cropping.
        Defaults: :data:`sharktopus.grib.DEFAULT_WRF_PAD_LON` /
        ``DEFAULT_WRF_PAD_LAT`` (2° each, ≈8 grid cells at 0.25° — the
        minimum margin we guarantee is WRF-safe). Pass ``0`` for an
        exact-bbox crop, or larger values (CONVECT's legacy scripts use
        5°) for extra safety.
    product : str
        GFS product code (default ``"pgrb2.0p25"``).
    timeout, max_retries, retry_wait : float, int, float
        Passed to :func:`~sharktopus.sources.base.stream_download`.
    verify : bool
        If True and wgrib2 is on PATH, run
        :func:`sharktopus.grib.verify` on the final file and raise
        :class:`~sharktopus.sources.base.SourceUnavailable` if it returns
        zero records.
    wgrib2 : str
        Name / path of the ``wgrib2`` binary.

    Returns
    -------
    Path
        The produced GRIB2 file.

    Raises
    ------
    SourceUnavailable
        Step not yet published (404), NOMADS retention exceeded, or the
        file failed verification.
    """
    check_retention(date, days=RETENTION_DAYS)
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
            verify=verify, wgrib2=wgrib2,
        )

    return download_and_crop(
        url, final,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        verify=verify, wgrib2=wgrib2,
    )
