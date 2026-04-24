#!/usr/bin/env bash
# Build a sharktopus wheel with wgrib2 bundled inside for the host platform.
#
# The binary is *not* compiled here. This script materialises a
# platform-appropriate wgrib2 binary into src/sharktopus/_bin/ from one
# of the supported sources, then runs `python -m build --wheel`. The
# hatch build hook (see hatch_build.py) detects the binary and tags the
# wheel platform-specific.
#
# Binary sources (first match wins):
#   1. $SHARKTOPUS_WGRIB2_SRC — path to an existing wgrib2 binary.
#   2. $SHARKTOPUS_WGRIB2_URL — URL to download (raw binary, not an archive).
#   3. Default local-dev fallback: CONVECT's bundled wgrib2 at
#      ~/CONVECT/images/azure_gfs/wgrib2 (present on the maintainer's
#      laptop; CI uses $SHARKTOPUS_WGRIB2_URL pointing at a GH release
#      asset).
#
# The binary must only depend on base-system libraries (libc, libm,
# libgfortran, libgomp, libpthread, libgcc_s). Binaries with extra deps
# — notably conda-forge's wgrib2, which needs libjasper/libnetcdf/
# libmysqlclient — are NOT portable across machines and will be
# rejected here.
#
# Linux post-processing: if auditwheel is available we run
# `auditwheel repair` to vendor libgfortran into the wheel and promote
# the tag to manylinux_2_28. macOS: delocate-wheel does the equivalent.
#
# Usage:
#   ./scripts/bundle_wgrib2.sh
#
# Output: dist/sharktopus-<version>-py3-none-<plat>.whl

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
bin_dir="$repo_root/src/sharktopus/_bin"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  EXE_NAME=wgrib2 ;;
    Linux-aarch64) EXE_NAME=wgrib2 ;;
    Darwin-x86_64) EXE_NAME=wgrib2 ;;
    Darwin-arm64)  EXE_NAME=wgrib2 ;;
    *) echo "unsupported host: $(uname -s)-$(uname -m)" >&2; exit 2 ;;
esac

mkdir -p "$bin_dir"
dest="$bin_dir/$EXE_NAME"

if [ -n "${SHARKTOPUS_WGRIB2_SRC:-}" ]; then
    echo ">>> copying wgrib2 from \$SHARKTOPUS_WGRIB2_SRC=$SHARKTOPUS_WGRIB2_SRC"
    cp "$SHARKTOPUS_WGRIB2_SRC" "$dest"
elif [ -n "${SHARKTOPUS_WGRIB2_URL:-}" ]; then
    echo ">>> downloading wgrib2 from \$SHARKTOPUS_WGRIB2_URL"
    curl -L --fail --silent --show-error -o "$dest" "$SHARKTOPUS_WGRIB2_URL"
elif [ -x "$HOME/CONVECT/images/azure_gfs/wgrib2" ]; then
    echo ">>> using CONVECT's bundled wgrib2 (local-dev fallback)"
    cp "$HOME/CONVECT/images/azure_gfs/wgrib2" "$dest"
else
    echo "no wgrib2 source found. Set \$SHARKTOPUS_WGRIB2_SRC or \$SHARKTOPUS_WGRIB2_URL." >&2
    exit 3
fi

chmod +x "$dest"
file "$dest"

echo ">>> smoke test"
# wgrib2 -version exits non-zero; we only care that it runs and prints.
"$dest" -version 2>&1 | head -1 || true

if [ "$(uname -s)" = "Linux" ]; then
    echo ">>> ldd (portability check)"
    # Only base-system libs are allowed; anything else will fail at
    # wheel install on users' machines.
    bad_deps="$(ldd "$dest" | awk '/=>/{print $1}' | grep -vE '^(libc|libm|libmvec|libgfortran|libgomp|libpthread|libgcc_s|libdl|libquadmath|libz|linux-vdso)' || true)"
    if [ -n "$bad_deps" ]; then
        echo "wgrib2 depends on libs that are not manylinux-safe:" >&2
        echo "$bad_deps" >&2
        echo "use a statically-built wgrib2 or one linked only against base-system libs." >&2
        exit 4
    fi
fi

echo ">>> building wheel"
cd "$repo_root"
rm -rf build/ dist/
python -m build --wheel

echo ">>> wheel:"
ls -l dist/

if [ "$(uname -s)" = "Linux" ] && command -v auditwheel >/dev/null 2>&1; then
    # Default to x86_64; CI overrides via $SHARKTOPUS_MANYLINUX_PLAT
    # (e.g. manylinux_2_28_aarch64 on ARM runners).
    plat="${SHARKTOPUS_MANYLINUX_PLAT:-manylinux_2_28_x86_64}"
    echo ">>> auditwheel repair (plat=$plat, vendor libgfortran)"
    # On a dev laptop this usually fails because the host glibc is newer
    # than the manylinux tag — that's expected, CI builds the release
    # wheel inside a manylinux_2_28 container. Don't crash the local flow.
    if auditwheel repair \
        --plat "$plat" \
        --wheel-dir dist/ \
        dist/sharktopus-*-linux_*.whl
    then
        rm -f dist/sharktopus-*-linux_*.whl
        echo ">>> repaired wheel:"
        ls -l dist/
    else
        echo ">>> auditwheel repair skipped (likely host glibc > manylinux_2_28)."
        echo ">>> the linux wheel above is usable locally; release wheels come from CI."
    fi
fi
