# sharktopus

Download and crop GFS forecast data. Local sources first (NOMADS, AWS Open Data,
Google Cloud, Azure Blob); serverless cloud recortador is an optional second
layer for free-tier distributed cropping.

> **Status (2026-04-17): pre-alpha, layer 0 only.** The current code is the
> `grib` module — pure wgrib2/`.idx` utilities used by every download source.
> No network code yet. See `docs/ROADMAP.md` for the layered build plan.

## Install

```bash
# Requires wgrib2 on PATH for most operations (verify, crop, filter)
pip install -e .
```

## Quick start (layer 0 — local wgrib2 utilities)

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
