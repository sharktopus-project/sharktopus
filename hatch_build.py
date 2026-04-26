"""Hatch build hook: tag the wheel platform-specific when wgrib2 is bundled.

When ``src/sharktopus/_bin/wgrib2`` (or ``wgrib2.exe``) is present at wheel
build time, this hook tags the wheel ``py3-none-<platform>`` (e.g.
``py3-none-linux_x86_64``) so every supported CPython minor version can
install it. CI post-processes the tag with ``auditwheel`` /
``delocate-wheel`` to promote it to the appropriate ``manylinux`` /
``macosx`` variant before publishing.

When the bundle dir is empty (sdist / source install) the wheel stays
``py3-none-any`` and users must bring their own wgrib2.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _macos_single_arch(bin_path: Path) -> str | None:
    """Return 'arm64'/'x86_64' for a single-arch Mach-O, else None.

    Universal (fat) binaries return None — we keep the universal2 tag
    so delocate validates every arch slice.
    """
    try:
        out = subprocess.check_output(
            ["lipo", "-archs", str(bin_path)],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    archs = out.split()
    return archs[0] if len(archs) == 1 else None


def _platform_tag(bin_path: Path | None = None) -> str:
    # e.g. "linux-x86_64" → "linux_x86_64"; "macosx-14.0-arm64" → same
    plat = sysconfig.get_platform()
    # setup-python's macOS Pythons are universal2 fat builds, so
    # sysconfig.get_platform() reports "macosx-X.Y-universal2" even on
    # a single-arch host. delocate-wheel >= 0.13 trusts the wheel tag
    # and demands every advertised arch be present in every bundled
    # binary, so a natively-built (arm64-only) wgrib2 trips
    # "Failed to find any binary with the required architecture:
    # 'x86_64'". Narrow the tag to the binary's actual arch.
    if (
        sys.platform == "darwin"
        and "universal2" in plat
        and bin_path is not None
    ):
        arch = _macos_single_arch(bin_path)
        if arch is not None:
            plat = plat.replace("universal2", arch)
    return plat.replace("-", "_").replace(".", "_")


class BundledBinaryHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        bin_dir = Path(self.root) / "src" / "sharktopus" / "_bin"
        if not bin_dir.is_dir():
            return
        binaries = [
            p for p in bin_dir.iterdir()
            if p.is_file() and p.name.startswith("wgrib2") and p.suffix != ".md"
        ]
        if not binaries:
            return
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{_platform_tag(binaries[0])}"
