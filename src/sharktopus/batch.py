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
  (server-side subset; opt-in only)

Priority — :data:`DEFAULT_PRIORITY` is the preferred order when the
caller doesn't pass ``priority=``. :func:`available_sources` filters
it to the mirrors that can actually serve a given date. ``fetch_batch``
wires those together so the common case is just
``fetch_batch(timestamps=..., lat_s=..., ...)``.

Concurrency — each source publishes a ``DEFAULT_MAX_WORKERS`` tuned
below its mirror's observed throttling threshold. :func:`fetch_batch`
runs steps in parallel with a ``ThreadPoolExecutor`` sized to the
*minimum* across the priority list (so the slowest-throttled mirror
paces the whole pool, not the fastest one). Callers who know better
can override via ``max_workers=...``.
"""

from __future__ import annotations

import os
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import grib, wrf
from ._queue import MultiSourceQueue, Step
from .sources import (
    SourceUnavailable,
    aws,
    aws_crop,
    azure,
    gcloud,
    nomads,
    nomads_filter,
    rda,
)

__all__ = [
    "DEFAULT_PRIORITY",
    "SourceRegistry",
    "available_sources",
    "default_max_workers",
    "fetch_batch",
    "generate_timestamps",
    "register_source",
    "registered_sources",
    "source_default_workers",
    "source_supports",
]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SourceFn = Callable[..., Path]
_SupportsFn = Callable[..., bool]


class SourceRegistry(dict[str, _SourceFn]):
    """Name → ``fetch_step`` callable. Just a dict, subclassed for typing."""


_REGISTRY = SourceRegistry(
    nomads=nomads.fetch_step,
    nomads_filter=nomads_filter.fetch_step,
    aws=aws.fetch_step,
    aws_crop=aws_crop.fetch_step,
    gcloud=gcloud.fetch_step,
    azure=azure.fetch_step,
    rda=rda.fetch_step,
)

# Per-source concurrency ceiling (read from each module).
_WORKER_DEFAULTS: dict[str, int] = {
    "nomads": nomads.DEFAULT_MAX_WORKERS,
    "nomads_filter": nomads_filter.DEFAULT_MAX_WORKERS,
    "aws": aws.DEFAULT_MAX_WORKERS,
    "aws_crop": aws_crop.DEFAULT_MAX_WORKERS,
    "gcloud": gcloud.DEFAULT_MAX_WORKERS,
    "azure": azure.DEFAULT_MAX_WORKERS,
    "rda": rda.DEFAULT_MAX_WORKERS,
}

# Per-source supports(date, cycle=None, *, now=None) -> bool.
_SUPPORTS: dict[str, _SupportsFn] = {
    "nomads": nomads.supports,
    "nomads_filter": nomads_filter.supports,
    "aws": aws.supports,
    "aws_crop": aws_crop.supports,
    "gcloud": gcloud.supports,
    "azure": azure.supports,
    "rda": rda.supports,
}


# Default preference order when the caller doesn't pass ``priority=``.
# Ordering reflects availability, cost, and wire efficiency:
#   * Cloud-side crop first (``aws_crop``) — Lambda does the byte-range
#     + wgrib2 work server-side and returns only the cropped bytes.
#     Orders of magnitude faster when the bbox is small. ``supports()``
#     checks AWS credentials, so this entry drops out of auto-priority
#     on machines without them, and ``aws_quota`` blocks it when paid
#     usage isn't authorised. ``gcloud_crop`` / ``azure_crop`` will
#     slot in next to it once phase 2 lands.
#   * Plain cloud mirrors (``gcloud``/``aws``/``azure``) — full-file or
#     client-side byte-range. Takes over when cloud-crop is blocked.
#   * ``rda`` picks up pre-2021 dates the cloud mirrors don't have.
#   * ``nomads`` last because the origin infrastructure is the most
#     rate-limited and useful mostly when the cycle is fresh enough
#     that cloud mirrors haven't staged it yet.
# ``nomads_filter`` is intentionally NOT here — its value is server-side
# subsetting, which requires the caller to pass ``variables`` +
# ``levels``. Include it explicitly in ``priority=`` when you want it.
DEFAULT_PRIORITY: tuple[str, ...] = (
    "aws_crop",
    "gcloud", "aws", "azure", "rda", "nomads",
)


def register_source(
    name: str,
    fetch_step: _SourceFn,
    *,
    max_workers: int = 1,
    supports: _SupportsFn | None = None,
) -> None:
    """Register a source so :func:`fetch_batch` can route priority to it.

    *max_workers* becomes this source's published throttle ceiling (used
    by :func:`default_max_workers`). Default is 1 (serial) — you must
    opt in to parallelism explicitly once you've verified your mirror
    won't 429 / 503 under load.

    *supports* is a ``(date, cycle=None, *, now=None) -> bool`` callable
    that decides whether this source has a given date. The default
    returns ``True`` (always available), which is fine for tests and
    custom mirrors with no known lower bound.
    """
    _REGISTRY[name] = fetch_step
    _WORKER_DEFAULTS[name] = int(max_workers)
    _SUPPORTS[name] = supports if supports is not None else _always_true


def _always_true(*_a: Any, **_kw: Any) -> bool:
    return True


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)


def source_default_workers(name: str) -> int:
    """Return the published throttle ceiling for *name* (falls back to 1)."""
    return _WORKER_DEFAULTS.get(name, 1)


def source_supports(
    name: str,
    date: str,
    cycle: str | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return ``True`` if source *name* can serve *date* (cycle optional)."""
    fn = _SUPPORTS.get(name)
    if fn is None:
        return False
    return fn(date, cycle, now=now)


def default_max_workers(priority: Sequence[str]) -> int:
    """Conservative pool size for a priority list.

    Returns the *minimum* ``DEFAULT_MAX_WORKERS`` across all sources in
    the list — any step may fall back to any of them, so we must size
    the pool for the most-throttled mirror. Never returns less than 1.
    """
    if not priority:
        return 1
    return max(1, min(source_default_workers(n) for n in priority))


def available_sources(
    date: str,
    cycle: str | None = None,
    *,
    now: datetime | None = None,
    candidates: Sequence[str] | None = None,
) -> list[str]:
    """Return the subset of *candidates* (default :data:`DEFAULT_PRIORITY`) that can serve *date*.

    Preserves the order of *candidates* so the return value is a valid
    priority list directly. Pass ``candidates=registered_sources()`` to
    scan every registered mirror, not just the default preference.
    """
    names = tuple(candidates) if candidates is not None else DEFAULT_PRIORITY
    return [n for n in names if source_supports(n, date, cycle, now=now)]


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

# Full-file mirrors that also support byte-range mode when the caller
# passes variables + levels. nomads_filter is separate because it needs
# server-side subsetting (variables/levels mandatory, no fallback). rda
# does not publish .idx sidecars, but borrows them from its sibling
# mirrors (aws/gcloud/azure) when available and transparently falls back
# to full-file + local filter on pre-2021 dates where no sibling has it.
_BYTE_RANGE_CAPABLE = frozenset({"aws", "aws_crop", "azure", "gcloud", "nomads", "rda"})


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
        elif name in _BYTE_RANGE_CAPABLE and variables and levels:
            kwargs["variables"] = list(variables)
            kwargs["levels"] = list(levels)
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
    priority: Sequence[str] | None = None,
    variables: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    max_workers: int | None = None,
    spread: bool | None = None,
    attempt_timeout: float | None = None,
    now: datetime | None = None,
    on_step_ok: Callable[[str, str, int, Path], None] | None = None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None = None,
) -> list[Path]:
    """Download every ``(cycle, fxx)`` step implied by the inputs.

    Parameters mirror CONVECT's ``download_batch``:

    * each element of *timestamps* is a ``YYYYMMDDHH`` string
      (date + cycle concatenated).
    * *ext* is the forecast horizon in hours (so fxx runs 0..ext).
    * *interval* is the step between fxx values.
    * *priority* is the ordered list of source names tried per step.
      ``None`` (the default) means "derive from :data:`DEFAULT_PRIORITY`
      filtered to sources that can serve the first timestamp" — so
      recent dates get the full cloud-mirror fan-out while pre-2021
      dates automatically fall through to RDA.

    ``nomads_filter`` needs *variables* and *levels*; if it's in the
    priority list they're required. Other sources consume the bbox plus
    whatever they support.

    *spread* selects between two concurrency models:

    * ``False`` (or when exactly one source is eligible) — classic
      fallback chain. Steps go through the priority list in order
      until one succeeds; step-level parallelism is sized by
      :func:`default_max_workers` (the minimum throttle ceiling across
      the list).
    * ``True`` — spread mode backed by
      :class:`~sharktopus._queue.MultiSourceQueue`. Every eligible
      source runs its own worker pool at the source's own
      ``DEFAULT_MAX_WORKERS``, pulling from a single globally ordered
      queue (oldest ``(date, cycle, fxx)`` first). A step that fails in
      source A is re-enqueued with A blacklisted; another source's pool
      picks it up at its own pace. Total concurrency rises to
      ``sum(workers per source)`` without any source exceeding its
      published ceiling.
    * ``None`` (default) — spread when *priority* was auto-resolved
      (i.e. caller didn't pass ``priority=``) and has more than one
      source; classic fallback chain otherwise. An explicit
      ``priority=[...]`` is read as a deliberate preference ordering
      and preserves first-wins semantics unless ``spread=True`` is
      also passed.

    *max_workers* (fallback-chain mode only) overrides the pool size.
    In spread mode concurrency is derived per-source and cannot be
    collapsed to a single number, so this is ignored.

    *attempt_timeout* (spread mode): wall-clock seconds allowed per
    attempt on a given source. When exceeded, the download is aborted
    cooperatively and the step re-enqueued with that source blacklisted
    so a less loaded mirror can try. ``None`` = no deadline.

    *now* lets tests freeze the clock used by availability filtering.
    Leave it ``None`` in production.

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

    priority_was_auto = priority is None
    if priority is None:
        first_date = str(timestamps[0])[:8]
        first_cycle = str(timestamps[0])[8:10] if len(str(timestamps[0])) >= 10 else None
        priority = available_sources(first_date, first_cycle, now=now)
        if not priority:
            raise SourceUnavailable(
                f"no registered source can serve {first_date}. "
                f"Checked: {list(DEFAULT_PRIORITY)}"
            )
    if not priority:
        raise ValueError("priority must be non-empty")
    unknown = set(priority) - set(_REGISTRY)
    if unknown:
        raise ValueError(
            f"unknown source(s) in priority: {sorted(unknown)}. "
            f"registered: {registered_sources()}"
        )
    if "nomads_filter" in priority:
        # Fall back to the WRF-canonical set when the caller hasn't
        # narrowed it down. Treat variables / levels independently so a
        # caller can override just one (e.g. add GUST to DEFAULT_VARS
        # while keeping the canonical level list).
        if variables is None:
            variables = wrf.DEFAULT_VARS
        if levels is None:
            levels = wrf.DEFAULT_LEVELS

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

    # Decide concurrency model. Spread requires >1 eligible source and
    # nomads_filter is excluded (needs mandatory variables/levels in a
    # way the generic worker loop doesn't carry yet — keep it simple
    # and route it through the fallback chain). When the caller passed
    # priority= explicitly we read that as a deliberate preference
    # ordering (first source should win), so default to classic fallback
    # chain; auto-resolved priorities default to spread.
    spread_eligible = (
        len(priority) > 1 and "nomads_filter" not in priority
    )
    if spread is None:
        use_spread = spread_eligible and priority_was_auto
    else:
        use_spread = bool(spread) and spread_eligible

    outputs: list[Path] = []

    if use_spread:
        _maybe_warn_omp_headroom(priority)
        _run_spread(
            jobs=jobs,
            priority=priority,
            common=common,
            var_list=var_list,
            lev_list=lev_list,
            outputs=outputs,
            attempt_timeout=attempt_timeout,
            on_step_ok=on_step_ok,
            on_step_fail=on_step_fail,
        )
        return outputs

    n_workers = max_workers if max_workers is not None else default_max_workers(priority)
    n_workers = max(1, min(n_workers, len(jobs)))

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


# ---------------------------------------------------------------------------
# OMP headroom warning — fires once per process when spread mode is
# about to leave cores idle that wgrib2 could use. See
# :func:`sharktopus.grib.suggest_omp_threads`.
# ---------------------------------------------------------------------------

_OMP_HEADROOM_WARNED = False
_OMP_HEADROOM_MIN_FREE_CORES = 8  # only warn when at least 8 cores go unused


def _maybe_warn_omp_headroom(priority: Sequence[str]) -> None:
    """Emit a one-shot warning if wgrib2 could use idle cores.

    Fires only when: spread mode is active, ``SHARKTOPUS_OMP_THREADS`` is
    unset, ``OMP_NUM_THREADS`` is unset (or 1), and the box has at least
    ``_OMP_HEADROOM_MIN_FREE_CORES`` unused cores after accounting for
    the peak concurrent-crop count in spread mode.
    """
    global _OMP_HEADROOM_WARNED
    if _OMP_HEADROOM_WARNED:
        return
    if os.environ.get("SHARKTOPUS_OMP_THREADS"):
        return
    omp_env = os.environ.get("OMP_NUM_THREADS")
    if omp_env and omp_env.strip() not in ("", "1"):
        return
    cpu = os.cpu_count() or 1
    concurrent = sum(source_default_workers(s) for s in priority)
    free = cpu - concurrent
    if free < _OMP_HEADROOM_MIN_FREE_CORES:
        return
    suggested = grib.suggest_omp_threads(concurrent, cpu_count=cpu)
    if suggested <= 1:
        return
    _OMP_HEADROOM_WARNED = True
    warnings.warn(
        f"sharktopus: spread mode will run ~{concurrent} concurrent wgrib2 "
        f"crops on a {cpu}-core host. {free} cores are idle during crops. "
        f"Set SHARKTOPUS_OMP_THREADS={suggested} to let wgrib2 use them "
        f"(accumulates across long batches). Silence this by setting "
        f"SHARKTOPUS_OMP_THREADS or OMP_NUM_THREADS explicitly.",
        stacklevel=2,
    )


# ---------------------------------------------------------------------------
# Spread mode — :class:`MultiSourceQueue` backed by a thread pool per source
# ---------------------------------------------------------------------------

def _run_spread(
    *,
    jobs: list[tuple[str, str, int]],
    priority: Sequence[str],
    common: dict[str, Any],
    var_list: list[str] | None,
    lev_list: list[str] | None,
    outputs: list[Path],
    attempt_timeout: float | None,
    on_step_ok: Callable[[str, str, int, Path], None] | None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None,
) -> None:
    """Fill *outputs* by draining *jobs* through a :class:`MultiSourceQueue`.

    One worker thread per ``(source, worker-slot)`` pair: each pops the
    next step its source is eligible for, calls ``fetch_step`` with an
    optional cooperative *attempt_timeout* deadline, and on failure
    re-enqueues with the source blacklisted. Per-step errors accumulate
    across re-enqueues so *on_step_fail* gets the full cross-source
    trail on final failure.
    """
    queue = MultiSourceQueue(priority)
    for date, cycle, fxx in jobs:
        queue.push(Step(key=(date, cycle, fxx)))

    errors_by_key: dict[tuple, list[tuple[str, Exception]]] = {}
    succeeded: set[tuple] = set()
    state_lock = threading.Lock()

    def _kwargs_for(source: str) -> dict[str, Any]:
        kw = dict(common)
        if source == "nomads_filter":
            kw["variables"] = list(var_list or [])
            kw["levels"] = list(lev_list or [])
        elif source in _BYTE_RANGE_CAPABLE and var_list and lev_list:
            kw["variables"] = list(var_list)
            kw["levels"] = list(lev_list)
        return kw

    def worker(source: str) -> None:
        fetch = _REGISTRY[source]
        kwargs = _kwargs_for(source)
        while True:
            step = queue.pop(source)
            if step is None:
                return
            date, cycle, fxx = step.key
            deadline = (
                time.monotonic() + attempt_timeout
                if attempt_timeout is not None else None
            )
            try:
                path = fetch(date, cycle, fxx, deadline=deadline, **kwargs)
            except SourceUnavailable as e:
                with state_lock:
                    errors_by_key.setdefault(step.key, []).append((source, e))
                queue.push(replace(
                    step, blacklist=step.blacklist | {source},
                ))
                continue
            with state_lock:
                outputs.append(path)
                succeeded.add(step.key)
            if on_step_ok is not None:
                on_step_ok(date, cycle, fxx, path)
            queue.mark_done(step)

    threads: list[threading.Thread] = []
    for source in priority:
        for slot in range(source_default_workers(source)):
            t = threading.Thread(
                target=worker, args=(source,),
                name=f"sharktopus-{source}-{slot}", daemon=True,
            )
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    if on_step_fail is not None:
        for key, errs in errors_by_key.items():
            if key in succeeded:
                continue
            date, cycle, fxx = key
            on_step_fail(date, cycle, fxx, errs)
