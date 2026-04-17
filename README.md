# sharktopus

Download and crop GFS forecast data. Local sources first (NOMADS, AWS Open Data,
Google Cloud, Azure Blob); serverless cloud recortador is an optional second
layer for free-tier distributed cropping.

> **Status (2026-04-17): pre-alpha, layers 0–1 partial.** `grib` utilities
> are complete; `sources.nomads` and `sources.nomads_filter` are ported
> from CONVECT and tested. Other mirrors (AWS/GCloud/Azure/RDA) are next.
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

sharktopus.fetch_batch(
    timestamps=["2024010200", "2024010206"],
    lat_s=-28, lat_n=-18, lon_w=-48, lon_e=-36,
    ext=24, interval=3,
    priority=["nomads_filter", "nomads"],
    variables=["TMP", "UGRD", "VGRD", "HGT"],     # nomads_filter
    levels=["500 mb", "850 mb", "surface"],
)
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

| Layer | Module | Depends on network? | Depends on cloud deploy? |
|---|---|---|---|
| 0. grib utilities | `sharktopus.grib` | no | no |
| 1. sources (NOMADS, AWS, GCloud, Azure, RDA) | `sharktopus.sources.*` | yes | no |
| 2. orchestrator `fetch()` | `sharktopus.fetch` | yes | no |
| 3. cloud invoke (extras) | `sharktopus.cloud` | yes | yes |
| 4. cloud deploy (extras) | `sharktopus.deploy` | yes | yes |
| 5. CLI + interactive menu | `sharktopus.cli` | — | — |

See `docs/ROADMAP.md` for details and validation criteria per layer.

## License

MIT — see `LICENSE`.
