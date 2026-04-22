"""SQLite schema + connection helpers for the web UI.

Four tables:

* ``jobs`` — one row per submitted batch. The form payload is stored
  as JSON so the Submit form can re-open a job as a template.
* ``job_steps`` — one row per ``(date, cycle, fxx)`` the orchestrator
  emits. Populated incrementally via ``on_step_ok`` / ``on_step_fail``.
* ``job_logs`` — freeform stderr-style log lines scoped to a job.
* ``inventory`` — what files are on disk. Populated by an async scan
  that crawls ``~/.cache/sharktopus/gfs/`` (or ``SHARKTOPUS_DATA``) and
  records size, bbox, variables, levels.
* ``quota_snapshots`` — periodic polls of each cloud provider's quota
  so the Quota page can draw a 30-day usage line.

All writes go through a module-level lock so concurrent requests from
the job runner thread and the HTTP handlers don't step on each other.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

from . import paths

__all__ = [
    "connect",
    "init_schema",
    "transaction",
]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    status           TEXT NOT NULL CHECK (status IN (
                         'queued','running','succeeded','failed','cancelled'
                     )),
    form_json        TEXT NOT NULL,
    priority         TEXT,
    steps_total      INTEGER DEFAULT 0,
    steps_done       INTEGER DEFAULT 0,
    steps_failed     INTEGER DEFAULT 0,
    bytes_downloaded INTEGER DEFAULT 0,
    started_at       TEXT,
    finished_at      TEXT,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS job_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    cycle      TEXT NOT NULL,
    fxx        INTEGER NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('ok','failed')),
    source     TEXT,
    path       TEXT,
    error      TEXT,
    finished_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_steps_job ON job_steps(job_id);

CREATE TABLE IF NOT EXISTS job_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    ts         TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);

CREATE TABLE IF NOT EXISTS inventory (
    path        TEXT PRIMARY KEY,
    date        TEXT,
    cycle       TEXT,
    fxx         INTEGER,
    source      TEXT,
    size_bytes  INTEGER,
    lon_w       REAL,
    lon_e       REAL,
    lat_s       REAL,
    lat_n       REAL,
    variables   TEXT,
    levels      TEXT,
    mtime       TEXT,
    scanned_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inventory_date ON inventory(date, cycle);

CREATE TABLE IF NOT EXISTS quota_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    provider       TEXT NOT NULL,
    invocations    INTEGER,
    seconds_used   REAL,
    pct_free_tier  REAL,
    charges_allowed INTEGER,
    ts             TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_quota_provider_ts ON quota_snapshots(provider, ts DESC);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT,
    variables     TEXT NOT NULL,
    levels        TEXT NOT NULL,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_presets_name ON presets(name);
"""


_LOCK = threading.RLock()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults applied."""
    target = Path(path) if path is not None else paths.db_path()
    conn = sqlite3.connect(target, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create tables if missing. Safe to call on every boot."""
    owned = conn is None
    if owned:
        conn = connect()
    try:
        with _LOCK:
            conn.executescript(_SCHEMA)
    finally:
        if owned:
            conn.close()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager that wraps a SQLite transaction under the module lock."""
    owned = conn is None
    if owned:
        conn = connect()
    try:
        with _LOCK:
            conn.execute("BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        if owned:
            conn.close()
