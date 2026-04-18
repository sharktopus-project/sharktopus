"""sharktopus — download and crop GFS forecast data.

Layers available in this release:

- :mod:`sharktopus.grib` — wgrib2 / .idx utilities (pure, no network).
- :mod:`sharktopus.sources` — mirror-specific downloaders.
  Full-file mirrors: ``nomads``, ``aws``, ``gcloud``, ``azure``, ``rda``.
  Server-side subset: ``nomads_filter``.
- :mod:`sharktopus.batch` — iterate cycles × steps, falling back across
  sources by priority. :func:`fetch_batch` and :func:`generate_timestamps`
  are re-exported at the top level.
- :mod:`sharktopus.wrf` — canonical WRF-ready variable / level set
  used as the default by :func:`fetch_batch` when ``nomads_filter`` is
  in priority and the caller doesn't pass explicit lists.
- :mod:`sharktopus.config` — INI config loader (``[gfs]`` section).
- :mod:`sharktopus.cli` — ``sharktopus`` command-line entry point.
"""

__version__ = "0.1.0"

from . import batch, config, grib, paths, sources, wrf
from .batch import available_sources, fetch_batch, generate_timestamps

__all__ = [
    "available_sources",
    "batch",
    "config",
    "fetch_batch",
    "generate_timestamps",
    "grib",
    "paths",
    "sources",
    "wrf",
    "__version__",
]
