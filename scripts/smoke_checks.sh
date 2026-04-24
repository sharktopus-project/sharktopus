#!/usr/bin/env bash
# Runs inside the smoke image. Each check either prints a pass line or
# exits non-zero. Kept tiny and offline so it works without credentials
# or network — the goal is to catch packaging regressions, not to
# validate the full pipeline.

set -euo pipefail

pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; exit 1; }

echo "[1/6] sharktopus --help"
sharktopus --help > /dev/null || fail "entry point broken"
pass "CLI help runs"

echo "[2/6] sharktopus --list-sources"
sharktopus --list-sources | grep -q "aws_crop" || fail "sources registry missing aws_crop"
pass "sources registered ($(sharktopus --list-sources | wc -l) entries)"

echo "[3/6] import sharktopus"
python -c "import sharktopus; print(sharktopus.__name__)" > /dev/null || fail "package import failed"
pass "package imports cleanly"

echo "[4/6] bundled wgrib2 resolves + runs"
python - <<'PY' || fail "wgrib2 bundle broken"
from sharktopus.io.wgrib2 import resolve_wgrib2
import subprocess, sys
p = resolve_wgrib2()
if not p:
    print("    resolve_wgrib2() returned None — binary not bundled or not on PATH")
    sys.exit(1)
# wgrib2 -version writes the banner then returns 8 by design; we only
# care that the binary loaded (no missing .so) and produced something
# that looks like a version string.
out = subprocess.run([p, "-version"], capture_output=True, text=True)
text = out.stdout + out.stderr
if "v" not in text.split(None, 1)[0]:
    print(f"    unexpected -version output: {text!r}")
    sys.exit(1)
print(f"    wgrib2 path: {p}")
print(f"    wgrib2 version: {text.splitlines()[0]}")
PY
pass "wgrib2 bundle runs on this glibc"

echo "[5/6] [ui] extra import chain"
python -c "import fastapi, uvicorn, jinja2; import sharktopus.webui" > /dev/null || fail "UI extra broken"
pass "[ui] extra imports"

echo "[6/6] sharktopus --availability (offline metadata only)"
sharktopus --availability 20260101 2>/dev/null | head -n 3 > /dev/null || fail "availability CLI failed"
pass "availability CLI runs"

echo
echo "all smoke checks passed"
