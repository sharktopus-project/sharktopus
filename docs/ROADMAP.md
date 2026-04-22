# Roadmap — six-layer build

Extracted from CONVECT's `PLANO_BIBLIOTECA_SHARKTOPUS.md`.

The package is built bottom-up. Each layer is validated on a **canonical test
case** (2024-01-21 00Z, fxx=6, bbox=(-45,-40,-25,-20) — same case used by the
CONVECT radar-DA pipeline) before the next layer starts.

```
┌──────────────────────────────────────────────────────────────┐
│ 5. CLI & WebUI                (sharktopus.cli, .webui)       │
├──────────────────────────────────────────────────────────────┤
│ 4. Serverless deploy  [extra] (sharktopus.deploy)            │
├──────────────────────────────────────────────────────────────┤
│ 3. Serverless invoke  [extra] (sharktopus.cloud + *_crop)    │
├──────────────────────────────────────────────────────────────┤
│ 2. Orchestrator fetch        (sharktopus.batch)              │
├──────────────────────────────────────────────────────────────┤
│ 1. Local sources             (sharktopus.sources.*)          │
├──────────────────────────────────────────────────────────────┤
│ 0. wgrib2 + .idx utilities   (sharktopus.io.grib)
└──────────────────────────────────────────────────────────────┘
       ← Layers 0–5 done for AWS + GCloud + Azure — all three live.
         WebUI ships the multi-product foundation (2026-04-21);
         GFS 0.25° is the only product registered today.
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the product-agnostic core
vs. product-specific adapter split.

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

## Layer 3 — cloud-crop sources — DONE (live on AWS + GCloud + Azure)

Cloud-side cropping: the serverless endpoint reads the public GFS
mirror byte-range itself and returns only the cropped GRIB2. Three
sources implemented:

1. ✅ `aws_crop` — invokes AWS Lambda (`sharktopus`) via boto3.
2. ✅ `gcloud_crop` — POSTs to Cloud Run (`sharktopus-crop`) via
   HTTPS + OIDC ID token.
3. ✅ `azure_crop` — POSTs to Azure Container Apps (`sharktopus-crop`)
   via plain HTTPS (no auth by default; optional bearer token).
   Live on megashark's subscription (2026-04-20), smoke passed on the
   WRF-canonical payload.

All three deliver in two modes (auto-selected by payload size):
`inline` (base64 in the HTTP response, ≤ 20 MB) and
`s3`/`gcs`/`blob` (signed URL valid for 1–24 h, client downloads
and deletes).

Free-tier quota tracking (`sharktopus.cloud.{aws,gcloud,azure}_quota`)
shares the same `~/.cache/sharktopus/quota.json`, keyed by provider.
Gates: `SHARKTOPUS_ACCEPT_CHARGES`, `SHARKTOPUS_MAX_SPEND_USD`.

## Layer 4 — `deploy/{aws,gcloud,azure}/provision.py` — DONE (unreleased)

One-shot provisioning scripts, each idempotent:

- **`deploy/aws/provision.py`** — creates ECR Pull-Through Cache rule
  pointing at `ghcr.io`, IAM role, S3 bucket with 7-day lifecycle,
  deploys the `sharktopus` Lambda (container image, 2048 MB, 300 s).
- **`deploy/gcloud/provision.py`** — enables APIs, creates GCS bucket
  with 7-day lifecycle, creates an AR **remote repository** named
  `ghcr-proxy` (Cloud Run refuses `ghcr.io/*` URLs directly), deploys
  `sharktopus-crop` to Cloud Run (1 vCPU, 2 GiB, 300 s timeout).
- **`deploy/azure/provision.py`** — pure-Python, uses the azure-mgmt-*
  SDKs. Creates RG, Storage V2 + blob container (7-day lifecycle),
  Log Analytics workspace, Container App Environment, Container App
  pulling directly from GHCR (Container Apps accepts public
  `ghcr.io/*` URLs — no registry mirror). Grants the app's managed
  identity `Storage Blob Data Contributor` for SAS generation.
- **`sharktopus --setup {gcloud,aws,azure}`** (`src/sharktopus/setup.py`)
  — interactive wrapper around the three scripts. Detects the cloud
  CLI, offers user-space install (opt-in; Azure requires sudo once,
  per Microsoft's installer), walks through browser OAuth, then calls
  the right `provision.py`. ~4 prompts end-to-end.

## Layer 5 — `sharktopus.cli` — DONE (unreleased)

`sharktopus` entry point mirrors CONVECT's `download_batch_cli.py` flag
names, reads INI config via `sharktopus.config`, and runs everything
through `fetch_batch`. Adds `--list-sources`, `--availability`,
`--quota {aws,gcloud,azure}`, and `--setup {gcloud,aws,azure}` for
introspection and bootstrap.

## Layer 5b — `sharktopus.webui` — DONE (milestones M0–M7, M11 landed; M8–M10, M12 pending)

Local FastAPI + Jinja + HTMX web UI (`sharktopus --ui`). No-JS-framework
on purpose; vendored `htmx.min.js` and Leaflet for the bbox picker. See
`CHANGELOG.md` for the per-milestone detail.

- M0 ✅ Scaffold (FastAPI, SQLite jobs DB, `--ui` entry point).
- M1 ✅ Submit + Jobs pages + JobRunner.
- M2 ✅ Inventory page with cache ingest.
- M3 ✅ Quota page with spend charts.
- M4 ✅ Leaflet map picker for bbox.
- M5 ✅ Variable/level pickers with presets.
- M6 ✅ Source priority drag-and-drop + availability probes.
- M7 ✅ Credentials page (view/rotate/clear).
- M8 ⏳ Setup Wizard GCloud (8 steps, streaming logs).
- M9 ⏳ Setup Wizard AWS.
- M10 ⏳ Setup Wizard Azure.
- M11 ✅ Settings page + Help/Docs inline.
- M12 ⏳ Polish (theme, empty states, skeletons, a11y).

## Multi-product foundation — DONE (2026-04-21)

Before this change the WebUI assumed GFS 0.25° exclusively. Post-change:

- `sharktopus.webui.products.Product` registry — frozen dataclass +
  `PRODUCTS` tuple. Adding a new product is a single entry (see
  [ADDING_A_PRODUCT.md](ADDING_A_PRODUCT.md)).
- Per-product catalog JSONs under
  `src/sharktopus/webui/data/products/`. `~/.cache/sharktopus/products/`
  is an override path — edit locally, no rebuild needed.
- Submit form's product field is a `<select>` populated from the
  registry; changing it re-fetches `/api/catalog?product=<id>` and
  swaps the variable/level picker live.
- Public positioning shifted from "GFS cropper" to "GRIB cropper —
  GFS today." See `README.md` and `site/index.html`.

**Pipeline for future products** (not yet implemented):
GFS 0.5°, GFS secondary (pgrb2b), HRRR, NAM-CONUS, RAP, ECMWF open-data.
Each requires a `Product(...)` entry, a catalog JSON, and either a new
`sources/<mirror>_<model>.py` or a `product`-keyed branch in the
existing source module.
