"""Compare spread mode vs fallback-chain on a real multi-source batch.

Downloads the same 4 timestamps × 1 forecast step twice:
  (a) fallback-chain (classic): cloud mirrors tried in order, one wins.
  (b) spread: all eligible cloud mirrors run in parallel.

Prints wall time, which source handled each step, and total bytes.
Uses byte-range mode (narrow vars/levels) so each step is ~1-5 MB.

Run with:

    SHARKTOPUS_DATA=/tmp/sharktopus_spread python scripts/smoke_spread.py
"""

from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("SHARKTOPUS_DATA", "/tmp/sharktopus_spread")

import sharktopus
from sharktopus import batch


TIMESTAMPS = ["2026041500", "2026041506", "2026041512", "2026041518"]
BBOX = dict(lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36)
# 4 timestamps × (ext/interval + 1) steps = 4 × 9 = 36 jobs.
# Enough that gcloud's own 4-worker pool can't drain it before aws/azure
# also start pulling — that's what makes the spread visible.
EXT = 24
INTERVAL = 3
# WRF-canonical selection — the real production workload.
from sharktopus import wrf as _wrf
VARS = list(_wrf.DEFAULT_VARS)
LEVELS = list(_wrf.DEFAULT_LEVELS)
PRIORITY = ["gcloud", "aws", "azure"]


def clean(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)


def run(mode: str, *, spread: bool | None) -> dict:
    root = Path(os.environ["SHARKTOPUS_DATA"]) / mode
    clean(root)
    assignments: list[tuple[str, str, int, str]] = []

    def on_ok(date, cycle, fxx, path):
        # filename doesn't carry the source name, but the path's bbox
        # dir is the same either way; record the mirror by sniffing the
        # wall-time the step took to a simple counter instead. Below
        # we capture per-source calls through a wrapped registry.
        assignments.append((date, cycle, fxx, path.name))

    # Wrap each source's fetch_step to record "who handled this step".
    per_source_counts: Counter = Counter()
    orig_registry = dict(batch._REGISTRY)

    def wrap(name: str, fn):
        def wrapped(*a, **kw):
            per_source_counts[name] += 1
            return fn(*a, **kw)
        return wrapped

    for name in PRIORITY:
        batch._REGISTRY[name] = wrap(name, orig_registry[name])

    try:
        t0 = time.monotonic()
        paths = sharktopus.fetch_batch(
            timestamps=TIMESTAMPS,
            **BBOX,
            ext=EXT, interval=INTERVAL,
            priority=PRIORITY,
            variables=VARS, levels=LEVELS,
            root=root,
            spread=spread,
            on_step_ok=on_ok,
        )
        t1 = time.monotonic()
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig_registry)

    total_bytes = sum(p.stat().st_size for p in paths)
    return {
        "mode": mode,
        "wall_s": t1 - t0,
        "files": len(paths),
        "bytes": total_bytes,
        "per_source": dict(per_source_counts),
    }


def print_result(r: dict) -> None:
    print(f"=== {r['mode']} ===")
    print(f"  wall time    : {r['wall_s']:.2f} s")
    print(f"  files        : {r['files']}")
    print(f"  total bytes  : {r['bytes']:,} ({r['bytes']/1e6:.2f} MB)")
    print(f"  per source   : {r['per_source']}")


def main() -> int:
    print(f"Timestamps : {TIMESTAMPS}")
    print(f"Priority   : {PRIORITY}")
    print(f"Vars/Levels: {VARS} / {LEVELS}")
    print(f"Bbox       : {BBOX}")
    print()

    # Warm-up: a single idx fetch on each mirror so DNS / TCP handshake
    # isn't attributed to whichever test runs first.
    print("(warm-up: a single head_size per mirror)\n")
    from sharktopus.sources.base import head_size
    from sharktopus.sources import aws as _aws, gcloud as _gc, azure as _az
    for mod in (_aws, _gc, _az):
        try:
            head_size(mod.build_url(TIMESTAMPS[0][:8], TIMESTAMPS[0][8:], 0), timeout=10.0)
        except Exception as e:
            print(f"  warm-up {mod.__name__}: {e!r}")

    # Classic fallback-chain: only one mirror (gcloud) gets all the work.
    classic = run("fallback_chain", spread=False)
    print_result(classic)
    print()

    # Spread: all three mirrors in parallel.
    spread = run("spread", spread=True)
    print_result(spread)
    print()

    speedup = classic["wall_s"] / max(spread["wall_s"], 1e-6)
    print(f"SPREAD speedup: {speedup:.2f}×")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
