"""Cloud-provider-specific policy (quota gates, billing tracking).

Keeps the ``sources/`` package focused on *how* to fetch from each
mirror and isolates *whether we should fetch at all* — the free-tier
tracking, the ``SHARKTOPUS_ACCEPT_CHARGES`` gate, the running
invocation counter — in one place per provider.

* :mod:`sharktopus.cloud.aws_quota` — AWS Lambda free-tier tracker
  for :mod:`sharktopus.sources.aws_crop`.

Future siblings (``gcloud_quota``, ``azure_quota``) land here when
phase 2 cloud-crop sources ship.
"""
