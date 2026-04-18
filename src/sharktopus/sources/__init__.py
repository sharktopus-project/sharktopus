"""Layer 1 — sources of GFS forecast data.

Each source module exposes ``fetch_step(date, cycle, fxx, *, dest, ...)``
returning the :class:`~pathlib.Path` of the produced GRIB2 file. Sources
raise :class:`SourceUnavailable` when the step is not retrievable from
this particular mirror (404, too old for retention window, mirror down).
The orchestrator (layer 2) catches that and falls back to the next source.

Five sources use the same recipe — download the full public GRIB2 and
optionally crop locally with wgrib2:

- :mod:`sharktopus.sources.nomads` — NOAA NOMADS (~10 day retention).
- :mod:`sharktopus.sources.aws` — AWS Open Data bucket ``noaa-gfs-bdp-pds``.
- :mod:`sharktopus.sources.gcloud` — GCS bucket ``global-forecast-system``.
- :mod:`sharktopus.sources.azure` — Azure Blob ``noaagfs/gfs``.
- :mod:`sharktopus.sources.rda` — NCAR RDA ``ds084.1`` (long-term archive).

One source takes a different approach — server-side subsetting:

- :mod:`sharktopus.sources.nomads_filter` — NOMADS ``filter_gfs_0p25.pl``
  asks the server to return only the requested variables/levels/window.
"""

from .base import SourceUnavailable, canonical_filename

__all__ = ["SourceUnavailable", "canonical_filename"]
