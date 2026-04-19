"""Tests for sharktopus.io.paths (default output convention)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sharktopus.io import paths


# ---------------------------------------------------------------------------
# bbox_tag
# ---------------------------------------------------------------------------

def test_bbox_tag_southern_hemisphere():
    # CONVECT: (lat_s=-32, lon_w=-52, lat_n=-13, lon_e=-28) → "32S_52W_13S_28W"
    assert paths.bbox_tag((-52.0, -28.0, -32.0, -13.0)) == "32S_52W_13S_28W"


def test_bbox_tag_mixed_hemispheres():
    # lat_s=-5, lon_w=-10, lat_n=5, lon_e=10
    assert paths.bbox_tag((-10.0, 10.0, -5.0, 5.0)) == "5S_10W_5N_10E"


def test_bbox_tag_northern_hemisphere():
    assert paths.bbox_tag((5.0, 15.0, 40.0, 55.0)) == "40N_5E_55N_15E"


def test_bbox_tag_rounds_coords():
    # CONVECT uses %.0f — values round to the nearest integer.
    assert paths.bbox_tag((-52.3, -27.7, -31.9, -12.4)) == "32S_52W_12S_28W"


def test_bbox_tag_none_is_global():
    assert paths.bbox_tag(None) == "90S_180W_90N_180E"
    assert paths.bbox_tag(None) == paths.GLOBAL_BBOX_TAG


# ---------------------------------------------------------------------------
# cycle_dir
# ---------------------------------------------------------------------------

def test_cycle_dir_concatenation():
    assert paths.cycle_dir("20240121", "00") == "2024012100"
    assert paths.cycle_dir("20241216", "18") == "2024121618"


# ---------------------------------------------------------------------------
# default_root / $SHARKTOPUS_DATA
# ---------------------------------------------------------------------------

def test_default_root_without_env(monkeypatch):
    monkeypatch.delenv("SHARKTOPUS_DATA", raising=False)
    assert paths.default_root() == paths.DEFAULT_ROOT


def test_default_root_honors_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path))
    assert paths.default_root() == tmp_path


def test_default_root_expands_tilde(monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_DATA", "~/custom-gfs")
    assert paths.default_root() == Path.home() / "custom-gfs"


# ---------------------------------------------------------------------------
# output_dir — the user-visible composition
# ---------------------------------------------------------------------------

def test_output_dir_default_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path))
    out = paths.output_dir(
        date="20240121", cycle="00", bbox=(-52.0, -28.0, -32.0, -13.0),
    )
    assert out == tmp_path / "fcst" / "2024012100" / "32S_52W_13S_28W"
    assert out.is_dir()


def test_output_dir_explicit_root_beats_env(monkeypatch, tmp_path):
    other = tmp_path / "other-root"
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path / "from-env"))
    out = paths.output_dir(
        date="20240121", cycle="00",
        bbox=(-52.0, -28.0, -32.0, -13.0),
        root=other,
    )
    assert out == other / "fcst" / "2024012100" / "32S_52W_13S_28W"


def test_output_dir_global_when_bbox_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path))
    out = paths.output_dir(date="20240121", cycle="00")
    assert out == tmp_path / "fcst" / "2024012100" / "90S_180W_90N_180E"


def test_output_dir_anls_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path))
    out = paths.output_dir(date="20240121", cycle="00", mode="anls")
    assert out == tmp_path / "anls" / "2024012100" / "90S_180W_90N_180E"


def test_output_dir_rejects_bad_mode(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        paths.output_dir(
            date="20240121", cycle="00", mode="bogus", root=tmp_path,
        )


def test_bbox_tag_rejects_bad_axis():
    with pytest.raises(ValueError, match="axis"):
        paths._coord(10.0, "altitude")
