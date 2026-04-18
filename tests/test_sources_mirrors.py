"""Tests for the AWS / GCloud / Azure / RDA full-file mirrors.

All four sources share the same full-download + local-crop recipe via
``sharktopus.sources._common``, so we exercise each one with a small
matrix of behaviours rather than duplicating every test.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from sharktopus.sources import aws, azure, gcloud, rda
from sharktopus.sources.base import SourceUnavailable

TODAY = datetime.now(tz=timezone.utc).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

def test_aws_build_url():
    assert aws.build_url("20240121", "00", 6) == (
        "https://noaa-gfs-bdp-pds.s3.amazonaws.com/"
        "gfs.20240121/00/atmos/gfs.t00z.pgrb2.0p25.f006"
    )


def test_gcloud_build_url():
    assert gcloud.build_url("20240121", "00", 6) == (
        "https://storage.googleapis.com/global-forecast-system/"
        "gfs.20240121/00/atmos/gfs.t00z.pgrb2.0p25.f006"
    )


def test_azure_build_url():
    assert azure.build_url("20240121", "00", 6) == (
        "https://noaagfs.blob.core.windows.net/gfs/"
        "gfs.20240121/00/atmos/gfs.t00z.pgrb2.0p25.f006"
    )


def test_rda_build_url_uses_validity_time_filename():
    """RDA ds084.1 stores files under <year>/<date>/ with validity-time name."""
    assert rda.build_url("20240121", "06", 24) == (
        "https://data.rda.ucar.edu/d084001/"
        "2024/20240121/gfs.0p25.2024012106.f024.grib2"
    )


def test_rda_rda_filename_helper():
    assert rda.rda_filename("20240121", "12", 6) == "gfs.0p25.2024012112.f006.grib2"


@pytest.mark.parametrize("mod", [aws, gcloud, azure, rda])
def test_build_url_rejects_bad_cycle(mod):
    with pytest.raises(ValueError):
        mod.build_url(TODAY, "99", 6)


# ---------------------------------------------------------------------------
# Default worker ceilings (anti-throttle)
# ---------------------------------------------------------------------------

def test_default_max_workers_are_exposed():
    # Cloud mirrors can absorb some parallelism; RDA intentionally serial.
    assert aws.DEFAULT_MAX_WORKERS >= 2
    assert gcloud.DEFAULT_MAX_WORKERS >= 2
    assert azure.DEFAULT_MAX_WORKERS >= 2
    assert rda.DEFAULT_MAX_WORKERS == 1


# ---------------------------------------------------------------------------
# RDA-specific: retention guard (no data before 2015-01-15)
# ---------------------------------------------------------------------------

def test_rda_rejects_old_dates(tmp_path):
    with pytest.raises(SourceUnavailable, match="ds084.1"):
        rda.fetch_step("20100101", "00", 0, dest=tmp_path)


# ---------------------------------------------------------------------------
# fetch_step happy-path (shared opener helper)
# ---------------------------------------------------------------------------

def _make_opener(payload: bytes):
    def _open(req, timeout=None):
        stream = io.BytesIO(payload)
        cm = MagicMock()
        cm.__enter__ = lambda self: stream
        cm.__exit__ = lambda self, *a: False
        return cm
    return _open


@pytest.mark.parametrize(
    "mod,date",
    [
        (aws, TODAY),
        (gcloud, TODAY),
        (azure, TODAY),
        (rda, "20240121"),  # RDA needs a post-2015 date
    ],
)
def test_fetch_step_happy_path(mod, date, tmp_path, monkeypatch):
    """Downloads full file; no bbox → kept as-is; wgrib2 verify skipped."""
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")

    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    # Force verify path off regardless of wgrib2 presence (tests ride
    # the shared _common.download_and_crop so any one source proves it).
    monkeypatch.setattr(
        "sharktopus.sources._common.grib.have_wgrib2",
        lambda *a, **k: False,
    )

    out = mod.fetch_step(date, "00", 6, dest=tmp_path)
    assert out == tmp_path / "gfs.t00z.pgrb2.0p25.f006"
    assert out.read_bytes().startswith(b"GRIB")


@pytest.mark.parametrize("mod", [aws, gcloud, azure])
def test_fetch_step_with_bbox_crops_locally(mod, tmp_path, monkeypatch):
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr(
        "sharktopus.sources._common.grib.have_wgrib2",
        lambda *a, **k: False,
    )

    crop_calls: list[tuple] = []

    def fake_crop(src, dst, bbox, wgrib2="wgrib2"):
        from pathlib import Path as _P
        crop_calls.append((str(src), str(dst), bbox))
        _P(dst).write_bytes(b"CROPPED")
        return _P(dst)

    monkeypatch.setattr("sharktopus.sources._common.grib.crop", fake_crop)

    out = mod.fetch_step(
        TODAY, "00", 6, dest=tmp_path,
        bbox=(-45, -40, -25, -20), pad_lon=0, pad_lat=0,
    )
    assert out.read_bytes() == b"CROPPED"
    assert len(crop_calls) == 1
    _, _, bbox = crop_calls[0]
    assert bbox == (-45, -40, -25, -20)
    # .full intermediate must be cleaned up
    assert not (tmp_path / "gfs.t00z.pgrb2.0p25.f006.full").exists()


@pytest.mark.parametrize(
    "mod,date",
    [(aws, TODAY), (gcloud, TODAY), (azure, TODAY), (rda, "20240121")],
)
def test_fetch_step_404_raises_source_unavailable(mod, date, tmp_path, monkeypatch):
    import urllib.error
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)

    def _open(req, timeout=None):
        raise urllib.error.HTTPError(
            url=req.full_url, code=404, msg="Not Found", hdrs=None, fp=None,
        )
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", _open)

    with pytest.raises(SourceUnavailable):
        mod.fetch_step(date, "00", 6, dest=tmp_path, verify=False)


@pytest.mark.parametrize(
    "mod,date",
    [(aws, TODAY), (gcloud, TODAY), (azure, TODAY), (rda, "20240121")],
)
def test_fetch_step_defaults_to_convention_dir(mod, date, tmp_path, monkeypatch):
    opener = _make_opener(b"GRIB" + b"\x00" * 100 + b"7777")
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr(
        "sharktopus.sources._common.grib.have_wgrib2",
        lambda *a, **k: False,
    )
    monkeypatch.setenv("SHARKTOPUS_DATA", str(tmp_path))

    out = mod.fetch_step(date, "00", 6)
    expected_dir = tmp_path / "fcst" / f"{date}00" / "90S_180W_90N_180E"
    assert out == expected_dir / "gfs.t00z.pgrb2.0p25.f006"


# ---------------------------------------------------------------------------
# RDA auth cookie passthrough
# ---------------------------------------------------------------------------

def test_rda_cookie_is_forwarded(tmp_path, monkeypatch):
    captured_headers: list[dict] = []

    def _open(req, timeout=None):
        captured_headers.append(dict(req.headers))
        stream = io.BytesIO(b"GRIB" + b"\x00" * 20 + b"7777")
        cm = MagicMock()
        cm.__enter__ = lambda self: stream
        cm.__exit__ = lambda self, *a: False
        return cm

    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.urllib.request, "urlopen", _open)
    monkeypatch.setattr(
        "sharktopus.sources._common.grib.have_wgrib2",
        lambda *a, **k: False,
    )
    monkeypatch.setenv("SHARKTOPUS_RDA_COOKIE", "rda-session=abc123")

    rda.fetch_step("20240121", "00", 6, dest=tmp_path)
    # urllib capitalizes header names; check case-insensitively
    lowered = {k.lower(): v for k, v in captured_headers[0].items()}
    assert lowered.get("cookie") == "rda-session=abc123"
