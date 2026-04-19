"""Batch orchestrator — Layer 2.

Drop-in replacement for CONVECT's ``menu_gfs.download_batch``:
iterates over a list of cycle timestamps, within each cycle iterates
over the requested forecast steps, and for each step tries the sources
in ``priority`` order until one succeeds.

This file is the *orchestration* layer only — the moving parts live
in focused submodules so each one can be read and tested in isolation:

* :mod:`sharktopus.batch.registry` — name → ``fetch_step`` routing.
* :mod:`sharktopus.batch.priority` — ``DEFAULT_PRIORITY`` +
  ``available_sources`` filter.
* :mod:`sharktopus.batch.schedule` — timestamp expansion + job list.
* :mod:`sharktopus.batch.spread` — per-source-pool worker for spread
  mode.
* :mod:`sharktopus.batch.queue` — the blacklist-aware multi-source
  queue that spread mode drains.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import wrf
from ..io import grib
from ..sources import SourceUnavailable
from .priority import DEFAULT_PRIORITY, available_sources, default_max_workers
from .registry import get_registry, registered_sources
from .schedule import build_jobs
from .spread import maybe_warn_omp_headroom, run_spread

__all__ = ["fetch_batch"]


_BYTE_RANGE_CAPABLE = frozenset({"aws", "aws_crop", "azure", "gcloud", "nomads", "rda"})


# ---------------------------------------------------------------------------
# Priority + input resolution (extracted from fetch_batch to keep each
# stage reviewable in isolation)
# ---------------------------------------------------------------------------

def _resolve_priority(
    timestamps: Sequence[str],
    priority: Sequence[str] | None,
    now: datetime | None,
) -> tuple[list[str], bool]:
    """Return (priority_list, priority_was_auto).

    When *priority* is ``None`` the list is auto-derived from
    :data:`DEFAULT_PRIORITY` filtered through
    :func:`available_sources` against the first timestamp. When the
    caller passes one, it's validated against the registry.
    """
    if priority is None:
        first = str(timestamps[0])
        first_date = first[:8]
        first_cycle = first[8:10] if len(first) >= 10 else None
        derived = available_sources(first_date, first_cycle, now=now)
        if not derived:
            raise SourceUnavailable(
                f"no registered source can serve {first_date}. "
                f"Checked: {list(DEFAULT_PRIORITY)}"
            )
        return derived, True

    if not priority:
        raise ValueError("priority must be non-empty")
    registry = get_registry()
    unknown = set(priority) - set(registry)
    if unknown:
        raise ValueError(
            f"unknown source(s) in priority: {sorted(unknown)}. "
            f"registered: {registered_sources()}"
        )
    return list(priority), False


def _apply_nomads_filter_defaults(
    priority: Sequence[str],
    variables: Iterable[str] | None,
    levels: Iterable[str] | None,
) -> tuple[Iterable[str] | None, Iterable[str] | None]:
    """Fill in WRF-canonical vars/levels when ``nomads_filter`` is in use.

    ``nomads_filter`` requires server-side subsetting parameters. If the
    caller didn't specify, fall back to the WRF-canonical set so the
    common case works out of the box. Each dimension is filled
    independently so callers can override just one.
    """
    if "nomads_filter" not in priority:
        return variables, levels
    if variables is None:
        variables = wrf.DEFAULT_VARS
    if levels is None:
        levels = wrf.DEFAULT_LEVELS
    return variables, levels


def _build_common_kwargs(
    *,
    lon_w: float, lon_e: float, lat_s: float, lat_n: float,
    pad_lon: float, pad_lat: float,
    product: str,
    dest: str | Path | None,
    root: str | Path | None,
) -> dict[str, Any]:
    """Assemble the ``fetch_step`` kwargs common to every source."""
    bbox = (float(lon_w), float(lon_e), float(lat_s), float(lat_n))
    common: dict[str, Any] = {
        "bbox": bbox, "pad_lon": pad_lon, "pad_lat": pad_lat,
        "product": product,
    }
    if dest is not None:
        common["dest"] = dest
    if root is not None:
        common["root"] = root
    return common


def _decide_concurrency_mode(
    priority: Sequence[str],
    priority_was_auto: bool,
    spread: bool | None,
) -> bool:
    """Decide whether to use spread mode.

    Spread requires >1 eligible source and ``nomads_filter`` not present
    (its mandatory variables/levels need a different worker loop).
    Explicit ``priority=`` reads as deliberate preference → default to
    classic fallback chain; auto-resolved priorities default to spread.
    """
    spread_eligible = len(priority) > 1 and "nomads_filter" not in priority
    if spread is None:
        return spread_eligible and priority_was_auto
    return bool(spread) and spread_eligible


# ---------------------------------------------------------------------------
# Per-step fallback — used by both serial and parallel (non-spread) modes
# ---------------------------------------------------------------------------

def _one_step(
    date: str, cycle: str, fxx: int,
    priority: Sequence[str],
    common: dict[str, Any],
    variables: list[str] | None,
    levels: list[str] | None,
) -> tuple[Path | None, list[tuple[str, Exception]]]:
    """Try the priority list for one ``(date, cycle, fxx)``; return ``(path_or_None, errors)``."""
    registry = get_registry()
    errors: list[tuple[str, Exception]] = []
    for name in priority:
        fetch = registry[name]
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


def _run_fallback_chain(
    *,
    jobs: list[tuple[str, str, int]],
    priority: Sequence[str],
    common: dict[str, Any],
    var_list: list[str] | None,
    lev_list: list[str] | None,
    max_workers: int | None,
    outputs: list[Path],
    on_step_ok: Callable[[str, str, int, Path], None] | None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None,
) -> None:
    """Run the classic priority-list fallback chain (serial or pooled)."""
    n_workers = max_workers if max_workers is not None else default_max_workers(priority)
    n_workers = max(1, min(n_workers, len(jobs)))

    def handle(date: str, cycle: str, fxx: int,
               ok: Path | None, errors: list[tuple[str, Exception]]) -> None:
        if ok is not None:
            outputs.append(ok)
            if on_step_ok is not None:
                on_step_ok(date, cycle, fxx, ok)
        elif on_step_fail is not None:
            on_step_fail(date, cycle, fxx, errors)

    if n_workers == 1:
        # Serial path — preserves strict ordering, useful for tests and
        # slow-disk scenarios. No thread overhead either.
        for date, cycle, fxx in jobs:
            ok, errors = _one_step(date, cycle, fxx, priority, common, var_list, lev_list)
            handle(date, cycle, fxx, ok, errors)
        return

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_one_step, date, cycle, fxx, priority, common, var_list, lev_list):
                (date, cycle, fxx)
            for date, cycle, fxx in jobs
        }
        for fut in as_completed(futures):
            date, cycle, fxx = futures[fut]
            ok, errors = fut.result()
            handle(date, cycle, fxx, ok, errors)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
      ``None`` (default) means "derive from :data:`DEFAULT_PRIORITY`
      filtered to sources that can serve the first timestamp" — so
      recent dates get the full cloud-mirror fan-out while pre-2021
      dates automatically fall through to RDA.

    ``nomads_filter`` needs *variables* and *levels*; if it's in the
    priority list they're required.

    *spread* selects between two concurrency models:

    * ``False`` (or exactly one source eligible) — classic fallback
      chain. Steps walk the priority list in order; step-level
      parallelism sized to the minimum throttle ceiling.
    * ``True`` — spread mode (see :mod:`sharktopus.batch.spread`).
      One pool per source, draining a single globally ordered queue.
    * ``None`` (default) — spread when *priority* was auto-resolved
      and has more than one source; classic fallback chain otherwise.

    *max_workers* (fallback-chain mode only) overrides the pool size.
    Ignored in spread mode (per-source pools).

    *attempt_timeout* (spread mode): wall-clock seconds per attempt;
    when exceeded the step re-enqueues with that source blacklisted.

    *now* lets tests freeze the clock used by availability filtering.

    Returns the list of produced Paths in completion order. Steps where
    every source raised :class:`~sharktopus.sources.SourceUnavailable`
    are reported via *on_step_fail* and skipped; any other exception
    is re-raised.
    """
    if not timestamps:
        raise ValueError("timestamps must be non-empty")

    resolved_priority, priority_was_auto = _resolve_priority(
        timestamps, priority, now,
    )
    variables, levels = _apply_nomads_filter_defaults(
        resolved_priority, variables, levels,
    )
    common = _build_common_kwargs(
        lon_w=lon_w, lon_e=lon_e, lat_s=lat_s, lat_n=lat_n,
        pad_lon=pad_lon, pad_lat=pad_lat, product=product,
        dest=dest, root=root,
    )
    var_list = list(variables) if variables is not None else None
    lev_list = list(levels) if levels is not None else None
    jobs = build_jobs(timestamps, ext, interval)

    outputs: list[Path] = []

    if _decide_concurrency_mode(resolved_priority, priority_was_auto, spread):
        maybe_warn_omp_headroom(resolved_priority)
        run_spread(
            jobs=jobs, priority=resolved_priority, common=common,
            var_list=var_list, lev_list=lev_list, outputs=outputs,
            attempt_timeout=attempt_timeout,
            on_step_ok=on_step_ok, on_step_fail=on_step_fail,
        )
        return outputs

    _run_fallback_chain(
        jobs=jobs, priority=resolved_priority, common=common,
        var_list=var_list, lev_list=lev_list, max_workers=max_workers,
        outputs=outputs,
        on_step_ok=on_step_ok, on_step_fail=on_step_fail,
    )
    return outputs
