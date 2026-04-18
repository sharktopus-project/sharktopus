"""Batch-download orchestrator — Layer 2.

Drop-in replacement for CONVECT's ``menu_gfs.download_batch``:
iterates over a list of cycle timestamps, within each cycle iterates
over the requested forecast steps, and for each step tries the sources
in ``priority`` order until one succeeds. Signature mirrors
``download_batch_cli.py`` (``lat_s/lat_n/lon_w/lon_e`` separate floats
rather than a tuple) so callers migrating from CONVECT don't have to
rewrite their call sites.

Source registry is a plain dict keyed by name. Built-in registrations:

* ``nomads`` — :mod:`sharktopus.sources.nomads` (full-file, NOAA)
* ``aws`` — :mod:`sharktopus.sources.aws` (AWS Open Data mirror)
* ``gcloud`` — :mod:`sharktopus.sources.gcloud` (Google Cloud mirror)
* ``azure`` — :mod:`sharktopus.sources.azure` (Azure Blob mirror)
* ``rda`` — :mod:`sharktopus.sources.rda` (NCAR long-term archive)
* ``nomads_filter`` — :mod:`sharktopus.sources.nomads_filter`
  (server-side subset)

Concurrency — each source publishes a ``DEFAULT_MAX_WORKERS`` tuned
below its mirror's observed throttling threshold. :func:`fetch_batch`
runs steps in parallel with a ``ThreadPoolExecutor`` sized to the
*minimum* across the priority list (so the slowest-throttled mirror
paces the whole pool, not the fastest one). Callers who know better
can override via ``max_workers=...``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import grib
from .sources import SourceUnavailable, aws, azure, gcloud, nomads, nomads_filter, rda

__all__ = [
    "SourceRegistry",
    "default_max_workers",
    "fetch_batch",
    "generate_timestamps",
    "register_source",
    "registered_sources",
    "source_default_workers",
]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SourceFn = Callable[..., Path]


class SourceRegistry(dict[str, _SourceFn]):
    """Name → ``fetch_step`` callable. Just a dict, subclassed for typing."""


_REGISTRY = SourceRegistry(
    nomads=nomads.fetch_step,
    nomads_filter=nomads_filter.fetch_step,
    aws=aws.fetch_step,
    gcloud=gcloud.fetch_step,
    azure=azure.fetch_step,
    rda=rda.fetch_step,
)


# Per-source concurrency ceiling. nomads and nomads_filter are capped
# lower because NOAA's origin infrastructure is the only one with
# observed throttling at low QPS. The cloud mirrors (aws/gcloud/azure)
# absorb much more.
_WORKER_DEFAULTS: dict[str, int] = {
    "nomads": 2,
    "nomads_filter": 2,
    "aws": aws.DEFAULT_MAX_WORKERS,
    "gcloud": gcloud.DEFAULT_MAX_WORKERS,
    "azure": azure.DEFAULT_MAX_WORKERS,
    "rda": rda.DEFAULT_MAX_WORKERS,
}


def register_source(name: str, fetch_step: _SourceFn, *, max_workers: int = 1) -> None:
    """Register a source so :func:`fetch_batch` can route priority to it.

    *max_workers* becomes this source's published throttle ceiling (used
    by :func:`default_max_workers`). Default is 1 (serial) — you must
    opt in to parallelism explicitly once you've verified your mirror
    won't 429 / 503 under load.
    """
    _REGISTRY[name] = fetch_step
    _WORKER_DEFAULTS[name] = int(max_workers)


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)


def source_default_workers(name: str) -> int:
    """Return the published throttle ceiling for *name* (falls back to 1)."""
    return _WORKER_DEFAULTS.get(name, 1)


def default_max_workers(priority: Sequence[str]) -> int:
    """Conservative pool size for a priority list.

    Returns the *minimum* ``DEFAULT_MAX_WORKERS`` across all sources in
    the list — any step may fall back to any of them, so we must size
    the pool for the most-throttled mirror. Never returns less than 1.
    """
    if not priority:
        return 1
    return max(1, min(source_default_workers(n) for n in priority))


# ---------------------------------------------------------------------------
# Timestamp expansion (YYYYMMDDHH strings)
# ---------------------------------------------------------------------------

def _parse_stamp(stamp: str) -> datetime:
    if len(stamp) != 10 or not stamp.isdigit():
        raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
    return datetime.strptime(stamp, "%Y%m%d%H")


def generate_timestamps(start: str, end: str, step: int = 6) -> list[str]:
    """Return every ``YYYYMMDDHH`` cycle from *start* to *end* inclusive.

    *step* is in hours and must be positive. CONVECT uses 6 as the
    default (four GFS cycles per day); we honor that.
    """
    if step <= 0:
        raise ValueError(f"step must be > 0, got {step}")
    s = _parse_stamp(start)
    e = _parse_stamp(end)
    if e < s:
        raise ValueError(f"end ({end}) < start ({start})")
    out: list[str] = []
    t = s
    dt = timedelta(hours=step)
    while t <= e:
        out.append(t.strftime("%Y%m%d%H"))
        t += dt
    return out


# ---------------------------------------------------------------------------
# Batch orchestrator
# ---------------------------------------------------------------------------

def _one_step(
    date: str, cycle: str, fxx: int,
    priority: Sequence[str],
    common: dict[str, Any],
    variables: list[str] | None,
    levels: list[str] | None,
) -> tuple[Path | None, list[tuple[str, Exception]]]:
    """Try the priority list for one (date, cycle, fxx); return (path_or_None, errors)."""
    errors: list[tuple[str, Exception]] = []
    for name in priority:
        fetch = _REGISTRY[name]
        kwargs = dict(common)
        if name == "nomads_filter":
            kwargs["variables"] = list(variables or [])
            kwargs["levels"] = list(levels or [])
        try:
            return fetch(date, cycle, fxx, **kwargs), errors
        except SourceUnavailable as e:
            errors.append((name, e))
            continue
    return None, errors


def fetch_batch(
    *,
    timestamps: Sequence[str],
    lat_s: float,
    lat_n: float,
    lon_w: float,
    lon_e: float,
    ext: int = 24,
    interval: int = 3,
    priority: Sequence[str] = ("nomads_filter", "nomads"),
    variables: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    max_workers: int | None = None,
    on_step_ok: Callable[[str, str, int, Path], None] | None = None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None = None,
) -> list[Path]:
    """Download every ``(cycle, fxx)`` step implied by the inputs.

    Parameters mirror CONVECT's ``download_batch``:

    * each element of *timestamps* is a ``YYYYMMDDHH`` string
      (date + cycle concatenated).
    * *ext* is the forecast horizon in hours (so fxx runs 0..ext).
    * *interval* is the step between fxx values.
    * *priority* is the list of source names (registered via
      :func:`register_source`) tried in order per step.

    ``nomads_filter`` needs *variables* and *levels*; if it's in the
    priority list they're required. Other sources consume the bbox plus
    whatever they support.

    *max_workers* controls step-level parallelism. When omitted, uses
    :func:`default_max_workers` for the priority list — the minimum
    throttle ceiling across the listed sources. Set to 1 for fully
    serial downloads (useful when debugging or on very slow disks).

    Returns the list of produced Paths in completion order (not request
    order). Steps where every source in the priority list raised
    :class:`~sharktopus.sources.SourceUnavailable` are reported via
    *on_step_fail* and skipped; any other exception is re-raised.
    """
    if not timestamps:
        raise ValueError("timestamps must be non-empty")
    if ext < 0:
        raise ValueError(f"ext must be >= 0, got {ext}")
    if interval <= 0:
        raise ValueError(f"interval must be > 0, got {interval}")
    if not priority:
        raise ValueError("priority must be non-empty")
    unknown = set(priority) - set(_REGISTRY)
    if unknown:
        raise ValueError(
            f"unknown source(s) in priority: {sorted(unknown)}. "
            f"registered: {registered_sources()}"
        )
    if "nomads_filter" in priority and (variables is None or levels is None):
        raise ValueError(
            "'nomads_filter' in priority but variables/levels not set"
        )

    bbox = (float(lon_w), float(lon_e), float(lat_s), float(lat_n))
    common: dict[str, Any] = {
        "bbox": bbox,
        "pad_lon": pad_lon,
        "pad_lat": pad_lat,
        "product": product,
    }
    if dest is not None:
        common["dest"] = dest
    if root is not None:
        common["root"] = root

    var_list = list(variables) if variables is not None else None
    lev_list = list(levels) if levels is not None else None

    fxx_range = list(range(0, ext + 1, interval))
    jobs: list[tuple[str, str, int]] = []
    for stamp in timestamps:
        if len(stamp) != 10 or not stamp.isdigit():
            raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
        date, cycle = stamp[:8], stamp[8:]
        for fxx in fxx_range:
            jobs.append((date, cycle, fxx))

    n_workers = max_workers if max_workers is not None else default_max_workers(priority)
    n_workers = max(1, min(n_workers, len(jobs)))

    outputs: list[Path] = []

    if n_workers == 1:
        # Serial path — preserves strict ordering, useful for tests and
        # slow-disk scenarios. No thread overhead either.
        for date, cycle, fxx in jobs:
            ok, errors = _one_step(date, cycle, fxx, priority, common, var_list, lev_list)
            if ok is not None:
                outputs.append(ok)
                if on_step_ok is not None:
                    on_step_ok(date, cycle, fxx, ok)
            elif on_step_fail is not None:
                on_step_fail(date, cycle, fxx, errors)
        return outputs

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_one_step, date, cycle, fxx, priority, common, var_list, lev_list):
                (date, cycle, fxx)
            for date, cycle, fxx in jobs
        }
        for fut in as_completed(futures):
            date, cycle, fxx = futures[fut]
            ok, errors = fut.result()
            if ok is not None:
                outputs.append(ok)
                if on_step_ok is not None:
                    on_step_ok(date, cycle, fxx, ok)
            elif on_step_fail is not None:
                on_step_fail(date, cycle, fxx, errors)

    return outputs
