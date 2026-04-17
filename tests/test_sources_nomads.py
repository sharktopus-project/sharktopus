"""Tests for sharktopus.sources.nomads."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from sharktopus.sources import nomads
from sharktopus.sources.base import SourceUnavailable


TODAY = datetime.now(tz=timezone.utc).strftime("%Y%m%d")


def test_build_url():
    url = nomads.build_url(TODAY, "00", 6)
    assert url == (
        f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
        f"gfs.{TODAY}/00/atmos/gfs.t00z.pgrb2.0p25.f006"
    )


def test_build_url_custom_product():
    url = nomads.build_url(TODAY, "12", 120, product="pgrb2b.0p25")
    assert url.endswith("gfs.t12z.pgrb2b.0p25.f120")


def test_build_url_rejects_bad_cycle():
    with pytest.raises(ValueError):
        nomads.build_url(TODAY, "99", 6)


def test_fetch_step_retention_guard(tmp_path):
    old = (
        datetime.now(tz=timezone.utc) - timedelta(days=30)
    ).strftime("%Y%m%d")
    with pytest.raises(SourceUnavailable):
        nomads.fetch_step(old, "00", 6, dest=tmp_path)


def _make_opener(payload: bytes):
    def _open(req, timeout=None):
        stream = io.BytesIO(payload)
        cm = MagicMock()
        cm.__enter__ = lambda self: stream
        cm.__exit__ = lambda self, *a: False
        return cm
    return _open


def test_fetch_step_happy_path_writes_file(tmp_path, monkeypatch):
    """Downloads full file; no bbox → file kept as-is; verify skipped
    because wgrib2 isn't on PATH in CI."""
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")

    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    # Force verify path off regardless of wgrib2 presence
    monkeypatch.setattr("sharktopus.sources.nomads.grib.have_wgrib2",
                        lambda *a, **k: False)

    out = nomads.fetch_step(TODAY, "00", 6, dest=tmp_path)
    assert out == tmp_path / "gfs.t00z.pgrb2.0p25.f006"
    assert out.read_bytes().startswith(b"GRIB")


def test_fetch_step_with_bbox_calls_grib_crop(tmp_path, monkeypatch):
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr("sharktopus.sources.nomads.grib.have_wgrib2",
                        lambda *a, **k: False)

    crop_calls: list[tuple] = []

    def fake_crop(src, dst, bbox, wgrib2="wgrib2"):
        crop_calls.append((str(src), str(dst), bbox))
        from pathlib import Path as _P
        _P(dst).write_bytes(b"CROPPED")
        return _P(dst)

    monkeypatch.setattr("sharktopus.sources.nomads.grib.crop", fake_crop)

    out = nomads.fetch_step(
        TODAY, "00", 6, dest=tmp_path, bbox=(-45, -40, -25, -20),
        pad_lon=0, pad_lat=0,  # exact-bbox, no buffer
    )
    assert out.read_bytes() == b"CROPPED"
    assert len(crop_calls) == 1
    _, _, bbox = crop_calls[0]
    assert bbox == (-45, -40, -25, -20)
    # .full intermediate should have been cleaned up
    assert not (tmp_path / "gfs.t00z.pgrb2.0p25.f006.full").exists()


def test_fetch_step_default_pad_expands_crop(tmp_path, monkeypatch):
    """Default pad_lon/pad_lat expand the bbox before cropping — user gets
    a WRF-safe margin without having to ask for it."""
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr("sharktopus.sources.nomads.grib.have_wgrib2",
                        lambda *a, **k: False)

    crop_calls: list[tuple] = []

    def fake_crop(src, dst, bbox, wgrib2="wgrib2"):
        crop_calls.append(bbox)
        from pathlib import Path as _P
        _P(dst).write_bytes(b"CROPPED")
        return _P(dst)

    monkeypatch.setattr("sharktopus.sources.nomads.grib.crop", fake_crop)

    nomads.fetch_step(
        TODAY, "00", 6, dest=tmp_path, bbox=(-45, -40, -25, -20),
        pad_lon=3, pad_lat=1,
    )
    assert crop_calls == [(-48, -37, -26, -19)]


def test_fetch_step_404_raises_source_unavailable(tmp_path, monkeypatch):
    import urllib.error
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)

    def _open(req, timeout=None):
        raise urllib.error.HTTPError(
            url=req.full_url, code=404, msg="Not Found", hdrs=None, fp=None,
        )
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", _open)

    with pytest.raises(SourceUnavailable):
        nomads.fetch_step(TODAY, "00", 6, dest=tmp_path, verify=False)
