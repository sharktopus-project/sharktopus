"""Walk the on-disk cache and record one row per GRIB2 file.

Kept dependency-free so it can run inside the FastAPI process without
pulling in cfgrib. bbox / variables / levels stay NULL unless the user
runs the deeper scan (``/api/inventory/scan?deep=1``) which *does*
open each file with ``io.grib``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..io import paths as io_paths
from . import db

__all__ = ["scan", "default_roots"]


_GRIB_EXT = (".grib2", ".grb2", ".grib", ".grb")
_GFS_NAME_RE = re.compile(
    r"gfs\.t(?P<cycle>\d{2})z\.(?:pgrb2|pgrb2b)\.\d+p\d+\.f(?P<fxx>\d{2,3})"
)
_NAME_RE = re.compile(r"gfs\.(?P<date>\d{8})/(?P<cycle>\d{2})/.*f(?P<fxx>\d{2,3})")


def _looks_like_grib(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(_GRIB_EXT):
        return True
    return bool(_GFS_NAME_RE.search(path.name))


def default_roots() -> list[Path]:
    """Cache directories that are worth scanning.

    Honors ``SHARKTOPUS_DATA`` (single root), else ``~/.cache/sharktopus``.
    """
    roots: list[Path] = []
    override = os.environ.get("SHARKTOPUS_DATA")
    if override:
        roots.append(Path(override).expanduser())
    roots.append(io_paths.default_root())
    seen = set()
    out: list[Path] = []
    for r in roots:
        key = str(r.resolve())
        if key not in seen and r.exists():
            seen.add(key)
            out.append(r)
    return out


def _iter_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                full = Path(dirpath) / fn
                if _looks_like_grib(full):
                    yield full


def _parse_path(path: Path) -> tuple[str | None, str | None, int | None]:
    m = _NAME_RE.search(str(path))
    if not m:
        return None, None, None
    return m.group("date"), m.group("cycle"), int(m.group("fxx"))


def scan(roots: Iterable[Path] | None = None) -> dict[str, int]:
    """Refresh the inventory table. Returns counts (added, updated, removed)."""
    if roots is None:
        roots = default_roots()
    seen_paths: set[str] = set()
    added = updated = 0

    with db.transaction() as conn:
        existing = {
            row["path"]: row["mtime"]
            for row in conn.execute("SELECT path, mtime FROM inventory")
        }

        for path in _iter_files(list(roots)):
            p = str(path)
            seen_paths.add(p)
            try:
                st = path.stat()
            except OSError:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
                timespec="seconds"
            )
            date, cycle, fxx = _parse_path(path)
            if p in existing:
                if existing[p] == mtime:
                    continue
                conn.execute(
                    "UPDATE inventory SET size_bytes=?, mtime=?, scanned_at=?,"
                    " date=COALESCE(date, ?), cycle=COALESCE(cycle, ?),"
                    " fxx=COALESCE(fxx, ?) WHERE path=?",
                    (st.st_size, mtime, datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ), date, cycle, fxx, p),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO inventory (path, date, cycle, fxx, size_bytes,"
                    " mtime) VALUES (?, ?, ?, ?, ?, ?)",
                    (p, date, cycle, fxx, st.st_size, mtime),
                )
                added += 1

        stale = set(existing) - seen_paths
        for p in stale:
            conn.execute("DELETE FROM inventory WHERE path=?", (p,))

    return {"added": added, "updated": updated, "removed": len(stale)}
