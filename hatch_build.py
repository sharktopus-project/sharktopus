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

import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _platform_tag() -> str:
    # e.g. "linux-x86_64" → "linux_x86_64"; "macosx-14.0-arm64" → same
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


class BundledBinaryHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        bin_dir = Path(self.root) / "src" / "sharktopus" / "_bin"
        if not bin_dir.is_dir():
            return
        has_binary = any(
            p.is_file() and p.name.startswith("wgrib2") and p.suffix != ".md"
            for p in bin_dir.iterdir()
        )
        if not has_binary:
            return
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{_platform_tag()}"
