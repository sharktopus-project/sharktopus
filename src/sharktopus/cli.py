"""``sharktopus`` command-line entry point.

Mirrors CONVECT's ``download_batch_cli.py`` flag names so a user
migrating from the fetcher container keeps muscle memory. Adds
``--config PATH`` to load an INI file, and ``--dest`` / ``--root`` /
``--vars`` / ``--levels`` that CONVECT did not expose.

Precedence: command-line flags > config file > hard-coded defaults.

Examples
--------

    # Everything on the command line
    sharktopus \\
        --start 2024010200 --end 2024010318 --step 6 \\
        --ext 24 --interval 3 \\
        --lat-s -28 --lat-n -18 --lon-w -48 --lon-e -36 \\
        --priority nomads_filter nomads \\
        --vars TMP UGRD VGRD HGT \\
        --levels "500 mb" "850 mb" surface

    # From a config file
    sharktopus --config my_run.ini

    # Config file + targeted overrides
    sharktopus --config my_run.ini --priority nomads
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from . import batch, config

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharktopus",
        description="Batch-download GFS steps from the best available mirror.",
    )

    parser.add_argument(
        "--config",
        help="Path to INI config file. Individual flags override its values.",
    )

    # Dates / cycles — timestamps xor range, enforced after merge.
    parser.add_argument(
        "--timestamps", nargs="+",
        help="Explicit list of YYYYMMDDHH cycles",
    )
    parser.add_argument("--start", help="First cycle YYYYMMDDHH (with --end)")
    parser.add_argument("--end", help="Last cycle YYYYMMDDHH (with --start)")
    parser.add_argument("--step", type=int, help="Cycle step in hours (default 6)")

    parser.add_argument("--ext", type=int, help="Forecast horizon in hours (default 24)")
    parser.add_argument("--interval", type=int, help="Step interval in hours (default 3)")

    parser.add_argument("--lat-s", type=float, dest="lat_s")
    parser.add_argument("--lat-n", type=float, dest="lat_n")
    parser.add_argument("--lon-w", type=float, dest="lon_w")
    parser.add_argument("--lon-e", type=float, dest="lon_e")

    parser.add_argument(
        "--priority", nargs="+",
        help=(
            "Source order. Registered names: "
            + ", ".join(batch.registered_sources())
        ),
    )
    parser.add_argument(
        "--vars", "--variables", dest="variables", nargs="+",
        help="GRIB2 variable names for nomads_filter (e.g. TMP UGRD VGRD)",
    )
    parser.add_argument(
        "--levels", nargs="+",
        help='Level names for nomads_filter (e.g. "500 mb" "850 mb" surface)',
    )
    parser.add_argument(
        "--dest", help="Explicit output directory (overrides the default convention)",
    )
    parser.add_argument(
        "--root", help="Root of the default convention (overrides $SHARKTOPUS_DATA)",
    )
    parser.add_argument("--product", help="GFS product code (default pgrb2.0p25)")
    parser.add_argument(
        "--pad-lon", type=float, dest="pad_lon",
        help="Bbox buffer in degrees (lon). Default 2°.",
    )
    parser.add_argument(
        "--pad-lat", type=float, dest="pad_lat",
        help="Bbox buffer in degrees (lat). Default 2°.",
    )

    return parser


def _merge(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Overlay non-None argparse values onto config dict (CLI wins)."""
    merged = dict(cfg)
    for key, val in vars(args).items():
        if key == "config":
            continue
        if val is not None:
            merged[key] = val
    return merged


def _build_kwargs(merged: dict[str, Any]) -> dict[str, Any]:
    """Turn the merged config into kwargs for :func:`batch.fetch_batch`.

    Resolves ``timestamps`` either from the explicit list or by expanding
    ``start`` / ``end`` / ``step``. Applies defaults for ext/interval/
    priority/step here — ``fetch_batch`` itself uses the same defaults,
    but we resolve them up-front so error messages reference the user's
    intent.
    """
    timestamps = merged.get("timestamps")
    if timestamps is None:
        start = merged.get("start")
        end = merged.get("end")
        if not start or not end:
            raise SystemExit(
                "error: supply either --timestamps OR --start AND --end "
                "(either via CLI or config)"
            )
        step = int(merged.get("step", 6))
        timestamps = batch.generate_timestamps(start, end, step)

    required = ("lat_s", "lat_n", "lon_w", "lon_e")
    missing = [k for k in required if merged.get(k) is None]
    if missing:
        raise SystemExit(f"error: missing required bbox arg(s): {missing}")

    kwargs: dict[str, Any] = {
        "timestamps": timestamps,
        "lat_s": float(merged["lat_s"]),
        "lat_n": float(merged["lat_n"]),
        "lon_w": float(merged["lon_w"]),
        "lon_e": float(merged["lon_e"]),
        "ext": int(merged.get("ext", 24)),
        "interval": int(merged.get("interval", 3)),
        "priority": tuple(merged.get("priority") or ("nomads_filter", "nomads")),
    }
    for k in ("variables", "levels", "dest", "root", "product", "pad_lon", "pad_lat"):
        if k in merged and merged[k] is not None:
            kwargs[k] = merged[k]
    return kwargs


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = {}
    if args.config:
        cfg = config.load_config(args.config)

    merged = _merge(cfg, args)
    kwargs = _build_kwargs(merged)

    print(
        f"[sharktopus] {len(kwargs['timestamps'])} cycle(s) × "
        f"{len(range(0, kwargs['ext'] + 1, kwargs['interval']))} step(s) "
        f"via {list(kwargs['priority'])}",
        file=sys.stderr,
    )
    try:
        outputs = batch.fetch_batch(**kwargs)
    except config.ConfigError as e:
        raise SystemExit(f"config error: {e}")

    for p in outputs:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
