# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Verified live
- **Azure Container App live on megashark's Azure subscription** —
  `sharktopus-crop` at `https://sharktopus-crop.ashyhill-35d1f7dd.
  eastus.azurecontainerapps.io`, pulling
  `ghcr.io/sharktopus-project/sharktopus:azure-latest`. Canonical
  WRF-payload smoke (`scripts/smoke_wrf_canonical.py`,
  `SMOKE_PROVIDERS=azure`): 269 records / 248,982 bytes in 10.7 s on
  the 2026-04-19 00Z f006 cycle, bbox (-43.5,-41.0,-23.5,-22.0), 13
  vars × 49 levels. All three cloud-crop sources now live.

### Added
- **Contributor doc `docs/CONTRIBUTING_IMAGES.md`** covering the GHCR
  image-publishing pipeline: three-variant build matrix
  (`lambda` / `cloudrun` / `azure`), tag naming scheme, public-package
  one-time setup, local `docker build` testing, how to add a new
  cloud variant, OCI index manifest Accept-header gotcha. Linked from
  `CONTRIBUTING.md` and `DEPLOY_AZURE.md`.
- **Clearer browser-auth framing** in `docs/DEPLOY_AZURE.md` and the
  interactive `sharktopus --setup azure` wrapper: password is typed
  at `microsoft.com`, never in the terminal; service-principal env
  vars reserved for CI / sudo-less hosts.
- **Azure cloud-side cropping** via a Container Apps service
  (`sharktopus.sources.azure_crop`). Parallel path to `aws_crop` /
  `gcloud_crop`: the same OCI image used on Cloud Run runs on
  Container Apps with ingress on port 8080, reads GFS byte-ranges
  from the anonymous `noaagfs.blob.core.windows.net` mirror, and
  returns only the cropped bytes. Two delivery modes, auto-selected:
  - `inline` — base64-encoded GRIB2 in the HTTP response (cap 20 MB,
    Container Apps tolerates 100 MB bodies; same safety headroom as
    Cloud Run).
  - `blob` — service uploads to a private blob container under
    `crops/` and returns a SAS GET URL signed with a user-delegation
    key (managed identity), valid for 24 h by default; client
    downloads then deletes the blob (retained when
    `SHARKTOPUS_RETAIN_BLOB=true`). 7-day lifecycle on the container
    is the backstop.
- **Container Apps free-tier quota tracking**
  (`sharktopus.cloud.azure_quota`) shares the JSON cache with the
  AWS + GCloud trackers, keyed by provider name (`azure`). Tracks
  three dimensions (invocations 2M/mo, vCPU-seconds 180k/mo,
  GiB-seconds 360k/mo) — structurally identical to Cloud Run, prices
  slightly different ($0.40/M req, $0.000024/vCPU-s, $0.0000026/GB-s).
  Same `SHARKTOPUS_LOCAL_CROP` / `SHARKTOPUS_ACCEPT_CHARGES` /
  `SHARKTOPUS_MAX_SPEND_USD` env gates.
- **Azure one-shot provisioning** (`deploy/azure/provision.py`) —
  pure-Python, uses the Azure management SDKs (no `az` shell-out).
  Idempotently registers resource providers, creates the resource
  group, storage account + blob container (7-day lifecycle), Log
  Analytics workspace, Container App Environment (Consumption plan),
  and the Container App itself pulling
  `ghcr.io/sharktopus-project/sharktopus:azure-latest` straight from
  GHCR. System-assigned managed identity on the app gets Storage
  Blob Data Contributor on the storage account so user-delegation
  SAS works. Prints the public ingress URL.
- **`sharktopus --setup azure`** extends the bootstrap wrapper with an
  Azure path: guides through `az login --use-device-code`, prompts
  for subscription / region / resource group, runs
  `deploy/azure/provision.py`.
- **`docs/DEPLOY_AZURE.md`** — runbook covering why Container Apps
  (not Functions) sidesteps the wgrib2 zip-deploy issue, auth,
  prerequisites, step-by-step deploy, smoke test, free-tier
  accounting, teardown.
- **`deploy/azure/` image** — Dockerfile mirrors `deploy/gcloud/`
  (python:3.11-slim-bookworm + wgrib2 + Flask). `requirements.txt`
  swaps `google-cloud-storage` for `azure-storage-blob` +
  `azure-identity`. `main.py` adapts the GCloud handler: GCS SDK →
  Blob SDK, V4 signed URL → SAS from user-delegation key,
  `gcs_bucket` / `gcs_url` envelope keys → `blob_container` /
  `blob_url`. Same HTTP contract otherwise.
- `DEFAULT_PRIORITY` now leads with all three cloud-crop sources —
  `("aws_crop", "gcloud_crop", "azure_crop", "gcloud", "aws",
  "azure", "rda", "nomads")`. `azure_crop.supports(date)` requires
  both `requests` and either `SHARKTOPUS_AZURE_URL` or the azure
  SDKs so hosts without Azure configured silently drop it.
- CLI `--quota azure` dispatches to the Container Apps tracker.

- **`sharktopus --setup {gcloud,aws}`** — one-command bootstrap that
  detects the cloud CLI, offers a user-space install (opt-in, with
  explicit download prompt), walks through browser-OAuth, and runs
  `deploy/<cloud>/provision.py`. Never runs during `pip install`.
  Lives in `src/sharktopus/setup.py`.
- **Docs on auth + billing**: `docs/DEPLOY_AWS.md` (new; IAM Identity
  Center as recommended path, static keys as fallback),
  `docs/DEPLOY_GCLOUD.md` (Auth section expanded with browser-OAuth
  details), `docs/IMAGE_STORAGE_AND_BILLING.md` (pull-once image
  model + free-tier headroom analysis — AR ~66 MB vs 500 MB ceiling;
  ECR ~90 MB vs 500 MB).
- **`scripts/smoke_wrf_canonical.py`** — live smoke of the full
  `DEFAULT_VARS × DEFAULT_LEVELS` (13 × 49) payload against AWS
  Lambda and GCloud Cloud Run. 2026-04-20 run (20260419 00Z f006,
  Macaé bbox): both clouds return 248,982 bytes / 269 records
  (AWS 5.3 s, GCloud 25.8 s incl. cold start). Confirms both
  endpoints serve the real WRF production payload byte-for-byte.
- **GCloud cloud-side cropping** via a Cloud Run service
  (`sharktopus.sources.gcloud_crop`). Parallel path to `aws_crop`: a
  container image built from `deploy/gcloud/Dockerfile` runs wgrib2
  inside Cloud Run, reads GFS byte-ranges from the anonymous
  `global-forecast-system` GCS mirror, and returns only the cropped
  bytes. Two delivery modes, auto-selected:
  - `inline` — base64-encoded GRIB2 in the HTTP response (cap 20 MB,
    well under Cloud Run's 32 MB ceiling).
  - `gcs` — service uploads to a private bucket under `crops/`, returns
    a V4 signed GET URL valid for 1 h, client downloads then deletes
    the object (retained when `SHARKTOPUS_RETAIN_GCS=true`).
- **Cloud Run free-tier quota tracking** (`sharktopus.cloud.gcloud_quota`)
  shares the JSON cache with the AWS tracker, keyed by provider name
  (`gcloud`). Tracks three dimensions — invocations (free: 2M/mo),
  vCPU-seconds (180k/mo), GiB-seconds (360k/mo) — with the same
  `SHARKTOPUS_LOCAL_CROP` / `SHARKTOPUS_ACCEPT_CHARGES` /
  `SHARKTOPUS_MAX_SPEND_USD` env gates, plus `SHARKTOPUS_RETAIN_GCS`
  for kept bucket objects.
- **GCloud one-shot provisioning** (`deploy/gcloud/provision.py`)
  enables run/storage/iamcredentials APIs, creates the crops bucket
  with a 7-day lifecycle on `crops/`, deploys the Cloud Run service
  pulling `ghcr.io/sharktopus-project/sharktopus:cloudrun-latest`
  directly from GHCR (no Artifact Registry mirror), and prints the
  service URL to export as `SHARKTOPUS_GCLOUD_URL`. `--min-instances N`
  opts into warm instances for cold-start-free invocations.
- **Matrix CI image build.** `.github/workflows/build-image.yml` now
  builds two variants (`lambda` + `cloudrun`) with scope-separated
  buildx cache and pushes tagged images to GHCR on every push to
  `main`.
- **CLI `--quota gcloud`** dispatches to the Cloud Run tracker;
  `--quota aws` continues to dispatch to the Lambda tracker. Same
  interface, different provider under the hood.
- **29 new tests** (`test_gcloud_quota.py` + `test_sources_gcloud_crop.py`)
  covering the three-dim free-tier gate, payload construction, URL
  discovery fallback chain, inline/gcs response handling, retention
  env, and the top-level `quota_report("gcloud")` dispatcher.
- `DEFAULT_PRIORITY` extended to
  `("aws_crop", "gcloud_crop", "gcloud", "aws", "azure", "rda", "nomads")`.
  `gcloud_crop.supports(date)` requires both a live `requests` import
  and a discoverable service URL, so hosts without GCloud configured
  silently drop it from auto-priority.

### Fixed
- `src/sharktopus/wrf.py`: comment claimed 48 canonical levels; tuple
  actually has 49 (soil layers + stratospheric + tropospheric +
  near-surface). Same off-by-one in two docs — all three fixed to
  read 49.
- `sharktopus.sources.gcloud_crop._id_token_for`: `fetch_id_token`
  fails with user-type ADC; fall back to
  `gcloud auth print-identity-token` (with and without `--audiences`
  for user vs SA creds). Unblocks live Cloud Run invocation when the
  deployer authenticated as a user rather than a service account.
- `deploy/gcloud/Dockerfile`: builder and runtime stages now share the
  `python:3.11-slim-bookworm` base so the wgrib2 binary's
  `libgfortran.so.5` matches what the runtime ships. Prior two-stage
  mix (amazonlinux:2 builder + Debian runtime) produced a loader
  failure at crop time (`libgfortran.so.4: cannot open shared
  object file`) — caught by local smoke test.

- **AWS cloud-side cropping** via the `sharktopus` Lambda
  (`sharktopus.sources.aws_crop`). Invokes a container-image Lambda
  that does the byte-range fetch + wgrib2 crop server-side and returns
  only the cropped bytes — typically 50-500 KB instead of 500 MB per
  step. Two delivery modes, chosen automatically by the Lambda based
  on output size:
  - `inline` — base64-encoded GRIB2 in the invocation response
    (synchronous Lambda caps at ~4.5 MB binary), no S3 round-trip.
  - `s3` — Lambda uploads to a short-lived prefix and returns a
    presigned GET URL. Client downloads and immediately deletes the
    object (retained only when `SHARKTOPUS_RETAIN_S3=true`).
- **Free-tier quota tracking** (`sharktopus.aws_quota`) — thread-safe
  local counter at `~/.cache/sharktopus/quota.json` tracking
  invocations, GB-seconds, and estimated spend per provider per UTC
  month (auto-rolls on the 1st). AWS Lambda Always-Free allowance is
  hardcoded (1M requests + 400k GB-seconds/month). Policy gates via
  env vars:
  - `SHARKTOPUS_LOCAL_CROP=true` — force local crop, skip cloud
    entirely.
  - `SHARKTOPUS_ACCEPT_CHARGES=true` + `SHARKTOPUS_MAX_SPEND_USD=N` —
    authorise paid usage up to $N/month once the free tier runs out.
  - Default (both unset): free-tier-only. `can_use_cloud_crop()`
    returns `(False, reason)` once the month's allowance is spent, and
    `aws_crop.fetch_step` raises `SourceUnavailable` so the orchestrator
    falls back to the plain `aws` source (byte-range + local crop, no
    Lambda cost).
- **Cloud-crop-first default priority.** `DEFAULT_PRIORITY` changes
  from `("gcloud", "aws", "azure", "rda", "nomads")` to
  `("aws_crop", "gcloud", "aws", "azure", "rda", "nomads")`.
  `aws_crop.supports(date)` returns `True` only when both the date
  window is covered **and** boto3 can resolve AWS credentials, so
  machines without AWS configured silently drop `aws_crop` from
  auto-priority at `available_sources` time — no failed invocation per
  batch, no behaviour change for existing users.
- 21 new tests in `test_sources_aws_crop.py` covering payload shape,
  inline/s3 response handling, quota gates, S3 retain-vs-delete,
  invocation error propagation, billed-duration log parsing, and
  credential detection.
- 13 new tests in `test_aws_quota.py` covering fresh-state defaults,
  persistence, per-provider keying, month rollover, policy gates
  (`LOCAL_CROP` / `ACCEPT_CHARGES` / `MAX_SPEND_USD`), and concurrent
  `record_invocation` calls (4 threads × 20 calls → 80 serialised).
- 3 new tests in `test_availability.py` asserting `aws_crop`'s
  placement in `DEFAULT_PRIORITY`, its credential-gated inclusion in
  auto-priority, and its silent exclusion when credentials are absent.
- **Community governance scaffolding.** `CITATION.cff` (IEAPM
  affiliation), `AUTHORS.md`, `GOVERNANCE.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1), `.github/CODEOWNERS`,
  PR template, bug/feature issue templates, and a `.github/workflows/ci.yml`
  matrix (pytest on Python 3.10/3.11/3.12). Prepares the repo for public
  release on a project-owned GitHub org while crediting IEAPM as the
  institutional origin.

### Added
- **Spread mode.** `fetch_batch(..., spread=True)` (default when priority
  is auto-resolved and has more than one source) distributes a batch
  across every eligible source concurrently instead of running the
  classic fallback chain. Each source drives its own worker pool at
  its own `DEFAULT_MAX_WORKERS` ceiling; all workers pull from a
  single globally ordered queue (oldest `(date, cycle, fxx)` first),
  so the earliest timestamps — the ones WRF will consume first —
  always complete first even when a later date is still in flight.
  Failure does **not** synchronously fall through to another source
  (which would bypass that source's rate limit). Instead the worker
  re-enqueues the step with its own source blacklisted; a worker on
  a different source picks it up at its own pace. Aggregate
  concurrency is `sum(workers per source)` — ~10 in the default
  gcloud/aws/azure fan-out — without any source exceeding its
  published ceiling.
- **Cooperative attempt deadlines.** New `attempt_timeout=` kwarg on
  `fetch_batch` sets a per-attempt wall-clock budget (seconds). When
  exceeded, the in-flight download is aborted and the step
  re-enqueued so another source can try. Deadlines are propagated
  end-to-end (`fetch_batch` → `fetch_step` → `download_and_crop` →
  `stream_download` / `stream_byte_ranges` / `fetch_text`) via a new
  `deadline: float | None = None` kwarg on every `fetch_step` and on
  the low-level helpers in `sources.base`. No SIGALRM / thread-kill
  primitives — checked between retries and between chunks, fine for
  HTTP I/O.
- New internal module `sharktopus._queue` with `Step` and
  `MultiSourceQueue` — priority queue sharded per source with lazy
  invalidation and in-flight claim tracking. O(log N) push/pop,
  thread-safe, wakes only the eligible source on push. 14 tests in
  `test_queue.py` covering ordering, re-enqueue, blacklist skipping,
  single-claim races, and stop/wakeup semantics.
- 10 new tests in `test_batch_spread.py` covering spread-mode
  distribution, auto-priority triggering, explicit-priority preserving
  fallback semantics, re-enqueue on failure, rate-limit ceiling
  preservation, deadline propagation, and global oldest-first
  ordering.
- **Opt-in wgrib2 OpenMP parallelism.** `grib.crop()` and
  `grib.filter_vars_levels()` now accept `omp_threads=N`, and read
  `SHARKTOPUS_OMP_THREADS` from the environment as a process-wide
  default. When set, wgrib2 is spawned with `OMP_NUM_THREADS=N` so
  its compiled-in OpenMP kicks in for `-small_grib` / `-match`
  (wgrib2 is built with `-fopenmp`). Zero impact on a single file;
  meaningful (accumulated ~10% savings) across long reanalysis
  batches. Default is still single-threaded — opt in explicitly.
- New helper `grib.suggest_omp_threads(concurrent_crops, cpu_count=None)`
  returns a safe per-process thread count given the expected
  concurrency. Formula: split idle cores across expected concurrent
  crops, cap at 8 (wgrib2 speedup flattens past ~8), leave 2 free
  for Python/I/O.
- **Headroom warning.** On the first `fetch_batch(spread=True)`
  call in a process, sharktopus emits a one-shot `UserWarning`
  when the host has ≥8 cores idle during crops and
  `SHARKTOPUS_OMP_THREADS` / `OMP_NUM_THREADS` are unset. The
  warning suggests a concrete value derived from
  `suggest_omp_threads`. Setting either env var (or `omp_threads=1`)
  silences it.

### Changed
- `fetch_batch` adds `spread: bool | None = None` and
  `attempt_timeout: float | None = None`. Default behavior when the
  caller did not pass `priority=` changes from fallback-chain to
  spread (multi-source availability → all mirrors in parallel). An
  explicit `priority=[...]` continues to use the fallback chain
  unless `spread=True` is also passed.
- Every source's `fetch_step` grows a `deadline: float | None = None`
  kwarg, forwarded through the shared `_common` helpers. `None`
  preserves the previous behavior exactly (no deadline).

## [0.1.0] — 2026-04-18

First tagged release. Layers 0, 1, 2 and 5 of the roadmap are complete:
GRIB utilities (wgrib2 wrappers + `.idx` parser + byte-range computer),
six sources (`nomads`, `nomads_filter`, `aws`, `gcloud`, `azure`, `rda`)
with byte-range mode on all five idx-capable mirrors (`rda` via
cross-mirror idx borrowing, with a full-file fallback for pre-2021),
orchestrator `fetch_batch()` with auto-priority from the availability
API, and CLI with INI-config support. Cloud-side cropping (Layers 3-4)
is planned for v0.2.

### Added
- **Cross-mirror `.idx` borrowing for RDA.** NCAR's ds084.1 does not
  publish `.idx` sidecars, but its GRIB2 files are byte-identical to
  the four NCEP-layout mirrors. When the caller passes
  `variables`+`levels` to `rda.fetch_step`, it now probes
  `aws → gcloud → azure` for the matching `.idx`, parses it, and
  issues HTTP Range requests against the RDA URL itself — record
  offsets are the same in every mirror's copy. Transfer drops from
  ~500 MB per file to ~1-15 MB, matching what the NCEP-layout sources
  already get.
- **Full-file fallback for the RDA-only pre-2021 window.** For dates
  the cloud mirrors do not cover (2015-01-15 → 2021-02-26), no
  sibling idx exists, so `download_byte_ranges_and_crop` transparently
  downloads the full file and filters locally with `wgrib2 -match`.
  The caller still receives exactly the requested subset; only the
  on-the-wire transfer is wider.
- `download_byte_ranges_and_crop` grows two kwargs: `sibling_urls`
  (list of byte-identical mirror GRIB2 URLs whose `.idx` may be
  borrowed) and `allow_full_file_fallback` (when every idx URL 404s,
  download + filter locally instead of raising). `batch._BYTE_RANGE_CAPABLE`
  gains `"rda"`.
- 4 new tests in `test_byte_range.py` covering the sibling-idx paths:
  borrows sibling when primary 404s, tries siblings in order, full-file
  fallback when all idx 404 and fallback is enabled, raises
  `SourceUnavailable` (citing the count of tried sources) when fallback
  is disabled.

### Added
- **Byte-range download via `.idx`** (ported from CONVECT, feature parity
  with Herbie). When the caller passes `variables=` and `levels=` to any
  of the four NCEP-layout mirrors (`nomads`, `aws`, `gcloud`, `azure`),
  `fetch_step` switches from "download the whole 500 MB GRIB, crop
  locally" to "fetch the tiny `.idx`, compute merged HTTP Range
  requests, download only the matching records in parallel, then
  optionally crop locally". Typical transfer drops from ~500 MB to
  ~1-15 MB and wall time from ~50 s to ~1-3 s per step. Works for
  **any date** the mirror serves, so it's a strict superset of
  `nomads_filter` (which is limited to the last ~10 days).
  `fetch_batch` forwards `variables`/`levels` to every byte-range-capable
  source, so you only set them once at the orchestrator level.
- New low-level helpers in `sharktopus.sources.base`:
  `fetch_text(url, ...)` for tiny text payloads (the `.idx` itself),
  `head_size(url, ...)` for file-size discovery (with a
  `Range: bytes=0-0` fallback when HEAD is rejected by S3-style hosts),
  and `stream_byte_ranges(url, ranges, dst, *, max_workers=N, ...)` that
  downloads ranges in parallel via `ThreadPoolExecutor` and concatenates
  them in original order for a valid GRIB2 stream.
- New pipeline helper
  `sharktopus.sources._common.download_byte_ranges_and_crop(...)` that
  wraps `fetch_text → parse_idx → filter → head_size → byte_ranges →
  stream_byte_ranges → optional local crop → verify`. Each full-file
  source's `fetch_step` dispatches to it when `variables`+`levels` are
  both provided.
- 14 new tests in `test_byte_range.py` covering: `.idx` fetch, HEAD
  fallback, parallel range download with out-of-order futures (order
  preserved), no-match detection, empty-ranges rejection, 404
  propagation, and the full `download_byte_ranges_and_crop` pipeline
  with a deterministic in-memory payload.
- `scripts/smoke_live.py` gains Phase 3b — byte-range fetch from aws /
  gcloud / azure / nomads with a narrow (`TMP/UGRD/VGRD @ 500, 850 mb`)
  selection, so the size/latency delta vs Phase 3 is visible at a glance.
  Phase 3b also runs the **WRF-canonical selection** (13 vars × 49
  levels = 269 records, ~485 KB after local crop) on each mirror to
  exercise the full production path. Measured on 2026-04-17 f000:
  nomads 21 s, gcloud 35 s, aws 50 s, azure 51 s — vs 53/47/52/213 s
  for the equivalent full-file downloads.
- **Availability API.** Each source now exposes
  `EARLIEST` (earliest date it's known to serve) and `RETENTION_DAYS`
  (rolling-window size; `None` = unbounded), plus a
  `supports(date, cycle=None, *, now=None) -> bool` helper.
  `sharktopus.batch.available_sources(date, cycle=None)` and
  `sharktopus.batch.DEFAULT_PRIORITY` expose the pre-filtered priority
  list. `fetch_batch(priority=None)` now *auto-derives* the priority
  from the first timestamp so recent dates fan out across the cloud
  mirrors, 2015–2020 requests route to RDA, and pre-2015 requests fail
  fast with `SourceUnavailable` instead of pinging every mirror in
  vain. Users still pass `priority=[...]` when they want to pin it.
- **WRF-canonical defaults.** New `sharktopus.wrf` module exposes
  `DEFAULT_VARS` (13 fields: HGT/LAND/MSLET/PRES/PRMSL/RH/SOILL/SOILW/
  SPFH/TMP/TSOIL/UGRD/VGRD) and `DEFAULT_LEVELS` (49 levels: full
  1000→0.01 mb isobaric column + 4 soil layers + 2 m/10 m/surface/MSL),
  matching CONVECT's production fetchers. `fetch_batch` now falls back
  to these when `nomads_filter` is in priority and the caller omits
  `variables` / `levels`. Pass your own lists to override — the library
  never assumes WRF anywhere else.
- **CLI introspection.** `sharktopus --list-sources` prints a
  name/workers/earliest/retention table. `sharktopus --availability
  YYYYMMDD` prints which sources can serve a given date (and why the
  others can't). Both short-circuit before any network I/O.
- `sharktopus.batch.source_supports(name, date, cycle=None, *, now=None)`
  for programmatic queries. `register_source(..., supports=fn)` lets
  custom mirrors plug in their own availability predicate.
- `scripts/smoke_live.py` rewritten as a four-phase verbose walkthrough
  (imports / CLI / per-source / availability) suitable for showing to
  humans.
- **Layer 1 complete** — four new full-file mirrors join the existing
  `nomads` / `nomads_filter` pair:
  - `sharktopus.sources.aws` — AWS Open Data bucket
    `noaa-gfs-bdp-pds` (anonymous HTTPS, ~2 year retention).
  - `sharktopus.sources.gcloud` — Google Cloud bucket
    `global-forecast-system` (anonymous HTTPS, long retention).
  - `sharktopus.sources.azure` — Azure Blob `noaagfs/gfs`
    (anonymous HTTPS, indefinite retention).
  - `sharktopus.sources.rda` — NCAR RDA dataset `ds084.1`
    (since 2015-01-15, validity-time filenames, optional
    `$SHARKTOPUS_RDA_COOKIE` for authenticated requests).
  All four share the same full-GRIB-download + local-crop recipe via
  the new `sharktopus.sources._common.download_and_crop` helper; each
  exposes `BASE_URL`, `build_url`, `DEFAULT_MAX_WORKERS`, and
  `fetch_step`.
- **Anti-throttle worker defaults.** Each source publishes a
  `DEFAULT_MAX_WORKERS` tuned below its observed throttle threshold
  (NOMADS/filter: 2, AWS/GCloud/Azure: 4, RDA: 1). `fetch_batch` runs
  steps in parallel via `ThreadPoolExecutor`, sizing the pool to
  `min(DEFAULT_MAX_WORKERS)` across the priority list so the
  slowest-throttled mirror paces the batch. `max_workers=N` lets
  callers override.
- `batch.register_source(name, fn, *, max_workers=1)` now records the
  per-source worker ceiling alongside the fetcher. New public helpers:
  `batch.source_default_workers(name)`,
  `batch.default_max_workers(priority)`.
- `cli.py` learns `--max-workers`; `config.py` accepts `max_workers` in
  the `[gfs]` section.
- **Layer 2 start** — `sharktopus.batch.fetch_batch(...)` orchestrator
  iterates over cycles × forecast steps and falls back across a
  `priority=[...]` list of sources on
  `SourceUnavailable`. Mirrors CONVECT's `menu_gfs.download_batch` call
  signature (separate `lat_s/lat_n/lon_w/lon_e` floats; optional
  `on_step_ok` / `on_step_fail` callbacks). Source registry is a plain
  dict; `register_source(name, fn)` adds entries.
  `sharktopus.generate_timestamps(start, end, step)` is the CONVECT
  helper, re-exported at the top level.
- **CLI** `sharktopus` (`sharktopus.cli:main`) — flag names match
  CONVECT's `download_batch_cli.py` (`--timestamps` XOR
  `--start/--end/--step`, `--ext`, `--interval`, `--lat-s/n/w/e`,
  `--priority`). Extras: `--config`, `--dest`, `--root`, `--vars`,
  `--levels`, `--pad-lon`, `--pad-lat`, `--product`.
- **Config loader** `sharktopus.config.load_config(path)` reads an INI
  file with a single `[gfs]` section. Keys mirror CLI flag names,
  lists use comma (or whitespace) separation, unknown keys raise
  `ConfigError`. Precedence when using the CLI: flag > config > default.
- `sharktopus.paths` — default output-path convention mirroring
  CONVECT's `/gfsdata/` layout:
  `<root>/{fcst|anls}/<YYYYMMDDHH>/<bbox_tag>/`, where `<bbox_tag>` is
  `lat_s_lon_w_lat_n_lon_e` with each coord formatted as
  `{abs:.0f}{N|S|E|W}` (e.g. `32S_52W_13S_28W`) and `<root>` defaults
  to `~/.cache/sharktopus`. Overridable via `$SHARKTOPUS_DATA` or a
  `root=` kwarg. `None` bbox produces the global
  `90S_180W_90N_180E` tag.
- `sources.nomads.fetch_step` and `sources.nomads_filter.fetch_step`
  now take an optional `root=` kwarg and accept `dest=None` (new
  default) — in that case the file lands in the convention directory
  above. Passing an explicit `dest=` preserves the old behavior.
- `sharktopus.grib.expand_bbox(bbox, pad_lon, pad_lat)` — pure helper
  that grows a bbox by independent lon/lat pads (clamps lat to ±90°,
  rejects negative pads).
- `sharktopus.grib.DEFAULT_WRF_PAD_LON` / `DEFAULT_WRF_PAD_LAT`
  constants (both `2.0°` = 8 grid cells at 0.25°, the minimum margin
  we consider WRF-safe for WPS / metgrid interpolation).
- `sharktopus._wgrib2` resolver module with public
  `resolve_wgrib2 / ensure_wgrib2 / bundled_wgrib2 / WgribNotFoundError`.
  Resolution order: explicit arg → `$SHARKTOPUS_WGRIB2` → bundled
  binary under `_bin/` → `$PATH`.
- `hatch_build.py` custom build hook that flips the wheel to
  `py3-none-<platform>` when a wgrib2 binary is present under
  `src/sharktopus/_bin/` at build time.
- `scripts/build_wgrib2.sh` — compile wgrib2 from NOAA upstream with
  optional features (AEC, OpenJPEG, NetCDF) disabled, producing a
  binary that depends only on base-system libs.
- `scripts/bundle_wgrib2.sh` — drive the full local wheel build
  (materialise binary → portability check → `python -m build` →
  `auditwheel repair`).
- `.github/workflows/build-wheels.yml` — CI that compiles wgrib2 and
  produces a `manylinux_2_28_x86_64` wheel as an artifact on `v*` tags
  and manual dispatch. Does not publish to PyPI yet.

### Changed
- `fetch_batch` signature: `priority` default changes from
  `("nomads_filter", "nomads")` to `None` (auto-derive from
  availability). Callers pinning the old behavior should pass
  `priority=("nomads_filter", "nomads")` explicitly. The old default
  required `variables` + `levels` anyway, so most real calls already
  pass a priority list.
- `fetch_batch` now accepts `now: datetime | None = None` for tests
  that need to freeze the availability clock.
- `_common.download_and_crop` and all six source `fetch_step` functions
  default `wgrib2=None` (was `"wgrib2"`, which silently bypassed the
  resolver and missed the bundled binary when wgrib2 wasn't on
  `$PATH`). `None` triggers `_wgrib2.ensure_wgrib2` normally.
- `sources.nomads.fetch_step` refactored to call the shared
  `_common.download_and_crop` helper (no behavioural change — the
  verify / crop / cleanup sequence is identical, just deduplicated).
- `sources.nomads.fetch_step` now expands *bbox* by `pad_lon` / `pad_lat`
  (both default 2°) before calling `grib.crop`. Previously cropped the
  exact user bbox, which is unsafe for WRF because metgrid needs a
  margin.
- `sources.nomads_filter.{build_url, fetch_step}` replace the single
  isotropic `pad_deg` parameter with independent `pad_lon` / `pad_lat`,
  both defaulting to 2°. Callers reproducing CONVECT's runs should pass
  `pad_lon=5, pad_lat=5` explicitly.
- All `grib.*` functions now take `wgrib2: str | None = None` (was
  `= "wgrib2"`). `None` triggers the resolver; passing a path keeps
  the explicit-override behavior.
- `grib.verify` raises `GribError` when wgrib2 parses zero records
  from a non-empty file. wgrib2 v3.1.3 stays silent on malformed
  input, so the previous behavior would silently return `0` on a
  corrupt or non-GRIB2 file.

## [0.1.0] — 2026-04-17

### Added
- **Layer 1 start** — `sharktopus.sources` sub-package:
  - `sharktopus.sources.base` — `SourceUnavailable` exception,
    `canonical_filename`, `validate_cycle`, `validate_date`,
    `check_retention`, and a stdlib-only `stream_download` with retries
    and HTTP 404 → `SourceUnavailable` mapping.
  - `sharktopus.sources.nomads` — direct full-file download from
    `nomads.ncep.noaa.gov/pub/.../gfs.prod/...`. Optional local crop via
    `grib.crop` when `bbox=` is passed. Enforces the ~10-day NOMADS
    retention window before touching the network.
  - `sharktopus.sources.nomads_filter` — server-side cropping via
    `filter_gfs_0p25.pl` (and `..._1hr.pl` with `hourly=True`). Accepts
    wgrib2-style level names (`"500 mb"`, `"2 m above ground"`) and
    converts them to NOMADS query params.
- 25 new tests covering URL construction, retention, retry, 404 mapping,
  level-name conversion, and the full download-then-crop flow (with
  monkeypatched `urlopen`).
- `docs/ORIGIN.md` updated with Layer 1 mapping.

### Changed
- Package metadata bumped to 0.1.0.

## [0.0.1] — 2026-04-17

### Added
- Initial package scaffold (`pyproject.toml`, `src/sharktopus/`, `tests/`).
- **Layer 0** — `sharktopus.grib` module with six wgrib2 / `.idx` utilities
  consolidated from the CONVECT project's five GFS download scripts
  (`containers/fetcher/scripts/download_{nomades,nomads_filter,aws,gcloud,azure}_gfs_0p25.py`):
  - `verify(path)` — count GRIB2 records via `wgrib2 -s`
  - `crop(src, dst, bbox)` — geographic subset via `wgrib2 -small_grib`
  - `filter_vars_levels(src, dst, vars, levels)` — variable/level filter via `wgrib2 -match`
  - `parse_idx(text)` — parse GFS `.idx` into structured `Record`s
  - `byte_ranges(records, wanted, total_size)` — consolidated HTTP Range tuples
  - `rename_by_validity(path)` — rename file to `gfs.0p25.{YYYYMMDDHH}.f{PPP}.grib2` using `wgrib2 -v`
- Tests for Layer 0 (`tests/test_grib.py`).
- `docs/ORIGIN.md` mapping every ported function back to its CONVECT source.
- `docs/ROADMAP.md` with the six-layer build plan.
