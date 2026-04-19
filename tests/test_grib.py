"""Tests for sharktopus.io.grib (layer 0)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sharktopus.io.grib import (
    DEFAULT_WRF_PAD_LAT,
    DEFAULT_WRF_PAD_LON,
    GribError,
    IdxRecord,
    byte_ranges,
    crop,
    expand_bbox,
    filter_vars_levels,
    have_wgrib2,
    parse_idx,
    rename_by_validity,
    suggest_omp_threads,
    verify,
)


# ---------------------------------------------------------------------------
# Pure-Python helpers: parse_idx, byte_ranges  (no wgrib2 needed)
# ---------------------------------------------------------------------------

# Five records mimicking a real GFS .idx.  Offsets are synthetic but realistic.
_SAMPLE_IDX = (
    "1:0:d=2024012100:HGT:500 mb:6 hour fcst:\n"
    "2:1000:d=2024012100:TMP:500 mb:6 hour fcst:\n"
    "3:2500:d=2024012100:UGRD:500 mb:6 hour fcst:\n"
    "4:3700:d=2024012100:VGRD:500 mb:6 hour fcst:\n"
    "5:5000:d=2024012100:TMP:850 mb:6 hour fcst:\n"
)
_SAMPLE_TOTAL = 6000


def test_parse_idx_basic():
    records = parse_idx(_SAMPLE_IDX)
    assert len(records) == 5
    assert records[0] == IdxRecord(
        record=1, offset=0, date="d=2024012100",
        variable="HGT", level="500 mb", forecast="6 hour fcst:",
    )
    assert records[-1].variable == "TMP"
    assert records[-1].level == "850 mb"
    assert records[1].key == "TMP:500 mb"


def test_parse_idx_skips_garbage_lines():
    text = _SAMPLE_IDX + "this is not a valid line\n" + "99:not_an_int:a:b:c:d\n"
    records = parse_idx(text)
    assert len(records) == 5


def test_parse_idx_empty():
    assert parse_idx("") == []
    assert parse_idx("\n\n   \n") == []


def test_byte_ranges_single_record_mid_file():
    records = parse_idx(_SAMPLE_IDX)
    ranges = byte_ranges(records, wanted=["TMP:500 mb"], total_size=_SAMPLE_TOTAL)
    # record 2: offset 1000, next offset 2500 → end = 2499
    assert ranges == [(1000, 2499)]


def test_byte_ranges_last_record_uses_total_size():
    records = parse_idx(_SAMPLE_IDX)
    ranges = byte_ranges(records, wanted=["TMP:850 mb"], total_size=_SAMPLE_TOTAL)
    # last record offset 5000, no next → end = total_size - 1
    assert ranges == [(5000, _SAMPLE_TOTAL - 1)]


def test_byte_ranges_merges_adjacent():
    records = parse_idx(_SAMPLE_IDX)
    # records 2, 3, 4 are contiguous (1000..2499, 2500..3699, 3700..4999)
    ranges = byte_ranges(
        records,
        wanted=["TMP:500 mb", "UGRD:500 mb", "VGRD:500 mb"],
        total_size=_SAMPLE_TOTAL,
    )
    assert ranges == [(1000, 4999)]


def test_byte_ranges_non_adjacent_stays_split():
    records = parse_idx(_SAMPLE_IDX)
    # records 1 (0..999) and 5 (5000..5999) are not adjacent
    ranges = byte_ranges(
        records,
        wanted=["HGT:500 mb", "TMP:850 mb"],
        total_size=_SAMPLE_TOTAL,
    )
    assert ranges == [(0, 999), (5000, 5999)]


def test_byte_ranges_accepts_record_objects_too():
    records = parse_idx(_SAMPLE_IDX)
    ranges = byte_ranges(records, wanted=[records[1]], total_size=_SAMPLE_TOTAL)
    assert ranges == [(1000, 2499)]


def test_byte_ranges_empty_inputs():
    assert byte_ranges([], wanted=["TMP:500 mb"], total_size=100) == []
    records = parse_idx(_SAMPLE_IDX)
    assert byte_ranges(records, wanted=[], total_size=_SAMPLE_TOTAL) == []


# ---------------------------------------------------------------------------
# bbox validation (pure)
# ---------------------------------------------------------------------------

def test_crop_rejects_inverted_bbox(tmp_path):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    with pytest.raises(ValueError):
        crop(src, dst, bbox=(-40, -45, -25, -20))  # lon_e < lon_w
    with pytest.raises(ValueError):
        crop(src, dst, bbox=(-45, -40, -20, -25))  # lat_n < lat_s


# ---------------------------------------------------------------------------
# wgrib2-backed paths — graceful when wgrib2 is missing
# ---------------------------------------------------------------------------

def test_verify_raises_when_wgrib2_missing(tmp_path):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"not a grib file")
    with pytest.raises(GribError):
        verify(src, wgrib2="/nonexistent/wgrib2_xyz_123")


def test_rename_raises_when_wgrib2_missing(tmp_path):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    with pytest.raises(GribError):
        rename_by_validity(src, wgrib2="/nonexistent/wgrib2_xyz_123")


def test_filter_rejects_empty_inputs(tmp_path):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    with pytest.raises(ValueError):
        filter_vars_levels(src, dst, variables=[], levels=["500 mb"])
    with pytest.raises(ValueError):
        filter_vars_levels(src, dst, variables=["TMP"], levels=[])


def test_have_wgrib2_returns_bool():
    assert isinstance(have_wgrib2(), bool)
    assert have_wgrib2("/nonexistent/wgrib2_xyz_123") is False


# ---------------------------------------------------------------------------
# Live test: verify counts records when wgrib2 is actually present
# ---------------------------------------------------------------------------

wgrib2_live = pytest.mark.skipif(
    not have_wgrib2(), reason="wgrib2 not on PATH"
)


@wgrib2_live
def test_verify_garbage_file_raises(tmp_path):
    src = tmp_path / "garbage.grib2"
    src.write_bytes(b"definitely not a grib file")
    with pytest.raises(GribError):
        verify(src)


@wgrib2_live
def test_verify_on_real_grib_if_available():
    """If a GFS GRIB2 is cached locally, check verify() returns a positive count.

    Skipped when no candidate file is found. The test is advisory — it only
    runs opportunistically on dev machines with CONVECT data mounted.
    """
    candidates: list[Path] = []
    for base in ("/gfsdata/fcst", "/gfsdata/anls", "/tmp"):
        p = Path(base)
        if not p.is_dir():
            continue
        candidates.extend(p.rglob("gfs.0p25.*.f*.grib2"))
        if candidates:
            break
    if not candidates:
        pytest.skip("no cached GFS GRIB2 file found")
    n = verify(candidates[0])
    assert n > 0


# ---------------------------------------------------------------------------
# expand_bbox
# ---------------------------------------------------------------------------

def test_expand_bbox_symmetric():
    assert expand_bbox((-45, -40, -25, -20), pad_lon=2, pad_lat=2) == (
        -47, -38, -27, -18,
    )


def test_expand_bbox_asymmetric():
    assert expand_bbox((-45, -40, -25, -20), pad_lon=3, pad_lat=1) == (
        -48, -37, -26, -19,
    )


def test_expand_bbox_zero_is_noop():
    bbox = (10.5, 20.5, -5.0, 5.0)
    assert expand_bbox(bbox, pad_lon=0, pad_lat=0) == bbox


def test_expand_bbox_clamps_latitude():
    # Latitude clamps at the poles; longitudes are left alone.
    out = expand_bbox((0, 10, -89, 89), pad_lon=0, pad_lat=5)
    assert out == (0, 10, -90.0, 90.0)


def test_expand_bbox_rejects_negative_pad():
    with pytest.raises(ValueError):
        expand_bbox((-45, -40, -25, -20), pad_lon=-1, pad_lat=0)
    with pytest.raises(ValueError):
        expand_bbox((-45, -40, -25, -20), pad_lon=0, pad_lat=-1)


# ---------------------------------------------------------------------------
# suggest_omp_threads
# ---------------------------------------------------------------------------

def test_suggest_omp_threads_splits_cores_fairly():
    # 128 cores, 12 concurrent crops, leave 2 free → (126/12)=10, capped at 8
    assert suggest_omp_threads(12, cpu_count=128) == 8
    # 128 cores, 12 concurrent, no cap → 10 threads per crop
    assert suggest_omp_threads(12, cpu_count=128, max_per_crop=32) == 10


def test_suggest_omp_threads_small_hosts_return_one():
    # Laptop: 8 cores, 12 concurrent crops → 0/12 → clamped to 1
    assert suggest_omp_threads(12, cpu_count=8) == 1
    # Tiny host: 4 cores, 4 concurrent → (2/4)=0 → 1
    assert suggest_omp_threads(4, cpu_count=4) == 1


def test_suggest_omp_threads_zero_concurrent_returns_one():
    assert suggest_omp_threads(0, cpu_count=64) == 1


def test_suggest_omp_threads_uses_os_cpu_count_when_none():
    # Just sanity-check it returns a positive int when cpu_count is auto.
    n = suggest_omp_threads(4)
    assert n >= 1


# ---------------------------------------------------------------------------
# OMP env wiring in crop / filter_vars_levels
# ---------------------------------------------------------------------------

def _fake_subprocess_run_capture_env(captured: dict):
    def _run(cmd, *, check, stdout, stderr, text, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        # fake "success" — write an empty output file so callers don't trip
        # on missing dst; crop/filter don't read their own output.
        out = cmd[-1]
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"")

        class _Ret:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Ret()

    return _run


def test_crop_passes_omp_num_threads_when_given(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    captured: dict = {}
    monkeypatch.setattr(
        "sharktopus.io.grib.subprocess.run",
        _fake_subprocess_run_capture_env(captured),
    )
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    crop(src, dst, bbox=(-45, -40, -25, -20), omp_threads=8)
    assert captured["env"] is not None
    assert captured["env"]["OMP_NUM_THREADS"] == "8"


def test_crop_reads_shark_topus_omp_threads_env(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    captured: dict = {}
    monkeypatch.setenv("SHARKTOPUS_OMP_THREADS", "4")
    monkeypatch.setattr(
        "sharktopus.io.grib.subprocess.run",
        _fake_subprocess_run_capture_env(captured),
    )
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    crop(src, dst, bbox=(-45, -40, -25, -20))
    assert captured["env"]["OMP_NUM_THREADS"] == "4"


def test_crop_no_env_when_omp_not_set(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    captured: dict = {}
    monkeypatch.delenv("SHARKTOPUS_OMP_THREADS", raising=False)
    monkeypatch.setattr(
        "sharktopus.io.grib.subprocess.run",
        _fake_subprocess_run_capture_env(captured),
    )
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    crop(src, dst, bbox=(-45, -40, -25, -20))
    assert captured["env"] is None  # inherit parent env


def test_crop_explicit_beats_env(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    captured: dict = {}
    monkeypatch.setenv("SHARKTOPUS_OMP_THREADS", "2")
    monkeypatch.setattr(
        "sharktopus.io.grib.subprocess.run",
        _fake_subprocess_run_capture_env(captured),
    )
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    crop(src, dst, bbox=(-45, -40, -25, -20), omp_threads=16)
    assert captured["env"]["OMP_NUM_THREADS"] == "16"


def test_crop_rejects_zero_omp_threads(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    with pytest.raises(ValueError):
        crop(src, dst, bbox=(-45, -40, -25, -20), omp_threads=0)


def test_crop_rejects_garbage_env(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    monkeypatch.setenv("SHARKTOPUS_OMP_THREADS", "eight")
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    with pytest.raises(ValueError):
        crop(src, dst, bbox=(-45, -40, -25, -20))


def test_filter_vars_levels_passes_omp_env(tmp_path, monkeypatch):
    src = tmp_path / "x.grib2"
    src.write_bytes(b"")
    dst = tmp_path / "y.grib2"
    captured: dict = {}
    monkeypatch.setattr(
        "sharktopus.io.grib.subprocess.run",
        _fake_subprocess_run_capture_env(captured),
    )
    monkeypatch.setattr("sharktopus.io.grib.ensure_wgrib2", lambda _: "/bin/true")
    filter_vars_levels(
        src, dst, variables=["TMP"], levels=["500 mb"], omp_threads=6,
    )
    assert captured["env"]["OMP_NUM_THREADS"] == "6"


def test_default_pad_is_positive():
    # Defaults must be > 0 so callers get a WRF-safe buffer by accident, not
    # an exact-bbox that silently breaks metgrid.
    assert DEFAULT_WRF_PAD_LON > 0
    assert DEFAULT_WRF_PAD_LAT > 0
