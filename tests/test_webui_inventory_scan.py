"""Tests for the filesystem → SQLite inventory scanner."""
from __future__ import annotations

from pathlib import Path

import pytest


def _touch(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_scan_indexes_new_files(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(cache))
    monkeypatch.setenv("SHARKTOPUS_DATA", str(data))

    from sharktopus.webui import db, inventory_scan
    db.init_schema()

    # CONVECT-style layout: gfs.YYYYMMDD/HH/...fFFF
    _touch(data / "gfs.20240102" / "00" / "gfs.t00z.pgrb2.0p25.f024", 2048)
    _touch(data / "gfs.20240102" / "06" / "gfs.t06z.pgrb2.0p25.f003", 1024)
    _touch(data / "README.txt", 12)  # not a grib, ignored

    result = inventory_scan.scan()
    assert result["added"] == 2
    assert result["updated"] == 0

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT date, cycle, fxx, size_bytes FROM inventory ORDER BY fxx"
        ).fetchall()
    assert [(r["date"], r["cycle"], r["fxx"]) for r in rows] == [
        ("20240102", "06", 3),
        ("20240102", "00", 24),
    ]


def test_scan_removes_missing_files(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    monkeypatch.setenv("SHARKTOPUS_CACHE_HOME", str(cache))
    monkeypatch.setenv("SHARKTOPUS_DATA", str(data))

    from sharktopus.webui import db, inventory_scan
    db.init_schema()

    f = _touch(data / "gfs.20240101" / "00" / "gfs.t00z.pgrb2.0p25.f000", 512)
    inventory_scan.scan()

    f.unlink()
    result = inventory_scan.scan()
    assert result["removed"] == 1

    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM inventory").fetchone()["n"]
    assert count == 0
