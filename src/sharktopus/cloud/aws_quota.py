"""AWS free-tier quota tracker for the ``aws_crop`` source.

Cloud-side cropping via Lambda is the fast path, but it costs money
outside the Always-Free tier. This module keeps a local counter of
how much of the current month's free allocation we've already burned,
decides whether the next invocation still fits, and raises a warning
when we're approaching the wall.

Counter lives at ``~/.cache/sharktopus/quota.json`` (override with
``SHARKTOPUS_QUOTA_CACHE``). Each field resets on the first invocation
of a new UTC month — no background job needed.

Policy knobs (env vars, all optional):

``SHARKTOPUS_ACCEPT_CHARGES``
    ``true`` to allow invocations after the free tier is exhausted.
    Anything else (or unset) blocks those invocations.

``SHARKTOPUS_MAX_SPEND_USD``
    Monthly ceiling the user authorises once ``ACCEPT_CHARGES`` is on.
    Default ``0`` (no paid spend). Cumulative: the counter compares
    ``spend_usd + estimated_next_cost`` against this.

``SHARKTOPUS_LOCAL_CROP``
    ``true`` to skip cloud-crop entirely, regardless of quota.

Nothing here talks to CloudWatch — we estimate locally. A separate
helper ``refresh_from_cloudwatch()`` can reconcile against reality
when needed (slower, requires IAM permission).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "AWS_FREE_INVOCATIONS",
    "AWS_FREE_GB_SECONDS",
    "AWS_PRICE_PER_REQUEST",
    "AWS_PRICE_PER_GB_SECOND",
    "QuotaState",
    "can_use_cloud_crop",
    "estimate_invocation_cost",
    "format_quota_report",
    "load_quota",
    "percent_of_free_tier_used",
    "record_invocation",
    "save_quota",
]


# AWS Lambda Always-Free (monthly, perpetual)
AWS_FREE_INVOCATIONS = 1_000_000
AWS_FREE_GB_SECONDS = 400_000.0

# On-demand prices (us-east-1, April 2026 schedule — update if AWS changes).
AWS_PRICE_PER_REQUEST = 0.20 / 1_000_000       # $0.20 per 1M requests
AWS_PRICE_PER_GB_SECOND = 0.0000166667         # $0.0000166667 per GB-s

# Default Lambda config for the ``sharktopus`` function.
DEFAULT_LAMBDA_MEMORY_MB = 512
DEFAULT_LAMBDA_DURATION_S = 60.0   # seed for cost estimates; updated in-place


def _default_cache_path() -> Path:
    override = os.environ.get("SHARKTOPUS_QUOTA_CACHE")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sharktopus" / "quota.json"


def _current_month_tag(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


@dataclass
class QuotaState:
    """One month's worth of counters for one provider (aws, gcloud, azure).

    ``vcpu_seconds`` is Cloud Run-specific (Lambda bundles CPU into the
    memory class). Default 0.0 keeps the AWS path unchanged.
    """
    provider: str
    month: str
    invocations: int = 0
    gb_seconds: float = 0.0
    vcpu_seconds: float = 0.0
    spend_usd: float = 0.0
    avg_duration_s: float = DEFAULT_LAMBDA_DURATION_S
    memory_mb: int = DEFAULT_LAMBDA_MEMORY_MB
    samples: int = 0

    def roll_if_new_month(self, now: datetime | None = None) -> None:
        tag = _current_month_tag(now)
        if self.month != tag:
            self.month = tag
            self.invocations = 0
            self.gb_seconds = 0.0
            self.vcpu_seconds = 0.0
            self.spend_usd = 0.0
            # Keep avg_duration / memory / samples across months — they're
            # calibration, not usage.


_LOCK = threading.RLock()


def _load_unlocked(provider: str, path: Path) -> QuotaState:
    if not path.is_file():
        return QuotaState(provider=provider, month=_current_month_tag())
    try:
        blob = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return QuotaState(provider=provider, month=_current_month_tag())
    data = blob.get(provider) if isinstance(blob, dict) else None
    if not isinstance(data, dict):
        return QuotaState(provider=provider, month=_current_month_tag())
    state = QuotaState(
        provider=provider,
        month=data.get("month", _current_month_tag()),
        invocations=int(data.get("invocations", 0)),
        gb_seconds=float(data.get("gb_seconds", 0.0)),
        vcpu_seconds=float(data.get("vcpu_seconds", 0.0)),
        spend_usd=float(data.get("spend_usd", 0.0)),
        avg_duration_s=float(data.get("avg_duration_s", DEFAULT_LAMBDA_DURATION_S)),
        memory_mb=int(data.get("memory_mb", DEFAULT_LAMBDA_MEMORY_MB)),
        samples=int(data.get("samples", 0)),
    )
    state.roll_if_new_month()
    return state


def _save_unlocked(state: QuotaState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob: dict = {}
    if path.is_file():
        try:
            blob = json.loads(path.read_text()) or {}
            if not isinstance(blob, dict):
                blob = {}
        except (OSError, json.JSONDecodeError):
            blob = {}
    blob[state.provider] = asdict(state)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, indent=2, sort_keys=True))
    tmp.replace(path)


def load_quota(provider: str = "aws", path: Path | None = None) -> QuotaState:
    """Load the counter for *provider*. Creates a fresh one if missing."""
    path = path or _default_cache_path()
    with _LOCK:
        return _load_unlocked(provider, path)


def save_quota(state: QuotaState, path: Path | None = None) -> None:
    """Persist *state* atomically into *path* (JSON, one key per provider)."""
    path = path or _default_cache_path()
    with _LOCK:
        _save_unlocked(state, path)


def estimate_invocation_cost(state: QuotaState) -> float:
    """Return a $ estimate for *one* more invocation given the rolling average.

    Used to decide whether submitting one more crop would exceed
    ``SHARKTOPUS_MAX_SPEND_USD``. Doesn't count the free tier here —
    caller does that separately.
    """
    gb_s = (state.memory_mb / 1024.0) * state.avg_duration_s
    return AWS_PRICE_PER_REQUEST + AWS_PRICE_PER_GB_SECOND * gb_s


def _next_would_fit_free_tier(state: QuotaState) -> bool:
    gb_s_next = (state.memory_mb / 1024.0) * state.avg_duration_s
    return (
        state.invocations + 1 <= AWS_FREE_INVOCATIONS
        and state.gb_seconds + gb_s_next <= AWS_FREE_GB_SECONDS
    )


def _envflag(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _max_spend_usd() -> float:
    raw = os.environ.get("SHARKTOPUS_MAX_SPEND_USD", "") or "0"
    try:
        return float(raw)
    except ValueError:
        return 0.0


def can_use_cloud_crop(
    provider: str = "aws",
    *,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> tuple[bool, str]:
    """Decide whether the next cloud-crop call is authorised.

    Returns ``(allowed, reason)``. *reason* is an empty string when
    allowed; otherwise a short phrase suitable for a log line or
    ``UserWarning`` payload.

    The caller is expected to fall back to byte-range + local crop
    when this returns ``False``.
    """
    if _envflag("SHARKTOPUS_LOCAL_CROP"):
        return False, "SHARKTOPUS_LOCAL_CROP=true (user forced local crop)"

    state = state or load_quota(provider, path)
    state.roll_if_new_month()

    if _next_would_fit_free_tier(state):
        return True, ""

    if not _envflag("SHARKTOPUS_ACCEPT_CHARGES"):
        return False, (
            f"{provider} free tier exhausted this month "
            f"({state.invocations}/{AWS_FREE_INVOCATIONS} req, "
            f"{state.gb_seconds:.0f}/{AWS_FREE_GB_SECONDS:.0f} GB-s); "
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
    provider: str = "aws",
    *,
    duration_s: float | None = None,
    memory_mb: int | None = None,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> QuotaState:
    """Persist one invocation against the local counter.

    Call this from the source wrapper *after* the Lambda returned —
    both on success and on failure (a timed-out Lambda still costs).
    *duration_s* is the actual billed duration in seconds (the Lambda
    response typically includes this in the context billing info); pass
    ``None`` to fall back to the running average. *memory_mb* likewise
    defaults to the last known config.
    """
    path = path or _default_cache_path()
    with _LOCK:
        if state is None:
            state = _load_unlocked(provider, path)
        state.roll_if_new_month()

        if memory_mb is not None:
            state.memory_mb = int(memory_mb)
        if duration_s is not None and duration_s > 0:
            # Running average, weighted by sample count (stabilises after ~20 runs).
            n = state.samples
            state.avg_duration_s = (state.avg_duration_s * n + duration_s) / (n + 1)
            state.samples += 1

        gb_s = (state.memory_mb / 1024.0) * (duration_s or state.avg_duration_s)
        state.invocations += 1
        state.gb_seconds += gb_s

        # Charge what actually billed: everything above free tier.
        if state.invocations > AWS_FREE_INVOCATIONS:
            state.spend_usd += AWS_PRICE_PER_REQUEST
        if state.gb_seconds > AWS_FREE_GB_SECONDS:
            paid_gb_s = min(gb_s, state.gb_seconds - AWS_FREE_GB_SECONDS)
            state.spend_usd += AWS_PRICE_PER_GB_SECOND * paid_gb_s

        _save_unlocked(state, path)
        return state


def percent_of_free_tier_used(state: QuotaState) -> float:
    """Return the higher of invocation% and GB-s% (0-100 range)."""
    inv_pct = 100.0 * state.invocations / AWS_FREE_INVOCATIONS
    gbs_pct = 100.0 * state.gb_seconds / AWS_FREE_GB_SECONDS
    return max(inv_pct, gbs_pct)


def format_quota_report(
    provider: str = "aws",
    *,
    state: QuotaState | None = None,
    path: Path | None = None,
) -> str:
    """Return a multi-line human-readable quota report for *provider*.

    Used by ``sharktopus --quota`` and safe to call from notebooks /
    scripts. Reads the local counter only — no CloudWatch round-trip.
    """
    state = state or load_quota(provider, path)
    inv_pct = 100.0 * state.invocations / AWS_FREE_INVOCATIONS
    gbs_pct = 100.0 * state.gb_seconds / AWS_FREE_GB_SECONDS
    allowed, reason = can_use_cloud_crop(provider, state=state, path=path)
    gate = "allowed" if allowed else f"blocked ({reason})"
    lines = [
        f"sharktopus cloud quota — {provider} — month {state.month}",
        "-" * 60,
        f"  invocations   : {state.invocations:>12,d} / {AWS_FREE_INVOCATIONS:>12,d}  ({inv_pct:5.2f}%)",
        f"  GB-seconds    : {state.gb_seconds:>12,.1f} / {AWS_FREE_GB_SECONDS:>12,.0f}  ({gbs_pct:5.2f}%)",
        f"  spend (paid)  : ${state.spend_usd:.4f}",
        f"  avg duration  : {state.avg_duration_s:.2f} s  (memory {state.memory_mb} MB, {state.samples} samples)",
        f"  est next call : ${estimate_invocation_cost(state):.6f}",
        f"  next call     : {gate}",
    ]
    return "\n".join(lines)
