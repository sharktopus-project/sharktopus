"""Filesystem locations used by the web UI (DB, logs, uploads)."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "cache_root",
    "db_path",
    "logs_root",
    "uploads_root",
    "package_root",
    "templates_dir",
    "static_dir",
]


def cache_root() -> Path:
    """Root directory for per-user UI state.

    Honors ``SHARKTOPUS_CACHE_HOME`` for tests; otherwise
    ``~/.cache/sharktopus/webui``.
    """
    override = os.environ.get("SHARKTOPUS_CACHE_HOME")
    if override:
        base = Path(override).expanduser().resolve()
    else:
        base = Path.home() / ".cache" / "sharktopus"
    root = base / "webui"
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return cache_root() / "webui.db"


def logs_root() -> Path:
    p = cache_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def uploads_root() -> Path:
    p = cache_root() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def package_root() -> Path:
    return Path(__file__).resolve().parent


def templates_dir() -> Path:
    return package_root() / "templates"


def static_dir() -> Path:
    return package_root() / "static"
