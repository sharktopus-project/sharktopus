# Roadmap — six-layer build

Extracted from CONVECT's `PLANO_BIBLIOTECA_SHARKTOPUS.md`.

The package is built bottom-up. Each layer is validated on a **canonical test
case** (2024-01-21 00Z, fxx=6, bbox=(-45,-40,-25,-20) — same case used by the
CONVECT radar-DA pipeline) before the next layer starts.

```
┌──────────────────────────────────────────────────────────────┐
│ 5. CLI & Menu interactive    (sharktopus.cli)                │
├──────────────────────────────────────────────────────────────┤
│ 4. Serverless deploy  [extra] (sharktopus.deploy)            │
├──────────────────────────────────────────────────────────────┤
│ 3. Serverless invoke  [extra] (sharktopus.cloud)             │
├──────────────────────────────────────────────────────────────┤
│ 2. Orchestrator fetch        (sharktopus.fetch)              │
├──────────────────────────────────────────────────────────────┤
│ 1. Local sources             (sharktopus.sources.*)          │
├──────────────────────────────────────────────────────────────┤
│ 0. wgrib2 + .idx utilities   (sharktopus.grib)
└──────────────────────────────────────────────────────────────┘
              ← Layers 0–2 + 5 all done; Layer 3/4 next
```

## Layer 0 — `sharktopus.grib` — DONE (v0.0.1)

Six pure utilities consolidated from CONVECT's five download scripts. See
`docs/ORIGIN.md`.

## Layer 1 — `sharktopus.sources.*` — DONE (v0.1.0 + unreleased)

Source modules, each exposing:

```python
def fetch_step(date, cycle, fxx, *, dest=None, bbox=None, ...) -> Path: ...
```

All six sources implemented and tested:

1. ✅ `nomads` — full-file download from `nomads.ncep.noaa.gov`
2. ✅ `nomads_filter` — server-side subset via `filter_gfs_0p25.pl`
3. ✅ `aws` — full-file from `noaa-gfs-bdp-pds` (anonymous HTTPS)
4. ✅ `gcloud` — full-file from `global-forecast-system` (anonymous HTTPS)
5. ✅ `azure` — full-file from `noaagfs.blob.core.windows.net` (anonymous HTTPS)
6. ✅ `rda` — full-file from NCAR `ds084.1` (optional cookie auth)

Strategy: full-GRIB download + local `wgrib2 -small_grib` crop. Byte-range
support remains available through `grib.byte_ranges` / `parse_idx` for
callers who need it.

## Layer 2 — `sharktopus.batch` — DONE (unreleased)

Top-level orchestrator `fetch_batch(timestamps, lat_s/n/w/e, priority=[...], ...)`.
Iterates cycles × fxx, falls back across sources on `SourceUnavailable`,
parallelizes with `ThreadPoolExecutor` sized to the minimum
`DEFAULT_MAX_WORKERS` across the priority list (anti-throttle).

## Layer 3 — `sharktopus.cloud` (extra `[cloud]`)

Invokes the serverless recortadores deployed in Layer 4. Reads endpoint
URLs from `~/.sharktopus/config.json` (written by `deploy.setup`).

## Layer 4 — `sharktopus.deploy` (extra `[cloud]`)

Ported from CONVECT's `orchestration/deploy/{aws,gcloud,azure,common}.py`.
`setup("aws"|"gcloud"|"azure")` creates all resources and saves config.

## Layer 5 — `sharktopus.cli` — DONE (unreleased)

`sharktopus` entry point mirrors CONVECT's `download_batch_cli.py` flag
names, reads INI config via `sharktopus.config`, and runs everything
through `fetch_batch`.
