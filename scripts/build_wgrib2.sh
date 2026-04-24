#!/usr/bin/env bash
# Compile wgrib2 from NOAA's upstream source into a minimal, portable
# binary suitable for bundling in a sharktopus wheel.
#
# Rationale: we want a binary that depends only on base-system libs
# (libc, libm, libgfortran, libgomp, libpthread, libgcc_s) so it runs on
# any manylinux_2_28-compatible host. We disable the optional features
# (AEC, OpenJPEG, NetCDF3/4) that pull in heavier deps — sharktopus only
# uses -small_grib / -match / -Match_inv / -set_date / -for, which need
# none of them.
#
# Usage:
#   ./scripts/build_wgrib2.sh [OUTPUT_PATH]
#
#   OUTPUT_PATH defaults to src/sharktopus/_bin/wgrib2.
#
# Expected toolchain: gcc, gfortran, make, wget/curl, tar. In CI this
# runs inside the manylinux_2_28 container where all of those are
# already installed (or a one-line dnf away).
#
# The upstream tarball is fetched from CPC over HTTPS. Pin WGRIB2_TGZ to
# a snapshot URL if you need reproducibility across runs.

set -euo pipefail

WGRIB2_TGZ="${SHARKTOPUS_WGRIB2_TGZ_URL:-https://ftp.cpc.ncep.noaa.gov/wd51we/wgrib2/wgrib2.tgz}"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$repo_root/src/sharktopus/_bin/wgrib2}"
mkdir -p "$(dirname "$out")"

for cmd in "${CC:-gcc}" "${FC:-gfortran}" make tar; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "missing required tool: $cmd" >&2
        exit 2
    }
done
if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    echo "missing required tool: wget or curl" >&2
    exit 2
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
cd "$workdir"

echo ">>> fetching wgrib2 source from $WGRIB2_TGZ"
if command -v wget >/dev/null 2>&1; then
    wget -q "$WGRIB2_TGZ" -O wgrib2.tgz
else
    curl -L --fail --silent --show-error -o wgrib2.tgz "$WGRIB2_TGZ"
fi
tar -xzf wgrib2.tgz
cd grib2

echo ">>> disabling optional features (AEC, OpenJPEG, NetCDF)"
sed -i.bak "s/^USE_AEC=1/USE_AEC=0/"           makefile
sed -i.bak "s/^USE_OPENJPEG=1/USE_OPENJPEG=0/" makefile
sed -i.bak "s/^USE_NETCDF3=1/USE_NETCDF3=0/"   makefile
sed -i.bak "s/^USE_NETCDF4=1/USE_NETCDF4=0/"   makefile

# wgrib2 bundles proj-4.8.0, whose config.guess is from 2007 and fails
# on modern CPU targets ("cannot guess build type"). Observed on
# aarch64 Linux. Force-feed --build=<triplet> to proj's configure so
# its config.guess is never consulted.
host_arch="$(uname -m)"
case "$(uname -s)" in
    Linux)  build_triplet="${host_arch}-unknown-linux-gnu" ;;
    Darwin) build_triplet="${host_arch}-apple-darwin" ;;
    *)      build_triplet="${host_arch}-unknown-unknown" ;;
esac
sed -i.bak "s|\./configure --disable-shared|./configure --build=${build_triplet} --disable-shared|g" makefile

echo ">>> compiling"
# CC / FC can be overridden by the caller — on macOS we need to force
# the Homebrew gcc (gcc-15 etc.) instead of Apple's Clang-as-gcc.
: "${CC:=gcc}"
: "${FC:=gfortran}"
export CC FC
jobs="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)"
# wgrib2's makefile has a race in ip2lib_d: gdswzd_mod.mod is sometimes
# consumed before it's produced under `make -j`. When that happens,
# fall back to a serial pass — missing .mod files are built first and
# the rest picks up where it left off.
make -j"$jobs" || make -j1

echo ">>> stripping binary"
# macOS `strip` removes too much by default and can break the binary;
# use -x (strip local symbols only). GNU strip accepts -x too and is
# safe there.
strip -x wgrib2/wgrib2 2>/dev/null || strip wgrib2/wgrib2

cp wgrib2/wgrib2 "$out"
chmod +x "$out"

echo ">>> result:"
file "$out"
du -h "$out" | cut -f1

echo ">>> dynamic deps:"
if command -v ldd >/dev/null 2>&1; then
    ldd "$out" || true
elif command -v otool >/dev/null 2>&1; then
    otool -L "$out" || true
fi
