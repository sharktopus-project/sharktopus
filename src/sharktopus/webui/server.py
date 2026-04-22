"""uvicorn boot + open-in-browser helper."""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

__all__ = ["run"]


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    reload: bool = False,
    open_browser: bool = True,
) -> int:
    """Block on the uvicorn server. Returns a process exit code."""
    import uvicorn

    url = f"http://{host}:{port}/"
    _print_banner(host, port, url)

    if open_browser:
        _launch_browser(url)

    config = uvicorn.Config(
        "sharktopus.webui.app:build_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        access_log=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        return 0
    return 0 if server.started else 1


def _print_banner(host: str, port: int, url: str) -> None:
    sys.stderr.write(
        "\n"
        "  ┌──────────────────────────────────────────────┐\n"
        f"  │  sharktopus web UI — http://{host:<16} │\n".replace(host, f"{host}:{port}", 1)
        + f"  │  serving at:  {url:<33s}│\n"
        "  │  Ctrl-C to stop.                             │\n"
        "  └──────────────────────────────────────────────┘\n\n"
    )


def _launch_browser(url: str) -> None:
    """Open the browser after the server is actually listening."""

    def _wait_and_open() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(_split(url), timeout=0.25):
                    webbrowser.open(url, new=1, autoraise=True)
                    return
            except OSError:
                time.sleep(0.15)

    t = threading.Thread(target=_wait_and_open, daemon=True, name="sharktopus-open")
    t.start()


def _split(url: str) -> tuple[str, int]:
    # http://host:port/
    raw = url.split("://", 1)[-1].rstrip("/")
    host, _, port = raw.rpartition(":")
    return host or "127.0.0.1", int(port or "80")
