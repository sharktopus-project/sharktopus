"""Default priority list + availability-based filtering.

The preferred order a client walks through when fetching one step:

* Cloud-side crop first (``aws_crop``, ``gcloud_crop``, ``azure_crop``)
  — the Lambda / Cloud Run / Container Apps service does the
  byte-range + wgrib2 work server-side and returns only the cropped
  bytes. Orders of magnitude faster when the bbox is small.
  ``supports()`` checks that the provider's SDK + credentials are
  present, so each entry drops out of auto-priority on machines
  without them, and the matching quota gate blocks it when paid usage
  isn't authorised.
* Plain cloud mirrors (``gcloud``/``aws``/``azure``) — full-file or
  client-side byte-range. Takes over when cloud-crop is blocked.
* ``rda`` picks up pre-2021 dates the cloud mirrors don't have.
* ``nomads`` last — origin infrastructure is the most rate-limited;
  useful mostly when the cycle is fresh enough that cloud mirrors
  haven't staged it yet.

``nomads_filter`` is intentionally NOT in the default list — its value
is server-side subsetting, which requires the caller to pass
``variables`` + ``levels``. Include it explicitly in ``priority=``
when you want it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .registry import source_default_workers, source_supports

__all__ = [
    "DEFAULT_PRIORITY",
    "available_sources",
    "default_max_workers",
]


DEFAULT_PRIORITY: tuple[str, ...] = (
    "aws_crop", "gcloud_crop", "azure_crop",
    "gcloud", "aws", "azure", "rda", "nomads",
)


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
