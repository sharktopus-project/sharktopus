"""Tests for byte-range download (idx-driven partial GRIB2 fetch).

Covers the low-level helpers in ``sharktopus.sources.base``
(``fetch_text``, ``head_size``, ``stream_byte_ranges``) and the
high-level pipeline in ``sharktopus.sources._common``
(``download_byte_ranges_and_crop``).

No network — everything is monkeypatched through the ``opener`` kwarg.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
import urllib.error

from sharktopus.sources import _common
from sharktopus.sources.base import (
    SourceUnavailable,
    fetch_text,
    head_size,
    stream_byte_ranges,
)


# ---------------------------------------------------------------------------
# Helpers: mock urlopen returning bytes / text / HEAD responses
# ---------------------------------------------------------------------------

def _cm(stream, headers: dict[str, str] | None = None):
    cm = MagicMock()
    resp = MagicMock()
    resp.read = stream.read
    resp.headers = headers or {}
    cm.__enter__ = lambda self: resp
    cm.__exit__ = lambda self, *a: False
    return cm


def _opener_with(payload_by_url: dict[str, bytes], head_by_url: dict[str, int] | None = None):
    """Build an opener that returns payload_by_url[url] on GET / HEAD."""
    head_by_url = head_by_url or {}

    def _open(req, timeout=None):
        method = getattr(req, "method", None) or "GET"
        url = req.full_url
        range_header = req.headers.get("Range")

        if method == "HEAD":
            if url in head_by_url:
                return _cm(io.BytesIO(b""), {"Content-Length": str(head_by_url[url])})
            raise urllib.error.HTTPError(
                url=url, code=405, msg="HEAD not allowed", hdrs=None, fp=None
            )

        payload = payload_by_url.get(url)
        if payload is None:
            raise urllib.error.HTTPError(
                url=url, code=404, msg="not found", hdrs=None, fp=None
            )

        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes="):]
            s, e = spec.split("-")
            start = int(s)
            end = int(e) if e else len(payload) - 1
            data = payload[start : end + 1]
            hdrs = {"Content-Range": f"bytes {start}-{end}/{len(payload)}"}
            return _cm(io.BytesIO(data), hdrs)

        return _cm(io.BytesIO(payload))

    return _open


# ---------------------------------------------------------------------------
# fetch_text
# ---------------------------------------------------------------------------

def test_fetch_text_returns_utf8():
    opener = _opener_with({"http://x/y.idx": "1:0:d=2024010100:TMP:500 mb:6 hour fcst\n".encode()})
    text = fetch_text("http://x/y.idx", opener=opener, max_retries=1)
    assert "TMP:500 mb" in text


def test_fetch_text_404_raises_source_unavailable():
    opener = _opener_with({})
    with pytest.raises(SourceUnavailable):
        fetch_text("http://x/missing.idx", opener=opener, max_retries=1, retry_wait=0)


# ---------------------------------------------------------------------------
# head_size
# ---------------------------------------------------------------------------

def test_head_size_uses_content_length():
    opener = _opener_with(
        {"http://x/y.grib2": b"0" * 12345},
        head_by_url={"http://x/y.grib2": 12345},
    )
    assert head_size("http://x/y.grib2", opener=opener, max_retries=1) == 12345


def test_head_size_falls_back_to_range_when_head_405():
    """Some S3-style hosts reject HEAD — we must fall back to a 0-0 Range GET."""
    payload = b"A" * 500
    # head_by_url empty → HEAD raises 405; GET with Range should still work
    opener = _opener_with({"http://x/y.grib2": payload})
    assert head_size("http://x/y.grib2", opener=opener, max_retries=1, retry_wait=0) == 500


def test_head_size_404_raises_source_unavailable():
    opener = _opener_with({})
    with pytest.raises(SourceUnavailable):
        head_size("http://x/missing.grib2", opener=opener, max_retries=1, retry_wait=0)


# ---------------------------------------------------------------------------
# stream_byte_ranges
# ---------------------------------------------------------------------------

def test_stream_byte_ranges_concatenates_in_order(tmp_path):
    payload = bytes(range(256)) * 4  # 1024 bytes, deterministic
    opener = _opener_with({"http://x/y.grib2": payload})
    dst = tmp_path / "out.grib2"

    # Ask for two non-adjacent ranges, in reverse order of what we pass
    ranges = [(10, 19), (100, 109)]
    stream_byte_ranges(
        "http://x/y.grib2", ranges, dst,
        opener=opener, max_workers=2, max_retries=1,
    )

    data = dst.read_bytes()
    assert data == payload[10:20] + payload[100:110]


def test_stream_byte_ranges_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        stream_byte_ranges("http://x/y", [], "/tmp/ignored", max_retries=1)


def test_stream_byte_ranges_404_raises_source_unavailable(tmp_path):
    opener = _opener_with({})
    with pytest.raises(SourceUnavailable):
        stream_byte_ranges(
            "http://x/missing", [(0, 9)], tmp_path / "out",
            opener=opener, max_retries=1, retry_wait=0,
        )


def test_stream_byte_ranges_preserves_order_under_parallelism(tmp_path):
    """With max_workers > 1, futures complete out of order. Output must still be ordered."""
    payload = b"".join(bytes([i]) * 50 for i in range(10))  # 500 bytes, 10 blocks
    opener = _opener_with({"http://x/y": payload})
    dst = tmp_path / "out"

    ranges = [(i * 50, i * 50 + 49) for i in range(10)]
    stream_byte_ranges(
        "http://x/y", ranges, dst,
        opener=opener, max_workers=5, max_retries=1,
    )
    assert dst.read_bytes() == payload


# ---------------------------------------------------------------------------
# download_byte_ranges_and_crop — end-to-end pipeline with mocked opener
# ---------------------------------------------------------------------------

IDX_TEXT = (
    "1:0:d=2024010100:TMP:500 mb:6 hour fcst\n"
    "2:100:d=2024010100:UGRD:500 mb:6 hour fcst\n"
    "3:250:d=2024010100:VGRD:500 mb:6 hour fcst\n"
    "4:450:d=2024010100:HGT:500 mb:6 hour fcst\n"
    "5:700:d=2024010100:RH:850 mb:6 hour fcst\n"
)


def _inject_opener(monkeypatch, opener):
    """Replace the default urlopen used by base helpers with *opener*."""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", opener)


def test_download_byte_ranges_and_crop_selects_requested_records(monkeypatch, tmp_path):
    """Only records matching variables/levels are downloaded."""
    grib_payload = b"X" * 1000
    opener = _opener_with(
        {
            "http://x/y.grib2": grib_payload,
            "http://x/y.grib2.idx": IDX_TEXT.encode(),
        },
        head_by_url={"http://x/y.grib2": 1000},
    )
    _inject_opener(monkeypatch, opener)

    dst = tmp_path / "out.grib2"
    _common.download_byte_ranges_and_crop(
        "http://x/y.grib2", dst,
        variables=["TMP", "UGRD"], levels=["500 mb"],
        verify=False,  # no wgrib2 needed for the mock payload
        max_retries=1, retry_wait=0,
    )

    # TMP: bytes 0-99 (offset 0, next offset 100 → end=99)
    # UGRD: bytes 100-249
    # Two adjacent ranges merge into one (0-249)
    assert dst.read_bytes() == grib_payload[0:250]


def test_download_byte_ranges_and_crop_raises_when_no_match(monkeypatch, tmp_path):
    opener = _opener_with(
        {
            "http://x/y.grib2": b"X" * 1000,
            "http://x/y.grib2.idx": IDX_TEXT.encode(),
        },
        head_by_url={"http://x/y.grib2": 1000},
    )
    _inject_opener(monkeypatch, opener)

    with pytest.raises(SourceUnavailable, match="no records"):
        _common.download_byte_ranges_and_crop(
            "http://x/y.grib2", tmp_path / "out",
            variables=["NOPE"], levels=["500 mb"],
            verify=False, max_retries=1, retry_wait=0,
        )


def test_download_byte_ranges_and_crop_404_on_idx(monkeypatch, tmp_path):
    opener = _opener_with({"http://x/y.grib2": b"X" * 100})
    _inject_opener(monkeypatch, opener)

    with pytest.raises(SourceUnavailable):
        _common.download_byte_ranges_and_crop(
            "http://x/y.grib2", tmp_path / "out",
            variables=["TMP"], levels=["500 mb"],
            verify=False, max_retries=1, retry_wait=0,
        )


def test_download_byte_ranges_and_crop_requires_variables_and_levels(tmp_path):
    with pytest.raises(ValueError, match="variables"):
        _common.download_byte_ranges_and_crop(
            "http://x/y.grib2", tmp_path / "out",
            variables=[], levels=["500 mb"],
            verify=False, max_retries=1,
        )
    with pytest.raises(ValueError, match="levels"):
        _common.download_byte_ranges_and_crop(
            "http://x/y.grib2", tmp_path / "out",
            variables=["TMP"], levels=[],
            verify=False, max_retries=1,
        )


def test_download_byte_ranges_merges_adjacent_records(monkeypatch, tmp_path):
    """Adjacent records (TMP+UGRD+VGRD+HGT at 500 mb) merge to a single HTTP request."""
    grib_payload = b"X" * 1000
    call_counter = {"range_requests": 0}

    def counting_opener(req, timeout=None):
        if req.headers.get("Range"):
            call_counter["range_requests"] += 1
        return _opener_with(
            {
                "http://x/y.grib2": grib_payload,
                "http://x/y.grib2.idx": IDX_TEXT.encode(),
            },
            head_by_url={"http://x/y.grib2": 1000},
        )(req, timeout)

    _inject_opener(monkeypatch, counting_opener)

    _common.download_byte_ranges_and_crop(
        "http://x/y.grib2", tmp_path / "out",
        variables=["TMP", "UGRD", "VGRD", "HGT"], levels=["500 mb"],
        verify=False, max_retries=1, retry_wait=0,
    )
    # 4 consecutive records at 500 mb → 1 merged Range GET (plus 1 HEAD for size).
    # HEAD uses Range: 0-0 as fallback only when HEAD fails — here we provide
    # Content-Length, so HEAD succeeds without a Range call.
    assert call_counter["range_requests"] == 1
