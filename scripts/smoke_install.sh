#!/usr/bin/env bash
# Build the sharktopus wheel and run the clean-host smoke test.
#
# End-to-end:
#   1. Ensure the wheel includes the bundled wgrib2 binary
#   2. Rebuild the wheel from source (optional — skip with --no-rebuild)
#   3. Build a minimal Ubuntu 24.04 image with only python + the wheel
#   4. Run a handful of CLI assertions (see smoke_checks.sh)
#
# Exit code mirrors the container: 0 = all good, non-zero = a check
# failed (the container's stdout shows which one).
#
# Usage:
#   scripts/smoke_install.sh              # full rebuild + smoke
#   scripts/smoke_install.sh --no-rebuild # reuse dist/*.whl

set -euo pipefail

cd "$(dirname "$0")/.."

REBUILD=1
if [[ "${1:-}" == "--no-rebuild" ]]; then
    REBUILD=0
fi

IMAGE=sharktopus-smoke:local
WHEEL_DIR=dist

if [[ ! -x src/sharktopus/_bin/wgrib2 ]]; then
    echo "[*] bundling wgrib2 into src/sharktopus/_bin/ ..."
    scripts/bundle_wgrib2.sh
fi

# Prefer the project venv if present — host Python may be
# PEP-668-protected (Debian/Ubuntu ≥ 23.04 refuse pip install outside a venv).
if [[ -x .venv/bin/python ]]; then
    PY=.venv/bin/python
else
    PY=python3
fi

if [[ $REBUILD -eq 1 ]]; then
    echo "[*] rebuilding wheel into $WHEEL_DIR/ (using $PY) ..."
    rm -f "$WHEEL_DIR"/sharktopus-*.whl "$WHEEL_DIR"/sharktopus-*.tar.gz
    "$PY" -m pip install -q --upgrade build
    "$PY" -m build --wheel --sdist
fi

WHEEL=$(ls -1 "$WHEEL_DIR"/sharktopus-*.whl | head -n1)
if [[ -z "$WHEEL" ]]; then
    echo "no wheel found in $WHEEL_DIR/ — run without --no-rebuild" >&2
    exit 1
fi
echo "[*] wheel: $WHEEL ($(du -h "$WHEEL" | awk '{print $1}'))"

echo "[*] building smoke image ($IMAGE) ..."
docker build \
    -f scripts/Dockerfile.smoke \
    -t "$IMAGE" \
    --quiet \
    . > /dev/null

echo "[*] running smoke checks ..."
docker run --rm "$IMAGE"

echo
echo "[OK] clean-install smoke passed — wheel is safe to upload"
