"""JSON + HTMX fragment endpoints.

Separated from :mod:`.pages` so full-page rendering and live-polling
fragments evolve independently. Every endpoint here is prefixed with
``/api`` by ``build_app``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import catalog as webcatalog
from .. import db as webdb
from .. import inventory_scan
from .. import products as webproducts
from ..models import JobRow
from ..runner import get_runner

router = APIRouter()


# --------------------------------------------------------------------- jobs

@router.get("/jobs")
def list_jobs(status: str | None = None) -> JSONResponse:
    q = "SELECT * FROM jobs"
    params: tuple = ()
    if status:
        q += " WHERE status=?"
        params = (status,)
    q += " ORDER BY id DESC LIMIT 200"
    with webdb.transaction() as conn:
        rows = conn.execute(q, params).fetchall()
    return JSONResponse([_job_dict(r) for r in rows])


@router.get("/jobs/{job_id}")
def job_json(job_id: int) -> JSONResponse:
    with webdb.transaction() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        steps_total = row["steps_total"] or 0
        steps_done = row["steps_done"] or 0
        steps_failed = row["steps_failed"] or 0
        latest_logs = conn.execute(
            "SELECT * FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 50",
            (job_id,),
        ).fetchall()
    return JSONResponse({
        **_job_dict(row),
        "percent": (100.0 * steps_done / steps_total) if steps_total else 0.0,
        "steps_total":  steps_total,
        "steps_done":   steps_done,
        "steps_failed": steps_failed,
        "logs": [{"ts": r["ts"], "level": r["level"], "message": r["message"]}
                 for r in latest_logs],
    })


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> JSONResponse:
    get_runner().cancel(job_id)
    return JSONResponse({"ok": True})


@router.get("/jobs/{job_id}/fragment", response_class=HTMLResponse)
def job_fragment(job_id: int, request: Request) -> HTMLResponse:
    with webdb.transaction() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return HTMLResponse("<p>job not found.</p>", status_code=404)
        steps = conn.execute(
            "SELECT * FROM job_steps WHERE job_id=? ORDER BY id DESC LIMIT 25",
            (job_id,),
        ).fetchall()
        logs = conn.execute(
            "SELECT * FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 50",
            (job_id,),
        ).fetchall()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "partials/job_live.html",
        {
            "job": JobRow.from_row(row),
            "steps": [dict(s) for s in steps],
            "logs":  [dict(l) for l in logs],
        },
    )


# --------------------------------------------------------------------- inventory

@router.post("/inventory/scan")
def scan_inventory() -> JSONResponse:
    result = inventory_scan.scan()
    return JSONResponse(result)


@router.get("/inventory")
def inventory_json(limit: int = 200) -> JSONResponse:
    with webdb.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM inventory ORDER BY mtime DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return JSONResponse([dict(r) for r in rows])


# --------------------------------------------------------------------- quota

@router.get("/quota/{provider}")
def quota_for(provider: str) -> JSONResponse:
    from ... import cloud
    try:
        text = cloud.quota_report(provider)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"provider": provider, "report": text})


@router.get("/quota")
def quota_all() -> JSONResponse:
    from ... import cloud
    out = []
    for name in ("aws", "gcloud", "azure"):
        try:
            out.append({"provider": name, "report": cloud.quota_report(name)})
        except Exception as e:
            out.append({"provider": name, "error": str(e)})
    return JSONResponse(out)


# --------------------------------------------------------------------- sources

@router.get("/sources")
def sources_json() -> JSONResponse:
    from ... import batch, sources as _src
    out = []
    for name in batch.registered_sources():
        mod = getattr(_src, name, None)
        earliest = getattr(mod, "EARLIEST", None)
        retention = getattr(mod, "RETENTION_DAYS", None)
        out.append({
            "name": name,
            "workers": batch.source_default_workers(name),
            "earliest": earliest.date().isoformat() if earliest else None,
            "retention_days": retention,
        })
    return JSONResponse(out)


@router.get("/availability/{date}")
def availability(date: str) -> JSONResponse:
    from ... import batch
    return JSONResponse({"date": date, "sources": batch.available_sources(date)})


# --------------------------------------------------------------------- products

@router.get("/products")
def products_json() -> JSONResponse:
    """All products the WebUI exposes (default first)."""
    return JSONResponse([
        {
            "id": p.id,
            "label": p.label,
            "model": p.model,
            "code": p.code,
            "description": p.description,
            "default_bbox": list(p.default_bbox) if p.default_bbox else None,
            "sources": list(p.sources),
        }
        for p in webproducts.list_products()
    ])


# --------------------------------------------------------------------- catalog

@router.get("/catalog")
def catalog_json(product: str | None = None) -> JSONResponse:
    """Variable/level catalog backing the cascade picker.

    Accepts ``?product=<product_id>``; falls back to the default product
    when missing or unknown.
    """
    resolved = webproducts.get_product(product)
    payload = webcatalog.load_catalog(resolved.id).as_dict()
    payload["product_id"] = resolved.id
    return JSONResponse(payload)


# --------------------------------------------------------------------- presets

@router.get("/presets")
def list_presets() -> JSONResponse:
    with webdb.transaction() as conn:
        rows = conn.execute(
            "SELECT id, name, description, variables, levels, "
            "       created_at, updated_at "
            "FROM presets ORDER BY name"
        ).fetchall()
    return JSONResponse([_preset_dict(r) for r in rows])


@router.post("/presets")
async def save_preset(request: Request) -> JSONResponse:
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    variables = payload.get("variables") or []
    levels = payload.get("levels") or []
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not isinstance(variables, list) or not isinstance(levels, list):
        return JSONResponse(
            {"error": "variables and levels must be arrays"}, status_code=400
        )
    if not variables or not levels:
        return JSONResponse(
            {"error": "variables and levels must be non-empty"}, status_code=400
        )
    vars_json = json.dumps([str(v) for v in variables])
    levels_json = json.dumps([str(lv) for lv in levels])
    with webdb.transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM presets WHERE name=?", (name,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE presets SET description=?, variables=?, levels=?, "
                "                   updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (description, vars_json, levels_json, existing["id"]),
            )
            preset_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO presets (name, description, variables, levels) "
                "VALUES (?, ?, ?, ?)",
                (name, description, vars_json, levels_json),
            )
            preset_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, name, description, variables, levels, "
            "       created_at, updated_at FROM presets WHERE id=?",
            (preset_id,),
        ).fetchone()
    return JSONResponse(_preset_dict(row))


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: int) -> JSONResponse:
    with webdb.transaction() as conn:
        cur = conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        if cur.rowcount == 0:
            return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "id": preset_id})


def _preset_dict(row: Any) -> dict[str, Any]:
    return {
        "id":          row["id"],
        "name":        row["name"],
        "description": row["description"] or "",
        "variables":   json.loads(row["variables"]),
        "levels":      json.loads(row["levels"]),
        "created_at":  row["created_at"],
        "updated_at":  row["updated_at"],
    }


# --------------------------------------------------------------------- helpers

def _job_dict(row: Any) -> dict[str, Any]:
    return {
        "id":            row["id"],
        "name":          row["name"],
        "status":        row["status"],
        "priority":      row["priority"],
        "steps_total":   row["steps_total"],
        "steps_done":    row["steps_done"],
        "steps_failed":  row["steps_failed"],
        "bytes_downloaded": row["bytes_downloaded"],
        "started_at":    row["started_at"],
        "finished_at":   row["finished_at"],
        "created_at":    row["created_at"],
    }
