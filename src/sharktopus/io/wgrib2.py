"""Locate the ``wgrib2`` binary used by :mod:`sharktopus.grib`.

Resolution order (first hit wins):

1. The *explicit* argument passed to any ``grib.*`` function (kept for
   tests that want to exercise a specific binary).
2. The ``SHARKTOPUS_WGRIB2`` environment variable.
3. A bundled binary at ``sharktopus/_bin/wgrib2`` — present in the
   platform-specific wheels we publish (see ``scripts/bundle_wgrib2.sh``).
4. ``shutil.which("wgrib2")`` on ``$PATH``.

If none resolve, :func:`ensure_wgrib2` raises :class:`WgribNotFoundError`
with a platform-appropriate install hint.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "BUNDLED_BIN_DIR",
    "WgribNotFoundError",
    "bundled_wgrib2",
    "ensure_wgrib2",
    "resolve_wgrib2",
]


BUNDLED_BIN_DIR = Path(__file__).resolve().parent.parent / "_bin"


class WgribNotFoundError(RuntimeError):
    """Raised when no wgrib2 binary can be located."""


def bundled_wgrib2() -> Path | None:
    """Path to the wgrib2 binary shipped inside the wheel, if present.

    Returns ``None`` when the wheel is the pure-Python sdist build (no
    bundled binary) or when the file is present but not executable.
    """
    exe = BUNDLED_BIN_DIR / ("wgrib2.exe" if sys.platform == "win32" else "wgrib2")
    if not exe.is_file():
        return None
    if not os.access(exe, os.X_OK):
        try:
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            return None
    return exe


@lru_cache(maxsize=None)
def _which_cached(name: str) -> str | None:
    return shutil.which(name)


def resolve_wgrib2(explicit: str | os.PathLike | None = None) -> str | None:
    """Return the path to a usable wgrib2, or ``None`` if unavailable.

    *explicit* short-circuits the lookup when callers want to pin a
    specific binary (e.g. in tests).
    """
    if explicit:
        exp = Path(explicit)
        # Path-like input (absolute or contains a separator): must exist.
        if exp.is_absolute() or os.sep in str(explicit) or "/" in str(explicit):
            return str(exp) if exp.is_file() else None
        # Bare name: look it up on PATH.
        found = _which_cached(str(explicit))
        if found:
            return found
        return None
    env = os.environ.get("SHARKTOPUS_WGRIB2")
    if env and Path(env).is_file():
        return env
    bundled = bundled_wgrib2()
    if bundled is not None:
        return str(bundled)
    return _which_cached("wgrib2")


def ensure_wgrib2(explicit: str | os.PathLike | None = None) -> str:
    """Like :func:`resolve_wgrib2`, but raises a helpful error on miss."""
    found = resolve_wgrib2(explicit)
    if found:
        return found
    raise WgribNotFoundError(
        "wgrib2 not found. sharktopus's platform wheels bundle it; "
        "if you installed from source, either install wgrib2 separately "
        "(`conda install -c conda-forge wgrib2` or `apt install wgrib2`) "
        "or point $SHARKTOPUS_WGRIB2 at an existing binary."
    )
