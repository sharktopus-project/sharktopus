"""Tests for :mod:`sharktopus.sources.aws_crop` — Lambda-backed cropping.

Exercises the client wrapper with mocks: payload construction, inline
vs. S3 response handling, quota gates, boto3/credential fallback. We
never actually invoke a Lambda here — the "lambda client" is a
MagicMock and the S3 presigned download is monkey-patched to read
from a temp file.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sharktopus import aws_quota
from sharktopus.sources import aws_crop
from sharktopus.sources.base import SourceUnavailable


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_quota(tmp_path, monkeypatch):
    """Isolate the quota cache and clear env-var policy gates."""
    monkeypatch.setenv("SHARKTOPUS_QUOTA_CACHE", str(tmp_path / "quota.json"))
    monkeypatch.delenv("SHARKTOPUS_ACCEPT_CHARGES", raising=False)
    monkeypatch.delenv("SHARKTOPUS_MAX_SPEND_USD", raising=False)
    monkeypatch.delenv("SHARKTOPUS_LOCAL_CROP", raising=False)
    monkeypatch.delenv("SHARKTOPUS_RETAIN_S3", raising=False)
    yield tmp_path


@pytest.fixture
def fake_boto3(monkeypatch):
    """Install a stand-in ``boto3`` module with predictable Session/clients.

    The fake exposes a single shared ``lambda_client`` / ``s3_client`` so
    tests can set return values on them after importing the fixture.
    """
    lambda_client = MagicMock(name="LambdaClient")
    s3_client = MagicMock(name="S3Client")
    session = MagicMock(name="Session")
    session.get_credentials.return_value = object()

    def _client(service_name, **_kwargs):
        if service_name == "lambda":
            return lambda_client
        if service_name == "s3":
            return s3_client
        raise ValueError(f"unexpected service {service_name!r}")

    mod = types.ModuleType("boto3")
    mod.Session = MagicMock(return_value=session)  # type: ignore[attr-defined]
    mod.client = _client  # type: ignore[attr-defined]

    botocore_config = types.ModuleType("botocore.config")
    botocore_config.Config = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    botocore = types.ModuleType("botocore")
    botocore.config = botocore_config  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "boto3", mod)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config)

    yield types.SimpleNamespace(
        lambda_client=lambda_client,
        s3_client=s3_client,
        session=session,
        module=mod,
    )


def _lambda_payload(body: dict, *, function_error: str | None = None) -> dict:
    """Build a fake ``lambda.invoke`` response envelope."""
    payload = io.BytesIO(json.dumps({"statusCode": 200, "body": body}).encode())
    resp: dict = {"Payload": payload}
    if function_error:
        resp["FunctionError"] = function_error
    return resp


# ---------------------------------------------------------------------------
# supports() — date window AND credentials
# ---------------------------------------------------------------------------

def test_supports_recent_date_with_credentials(fake_boto3):
    assert aws_crop.supports("20240121") is True


def test_supports_rejects_pre_2021(fake_boto3):
    # Date window fails before credentials even matter.
    assert aws_crop.supports("20190101") is False


def test_supports_false_without_credentials(fake_boto3):
    fake_boto3.session.get_credentials.return_value = None
    assert aws_crop.supports("20240121") is False


# ---------------------------------------------------------------------------
# have_credentials()
# ---------------------------------------------------------------------------

def test_have_credentials_true_when_session_returns_creds(fake_boto3):
    assert aws_crop.have_credentials() is True


def test_have_credentials_false_when_session_returns_none(fake_boto3):
    fake_boto3.session.get_credentials.return_value = None
    assert aws_crop.have_credentials() is False


def test_have_credentials_false_without_boto3(monkeypatch):
    """No boto3 installed → False, never raises."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    # Force the lazy import to see the None entry and fail.
    import importlib
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def bad_import(name, *a, **kw):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", bad_import)
    assert aws_crop.have_credentials() is False


# ---------------------------------------------------------------------------
# Quota gate routing
# ---------------------------------------------------------------------------

def test_local_crop_env_raises_source_unavailable(tmp_quota, monkeypatch, fake_boto3):
    monkeypatch.setenv("SHARKTOPUS_LOCAL_CROP", "true")
    with pytest.raises(SourceUnavailable, match="LOCAL_CROP"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota)


def test_free_tier_exhausted_raises(tmp_quota, fake_boto3):
    state = aws_quota.load_quota("aws")
    state.invocations = aws_quota.AWS_FREE_INVOCATIONS
    aws_quota.save_quota(state)
    with pytest.raises(SourceUnavailable, match="free tier|ACCEPT_CHARGES"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota)


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def test_payload_includes_bbox_and_vars(tmp_quota, fake_boto3):
    payload = aws_crop._build_payload(
        "20240121", "00", 6,
        bbox=(-45, -40, -25, -20),
        pad_lon=0.5, pad_lat=0.5,
        variables=["TMP", "UGRD"],
        levels=["500 mb"],
        product="pgrb2.0p25",
        response_mode="auto",
        s3_bucket=None,
        s3_expires_s=3600,
    )
    assert payload["date"] == "20240121"
    assert payload["cycle"] == "00"
    assert payload["fxx"] == 6
    assert payload["variables"] == ["TMP", "UGRD"]
    assert payload["levels"] == ["500 mb"]
    # bbox was expanded by pad and sent as lon_w/lon_e/lat_s/lat_n.
    bbox = payload["bbox"]
    assert bbox["lon_w"] < -45 and bbox["lon_e"] > -40
    assert bbox["lat_s"] < -25 and bbox["lat_n"] > -20


def test_payload_omits_bbox_when_none(tmp_quota, fake_boto3):
    payload = aws_crop._build_payload(
        "20240121", "00", 0,
        bbox=None, pad_lon=0.0, pad_lat=0.0,
        variables=None, levels=None,
        product="pgrb2.0p25", response_mode="auto",
        s3_bucket=None, s3_expires_s=3600,
    )
    assert "bbox" not in payload
    assert "variables" not in payload


def test_payload_rejects_bad_cycle():
    with pytest.raises(ValueError):
        aws_crop._build_payload(
            "20240121", "99", 6,
            bbox=None, pad_lon=0.0, pad_lat=0.0,
            variables=None, levels=None,
            product="pgrb2.0p25", response_mode="auto",
            s3_bucket=None, s3_expires_s=3600,
        )


# ---------------------------------------------------------------------------
# Inline response mode
# ---------------------------------------------------------------------------

def test_inline_response_writes_file(tmp_quota, fake_boto3):
    """Inline base64 payload is decoded straight to the final path."""
    content = b"GRIB" + b"\x00" * 96 + b"..."  # 100 bytes, fake GRIB2 header
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload({
        "mode": "inline",
        "b64": base64.b64encode(content).decode("ascii"),
        "billed_duration_ms": 800,
        "memory_mb": 512,
    })

    out = aws_crop.fetch_step(
        "20240121", "00", 6,
        dest=tmp_quota,
        bbox=(-45, -40, -25, -20),
        verify=False,  # fake GRIB bytes won't parse
    )
    assert out.read_bytes() == content
    assert out.name == "gfs.t00z.pgrb2.0p25.f006"

    # One invocation recorded against the quota.
    state = aws_quota.load_quota("aws")
    assert state.invocations == 1
    # billed_duration_ms=800 → 0.8s → 512 MB × 0.8 s / 1024 = 0.4 GB-s
    assert state.gb_seconds == pytest.approx(0.4, rel=1e-3)


def test_inline_missing_b64_raises(tmp_quota, fake_boto3):
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload({
        "mode": "inline",
        # no b64 field
    })
    with pytest.raises(SourceUnavailable, match="inline.*b64"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)


# ---------------------------------------------------------------------------
# S3 response mode
# ---------------------------------------------------------------------------

def test_s3_response_downloads_and_deletes(tmp_quota, fake_boto3, monkeypatch):
    """S3 mode: fetch via presigned URL, then delete the object."""
    payload_bytes = b"GRIB" + b"\x00" * 1024
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload({
        "mode": "s3",
        "s3_url": "https://bucket.s3.amazonaws.com/signed?X=Y",
        "s3_bucket": "bucket",
        "s3_key": "prefix/crop_abc.grib2",
        "billed_duration_ms": 1500,
        "memory_mb": 512,
    })

    def fake_stream_download(url, dst, **_kw):
        Path(dst).write_bytes(payload_bytes)
        return Path(dst)

    monkeypatch.setattr(aws_crop, "stream_download", fake_stream_download)

    out = aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)
    assert out.read_bytes() == payload_bytes
    # Object deleted after successful download.
    fake_boto3.s3_client.delete_object.assert_called_once_with(
        Bucket="bucket", Key="prefix/crop_abc.grib2"
    )


def test_s3_retain_env_skips_delete(tmp_quota, fake_boto3, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_RETAIN_S3", "true")
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload({
        "mode": "s3",
        "s3_url": "https://bucket.s3.amazonaws.com/signed",
        "s3_bucket": "bucket", "s3_key": "prefix/x.grib2",
    })
    monkeypatch.setattr(
        aws_crop, "stream_download",
        lambda url, dst, **_kw: Path(dst).write_bytes(b"GRIB\x00") or Path(dst),
    )

    aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)
    fake_boto3.s3_client.delete_object.assert_not_called()


def test_unknown_response_mode_raises(tmp_quota, fake_boto3):
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload({"mode": "bogus"})
    with pytest.raises(SourceUnavailable, match="unknown response mode"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_lambda_function_error_raises(tmp_quota, fake_boto3):
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload(
        {"error": "wgrib2 crashed"},
        function_error="Unhandled",
    )
    with pytest.raises(SourceUnavailable, match="wgrib2 crashed"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)


def test_invoke_exception_raises_source_unavailable(tmp_quota, fake_boto3):
    fake_boto3.lambda_client.invoke.side_effect = RuntimeError("network blew up")
    with pytest.raises(SourceUnavailable, match="lambda invoke failed"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)


def test_non_json_payload_raises(tmp_quota, fake_boto3):
    fake_boto3.lambda_client.invoke.return_value = {
        "Payload": io.BytesIO(b"not-json"),
    }
    with pytest.raises(SourceUnavailable, match="non-JSON"):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)


# ---------------------------------------------------------------------------
# Quota bookkeeping on error
# ---------------------------------------------------------------------------

def test_failed_invocation_still_records_billed_time(tmp_quota, fake_boto3):
    """A failed crop still costs money — counter must tick."""
    fake_boto3.lambda_client.invoke.return_value = _lambda_payload(
        {"error": "no data", "billed_duration_ms": 250, "memory_mb": 512},
        function_error="Unhandled",
    )
    with pytest.raises(SourceUnavailable):
        aws_crop.fetch_step("20240121", "00", 6, dest=tmp_quota, verify=False)

    state = aws_quota.load_quota("aws")
    assert state.invocations == 1


# ---------------------------------------------------------------------------
# Billed-duration log parsing
# ---------------------------------------------------------------------------

def test_parse_billed_duration_from_log_tail():
    log = (
        "START RequestId: abc Version: $LATEST\n"
        "END RequestId: abc\n"
        "REPORT RequestId: abc\tDuration: 812.34 ms\t"
        "Billed Duration: 813 ms\tMemory Size: 512 MB\t"
        "Max Memory Used: 300 MB\n"
    )
    tail_b64 = base64.b64encode(log.encode()).decode()
    assert aws_crop._parse_billed_duration_ms(tail_b64) == 813.0


def test_parse_billed_duration_missing_returns_none():
    tail_b64 = base64.b64encode(b"nothing interesting").decode()
    assert aws_crop._parse_billed_duration_ms(tail_b64) is None
