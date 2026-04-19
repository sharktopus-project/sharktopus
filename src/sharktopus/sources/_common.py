"""Shared download + local-crop recipes used by full-file mirrors.

Five of our six sources (NOMADS, AWS S3, Google Cloud, Azure Blob, RDA)
follow the same recipe: download a public GRIB2 file, optionally crop
with ``wgrib2 -small_grib``, and verify it parses. Only
:mod:`~sharktopus.sources.nomads_filter` and
:mod:`~sharktopus.sources.aws_crop` bypass this — their servers do the
cropping for us.

Public helpers:

* :func:`download_and_crop` — full-file download + optional local crop.
* :func:`download_byte_ranges_and_crop` — byte-range mode driven by an
  ``.idx`` sidecar, with a full-file fallback when no idx is available.

The byte-range path is deliberately split into small private helpers
(``_find_idx``, ``_select_records``, ``_fetch_consolidated_ranges``,
``_crop_in_place``, ``_verify_or_raise``) so each stage reads in
isolation and can be swapped for mirrors that need custom behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..io import grib
from .base import (
    SourceUnavailable,
    fetch_text,
    head_size,
    stream_byte_ranges,
    stream_download,
)

__all__ = ["download_and_crop", "download_byte_ranges_and_crop"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
    deadline: float | None = None,
) -> Path:
    """Download *url* into *final* and optionally crop locally.

    Writes atomically (via ``.part``), then — if *bbox* is provided —
    renames the full file to ``<final>.full`` and runs
    :func:`sharktopus.io.grib.crop` with the bbox expanded by
    *pad_lon* / *pad_lat*, leaving the cropped result at *final*.

    If *verify* is true and wgrib2 is resolvable, runs
    :func:`sharktopus.io.grib.verify` on the final file and raises
    :class:`~sharktopus.sources.base.SourceUnavailable` (not
    :class:`~sharktopus.io.grib.GribError`) when it reports zero
    records — that way the orchestrator's fallback path treats a
    corrupt mirror file the same as a 404.
    """
    stream_download(
        url, final,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )
    _crop_in_place(final, bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat, wgrib2=wgrib2)
    _verify_or_raise(final, url, verify=verify, wgrib2=wgrib2)
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
    sibling_urls: Sequence[str] = (),
    allow_full_file_fallback: bool = False,
    deadline: float | None = None,
) -> Path:
    """Download only the GRIB2 records matching *variables*/*levels*.

    Pipeline (each step is a private helper below):

    1. :func:`_find_idx` — try ``url + idx_suffix`` first, then each
       ``sibling_urls + idx_suffix``. Sibling mirrors share offsets
       with the primary (the GRIB2 files are byte-identical), so RDA
       — which doesn't publish its own idx — can borrow from the
       AWS/GCloud/Azure mirrors.
    2. :func:`_select_records` — parse idx, filter to requested
       ``VAR:LEVEL`` pairs.
    3. :func:`_fetch_consolidated_ranges` — HEAD for total size,
       consolidate adjacent byte ranges, download in parallel.
    4. :func:`_crop_in_place` — optional local bbox crop.
    5. :func:`_verify_or_raise` — ``wgrib2 -s`` sanity check.

    Transfer is typically 30–100× smaller than a full download — for a
    13-var × 49-level WRF selection on a 500 MB GFS file, ~15 MB.

    *sibling_urls* are GRIB2 data URLs (``.idx`` appended internally)
    pointing to mirrors whose files are byte-identical to *url*.

    *allow_full_file_fallback*: when every idx 404s (pre-2021 dates on
    RDA, for example), fall back to full-file download + local filter.

    *idx_suffix* lets callers override (``.idx`` for NCEP mirrors;
    ``.index`` for ECMWF).
    """
    if not variables:
        raise ValueError("variables must be non-empty for byte-range mode")
    if not levels:
        raise ValueError("levels must be non-empty for byte-range mode")

    idx_text, idx_source, last_err = _find_idx(
        url, sibling_urls, idx_suffix,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )

    if idx_text is None:
        if allow_full_file_fallback:
            return _download_full_and_filter(
                url, final,
                variables=variables, levels=levels,
                bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
                timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
                verify=verify, wgrib2=wgrib2, headers=headers,
                deadline=deadline,
            )
        raise SourceUnavailable(
            f"no .idx available for {url} (tried {1 + len(sibling_urls)} "
            f"sources): {last_err}"
        )

    wanted, all_records = _select_records(idx_text, idx_source, variables, levels)
    _fetch_consolidated_ranges(
        url, final, wanted, all_records,
        max_workers=max_workers,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )
    _crop_in_place(final, bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat, wgrib2=wgrib2)
    _verify_or_raise(final, url, verify=verify, wgrib2=wgrib2)
    return final


# ---------------------------------------------------------------------------
# Private helpers — each owns exactly one step of the pipeline
# ---------------------------------------------------------------------------

def _find_idx(
    url: str,
    sibling_urls: Sequence[str],
    idx_suffix: str,
    *,
    timeout: float,
    max_retries: int,
    retry_wait: float,
    headers: dict[str, str] | None,
    deadline: float | None,
) -> tuple[str | None, str | None, Exception | None]:
    """Try the primary idx URL, then each sibling. Return (text, source, last_error).

    Returns ``(None, None, last_error)`` when every candidate 404s.
    """
    candidates = [url + idx_suffix] + [s + idx_suffix for s in sibling_urls]
    last_err: Exception | None = None
    for cand in candidates:
        try:
            text = fetch_text(
                cand,
                timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
                headers=headers, deadline=deadline,
            )
            return text, cand, None
        except SourceUnavailable as e:
            last_err = e
            continue
    return None, None, last_err


def _select_records(
    idx_text: str,
    idx_source: str,
    variables: Sequence[str],
    levels: Sequence[str],
) -> tuple[list[grib.IdxRecord], list[grib.IdxRecord]]:
    """Parse *idx_text* and keep only records with ``VAR:LEVEL`` in the requested set.

    Returns ``(wanted, all_records)`` — :func:`grib.byte_ranges` needs
    the full list to compute the "end" offset of the last wanted record.
    """
    records = grib.parse_idx(idx_text)
    if not records:
        raise SourceUnavailable(f"empty or unparseable .idx at {idx_source}")

    var_set = set(variables)
    lvl_set = set(levels)
    wanted = [r for r in records if r.variable in var_set and r.level in lvl_set]
    if not wanted:
        raise SourceUnavailable(
            f"no records in {idx_source} match "
            f"variables={sorted(var_set)} levels={sorted(lvl_set)}"
        )
    return wanted, records


def _fetch_consolidated_ranges(
    url: str,
    final: Path,
    wanted: list[grib.IdxRecord],
    all_records: list[grib.IdxRecord],
    *,
    max_workers: int,
    timeout: float,
    max_retries: int,
    retry_wait: float,
    headers: dict[str, str] | None,
    deadline: float | None,
) -> None:
    """HEAD for size, compute consolidated ranges, download in parallel into *final*."""
    total = head_size(
        url,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )
    ranges = grib.byte_ranges(all_records, wanted, total_size=total)
    if not ranges:
        raise SourceUnavailable(f"no byte ranges computed for {url}")

    final.parent.mkdir(parents=True, exist_ok=True)
    stream_byte_ranges(
        url, ranges, final,
        max_workers=max_workers,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )


def _crop_in_place(
    final: Path,
    *,
    bbox: grib.Bbox | None,
    pad_lon: float,
    pad_lat: float,
    wgrib2: str | None,
) -> None:
    """Apply :func:`sharktopus.io.grib.crop` in place when *bbox* is set.

    Moves the full file aside to ``<final>.full``, writes the cropped
    result to *final*, and deletes the temp. No-op when *bbox* is None.
    """
    if bbox is None:
        return
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


def _verify_or_raise(
    final: Path,
    url: str,
    *,
    verify: bool,
    wgrib2: str | None,
) -> None:
    """Run ``wgrib2 -s`` on *final*; raise :class:`SourceUnavailable` on failure.

    Shared between :func:`download_and_crop`,
    :func:`download_byte_ranges_and_crop`, and
    :func:`_download_full_and_filter`.
    """
    if not verify or not grib.have_wgrib2(wgrib2):
        return
    try:
        n = grib.verify(final, wgrib2=wgrib2)
    except grib.GribError as e:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable(f"output file unparseable: {url}: {e}") from e
    if n <= 0:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable(f"output file has no records: {url}")


def _download_full_and_filter(
    url: str,
    final: Path,
    *,
    variables: Sequence[str],
    levels: Sequence[str],
    bbox: grib.Bbox | None,
    pad_lon: float,
    pad_lat: float,
    timeout: float,
    max_retries: int,
    retry_wait: float,
    verify: bool,
    wgrib2: str | None,
    headers: dict[str, str] | None,
    deadline: float | None = None,
) -> Path:
    """Fallback used when no idx can be fetched from primary or siblings.

    Downloads the whole file once, crops the bbox (optional), then
    filters to *variables* × *levels* with wgrib2. The result matches
    what byte-range mode would have produced.
    """
    stream_download(
        url, final,
        timeout=timeout, max_retries=max_retries, retry_wait=retry_wait,
        headers=headers, deadline=deadline,
    )
    _crop_in_place(final, bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat, wgrib2=wgrib2)

    tmp = final.with_suffix(final.suffix + ".unfiltered")
    final.rename(tmp)
    try:
        grib.filter_vars_levels(
            tmp, final,
            variables=variables, levels=levels,
            wgrib2=wgrib2,
        )
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    _verify_or_raise(final, url, verify=verify, wgrib2=wgrib2)
    return final
