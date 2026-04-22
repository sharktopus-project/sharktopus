"""Server-rendered HTML pages.

Each route returns a full-page Jinja2 template inheriting from
``base.html``. The page's interactive bits (job live panels, quota
chart refresh, inventory filters) are driven by HTMX fragments served
from :mod:`.api`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db as webdb
from ..models import JobRow, parse_submit_form
from ..runner import get_runner

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request, name, {"active_path": request.url.path, **ctx}
    )


# --------------------------------------------------------------------- dashboard

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    with webdb.transaction() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS n, "
            "       SUM(CASE WHEN status='running'  THEN 1 ELSE 0 END) AS running, "
            "       SUM(CASE WHEN status='queued'   THEN 1 ELSE 0 END) AS queued, "
            "       SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS ok, "
            "       SUM(CASE WHEN status='failed'   THEN 1 ELSE 0 END) AS failed "
            "FROM jobs"
        ).fetchone()
        recent_rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        inventory = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes "
            "FROM inventory"
        ).fetchone()
    return _render(request, "dashboard.html", {
        "totals": dict(totals) if totals else {"n": 0},
        "recent": [JobRow.from_row(r) for r in recent_rows],
        "inventory": dict(inventory) if inventory else {"n": 0, "bytes": 0},
    })


# --------------------------------------------------------------------- submit

_SOURCE_META: dict[str, dict[str, str]] = {
    "aws_crop":      {"label": "AWS cloud-crop",     "hint": "Lambda subsets in-cloud (fastest, tiny egress)"},
    "gcloud_crop":   {"label": "GCloud cloud-crop",  "hint": "Cloud Run subsets in-cloud"},
    "azure_crop":    {"label": "Azure cloud-crop",   "hint": "Container Apps subsets in-cloud"},
    "aws":           {"label": "AWS local-crop",     "hint": "Full download + local wgrib2 crop"},
    "gcloud":        {"label": "GCloud local-crop",  "hint": "Full download + local wgrib2 crop"},
    "azure":         {"label": "Azure local-crop",   "hint": "Full download + local wgrib2 crop"},
    "nomads_filter": {"label": "NOMADS filter",      "hint": "NOAA server-side filter (var/lev/bbox)"},
    "nomads":        {"label": "NOMADS local-crop",  "hint": "Full download + local wgrib2 crop"},
    "rda":           {"label": "RDA (NCAR)",         "hint": "Historical archive, local crop"},
}

# Recommended default order for the Submit chip UI: cloud-crops first
# (fastest, tiny egress), then NOMADS server-side filter, then local
# crops, RDA last (historical archive fallback).
_RECOMMENDED_ORDER: tuple[str, ...] = (
    "aws_crop", "gcloud_crop", "azure_crop",
    "nomads_filter",
    "aws", "gcloud", "azure", "nomads",
    "rda",
)


def _source_catalog() -> list[dict[str, Any]]:
    from ... import batch, sources as _src
    registered = set(batch.registered_sources())
    ordered = [n for n in _RECOMMENDED_ORDER if n in registered]
    # any registered source we didn't pre-rank goes after, alphabetically
    ordered += sorted(registered - set(ordered))
    out: list[dict[str, Any]] = []
    for name in ordered:
        mod = getattr(_src, name, None)
        earliest = getattr(mod, "EARLIEST", None)
        retention = getattr(mod, "RETENTION_DAYS", None)
        meta = _SOURCE_META.get(name, {"label": name, "hint": ""})
        out.append({
            "name": name,
            "label": meta["label"],
            "hint": meta["hint"],
            "workers": batch.source_default_workers(name),
            "earliest": earliest.date().isoformat() if earliest else None,
            "retention_days": retention,
        })
    return out


def _submit_defaults() -> dict[str, Any]:
    from ... import batch, wrf, sources as _src
    from .. import products as webproducts
    from datetime import datetime as _dt, timezone as _tz

    registered = list(batch.registered_sources())

    def _earliest_for(source_names: list[str]):
        out = None
        for nm in source_names:
            mod = getattr(_src, nm, None)
            e = getattr(mod, "EARLIEST", None)
            if e is not None and (out is None or e < out):
                out = e
        return out

    earliest = _earliest_for(registered) or _dt(2015, 1, 15, tzinfo=_tz.utc)
    product_list = []
    for p in webproducts.list_products():
        scope = list(p.sources) if p.sources else registered
        p_earliest = _earliest_for(scope) or earliest
        product_list.append({
            "id": p.id,
            "label": p.label,
            "model": p.model,
            "code": p.code,
            "description": p.description,
            "coverage_bbox": list(p.default_bbox) if p.default_bbox else None,
            "earliest_date": p_earliest.date().isoformat(),
            "earliest_year": p_earliest.year,
            # Empty = all registered sources; non-empty = allowlist
            "allowed_sources": list(p.sources),
        })
    return {
        "priority": list(batch.registered_sources()),
        "variables": list(wrf.DEFAULT_VARS),
        "levels": list(wrf.DEFAULT_LEVELS),
        "earliest_date": earliest.date().isoformat(),
        "earliest_year": earliest.year,
        "sources": _source_catalog(),
        "products": product_list,
        "default_product_id": product_list[0]["id"] if product_list else None,
        # SE Brazil / South Atlantic — matches CONVECT project area.
        # Users get a visible default box; change freely.
        "lat_s": -35.0,
        "lat_n": -5.0,
        "lon_w": -55.0,
        "lon_e": -30.0,
    }


@router.get("/submit", response_class=HTMLResponse)
def submit_get(request: Request) -> HTMLResponse:
    return _render(request, "submit.html", {
        "defaults": _submit_defaults(),
        "form": None,
        "errors": [],
    })


@router.post("/submit")
async def submit_post(request: Request):
    form_data = await request.form()
    form, errors = parse_submit_form(dict(form_data))
    if errors:
        return _render(request, "submit.html", {
            "defaults": _submit_defaults(), "form": form, "errors": errors,
        })
    job_id = get_runner().submit(form)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# --------------------------------------------------------------------- jobs

@router.get("/jobs", response_class=HTMLResponse)
def jobs_list(request: Request, status: str | None = None) -> HTMLResponse:
    q = "SELECT * FROM jobs"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    q += " ORDER BY id DESC LIMIT 200"
    with webdb.transaction() as conn:
        rows = conn.execute(q, params).fetchall()
    return _render(request, "jobs.html", {
        "jobs": [JobRow.from_row(r) for r in rows],
        "status_filter": status,
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int) -> HTMLResponse:
    with webdb.transaction() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return _render(request, "404.html", {"what": f"job #{job_id}"})
        steps = conn.execute(
            "SELECT * FROM job_steps WHERE job_id=? ORDER BY id DESC LIMIT 200",
            (job_id,),
        ).fetchall()
        logs = conn.execute(
            "SELECT * FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 200",
            (job_id,),
        ).fetchall()
    return _render(request, "job_detail.html", {
        "job": JobRow.from_row(row),
        "form_json": row["form_json"],
        "steps": [dict(s) for s in steps],
        "logs": [dict(l) for l in logs],
    })


# --------------------------------------------------------------------- inventory

@router.get("/inventory", response_class=HTMLResponse)
def inventory(request: Request) -> HTMLResponse:
    with webdb.transaction() as conn:
        summary = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes "
            "FROM inventory"
        ).fetchone()
        by_date = conn.execute(
            "SELECT date, COUNT(*) AS n, SUM(size_bytes) AS bytes "
            "FROM inventory WHERE date IS NOT NULL GROUP BY date "
            "ORDER BY date DESC LIMIT 60"
        ).fetchall()
        files = conn.execute(
            "SELECT * FROM inventory ORDER BY mtime DESC LIMIT 200"
        ).fetchall()
    return _render(request, "inventory.html", {
        "summary": dict(summary) if summary else {"n": 0, "bytes": 0},
        "by_date": [dict(r) for r in by_date],
        "files": [dict(r) for r in files],
    })


# --------------------------------------------------------------------- quota

@router.get("/quota", response_class=HTMLResponse)
def quota(request: Request) -> HTMLResponse:
    from ... import cloud
    providers: list[dict[str, Any]] = []
    for name in ("aws", "gcloud", "azure"):
        try:
            report = cloud.quota_report(name)
        except Exception as e:
            report = f"error: {e}"
        providers.append({"name": name, "report": report})
    return _render(request, "quota.html", {"providers": providers})


# --------------------------------------------------------------------- sources

@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request) -> HTMLResponse:
    from ... import batch, sources as _src
    rows = []
    for name in batch.registered_sources():
        mod = getattr(_src, name, None)
        earliest = getattr(mod, "EARLIEST", None)
        retention = getattr(mod, "RETENTION_DAYS", None)
        rows.append({
            "name": name,
            "workers": batch.source_default_workers(name),
            "earliest": earliest.date().isoformat() if earliest else "—",
            "retention": f"{retention}d" if retention else "∞",
        })
    return _render(request, "sources.html", {"sources": rows})


# --------------------------------------------------------------------- setup

@router.get("/setup", response_class=HTMLResponse)
def setup(request: Request) -> HTMLResponse:
    return _render(request, "setup.html", {})


@router.get("/setup/{provider}", response_class=HTMLResponse)
def setup_provider(request: Request, provider: str) -> HTMLResponse:
    if provider not in ("gcloud", "aws", "azure"):
        return _render(request, "404.html", {"what": f"setup {provider!r}"})
    return _render(request, "setup_provider.html", {"provider": provider})


# --------------------------------------------------------------------- credentials

@router.get("/credentials", response_class=HTMLResponse)
def credentials(request: Request) -> HTMLResponse:
    creds: list[dict[str, Any]] = []
    for name, inspector in _credential_inspectors():
        try:
            creds.append({"name": name, "info": inspector()})
        except Exception as e:
            creds.append({"name": name, "info": {"error": str(e)}})
    return _render(request, "credentials.html", {"creds": creds})


def _credential_inspectors():
    """Small dispatch table; keeps imports lazy so missing SDKs don't crash the page."""

    def gcloud():
        try:
            from ..._gcloud_auth import describe_cached_token
            return describe_cached_token()
        except ImportError:
            return {"status": "module not available"}
        except Exception as e:
            return {"status": f"error: {e}"}

    def aws():
        import os
        return {
            "AWS_ACCESS_KEY_ID": "set" if os.environ.get("AWS_ACCESS_KEY_ID") else "not set",
            "AWS_PROFILE":       os.environ.get("AWS_PROFILE") or "default",
            "AWS_REGION":        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "not set",
        }

    def azure():
        import os
        return {
            "AZURE_SUBSCRIPTION_ID": "set" if os.environ.get("AZURE_SUBSCRIPTION_ID") else "not set",
            "AZURE_TENANT_ID":       "set" if os.environ.get("AZURE_TENANT_ID") else "not set",
        }

    return (("gcloud", gcloud), ("aws", aws), ("azure", azure))


# --------------------------------------------------------------------- settings

@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request) -> HTMLResponse:
    import os
    env = {
        "SHARKTOPUS_DATA":         os.environ.get("SHARKTOPUS_DATA") or "(unset — ~/.cache/sharktopus)",
        "SHARKTOPUS_CACHE_HOME":   os.environ.get("SHARKTOPUS_CACHE_HOME") or "(unset)",
        "SHARKTOPUS_ACCEPT_CHARGES": os.environ.get("SHARKTOPUS_ACCEPT_CHARGES") or "(unset — paid-tier blocked)",
    }
    return _render(request, "settings.html", {"env": env})


# --------------------------------------------------------------------- help

@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    return _render(request, "help.html", {})
