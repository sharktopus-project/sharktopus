# sharktopus

Download and crop GFS forecast data. Local sources first (NOMADS, AWS Open Data,
Google Cloud, Azure Blob); serverless cloud recortador is an optional second
layer for free-tier distributed cropping.

> **Status (2026-04-18): pre-alpha, Layer 1 complete.** All six sources
> are ported from CONVECT and tested: `nomads`, `nomads_filter`, `aws`,
> `gcloud`, `azure`, `rda`. All full-file sources share the same
> download + local-crop recipe; `nomads_filter` does server-side subset.
> See `docs/ROADMAP.md` for the layered build plan.

## Install

```bash
# Platform wheel (Linux x86_64 today; more platforms coming) — wgrib2
# is bundled inside the wheel, no system install needed:
pip install sharktopus

# Source install — you need to bring your own wgrib2:
pip install sharktopus --no-binary sharktopus
# then one of:
#   conda install -c conda-forge wgrib2
#   apt install wgrib2
#   export SHARKTOPUS_WGRIB2=/path/to/wgrib2
```

Resolution order at runtime: explicit `wgrib2=...` argument →
`$SHARKTOPUS_WGRIB2` → bundled binary under
`site-packages/sharktopus/_bin/` → `$PATH`. A clear `WgribNotFoundError`
with install hints is raised when nothing works.

## Quick start

### Layer 0 — wgrib2 utilities

```python
from sharktopus.grib import verify, crop, filter_vars_levels, parse_idx, byte_ranges

# Count GRIB2 records (wraps `wgrib2 -s`)
n = verify("gfs.t00z.pgrb2.0p25.f006")
print(n)  # e.g. 743

# Geographic subset (wraps `wgrib2 -small_grib`)
crop("in.grib2", "out.grib2", bbox=(-45, -40, -25, -20))

# Parse a .idx file into structured records
records = parse_idx(open("in.grib2.idx").read())

# Compute consolidated HTTP Range tuples for a subset of records
ranges = byte_ranges(records, wanted={"TMP:500 mb", "UGRD:850 mb"}, total_size=524_288_000)
```

### Three ways to drive a batch

`sharktopus.fetch_batch()` is the orchestrator equivalent to CONVECT's
`download_batch()`. Three equivalent ways to invoke it:

**(a) Python import**

```python
import sharktopus

# Default — auto-select the priority from availability of the first
# timestamp (cloud mirrors for recent dates, RDA for pre-2021, etc.).
sharktopus.fetch_batch(
    timestamps=["2024010200", "2024010206"],
    lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
    ext=24, interval=3,
)

# Pin the priority when you know better
sharktopus.fetch_batch(
    timestamps=["2024010200"],
    lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
    ext=24, interval=3,
    priority=["aws", "gcloud", "nomads"],
)

# Server-side subset via nomads_filter — omitting variables / levels
# falls back to the WRF-canonical set (sharktopus.wrf.DEFAULT_*).
sharktopus.fetch_batch(
    timestamps=["2024010200"],
    lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
    ext=24, interval=3,
    priority=["nomads_filter", "nomads"],
    variables=["TMP", "UGRD", "VGRD", "HGT"],   # override defaults
    levels=["500 mb", "850 mb", "surface"],
)

# Ask the library which mirrors have a given date before running
from sharktopus import batch
batch.available_sources("20180601")
# -> ['rda']
batch.available_sources("20240101")
# -> ['gcloud', 'aws', 'azure', 'rda']
```

**(b) Command line** (CLI flags mirror CONVECT's `download_batch_cli.py`)

```bash
sharktopus \
    --start 2024010200 --end 2024010318 --step 6 \
    --ext 24 --interval 3 \
    --lat-s -28 --lat-n -18 --lon-w -48 --lon-e -36 \
    --priority nomads_filter nomads \
    --vars TMP UGRD VGRD HGT \
    --levels "500 mb" "850 mb" surface
```

**(c) INI config file**

```ini
# my_run.ini
[gfs]
start = 2024010200
end   = 2024010318
step  = 6
ext   = 24
interval = 3
lat_s = -28
lat_n = -18
lon_w = -48
lon_e = -36
priority  = nomads_filter, nomads
variables = TMP, UGRD, VGRD, HGT
levels    = 500 mb, 850 mb, surface
```

```bash
sharktopus --config my_run.ini                # use file as-is
sharktopus --config my_run.ini --priority nomads  # override one key
```

Precedence is **CLI flag > config file > built-in defaults**, matching
Python stdlib conventions. Keys in the `[gfs]` section match the
CLI flag names (dashes → underscores) and raise a clear `ConfigError`
on typos.

### Sources (Layer 1)

Six sources, all registered at import time:

| Name            | Endpoint                                            | Earliest   | Retention  | Strategies                       |
|-----------------|-----------------------------------------------------|------------|------------|----------------------------------|
| `nomads`        | `nomads.ncep.noaa.gov` (origin)                     | rolling    | ~10 days   | Full download / **idx byte-range** |
| `nomads_filter` | `nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl`   | rolling    | ~10 days   | Server-side subset               |
| `aws`           | `noaa-gfs-bdp-pds.s3.amazonaws.com`                 | 2021-02-27 | indefinite | Full download / **idx byte-range** |
| `gcloud`        | `storage.googleapis.com/global-forecast-system`     | 2021-01-01 | indefinite | Full download / **idx byte-range** |
| `azure`         | `noaagfs.blob.core.windows.net/gfs`                 | 2021-01-01 | indefinite | Full download / **idx byte-range** |
| `rda`           | `data.rda.ucar.edu/d084001` (NCAR)                  | 2015-01-15 | indefinite | Full download / **borrowed idx byte-range** *(sibling AWS/GCloud/Azure; full-file fallback for pre-2021)* |

**Byte-range mode.** When you pass both `variables=` and `levels=`,
the four NCEP-layout mirrors (`nomads`, `aws`, `gcloud`, `azure`)
switch from "download the whole 500 MB GRIB, crop locally" to
"fetch the tiny `.idx`, compute merged HTTP Range requests, download
only the requested records in parallel". It's the same pattern Herbie
uses and is a strict superset of `nomads_filter` because it works on
**any date** the mirror serves, not just the last 10 days.
`rda` participates **via borrowed idx**: NCAR's ds084.1 does not
publish `.idx` sidecars of its own, but its GRIB2 files are
byte-identical to the NCEP mirrors (AWS/GCloud/Azure), so the idx
records — and their offsets — transfer 1:1. When you pass
`variables`+`levels`, RDA probes each sibling's `.idx` in turn and
issues HTTP Range requests against the RDA URL itself. For the
pre-2021 window that **only** RDA covers (no sibling idx exists),
it transparently falls back to a full download followed by
`wgrib2 -match`, producing the same subset on disk.

Measured end-to-end for the WRF-canonical set (13 vars × 49 levels =
269 records, ~485 KB after local crop), `f000 00Z` on 2026-04-17:

| Source  | Byte-range + crop | Full-file + crop | Speed-up |
|---------|-------------------|------------------|----------|
| nomads  |              21 s |             53 s |     2.5× |
| gcloud  |              35 s |             47 s |     1.4× |
| aws     |              50 s |             52 s |       ≈  |
| azure   |              51 s |            213 s |     4.2× |

For a narrow subset (e.g. `TMP/UGRD/VGRD @ 500,850 mb`) the transfer
drops into the low-MB range and wall time into the 1-5 s band.

```python
sharktopus.fetch_batch(
    timestamps=["2024010200"],
    lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
    ext=24, interval=3,
    priority=["gcloud", "aws"],
    variables=["TMP", "UGRD", "VGRD"],         # triggers byte-range mode
    levels=["500 mb", "850 mb"],
)
```

All are anonymous — no accounts, no API keys, no SDK installs. RDA
accepts an optional `SHARKTOPUS_RDA_COOKIE` env var for the rare files
behind login.

The `Earliest` column is what `EARLIEST` is set to in each source
module — approximate and intentionally conservative. Every source also
exposes a `supports(date, cycle=None)` predicate, which
`batch.available_sources(date)` uses to filter `DEFAULT_PRIORITY`
before `fetch_batch` touches the network. Command-line shortcut:

```bash
sharktopus --list-sources                  # name/workers/earliest/retention table
sharktopus --availability 20240101          # which mirrors can serve this date
```

Default priority when the caller does not pass `priority=`:
`gcloud > aws > azure > rda > nomads` — cloud mirrors first for their
throughput and stable retention, NOMADS last as a rate-limited origin
fallback. `nomads_filter` is **opt-in** because its value comes from
server-side variable/level subsetting: include it in `priority=`
explicitly when you want it.

**WRF-canonical defaults.** When `nomads_filter` is in the priority
list and the caller doesn't pass `variables` / `levels`,
`fetch_batch` falls back to `sharktopus.wrf.DEFAULT_VARS` (13 fields)
and `sharktopus.wrf.DEFAULT_LEVELS` (49 levels) — the minimum set WPS
needs to build WRF boundary conditions. Pass your own lists to
override when you care about a different subset
(e.g. just `TMP @ 500 mb` for a quick check, or radiation fluxes for
a cloud-study).

**Anti-throttle defaults.** Each source publishes a conservative
`DEFAULT_MAX_WORKERS` tuned below its observed throttle threshold.
`fetch_batch` caps step-level parallelism at the *minimum* across the
priority list, so the slowest-throttled mirror paces the pool:

| Source            | Default max workers |
|-------------------|---------------------|
| `nomads`          | 2                   |
| `nomads_filter`   | 2                   |
| `aws`             | 4                   |
| `gcloud`          | 4                   |
| `azure`           | 4                   |
| `rda`             | 1 (serial)          |

Override per batch when you know better:

```python
sharktopus.fetch_batch(..., max_workers=8)   # explicit
```

### Layer 1 — NOMADS sources

```python
from sharktopus.sources import nomads, nomads_filter

# Option A — full file (~500 MB), cropped locally afterwards.
# Omitting dest= routes the file into ~/.cache/sharktopus/fcst/YYYYMMDDHH/<bbox_tag>/
# (CONVECT-compatible layout). Set $SHARKTOPUS_DATA or pass root=... to move
# the root without touching callers.
path = nomads.fetch_step(
    "20240417", "00", 6,
    bbox=(-45, -40, -25, -20),   # lon_w, lon_e, lat_s, lat_n
)

# Option B — server-side subset (tiny download, no wgrib2 crop needed).
path = nomads_filter.fetch_step(
    "20240417", "00", 6,
    bbox=(-45, -40, -25, -20),
    variables=["TMP", "UGRD", "VGRD", "HGT"],
    levels=["500 mb", "850 mb", "surface"],
)

# Opt out of the convention any time:
nomads.fetch_step(..., dest="/scratch/my-run")
```

Default layout (drop-in compatible with CONVECT's `/gfsdata/` tree):

```
<root>/                                   # $SHARKTOPUS_DATA or ~/.cache/sharktopus
└── fcst/
    └── 20240417-00 → 2024041700/
        ├── 90S_180W_90N_180E/            # no bbox = global tag
        │   └── gfs.t00z.pgrb2.0p25.f006
        └── 25S_45W_20S_40W/              # lat_s_lon_w_lat_n_lon_e
            └── gfs.t00z.pgrb2.0p25.f006
```

Both sources apply a **default WRF-safe buffer of 2° on each side** of
*bbox* before downloading/cropping (8 grid cells at 0.25° — enough
margin for WPS / metgrid to interpolate into the WRF outer domain
without edge effects). Override per axis when you need to:

```python
# Exact bbox, no buffer at all (e.g. you already padded upstream):
nomads.fetch_step(..., bbox=bbox, pad_lon=0, pad_lat=0)

# Wider zonal padding, narrow meridional padding (elongated domain):
nomads_filter.fetch_step(..., bbox=bbox, pad_lon=4.0, pad_lat=1.5, ...)

# Match CONVECT's legacy 5°-everywhere convention:
nomads.fetch_step(..., bbox=bbox, pad_lon=5.0, pad_lat=5.0)
```

Both raise `sharktopus.sources.SourceUnavailable` when the step cannot be
served (404, NOMADS retention exceeded, etc.) so Layer 2's orchestrator
can fall back to another mirror.

## Inspiration and origin

`sharktopus` is being extracted from the CONVECT project's GFS fetcher
(<https://github.com/leandrometeoro>…/CONVECT, `containers/fetcher/scripts/`),
where the same wgrib2/idx patterns appear across five download scripts
(NOMADS, NOMADS-filter, AWS S3, Google Cloud Storage, Azure Blob). This
package consolidates those utilities into a single reusable library.

Organization inspired by [Herbie](https://herbie.readthedocs.io/), but with a
narrower scope: **GFS only, download + crop only** (no plotting, no
inventory DSL, no multi-model abstraction).

See `docs/ORIGIN.md` for a per-function mapping CONVECT → sharktopus.

## Roadmap

The package is built bottom-up in 6 layers. Each layer is validated before the
next starts.

| Layer | Module | Status | Depends on network? | Depends on cloud deploy? |
|---|---|---|---|---|
| 0. grib utilities | `sharktopus.grib` | ✅ done | no | no |
| 1. sources (NOMADS/AWS/GCloud/Azure/RDA) | `sharktopus.sources.*` | ✅ done | yes | no |
| 2. orchestrator `fetch_batch()` | `sharktopus.batch` | ✅ done | yes | no |
| 3. cloud invoke (extras) | `sharktopus.cloud` | pending | yes | yes |
| 4. cloud deploy (extras) | `sharktopus.deploy` | pending | yes | yes |
| 5. CLI + interactive menu | `sharktopus.cli` | ✅ done | — | — |

See `docs/ROADMAP.md` for details and validation criteria per layer.

## License

MIT — see `LICENSE`.
