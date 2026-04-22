"""Local web UI for sharktopus.

A self-contained FastAPI application that mirrors every CLI capability
in a browser. Everything ``sharktopus`` can do from the terminal is
also available here: submit jobs, browse the download inventory, track
cloud-quota usage, manage credentials, run the per-provider setup
wizards, edit source priority, and inspect logs.

Boot with::

    sharktopus --ui                # 127.0.0.1:8765
    sharktopus --ui --port 9000
    sharktopus --ui --host 0.0.0.0 # LAN-accessible (be careful)

Install requirements::

    pip install "sharktopus[ui]"

The UI imports FastAPI/uvicorn/Jinja2 lazily — users who stick to the
CLI never pay the dependency cost.
"""

from __future__ import annotations

__all__ = ["start_server", "create_app"]


def start_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    reload: bool = False,
    open_browser: bool = True,
) -> int:
    """Boot the sharktopus web UI and block until the process exits.

    Returns a process exit code (0 = clean shutdown). Import errors for
    FastAPI/uvicorn/Jinja2 are converted to a friendly message telling
    the user to install the ``[ui]`` extra instead of dumping a stack
    trace.
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        import sys

        print(
            "error: the sharktopus web UI requires extra packages.\n"
            '       install them with:  pip install "sharktopus[ui]"',
            file=sys.stderr,
        )
        return 2

    from .server import run as _run

    return _run(host=host, port=port, reload=reload, open_browser=open_browser)


def create_app():
    """Return a FastAPI application instance (for tests / programmatic use)."""
    from .app import build_app

    return build_app()
