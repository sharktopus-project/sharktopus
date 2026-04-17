"""Tests for sharktopus._wgrib2 (binary resolver)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sharktopus import _wgrib2
from sharktopus._wgrib2 import (
    WgribNotFoundError,
    bundled_wgrib2,
    ensure_wgrib2,
    resolve_wgrib2,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Each test starts with no $SHARKTOPUS_WGRIB2 and a fresh which cache."""
    monkeypatch.delenv("SHARKTOPUS_WGRIB2", raising=False)
    clear = getattr(_wgrib2._which_cached, "cache_clear", None)
    if clear:
        clear()
    yield
    clear = getattr(_wgrib2._which_cached, "cache_clear", None)
    if clear:
        clear()


def _make_fake_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_explicit_absolute_path_used_when_it_exists(tmp_path):
    fake = _make_fake_exe(tmp_path / "my_wgrib2")
    assert resolve_wgrib2(fake) == str(fake)


def test_explicit_absolute_missing_returns_none(tmp_path):
    assert resolve_wgrib2(tmp_path / "does-not-exist") is None


def test_explicit_relative_path_missing_returns_none(tmp_path):
    # Path-like strings (contain '/') must exist; no PATH fallback.
    assert resolve_wgrib2("./does-not-exist") is None


def test_env_var_overrides_path(tmp_path, monkeypatch):
    fake = _make_fake_exe(tmp_path / "envwgrib2")
    monkeypatch.setenv("SHARKTOPUS_WGRIB2", str(fake))
    assert resolve_wgrib2() == str(fake)


def test_env_var_ignored_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARKTOPUS_WGRIB2", str(tmp_path / "nope"))
    # Should fall through to bundled / PATH.
    result = resolve_wgrib2()
    assert result != str(tmp_path / "nope")


def test_bundled_binary_used_when_present(tmp_path, monkeypatch):
    bundled = _make_fake_exe(tmp_path / "_bin" / "wgrib2")
    monkeypatch.setattr(_wgrib2, "BUNDLED_BIN_DIR", bundled.parent)
    # No env, no explicit: bundled should win over any $PATH wgrib2 too.
    assert resolve_wgrib2() == str(bundled)


def test_bundled_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_wgrib2, "BUNDLED_BIN_DIR", tmp_path / "empty")
    assert bundled_wgrib2() is None


def test_resolution_order_explicit_beats_env(tmp_path, monkeypatch):
    explicit = _make_fake_exe(tmp_path / "explicit")
    env_exe = _make_fake_exe(tmp_path / "env")
    monkeypatch.setenv("SHARKTOPUS_WGRIB2", str(env_exe))
    assert resolve_wgrib2(explicit) == str(explicit)


def test_ensure_raises_with_helpful_message(tmp_path, monkeypatch):
    # Force every lookup to fail.
    monkeypatch.setattr(_wgrib2, "BUNDLED_BIN_DIR", tmp_path / "empty")
    monkeypatch.setattr(_wgrib2, "_which_cached", lambda name: None)
    with pytest.raises(WgribNotFoundError, match="conda install"):
        ensure_wgrib2()


def test_bundled_binary_gets_chmod_if_missing(tmp_path, monkeypatch):
    """Wheels may ship a binary without +x (e.g. zip on Windows → Linux);
    bundled_wgrib2() should make it executable on the fly."""
    exe = tmp_path / "_bin" / "wgrib2"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\nexit 0\n")
    # Explicitly strip exec bits.
    exe.chmod(0o644)
    monkeypatch.setattr(_wgrib2, "BUNDLED_BIN_DIR", exe.parent)
    out = bundled_wgrib2()
    assert out == exe
    assert os.access(out, os.X_OK)
