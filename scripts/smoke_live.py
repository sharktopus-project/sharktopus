"""Verbose end-to-end demo — imports, CLI, per-source, availability.

Run with:

    SHARKTOPUS_DATA=/tmp/sharktopus_live python scripts/smoke_live.py

What it exercises (in order):

1. Imports — mirrors the three call patterns from README.
2. CLI — subprocess invocations of ``sharktopus --list-sources`` and
   ``sharktopus --availability ...`` (no network).
3. Per-source ``fetch_step`` — downloads + crops one analysis step from
   every registered source, printing URL, sizes, crop bbox and the
   first 3 records of the resulting GRIB2 file (via wgrib2 -s).
4. Availability — calls ``batch.available_sources()`` at five different
   historical dates so you can see the priority auto-select in action.

Each phase is separately timed and printed under a header so it's
obvious where each request lands.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("SHARKTOPUS_DATA", "/tmp/sharktopus_live")

import sharktopus
from sharktopus import batch, wrf
from sharktopus._wgrib2 import resolve_wgrib2
from sharktopus.grib import verify
from sharktopus.sources import aws, azure, gcloud, nomads, nomads_filter, rda
from sharktopus.sources.base import SourceUnavailable


# Recent cycle within the NOMADS 10-day window. Adjust if running this
# script more than ~10 days after the commit date.
DATE = "20260417"
CYCLE = "00"
FXX = 0
BBOX = (-45, -40, -25, -20)  # lon_w, lon_e, lat_s, lat_n — small Brazil box


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def sub(title: str) -> None:
    print()
    print(f"--- {title} ---")


def first_records(path: Path, n: int = 3) -> str:
    """Return the first *n* lines of ``wgrib2 -s <path>`` for display."""
    exe = resolve_wgrib2()
    if not exe:
        return "(wgrib2 not resolvable, skipping record listing)"
    try:
        out = subprocess.check_output([exe, "-s", str(path)], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        return f"(wgrib2 failed: {e})"
    lines = out.strip().splitlines()
    return "\n".join(lines[:n]) + (f"\n… ({len(lines) - n} more records)" if len(lines) > n else "")


# ---------------------------------------------------------------------------
# Phase 1 — imports & library surface
# ---------------------------------------------------------------------------

def phase_imports() -> None:
    header("1. Imports & library surface")

    print(f"sharktopus version   : {sharktopus.__version__}")
    print(f"wgrib2 resolved to   : {resolve_wgrib2()}")
    print(f"registered sources   : {batch.registered_sources()}")
    print(f"DEFAULT_PRIORITY     : {list(batch.DEFAULT_PRIORITY)}")
    print(f"WRF DEFAULT_VARS     : {list(wrf.DEFAULT_VARS)}")
    print(f"WRF DEFAULT_LEVELS   : {len(wrf.DEFAULT_LEVELS)} levels")
    print(f"  first 5            : {list(wrf.DEFAULT_LEVELS[:5])}")
    print(f"  last 5             : {list(wrf.DEFAULT_LEVELS[-5:])}")
    print()
    print("Per-source ceilings (DEFAULT_MAX_WORKERS):")
    for name in batch.registered_sources():
        print(f"  {name:15s} -> {batch.source_default_workers(name)}")


# ---------------------------------------------------------------------------
# Phase 2 — CLI (no network)
# ---------------------------------------------------------------------------

def phase_cli() -> None:
    header("2. CLI (no network)")

    sub("sharktopus --help (first 15 lines)")
    out = subprocess.check_output(
        [sys.executable, "-m", "sharktopus.cli", "--help"], text=True
    )
    print("\n".join(out.splitlines()[:15]))

    sub("sharktopus --list-sources")
    subprocess.run([sys.executable, "-m", "sharktopus.cli", "--list-sources"], check=True)

    for d in ("20260417", "20250101", "20180601", "20100101"):
        sub(f"sharktopus --availability {d}")
        subprocess.run(
            [sys.executable, "-m", "sharktopus.cli", "--availability", d],
            check=True,
        )


# ---------------------------------------------------------------------------
# Phase 3 — per-source fetch_step (one analysis step per source)
# ---------------------------------------------------------------------------

def run_source(name: str, source, **kwargs) -> None:
    sub(f"{name}: fetch_step({DATE}, {CYCLE!r}, {FXX}, bbox={BBOX})")
    try:
        url = source.build_url(DATE, CYCLE, FXX, **{k: v for k, v in kwargs.items() if k == "variables" or k == "levels" or k == "bbox" or k == "pad_lon" or k == "pad_lat"})
    except TypeError:
        try:
            url = source.build_url(DATE, CYCLE, FXX)
        except Exception as e:
            url = f"(build_url failed: {e})"
    except Exception as e:
        url = f"(build_url failed: {e})"
    print(f"URL          : {url}")

    t0 = time.time()
    try:
        path = source.fetch_step(DATE, CYCLE, FXX, bbox=BBOX, **kwargs)
    except SourceUnavailable as exc:
        print(f"SKIP         : {exc}")
        return
    except Exception:
        print("ERROR        :")
        traceback.print_exc()
        return
    dt = time.time() - t0

    size_mb = path.stat().st_size / 1e6
    n = verify(path)
    print(f"Output file  : {path}")
    print(f"Size         : {size_mb:.2f} MB")
    print(f"Records      : {n}")
    print(f"Elapsed      : {dt:.1f} s")
    print("First 3 recs :")
    print(textwrap.indent(first_records(path, n=3), "  "))


def phase_per_source() -> None:
    header("3. Per-source fetch_step (download + local crop)")

    run_source("nomads", nomads)
    # nomads_filter with the *WRF-canonical* defaults so you see a full file
    run_source(
        "nomads_filter (WRF defaults)",
        nomads_filter,
        variables=list(wrf.DEFAULT_VARS),
        levels=list(wrf.DEFAULT_LEVELS),
    )
    # Demonstrate the narrow-subset form too
    run_source(
        "nomads_filter (narrow: TMP@500,850 only)",
        nomads_filter,
        variables=["TMP"],
        levels=["500 mb", "850 mb"],
    )
    run_source("aws", aws)
    run_source("gcloud", gcloud)
    run_source("azure", azure)
    run_source("rda", rda)


# ---------------------------------------------------------------------------
# Phase 4 — availability at 5 historical dates
# ---------------------------------------------------------------------------

def phase_availability() -> None:
    header("4. Availability API at different dates")

    now = datetime.now(tz=timezone.utc)
    for d, label in [
        (DATE, "yesterday (within NOMADS window)"),
        ("20260101", "~3 months back (NOMADS expired, clouds OK)"),
        ("20220101", "4 years back (cloud mirrors + RDA)"),
        ("20170615", "pre-cloud-mirror (RDA only)"),
        ("20100101", "pre-RDA (no source has it)"),
    ]:
        avail = batch.available_sources(d, now=now)
        print(f"  {d}  {label}")
        print(f"    -> {avail}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"sharktopus verbose smoke — {DATE} {CYCLE}Z f{FXX:03d}  bbox={BBOX}")
    print(f"Output root: {os.environ['SHARKTOPUS_DATA']}")
    t0 = time.time()

    phase_imports()
    phase_cli()
    phase_per_source()
    phase_availability()

    print()
    print("=" * 72)
    print(f"Total wall time: {time.time() - t0:.1f} s")
    print("=" * 72)


if __name__ == "__main__":
    main()
