"""Tests for sharktopus.aws_quota — local free-tier counter + policy gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sharktopus import aws_quota


@pytest.fixture
def tmp_quota(tmp_path, monkeypatch):
    """Isolate the quota cache under tmp_path and clear policy env."""
    cache = tmp_path / "quota.json"
    monkeypatch.setenv("SHARKTOPUS_QUOTA_CACHE", str(cache))
    monkeypatch.delenv("SHARKTOPUS_ACCEPT_CHARGES", raising=False)
    monkeypatch.delenv("SHARKTOPUS_MAX_SPEND_USD", raising=False)
    monkeypatch.delenv("SHARKTOPUS_LOCAL_CROP", raising=False)
    yield cache


def test_fresh_state_is_empty(tmp_quota):
    state = aws_quota.load_quota("aws")
    assert state.invocations == 0
    assert state.gb_seconds == 0.0
    assert state.spend_usd == 0.0


def test_record_invocation_persists(tmp_quota):
    aws_quota.record_invocation("aws", duration_s=60.0, memory_mb=512)
    state = aws_quota.load_quota("aws")
    assert state.invocations == 1
    # 512 MB × 60 s = 30 GB-s
    assert state.gb_seconds == pytest.approx(30.0, rel=1e-3)
    assert state.spend_usd == 0.0  # still inside free tier


def test_multiple_providers_keyed_separately(tmp_quota):
    aws_quota.record_invocation("aws", duration_s=30)
    aws_quota.record_invocation("gcloud", duration_s=45)
    aws_quota.record_invocation("aws", duration_s=30)
    aws = aws_quota.load_quota("aws")
    gc = aws_quota.load_quota("gcloud")
    assert aws.invocations == 2
    assert gc.invocations == 1


def test_can_use_within_free_tier(tmp_quota):
    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok, reason
    assert reason == ""


def test_local_crop_env_forces_false(tmp_quota, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_LOCAL_CROP", "true")
    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok is False
    assert "SHARKTOPUS_LOCAL_CROP" in reason


def test_free_tier_exhausted_blocks_without_accept(tmp_quota):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS  # at the cap
    aws_quota.save_quota(state)

    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok is False
    assert "SHARKTOPUS_ACCEPT_CHARGES" in reason


def test_accept_charges_requires_max_spend(tmp_quota, monkeypatch):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS
    aws_quota.save_quota(state)

    monkeypatch.setenv("SHARKTOPUS_ACCEPT_CHARGES", "true")
    # MAX_SPEND defaults to 0 → any paid invocation exceeds it
    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok is False
    assert "MAX_SPEND_USD" in reason


def test_accept_charges_with_budget_allows(tmp_quota, monkeypatch):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS
    aws_quota.save_quota(state)

    monkeypatch.setenv("SHARKTOPUS_ACCEPT_CHARGES", "true")
    monkeypatch.setenv("SHARKTOPUS_MAX_SPEND_USD", "5.00")
    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok is True


def test_accept_charges_with_exhausted_budget_blocks(tmp_quota, monkeypatch):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS
    state.spend_usd = 4.99  # close to the cap
    aws_quota.save_quota(state)

    monkeypatch.setenv("SHARKTOPUS_ACCEPT_CHARGES", "true")
    monkeypatch.setenv("SHARKTOPUS_MAX_SPEND_USD", "5.00")
    # estimate_invocation_cost is tiny but positive; projected would
    # still fit. Use a harder cap to force the block.
    monkeypatch.setenv("SHARKTOPUS_MAX_SPEND_USD", "4.99")
    ok, reason = aws_quota.can_use_cloud_crop("aws")
    assert ok is False
    assert "exceed" in reason.lower() or "cap" in reason.lower()


def test_month_rollover_resets_counters(tmp_quota):
    state = aws_quota.load_quota("aws")
    state.invocations = 100
    state.gb_seconds = 500.0
    state.spend_usd = 1.50
    state.month = "1999-01"  # clearly in the past
    aws_quota.save_quota(state)

    reloaded = aws_quota.load_quota("aws")
    assert reloaded.invocations == 0
    assert reloaded.gb_seconds == 0.0
    assert reloaded.spend_usd == 0.0


def test_estimate_cost_positive(tmp_quota):
    state = aws_quota.load_quota("aws")
    cost = aws_quota.estimate_invocation_cost(state)
    assert cost > 0
    # sanity: 512 MB × 60 s = 30 GB-s → ~ $0.0005
    assert cost < 0.01


def test_concurrent_record_invocations_serialised(tmp_quota):
    """Two threads hammering record_invocation must not lose writes."""
    import threading

    def work():
        for _ in range(20):
            aws_quota.record_invocation("aws", duration_s=10)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = aws_quota.load_quota("aws")
    assert state.invocations == 80


def test_percent_used(tmp_quota):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS // 2
    assert aws_quota.percent_of_free_tier_used(state) == pytest.approx(50.0)
