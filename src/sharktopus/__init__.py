"""sharktopus — download and crop GFS forecast data.

Layered architecture (each subpackage has its own ``__init__`` doc):

* :mod:`sharktopus.io` — low-level primitives: GRIB2 parsing (``grib``),
  filesystem layout (``paths``), wgrib2 resolver (``wgrib2``), user
  config loader (``config``).
* :mod:`sharktopus.sources` — mirror-specific downloaders.
  Full-file mirrors: ``nomads``, ``aws``, ``gcloud``, ``azure``, ``rda``.
  Server-side subset: ``nomads_filter``. Cloud-side crop: ``aws_crop``.
* :mod:`sharktopus.cloud` — cloud-provider policy gates (Lambda
  quota tracking, paid-usage opt-in).
* :mod:`sharktopus.batch` — iterate cycles × steps, falling back across
  sources by priority. :func:`fetch_batch` and :func:`generate_timestamps`
  are re-exported at the top level.
* :mod:`sharktopus.wrf` — canonical WRF-ready variable / level set
  used as the default when ``nomads_filter`` is in priority.
* :mod:`sharktopus.cli` — ``sharktopus`` command-line entry point.
"""

__version__ = "0.1.7rc1"

from . import batch, cloud, io, sources, wrf
from .batch import available_sources, fetch_batch, generate_timestamps
from .cloud import quota_report

__all__ = [
    "__version__",
    "available_sources",
    "batch",
    "cloud",
    "fetch_batch",
    "generate_timestamps",
    "io",
    "quota_report",
    "sources",
    "wrf",
]
