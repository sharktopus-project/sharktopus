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

from pathlib import Path

from .. import grib
from .base import (
    SourceUnavailable,
    canonical_filename,
    check_retention,
    stream_download,
    validate_cycle,
    validate_date,
)

BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
RETENTION_DAYS = 10

__all__ = ["BASE_URL", "build_url", "fetch_step"]


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
    dest: str | Path,
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
    """Download one GFS forecast step from NOMADS.

    Parameters
    ----------
    date : str
        Run date as ``YYYYMMDD`` (UTC).
    cycle : str
        Run cycle, one of ``"00"``, ``"06"``, ``"12"``, ``"18"``.
    fxx : int
        Forecast hour (0 for analysis).
    dest : path-like
        Destination directory. Created if missing.
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
    dest_dir = Path(dest)
    final = dest_dir / canonical_filename(cycle, fxx, product=product)

    stream_download(
        url, final,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
    )

    if bbox is not None:
        crop_bbox = grib.expand_bbox(bbox, pad_lon=pad_lon, pad_lat=pad_lat)
        tmp = final.with_suffix(final.suffix + ".full")
        final.rename(tmp)
        try:
            grib.crop(tmp, final, bbox=crop_bbox, wgrib2=wgrib2)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    if verify and grib.have_wgrib2(wgrib2):
        n = grib.verify(final, wgrib2=wgrib2)
        if n <= 0:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(f"downloaded file has no records: {url}")

    return final
