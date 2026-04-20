"""Canonical WRF-ready GFS variable / level set.

The :data:`DEFAULT_VARS` + :data:`DEFAULT_LEVELS` lists are the minimum
set of GFS 0.25° fields required for WPS / ungrib / metgrid to produce
a WRF boundary-condition dataset without missing-variable warnings. They
mirror the constants used in CONVECT's five original fetcher scripts
(``download_{nomades,nomads_filter,aws,gcloud,azure}_gfs_0p25.py``).

Treat these as *defaults*, not *limits*. ``fetch_batch`` and the
:mod:`sharktopus.sources.nomads_filter` helper use them when the caller
doesn't pass ``variables=`` / ``levels=``. Pass your own lists when you
want a narrower (e.g. just TMP @ 500 mb for quick tests) or entirely
different (e.g. radiation fluxes for a radiative study) subset — the
library does not assume WRF anywhere else.
"""

from __future__ import annotations

__all__ = ["DEFAULT_VARS", "DEFAULT_LEVELS"]


# The 13 surface/soil/atmospheric fields WPS ungrib's ``Vtable.GFS`` maps
# into ``met_em`` variables. Dropping any one of these triggers a hard
# metgrid failure or a silently-degraded initial condition.
DEFAULT_VARS: tuple[str, ...] = (
    "HGT",     # Geopotential height (isobaric + surface + mean sea level)
    "LAND",    # Land-sea mask (binary, needed for soil init)
    "MSLET",   # Eta-model reduction mean sea level pressure
    "PRES",    # Pressure (surface + soil layers)
    "PRMSL",   # Mean sea level pressure
    "RH",      # Relative humidity (isobaric)
    "SOILL",   # Liquid volumetric soil moisture (4 soil layers)
    "SOILW",   # Volumetric soil moisture (4 soil layers)
    "SPFH",   # Specific humidity (isobaric + 2 m above ground)
    "TMP",     # Temperature (isobaric + surface + 2 m + soil)
    "TSOIL",   # Soil temperature (4 soil layers)
    "UGRD",    # Zonal wind (isobaric + 10 m above ground)
    "VGRD",    # Meridional wind (isobaric + 10 m above ground)
)


# 49 level names that CONVECT's production fetchers request. Covers the
# full 1000→0.01 mb isobaric column, four soil layers, plus surface /
# 2 m / 10 m / mean sea level diagnostics. Strings use the human-readable
# wgrib2 form (``"500 mb"``, ``"2 m above ground"``) — see
# :func:`sharktopus.sources.nomads_filter.level_to_param` for the NOMADS
# query-param translation.
DEFAULT_LEVELS: tuple[str, ...] = (
    # Soil layers
    "0-0.1 m below ground",
    "0.1-0.4 m below ground",
    "0.4-1 m below ground",
    "1-2 m below ground",
    # Stratospheric isobaric column
    "0.01 mb", "0.02 mb", "0.04 mb", "0.07 mb",
    "0.1 mb", "0.2 mb", "0.4 mb", "0.7 mb",
    "1 mb", "2 mb", "3 mb", "5 mb", "7 mb",
    "10 mb", "15 mb", "20 mb", "30 mb", "40 mb", "50 mb",
    "70 mb", "100 mb", "150 mb", "200 mb",
    # Tropospheric isobaric column
    "250 mb", "300 mb", "350 mb",
    "400 mb", "450 mb", "500 mb", "550 mb",
    "600 mb", "650 mb", "700 mb", "750 mb",
    "800 mb", "850 mb", "900 mb",
    "925 mb", "950 mb", "975 mb", "1000 mb",
    # Near-surface diagnostics
    "2 m above ground",
    "10 m above ground",
    "mean sea level",
    "surface",
)
