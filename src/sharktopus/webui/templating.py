"""Jinja2 template registry with a few project-wide globals.

Keeps the template boilerplate out of app.py so tests can pull the
same ``Jinja2Templates`` instance without standing up the whole app.
"""

from __future__ import annotations

import os

from . import paths


def _asset_version() -> str:
    """Newest mtime across css/js/templates — cache-bust string for static URLs."""
    roots = [paths.static_dir(), paths.templates_dir()]
    latest = 0.0
    for root in roots:
        for dirpath, _dirs, files in os.walk(str(root)):
            for fn in files:
                try:
                    mt = os.path.getmtime(os.path.join(dirpath, fn))
                    if mt > latest:
                        latest = mt
                except OSError:
                    continue
    return str(int(latest)) if latest else "0"


def get_templates():
    """Return the shared ``Jinja2Templates`` object."""
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(paths.templates_dir()))
    env = templates.env
    env.globals["site_name"] = "sharktopus"
    env.globals["nav_items"] = _NAV
    env.globals["asset_version"] = _asset_version
    env.filters["human_bytes"] = _human_bytes
    env.filters["short_num"] = _short_num
    return templates


_NAV = (
    {"path": "/",            "label": "Dashboard",   "icon": "home"},
    {"path": "/submit",      "label": "Submit",      "icon": "upload"},
    {"path": "/jobs",        "label": "Jobs",        "icon": "list"},
    {"path": "/inventory",   "label": "Inventory",   "icon": "archive"},
    {"path": "/quota",       "label": "Quota",       "icon": "gauge"},
    {"path": "/sources",     "label": "Sources",     "icon": "network"},
    {"path": "/setup",       "label": "Setup",       "icon": "wand"},
    {"path": "/credentials", "label": "Credentials", "icon": "key"},
    {"path": "/settings",    "label": "Settings",    "icon": "settings"},
    {"path": "/help",        "label": "Help",        "icon": "book"},
    {"path": "/about",       "label": "About",       "icon": "info"},
)


_INSTITUTIONS = (
    {"name": "CONVECT",
     "logo": "/static/img/institutions/cnpq.png",
     "href": "https://www.gov.br/cnpq/pt-br",
     "alt":  "CNPq — Chamada Eventos Extremos 15/2023"},
    {"name": "IEAPM",
     "logo": "/static/img/institutions/ieapm.png",
     "href": "https://www.marinha.mil.br/ieapm/",
     "alt":  "Instituto de Estudos do Mar Almirante Paulo Moreira"},
    {"name": "UENF",
     "logo": "/static/img/institutions/uenf.png",
     "href": "https://uenf.br/",
     "alt":  "Universidade Estadual do Norte Fluminense Darcy Ribeiro"},
    {"name": "UFPR",
     "logo": "/static/img/institutions/ufpr.svg",
     "href": "https://www.ufpr.br/",
     "alt":  "Universidade Federal do Paraná"},
)


def institutions():
    """Supporting institutions — exposed to the About page only, not globally.

    Kept out of the default template context so every page doesn't lean
    on the institutional branding; the About page opts in explicitly.
    """
    return _INSTITUTIONS


def _human_bytes(n) -> str:
    try:
        x = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024:
            return f"{x:,.1f} {unit}"
        x /= 1024
    return f"{x:,.1f} PB"


def _short_num(n) -> str:
    try:
        x = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if x < 1000:
        return f"{int(x)}"
    if x < 1_000_000:
        return f"{x / 1000:.1f}k"
    return f"{x / 1_000_000:.1f}M"
