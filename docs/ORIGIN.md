# Origin — mapping to CONVECT

`sharktopus` is extracted from the **CONVECT** project's GFS fetcher
(`containers/fetcher/scripts/`). Every utility ported into
`sharktopus.grib` has a direct origin in the CONVECT codebase; this
document maps each function so contributors can check behaviour against
the battle-tested scripts.

## Layer 0 — `sharktopus.grib`

| sharktopus function | CONVECT source(s) | Notes |
|---|---|---|
| `verify(path)` | `run_verification` in `download_nomades_gfs_0p25.py:79` and `download_aws_gfs_0p25_full4.py:79`; `verify_grib` in `download_gcloud_gfs_0p25.py:198` and `download_azure_gfs_0p25.py:145`; `_verify_grib` in `download_rda_gfs.py:81` | All four variants return the number of output lines from `wgrib2 -s`; ported semantics = `returncode==0 ? line_count : None`. |
| `crop(src, dst, bbox)` | `crop_grib` in `download_gcloud_gfs_0p25.py:186` and `download_azure_gfs_0p25.py:151`; `_crop_region` in `download_rda_gfs.py:266` | All wrap `wgrib2 -small_grib {lon_w}:{lon_e} {lat_s}:{lat_n}`. |
| `filter_vars_levels(src, dst, vars, levels)` | `filter_grib_by_vars_levels` in `download_nomades_gfs_0p25.py:389` and `download_aws_gfs_0p25_full4.py:96`; `filter_and_small_grib` (combined) in `download_aws_gfs_0p25_full4.py:119` | Uses two `wgrib2 -match` filters in sequence. The combined variant lives in the `aws_s3` source, not Layer 0. |
| `parse_idx(text)` | `read_idx` in `download_aws_gfs_0p25_full4.py` (byte-range block), `download_gcloud_gfs_0p25.py:69`, `download_azure_gfs_0p25.py:63` | All three read the same `record:offset:date:var:level:forecast` format. Ported as a pure parser (no HTTP). |
| `byte_ranges(records, wanted, total_size)` | `compute_ranges` in `download_aws_gfs_0p25_full4.py:112`, `download_gcloud_gfs_0p25.py:112`, `download_azure_gfs_0p25.py:90` | All three share the same "compute + consolidate adjacent" logic. Ported as a pure function of the parsed records. |
| `rename_by_validity(path)` | `create_link_abs`, `create_link_rel` in `download_nomades_gfs_0p25.py:128` and `:182`; `rename_grib` in `download_aws_gfs_0p25_full4.py:204` | All call `wgrib2 -v`, regex the validity date and forecast hour, and produce `gfs.0p25.{YYYYMMDDHH}.f{PPP}.grib2`. Ported as a renamer (not a symlinker) — source scripts differ in whether they symlink or rename; consumer chooses. |

## Constants moved

The two large constants `VARIABLES` and `LEVELS` (hard-coded in each CONVECT
script with identical content modulo escaping) are **not** part of Layer 0.
They belong to the NOMADS / AWS / GCloud / Azure *sources* (Layer 1), because
they describe what each source is expected to deliver for WRF input, not a
general GRIB2 fact. Layer 0's `filter_vars_levels` takes them as parameters.

## Layer 1 — `sharktopus.sources.*`

### `sharktopus.sources.nomads`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` | `url_base` in `download_nomades_gfs_0p25.py::download_global` (line 278) |
| `build_url(date, cycle, fxx)` | URL composition in the same function |
| `fetch_step(...)` | Consolidation of `download_global` + retry loop from `download_file_with_progress` (line 25) |
| 10-day retention guard | Same check exists in `download_nomads_filter.py:106-112` — generalized here via `base.check_retention` |

### `sharktopus.sources.nomads_filter`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` / `BASE_URL_1HR` | `url_base` in `download_nomades_gfs_0p25.py::download_recorte` (line 349) and `BASE_URL` in `download_nomads_filter.py:150` |
| `level_to_param()` | The `&lev_{key}=on` encoding scattered across both CONVECT scripts (e.g. `download_nomades_gfs_0p25.py:329-339`) |
| `build_url(...)` | `query_static + query_levels + query_spatial` block in `download_nomades_gfs_0p25.py:325-362` |
| `fetch_step(...)` | `download_recorte` orchestration, but dropping the hardcoded variable/level set (caller's responsibility) |

### `sharktopus.sources.aws`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` | `f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/..."` in `download_aws_gfs_0p25_full4.py::download_global` (line 279) |
| `build_url(date, cycle, fxx)` | URL composition in the same function |
| `fetch_step(...)` | Simplified from `download_global` + `download_file_with_progress` — dropping the `s5cmd`/`aws s3 cp` path (we use plain HTTPS so there are no AWS-CLI deps) and the hardcoded variable/level filter (caller passes `bbox` for geographic crop only). |
| `DEFAULT_MAX_WORKERS = 4` | Matches the outer-loop `max_workers=2` in `download_aws_gfs_0p25_full4.py:277`, relaxed to 4 after verifying S3 absorbs it without 429s |

### `sharktopus.sources.gcloud`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` / `BUCKET` | `GFS_BUCKET = "global-forecast-system"` in `download_gcloud_gfs_0p25.py:43` |
| `build_url(date, cycle, fxx)` | URL composition inline in `download_gcloud_gfs_0p25.py::read_idx` (line 82) |
| `fetch_step(...)` | Simplified from the byte-range download + crop flow — we download the full file instead of assembling HTTP ranges, so the whole `.idx`-driven byte-range block is not needed here. |
| `DEFAULT_MAX_WORKERS = 4` | Matches `max_workers=2` in `download_gcloud_gfs_0p25.py:307`; GCS absorbs 4 easily |

### `sharktopus.sources.azure`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` / `CONTAINER` | `AZURE_BASE_URL = "https://noaagfs.blob.core.windows.net/gfs"` in `download_azure_gfs_0p25.py:37` |
| `build_url(date, cycle, fxx)` | URL composition inline in `download_azure_gfs_0p25.py::read_idx` (line 66) |
| `fetch_step(...)` | Simplified from the byte-range download + crop flow (same reasoning as `gcloud`) |
| `DEFAULT_MAX_WORKERS = 4` | Matches `max_workers=2` in `download_azure_gfs_0p25.py:205` |

### `sharktopus.sources.rda`

| sharktopus symbol | CONVECT source |
|---|---|
| `BASE_URL` / `DATASET` | `"https://data.rda.ucar.edu/d084001"` in `download_rda_gfs.py::_download_025` (line 149) |
| `EARLIEST` | `THRESHOLD_025 = datetime(2015, 1, 15, 0)` at `download_rda_gfs.py:55` |
| `rda_filename(date, cycle, fxx)` | `nome = f"gfs.0p25.{date_str}{hora}.f{step:03d}.grib2"` at line 148 |
| `build_url(date, cycle, fxx)` | URL composition at line 149 |
| `fetch_step(...)` | Simplified from `_download_025` + `_download_file` + `_crop_region`; drops the FNL 1° fallback (that's a different dataset — consumers who want it can implement `sharktopus.sources.rda_fnl`). |
| `DEFAULT_MAX_WORKERS = 1` | The CONVECT script never parallelised RDA — academic infra throttles aggressive anonymous callers |
| `$SHARKTOPUS_RDA_COOKIE` | New — CONVECT relied on system-level `wget` cookies; we pass the header explicitly for reproducibility |

### `sharktopus.sources._common`

| sharktopus symbol | CONVECT origin |
|---|---|
| `download_and_crop(url, final, ...)` | New helper — consolidates the full-file + local-crop + verify pattern that repeats verbatim in `download_nomades_gfs_0p25.py`, `download_aws_gfs_0p25_full4.py::download_global`, `download_gcloud_gfs_0p25.py::download_step`, `download_azure_gfs_0p25.py::download_step`, and `download_rda_gfs.py::_download_025`. |

### `sharktopus.sources.base`

| sharktopus symbol | CONVECT origin |
|---|---|
| `SourceUnavailable` | New — CONVECT scripts `sys.exit(1)` or raise generic `RuntimeError`; we need a typed exception so Layer 2 can fall back |
| `stream_download()` | Simplified stdlib port of `download_file_with_progress` variants (used both `wget` and `requests`; we use `urllib.request`) |
| `check_retention()` | Generalized from the hard-coded 10-day check in `download_nomads_filter.py:106-112` |
| `canonical_filename()` | `f"gfs.t{ref}z.pgrb2.0p25.f{prog:03d}"` appears identically in every CONVECT download script |

## WRF buffer / bbox padding

CONVECT's download scripts hard-code a 5° buffer around the user's bbox
before calling the filter endpoint or `wgrib2 -small_grib`:
`download_nomades_gfs_0p25.py:343-346`, `download_aws_gfs_0p25_full4.py:245-248`,
`download_rda_gfs.py:284` (all `margin = 5` or `+5`/`-5`). That margin is
a safety net for WRF's WPS / metgrid, which interpolates GFS onto the
WRF grid and needs data slightly outside the WRF outer domain.

`sharktopus` exposes this explicitly:

- `grib.expand_bbox(bbox, pad_lon, pad_lat)` is the pure helper.
- Every source that takes a *bbox* also takes `pad_lon` and `pad_lat`
  (independent, default 2° each = 8 grid cells at 0.25°, the minimum
  we consider WRF-safe). CONVECT's 5°-everywhere is still reachable
  via `pad_lon=5, pad_lat=5`.

## wgrib2 binary

CONVECT ships its own `wgrib2` binary under `images/azure_gfs/wgrib2`
(compiled by `images/azure_gfs/build_wgrib2.sh`) with optional features
disabled so it depends only on base-system libs. `sharktopus` reuses
the same recipe in `scripts/build_wgrib2.sh` and bundles the resulting
binary inside the platform wheel via a Hatchling build hook
(`hatch_build.py`). Resolution at runtime is centralised in
`sharktopus._wgrib2` (explicit arg → `$SHARKTOPUS_WGRIB2` → bundled →
`$PATH`); all Layer 0 / Layer 1 functions call through it.

## Intentional differences

- **`bbox` convention.** CONVECT passes `lat_s, lat_n, lon_w, lon_e` as four
  separate floats. `sharktopus` uses a single 4-tuple `(lon_w, lon_e, lat_s, lat_n)`
  matching Herbie and `wgrib2 -small_grib`'s own ordering.
- **Errors are exceptions.** CONVECT variants sometimes return `-1` or `None`
  on failure. `sharktopus` raises `GribError` (subclass of `RuntimeError`) so
  callers can't silently consume a failed verify/crop.
- **No hidden I/O.** `parse_idx` is a pure function (takes text, returns a
  list of records). CONVECT's `read_idx` mixes HTTP + filtering; those get
  rebuilt at the sources layer on top of `parse_idx`.
- **No hardcoded variable/level set in Layer 1.** Every CONVECT script
  carries a private copy of the 13-variable / 48-level WRF-input set.
  `sharktopus` pushes that decision to the caller: `nomads_filter.fetch_step`
  accepts `variables=` and `levels=` as required arguments. This keeps the
  library useful for workflows other than WRF.
- **Stdlib-only networking.** CONVECT uses `wget` (in `nomades`) and
  `requests` (in `nomads_filter`). The library uses `urllib.request` so
  the base install has zero runtime dependencies.
- **Full download, not byte-range.** CONVECT's `aws` / `gcloud` /
  `azure` scripts fetch individual variable/level records via HTTP
  range requests driven by the `.idx`. `sharktopus` instead downloads
  the full ~500 MB file and crops locally with `wgrib2 -small_grib`.
  The full-file approach is simpler, uses one HTTP connection per
  step (friendlier to mirrors), and lets the same `bbox` → crop flow
  apply regardless of source. Byte-range fetching remains possible in
  `sharktopus.grib.byte_ranges` / `parse_idx` for users who need it
  (e.g. fetching two variables from 40 years of archive), but is not
  the default source-layer path.
- **Step-level parallelism, per-source defaults.** CONVECT hard-codes
  `max_workers=2` in every source. `sharktopus` publishes a
  `DEFAULT_MAX_WORKERS` per source that reflects each mirror's
  observed throttle threshold (NOMADS 2, cloud 4, RDA 1), and
  `fetch_batch` caps pool size to `min(...)` across the priority
  list so a mixed priority is paced by its weakest mirror.
