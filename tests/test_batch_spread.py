"""Tests for sharktopus.batch spread mode (MultiSourceQueue-backed)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sharktopus import batch
from sharktopus.sources import SourceUnavailable


@pytest.fixture
def isolated_registry():
    """Clear the source registry for the test, restore on exit."""
    orig_reg = dict(batch._REGISTRY)
    orig_workers = dict(batch._WORKER_DEFAULTS)
    orig_supports = dict(batch._SUPPORTS)
    batch._REGISTRY.clear()
    try:
        yield
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig_reg)
        batch._WORKER_DEFAULTS.clear()
        batch._WORKER_DEFAULTS.update(orig_workers)
        batch._SUPPORTS.clear()
        batch._SUPPORTS.update(orig_supports)


def _tracking_source(name, tmp_path, *, fail_always=False, slow=0.0, fail_on=None):
    """Build a fetch callable that records (source, key) per call."""
    calls: list[tuple[str, str, str, int]] = []
    active: dict[str, int] = {name: 0}
    peak: dict[str, int] = {name: 0}
    lock = threading.Lock()

    def fetch(date, cycle, fxx, **kwargs):
        with lock:
            calls.append((name, date, cycle, fxx))
            active[name] += 1
            if active[name] > peak[name]:
                peak[name] = active[name]
        try:
            if slow:
                time.sleep(slow)
            if fail_always:
                raise SourceUnavailable(f"{name} always fails")
            if fail_on is not None and (date, cycle, fxx) in fail_on:
                raise SourceUnavailable(f"{name} rejects {date}{cycle}.f{fxx}")
            p = tmp_path / f"{name}.{date}{cycle}.f{fxx:03d}.grib2"
            p.write_bytes(b"GRIB")
            return p
        finally:
            with lock:
                active[name] -= 1

    fetch.calls = calls
    fetch.peak = peak
    return fetch


# ---------------------------------------------------------------------------
# Explicit spread=True
# ---------------------------------------------------------------------------

def test_spread_distributes_across_sources(isolated_registry, tmp_path):
    """spread=True spreads a multi-timestamp batch across sources."""
    a = _tracking_source("a", tmp_path)
    b = _tracking_source("b", tmp_path)
    c = _tracking_source("c", tmp_path)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)
    batch.register_source("c", c, max_workers=1)

    # 9 timestamps × 1 step = 9 jobs, ideally ~3 per source.
    stamps = [f"202401{day:02d}00" for day in range(1, 10)]
    out = batch.fetch_batch(
        timestamps=stamps,
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b", "c"],
        spread=True,
    )

    assert len(out) == 9
    # Each source should pick up at least one job (spread, not fallback chain).
    assert len(a.calls) >= 1
    assert len(b.calls) >= 1
    assert len(c.calls) >= 1
    # And together cover everything exactly once.
    all_keys = {c[1:] for c in a.calls + b.calls + c.calls}
    assert len(all_keys) == 9


def test_spread_auto_default_when_priority_autoresolved(
    isolated_registry, tmp_path, monkeypatch
):
    """With priority=None (auto), multi-source availability triggers spread."""
    a = _tracking_source("a", tmp_path)
    b = _tracking_source("b", tmp_path)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)

    # Force both into DEFAULT_PRIORITY so auto-resolve picks both.
    monkeypatch.setattr(batch, "DEFAULT_PRIORITY", ("a", "b"))

    stamps = [f"202401{day:02d}00" for day in range(1, 7)]
    batch.fetch_batch(
        timestamps=stamps,
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
    )

    # Auto mode → spread → both sources get work.
    assert len(a.calls) >= 1 and len(b.calls) >= 1


def test_explicit_priority_defaults_to_fallback_chain(isolated_registry, tmp_path):
    """Explicit priority=[...] preserves first-wins semantics by default."""
    a = _tracking_source("a", tmp_path)
    b = _tracking_source("b", tmp_path)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)

    batch.fetch_batch(
        timestamps=["2024010200", "2024010206"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
    )

    # Classic fallback: primary handles everything, secondary never called.
    assert len(a.calls) == 2
    assert b.calls == []


# ---------------------------------------------------------------------------
# Fallback via re-enqueue
# ---------------------------------------------------------------------------

def test_spread_reenqueues_on_failure(isolated_registry, tmp_path):
    """A step that fails on A should be picked up by B via re-enqueue."""
    a = _tracking_source("a", tmp_path, fail_always=True)
    b = _tracking_source("b", tmp_path)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)

    out = batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
        spread=True,
    )

    assert len(out) == 1
    assert "b." in out[0].name
    # A got its one try; B took the re-enqueued step.
    assert len(a.calls) == 1
    assert len(b.calls) == 1


def test_spread_reports_final_failures(isolated_registry, tmp_path):
    """All-sources-fail is reported through on_step_fail with per-source errors."""
    a = _tracking_source("a", tmp_path, fail_always=True)
    b = _tracking_source("b", tmp_path, fail_always=True)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)

    fails: list = []
    out = batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
        spread=True,
        on_step_fail=lambda *args: fails.append(args),
    )

    assert out == []
    assert len(fails) == 1
    date, cycle, fxx, errs = fails[0]
    assert (date, cycle, fxx) == ("20240102", "00", 0)
    # Every source in the priority list reported at least once.
    assert {name for name, _ in errs} == {"a", "b"}


def test_spread_re_enqueue_does_not_double_count_ok(isolated_registry, tmp_path):
    """When B succeeds after A failed, on_step_fail is NOT called for that key."""
    a = _tracking_source("a", tmp_path, fail_always=True)
    b = _tracking_source("b", tmp_path)
    batch.register_source("a", a, max_workers=1)
    batch.register_source("b", b, max_workers=1)

    fails: list = []
    oks: list = []
    batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
        spread=True,
        on_step_ok=lambda *args: oks.append(args),
        on_step_fail=lambda *args: fails.append(args),
    )

    assert len(oks) == 1
    assert fails == []


# ---------------------------------------------------------------------------
# Rate-limit preservation
# ---------------------------------------------------------------------------

def test_spread_respects_per_source_worker_ceiling(isolated_registry, tmp_path):
    """No source ever runs more threads than its DEFAULT_MAX_WORKERS."""
    a = _tracking_source("a", tmp_path, slow=0.02)
    b = _tracking_source("b", tmp_path, slow=0.02)
    batch.register_source("a", a, max_workers=2)
    batch.register_source("b", b, max_workers=3)

    stamps = [f"202401{day:02d}00" for day in range(1, 13)]
    batch.fetch_batch(
        timestamps=stamps,
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
        spread=True,
    )

    assert a.peak["a"] <= 2, f"a exceeded its 2-worker ceiling: {a.peak}"
    assert b.peak["b"] <= 3, f"b exceeded its 3-worker ceiling: {b.peak}"


# ---------------------------------------------------------------------------
# Attempt timeout
# ---------------------------------------------------------------------------

def test_spread_attempt_timeout_propagated_as_deadline(isolated_registry, tmp_path):
    """attempt_timeout is forwarded as a per-call deadline kwarg."""
    seen_deadlines: list[float | None] = []

    def fetch(date, cycle, fxx, **kwargs):
        seen_deadlines.append(kwargs.get("deadline"))
        p = tmp_path / f"x.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    other = _tracking_source("other", tmp_path)
    batch.register_source("src", fetch, max_workers=1)
    batch.register_source("other", other, max_workers=1)

    batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["src", "other"],
        spread=True,
        attempt_timeout=30.0,
    )

    assert len(seen_deadlines) == 1
    dl = seen_deadlines[0]
    assert dl is not None and dl > time.monotonic() - 1.0


def test_spread_attempt_timeout_none_means_no_deadline(isolated_registry, tmp_path):
    """attempt_timeout=None forwards deadline=None (no deadline)."""
    seen_deadlines: list[float | None] = []

    def fetch(date, cycle, fxx, **kwargs):
        seen_deadlines.append(kwargs.get("deadline"))
        p = tmp_path / f"x.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    other = _tracking_source("other", tmp_path)
    batch.register_source("src", fetch, max_workers=1)
    batch.register_source("other", other, max_workers=1)

    batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["src", "other"],
        spread=True,
    )

    assert seen_deadlines == [None]


# ---------------------------------------------------------------------------
# Global ordering (oldest date first)
# ---------------------------------------------------------------------------

def test_spread_global_ordering_oldest_first(isolated_registry, tmp_path):
    """With a single slow source, jobs come out in ascending (date, cycle, fxx) order."""
    order: list[tuple[str, str, int]] = []
    lock = threading.Lock()

    def fetch(date, cycle, fxx, **kwargs):
        with lock:
            order.append((date, cycle, fxx))
        p = tmp_path / f"x.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    batch.register_source("src", fetch, max_workers=1)
    batch.register_source("backup", fetch, max_workers=1)

    stamps = ["2024010312", "2024010106", "2024010200"]  # unordered input
    batch.fetch_batch(
        timestamps=stamps,
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=3, interval=3,
        priority=["src", "backup"],
        spread=True,
    )

    # The earliest timestamp's fxx=0 must be picked before the latest timestamp's.
    first_20240101 = next(i for i, k in enumerate(order) if k[0] == "20240101")
    first_20240103 = next(i for i, k in enumerate(order) if k[0] == "20240103")
    assert first_20240101 < first_20240103
