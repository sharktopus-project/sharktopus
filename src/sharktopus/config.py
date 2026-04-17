"""Load batch-download parameters from an INI-style config file.

File format (single ``[gfs]`` section, all keys optional unless the
caller needs them)::

    [gfs]
    # One of these two date modes is required:
    timestamps = 2024010200, 2024010206
    #  — or —
    start = 2024010200
    end   = 2024010318
    step  = 6                       # cycle step in hours, multiples of 6

    ext      = 24                   # forecast horizon (hours)
    interval = 3                    # step interval within each cycle (hours)

    lat_s = -28                     # required for cropped downloads
    lat_n = -18
    lon_w = -48
    lon_e = -36

    priority = nomads_filter, nomads
    variables = TMP, UGRD, VGRD, HGT          # nomads_filter only
    levels    = 500 mb, 850 mb, surface       # nomads_filter only

    dest = /scratch/run                       # overrides the default convention
    root = /data/gfs                          # overrides ~/.cache/sharktopus

Keys mirror CONVECT's ``download_batch_cli.py`` flag names, with dashes
swapped for underscores. List values are comma-separated; whitespace is
stripped. Numeric keys are coerced; unknown keys raise ``ConfigError``
so typos surface immediately instead of being silently ignored.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

__all__ = ["ConfigError", "load_config", "BatchConfig"]


_INT_KEYS = ("step", "ext", "interval")
_FLOAT_KEYS = ("lat_s", "lat_n", "lon_w", "lon_e", "pad_lon", "pad_lat")
_LIST_KEYS = ("timestamps", "priority", "variables", "levels")
_STR_KEYS = ("start", "end", "dest", "root", "product")

_ALL_KEYS = _INT_KEYS + _FLOAT_KEYS + _LIST_KEYS + _STR_KEYS


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed, or has unknown keys."""


BatchConfig = dict[str, Any]


def _split_list(value: str) -> list[str]:
    # Accept comma OR whitespace separation (so "500 mb, 850 mb" works AND
    # "nomads_filter nomads" works). Comma wins: split on commas first,
    # then strip whitespace around each element. If no commas, fall back
    # to whitespace-split for convenience with single-token lists.
    if "," in value:
        items = [p.strip() for p in value.split(",")]
    else:
        items = value.split()
    return [x for x in items if x]


def load_config(path: str | Path) -> BatchConfig:
    """Parse *path* into a dict ready to pass into
    :func:`sharktopus.batch.fetch_batch` (via ``**cfg``).

    Missing optional keys are omitted from the result — don't default
    them here; let the caller / CLI decide defaults so one place owns
    the policy.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")

    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error as e:
        raise ConfigError(f"failed to parse {path}: {e}") from e

    if "gfs" not in parser:
        raise ConfigError(f"{path}: missing required [gfs] section")

    section = parser["gfs"]
    unknown = set(section.keys()) - set(_ALL_KEYS)
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) in [gfs]: {sorted(unknown)}. "
            f"Known keys: {sorted(_ALL_KEYS)}"
        )

    out: BatchConfig = {}
    for key in _INT_KEYS:
        if key in section:
            try:
                out[key] = int(section[key])
            except ValueError as e:
                raise ConfigError(f"{path}: {key}={section[key]!r} is not an int") from e
    for key in _FLOAT_KEYS:
        if key in section:
            try:
                out[key] = float(section[key])
            except ValueError as e:
                raise ConfigError(f"{path}: {key}={section[key]!r} is not a float") from e
    for key in _LIST_KEYS:
        if key in section:
            out[key] = _split_list(section[key])
    for key in _STR_KEYS:
        if key in section:
            out[key] = section[key].strip()

    return out
