"""Smoke test the deployed `sharktopus` Lambda end-to-end.

Invokes via ``sharktopus.sources.aws_crop.fetch_step`` with a small
bbox (Macae region), so the response should come back inline
(base64 in Lambda response payload, <4 MB).

Run with::

    AWS_PROFILE=pop-cli-user python scripts/smoke_aws_crop.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SHARKTOPUS_LOCAL_CROP", "")
# Allow the Lambda path under the free-tier quota gate.
os.environ.setdefault("SHARKTOPUS_MAX_SPEND_USD", "0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sharktopus.io import grib
from sharktopus.sources import aws_crop


BBOX = (-43.5, -41.0, -23.5, -22.0)  # lon_w, lon_e, lat_s, lat_n (Macae)
DATE = os.environ.get("SMOKE_DATE", "20260419")
CYCLE = os.environ.get("SMOKE_CYCLE", "00")
FXX = int(os.environ.get("SMOKE_FXX", "0"))
DEST = Path(os.environ.get("SMOKE_DEST", "/tmp/sharktopus_smoke"))


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"[smoke] date={DATE} cycle={CYCLE}Z f{FXX:03d} bbox={BBOX}")
    print(f"[smoke] dest={DEST}")

    t0 = time.monotonic()
    try:
        out = aws_crop.fetch_step(
            DATE, CYCLE, FXX,
            dest=DEST,
            bbox=BBOX,
            variables=["TMP", "UGRD", "VGRD"],
            levels=["surface", "10 m above ground", "2 m above ground"],
            response_mode="auto",
            verify=True,
        )
    except Exception as e:
        print(f"[smoke] FAILED: {type(e).__name__}: {e}")
        return 1
    elapsed = time.monotonic() - t0

    size = out.stat().st_size
    print(f"[smoke] OK  path={out}")
    print(f"[smoke]     size={size} bytes  elapsed={elapsed:.1f}s")

    try:
        n = grib.verify(out)
        print(f"[smoke]     wgrib2 records={n}")
    except Exception as e:
        print(f"[smoke] wgrib2 verify failed: {e}")
        return 2

    qpath = Path.home() / ".cache/sharktopus/quota.json"
    if qpath.exists():
        print(f"[smoke] quota file: {qpath}")
        print(qpath.read_text())
    else:
        print("[smoke] no quota file (unexpected)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
