"""sharktopus — download and crop GFS forecast data.

Layers available in this release:

- :mod:`sharktopus.grib` — wgrib2 / .idx utilities (pure, no network).
- :mod:`sharktopus.sources` — mirror-specific downloaders.
  Currently: ``nomads`` (full-file) and ``nomads_filter`` (server-side subset).
"""

__version__ = "0.1.0"

from . import grib, sources

__all__ = ["grib", "sources", "__version__"]
