"""sharktopus — download and crop GFS forecast data.

Layers available in this release:

- :mod:`sharktopus.grib` — wgrib2 / .idx utilities (pure, no network).
- :mod:`sharktopus.sources` — mirror-specific downloaders.
  Full-file mirrors: ``nomads``, ``aws``, ``gcloud``, ``azure``, ``rda``.
  Server-side subset: ``nomads_filter``.
- :mod:`sharktopus.batch` — iterate cycles × steps, falling back across
  sources by priority. :func:`fetch_batch` and :func:`generate_timestamps`
  are re-exported at the top level.
- :mod:`sharktopus.config` — INI config loader (``[gfs]`` section).
- :mod:`sharktopus.cli` — ``sharktopus`` command-line entry point.
"""

__version__ = "0.1.0"

from . import batch, config, grib, paths, sources
from .batch import fetch_batch, generate_timestamps

__all__ = [
    "batch",
    "config",
    "fetch_batch",
    "generate_timestamps",
    "grib",
    "paths",
    "sources",
    "__version__",
]
