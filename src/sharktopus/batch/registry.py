"""Source registry — name → ``fetch_step`` routing for :func:`fetch_batch`.

Built-in registrations cover the six data mirrors in
:mod:`sharktopus.sources`. :func:`register_source` lets callers add
custom mirrors (private data lakes, experimental forecast systems)
without touching the library.

Every registered source has three published attributes:

* ``fetch_step`` — the callable that downloads one ``(date, cycle, fxx)``.
* ``DEFAULT_MAX_WORKERS`` — the concurrency ceiling the mirror tolerates
  before it starts throttling.
* ``supports(date, cycle=None, *, now=None)`` — availability predicate
  used by :mod:`sharktopus.batch.priority` to filter the priority list.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..sources import (
    aws,
    aws_crop,
    azure,
    gcloud,
    gcloud_crop,
    nomads,
    nomads_filter,
    rda,
)

__all__ = [
    "SourceRegistry",
    "register_source",
    "registered_sources",
    "source_default_workers",
    "source_supports",
]


_SourceFn = Callable[..., Path]
_SupportsFn = Callable[..., bool]


class SourceRegistry(dict[str, _SourceFn]):
    """Name → ``fetch_step`` callable. Plain dict, subclassed for typing clarity."""


_REGISTRY = SourceRegistry(
    nomads=nomads.fetch_step,
    nomads_filter=nomads_filter.fetch_step,
    aws=aws.fetch_step,
    aws_crop=aws_crop.fetch_step,
    gcloud=gcloud.fetch_step,
    gcloud_crop=gcloud_crop.fetch_step,
    azure=azure.fetch_step,
    rda=rda.fetch_step,
)


_WORKER_DEFAULTS: dict[str, int] = {
    "nomads": nomads.DEFAULT_MAX_WORKERS,
    "nomads_filter": nomads_filter.DEFAULT_MAX_WORKERS,
    "aws": aws.DEFAULT_MAX_WORKERS,
    "aws_crop": aws_crop.DEFAULT_MAX_WORKERS,
    "gcloud": gcloud.DEFAULT_MAX_WORKERS,
    "gcloud_crop": gcloud_crop.DEFAULT_MAX_WORKERS,
    "azure": azure.DEFAULT_MAX_WORKERS,
    "rda": rda.DEFAULT_MAX_WORKERS,
}


_SUPPORTS: dict[str, _SupportsFn] = {
    "nomads": nomads.supports,
    "nomads_filter": nomads_filter.supports,
    "aws": aws.supports,
    "aws_crop": aws_crop.supports,
    "gcloud": gcloud.supports,
    "gcloud_crop": gcloud_crop.supports,
    "azure": azure.supports,
    "rda": rda.supports,
}


def register_source(
    name: str,
    fetch_step: _SourceFn,
    *,
    max_workers: int = 1,
    supports: _SupportsFn | None = None,
) -> None:
    """Register a source so :func:`fetch_batch` can route priority to it.

    *max_workers* becomes this source's published throttle ceiling (used
    by :func:`sharktopus.batch.priority.default_max_workers`). Default is
    1 (serial) — opt in to parallelism explicitly once you've verified
    your mirror won't 429 / 503 under load.

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
    """Return sorted list of all registered source names."""
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


def get_registry() -> SourceRegistry:
    """Return the live registry. Prefer this over reaching for ``_REGISTRY``."""
    return _REGISTRY
