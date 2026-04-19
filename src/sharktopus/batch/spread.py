"""Spread-mode worker: per-source thread pools draining one queue.

Classic fallback mode has all workers share the same priority list —
step tries source A, fails, tries B, etc. Fine for small batches.

Spread mode runs N independent pools (one per source, sized to each
source's ``DEFAULT_MAX_WORKERS``) pulling from a single globally
ordered :class:`~sharktopus.batch.queue.MultiSourceQueue`. A step that
fails in source A is re-enqueued with A blacklisted; another source's
pool picks it up at its own pace. Total concurrency rises to
``sum(workers per source)`` without any source exceeding its own
published ceiling.

Net effect on real batches: download rate bounded by the *aggregate*
of three or four mirrors' throttle ceilings instead of the single
slowest one.
"""

from __future__ import annotations

import os
import threading
import time
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

from ..io import grib
from ..sources import SourceUnavailable
from .queue import MultiSourceQueue, Step
from .registry import get_registry, source_default_workers

__all__ = ["run_spread", "maybe_warn_omp_headroom"]


_BYTE_RANGE_CAPABLE = frozenset({"aws", "aws_crop", "azure", "gcloud", "nomads", "rda"})


def run_spread(
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
    registry = get_registry()
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
        fetch = registry[source]
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


# ---------------------------------------------------------------------------
# OMP headroom warning — fires once per process when spread mode is
# about to leave cores idle that wgrib2 could use. See
# :func:`sharktopus.io.grib.suggest_omp_threads`.
# ---------------------------------------------------------------------------

_OMP_HEADROOM_WARNED = False
_OMP_HEADROOM_MIN_FREE_CORES = 8  # only warn when at least 8 cores go unused


def maybe_warn_omp_headroom(priority: Sequence[str]) -> None:
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
