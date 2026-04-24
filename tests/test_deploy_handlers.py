"""Product-whitelist tests for deploy/{aws,gcloud,azure} cloud handlers.

Verifies that each handler rejects unknown product codes with HTTP 400
and lets known GFS codes through the whitelist stage. The handlers live
outside the installed package (they ship inside their respective
container images), so each test group imports them via ``sys.path`` and
the Flask-based handlers skip gracefully when ``flask`` is not installed
in the test venv.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"

GOOD_EVENT = {"date": "20260417", "cycle": "00", "fxx": 0, "product": "pgrb2.0p25"}
BAD_EVENT = {**GOOD_EVENT, "product": "nonsense"}


def _import_from(path: Path, module_name: str):
    """Import a module from *path* (a directory outside the package tree)."""
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# AWS Lambda handler
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_handler():
    pytest.importorskip("boto3")
    return _import_from(DEPLOY / "aws", "handler")


def test_aws_allowed_products_contains_canonical_gfs_codes(aws_handler):
    assert "pgrb2.0p25" in aws_handler.ALLOWED_PRODUCTS
    assert "sfluxgrbf" in aws_handler.ALLOWED_PRODUCTS
    assert "nonsense" not in aws_handler.ALLOWED_PRODUCTS


def test_aws_rejects_unknown_product_with_400(aws_handler):
    resp = aws_handler.lambda_handler(BAD_EVENT, None)
    assert resp["statusCode"] == 400
    assert "not allowed" in resp["body"]["error"]
    assert resp["body"]["type"] == "ValueError"


def test_aws_known_product_passes_whitelist(aws_handler, monkeypatch):
    """Known product must reach S3 — stub it so we see the whitelist passed."""
    sentinel = RuntimeError("got past whitelist — hit stubbed boto3")

    def fake_client(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(aws_handler.boto3, "client", fake_client)
    resp = aws_handler.lambda_handler(GOOD_EVENT, None)
    assert resp["statusCode"] == 500
    assert "not allowed" not in resp["body"]["error"]
    assert "got past whitelist" in resp["body"]["error"]


# ---------------------------------------------------------------------------
# GCloud Cloud Run handler (Flask)
# ---------------------------------------------------------------------------

@pytest.fixture
def gcloud_handler():
    pytest.importorskip("flask")
    return _import_from(DEPLOY / "gcloud", "main")


def test_gcloud_allowed_products_contains_canonical_gfs_codes(gcloud_handler):
    assert "pgrb2.0p25" in gcloud_handler.ALLOWED_PRODUCTS
    assert "sfluxgrbf" in gcloud_handler.ALLOWED_PRODUCTS
    assert "nonsense" not in gcloud_handler.ALLOWED_PRODUCTS


def test_gcloud_rejects_unknown_product_with_400(gcloud_handler):
    client = gcloud_handler.app.test_client()
    resp = client.post("/", json=BAD_EVENT)
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["statusCode"] == 400
    assert "not allowed" in payload["body"]["error"]


def test_gcloud_known_product_passes_whitelist(gcloud_handler, monkeypatch):
    sentinel = RuntimeError("got past whitelist — hit stubbed urlopen")

    def fake_urlopen(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(gcloud_handler, "urlopen", fake_urlopen)
    client = gcloud_handler.app.test_client()
    resp = client.post("/", json=GOOD_EVENT)
    assert resp.status_code == 500
    payload = resp.get_json()
    assert "not allowed" not in payload["body"]["error"]


# ---------------------------------------------------------------------------
# Azure Container Apps handler (Flask)
# ---------------------------------------------------------------------------

@pytest.fixture
def azure_handler():
    pytest.importorskip("flask")
    return _import_from(DEPLOY / "azure", "main")


def test_azure_allowed_products_contains_canonical_gfs_codes(azure_handler):
    assert "pgrb2.0p25" in azure_handler.ALLOWED_PRODUCTS
    assert "sfluxgrbf" in azure_handler.ALLOWED_PRODUCTS
    assert "nonsense" not in azure_handler.ALLOWED_PRODUCTS


def test_azure_rejects_unknown_product_with_400(azure_handler):
    client = azure_handler.app.test_client()
    resp = client.post("/", json=BAD_EVENT)
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["statusCode"] == 400
    assert "not allowed" in payload["body"]["error"]


def test_azure_known_product_passes_whitelist(azure_handler, monkeypatch):
    sentinel = RuntimeError("got past whitelist — hit stubbed urlopen")

    def fake_urlopen(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(azure_handler, "urlopen", fake_urlopen)
    client = azure_handler.app.test_client()
    resp = client.post("/", json=GOOD_EVENT)
    assert resp.status_code == 500
    payload = resp.get_json()
    assert "not allowed" not in payload["body"]["error"]
