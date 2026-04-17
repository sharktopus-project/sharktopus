"""Layer 1 — sources of GFS forecast data.

Each source module exposes ``fetch_step(date, cycle, fxx, *, dest, ...)``
returning the :class:`~pathlib.Path` of the produced GRIB2 file. Sources
raise :class:`SourceUnavailable` when the step is not retrievable from
this particular mirror (404, too old for retention window, mirror down).
The orchestrator (layer 2) catches that and falls back to the next source.

Available sources:

- :mod:`sharktopus.sources.nomads` — NOMADS direct full-file download.
- :mod:`sharktopus.sources.nomads_filter` — NOMADS ``filter_gfs_0p25.pl``
  with server-side variable/level/subregion cropping.
"""

from .base import SourceUnavailable, canonical_filename

__all__ = ["SourceUnavailable", "canonical_filename"]
