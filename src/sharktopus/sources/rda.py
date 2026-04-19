"""NCAR RDA mirror — full-file download + local crop.

NCAR's Research Data Archive hosts a long-term GFS 0.25° archive at
dataset ``ds084.1`` (available since 2015-01-15). Files use a
validity-time naming scheme (``gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2``),
different from NOMADS/AWS/GCS/Azure which all mirror NOAA's cycle-time
layout.

Public HTTPS works for recent files — ``https://data.rda.ucar.edu/``
serves anonymously. Older files sometimes require a free NCAR RDA
account; set ``SHARKTOPUS_RDA_COOKIE`` (the value of the
``rda-cookie`` header after logging in to
https://rda.ucar.edu/login/) and we'll pass it on requests.

.. note::
   RDA does **not** publish ``.idx`` sidecars next to its GRIB2
   files, but its files are **byte-identical** to NCEP's canonical
   0p25 files on AWS / GCloud / Azure — same records in the same
   byte positions. So when ``variables`` and ``levels`` are passed,
   we try to borrow the idx from those mirrors (post-2021 dates,
   where all four exist side-by-side) and byte-range against RDA
   directly. For pre-2021 dates (the RDA-only window), no sibling
   idx exists and we transparently fall back to full download +
   ``wgrib2 -match`` locally — the caller still receives exactly
   the requested subset.

Example
-------

>>> from sharktopus.sources import rda
>>> path = rda.fetch_step(
...     "20240121", "00", 6,
...     bbox=(-45, -40, -25, -20),
... )
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .. import grib, paths
from ._common import download_and_crop, download_byte_ranges_and_crop
from .base import (
    SourceUnavailable,
    canonical_filename,
    supports_date,
    validate_cycle,
    validate_date,
)

DATASET = "d084001"
BASE_URL = f"https://data.rda.ucar.edu/{DATASET}"

# Earliest date for which ds084.1 has 0.25° data. Requests older than
# this raise SourceUnavailable up front so the orchestrator can fall
# through to a different source (or nothing — RDA is our oldest mirror).
EARLIEST: datetime | None = datetime(2015, 1, 15, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None  # NCAR keeps ds084.1 indefinitely

# Be nice to academic infrastructure. NCAR has throttled aggressive
# anonymous callers in the past; the CONVECT RDA script ran serial
# (effectively max_workers=1) and never saw a 429. We keep that here.
DEFAULT_MAX_WORKERS = 1

# Canonical RDA filename (validity-time layout). Independent of the
# NOMADS/AWS/GCS/Azure cycle-time canonical_filename.
def rda_filename(date: str, cycle: str, fxx: int) -> str:
    """Return ``gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`` for RDA."""
    validate_cycle(cycle)
    if fxx < 0:
        raise ValueError(f"fxx must be >= 0, got {fxx}")
    return f"gfs.0p25.{date}{cycle}.f{fxx:03d}.grib2"


__all__ = [
    "BASE_URL",
    "DATASET",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "build_url",
    "fetch_step",
    "rda_filename",
    "supports",
]


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if RDA ds084.1 has *date* (on or after 2015-01-15)."""
    return supports_date(
        date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now
    )


def build_url(date: str, cycle: str, fxx: int) -> str:
    """Return the public HTTPS URL of one GFS forecast file on RDA ds084.1."""
    dt = validate_date(date)
    validate_cycle(cycle)
    year = dt.strftime("%Y")
    fname = rda_filename(date, cycle, fxx)
    return f"{BASE_URL}/{year}/{date}/{fname}"


def _auth_headers() -> dict[str, str] | None:
    cookie = os.environ.get("SHARKTOPUS_RDA_COOKIE")
    if cookie:
        return {"Cookie": cookie}
    return None


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
    product: str = "pgrb2.0p25",  # accepted for signature parity; ignored
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
    """Download one GFS forecast step from NCAR RDA ds084.1.

    Parameters mirror :func:`sharktopus.sources.nomads.fetch_step`.

    ``product`` is accepted for signature parity with the other
    sources but ignored — RDA serves only one GRIB2 flavour under
    ds084.1. The file is written under the canonical NOMADS/AWS name
    (``gfs.t{HH}z.pgrb2.0p25.f{FFF}``), not the RDA validity-time
    name, so downstream tools can find it the same way regardless of
    which source supplied it.

    When *variables* and *levels* are provided, this routes through
    byte-range mode using an idx borrowed from AWS / GCloud / Azure
    (the files are byte-identical). If none of them serves the date
    either — the ``2015-01-15 → 2021-02-26`` window that RDA alone
    covers — the call transparently falls back to a full download
    followed by ``wgrib2 -match`` to produce the same subset locally.
    """
    del product  # silence linters; kept for API compatibility
    dt = validate_date(date)
    if dt < EARLIEST:
        raise SourceUnavailable(
            f"RDA ds084.1 starts {EARLIEST.date()}; requested {date}"
        )
    url = build_url(date, cycle, fxx)
    if dest is None:
        dest_dir = paths.output_dir(
            date=date, cycle=cycle, bbox=bbox, mode="fcst", root=root,
        )
    else:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / canonical_filename(cycle, fxx)

    if variables and levels:
        from . import aws, azure, gcloud
        siblings = [
            aws.build_url(date, cycle, fxx),
            gcloud.build_url(date, cycle, fxx),
            azure.build_url(date, cycle, fxx),
        ]
        return download_byte_ranges_and_crop(
            url, final,
            variables=variables, levels=levels,
            bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
            max_workers=max_workers,
            timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
            verify=verify, wgrib2=wgrib2,
            headers=_auth_headers(),
            sibling_urls=siblings,
            allow_full_file_fallback=True,
            deadline=deadline,
        )

    return download_and_crop(
        url, final,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        verify=verify, wgrib2=wgrib2,
        headers=_auth_headers(),
        deadline=deadline,
    )
