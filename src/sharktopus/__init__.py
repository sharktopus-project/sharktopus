"""sharktopus — download and crop GFS forecast data.

Layer 0 (this release): pure wgrib2 / .idx utilities. See `sharktopus.grib`.
"""

__version__ = "0.0.1"

from . import grib

__all__ = ["grib", "__version__"]
