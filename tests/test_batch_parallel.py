"""Tests for per-source worker defaults and step-level parallelism."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sharktopus import batch


def test_builtin_worker_defaults_registered():
    # Built-in cloud mirrors get the anti-throttle defaults.
    assert batch.source_default_workers("aws") >= 2
    assert batch.source_default_workers("gcloud") >= 2
    assert batch.source_default_workers("azure") >= 2
    # NOMADS (origin) stays at 2.
    assert batch.source_default_workers("nomads") == 2
    assert batch.source_default_workers("nomads_filter") == 2
    # RDA (academic mirror) intentionally serial.
    assert batch.source_default_workers("rda") == 1


def test_default_max_workers_picks_min_across_priority():
    """Priority list should be paced by its most-throttled source."""
    # aws alone: 4; [aws, rda] → 1 (RDA is the bottleneck)
    assert batch.default_max_workers(["aws"]) == batch.source_default_workers("aws")
    assert batch.default_max_workers(["aws", "rda"]) == 1
    assert batch.default_max_workers(["nomads", "aws"]) == 2


def test_unknown_source_defaults_to_serial():
    """register_source without max_workers stays serial — safe default."""
    assert batch.source_default_workers("does_not_exist") == 1


def test_register_source_max_workers_is_honored():
    def dummy(*a, **k):
        return Path("/nowhere")

    batch.register_source("_test_high_workers", dummy, max_workers=8)
    try:
        assert batch.source_default_workers("_test_high_workers") == 8
    finally:
        batch._REGISTRY.pop("_test_high_workers", None)
        batch._WORKER_DEFAULTS.pop("_test_high_workers", None)


def test_fetch_batch_runs_parallel_when_max_workers_gt_1(tmp_path, monkeypatch):
    """With max_workers>1, steps overlap in time."""
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_source(date, cycle, fxx, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        p = tmp_path / f"stub.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    orig = dict(batch._REGISTRY)
    batch._REGISTRY.clear()
    batch.register_source("slow", slow_source, max_workers=4)
    try:
        outputs = batch.fetch_batch(
            timestamps=["2024010200"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=9, interval=3,  # 4 steps
            priority=["slow"],
        )
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig)
        batch._WORKER_DEFAULTS.pop("slow", None)

    assert len(outputs) == 4
    # With 4 workers + 4 steps, peak concurrency must be > 1.
    assert peak > 1, f"expected concurrent execution, peak={peak}"


def test_fetch_batch_serial_when_max_workers_1(tmp_path, monkeypatch):
    """max_workers=1 → strict sequential execution, no thread pool."""
    order: list[int] = []

    def src(date, cycle, fxx, **kwargs):
        order.append(fxx)
        p = tmp_path / f"stub.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    orig = dict(batch._REGISTRY)
    batch._REGISTRY.clear()
    batch.register_source("serial_src", src, max_workers=1)
    try:
        batch.fetch_batch(
            timestamps=["2024010200"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=9, interval=3,
            priority=["serial_src"],
            max_workers=1,
        )
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig)
        batch._WORKER_DEFAULTS.pop("serial_src", None)

    assert order == [0, 3, 6, 9]


def test_fetch_batch_explicit_max_workers_overrides_default(tmp_path, monkeypatch):
    """Explicit max_workers wins even over a source's low ceiling."""
    peak = 0
    active = 0
    lock = threading.Lock()

    def src(date, cycle, fxx, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        p = tmp_path / f"{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    orig = dict(batch._REGISTRY)
    batch._REGISTRY.clear()
    batch.register_source("conservative", src, max_workers=1)
    try:
        batch.fetch_batch(
            timestamps=["2024010200"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=9, interval=3,
            priority=["conservative"],
            max_workers=4,  # override — user vouches for the mirror
        )
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig)
        batch._WORKER_DEFAULTS.pop("conservative", None)

    assert peak > 1
