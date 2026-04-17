"""Tests for sharktopus.cli (argparse + config merging)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from sharktopus import batch, cli
from sharktopus.sources import SourceUnavailable


@pytest.fixture
def stub_registry(monkeypatch, tmp_path):
    """Swap the batch registry with a deterministic stub for CLI tests."""
    calls: list = []

    def stub_fetch(date, cycle, fxx, **kwargs):
        calls.append({"date": date, "cycle": cycle, "fxx": fxx, **kwargs})
        p = tmp_path / f"stub.{date}{cycle}.f{fxx:03d}.grib2"
        p.write_bytes(b"GRIB")
        return p

    orig = dict(batch._REGISTRY)
    batch._REGISTRY.clear()
    batch._REGISTRY["nomads"] = stub_fetch
    batch._REGISTRY["nomads_filter"] = stub_fetch
    try:
        yield calls
    finally:
        batch._REGISTRY.clear()
        batch._REGISTRY.update(orig)


def test_cli_flags_only(stub_registry, capsys):
    rc = cli.main([
        "--start", "2024010200", "--end", "2024010206", "--step", "6",
        "--ext", "0", "--interval", "3",
        "--lat-s", "-10", "--lat-n", "0", "--lon-w", "-50", "--lon-e", "-40",
        "--priority", "nomads",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    # Two cycles × one step (fxx=0) = 2 lines on stdout
    assert len(captured.out.strip().splitlines()) == 2
    # Each call used the right bbox
    for call in stub_registry:
        assert call["bbox"] == (-50.0, -40.0, -10.0, 0.0)


def test_cli_reads_config_file(tmp_path, stub_registry, capsys):
    cfg = tmp_path / "run.ini"
    cfg.write_text(dedent("""
        [gfs]
        timestamps = 2024010200
        ext = 0
        interval = 3
        lat_s = -10
        lat_n = 0
        lon_w = -50
        lon_e = -40
        priority = nomads
    """).lstrip())
    rc = cli.main(["--config", str(cfg)])
    assert rc == 0
    assert len(stub_registry) == 1
    assert stub_registry[0]["date"] == "20240102"
    assert stub_registry[0]["cycle"] == "00"


def test_cli_flag_overrides_config(tmp_path, stub_registry):
    cfg = tmp_path / "run.ini"
    cfg.write_text(dedent("""
        [gfs]
        timestamps = 2024010200
        ext = 0
        interval = 3
        lat_s = -10
        lat_n = 0
        lon_w = -50
        lon_e = -40
        priority = nomads_filter
        variables = TMP
        levels = 500 mb
    """).lstrip())
    # Override priority → nomads (no vars/levels needed)
    cli.main(["--config", str(cfg), "--priority", "nomads"])
    assert all(c.get("variables") != ["TMP"] for c in stub_registry)


def test_cli_missing_bbox_errors(stub_registry):
    with pytest.raises(SystemExit, match="bbox"):
        cli.main([
            "--timestamps", "2024010200",
            "--ext", "0", "--interval", "3",
            # no --lat-s etc.
        ])


def test_cli_missing_dates_errors(stub_registry):
    with pytest.raises(SystemExit, match="--timestamps"):
        cli.main([
            "--lat-s", "-10", "--lat-n", "0", "--lon-w", "-50", "--lon-e", "-40",
        ])


def test_cli_variables_levels_forwarded(tmp_path, stub_registry):
    cli.main([
        "--timestamps", "2024010200",
        "--ext", "0", "--interval", "3",
        "--lat-s", "-10", "--lat-n", "0", "--lon-w", "-50", "--lon-e", "-40",
        "--priority", "nomads_filter",
        "--vars", "TMP", "UGRD",
        "--levels", "500 mb", "850 mb", "surface",
    ])
    call = stub_registry[0]
    assert call["variables"] == ["TMP", "UGRD"]
    assert call["levels"] == ["500 mb", "850 mb", "surface"]
