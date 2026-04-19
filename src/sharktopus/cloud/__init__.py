"""Cloud-provider-specific policy (quota gates, billing tracking).

Keeps the ``sources/`` package focused on *how* to fetch from each
mirror and isolates *whether we should fetch at all* — the free-tier
tracking, the ``SHARKTOPUS_ACCEPT_CHARGES`` gate, the running
invocation counter — in one place per provider.

* :mod:`sharktopus.cloud.aws_quota` — AWS Lambda free-tier tracker
  for :mod:`sharktopus.sources.aws_crop`.

Future siblings (``gcloud_quota``, ``azure_quota``) land here when
phase 2 cloud-crop sources ship.

Convenience re-exports (``load_quota``, ``quota_report``,
``can_use_cloud_crop``) let callers write
``from sharktopus.cloud import quota_report`` without reaching into
provider-specific submodules.
"""

from . import aws_quota
from .aws_quota import (
    QuotaState,
    can_use_cloud_crop,
    format_quota_report as quota_report,
    load_quota,
    percent_of_free_tier_used,
)

__all__ = [
    "QuotaState",
    "aws_quota",
    "can_use_cloud_crop",
    "load_quota",
    "percent_of_free_tier_used",
    "quota_report",
]
