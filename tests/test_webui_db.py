"""Tests for the web UI's SQLite schema + transaction helper.

These run without FastAPI — the DB layer is pure stdlib.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _setup_cache(tmp_path: Path) -> None:
    os.environ["SHARKTOPUS_CACHE_HOME"] = str(tmp_path)


def test_init_schema_creates_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(tmp_path))
    # Reimport to pick up the new path.
    from sharktopus.webui import db, paths
    db.init_schema()

    assert paths.db_path().exists()
    with db.transaction() as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"jobs", "job_steps", "job_logs", "inventory", "quota_snapshots", "settings"} <= tables


def test_init_schema_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(tmp_path))
    from sharktopus.webui import db
    db.init_schema()
    db.init_schema()  # no error on second call
    with db.transaction() as conn:
        # Insert + read to verify the jobs schema round-trips.
        conn.execute(
            "INSERT INTO jobs (name, status, form_json) VALUES (?, 'queued', '{}')",
            ("hello",),
        )
        row = conn.execute("SELECT name, status FROM jobs").fetchone()
    assert row["name"] == "hello"
    assert row["status"] == "queued"


def test_transaction_rolls_back_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(tmp_path))
    from sharktopus.webui import db
    db.init_schema()
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO jobs (name, status, form_json) VALUES (?, 'queued', '{}')",
                ("a",),
            )
            raise RuntimeError("boom")
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    assert count == 0
