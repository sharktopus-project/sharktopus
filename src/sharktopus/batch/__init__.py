"""Batch orchestration subpackage.

Re-exports the public surface used by callers (``fetch_batch``,
``available_sources``, ``DEFAULT_PRIORITY``, etc.) so
``from sharktopus import batch; batch.fetch_batch(...)`` stays a
one-line import after the refactor that split the old 635-line
``batch.py`` into focused modules.

Internal structure:

* :mod:`.orchestrator` — the ``fetch_batch`` entry point.
* :mod:`.registry` — source name → ``fetch_step`` routing, plus
  :func:`register_source` for plugging in custom mirrors.
* :mod:`.priority` — ``DEFAULT_PRIORITY`` and availability filtering.
* :mod:`.schedule` — timestamp expansion + job-list construction.
* :mod:`.spread` — per-source-pool worker for spread mode.
* :mod:`.queue` — the blacklist-aware multi-source queue spread mode
  drains.
"""

from __future__ import annotations

from .orchestrator import fetch_batch
from .priority import DEFAULT_PRIORITY, available_sources, default_max_workers
from .queue import MultiSourceQueue, Step
from .registry import (
    _REGISTRY,
    _SUPPORTS,
    _WORKER_DEFAULTS,
    SourceRegistry,
    _always_true,
    register_source,
    registered_sources,
    source_default_workers,
    source_supports,
)
from .schedule import build_jobs, generate_timestamps

__all__ = [
    "DEFAULT_PRIORITY",
    "MultiSourceQueue",
    "SourceRegistry",
    "Step",
    "available_sources",
    "build_jobs",
    "default_max_workers",
    "fetch_batch",
    "generate_timestamps",
    "register_source",
    "registered_sources",
    "source_default_workers",
    "source_supports",
]
