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
