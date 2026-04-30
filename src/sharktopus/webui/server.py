"""uvicorn boot + open-in-browser helper."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

__all__ = ["run"]


def _looks_headless() -> bool:
    """Return True when ``webbrowser.open`` is unlikely to do anything visible.

    On a desktop machine the URL is opened automatically — convenient. On
    a headless server (no DISPLAY / no $WAYLAND_DISPLAY / SSH session)
    ``webbrowser.open`` typically fails silently or launches a text-mode
    browser that never connects. We use this hint to escalate the banner
    so users in that environment can't miss the URL.
    """
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
        return True
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return True
    return False


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    reload: bool = False,
    open_browser: bool = True,
) -> int:
    """Block on the uvicorn server. Returns a process exit code."""
    import uvicorn

    requested_port = port
    port = _resolve_port(host, port)
    url = f"http://{host}:{port}/"
    _print_banner(host, port, url, fell_back=(port != requested_port))

    if open_browser and not _looks_headless():
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


def _resolve_port(host: str, preferred: int) -> int:
    """Return *preferred* if it's free, otherwise an OS-picked free port.

    We probe by binding a short-lived socket. There is a TOCTOU window
    between the probe and uvicorn's bind — another process could steal
    the port in between — but for single-user local dev that's fine.
    """
    if preferred and _port_is_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        return s.getsockname()[1]


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _print_banner(host: str, port: int, url: str, *, fell_back: bool = False) -> None:
    headless = _looks_headless()
    rule = "═" * 60
    lines = ["", rule, "  sharktopus web UI", "", f"    {url}", ""]
    if headless:
        lines += [
            "  No display detected (SSH or headless host).",
            "  Open the URL above in a browser on your local machine.",
            "  For SSH hosts, forward the port first:",
            f"      ssh -L {port}:localhost:{port} user@host",
        ]
    else:
        lines.append("  Opening this URL in your browser ...")
    if fell_back:
        lines += [
            "",
            "  (default port was busy — picked a free one above;",
            "   pass --ui-port N to override)",
        ]
    lines += ["", "  Ctrl-C to stop.", rule, ""]
    sys.stderr.write("\n".join(lines) + "\n")


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
