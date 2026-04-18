"""Shared helper for full-GRIB download + optional local crop.

Five of our six sources (NOMADS, AWS S3, Google Cloud, Azure Blob, RDA)
follow the same recipe: download a full public GRIB2 file, optionally
crop it locally with ``wgrib2 -small_grib``, and verify it parses. Only
:mod:`~sharktopus.sources.nomads_filter` is different, because its
server does the cropping for us.

:func:`download_and_crop` captures that recipe in one place so the
source modules stay a thin URL-plus-defaults wrapper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .. import grib
from .base import (
    SourceUnavailable,
    fetch_text,
    head_size,
    stream_byte_ranges,
    stream_download,
)

__all__ = ["download_and_crop", "download_byte_ranges_and_crop"]


def download_and_crop(
    url: str,
    final: Path,
    *,
    bbox: grib.Bbox | None,
    pad_lon: float,
    pad_lat: float,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str | None = None,
    headers: dict[str, str] | None = None,
) -> Path:
    """Download *url* into *final* and optionally crop locally.

    Writes atomically (via ``.part``), then — if *bbox* is provided —
    renames the full file to ``<final>.full`` and runs
    :func:`sharktopus.grib.crop` with the bbox expanded by
    *pad_lon* / *pad_lat*, leaving the cropped result at *final*.

    If *verify* is true and wgrib2 is resolvable, runs
    :func:`sharktopus.grib.verify` on the final file and raises
    :class:`~sharktopus.sources.base.SourceUnavailable` (not
    :class:`~sharktopus.grib.GribError`) when it reports zero records —
    that way the orchestrator's fallback path treats a corrupt mirror
    file the same as a 404.
    """
    stream_download(
        url, final,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers,
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
        try:
            n = grib.verify(final, wgrib2=wgrib2)
        except grib.GribError as e:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(f"downloaded file unparseable: {url}: {e}") from e
        if n <= 0:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(f"downloaded file has no records: {url}")

    return final


def download_byte_ranges_and_crop(
    url: str,
    final: Path,
    *,
    variables: Sequence[str],
    levels: Sequence[str],
    bbox: grib.Bbox | None = None,
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    max_workers: int = 4,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    verify: bool = True,
    wgrib2: str | None = None,
    headers: dict[str, str] | None = None,
    idx_suffix: str = ".idx",
) -> Path:
    """Download only the GRIB2 records matching *variables*/*levels*.

    Recipe (ported from CONVECT's production scripts):

    1. Fetch ``url + idx_suffix`` (tiny text file, < 50 KB).
    2. Parse it with :func:`sharktopus.grib.parse_idx`.
    3. Filter records whose ``VAR:LEVEL`` is in the requested set.
    4. HEAD *url* to get total size so we can close the last record.
    5. Consolidate adjacent ranges with :func:`sharktopus.grib.byte_ranges`.
    6. Download ranges in parallel with :func:`stream_byte_ranges`.
    7. Optionally crop locally (bbox) and verify.

    Transfer is typically 30-100× smaller than a full download — for a
    13-var × 49-level WRF selection on a 500 MB GFS file, that's ~15 MB.
    Works on any mirror that publishes ``.idx`` alongside the GRIB2
    (nomads, aws, gcloud, azure — rda does not).

    *idx_suffix* lets callers override the suffix; NCEP-aligned mirrors
    use ``".idx"``, ECMWF uses ``".index"``.
    """
    if not variables:
        raise ValueError("variables must be non-empty for byte-range mode")
    if not levels:
        raise ValueError("levels must be non-empty for byte-range mode")

    idx_text = fetch_text(
        url + idx_suffix,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers,
    )
    records = grib.parse_idx(idx_text)
    if not records:
        raise SourceUnavailable(f"empty or unparseable .idx at {url}{idx_suffix}")

    var_set = set(variables)
    lvl_set = set(levels)
    wanted = [r for r in records if r.variable in var_set and r.level in lvl_set]
    if not wanted:
        raise SourceUnavailable(
            f"no records in {url}{idx_suffix} match "
            f"variables={sorted(var_set)} levels={sorted(lvl_set)}"
        )

    total = head_size(
        url,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers,
    )
    ranges = grib.byte_ranges(records, wanted, total_size=total)
    if not ranges:
        raise SourceUnavailable(f"no byte ranges computed for {url}")

    final.parent.mkdir(parents=True, exist_ok=True)
    stream_byte_ranges(
        url, ranges, final,
        max_workers=max_workers,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers,
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
        try:
            n = grib.verify(final, wgrib2=wgrib2)
        except grib.GribError as e:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(
                f"byte-range file unparseable: {url}: {e}"
            ) from e
        if n <= 0:
            try:
                final.unlink()
            except FileNotFoundError:
                pass
            raise SourceUnavailable(f"byte-range file has no records: {url}")

    return final
