"""I/O primitives: GRIB handling, filesystem paths, wgrib2 resolution, config.

This subpackage groups every low-level concern that touches bytes on
disk or on the wire but has no knowledge of data sources or
orchestration policy. The modules here are consumed by
:mod:`sharktopus.sources` and :mod:`sharktopus.batch`, and by the CLI.

* :mod:`sharktopus.io.grib` — GRIB2 parsing, idx records, byte-range
  consolidation, wgrib2 wrappers (``crop``, ``verify``,
  ``filter_vars_levels``).
* :mod:`sharktopus.io.paths` — output directory layout.
* :mod:`sharktopus.io.wgrib2` — resolver for the wgrib2 binary
  (bundled / ``$PATH`` / explicit override).
* :mod:`sharktopus.io.config` — user config file loader.
"""
