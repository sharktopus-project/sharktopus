"""Tests for sharktopus.sources.nomads_filter."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from sharktopus.sources import nomads_filter
from sharktopus.sources.base import SourceUnavailable


TODAY = datetime.now(tz=timezone.utc).strftime("%Y%m%d")


def test_level_to_param_examples():
    assert nomads_filter.level_to_param("500 mb") == "lev_500_mb"
    assert nomads_filter.level_to_param("surface") == "lev_surface"
    assert nomads_filter.level_to_param("mean sea level") == "lev_mean_sea_level"
    assert nomads_filter.level_to_param("2 m above ground") == "lev_2_m_above_ground"
    assert (
        nomads_filter.level_to_param("0-0.1 m below ground")
        == "lev_0-0.1_m_below_ground"
    )


def test_build_url_structure():
    url = nomads_filter.build_url(
        TODAY, "00", 6,
        variables=["TMP", "UGRD"],
        levels=["500 mb", "surface"],
        bbox=(-45, -40, -25, -20),
        pad_lon=0, pad_lat=0,
    )
    parsed = urlparse(url)
    assert parsed.path.endswith("filter_gfs_0p25.pl")
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["dir"] == [f"/gfs.{TODAY}/00/atmos"]
    assert qs["file"] == ["gfs.t00z.pgrb2.0p25.f006"]
    assert qs["var_TMP"] == ["on"]
    assert qs["var_UGRD"] == ["on"]
    assert qs["lev_500_mb"] == ["on"]
    assert qs["lev_surface"] == ["on"]
    assert qs["toplat"] == ["-20"]
    assert qs["bottomlat"] == ["-25"]
    assert qs["leftlon"] == ["-45"]
    assert qs["rightlon"] == ["-40"]


def test_build_url_default_pad_is_wrf_safe():
    # Defaults are grib.DEFAULT_WRF_PAD_LON / LAT (2° each): caller who just
    # passes a bbox gets a WRF-safe margin for free.
    url = nomads_filter.build_url(
        TODAY, "00", 6,
        variables=["TMP"], levels=["500 mb"],
        bbox=(-45, -40, -25, -20),
    )
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert qs["toplat"] == ["-18"]       # -20 + 2
    assert qs["bottomlat"] == ["-27"]    # -25 - 2
    assert qs["leftlon"] == ["-47"]      # -45 - 2
    assert qs["rightlon"] == ["-38"]     # -40 + 2


def test_build_url_pads_bbox():
    url = nomads_filter.build_url(
        TODAY, "00", 6,
        variables=["TMP"], levels=["500 mb"],
        bbox=(-45, -40, -25, -20), pad_lon=5, pad_lat=5,
    )
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert qs["toplat"] == ["-15"]       # -20 + 5
    assert qs["bottomlat"] == ["-30"]    # -25 - 5
    assert qs["leftlon"] == ["-50"]      # -45 - 5
    assert qs["rightlon"] == ["-35"]     # -40 + 5


def test_build_url_asymmetric_pad():
    # pad_lon and pad_lat can differ — e.g. a zonal-elongated domain.
    url = nomads_filter.build_url(
        TODAY, "00", 6,
        variables=["TMP"], levels=["500 mb"],
        bbox=(-45, -40, -25, -20), pad_lon=3, pad_lat=1,
    )
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert qs["toplat"] == ["-19"]       # -20 + 1
    assert qs["bottomlat"] == ["-26"]    # -25 - 1
    assert qs["leftlon"] == ["-48"]      # -45 - 3
    assert qs["rightlon"] == ["-37"]     # -40 + 3


def test_build_url_hourly_flag():
    url = nomads_filter.build_url(
        TODAY, "00", 6,
        variables=["TMP"], levels=["500 mb"],
        bbox=(-45, -40, -25, -20), hourly=True,
    )
    assert "filter_gfs_0p25_1hr.pl" in url


def test_build_url_rejects_empty_vars_or_levels():
    with pytest.raises(ValueError):
        nomads_filter.build_url(
            TODAY, "00", 6,
            variables=[], levels=["500 mb"],
            bbox=(-45, -40, -25, -20),
        )
    with pytest.raises(ValueError):
        nomads_filter.build_url(
            TODAY, "00", 6,
            variables=["TMP"], levels=[],
            bbox=(-45, -40, -25, -20),
        )


def test_build_url_rejects_inverted_bbox():
    with pytest.raises(ValueError):
        nomads_filter.build_url(
            TODAY, "00", 6,
            variables=["TMP"], levels=["500 mb"],
            bbox=(-40, -45, -25, -20),  # lon_e < lon_w
        )


def test_fetch_step_happy_path(tmp_path, monkeypatch):
    def _open(req, timeout=None):
        stream = io.BytesIO(b"GRIB" + b"\x00" * 50 + b"7777")
        cm = MagicMock()
        cm.__enter__ = lambda self: stream
        cm.__exit__ = lambda self, *a: False
        return cm

    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", _open)
    monkeypatch.setattr(
        "sharktopus.sources.nomads_filter.grib.have_wgrib2",
        lambda *a, **k: False,
    )

    out = nomads_filter.fetch_step(
        TODAY, "00", 6,
        dest=tmp_path,
        bbox=(-45, -40, -25, -20),
        variables=["TMP"], levels=["500 mb"],
    )
    assert out == tmp_path / "gfs.t00z.pgrb2.0p25.f006"
    assert out.read_bytes().startswith(b"GRIB")
