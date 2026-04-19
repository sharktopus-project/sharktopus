"""Default output-path convention, mirroring CONVECT's fetcher layout.

Files land under::

    <root>/<mode>/<YYYYMMDDHH>/<bbox_tag>/<filename>

where:

* ``<root>`` defaults to ``~/.cache/sharktopus`` and is overridden by
  the ``$SHARKTOPUS_DATA`` environment variable or an explicit
  ``root=`` kwarg on the sources' ``fetch_step``.
* ``<mode>`` is ``"fcst"`` for forecast runs and ``"anls"`` for analyses
  — the same split CONVECT uses under ``/gfsdata/``.
* ``<YYYYMMDDHH>`` is ``date + cycle`` with no separator.
* ``<bbox_tag>`` is ``{lat_s}_{lon_w}_{lat_n}_{lon_e}`` with each coord
  formatted as ``{abs(val):.0f}{N|S|E|W}`` (e.g. ``32S_52W_13S_28W``).
  For a global (non-cropped) download the tag is ``90S_180W_90N_180E``.

The format matches CONVECT's ``download_nomads_filter.py:147`` so files
produced by either system live side-by-side without confusion.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "DEFAULT_ROOT",
    "GLOBAL_BBOX_TAG",
    "bbox_tag",
    "cycle_dir",
    "default_root",
    "output_dir",
]

# Standard XDG-style cache location. Users who share the machine with
# CONVECT can point $SHARKTOPUS_DATA at /gfsdata or /gfsdata_store to
# reuse the same tree.
DEFAULT_ROOT = Path.home() / ".cache" / "sharktopus"

GLOBAL_BBOX_TAG = "90S_180W_90N_180E"


def default_root() -> Path:
    """Return the active download root.

    Resolution order: ``$SHARKTOPUS_DATA`` → :data:`DEFAULT_ROOT`.
    """
    env = os.environ.get("SHARKTOPUS_DATA")
    if env:
        return Path(env).expanduser()
    return DEFAULT_ROOT


def _coord(val: float, axis: str) -> str:
    """Format one coordinate as ``{abs:.0f}{hemisphere}``.

    *axis* is ``"lat"`` or ``"lon"``. Matches CONVECT's ``format_coord``.
    """
    if axis not in ("lat", "lon"):
        raise ValueError(f"axis must be 'lat' or 'lon', got {axis!r}")
    v = float(val)
    if axis == "lat":
        suffix = "S" if v < 0 else "N"
    else:
        suffix = "W" if v < 0 else "E"
    return f"{abs(v):.0f}{suffix}"


def bbox_tag(bbox: tuple[float, float, float, float] | None) -> str:
    """Encode a bbox as the CONVECT-style directory name.

    *bbox* is ``(lon_w, lon_e, lat_s, lat_n)`` to match the rest of the
    library. The directory is ordered ``lat_s_lon_w_lat_n_lon_e`` —
    CONVECT's convention. ``None`` returns :data:`GLOBAL_BBOX_TAG`.
    """
    if bbox is None:
        return GLOBAL_BBOX_TAG
    lon_w, lon_e, lat_s, lat_n = bbox
    return (
        f"{_coord(lat_s, 'lat')}_{_coord(lon_w, 'lon')}_"
        f"{_coord(lat_n, 'lat')}_{_coord(lon_e, 'lon')}"
    )


def cycle_dir(date: str, cycle: str) -> str:
    """Return ``YYYYMMDDHH`` — the per-cycle directory name."""
    return f"{date}{cycle}"


def output_dir(
    *,
    date: str,
    cycle: str,
    bbox: tuple[float, float, float, float] | None = None,
    mode: str = "fcst",
    root: str | os.PathLike | None = None,
) -> Path:
    """Compute and create the output directory for one step.

    ``root`` wins over ``$SHARKTOPUS_DATA`` which wins over
    :data:`DEFAULT_ROOT`. Parent directories are created.
    """
    if mode not in ("fcst", "anls"):
        raise ValueError(f"mode must be 'fcst' or 'anls', got {mode!r}")
    base = Path(root).expanduser() if root is not None else default_root()
    out = base / mode / cycle_dir(date, cycle) / bbox_tag(bbox)
    out.mkdir(parents=True, exist_ok=True)
    return out
