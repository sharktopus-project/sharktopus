"""Cloud-provider-specific policy (quota gates, billing tracking).

Keeps the ``sources/`` package focused on *how* to fetch from each
mirror and isolates *whether we should fetch at all* — the free-tier
tracking, the ``SHARKTOPUS_ACCEPT_CHARGES`` gate, the running
invocation counter — in one place per provider.

* :mod:`sharktopus.cloud.aws_quota` — AWS Lambda free-tier tracker
  for :mod:`sharktopus.sources.aws_crop`.
* :mod:`sharktopus.cloud.gcloud_quota` — GCloud Cloud Run free-tier
  tracker for :mod:`sharktopus.sources.gcloud_crop`.

Future siblings (``azure_quota``) land here when phase-3 cloud-crop
sources ship.

Convenience re-exports (``load_quota``, ``quota_report``,
``can_use_cloud_crop``) let callers write
``from sharktopus.cloud import quota_report`` without reaching into
provider-specific submodules. ``quota_report(provider)`` dispatches to
the right backend by name.
"""

from __future__ import annotations

from . import aws_quota, gcloud_quota
from .aws_quota import (
    QuotaState,
    load_quota,
)

__all__ = [
    "QuotaState",
    "aws_quota",
    "can_use_cloud_crop",
    "gcloud_quota",
    "load_quota",
    "percent_of_free_tier_used",
    "quota_report",
]


def quota_report(provider: str = "aws") -> str:
    """Return a human-readable quota report for *provider*.

    Dispatches by provider name. ``aws`` → Lambda tracker, ``gcloud``
    → Cloud Run tracker. Raises ``ValueError`` on unknown providers.
    """
    if provider == "aws":
        return aws_quota.format_quota_report("aws")
    if provider == "gcloud":
        return gcloud_quota.format_quota_report("gcloud")
    raise ValueError(
        f"unknown cloud provider: {provider!r} (expected 'aws' or 'gcloud')"
    )


def can_use_cloud_crop(provider: str = "aws", **kwargs) -> tuple[bool, str]:
    """Dispatch ``can_use_cloud_crop`` by provider name."""
    if provider == "aws":
        return aws_quota.can_use_cloud_crop("aws", **kwargs)
    if provider == "gcloud":
        return gcloud_quota.can_use_cloud_crop("gcloud", **kwargs)
    raise ValueError(f"unknown cloud provider: {provider!r}")


def percent_of_free_tier_used(state: QuotaState) -> float:
    """Dispatch ``percent_of_free_tier_used`` by ``state.provider``."""
    if state.provider == "gcloud":
        return gcloud_quota.percent_of_free_tier_used(state)
    return aws_quota.percent_of_free_tier_used(state)
