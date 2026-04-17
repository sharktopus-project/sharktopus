# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
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
