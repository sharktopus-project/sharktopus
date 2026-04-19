# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

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
  Phase 3b also runs the **WRF-canonical selection** (13 vars × 48
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
