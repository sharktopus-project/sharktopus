"""Smoke tests for the ``--ui`` flag on the sharktopus CLI."""
from __future__ import annotations

import sys
import types


def test_cli_help_mentions_ui(capsys):
    from sharktopus import cli
    try:
        cli.main(["--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "--ui" in out
    assert "--ui-port" in out
    assert "--ui-host" in out


def test_cli_ui_flag_dispatches_to_start_server(monkeypatch):
    """Without FastAPI installed --ui should exit cleanly with guidance."""
    calls: list[dict] = []

    stub = types.SimpleNamespace()

    def fake_start(**kwargs):
        calls.append(kwargs)
        return 0

    stub.start_server = fake_start

    # Prime the sharktopus package so the CLI's `from . import webui`
    # returns our stub, not the real module.
    import sharktopus
    monkeypatch.setattr(sharktopus, "webui", stub, raising=False)
    sys.modules["sharktopus.webui"] = stub

    try:
        from sharktopus import cli
        rc = cli.main(["--ui", "--ui-port", "9999", "--ui-no-browser"])
    finally:
        sys.modules.pop("sharktopus.webui", None)

    assert rc == 0
    assert calls == [{"host": "127.0.0.1", "port": 9999, "open_browser": False}]
