"""Tests for sharktopus.sources.gcloud_crop — Cloud Run backed cropping.

Client-side wrapper only: the HTTP POST is mocked with a fake
``requests`` module, no Cloud Run is ever contacted. Exercises payload
construction, inline vs. gcs response handling, quota gates, and the
URL-discovery fallback chain.
"""

from __future__ import annotations

import base64
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sharktopus.cloud import gcloud_quota
from sharktopus.sources import gcloud_crop
from sharktopus.sources.base import SourceUnavailable


@pytest.fixture
def tmp_quota(tmp_path, monkeypatch):
    """Isolate quota cache + clear all policy / URL env vars."""
    monkeypatch.setenv("SHARKTOPUS_QUOTA_CACHE", str(tmp_path / "quota.json"))
    monkeypatch.delenv("SHARKTOPUS_ACCEPT_CHARGES", raising=False)
    monkeypatch.delenv("SHARKTOPUS_MAX_SPEND_USD", raising=False)
    monkeypatch.delenv("SHARKTOPUS_LOCAL_CROP", raising=False)
    monkeypatch.delenv("SHARKTOPUS_RETAIN_GCS", raising=False)
    monkeypatch.setenv("SHARKTOPUS_GCLOUD_URL", "https://fake-run.example.com")
    yield tmp_path


@pytest.fixture
def fake_requests(monkeypatch):
    """Install a stand-in ``requests`` module. Tests set post.return_value."""
    post = MagicMock(name="requests.post")
    mod = types.ModuleType("requests")
    mod.post = post  # type: ignore[attr-defined]

    class _RequestException(Exception):
        pass

    mod.RequestException = _RequestException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", mod)

    yield types.SimpleNamespace(post=post, module=mod, RequestException=_RequestException)


def _response(status_code: int, body: dict | None = None, text: str = ""):
    """Build a MagicMock mimicking requests.Response."""
    resp = MagicMock(name="Response")
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = {"statusCode": status_code, "body": body or {}}
    return resp


# ---------------------------------------------------------------------------
# supports()
# ---------------------------------------------------------------------------

def test_supports_recent_date_with_requests_and_url(tmp_quota, fake_requests):
    assert gcloud_crop.supports("20240121") is True


def test_supports_rejects_pre_2021(tmp_quota, fake_requests):
    assert gcloud_crop.supports("20190101") is False


def test_supports_false_without_requests(tmp_quota, monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def bad_import(name, *a, **kw):
        if name == "requests":
            raise ImportError("no requests")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", bad_import)
    assert gcloud_crop.supports("20240121") is False


# ---------------------------------------------------------------------------
# Quota gate routing
# ---------------------------------------------------------------------------

def test_local_crop_env_raises_source_unavailable(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setenv("SHARKTOPUS_LOCAL_CROP", "true")
    with pytest.raises(SourceUnavailable, match="LOCAL_CROP"):
        gcloud_crop.fetch_step("20240121", "00", 6, dest=tmp_quota)


def test_free_tier_exhausted_raises(tmp_quota, fake_requests):
    state = gcloud_quota.load_quota("gcloud")
    state.invocations = gcloud_quota.GCLOUD_FREE_REQUESTS
    gcloud_quota.save_quota(state)
    with pytest.raises(SourceUnavailable, match="free tier|ACCEPT_CHARGES"):
        gcloud_crop.fetch_step("20240121", "00", 6, dest=tmp_quota)


# ---------------------------------------------------------------------------
# URL discovery fallback chain
# ---------------------------------------------------------------------------

def test_missing_url_and_no_adc_raises(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.delenv("SHARKTOPUS_GCLOUD_URL", raising=False)
    monkeypatch.setattr(gcloud_crop, "_discover_service_url", lambda *a, **kw: None)

    with pytest.raises(SourceUnavailable, match="could not resolve service URL"):
        gcloud_crop.fetch_step("20240121", "00", 6, dest=tmp_quota)


def test_explicit_url_arg_overrides_discovery(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.delenv("SHARKTOPUS_GCLOUD_URL", raising=False)
    captured: list[str] = []

    def fake_post(url, **kwargs):
        captured.append(url)
        return _response(200, {
            "mode": "inline",
            "b64": base64.b64encode(b"GRIB2").decode(),
        })

    fake_requests.post.side_effect = fake_post
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)

    out = gcloud_crop.fetch_step(
        "20240121", "00", 6,
        dest=tmp_quota, service_url="https://explicit.example.com",
        bbox=(-45, -40, -25, -20), verify=False,
    )
    assert captured == ["https://explicit.example.com"]
    assert out.exists()


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def test_payload_includes_bbox_and_vars():
    payload = gcloud_crop._build_payload(
        "20240121", "00", 6,
        bbox=(-45, -40, -25, -20),
        pad_lon=0.5, pad_lat=0.5,
        variables=["TMP", "UGRD"],
        levels=["500 mb"],
        product="pgrb2.0p25",
        response_mode="auto",
        gcs_bucket=None,
        gcs_expires_s=3600,
    )
    assert payload["date"] == "20240121"
    assert payload["variables"] == ["TMP", "UGRD"]
    assert payload["bbox"]["lon_w"] < -45
    assert "gcs_bucket" not in payload


def test_payload_rejects_bad_cycle():
    with pytest.raises(ValueError):
        gcloud_crop._build_payload(
            "20240121", "99", 6,
            bbox=None, pad_lon=0.0, pad_lat=0.0,
            variables=None, levels=None,
            product="pgrb2.0p25", response_mode="auto",
            gcs_bucket=None, gcs_expires_s=3600,
        )


# ---------------------------------------------------------------------------
# Response materialisation — inline mode
# ---------------------------------------------------------------------------

def test_inline_response_writes_bytes_to_dest(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)

    def fake_post(url, **kwargs):
        return _response(200, {
            "mode": "inline",
            "b64": base64.b64encode(b"FAKE_GRIB2_BYTES").decode(),
            "billed_duration_ms": 1200,
        })

    fake_requests.post.side_effect = fake_post

    out = gcloud_crop.fetch_step(
        "20240121", "00", 6,
        dest=tmp_quota, bbox=(-45, -40, -25, -20), verify=False,
    )
    assert out.exists()
    assert out.read_bytes() == b"FAKE_GRIB2_BYTES"
    # Quota counter advanced.
    state = gcloud_quota.load_quota("gcloud")
    assert state.invocations == 1
    # duration_s = 1.2 s from billed_duration_ms
    assert state.samples == 1
    assert state.avg_duration_s == pytest.approx(1.2, rel=1e-2)


def test_non_200_response_raises(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)

    err = MagicMock()
    err.status_code = 500
    err.text = "wgrib2 crop failed"
    fake_requests.post.return_value = err

    with pytest.raises(SourceUnavailable, match="cloud run error"):
        gcloud_crop.fetch_step(
            "20240121", "00", 6,
            dest=tmp_quota, bbox=(-45, -40, -25, -20), verify=False,
        )


def test_inline_missing_b64_raises(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)
    fake_requests.post.return_value = _response(200, {"mode": "inline"})

    with pytest.raises(SourceUnavailable, match="missing 'b64'"):
        gcloud_crop.fetch_step(
            "20240121", "00", 6,
            dest=tmp_quota, bbox=(-45, -40, -25, -20), verify=False,
        )


# ---------------------------------------------------------------------------
# GCS mode download
# ---------------------------------------------------------------------------

def test_gcs_response_downloads_and_cleans(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)

    fake_requests.post.return_value = _response(200, {
        "mode": "gcs",
        "gcs_url": "https://storage.googleapis.com/bucket/obj?X-Goog-Signature=...",
        "gcs_bucket": "bucket",
        "gcs_key": "crops/abc/file.grib2",
        "billed_duration_ms": 2500,
    })

    # Stand in for the streaming download.
    def fake_stream(url, dest, **_kw):
        Path(dest).write_bytes(b"DOWNLOADED_GRIB2")

    monkeypatch.setattr(gcloud_crop, "stream_download", fake_stream)
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gcloud_crop, "_delete_gcs",
        lambda b, k: deleted.append((b, k)),
    )

    out = gcloud_crop.fetch_step(
        "20240121", "00", 6,
        dest=tmp_quota, bbox=(-45, -40, -25, -20), verify=False,
    )
    assert out.read_bytes() == b"DOWNLOADED_GRIB2"
    assert deleted == [("bucket", "crops/abc/file.grib2")]


def test_gcs_retention_env_skips_delete(tmp_quota, monkeypatch, fake_requests):
    monkeypatch.setenv("SHARKTOPUS_RETAIN_GCS", "true")
    monkeypatch.setattr(gcloud_crop, "_id_token_for", lambda _: None)

    fake_requests.post.return_value = _response(200, {
        "mode": "gcs",
        "gcs_url": "https://storage.googleapis.com/bucket/obj",
        "gcs_bucket": "bucket",
        "gcs_key": "crops/keep/file.grib2",
    })
    monkeypatch.setattr(
        gcloud_crop, "stream_download",
        lambda url, dest, **_kw: Path(dest).write_bytes(b"X"),
    )
    deleted: list = []
    monkeypatch.setattr(
        gcloud_crop, "_delete_gcs", lambda b, k: deleted.append((b, k)),
    )

    gcloud_crop.fetch_step(
        "20240121", "00", 6,
        dest=tmp_quota, bbox=(-45, -40, -25, -20), verify=False,
    )
    assert deleted == []
