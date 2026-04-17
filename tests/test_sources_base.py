"""Tests for sharktopus.sources.base."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import urllib.error

from sharktopus.sources.base import (
    SourceUnavailable,
    canonical_filename,
    check_retention,
    stream_download,
    validate_cycle,
    validate_date,
)


def test_canonical_filename():
    assert canonical_filename("00", 0) == "gfs.t00z.pgrb2.0p25.f000"
    assert canonical_filename("12", 6) == "gfs.t12z.pgrb2.0p25.f006"
    assert canonical_filename("18", 120) == "gfs.t18z.pgrb2.0p25.f120"
    assert canonical_filename("00", 6, product="pgrb2b.0p25") == (
        "gfs.t00z.pgrb2b.0p25.f006"
    )


def test_canonical_filename_rejects_bad_cycle():
    with pytest.raises(ValueError):
        canonical_filename("03", 6)


def test_canonical_filename_rejects_negative_fxx():
    with pytest.raises(ValueError):
        canonical_filename("00", -1)


def test_validate_cycle_ok():
    for c in ("00", "06", "12", "18"):
        assert validate_cycle(c) == c


def test_validate_cycle_bad():
    with pytest.raises(ValueError):
        validate_cycle("24")


def test_validate_date_ok():
    dt = validate_date("20240121")
    assert dt.year == 2024 and dt.month == 1 and dt.day == 21
    assert dt.tzinfo == timezone.utc


def test_validate_date_bad():
    with pytest.raises(ValueError):
        validate_date("2024-01-21")
    with pytest.raises(ValueError):
        validate_date("not-a-date")


def test_check_retention_within_window():
    now = datetime.now(tz=timezone.utc)
    fresh = (now - timedelta(days=3)).strftime("%Y%m%d")
    check_retention(fresh, days=10, now=now)  # no raise


def test_check_retention_outside_window():
    now = datetime.now(tz=timezone.utc)
    stale = (now - timedelta(days=30)).strftime("%Y%m%d")
    with pytest.raises(SourceUnavailable):
        check_retention(stale, days=10, now=now)


# ---------------------------------------------------------------------------
# stream_download — with a mocked urlopen
# ---------------------------------------------------------------------------

def _fake_urlopen_ok(payload: bytes):
    """Return a callable that mimics urlopen's context manager."""
    def _open(req, timeout=None):
        stream = io.BytesIO(payload)
        cm = MagicMock()
        cm.__enter__ = lambda self: stream
        cm.__exit__ = lambda self, *a: False
        return cm
    return _open


def _fake_urlopen_http_error(code: int):
    def _open(req, timeout=None):
        raise urllib.error.HTTPError(
            url=getattr(req, "full_url", str(req)),
            code=code, msg="boom", hdrs=None, fp=None,
        )
    return _open


def test_stream_download_writes_file(tmp_path):
    dst = tmp_path / "out.grib2"
    opener = _fake_urlopen_ok(b"GRIB" + b"\x00" * 1000 + b"7777")
    result = stream_download("http://x/y", dst, opener=opener, max_retries=1)
    assert result == dst
    assert dst.read_bytes().startswith(b"GRIB")
    # .part should have been renamed, not left behind
    assert not (tmp_path / "out.grib2.part").exists()


def test_stream_download_404_raises_source_unavailable_without_retry(tmp_path):
    dst = tmp_path / "out.grib2"
    calls = {"n": 0}
    base = _fake_urlopen_http_error(404)

    def _open(req, timeout=None):
        calls["n"] += 1
        return base(req, timeout=timeout)

    with pytest.raises(SourceUnavailable):
        stream_download("http://x/y", dst, opener=_open, max_retries=5, retry_wait=0)
    assert calls["n"] == 1  # no retries on 404


def test_stream_download_retries_on_timeout_then_fails(tmp_path, monkeypatch):
    dst = tmp_path / "out.grib2"
    calls = {"n": 0}
    # Make time.sleep a no-op to keep the test fast
    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)

    def _open(req, timeout=None):
        calls["n"] += 1
        raise TimeoutError("slow")

    with pytest.raises(SourceUnavailable):
        stream_download("http://x/y", dst, opener=_open, max_retries=3, retry_wait=0)
    assert calls["n"] == 3


def test_stream_download_cleans_part_file_on_error(tmp_path, monkeypatch):
    dst = tmp_path / "out.grib2"
    part = dst.with_suffix(dst.suffix + ".part")
    # Pre-create a stale .part to make sure it's cleaned up
    part.write_bytes(b"stale")

    import sharktopus.sources.base as base_mod
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)

    def _open(req, timeout=None):
        raise TimeoutError("slow")

    with pytest.raises(SourceUnavailable):
        stream_download("http://x/y", dst, opener=_open, max_retries=2, retry_wait=0)
    assert not part.exists()
    assert not dst.exists()
