"""Per-product variable / level catalog — bundled JSON + optional wgrib2 refresh.

The catalog backs the Submit form's cascade picker: user selects a
variable, the UI narrows levels to those that variable actually appears
at. Not every (var, level) pair is valid — e.g. soil moisture only has
soil layers, ozone only strato isobaric.

Catalogs are per-product. They live at
``data/products/<catalog_file>.json`` (bundled) with an optional
override at ``~/.cache/sharktopus/products/<catalog_file>.json`` that
lets power users refresh without touching the install.

The initial GFS 0.25° catalog was derived from a full ``pgrb2.0p25``
inventory via ``scripts/generate_gfs_catalog.py``. See
``docs/ADDING_A_PRODUCT.md`` for adding a new product.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import paths
from . import products as _products

__all__ = [
    "Variable",
    "LevelGroup",
    "Catalog",
    "load_catalog",
    "refresh_from_grib",
]


@dataclass
class Variable:
    name: str
    desc: str
    unit: str
    category: str
    levels: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "desc": self.desc,
            "unit": self.unit,
            "category": self.category,
            "levels": list(self.levels),
        }


@dataclass
class LevelGroup:
    name: str
    levels: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"name": self.name, "levels": list(self.levels)}


@dataclass
class Catalog:
    version: str
    product: str
    source: str
    note: str = ""
    variables: tuple[Variable, ...] = field(default_factory=tuple)
    level_groups: tuple[LevelGroup, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "product": self.product,
            "source": self.source,
            "note": self.note,
            "level_groups": [g.as_dict() for g in self.level_groups],
            "variables": [v.as_dict() for v in self.variables],
        }

    def variable(self, name: str) -> Variable | None:
        for v in self.variables:
            if v.name == name:
                return v
        return None

    def categories(self) -> list[str]:
        seen: list[str] = []
        for v in self.variables:
            if v.category and v.category not in seen:
                seen.append(v.category)
        return seen


def _bundled_path(catalog_file: str) -> Path:
    return _products.products_dir() / catalog_file


def _override_path(catalog_file: str) -> Path:
    """User-editable catalog override in the cache dir.

    If present, takes precedence over the bundled catalog. Lets users
    refresh the catalog without touching the package install.
    """
    return paths.cache_root() / "products" / catalog_file


def _decode(payload: dict) -> Catalog:
    variables = tuple(
        Variable(
            name=v["name"],
            desc=v.get("desc", ""),
            unit=v.get("unit", ""),
            category=v.get("category", ""),
            levels=tuple(v.get("levels", [])),
        )
        for v in payload.get("variables", [])
    )
    level_groups = tuple(
        LevelGroup(name=g["name"], levels=tuple(g.get("levels", [])))
        for g in payload.get("level_groups", [])
    )
    return Catalog(
        version=payload.get("version", ""),
        product=payload.get("product", "pgrb2.0p25"),
        source=payload.get("source", ""),
        note=payload.get("note", ""),
        variables=variables,
        level_groups=level_groups,
    )


def load_catalog(product_id: str | None = None) -> Catalog:
    """Return the effective catalog for *product_id* (default if None).

    User override in ``~/.cache/sharktopus/products/<file>.json`` wins
    over the bundled copy if present.
    """
    product = _products.get_product(product_id)
    override = _override_path(product.catalog_file)
    if override.is_file():
        try:
            with override.open("r", encoding="utf-8") as f:
                return _decode(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    with _bundled_path(product.catalog_file).open("r", encoding="utf-8") as f:
        return _decode(json.load(f))


# ------------------------------------------------------------ refresh helper

_ISOBARIC_GROUP_TROPO = {
    "1000 mb", "975 mb", "950 mb", "925 mb", "900 mb", "850 mb", "800 mb",
    "750 mb", "700 mb", "650 mb", "600 mb", "550 mb", "500 mb", "450 mb",
    "400 mb", "350 mb", "300 mb", "250 mb", "200 mb", "150 mb", "100 mb",
}

_ISOBARIC_GROUP_STRATO = {
    "70 mb", "50 mb", "40 mb", "30 mb", "20 mb", "15 mb", "10 mb", "7 mb",
    "5 mb", "3 mb", "2 mb", "1 mb", "0.7 mb", "0.4 mb", "0.2 mb", "0.1 mb",
    "0.07 mb", "0.04 mb", "0.02 mb", "0.01 mb",
}

_SOIL_GROUP = {
    "0-0.1 m below ground", "0.1-0.4 m below ground",
    "0.4-1 m below ground", "1-2 m below ground",
}

_NEAR_SURFACE = {
    "surface", "mean sea level", "2 m above ground", "10 m above ground",
    "80 m above ground", "100 m above ground", "0.995 sigma level",
    "planetary boundary layer",
}


def _categorize_level(level: str) -> str:
    if level in _ISOBARIC_GROUP_TROPO:
        return "Isobaric — troposphere"
    if level in _ISOBARIC_GROUP_STRATO:
        return "Isobaric — stratosphere"
    if level in _SOIL_GROUP:
        return "Soil"
    if level in _NEAR_SURFACE:
        return "Near-surface"
    return "Other"


def _infer_category(var: str) -> str:
    if var in ("TMP", "APTMP", "DPT", "TMAX", "TMIN"):
        return "Thermodynamic"
    if var in ("UGRD", "VGRD", "WIND", "GUST", "VVEL", "DZDT", "ABSV", "VWSH",
               "USTM", "VSTM"):
        return "Wind"
    if var in ("RH", "SPFH", "PWAT"):
        return "Moisture"
    if var in ("HGT", "PRES", "PRMSL", "MSLET"):
        return "Mass"
    if var in ("TSOIL", "SOILW", "SOILL"):
        return "Soil"
    if var in ("APCP", "ACPCP", "NCPCP", "PRATE", "CPRAT", "WEASD",
               "CSNOW", "CICEP", "CFRZR", "CRAIN"):
        return "Precipitation"
    if var in ("TCDC", "LCDC", "MCDC", "HCDC", "CDCON", "CLWMR", "ICMR",
               "RWMR", "SNMR", "GRLE", "CWAT", "CWORK"):
        return "Cloud"
    if var in ("DSWRF", "USWRF", "DLWRF", "ULWRF", "SHTFL", "LHTFL", "GFLUX"):
        return "Radiation"
    if var in ("CAPE", "CIN", "HLCY", "LFTX", "4LFTX", "MXUPHL", "MNUPHL"):
        return "Severe weather"
    if var in ("REFC", "REFD", "MAXREF"):
        return "Radar"
    if var in ("O3MR", "TOZNE"):
        return "Atmospheric chemistry"
    if var in ("HPBL", "FRICV"):
        return "Boundary layer"
    return "Surface"


def refresh_from_grib(
    grib_path: Path,
    wgrib2: str | Path | None = None,
    product_id: str | None = None,
) -> Catalog:
    """Build a catalog by running ``wgrib2 -s`` on *grib_path*.

    The resulting catalog is returned and also written to the override
    for *product_id* (defaults to the first registered product) so later
    :func:`load_catalog` calls pick it up automatically.
    """
    product = _products.get_product(product_id)
    from ..io.wgrib2 import ensure_wgrib2

    binary = ensure_wgrib2(str(wgrib2) if wgrib2 else None)
    proc = subprocess.run(
        [str(binary), "-s", str(grib_path)],
        check=True, capture_output=True, text=True,
    )
    pairs: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        var = parts[3].strip()
        level = parts[4].strip()
        if not var or not level:
            continue
        pairs.setdefault(var, [])
        if level not in pairs[var]:
            pairs[var].append(level)

    variables = tuple(
        Variable(name=v, desc="", unit="", category=_infer_category(v),
                 levels=tuple(lv))
        for v, lv in sorted(pairs.items())
    )

    all_levels: list[str] = []
    for lv in pairs.values():
        for level in lv:
            if level not in all_levels:
                all_levels.append(level)
    grouped: dict[str, list[str]] = {}
    for level in all_levels:
        g = _categorize_level(level)
        grouped.setdefault(g, []).append(level)
    group_order = [
        "Near-surface", "Isobaric — troposphere", "Isobaric — stratosphere",
        "Soil", "Other",
    ]
    level_groups = tuple(
        LevelGroup(name=g, levels=tuple(grouped[g]))
        for g in group_order if grouped.get(g)
    )

    from datetime import datetime, timezone
    cat = Catalog(
        version=datetime.now(timezone.utc).date().isoformat(),
        product=product.code,
        source=f"wgrib2 -s {grib_path.name}",
        note=f"Regenerated locally for product {product.id}.",
        variables=variables,
        level_groups=level_groups,
    )
    override = _override_path(product.catalog_file)
    override.parent.mkdir(parents=True, exist_ok=True)
    with override.open("w", encoding="utf-8") as f:
        json.dump(cat.as_dict(), f, ensure_ascii=False, indent=2)
    return cat


def filter_valid_pairs(
    variables: Iterable[str], levels: Iterable[str], catalog: Catalog | None = None
) -> list[tuple[str, str]]:
    """Return only (var, level) pairs that exist in *catalog*.

    Useful for validating user selections before building a NOMADS URL.
    """
    cat = catalog or load_catalog()
    var_levels = {v.name: set(v.levels) for v in cat.variables}
    var_list = list(variables)
    lvl_list = list(levels)
    valid: list[tuple[str, str]] = []
    for v in var_list:
        lvls = var_levels.get(v, set())
        for lv in lvl_list:
            if lv in lvls:
                valid.append((v, lv))
    return valid
