# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

## [0.1.6] — 2026-04-30

This release closes the install-and-deploy gaps that uchoa@snowshark
documented while smoke-testing 0.1.1 on a fresh Debian/Ubuntu host.

### Added
- **`[aws]`, `[gcloud]`, `[azure]`, `[all]` pip extras.** Each cloud's
  deploy SDK (boto3 / google-* / azure-*) is now declared as an
  optional extra — install before running the matching `--setup`.
  Previously the user authenticated over SSO and only then hit
  `ModuleNotFoundError: No module named 'boto3'`.
- **Cloud deploy scripts shipped in the wheel.** `deploy/{aws,gcloud,azure}/`
  is force-included under `sharktopus/_deploy/` so a plain
  `pip install sharktopus` is enough to run `--setup` — no source
  checkout needed.
- **`provision.py --credential-arn` and `--create-credential` (AWS).**
  Both options handle AWS's requirement that ECR pull-through cache
  rules pointing at ghcr.io carry Secrets Manager credentials, even
  for public images. `--create-credential` walks the user through
  creating the secret in place; the GitHub PAT goes straight to
  Secrets Manager and is never written to disk.
- **README "Prerequisites" section.** Documents Python ≥ 3.10, the
  Debian/Ubuntu `python3-venv` package, and the PEP 668
  externally-managed-environment trap. Adds a venv-first install path.

### Changed
- **`sharktopus --setup` pre-flights its dependencies.** `provision.py`
  presence and the cloud's SDK import are checked *before* any prompt,
  so the user can't burn ten minutes on SSO and then be told boto3
  is missing.
- **`sharktopus --ui` banner is louder and headless-aware.** When
  `$SSH_CONNECTION` or no `$DISPLAY` is detected, the URL is wrapped
  in a `═══` rule and an explicit "no display detected — open
  manually / forward the port" hint replaces the silent
  `webbrowser.open` attempt.
- **`provision.py` ECR pull-through cache failure is now self-explaining.**
  On `UnsupportedUpstreamRegistryException`, the user sees a numbered
  three-option recovery (use `--create-credential`, use
  `--credential-arn`, or push to private ECR by hand) instead of a
  raw boto3 traceback.

## [0.1.5] — 2026-04-27

### Added
- **WebUI language toggle** (EN/PT). English is the new default; Portuguese
  is one click away via the EN/PT pills in the header. Cookie-based, so
  the choice sticks per browser. Header, nav, footer, and the dashboard
  are translated; other pages fall back gracefully to English when no
  translation is registered (incremental translation as needed).

### Changed
- **Dashboard hero in English by default.** The previous hero copy was
  Portuguese; both languages are now driven by the same translation
  table.

### Fixed
- **`__version__` was stuck at 0.1.0.** The package now reports the
  pyproject version correctly.

## [0.1.4] — 2026-04-25

### Fixed
- **macOS arm64 + x86_64 wheel builds** (2026-04-25). The 0.1.3 fix made
  the gfortran preflight succeed, but compilation still failed in
  proj-4.8.0's `configure` because its bundled `config.sub` (2007) does
  not recognise `arm64`. Sharktopus does not actually need `proj` —
  bbox cropping is done with wgrib2's native `-small_grib`, no
  reprojection. Disabled `USE_PROJ4`, `USE_IPOLATES`, and
  `USE_SPECTRAL` in the wgrib2 makefile so the proj/ipolates/spectral
  bundles are never extracted or compiled. Also dropped the brittle
  `--build=<triplet>` workaround from 0.1.2; it's no longer needed.

## [0.1.3] — 2026-04-24

### Fixed
- **macOS wheel build — gfortran preflight check** (2026-04-24). The 0.1.2
  fix made the CI workflow set `FC=/opt/homebrew/bin/gfortran-13`, but
  `scripts/build_wgrib2.sh` still ran a preflight `command -v gfortran`
  before consulting `$FC`, so the bare-name lookup kept failing on
  macOS. Changed the preflight loop to honour `${CC:-gcc}` and
  `${FC:-gfortran}`, which works on both Linux (where the workflow
  doesn't set them) and macOS (where it does).

## [0.1.2] — 2026-04-23

### Fixed
- **macOS wheel build (arm64 + x86_64)** (2026-04-23). CI workflow set
  `FC: gfortran` plainly, but Homebrew's `gcc` formula only installs
  versioned binaries (`gfortran-13`), so `command -v gfortran` in
  `scripts/build_wgrib2.sh` failed with "missing required tool: gfortran".
  Fixed by deriving `BREW_FC` from the versioned gfortran-N in the
  Homebrew prefix, mirroring how `BREW_GCC` is already derived.
- **Linux aarch64 wheel build** (2026-04-23). wgrib2 bundles proj-4.8.0
  whose `config.guess` is frozen at 2007-03-06 and fails on aarch64
  Linux with "cannot guess build type". Fixed by sed-patching wgrib2's
  makefile to pass `--build=<host-triplet>` to proj's configure,
  skipping the auto-detection entirely.

## [0.1.1] — 2026-04-23 (PyPI)

### Added
- **WebUI screenshots in docs and PyPI page** (2026-04-23). README and the
  Cloudflare Pages landing page (`site/index.html`) now feature two
  full-resolution screenshots — the dashboard and the Submit page — so
  first-time visitors immediately see what the UI offers without having
  to install and run it. README image URLs are absolute
  (`raw.githubusercontent.com/.../main/docs/screenshots/...`) so they
  also render on the PyPI project page.

### Changed
- **Package author email** (2026-04-23). `pyproject.toml` author entry
  switched from `leandrometeoro@gmail.com` to
  `sharktopus.convect@gmail.com` — the project-wide maintainer address,
  consistent with the site Contact section and the GCP project owner.
- **Inspiration / origin section removed from README** (2026-04-23). The
  project's origin and CNPq/CONVECT funding context are covered on the
  in-UI `/about` page and in `GOVERNANCE.md`; repeating them in README
  was redundant and framed the package as a CONVECT appendage rather
  than an independent open-source project.

### Added
- **Multi-platform wheel builds** (2026-04-23). `.github/workflows/build-wheels.yml`
  now ships jobs for macOS arm64 (Apple Silicon, runner `macos-14`), macOS
  x86_64 (Intel, runner `macos-13`), and Linux aarch64 (runner
  `ubuntu-24.04-arm` with the `manylinux_2_28_aarch64` container) in addition
  to the existing Linux x86_64 job. macOS jobs compile wgrib2 against
  Homebrew's gcc/gfortran and delocate vendored `libgfortran`/`libgomp` into
  the wheel; the Linux ARM job uses `auditwheel` with the aarch64 plat tag.
  Windows is deliberately skipped — wgrib2 upstream has weak Windows support
  and the target audience (meteorologists on Linux/macOS/WSL) is small
  enough to wait until someone asks.
- **`scripts/build_wgrib2.sh` portability** (2026-04-23). `CC`/`FC` are now
  caller-overridable (needed on macOS because `/usr/bin/gcc` is Apple's
  Clang, not Homebrew's real gcc), job count falls back via `sysctl -n
  hw.ncpu` on macOS, `strip -x` replaces plain `strip` (macOS's default
  strip was breaking the binary), and post-build dep inspection uses
  `otool -L` when `ldd` isn't available.
- **`scripts/bundle_wgrib2.sh` plat-tag override** (2026-04-23). The
  manylinux platform tag passed to `auditwheel repair` is configurable via
  `$SHARKTOPUS_MANYLINUX_PLAT`, defaulting to `manylinux_2_28_x86_64`. The
  aarch64 CI job sets it to `manylinux_2_28_aarch64`.
- **`docs/ACCOUNT_SETUP.md` + in-UI onboarding links** (2026-04-22). New
  pre-authentication walk-through covering sign-up, billing, and minimum
  IAM roles for AWS / Google Cloud / Azure. The WebUI `/help` page has a
  dedicated "Starting from zero" card with the three sign-up links and a
  link to the full guide on GitHub. The `/setup/{provider}` pages stopped
  being bare "Coming in M8–M10" placeholders — they now surface the
  sign-up URL, minimum-role link, verification command, and the
  `sharktopus --setup {provider}` one-liner for the guided deploy. The
  `/credentials` page sub-header also points at ACCOUNT_SETUP.md for
  users who don't yet have credentials to show. README has a new
  "Starting from zero" callout next to the install block linking to the
  same doc. Motivation: `pip install sharktopus` targets users who may
  not have a cloud account yet — the app needs to guide them to one
  without requiring them to clone the repo.
- **Remote-access guidance** (2026-04-22). The `/help` page now has a
  dedicated "Accessing the UI remotely" card explaining that the UI is
  single-user-local by design (no auth, directory picker reads the
  server filesystem) and documenting the two safe patterns: SSH port
  forward (recommended) and SSH X-forwarding. The `--ui-host` flag's
  help text also warns against binding to `0.0.0.0` on untrusted
  networks and points at the SSH tunnel recipe.
- **About page** (`/about`, 2026-04-21). New SOBRE page in the WebUI
  lists the project coordinator (Dra. Tânia Ocimoto Oda), initial
  developer (Leandro Machado Cruz — IEAPM), and supporting
  institutions (CNPq, IEAPM, UENF, UFPR). Future contributors can add
  themselves via PR. The institution logos moved off the global
  footer so the default UX doesn't foreground the backing
  institutions — new contributors don't have to feel like joining
  means joining *them*.
- **`sources.base.format_filename(template, cycle=..., fxx=..., product=...)`**
  (2026-04-21). Generic filename formatter for per-product source
  modules. Complements the existing GFS-shaped
  `canonical_filename()` / `gfs_canonical_filename` (alias) so new
  sibling sources (`sources/aws_hrrr.py`, …) can express their own
  filename convention without touching the GFS modules.
- **Product whitelist in cloud handlers** (2026-04-21). AWS Lambda,
  GCloud Cloud Run, and Azure Container Apps handlers now enforce
  an `ALLOWED_PRODUCTS` set and return HTTP 400 for unknown product
  codes. Defence in depth — the handlers point at the public GFS
  mirror, so accepting arbitrary strings as `product` would let a
  crafted payload construct unrelated keys.
- **Clean-install smoke test** (2026-04-22). `scripts/smoke_install.sh`
  + `scripts/Dockerfile.smoke` + `scripts/smoke_checks.sh` build the
  wheel, install it into a minimal Ubuntu 24.04 image (python +
  `libgfortran5` + `libgomp1` only — no compilers, no conda, no system
  wgrib2), and run six assertions: `sharktopus --help`,
  `--list-sources`, package import, bundled wgrib2 resolves & runs,
  `[ui]` extra imports, and `--availability`. Run with
  `scripts/smoke_install.sh` before every PyPI upload.
- **Handler whitelist tests** (2026-04-22).
  `tests/test_deploy_handlers.py` covers all three cloud handlers
  (AWS Lambda, GCloud Cloud Run, Azure Container Apps) with three
  cases each: allowed-products set membership, HTTP 400 on unknown
  product, and known product passing the whitelist stage (verified
  by stubbing the downstream I/O so failure happens *past* the
  whitelist). Flask-based handlers skip gracefully when `flask` is
  not installed; `flask>=3` is now listed under the `test` extra.

### Fixed
- **`--ui` auto-picks a free port when the default is busy** (2026-04-23).
  Previously `sharktopus --ui` hard-failed with `OSError: Address already
  in use` if port 8765 was taken. Now it probes the requested port, falls
  back to an OS-assigned free port if needed, and prints the actual URL
  so the user always knows where to point their browser.
- **PyPI Homepage URL** (2026-04-23). `pyproject.toml`'s project-URL
  Homepage now points at the public site `sharktopus.leandrometeoro.com.br`
  instead of the GitHub repo (which is still listed as `Source`).
- **Bundled wgrib2 lookup after subpackage reorg** (2026-04-22).
  `BUNDLED_BIN_DIR` in `sharktopus.io.wgrib2` was pointing at
  `sharktopus/io/_bin/` instead of `sharktopus/_bin/` after the
  commit-`1504084` reorg moved the resolver into the `io` subpackage.
  The bundled binary was silently ignored on every clean install —
  callers only noticed if they had system-wide wgrib2 on `$PATH`
  (fallback step 4 of the resolver). Caught by the new clean-install
  smoke test.

### Changed
- **Branding: "GFS cropper" → "GRIB cropper"** (2026-04-21). Header
  brand-tag and app docstring now read "cloud-native GRIB cropper";
  a small italic slogan
  "GFS today · HRRR tomorrow · who knows next?" sits beside it.
  Visible signalling that the core is product-agnostic and the
  roadmap is plural.
- **Sticky header and footer** (2026-04-21). The topbar now pins to
  the top of the viewport and the slim footer pins to the bottom;
  page content scrolls under both with a subtle shadow to mark the
  edge. The footer dropped to a single credit line plus a link to
  the new About page — it no longer carries the institution logos.
- **`docs/ADDING_A_PRODUCT.md`: sibling-file path is now the
  preferred approach** (2026-04-21). Step 3b (one source file per
  (cloud, product) pair) is explicitly marked as the default for any
  new model; step 3a is reserved for variants that share both URL
  path and filename contract with an already-served product. The
  rationale: adding a product must never be able to regress an
  existing one in production.

- **Multi-product foundation in the WebUI** (2026-04-21). Introduces a
  `sharktopus.webui.products.Product` registry and per-product catalog
  JSONs under `src/sharktopus/webui/data/products/`. The Submit form's
  product field is now a `<select>` populated from the registry; when
  the user changes product, the UI re-fetches
  `/api/catalog?product=<id>` and hot-swaps the variable/level picker
  (pruning any selections no longer in the new catalog). A per-product
  override at `~/.cache/sharktopus/products/<file>.json` lets power
  users update catalogs without rebuilding the wheel. GFS 0.25° is the
  only product registered today; the design is explicitly extensible
  for GFS 0.5°, GFS secondary (pgrb2b), HRRR, NAM, RAP, and ECMWF
  open-data.
- **Product picker drives the Submit form end-to-end** (2026-04-21).
  The Product fieldset moved to the top of the form and now steers
  four other fields as the user switches model: (a) the Leaflet bbox
  map clamps to `product.default_bbox` with a dashed coverage rectangle
  (world-extent treated as "global"); (b) the Dates & cycles pickers'
  `min` and year-menu floor jump to the earliest `EARLIEST` across the
  product's sources; (c) the Sources chip pool hides any source not in
  `product.sources` (empty tuple = all); (d) the variables/levels
  catalog reloads as before. GFS 0.25° declares explicit global
  coverage `(-90, 90, -180, 180)` and an empty source allowlist, so
  visible behaviour is unchanged — the wiring is for adding HRRR, NAM,
  etc. without touching the form.
- **Architecture + contributor docs.** New `docs/ARCHITECTURE.md`
  explains the product-agnostic core (batch orchestrator, priority,
  spread queue, wgrib2 wrappers, WebUI framework) vs. product-specific
  adapters (source `build_url()`, catalog JSON, cloud handler). New
  `docs/ADDING_A_PRODUCT.md` is a worked HRRR example walking through
  the five files a contributor touches.
- **Public positioning: "GRIB cropper — GFS today."** `README.md` and
  `site/index.html` now describe sharktopus as a generic cloud-native
  GRIB2 cropper whose core is product-agnostic, with GFS 0.25° as the
  shipped product and HRRR / NAM / RAP / ECMWF open-data as roadmap.
  Rationale: the library's value proposition (crop-before-download,
  multi-cloud, free-tier friendly) generalises beyond GFS; narrowing
  to "GFS cropper" under-sells the architecture and discourages
  contributors from adding adjacent products.

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

## [0.1.0] — 2026-04-18 (tag) / 2026-04-23 (PyPI release)

First public release on PyPI: <https://pypi.org/project/sharktopus/0.1.0/>.
The wheel is manylinux_2_27_x86_64 / manylinux_2_28_x86_64 (built inside
`quay.io/pypa/manylinux_2_28_x86_64` with `wgrib2` bundled). The tag was
cut on 2026-04-18; the release to PyPI happened on 2026-04-23 after the
multi-platform wheel pipeline landed.


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
