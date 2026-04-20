"""Smoke the canonical WRF-input payload against both cloud crop endpoints.

Sends the 13-variable × 48-level set from :mod:`sharktopus.wrf` to AWS
Lambda (``aws_crop``) and GCloud Cloud Run (``gcloud_crop``). This is
the payload the CONVECT pipeline actually uses for WRF IC/BC — roughly
243 GRIB2 records when intersected with each variable's applicable
levels. Fills the gap between the tiny-filter and fully-unfiltered
smokes that already exist for each provider.

Run with::

    AWS_PROFILE=pop-cli-user python scripts/smoke_wrf_canonical.py

Environment knobs (optional)::

    SMOKE_DATE=20260419   SMOKE_CYCLE=00   SMOKE_FXX=6
    SMOKE_BBOX="-43.5,-41.0,-23.5,-22.0"   # lon_w,lon_e,lat_s,lat_n
    SMOKE_PROVIDERS="aws,gcloud"           # subset which to hit
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sharktopus.io import grib
from sharktopus.wrf import DEFAULT_LEVELS, DEFAULT_VARS


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    a, b, c, d = (float(x) for x in raw.split(","))
    return a, b, c, d


DATE = os.environ.get("SMOKE_DATE", "20260419")
CYCLE = os.environ.get("SMOKE_CYCLE", "00")
FXX = int(os.environ.get("SMOKE_FXX", "6"))
BBOX = _parse_bbox(os.environ.get("SMOKE_BBOX", "-43.5,-41.0,-23.5,-22.0"))
PROVIDERS = tuple(
    p.strip() for p in os.environ.get("SMOKE_PROVIDERS", "aws,gcloud").split(",") if p.strip()
)
DEST = Path(os.environ.get("SMOKE_DEST", "/tmp/sharktopus_smoke_wrf"))


def smoke(provider: str) -> int:
    mod_name = f"{provider}_crop"
    print(f"\n=== {mod_name} ===")
    print(f"    date={DATE} cycle={CYCLE}Z f{FXX:03d} bbox={BBOX}")
    print(f"    vars  ({len(DEFAULT_VARS)}): {', '.join(DEFAULT_VARS)}")
    print(f"    levels({len(DEFAULT_LEVELS)}): {DEFAULT_LEVELS[0]!r} ... {DEFAULT_LEVELS[-1]!r}")

    from importlib import import_module
    try:
        mod = import_module(f"sharktopus.sources.{mod_name}")
    except Exception as e:
        print(f"    [SKIP] cannot import: {type(e).__name__}: {e}")
        return 0

    sub_dest = DEST / provider
    sub_dest.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        out = mod.fetch_step(
            DATE, CYCLE, FXX,
            dest=sub_dest,
            bbox=BBOX,
            variables=list(DEFAULT_VARS),
            levels=list(DEFAULT_LEVELS),
            response_mode="auto",
            verify=True,
        )
    except Exception as e:
        print(f"    [FAIL] {type(e).__name__}: {e}")
        return 1
    elapsed = time.monotonic() - t0

    size = out.stat().st_size
    try:
        n = grib.verify(out)
    except Exception as e:
        print(f"    [FAIL] wgrib2 verify: {e}")
        return 2

    print(f"    [OK] {out.name}")
    print(f"         size={size:,} bytes  records={n}  elapsed={elapsed:.1f}s")
    return 0


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    rcs = [smoke(p) for p in PROVIDERS]
    return max(rcs) if rcs else 0


if __name__ == "__main__":
    raise SystemExit(main())
