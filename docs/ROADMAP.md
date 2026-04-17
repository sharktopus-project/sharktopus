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
│ 0. wgrib2 + .idx utilities   (sharktopus.grib)    ← we are here
└──────────────────────────────────────────────────────────────┘
```

## Layer 0 — `sharktopus.grib` — DONE (v0.0.1)

Six pure utilities consolidated from CONVECT's five download scripts. See
`docs/ORIGIN.md`.

## Layer 1 — `sharktopus.sources.*` — IN PROGRESS (v0.1.0)

Source modules, each exposing:

```python
def fetch_step(date, cycle, fxx, *, dest, bbox=None, ...) -> Path: ...
```

Port order (2/6 done):

1. ✅ `nomads` — full-file download from `nomads.ncep.noaa.gov`
2. ✅ `nomads_filter` — server-side subset via `filter_gfs_0p25.pl`
3. ⬜ `aws_s3` — byte-range from `noaa-gfs-bdp-pds` (boto3)
4. ⬜ `gcloud_storage` — byte-range from `global-forecast-system` (HTTPS public)
5. ⬜ `azure_blob` — byte-range from `noaagfs.blob.core.windows.net` (HTTPS public)
6. ⬜ `rda` — NCAR RDA authenticated download (0.25° and 1°)

## Layer 2 — `sharktopus.fetch`

Top-level orchestrator. Accepts `priority=[...]`, iterates `fxx`, falls back
between sources on `SourceUnavailable`, parallelizes with
`ThreadPoolExecutor`.

## Layer 3 — `sharktopus.cloud` (extra `[cloud]`)

Invokes the serverless recortadores deployed in Layer 4. Reads endpoint
URLs from `~/.sharktopus/config.json` (written by `deploy.setup`).

## Layer 4 — `sharktopus.deploy` (extra `[cloud]`)

Ported from CONVECT's `orchestration/deploy/{aws,gcloud,azure,common}.py`.
`setup("aws"|"gcloud"|"azure")` creates all resources and saves config.

## Layer 5 — `sharktopus.cli`

Port of CONVECT's `menu_gfs.py` as an entry point `sharktopus`.
