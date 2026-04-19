"""GRIB2 / `.idx` utilities (wgrib2-backed).

Six pure functions consolidated from CONVECT's five GFS download scripts
(see `docs/ORIGIN.md`). No HTTP, no state — they take files or text and
return files, counts, or structured data.

wgrib2 must be on PATH for `verify`, `crop`, `filter_vars_levels`, and
`rename_by_validity`. `parse_idx` and `byte_ranges` are pure Python.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .wgrib2 import (
    WgribNotFoundError,
    bundled_wgrib2,
    ensure_wgrib2,
    resolve_wgrib2,
)


class GribError(RuntimeError):
    """Any failure from a wgrib2 call or malformed .idx."""


# Bounding box: (lon_w, lon_e, lat_s, lat_n) — matches wgrib2 -small_grib order
Bbox = tuple[float, float, float, float]


# Default buffer applied by source fetchers before cropping the downloaded
# file. At 0.25° GFS this is 8 grid cells per side, a comfortable margin for
# WPS/metgrid interpolation into a WRF outer domain. CONVECT's legacy scripts
# hard-coded 5° (20 cells); anyone reproducing those runs can pass
# ``pad_lon=5.0, pad_lat=5.0`` explicitly.
DEFAULT_WRF_PAD_LON = 2.0
DEFAULT_WRF_PAD_LAT = 2.0


def suggest_omp_threads(
    concurrent_crops: int,
    cpu_count: int | None = None,
    *,
    leave_free: int = 2,
    max_per_crop: int = 8,
) -> int:
    """Suggest a safe ``OMP_NUM_THREADS`` for wgrib2 given concurrency.

    Returns ``max(1, min(max_per_crop, (cpu_count - leave_free) //
    concurrent_crops))`` — i.e. split the cores fairly across the
    expected number of concurrent wgrib2 invocations, leaving a small
    headroom for Python / I/O threads, and capping each crop at
    *max_per_crop* (wgrib2's OpenMP speedup flattens quickly past ~8
    threads on a single ~50 MB file).

    *concurrent_crops* should be the peak number of wgrib2 processes
    expected to run at the same time — in spread mode that's
    ``sum(DEFAULT_MAX_WORKERS over eligible sources)``; in
    fallback-chain it's just the batch pool size.
    """
    if concurrent_crops <= 0:
        return 1
    if cpu_count is None:
        cpu_count = os.cpu_count() or 1
    per_crop = (max(1, cpu_count) - max(0, leave_free)) // concurrent_crops
    return max(1, min(max_per_crop, per_crop))


def _resolve_omp_threads(explicit: int | None) -> int | None:
    """Return the OMP_NUM_THREADS wgrib2 should run with, or ``None``.

    Priority: explicit *explicit* arg > ``SHARKTOPUS_OMP_THREADS`` env
    var > return ``None`` (don't set ``OMP_NUM_THREADS`` — inherit from
    caller's environment, which is wgrib2's native default).
    """
    if explicit is not None:
        if explicit < 1:
            raise ValueError(f"omp_threads must be >= 1, got {explicit}")
        return int(explicit)
    raw = os.environ.get("SHARKTOPUS_OMP_THREADS")
    if raw is None or raw.strip() == "":
        return None
    try:
        n = int(raw)
    except ValueError as e:
        raise ValueError(
            f"SHARKTOPUS_OMP_THREADS must be an integer, got {raw!r}"
        ) from e
    if n < 1:
        raise ValueError(f"SHARKTOPUS_OMP_THREADS must be >= 1, got {n}")
    return n


def _env_with_omp(omp_threads: int | None) -> dict[str, str] | None:
    """Return an env dict with ``OMP_NUM_THREADS`` set, or ``None`` for inherit."""
    if omp_threads is None:
        return None
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = str(omp_threads)
    return env


def expand_bbox(bbox: Bbox, pad_lon: float, pad_lat: float) -> Bbox:
    """Return *bbox* grown by *pad_lon* deg on each lon side and *pad_lat* on each lat side.

    Negative pads are rejected (shrinking would silently drop data the user
    asked for); zero is allowed and is a no-op. Lat bounds are clamped to
    ``[-90, 90]``; longitudes are left alone (callers may use any convention,
    e.g. 0–360 or -180–180).
    """
    if pad_lon < 0 or pad_lat < 0:
        raise ValueError(f"pad_lon/pad_lat must be >= 0, got {pad_lon!r}, {pad_lat!r}")
    lon_w, lon_e, lat_s, lat_n = bbox
    return (
        lon_w - pad_lon,
        lon_e + pad_lon,
        max(-90.0, lat_s - pad_lat),
        min(90.0, lat_n + pad_lat),
    )


# ---------------------------------------------------------------------------
# 1. verify
# ---------------------------------------------------------------------------

def verify(path: str | os.PathLike, wgrib2: str | None = None) -> int:
    """Return the number of GRIB2 records in *path*.

    Wraps ``wgrib2 -s`` and counts its output lines. Raises :class:`GribError`
    if wgrib2 fails, is absent, or the file parses to zero records while
    being non-empty on disk — wgrib2 stays silent on malformed input, so
    we treat that as a corrupt / non-GRIB2 file.
    """
    exe = _resolve_or_grib_error(wgrib2)
    try:
        proc = subprocess.run(
            [exe, "-s", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise GribError(f"{exe} -s failed on {path}: {e.stderr.strip()}") from e
    n = len(proc.stdout.splitlines())
    if n == 0:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > 0:
            raise GribError(
                f"{path} has {size} bytes but wgrib2 parsed zero records "
                f"— file is not valid GRIB2"
            )
    return n


# ---------------------------------------------------------------------------
# 2. crop
# ---------------------------------------------------------------------------

def crop(
    src: str | os.PathLike,
    dst: str | os.PathLike,
    bbox: Bbox,
    wgrib2: str | None = None,
    omp_threads: int | None = None,
) -> Path:
    """Geographic subset of *src* into *dst*.

    *bbox* is ``(lon_w, lon_e, lat_s, lat_n)`` in degrees. Wraps
    ``wgrib2 -small_grib``. Creates ``dst``'s parent directory if missing.
    Returns the destination :class:`Path`.

    *omp_threads* sets ``OMP_NUM_THREADS`` for this wgrib2 invocation.
    ``None`` reads ``SHARKTOPUS_OMP_THREADS`` from the environment; if
    that's also unset, wgrib2 runs single-threaded (its default). Use
    :func:`suggest_omp_threads` to pick a safe value given your
    concurrency.
    """
    lon_w, lon_e, lat_s, lat_n = bbox
    _validate_bbox(lon_w, lon_e, lat_s, lat_n)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    exe = _resolve_or_grib_error(wgrib2)
    env = _env_with_omp(_resolve_omp_threads(omp_threads))
    try:
        subprocess.run(
            [
                exe, str(src),
                "-small_grib", f"{lon_w}:{lon_e}", f"{lat_s}:{lat_n}",
                str(dst),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        raise GribError(f"{exe} -small_grib failed: {e.stderr.strip()}") from e
    return dst


# ---------------------------------------------------------------------------
# 3. filter_vars_levels
# ---------------------------------------------------------------------------

def filter_vars_levels(
    src: str | os.PathLike,
    dst: str | os.PathLike,
    variables: Iterable[str],
    levels: Iterable[str],
    wgrib2: str | None = None,
    omp_threads: int | None = None,
) -> Path:
    """Filter *src* to records matching *variables* AND *levels*.

    Each element of *variables* / *levels* is taken as a wgrib2 regex
    alternative. ``wgrib2 -match`` is applied twice: once for the variable
    pattern, once for the level pattern.

    *omp_threads*: see :func:`crop`. Read from
    ``SHARKTOPUS_OMP_THREADS`` when not passed.
    """
    var_list = list(variables)
    lev_list = list(levels)
    if not var_list or not lev_list:
        raise ValueError("variables and levels must both be non-empty")
    var_pattern = "(" + "|".join(var_list) + ")"
    lev_pattern = "(" + "|".join(lev_list) + ")"
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    exe = _resolve_or_grib_error(wgrib2)
    env = _env_with_omp(_resolve_omp_threads(omp_threads))
    try:
        subprocess.run(
            [
                exe, str(src),
                "-match", f":{var_pattern}:",
                "-match", f":{lev_pattern}:",
                "-grib", str(dst),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        raise GribError(f"{exe} -match failed: {e.stderr.strip()}") from e
    return dst


# ---------------------------------------------------------------------------
# 4. parse_idx
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdxRecord:
    """One line of a GFS `.idx` file."""
    record: int       # 1-based record number
    offset: int       # byte offset into the .grib2 file
    date: str         # e.g. "d=2024012100"
    variable: str     # e.g. "TMP"
    level: str        # e.g. "500 mb"
    forecast: str     # e.g. "6 hour fcst"

    @property
    def key(self) -> str:
        """A ``"VAR:LEVEL"`` shorthand useful for filtering."""
        return f"{self.variable}:{self.level}"


def parse_idx(text: str) -> list[IdxRecord]:
    """Parse the content of a GFS `.idx` file.

    Each line has the format ``record:offset:date:var:level:forecast``. Lines
    with fewer than 6 colon-separated fields are skipped. Records are
    returned in ``record`` order (usually already sorted in source files).
    """
    out: list[IdxRecord] = []
    for line in text.strip().splitlines():
        parts = line.split(":", 5)
        if len(parts) < 6:
            continue
        try:
            rec = int(parts[0])
            off = int(parts[1])
        except ValueError:
            continue
        out.append(
            IdxRecord(
                record=rec,
                offset=off,
                date=parts[2],
                variable=parts[3],
                level=parts[4],
                forecast=parts[5].rstrip("\n"),
            )
        )
    out.sort(key=lambda r: r.record)
    return out


# ---------------------------------------------------------------------------
# 5. byte_ranges
# ---------------------------------------------------------------------------

def byte_ranges(
    records: list[IdxRecord],
    wanted: Iterable[IdxRecord] | Iterable[str],
    total_size: int,
) -> list[tuple[int, int]]:
    """Consolidated HTTP Range tuples covering *wanted* within *records*.

    *records* must be the full parsed .idx (needed to compute the end offset
    of the last wanted record). *wanted* may be a subset of
    :class:`IdxRecord` instances or ``"VAR:LEVEL"`` strings. *total_size* is
    the length of the underlying .grib2 file (from a HEAD request).

    Returns a list of ``(start, end)`` inclusive byte ranges. Adjacent
    ranges are merged to minimise HTTP round-trips.
    """
    if not records:
        return []
    sorted_records = sorted(records, key=lambda r: r.record)
    record_offsets = [r.offset for r in sorted_records]
    by_record: dict[int, int] = {r.record: i for i, r in enumerate(sorted_records)}

    wanted_records: list[IdxRecord] = []
    wanted = list(wanted)
    if wanted and isinstance(wanted[0], str):
        wanted_keys = set(wanted)  # type: ignore[arg-type]
        wanted_records = [r for r in sorted_records if r.key in wanted_keys]
    else:
        wanted_records = sorted(wanted, key=lambda r: r.record)  # type: ignore[arg-type]
    if not wanted_records:
        return []

    raw: list[tuple[int, int]] = []
    for r in wanted_records:
        start = r.offset
        idx = by_record.get(r.record)
        if idx is None:
            continue
        if idx + 1 < len(sorted_records):
            end = record_offsets[idx + 1] - 1
        else:
            end = total_size - 1
        raw.append((start, end))

    raw.sort()
    merged: list[tuple[int, int]] = [raw[0]]
    for start, end in raw[1:]:
        prev_start, prev_end = merged[-1]
        if start == prev_end + 1:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------------------
# 6. rename_by_validity
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"d=(\d{10})")
_FCST_RE = re.compile(r"(\d+)\s+hour\s+fcst:")


def rename_by_validity(
    path: str | os.PathLike,
    wgrib2: str | None = None,
    overwrite: bool = True,
) -> Path:
    """Rename a GRIB2 file to ``gfs.0p25.{YYYYMMDDHH}.f{PPP}.grib2``.

    Calls ``wgrib2 -v`` on *path*, extracts the date (``d=...``) and
    forecast hour from the first record, and renames in place. Returns the
    new path. If *overwrite* is False and the target already exists, raises
    :class:`GribError`.
    """
    src = Path(path).resolve()
    exe = _resolve_or_grib_error(wgrib2)
    try:
        proc = subprocess.run(
            [exe, "-v", str(src)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise GribError(f"{exe} -v failed on {src}: {e.stderr.strip()}") from e

    lines = proc.stdout.splitlines()
    if not lines:
        raise GribError(f"{wgrib2} -v returned no output for {src}")
    first = lines[0]
    m_date = _DATE_RE.search(first)
    if not m_date:
        raise GribError(f"no date (d=YYYYMMDDHH) in: {first}")
    date_str = m_date.group(1)

    if first.rstrip().endswith("anl:"):
        prog = 0
    else:
        m_fcst = _FCST_RE.search(first)
        prog = int(m_fcst.group(1)) if m_fcst else 0

    new_name = f"gfs.0p25.{date_str}.f{prog:03d}.grib2"
    new_path = src.parent / new_name
    if new_path == src:
        return src
    if new_path.exists():
        if not overwrite:
            raise GribError(f"target exists: {new_path}")
        new_path.unlink()
    src.rename(new_path)
    return new_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _validate_bbox(lon_w: float, lon_e: float, lat_s: float, lat_n: float) -> None:
    if lat_n <= lat_s:
        raise ValueError(f"lat_n ({lat_n}) must be > lat_s ({lat_s})")
    if lon_e <= lon_w:
        raise ValueError(f"lon_e ({lon_e}) must be > lon_w ({lon_w})")


def _resolve_or_grib_error(explicit: str | os.PathLike | None) -> str:
    """Internal helper: turn a missing wgrib2 into :class:`GribError`.

    The public :func:`ensure_wgrib2` raises :class:`WgribNotFoundError`;
    callers of :mod:`sharktopus.grib` expect the grib-specific exception,
    so we re-raise.
    """
    try:
        return ensure_wgrib2(explicit)
    except WgribNotFoundError as e:
        raise GribError(str(e)) from e


def have_wgrib2(wgrib2: str | None = None) -> bool:
    """Return True if a usable wgrib2 binary can be resolved.

    Checks the full resolution chain (explicit arg, ``SHARKTOPUS_WGRIB2``,
    bundled binary, ``$PATH``), not just ``$PATH``.
    """
    return resolve_wgrib2(wgrib2) is not None


__all__ = [
    "Bbox",
    "DEFAULT_WRF_PAD_LAT",
    "DEFAULT_WRF_PAD_LON",
    "GribError",
    "IdxRecord",
    "WgribNotFoundError",
    "bundled_wgrib2",
    "byte_ranges",
    "crop",
    "ensure_wgrib2",
    "expand_bbox",
    "filter_vars_levels",
    "have_wgrib2",
    "parse_idx",
    "rename_by_validity",
    "resolve_wgrib2",
    "suggest_omp_threads",
    "verify",
]
