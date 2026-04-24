"""FastAPI application factory.

``build_app`` returns a configured :class:`fastapi.FastAPI` instance
with routes, static mounts, templating, and the on-startup schema
bootstrap already wired. Kept as a factory so tests can construct an
isolated app and run it with ``TestClient`` without touching a real
``~/.cache/sharktopus/webui.db``.
"""

from __future__ import annotations

from . import db, paths


def build_app():
    """Construct the FastAPI app. Raises ImportError when extras missing."""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from .routes import api, pages
    from .templating import get_templates

    # Ensure the DB is ready on every import path that builds the app,
    # so TestClient-only flows work the same as the uvicorn boot path.
    db.init_schema()

    app = FastAPI(
        title="sharktopus",
        description="Local web UI for the sharktopus GRIB cropper.",
        version=_read_version(),
        docs_url=None,  # we serve our own /help page
        redoc_url=None,
    )

    app.mount(
        "/static",
        StaticFiles(directory=str(paths.static_dir())),
        name="static",
    )

    # Stash the templates on the app so routes can fetch it without
    # re-importing paths.
    app.state.templates = get_templates()

    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")

    return app


def _read_version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:
        return "0.0.0"
