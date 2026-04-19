"""Tests for sharktopus.io.config (INI loader)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from sharktopus.io import config


def _write(tmp_path, body: str):
    p = tmp_path / "my.ini"
    p.write_text(dedent(body).lstrip())
    return p


def test_loads_full_file(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        timestamps = 2024010200, 2024010206
        ext = 24
        interval = 3
        lat_s = -28
        lat_n = -18
        lon_w = -48
        lon_e = -36
        priority = nomads_filter, nomads
        variables = TMP, UGRD, VGRD, HGT
        levels = 500 mb, 850 mb, surface
    """)
    cfg = config.load_config(p)
    assert cfg == {
        "timestamps": ["2024010200", "2024010206"],
        "ext": 24,
        "interval": 3,
        "lat_s": -28.0,
        "lat_n": -18.0,
        "lon_w": -48.0,
        "lon_e": -36.0,
        "priority": ["nomads_filter", "nomads"],
        "variables": ["TMP", "UGRD", "VGRD", "HGT"],
        "levels": ["500 mb", "850 mb", "surface"],
    }


def test_start_end_step_only(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        start = 2024010200
        end = 2024010318
        step = 6
        lat_s = -10
        lat_n = 0
        lon_w = -50
        lon_e = -40
    """)
    cfg = config.load_config(p)
    assert cfg["start"] == "2024010200"
    assert cfg["end"] == "2024010318"
    assert cfg["step"] == 6
    assert "timestamps" not in cfg


def test_list_split_on_whitespace_when_no_comma(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        priority = nomads_filter nomads
    """)
    cfg = config.load_config(p)
    assert cfg["priority"] == ["nomads_filter", "nomads"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load_config(tmp_path / "nope.ini")


def test_missing_section_raises(tmp_path):
    p = _write(tmp_path, """
        [other]
        foo = 1
    """)
    with pytest.raises(config.ConfigError, match=r"\[gfs\]"):
        config.load_config(p)


def test_unknown_key_raises(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        lat_s = -10
        lat_north = 0   ; typo of lat_n
    """)
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load_config(p)


def test_int_coercion_error(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        ext = twenty-four
    """)
    with pytest.raises(config.ConfigError, match="not an int"):
        config.load_config(p)


def test_float_coercion_error(tmp_path):
    p = _write(tmp_path, """
        [gfs]
        lat_s = south
    """)
    with pytest.raises(config.ConfigError, match="not a float"):
        config.load_config(p)


def test_empty_gfs_section_is_ok(tmp_path):
    p = _write(tmp_path, "[gfs]\n")
    assert config.load_config(p) == {}
