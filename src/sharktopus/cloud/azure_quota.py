"""Azure Container Apps free-tier quota tracker for the ``azure_crop`` source.

Mirrors :mod:`sharktopus.cloud.gcloud_quota` because Container Apps
free tier is structurally identical to Cloud Run's — same three
dimensions (requests, vCPU-seconds, GiB-seconds), same monthly
allowances in Tier-1 regions.

Counter lives in the same ``~/.cache/sharktopus/quota.json`` file as
the AWS + GCloud trackers, keyed by provider name (``azure``).

Policy knobs (env vars, all optional) — same semantics as AWS/GCloud:

* ``SHARKTOPUS_ACCEPT_CHARGES`` — opt in to paid usage past free tier.
* ``SHARKTOPUS_MAX_SPEND_USD`` — monthly ceiling once charges accepted.
* ``SHARKTOPUS_LOCAL_CROP`` — force fully-local crop, skip cloud.

Free-tier numbers follow Microsoft's current Container Apps Consumption
publication (2026). Update if Microsoft changes them.
"""

from __future__ import annotations

from pathlib import Path

from .aws_quota import (
    QuotaState,
    _envflag,
    _load_unlocked,
    _LOCK,
    _max_spend_usd,
    _save_unlocked,
    _default_cache_path,
    load_quota,
)

__all__ = [
    "AZURE_FREE_REQUESTS",
    "AZURE_FREE_VCPU_SECONDS",
    "AZURE_FREE_GB_SECONDS",
    "AZURE_PRICE_PER_REQUEST",
    "AZURE_PRICE_PER_VCPU_SECOND",
    "AZURE_PRICE_PER_GB_SECOND",
    "DEFAULT_VCPU",
    "DEFAULT_MEMORY_GB",
    "can_use_cloud_crop",
    "estimate_invocation_cost",
    "format_quota_report",
    "record_invocation",
]


# Container Apps Consumption-plan free grant (monthly, perpetual).
# https://learn.microsoft.com/azure/container-apps/billing
AZURE_FREE_REQUESTS = 2_000_000
AZURE_FREE_VCPU_SECONDS = 180_000.0
AZURE_FREE_GB_SECONDS = 360_000.0

# Consumption-plan on-demand prices (April 2026 schedule). The request
# price and vCPU-s/GB-s rates are close to Cloud Run's, not identical;
# keep separate constants so future drift is explicit.
AZURE_PRICE_PER_REQUEST = 0.40 / 1_000_000
AZURE_PRICE_PER_VCPU_SECOND = 0.000024
AZURE_PRICE_PER_GB_SECOND = 0.0000026

# Default Container App shape for ``sharktopus-crop``. Matches the
# Cloud Run deploy — 1 vCPU + 2 GiB keeps wgrib2 crop snappy and the
# per-second cost minimal.
DEFAULT_VCPU = 1.0
DEFAULT_MEMORY_GB = 2.0
DEFAULT_DURATION_S = 60.0


def _next_resource_use(state: QuotaState) -> tuple[float, float]:
    d = state.avg_duration_s or DEFAULT_DURATION_S
    return DEFAULT_VCPU * d, DEFAULT_MEMORY_GB * d


def estimate_invocation_cost(state: QuotaState) -> float:
    """Return a $ estimate for *one* more Container Apps invocation."""
    vcpu_s, gb_s = _next_resource_use(state)
    return (
        AZURE_PRICE_PER_REQUEST
        + AZURE_PRICE_PER_VCPU_SECOND * vcpu_s
        + AZURE_PRICE_PER_GB_SECOND * gb_s
    )


def _next_would_fit_free_tier(state: QuotaState) -> bool:
    vcpu_s_next, gb_s_next = _next_resource_use(state)
    return (
        state.invocations + 1 <= AZURE_FREE_REQUESTS
        and state.vcpu_seconds + vcpu_s_next <= AZURE_FREE_VCPU_SECONDS
        and state.gb_seconds + gb_s_next <= AZURE_FREE_GB_SECONDS
    )


def can_use_cloud_crop(
    provider: str = "azure",
    *,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for the next Container Apps invocation."""
    if _envflag("SHARKTOPUS_LOCAL_CROP"):
        return False, "SHARKTOPUS_LOCAL_CROP=true (user forced local crop)"

    state = state or load_quota(provider, path)
    state.roll_if_new_month()

    if _next_would_fit_free_tier(state):
        return True, ""

    if not _envflag("SHARKTOPUS_ACCEPT_CHARGES"):
        return False, (
            f"{provider} free tier exhausted this month "
            f"({state.invocations}/{AZURE_FREE_REQUESTS} req, "
            f"{state.vcpu_seconds:.0f}/{AZURE_FREE_VCPU_SECONDS:.0f} vCPU-s, "
            f"{state.gb_seconds:.0f}/{AZURE_FREE_GB_SECONDS:.0f} GB-s); "
            f"set SHARKTOPUS_ACCEPT_CHARGES=true + SHARKTOPUS_MAX_SPEND_USD=N "
            f"to authorise paid usage"
        )

    projected = state.spend_usd + estimate_invocation_cost(state)
    max_spend = _max_spend_usd()
    if projected > max_spend:
        return False, (
            f"{provider} would exceed SHARKTOPUS_MAX_SPEND_USD "
            f"(projected ${projected:.4f} > cap ${max_spend:.2f})"
        )
    return True, ""


def record_invocation(
    provider: str = "azure",
    *,
    duration_s: float | None = None,
    vcpu: float = DEFAULT_VCPU,
    memory_gb: float = DEFAULT_MEMORY_GB,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> QuotaState:
    """Persist one Container Apps invocation against the local counter."""
    path = path or _default_cache_path()
    with _LOCK:
        if state is None:
            state = _load_unlocked(provider, path)
        state.roll_if_new_month()

        state.memory_mb = int(memory_gb * 1024)
        if duration_s is not None and duration_s > 0:
            n = state.samples
            state.avg_duration_s = (state.avg_duration_s * n + duration_s) / (n + 1)
            state.samples += 1

        d = duration_s if duration_s and duration_s > 0 else state.avg_duration_s
        vcpu_s = vcpu * d
        gb_s = memory_gb * d

        state.invocations += 1
        state.vcpu_seconds += vcpu_s
        state.gb_seconds += gb_s

        if state.invocations > AZURE_FREE_REQUESTS:
            state.spend_usd += AZURE_PRICE_PER_REQUEST
        if state.vcpu_seconds > AZURE_FREE_VCPU_SECONDS:
            paid = min(vcpu_s, state.vcpu_seconds - AZURE_FREE_VCPU_SECONDS)
            state.spend_usd += AZURE_PRICE_PER_VCPU_SECOND * paid
        if state.gb_seconds > AZURE_FREE_GB_SECONDS:
            paid = min(gb_s, state.gb_seconds - AZURE_FREE_GB_SECONDS)
            state.spend_usd += AZURE_PRICE_PER_GB_SECOND * paid

        _save_unlocked(state, path)
        return state


def percent_of_free_tier_used(state: QuotaState) -> float:
    req_pct = 100.0 * state.invocations / AZURE_FREE_REQUESTS
    vcpu_pct = 100.0 * state.vcpu_seconds / AZURE_FREE_VCPU_SECONDS
    gbs_pct = 100.0 * state.gb_seconds / AZURE_FREE_GB_SECONDS
    return max(req_pct, vcpu_pct, gbs_pct)


def format_quota_report(
    provider: str = "azure",
    *,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> str:
    """Return a multi-line human-readable Container Apps quota report."""
    state = state or load_quota(provider, path)
    req_pct = 100.0 * state.invocations / AZURE_FREE_REQUESTS
    vcpu_pct = 100.0 * state.vcpu_seconds / AZURE_FREE_VCPU_SECONDS
    gbs_pct = 100.0 * state.gb_seconds / AZURE_FREE_GB_SECONDS
    allowed, reason = can_use_cloud_crop(provider, state=state, path=path)
    gate = "allowed" if allowed else f"blocked ({reason})"
    lines = [
        f"sharktopus cloud quota — {provider} — month {state.month}",
        "-" * 60,
        f"  invocations   : {state.invocations:>12,d} / {AZURE_FREE_REQUESTS:>12,d}  ({req_pct:5.2f}%)",
        f"  vCPU-seconds  : {state.vcpu_seconds:>12,.1f} / {AZURE_FREE_VCPU_SECONDS:>12,.0f}  ({vcpu_pct:5.2f}%)",
        f"  GB-seconds    : {state.gb_seconds:>12,.1f} / {AZURE_FREE_GB_SECONDS:>12,.0f}  ({gbs_pct:5.2f}%)",
        f"  spend (paid)  : ${state.spend_usd:.4f}",
        f"  avg duration  : {state.avg_duration_s:.2f} s  ({DEFAULT_VCPU} vCPU, {DEFAULT_MEMORY_GB} GiB assumed)",
        f"  est next call : ${estimate_invocation_cost(state):.6f}",
        f"  next call     : {gate}",
    ]
    return "\n".join(lines)
