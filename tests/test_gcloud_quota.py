"""Tests for sharktopus.cloud.gcloud_quota — Cloud Run free-tier tracker."""

from __future__ import annotations

import pytest

from sharktopus.cloud import gcloud_quota


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
    state = gcloud_quota.load_quota("gcloud")
    assert state.invocations == 0
    assert state.vcpu_seconds == 0.0
    assert state.gb_seconds == 0.0
    assert state.spend_usd == 0.0


def test_record_invocation_tracks_all_three_dimensions(tmp_quota):
    gcloud_quota.record_invocation("gcloud", duration_s=30.0)
    state = gcloud_quota.load_quota("gcloud")
    assert state.invocations == 1
    # 1 vCPU × 30 s → 30 vCPU-s
    assert state.vcpu_seconds == pytest.approx(30.0, rel=1e-3)
    # 2 GiB × 30 s → 60 GB-s
    assert state.gb_seconds == pytest.approx(60.0, rel=1e-3)
    assert state.spend_usd == 0.0  # inside free tier


def test_separate_provider_bucket_from_aws(tmp_quota):
    gcloud_quota.record_invocation("gcloud", duration_s=10.0)
    # Did not pollute AWS counter.
    from sharktopus.cloud import aws_quota
    aws_state = aws_quota.load_quota("aws")
    assert aws_state.invocations == 0


def test_can_use_within_free_tier(tmp_quota):
    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok, reason
    assert reason == ""


def test_local_crop_env_forces_false(tmp_quota, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_LOCAL_CROP", "true")
    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok is False
    assert "LOCAL_CROP" in reason


def test_free_tier_exhausted_blocks_without_accept(tmp_quota):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = gcloud_quota.GCLOUD_FREE_REQUESTS
    gcloud_quota.save_quota(state)

    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok is False
    assert "ACCEPT_CHARGES" in reason


def test_vcpu_cap_hit_blocks_too(tmp_quota):
    state = gcloud_quota.load_quota("gcloud")
    state.vcpu_seconds = gcloud_quota.GCLOUD_FREE_VCPU_SECONDS
    gcloud_quota.save_quota(state)

    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok is False
    assert "vCPU-s" in reason or "free tier" in reason


def test_accept_charges_requires_budget(tmp_quota, monkeypatch):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = gcloud_quota.GCLOUD_FREE_REQUESTS
    gcloud_quota.save_quota(state)

    monkeypatch.setenv("SHARKTOPUS_ACCEPT_CHARGES", "true")
    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok is False
    assert "MAX_SPEND_USD" in reason


def test_accept_charges_with_budget_allows(tmp_quota, monkeypatch):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = gcloud_quota.GCLOUD_FREE_REQUESTS
    gcloud_quota.save_quota(state)

    monkeypatch.setenv("SHARKTOPUS_ACCEPT_CHARGES", "true")
    monkeypatch.setenv("SHARKTOPUS_MAX_SPEND_USD", "5.00")
    ok, reason = gcloud_quota.can_use_cloud_crop("gcloud")
    assert ok is True, reason


def test_month_rollover_resets_counters(tmp_quota):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = 100
    state.vcpu_seconds = 200.0
    state.gb_seconds = 500.0
    state.spend_usd = 1.50
    state.month = "1999-01"
    gcloud_quota.save_quota(state)

    reloaded = gcloud_quota.load_quota("gcloud")
    assert reloaded.invocations == 0
    assert reloaded.vcpu_seconds == 0.0
    assert reloaded.gb_seconds == 0.0
    assert reloaded.spend_usd == 0.0


def test_estimate_cost_positive(tmp_quota):
    state = gcloud_quota.load_quota("gcloud")
    cost = gcloud_quota.estimate_invocation_cost(state)
    assert cost > 0
    # 1 vCPU × 60 s = 60 vCPU-s × $0.000018 ≈ $0.00108
    # 2 GiB × 60 s = 120 GiB-s × $0.000002 ≈ $0.00024
    # plus $0.0000004 per request → under $0.002
    assert cost < 0.002


def test_percent_used(tmp_quota):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = gcloud_quota.GCLOUD_FREE_REQUESTS // 2
    assert gcloud_quota.percent_of_free_tier_used(state) == pytest.approx(50.0)


def test_format_quota_report_smokes(tmp_quota):
    gcloud_quota.record_invocation("gcloud", duration_s=12.5)
    report = gcloud_quota.format_quota_report("gcloud")
    assert "gcloud" in report
    assert "invocations" in report
    assert "vCPU-seconds" in report
    assert "GB-seconds" in report
    assert "next call" in report


def test_top_level_quota_report_dispatches(tmp_quota):
    """``sharktopus.quota_report('gcloud')`` hits the Cloud Run tracker."""
    import sharktopus
    report = sharktopus.quota_report("gcloud")
    assert "gcloud" in report
    assert "vCPU-seconds" in report


def test_top_level_quota_report_unknown_provider(tmp_quota):
    import sharktopus
    with pytest.raises(ValueError, match="unknown"):
        sharktopus.quota_report("linode")
