"""NOMADS ``filter_gfs_0p25.pl`` — server-side variable/level/subregion subset.

Unlike :mod:`~sharktopus.sources.nomads`, which fetches the full ~500 MB
file and crops locally, this source asks NOAA's CGI filter to return
only the requested variables, levels, and geographic window. Much
faster when you need a small slice, but requires the caller to know
the NOMADS query-parameter vocabulary for variables and levels.

The filter endpoint enforces the same ~10-day retention window as
direct NOMADS.

Example
-------

>>> from sharktopus.sources import nomads_filter
>>> path = nomads_filter.fetch_step(
...     "20240121", "00", 6,
...     dest="/tmp/gfs",
...     bbox=(-45, -40, -25, -20),
...     variables=["TMP", "UGRD", "VGRD", "HGT"],
...     levels=["500 mb", "850 mb", "surface"],
... )
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

from datetime import datetime

from .. import grib, paths
from .base import (
    SourceUnavailable,
    canonical_filename,
    check_retention,
    stream_download,
    supports_date,
    validate_cycle,
    validate_date,
)

BASE_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
BASE_URL_1HR = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25_1hr.pl"
RETENTION_DAYS = 10
EARLIEST: datetime | None = None

# NOMADS filter shares the origin rate-limiter with nomads itself.
DEFAULT_MAX_WORKERS = 2

__all__ = [
    "BASE_URL",
    "BASE_URL_1HR",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "build_url",
    "fetch_step",
    "level_to_param",
    "supports",
]


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if NOMADS-filter can serve *date* given its rolling window."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def level_to_param(level: str) -> str:
    """Convert a wgrib2-style level string into a NOMADS query-param key.

    Examples
    --------

    >>> level_to_param("500 mb")
    'lev_500_mb'
    >>> level_to_param("2 m above ground")
    'lev_2_m_above_ground'
    >>> level_to_param("mean sea level")
    'lev_mean_sea_level'
    >>> level_to_param("0-0.1 m below ground")
    'lev_0-0.1_m_below_ground'
    """
    return "lev_" + level.replace(" ", "_")


def build_url(
    date: str,
    cycle: str,
    fxx: int,
    *,
    variables: Iterable[str],
    levels: Iterable[str],
    bbox: grib.Bbox,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    hourly: bool = False,
) -> str:
    """Return the NOMADS filter URL for one step.

    *bbox* is ``(lon_w, lon_e, lat_s, lat_n)``. *pad_lon* / *pad_lat*
    expand the bbox on each side before requesting the subregion; the
    defaults match :data:`sharktopus.grib.DEFAULT_WRF_PAD_LON` /
    ``DEFAULT_WRF_PAD_LAT`` (2° each), i.e. the minimum margin we
    consider safe for WRF/WPS. Pass ``0`` for an exact-bbox request.
    """
    validate_cycle(cycle)
    validate_date(date)
    var_list = list(variables)
    lev_list = list(levels)
    if not var_list:
        raise ValueError("variables must be a non-empty iterable")
    if not lev_list:
        raise ValueError("levels must be a non-empty iterable")
    lon_w, lon_e, lat_s, lat_n = bbox
    if lon_e <= lon_w or lat_n <= lat_s:
        raise ValueError(f"invalid bbox: {bbox!r}")
    padded_w, padded_e, padded_s, padded_n = grib.expand_bbox(
        bbox, pad_lon=pad_lon, pad_lat=pad_lat,
    )

    file_param = canonical_filename(cycle, fxx, product=product)
    # Pairs preserve order in the URL, matching CONVECT's layout.
    params: list[tuple[str, str]] = [
        ("dir", f"/gfs.{date}/{cycle}/atmos"),
        ("file", file_param),
    ]
    for v in var_list:
        params.append((f"var_{v}", "on"))
    for lv in lev_list:
        params.append((level_to_param(lv), "on"))
    params.extend([
        ("subregion", ""),
        ("toplat", f"{padded_n:g}"),
        ("leftlon", f"{padded_w:g}"),
        ("rightlon", f"{padded_e:g}"),
        ("bottomlat", f"{padded_s:g}"),
    ])
    base = BASE_URL_1HR if hourly else BASE_URL
    return base + "?" + urlencode(params)


def fetch_step(
    date: str,
    cycle: str,
    fxx: int,
    *,
    bbox: grib.Bbox,
    variables: Iterable[str],
    levels: Iterable[str],
    dest: str | Path | None = None,
    root: str | Path | None = None,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    hourly: bool = False,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str | None = None,
    deadline: float | None = None,
) -> Path:
    """Download one forecast step already subset on the server.

    Same contract as :func:`sharktopus.sources.nomads.fetch_step`, but
    *bbox*, *variables*, and *levels* are mandatory since they define
    what the server is asked to return. No local crop is performed —
    whatever the server returns is the final file.

    *pad_lon* / *pad_lat* expand the requested subregion by that many
    degrees on each side (defaults: 2° — see
    :data:`sharktopus.grib.DEFAULT_WRF_PAD_LON`). *hourly* selects the
    ``filter_gfs_0p25_1hr.pl`` endpoint (fxx 0–120 hourly), otherwise
    the default 3-hourly endpoint is used.

    When *dest* is omitted, the file lands under
    ``<root>/fcst/<YYYYMMDDHH>/<bbox_tag>/``; see
    :mod:`sharktopus.paths` for the root-resolution rules.
    """
    check_retention(date, days=RETENTION_DAYS)
    url = build_url(
        date, cycle, fxx,
        variables=variables, levels=levels, bbox=bbox,
        product=product, pad_lon=pad_lon, pad_lat=pad_lat, hourly=hourly,
    )
    if dest is None:
        dest_dir = paths.output_dir(
            date=date, cycle=cycle, bbox=bbox, mode="fcst", root=root,
        )
    else:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / canonical_filename(cycle, fxx, product=product)

    stream_download(
        url, final,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        deadline=deadline,
    )

    if verify and grib.have_wgrib2(wgrib2):
        n = grib.verify(final, wgrib2=wgrib2)
        if n <= 0:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(f"filter returned no records: {url}")

    return final
