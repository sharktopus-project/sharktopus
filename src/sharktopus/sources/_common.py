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

from .. import grib
from .base import SourceUnavailable, stream_download

__all__ = ["download_and_crop"]


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
