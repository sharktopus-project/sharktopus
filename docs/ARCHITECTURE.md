# Architecture

How sharktopus is put together, and — more importantly for contributors —
which parts are **product-agnostic** (shared) and which are
**product-specific** (need a new adapter for each model you want to add).

> **Scope today:** GFS 0.25° (`pgrb2.0p25`) end-to-end on NOMADS + AWS
> Open Data + Google Cloud + Azure + RDA, plus serverless cloud-crop on
> AWS Lambda and GCloud Cloud Run and Azure Container Apps.
>
> **Target:** GFS 0.5°, GFS secondary (pgrb2b), HRRR, NAM, RAP, and
> ECMWF open-data follow the same pattern; the core is explicitly
> factored so adding them is a plug-in, not a fork. See
> [ADDING_A_PRODUCT.md](ADDING_A_PRODUCT.md).

## The two sides of the package

```
┌──────────────────────── product-agnostic core ──────────────────────┐
│                                                                     │
│   io.grib       — wgrib2 wrappers, .idx parse, bbox math            │
│   io.paths      — cache / output dir layout                         │
│   sources.base  — streaming HTTP GET / HEAD / Range, retry,         │
│                   SourceUnavailable, retention gate                 │
│   sources._common — download + local-crop, byte-range + local-crop  │
│   batch         — orchestrator, priority, spread, queue             │
│   cloud.*_quota — free-tier tracking, spend gates                   │
│   webui         — FastAPI app, jobs, presets, catalog loader        │
│                                                                     │
│   None of this cares which model/product is flowing through.        │
│   All it knows is: "call fetch_step(date, cycle, fxx, product=...)  │
│   on a source module and get a Path back."                          │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────── product-specific adapters ──────────────────────┐
│                                                                     │
│   sources/<mirror>.py         — build_url(date, cycle, fxx,         │
│                                  product) + canonical_filename;     │
│                                  one file per (mirror, model)       │
│                                  combination.                       │
│                                                                     │
│   sources/<mirror>_crop.py    — invokes the serverless endpoint,    │
│                                  sends an event payload, reads      │
│                                  the response. Endpoint URL and     │
│                                  payload shape are product-scoped.  │
│                                                                     │
│   webui/products.py           — Product registry. One entry per     │
│                                  (model, format) pair.              │
│                                                                     │
│   webui/data/products/        — JSON catalog per product:           │
│     gfs_pgrb2_0p25.json         variables, levels, categories.      │
│                                                                     │
│   deploy/<cloud>/handler.py   — the serverless worker itself.       │
│     (Lambda / Cloud Run /       Knows how to turn {date,cycle,fxx,  │
│      Container App)             product, bbox, vars, levels} into   │
│                                  a GRIB2 byte-range fetch + crop.   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

Everything in the top box is reused untouched when you add a new
product. Everything in the bottom box grows by one file (or one entry)
per product.

## Layer diagram

```
┌──────────────────────────────────────────────────────────────┐
│ 5. CLI & Menu interactive    (sharktopus.cli)                │
├──────────────────────────────────────────────────────────────┤
│ 4. Serverless deploy  [extra] (sharktopus.deploy)            │
├──────────────────────────────────────────────────────────────┤
│ 3. Serverless invoke  [extra] (sharktopus.cloud + *_crop)    │
├──────────────────────────────────────────────────────────────┤
│ 2. Orchestrator fetch        (sharktopus.batch)              │
├──────────────────────────────────────────────────────────────┤
│ 1. Mirror sources            (sharktopus.sources.*)          │
├──────────────────────────────────────────────────────────────┤
│ 0. wgrib2 + .idx utilities   (sharktopus.io.grib)            │
└──────────────────────────────────────────────────────────────┘
       ╔══════════════════════════════════════════════╗
       ║  WebUI sits beside the CLI, calls Layer 2    ║
       ║  (FastAPI; sharktopus.webui)                 ║
       ╚══════════════════════════════════════════════╝
```

Layers are built bottom-up. Nothing below Layer 3 knows what a cloud
provider is; nothing below Layer 2 knows what a batch is.

## Where each layer lives

| Layer | Module | Product-specific bits |
|-------|--------|----------------------|
| 0 | `sharktopus.io.grib`, `sharktopus.io.paths` | *none* — bbox math, wgrib2 calls, cache layout |
| 1 | `sharktopus.sources.{nomads,nomads_filter,aws,gcloud,azure,rda}` | `build_url()` URL pattern, `canonical_filename()` |
| 1 | `sharktopus.sources.{aws,gcloud,azure}_crop` | payload shape, endpoint URL, response parsing |
| 2 | `sharktopus.batch.{orchestrator,priority,spread,queue,schedule,registry}` | *none* — sources are registered by name, product passes through |
| 3 | `sharktopus.cloud.{aws,gcloud,azure}_quota` | *none* — quota counters keyed by provider, not product |
| 4 | `sharktopus.deploy.{aws,gcloud,azure}` (provision.py + handler.py) | handler.py URL-resolution + product whitelist |
| 5 | `sharktopus.cli`, `sharktopus.webui` | product registry + catalog; everything else agnostic |

## Why this factoring is cheap to extend

A new product changes **three things**, none of which touch the core:

1. A `Product(...)` entry in `webui/products.py`.
2. A `data/products/<model>_<fmt>.json` catalog.
3. Either (a) a new `sources/<mirror>_<model>.py` per mirror that
   serves this product, or (b) a `product` branch inside the existing
   `build_url()` if the same mirror hosts both (GFS case).

The orchestrator, the queue, the priority filter, the availability
probes, the WebUI, and the quota counters don't need to change at all.

## Data flow for one job

```
  user clicks Submit               ┌──────────────────────────┐
  ──────────────────────────▶     │     webui/routes/pages   │
                                   │    serializes form       │
                                   └────────────┬─────────────┘
                                                │  product id →
                                                │  resolve_code()
                                                ▼
                                   ┌──────────────────────────┐
                                   │        cli / batch       │◀── priority,
                                   │   fetch_batch(...)       │    bbox, vars,
                                   └────────────┬─────────────┘    levels
                                                │  for each step:
                                                │    tries sources in order
                                                ▼
                                   ┌──────────────────────────┐
                                   │  sources.<mirror>        │  — build_url(product)
                                   │   .fetch_step(...)       │    + byte-range or
                                   └────────────┬─────────────┘      full download
                                                │
                                                ▼
                                   ┌──────────────────────────┐
                                   │  io.grib.crop_small(...) │  ← bbox in
                                   │   or cloud crop Lambda   │     lon_w/lon_e/lat_s/lat_n
                                   └────────────┬─────────────┘
                                                │
                                                ▼
                                         cropped GRIB2
                                         on local disk
```

The **product** parameter rides on the call chain from the form all the
way down to `build_url(..., product=<code>)` and
`canonical_filename(cycle, fxx, product=<code>)`. The orchestrator
itself never inspects it — it only routes by source name.

## WebUI catalog loading

The UI dynamically swaps the variable/level picker when the user
changes product:

```
┌────────────────────┐   GET /api/products
│ Submit page load   │  ───────────────────▶ [{id, label, code, ...}, ...]
└────────┬───────────┘
         │ (render <select>)
         ▼
┌────────────────────┐   GET /api/catalog?product=<id>
│ product-select     │  ───────────────────▶ {variables, level_groups,
│ change event       │                         product_id}
└────────┬───────────┘
         │ (reload var/level pickers, prune invalid selections)
         ▼
   state.catalog ← new catalog
```

The server endpoint (`api.catalog_json`) delegates to
`webui.catalog.load_catalog(product_id)`, which checks a per-product
override at `~/.cache/sharktopus/products/<file>.json` before falling
back to the bundled JSON. This means a power user can regenerate an
updated catalog with `scripts/generate_gfs_catalog.py` without
rebuilding the wheel.

## What's deliberately out of scope today

- Non-GRIB2 formats (NetCDF, Zarr). The interface is pure bytes →
  cropped GRIB2; anything else is an ingestion step a downstream
  consumer performs on the output.
- Custom regridding beyond what `wgrib2 -small_grib` offers. The
  library crops; consumers (WRF WPS, ROMS prep, xarray) do their own
  interpolation.
- Multi-cycle ensembles (GEFS). Feasible under the same architecture
  but requires a member-index dimension on the catalog schema —
  planned, not implemented.
- Streaming (server-sent events) from the crop endpoint. Today the
  cloud crop returns the whole cropped file; for very large crops a
  chunked response would cut peak memory — open design question.

## References

- [ADDING_A_PRODUCT.md](ADDING_A_PRODUCT.md) — worked HRRR example
- [ROADMAP.md](ROADMAP.md) — layer completion status + upcoming work
- [ORIGIN.md](ORIGIN.md) — which CONVECT scripts each layer consolidates
