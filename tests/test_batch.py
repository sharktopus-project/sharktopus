"""Tests for sharktopus.batch (timestamp generation + orchestrator)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sharktopus import batch
from sharktopus.sources import SourceUnavailable


# ---------------------------------------------------------------------------
# generate_timestamps
# ---------------------------------------------------------------------------

def test_generate_timestamps_basic():
    assert batch.generate_timestamps("2024010200", "2024010218", 6) == [
        "2024010200", "2024010206", "2024010212", "2024010218",
    ]


def test_generate_timestamps_single_cycle():
    assert batch.generate_timestamps("2024010200", "2024010200", 6) == ["2024010200"]


def test_generate_timestamps_3h_step():
    out = batch.generate_timestamps("2024010200", "2024010212", 3)
    assert out == ["2024010200", "2024010203", "2024010206", "2024010209", "2024010212"]


def test_generate_timestamps_rejects_bad_step():
    with pytest.raises(ValueError, match="step"):
        batch.generate_timestamps("2024010200", "2024010218", 0)


def test_generate_timestamps_rejects_reversed_range():
    with pytest.raises(ValueError, match="end"):
        batch.generate_timestamps("2024010218", "2024010200", 6)


def test_generate_timestamps_rejects_bad_format():
    with pytest.raises(ValueError, match="YYYYMMDDHH"):
        batch.generate_timestamps("2024-01-02", "2024010218", 6)


# ---------------------------------------------------------------------------
# fetch_batch — orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_registry(monkeypatch, tmp_path):
    """Swap the source registry for a controllable stub registry."""
    calls: list[tuple[str, str, str, int, dict]] = []

    def make_source(name, *, succeed_for=None, fail_always=False):
        def fetch(date, cycle, fxx, **kwargs):
            calls.append((name, date, cycle, fxx, kwargs))
            if fail_always:
                raise SourceUnavailable(f"{name} intentionally unavailable")
            if succeed_for is not None and (date, cycle, fxx) not in succeed_for:
                raise SourceUnavailable(f"{name} no data for {date}{cycle}.f{fxx}")
            p = tmp_path / f"{name}.{date}{cycle}.f{fxx:03d}.grib2"
            p.write_bytes(b"GRIB")
            return p
        return fetch

    orig = dict(batch._REGISTRY)
    batch._REGISTRY.clear()
    try:
        yield calls, make_source
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig)


def test_fetch_batch_first_source_wins(fake_registry):
    calls, make_source = fake_registry
    batch._REGISTRY["primary"] = make_source("primary")
    batch._REGISTRY["secondary"] = make_source("secondary")

    out = batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=6, interval=3,
        priority=["primary", "secondary"],
    )
    # fxx 0, 3, 6 → 3 files, all from "primary"
    assert len(out) == 3
    assert all("primary" in p.name for p in out)
    # Secondary never called
    assert all(c[0] == "primary" for c in calls)


def test_fetch_batch_falls_back_on_source_unavailable(fake_registry):
    calls, make_source = fake_registry
    batch._REGISTRY["primary"] = make_source("primary", fail_always=True)
    batch._REGISTRY["secondary"] = make_source("secondary")

    out = batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["primary", "secondary"],
    )
    assert len(out) == 1 and "secondary" in out[0].name
    # Both got called for the single step
    assert [c[0] for c in calls] == ["primary", "secondary"]


def test_fetch_batch_reports_failures(fake_registry):
    calls, make_source = fake_registry
    batch._REGISTRY["a"] = make_source("a", fail_always=True)
    batch._REGISTRY["b"] = make_source("b", fail_always=True)

    fails: list = []

    out = batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=0, interval=3,
        priority=["a", "b"],
        on_step_fail=lambda *args: fails.append(args),
    )
    assert out == []
    assert len(fails) == 1
    date, cycle, fxx, errs = fails[0]
    assert (date, cycle, fxx) == ("20240102", "00", 0)
    assert [name for name, _ in errs] == ["a", "b"]


def test_fetch_batch_calls_on_step_ok(fake_registry):
    _, make_source = fake_registry
    batch._REGISTRY["only"] = make_source("only")

    ok: list = []
    batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        ext=3, interval=3,
        priority=["only"],
        on_step_ok=lambda d, c, f, p: ok.append((d, c, f)),
    )
    assert ok == [("20240102", "00", 0), ("20240102", "00", 3)]


def test_fetch_batch_passes_bbox_in_wgrib2_order(fake_registry):
    calls, make_source = fake_registry
    batch._REGISTRY["probe"] = make_source("probe")

    batch.fetch_batch(
        timestamps=["2024010200"],
        lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
        ext=0, interval=3,
        priority=["probe"],
    )
    _, _, _, _, kwargs = calls[0]
    assert kwargs["bbox"] == (-48.0, -36.0, -28.0, -18.0)


def test_fetch_batch_requires_vars_levels_for_nomads_filter(fake_registry):
    _, make_source = fake_registry
    batch._REGISTRY["nomads_filter"] = make_source("nomads_filter")

    with pytest.raises(ValueError, match="variables/levels"):
        batch.fetch_batch(
            timestamps=["2024010200"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=0, interval=3,
            priority=["nomads_filter"],
        )


def test_fetch_batch_rejects_unknown_source(fake_registry):
    _, make_source = fake_registry
    batch._REGISTRY["nomads"] = make_source("nomads")
    with pytest.raises(ValueError, match="unknown source"):
        batch.fetch_batch(
            timestamps=["2024010200"],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
            ext=0, interval=3,
            priority=["nomads", "does_not_exist"],
        )


def test_fetch_batch_rejects_empty_timestamps():
    with pytest.raises(ValueError, match="timestamps"):
        batch.fetch_batch(
            timestamps=[],
            lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        )


def test_register_source_adds_to_registry():
    def dummy(*a, **k):
        return Path("/nowhere")
    batch.register_source("_test_dummy", dummy)
    try:
        assert "_test_dummy" in batch.registered_sources()
    finally:
        batch._REGISTRY.pop("_test_dummy", None)
