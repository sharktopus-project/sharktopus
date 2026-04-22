#!/usr/bin/env python3
"""Regenerate the bundled GFS variable/level catalog from a wgrib2 dump.

Usage
-----

  # From an existing GRIB2 file (full GFS pgrb2 recommended, not a subset):
  python scripts/generate_gfs_catalog.py /path/to/gfs.t00z.pgrb2.0p25.f000

  # Or have it download a fresh NOMADS file and do the extraction:
  python scripts/generate_gfs_catalog.py --download

The result is written to
``src/sharktopus/webui/data/products/gfs_pgrb2_0p25.json`` (overwriting
the bundled catalog). For a live UI override without touching the
install, drop the file at
``~/.cache/sharktopus/products/gfs_pgrb2_0p25.json`` instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


def _find_wgrib2() -> str:
    bundled = Path(__file__).resolve().parent.parent / "src/sharktopus/_bin/wgrib2"
    if bundled.is_file():
        return str(bundled)
    resolved = shutil.which("wgrib2")
    if not resolved:
        sys.exit("wgrib2 not found (not on PATH and bundled binary missing).")
    return resolved


def _latest_nomads_file(dest: Path) -> Path:
    """Download the most recent available GFS 0.25° forecast file.

    Uses the NOMADS public HTTP endpoint. Falls back through 6-hour
    cycles until one answers 200.
    """
    from datetime import datetime, timedelta, timezone

    base = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
    now = datetime.now(timezone.utc)
    for hours_back in range(0, 48, 6):
        t = now - timedelta(hours=hours_back)
        cycle = (t.hour // 6) * 6
        day = t.strftime("%Y%m%d")
        path = f"{base}/gfs.{day}/{cycle:02d}/atmos/gfs.t{cycle:02d}z.pgrb2.0p25.f000"
        try:
            req = urllib.request.Request(path, method="HEAD")
            with urllib.request.urlopen(req, timeout=30):
                pass
            out = dest / f"gfs.t{cycle:02d}z.pgrb2.0p25.f000"
            print(f"downloading {path} …", file=sys.stderr)
            urllib.request.urlretrieve(path, out)
            return out
        except Exception:  # noqa: BLE001 — try next cycle
            continue
    sys.exit("No NOMADS cycle answered in the last 48 h. Try a local file.")


def _inventory(grib_path: Path, wgrib2: str) -> list[tuple[str, str]]:
    proc = subprocess.run(
        [wgrib2, "-s", str(grib_path)],
        check=True, capture_output=True, text=True,
    )
    pairs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        var, level = parts[3].strip(), parts[4].strip()
        if var and level:
            pairs.append((var, level))
    return pairs


def _infer_category(var: str) -> str:
    return {
        "TMP": "Thermodynamic", "APTMP": "Thermodynamic", "DPT": "Thermodynamic",
        "TMAX": "Thermodynamic", "TMIN": "Thermodynamic",
        "UGRD": "Wind", "VGRD": "Wind", "WIND": "Wind", "GUST": "Wind",
        "VVEL": "Wind", "DZDT": "Wind", "ABSV": "Wind", "VWSH": "Wind",
        "USTM": "Wind", "VSTM": "Wind",
        "RH": "Moisture", "SPFH": "Moisture", "PWAT": "Moisture",
        "HGT": "Mass", "PRES": "Mass", "PRMSL": "Mass", "MSLET": "Mass",
        "TSOIL": "Soil", "SOILW": "Soil", "SOILL": "Soil",
        "APCP": "Precipitation", "ACPCP": "Precipitation", "NCPCP": "Precipitation",
        "PRATE": "Precipitation", "CPRAT": "Precipitation", "WEASD": "Precipitation",
        "CSNOW": "Precipitation", "CICEP": "Precipitation",
        "CFRZR": "Precipitation", "CRAIN": "Precipitation",
        "TCDC": "Cloud", "LCDC": "Cloud", "MCDC": "Cloud", "HCDC": "Cloud",
        "CDCON": "Cloud", "CLWMR": "Cloud", "ICMR": "Cloud", "RWMR": "Cloud",
        "SNMR": "Cloud", "GRLE": "Cloud", "CWAT": "Cloud", "CWORK": "Cloud",
        "DSWRF": "Radiation", "USWRF": "Radiation", "DLWRF": "Radiation",
        "ULWRF": "Radiation", "SHTFL": "Radiation", "LHTFL": "Radiation",
        "GFLUX": "Radiation",
        "CAPE": "Severe weather", "CIN": "Severe weather", "HLCY": "Severe weather",
        "LFTX": "Severe weather", "4LFTX": "Severe weather",
        "MXUPHL": "Severe weather", "MNUPHL": "Severe weather",
        "REFC": "Radar", "REFD": "Radar", "MAXREF": "Radar",
        "O3MR": "Atmospheric chemistry", "TOZNE": "Atmospheric chemistry",
        "HPBL": "Boundary layer", "FRICV": "Boundary layer",
    }.get(var, "Surface")


_TROPO = {f"{p} mb" for p in [
    1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450,
    400, 350, 300, 250, 200, 150, 100,
]}
_STRATO = {f"{p} mb" for p in [70, 50, 40, 30, 20, 15, 10, 7, 5, 3, 2, 1]} | {
    "0.7 mb", "0.4 mb", "0.2 mb", "0.1 mb", "0.07 mb", "0.04 mb", "0.02 mb", "0.01 mb",
}
_SOIL = {
    "0-0.1 m below ground", "0.1-0.4 m below ground",
    "0.4-1 m below ground", "1-2 m below ground",
}
_NEAR = {
    "surface", "mean sea level", "2 m above ground", "10 m above ground",
    "80 m above ground", "100 m above ground", "0.995 sigma level",
    "planetary boundary layer",
}


def _level_group(level: str) -> str:
    if level in _TROPO:  return "Isobaric — troposphere"
    if level in _STRATO: return "Isobaric — stratosphere"
    if level in _SOIL:   return "Soil"
    if level in _NEAR:   return "Near-surface"
    if "cloud" in level or "tropopause" in level or "max wind" in level:
        return "Tropopause / max wind / clouds"
    if "above ground" in level or "entire atmosphere" in level or "mb above ground" in level:
        return "Atmosphere column"
    return "Other"


def build_catalog(pairs: list[tuple[str, str]], source: str) -> dict:
    from datetime import datetime, timezone

    pairs_by_var: dict[str, list[str]] = {}
    for var, level in pairs:
        pairs_by_var.setdefault(var, [])
        if level not in pairs_by_var[var]:
            pairs_by_var[var].append(level)

    variables = [
        {
            "name": v,
            "desc": "",
            "unit": "",
            "category": _infer_category(v),
            "levels": pairs_by_var[v],
        }
        for v in sorted(pairs_by_var)
    ]

    all_levels: list[str] = []
    for levels in pairs_by_var.values():
        for lv in levels:
            if lv not in all_levels:
                all_levels.append(lv)

    grouped: dict[str, list[str]] = {}
    for lv in all_levels:
        grouped.setdefault(_level_group(lv), []).append(lv)
    order = [
        "Near-surface", "Isobaric — troposphere", "Isobaric — stratosphere",
        "Soil", "Atmosphere column", "Tropopause / max wind / clouds", "Other",
    ]
    level_groups = [
        {"name": name, "levels": grouped[name]}
        for name in order if grouped.get(name)
    ]

    return {
        "version": datetime.now(timezone.utc).date().isoformat(),
        "product": "pgrb2.0p25",
        "source": source,
        "note": "Generated by scripts/generate_gfs_catalog.py",
        "level_groups": level_groups,
        "variables": variables,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "grib",
        nargs="?",
        help="Local GFS pgrb2 file. Omit when --download is set.",
    )
    ap.add_argument(
        "--download", action="store_true",
        help="Download the latest public GFS f000 from NOMADS before extracting.",
    )
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent
                    / "src/sharktopus/webui/data/products/gfs_pgrb2_0p25.json"),
        help="Output JSON path (default: bundled GFS 0.25° catalog).",
    )
    args = ap.parse_args()

    if not args.grib and not args.download:
        ap.error("either supply a GRIB path or pass --download.")

    with tempfile.TemporaryDirectory() as tmp:
        if args.download:
            grib = _latest_nomads_file(Path(tmp))
        else:
            grib = Path(args.grib).expanduser().resolve()
            if not grib.is_file():
                sys.exit(f"not a file: {grib}")

        wgrib2 = _find_wgrib2()
        print(f"inventorying {grib} with {wgrib2} …", file=sys.stderr)
        pairs = _inventory(grib, wgrib2)
        print(f"  {len(pairs)} records → {len({v for v, _ in pairs})} variables",
              file=sys.stderr)

        catalog = build_catalog(pairs, source=f"wgrib2 -s {grib.name}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    print(f"wrote {out}", file=sys.stderr)
    print(f"  {len(catalog['variables'])} vars × {sum(len(g['levels']) for g in catalog['level_groups'])} grouped levels",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
