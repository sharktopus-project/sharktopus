"""Tests for availability API (per-source supports + batch.available_sources + auto-priority)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sharktopus import batch
from sharktopus.sources import aws, azure, gcloud, nomads, nomads_filter, rda


# ---------------------------------------------------------------------------
# Per-source supports()
# ---------------------------------------------------------------------------

# Frozen "now" used throughout so tests are deterministic even if time marches on.
NOW = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "source,date,expected",
    [
        # NOMADS: rolling 10-day window
        (nomads, "20260417", True),   # 1 day back
        (nomads, "20260410", True),   # ~8 days back
        (nomads, "20260401", False),  # ~17 days back
        (nomads, "20230101", False),  # far outside window
        # nomads_filter: same window as nomads
        (nomads_filter, "20260417", True),
        (nomads_filter, "20260401", False),
        # AWS: since 2021-02-27, no upper age
        (aws, "20260417", True),
        (aws, "20210301", True),
        (aws, "20210101", False),  # before EARLIEST
        (aws, "20150115", False),
        # GCloud: since 2021-01-01
        (gcloud, "20210101", True),
        (gcloud, "20201231", False),
        # Azure: since 2021-01-01
        (azure, "20210101", True),
        (azure, "20201231", False),
        # RDA: since 2015-01-15
        (rda, "20150115", True),
        (rda, "20150114", False),
        (rda, "20100101", False),
    ],
)
def test_per_source_supports(source, date, expected):
    assert source.supports(date, now=NOW) is expected


# ---------------------------------------------------------------------------
# available_sources(date) filters DEFAULT_PRIORITY
# ---------------------------------------------------------------------------

def test_available_sources_recent_date_returns_all_cloud_mirrors():
    """Recent date (within NOMADS window) → full default priority."""
    avail = batch.available_sources("20260417", now=NOW)
    # Order matters (preserves DEFAULT_PRIORITY order)
    assert avail == ["gcloud", "aws", "azure", "rda", "nomads"]


def test_available_sources_drops_nomads_outside_retention():
    """Date beyond NOMADS retention drops nomads; cloud mirrors stay."""
    avail = batch.available_sources("20260101", now=NOW)  # ~108 days back
    assert "nomads" not in avail
    assert avail == ["gcloud", "aws", "azure", "rda"]


def test_available_sources_pre_2021_drops_cloud_mirrors():
    """Pre-2021 date → only rda (cloud mirrors start around 2021)."""
    avail = batch.available_sources("20180615", now=NOW)
    assert avail == ["rda"]


def test_available_sources_pre_2015_returns_empty():
    """Pre-2015 date → no source has it."""
    assert batch.available_sources("20140101", now=NOW) == []


def test_available_sources_nomads_filter_not_in_default_priority():
    """nomads_filter is opt-in (requires variables/levels); default excludes it."""
    avail = batch.available_sources("20260417", now=NOW)
    assert "nomads_filter" not in avail


def test_default_priority_lists_aws_crop_first():
    """Cloud-side crop is the preferred path when reachable."""
    assert batch.DEFAULT_PRIORITY[0] == "aws_crop"
    # Plain mirrors still follow — they're the fallback when aws_crop is
    # blocked by missing credentials or quota policy.
    assert set(batch.DEFAULT_PRIORITY[1:]) >= {"gcloud", "aws", "azure", "rda", "nomads"}


def test_available_sources_includes_aws_crop_when_credentials_present(monkeypatch):
    """With credentials reachable, aws_crop stays in auto-priority."""
    monkeypatch.setattr(
        "sharktopus.sources.aws_crop.have_credentials", lambda: True,
    )
    avail = batch.available_sources("20260417", now=NOW)
    assert avail[0] == "aws_crop"


def test_available_sources_drops_aws_crop_without_credentials(monkeypatch):
    """Default CI state: no AWS creds → aws_crop falls out before invocation."""
    monkeypatch.setattr(
        "sharktopus.sources.aws_crop.have_credentials", lambda: False,
    )
    avail = batch.available_sources("20260417", now=NOW)
    assert "aws_crop" not in avail


def test_available_sources_respects_explicit_candidates():
    """Passing candidates= overrides DEFAULT_PRIORITY but still filters by supports()."""
    avail = batch.available_sources(
        "20260417",
        candidates=["nomads_filter", "nomads", "aws"],
        now=NOW,
    )
    assert avail == ["nomads_filter", "nomads", "aws"]

    # Same candidates, but date outside NOMADS window → only aws survives
    avail = batch.available_sources(
        "20230101",
        candidates=["nomads_filter", "nomads", "aws"],
        now=NOW,
    )
    assert avail == ["aws"]


# ---------------------------------------------------------------------------
# fetch_batch(priority=None) auto-derivation
# ---------------------------------------------------------------------------

def test_fetch_batch_auto_priority_uses_available_sources(monkeypatch, tmp_path):
    """priority=None → derive from available_sources(first timestamp)."""
    calls: list[str] = []

    def fake_gcloud(date, cycle, fxx, **kw):
        calls.append(f"gcloud:{date}{cycle}.f{fxx:03d}")
        return tmp_path / f"gcloud-{date}{cycle}-{fxx:03d}.grib2"

    def fake_rda(*a, **k):
        calls.append("rda-called")
        raise RuntimeError("rda should not be called when gcloud succeeds")

    monkeypatch.setitem(batch._REGISTRY, "gcloud", fake_gcloud)
    monkeypatch.setitem(batch._REGISTRY, "rda", fake_rda)

    batch.fetch_batch(
        timestamps=["2026041700"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        now=NOW,  # recent date → gcloud first
    )

    # gcloud was tried first in the auto-derived priority
    assert calls == ["gcloud:2026041700.f000"]


def test_fetch_batch_auto_priority_skips_nomads_for_old_dates(monkeypatch, tmp_path):
    """priority=None with an old date → auto-priority skips nomads entirely."""
    nomads_calls: list[str] = []

    def fake_nomads(*a, **k):
        nomads_calls.append("nomads-called")
        return tmp_path / "nomads.grib2"

    def fake_gcloud(date, cycle, fxx, **kw):
        return tmp_path / f"{date}{cycle}.grib2"

    monkeypatch.setitem(batch._REGISTRY, "nomads", fake_nomads)
    monkeypatch.setitem(batch._REGISTRY, "gcloud", fake_gcloud)

    batch.fetch_batch(
        timestamps=["2023060100"],  # ~10 months back, outside NOMADS window
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        now=NOW,
    )

    assert nomads_calls == [], "nomads should not be called for dates outside its rolling window"


def test_fetch_batch_auto_priority_raises_when_no_source_available(tmp_path):
    """priority=None with a pre-2015 date → SourceUnavailable (no mirror has it)."""
    from sharktopus.sources import SourceUnavailable

    with pytest.raises(SourceUnavailable, match="no registered source"):
        batch.fetch_batch(
            timestamps=["2010010100"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=0, interval=3,
            now=NOW,
        )


# ---------------------------------------------------------------------------
# register_source(supports=...) integration
# ---------------------------------------------------------------------------

def test_register_source_without_supports_defaults_to_always_true(monkeypatch, tmp_path):
    """Custom sources without supports= always claim availability."""

    def fake(date, cycle, fxx, **kw):
        return tmp_path / "x.grib2"

    # Isolate via monkeypatch so real registry stays clean.
    monkeypatch.setitem(batch._REGISTRY, "my_custom", fake)
    monkeypatch.setitem(batch._WORKER_DEFAULTS, "my_custom", 1)
    monkeypatch.setitem(batch._SUPPORTS, "my_custom", batch._always_true)

    assert batch.source_supports("my_custom", "19900101") is True


def test_register_source_with_supports_filters_availability(monkeypatch, tmp_path):
    """Custom supports= is honored by available_sources when it's in candidates."""

    def fake(date, cycle, fxx, **kw):
        return tmp_path / "x.grib2"

    def custom_supports(date, cycle=None, *, now=None):
        # Pretend this source only has July 2024.
        return date.startswith("202407")

    monkeypatch.setitem(batch._REGISTRY, "custom_mirror", fake)
    monkeypatch.setitem(batch._WORKER_DEFAULTS, "custom_mirror", 1)
    monkeypatch.setitem(batch._SUPPORTS, "custom_mirror", custom_supports)

    assert batch.source_supports("custom_mirror", "20240715") is True
    assert batch.source_supports("custom_mirror", "20240815") is False

    avail = batch.available_sources(
        "20240715",
        candidates=["custom_mirror", "aws"],
        now=NOW,
    )
    assert avail == ["custom_mirror", "aws"]
