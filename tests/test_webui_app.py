"""End-to-end route tests via FastAPI TestClient.

Skipped entirely when FastAPI (or httpx) isn't installed so the rest
of the suite stays green on a bare clone.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
jinja2 = pytest.importorskip("jinja2")

from fastapi.testclient import TestClient  # noqa: E402  (after importorskip)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(tmp_path))
    from sharktopus.webui.app import build_app
    app = build_app()
    with TestClient(app) as c:
        yield c


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "sharktopus" in r.text.lower()
    assert "dashboard" in r.text.lower()


def test_nav_links_200(client):
    for path in (
        "/submit", "/jobs", "/inventory", "/quota",
        "/sources", "/setup", "/credentials", "/settings", "/help",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"


def test_submit_form_validation_errors(client):
    # missing bbox, missing start/end → should re-render with errors
    r = client.post("/submit", data={"mode": "range"})
    assert r.status_code == 200
    assert "Fix these before submitting" in r.text or "error" in r.text.lower()


def test_api_sources_json(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    data = r.json()
    names = {row["name"] for row in data}
    assert {"nomads", "aws", "gcloud"} <= names


def test_api_availability(client):
    r = client.get("/api/availability/20240102")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "20240102"
    assert isinstance(body["sources"], list)


def test_inventory_scan_endpoint_runs(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "gfs.20240102" / "00").mkdir(parents=True)
    (data / "gfs.20240102" / "00" / "gfs.t00z.pgrb2.0p25.f000").write_bytes(b"x" * 32)
    monkeypatch.setenv("SHARKTOPUS_DATA", str(data))

    r = client.post("/api/inventory/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["added"] >= 1


def test_submit_happy_path_queues_job(client, monkeypatch):
    # Stub the runner so we don't spawn a thread that would try to
    # actually download GFS data.
    from sharktopus.webui import runner
    calls: list[int] = []

    class StubRunner:
        def submit(self, form):
            calls.append(1)
            # pretend the DB inserted id=1
            return 1

    monkeypatch.setattr(runner, "get_runner", lambda: StubRunner())
    # pages.py imports at module scope; patch there too.
    from sharktopus.webui.routes import pages as pages_mod
    monkeypatch.setattr(pages_mod, "get_runner", lambda: StubRunner())

    r = client.post(
        "/submit",
        data={
            "mode": "list",
            "timestamps": "2024010200",
            "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
            "ext": "6", "interval": "3",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/jobs/1"
    assert calls == [1]


def test_static_assets_mounted(client):
    for path in (
        "/static/css/app.css",
        "/static/js/htmx.min.js",
        "/static/img/mark.png",
        "/static/img/institutions/ieapm.png",
        "/static/img/institutions/cnpq.png",
        "/static/img/institutions/uenf.png",
        "/static/img/institutions/ufpr.svg",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"


def test_catalog_endpoint(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200
    data = r.json()
    assert "variables" in data and "level_groups" in data
    assert any(v["name"] == "TMP" for v in data["variables"])
    tmp = next(v for v in data["variables"] if v["name"] == "TMP")
    assert "500 mb" in tmp["levels"]
    assert "surface" in tmp["levels"]
    soilw = next(v for v in data["variables"] if v["name"] == "SOILW")
    assert all("below ground" in lv for lv in soilw["levels"])


def test_presets_crud(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    assert r.json() == []

    r = client.post("/api/presets", json={
        "name": "tmp-500",
        "description": "just TMP@500",
        "variables": ["TMP"],
        "levels": ["500 mb"],
    })
    assert r.status_code == 200
    saved = r.json()
    assert saved["id"] > 0
    assert saved["variables"] == ["TMP"]

    r = client.get("/api/presets")
    assert len(r.json()) == 1

    r = client.post("/api/presets", json={
        "name": "tmp-500",
        "variables": ["TMP", "HGT"],
        "levels": ["500 mb"],
    })
    assert r.status_code == 200
    assert r.json()["variables"] == ["TMP", "HGT"]

    r = client.delete(f"/api/presets/{saved['id']}")
    assert r.status_code == 200
    r = client.get("/api/presets")
    assert r.json() == []


def test_presets_validation(client):
    r = client.post("/api/presets", json={"name": ""})
    assert r.status_code == 400
    r = client.post("/api/presets", json={
        "name": "empty", "variables": [], "levels": [],
    })
    assert r.status_code == 400
    r = client.delete("/api/presets/9999")
    assert r.status_code == 404
