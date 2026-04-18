"""Live smoke test — one fxx=0 step per source, small bbox, report outcome.

Run with:
    SHARKTOPUS_DATA=/tmp/sharktopus_live python scripts/smoke_live.py
"""
from __future__ import annotations

import os
import time
import traceback

os.environ.setdefault("SHARKTOPUS_DATA", "/tmp/sharktopus_live")

from sharktopus.sources import aws, azure, gcloud, nomads, nomads_filter, rda
from sharktopus.sources.base import SourceUnavailable
from sharktopus.grib import verify

DATE = "20260417"
CYCLE = "00"
FXX = 0
BBOX = (-45, -40, -25, -20)  # lon_w, lon_e, lat_s, lat_n — small Brazil box


def run(name, source, **kwargs):
    t0 = time.time()
    try:
        path = source.fetch_step(DATE, CYCLE, FXX, bbox=BBOX, **kwargs)
        size_mb = path.stat().st_size / 1e6
        n = verify(path)
        dt = time.time() - t0
        print(f"  {name:15s} OK    {size_mb:7.2f} MB  {n:4d} recs  {dt:6.1f}s  {path}")
    except SourceUnavailable as exc:
        print(f"  {name:15s} SKIP  (unavailable: {exc})")
    except Exception:
        print(f"  {name:15s} ERROR:")
        traceback.print_exc()


def main() -> None:
    print(f"Smoke test: {DATE} {CYCLE}Z f{FXX:03d}  bbox={BBOX}")
    print(f"Output root: {os.environ['SHARKTOPUS_DATA']}")
    print()
    run("nomads", nomads)
    run(
        "nomads_filter",
        nomads_filter,
        variables=["TMP", "HGT"],
        levels=["500 mb", "surface"],
    )
    run("aws", aws)
    run("gcloud", gcloud)
    run("azure", azure)
    run("rda", rda)


if __name__ == "__main__":
    main()
