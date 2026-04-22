"""In-process job runner for the web UI.

A single background thread pool drains the ``jobs`` table in insertion
order. Each running job gets progress + log callbacks wired to
:func:`sharktopus.batch.fetch_batch` so the UI can poll ``GET
/api/jobs/{id}`` and see rows update live.

This is deliberately not a distributed queue. The UI is local-first —
one user on one machine — and spreading work across processes would
collide with the per-source worker caps the orchestrator already
enforces. The pool size is 1 by default (serial jobs) with an optional
concurrent mode for users who want to stack several small cycles.
"""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import batch
from . import db
from .models import SubmitForm

__all__ = ["JobRunner", "get_runner"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRunner:
    """Submit / run / cancel jobs in a background pool."""

    def __init__(self, *, max_concurrent: int = 1) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_concurrent)),
            thread_name_prefix="sharktopus-job",
        )
        self._futures: dict[int, Future] = {}
        self._cancelled: set[int] = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ submit

    def submit(self, form: SubmitForm) -> int:
        """Insert a queued job and return its ID."""
        payload = form.to_json()
        priority = ",".join(form.priority) if form.priority else None
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (name, status, form_json, priority) "
                "VALUES (?, 'queued', ?, ?)",
                (form.name or None, payload, priority),
            )
            job_id = int(cur.lastrowid)
        fut = self._pool.submit(self._run, job_id)
        with self._lock:
            self._futures[job_id] = fut
        return job_id

    def cancel(self, job_id: int) -> bool:
        """Request cancellation; effect is best-effort.

        The orchestrator doesn't expose a cancel hook mid-step, so a
        running step finishes before the job exits. Queued jobs (not
        yet picked up) are cancelled immediately.
        """
        with self._lock:
            self._cancelled.add(job_id)
            fut = self._futures.get(job_id)
        if fut is not None and fut.cancel():
            self._finalize(job_id, status="cancelled", error="cancelled before start")
            return True
        return True

    def is_cancelled(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancelled

    # ------------------------------------------------------------------ run

    def _run(self, job_id: int) -> None:
        """Thread worker: load the form, call fetch_batch, update rows."""
        if self.is_cancelled(job_id):
            self._finalize(job_id, status="cancelled", error="cancelled before start")
            return

        form = self._load_form(job_id)
        if form is None:
            return

        kwargs = form.to_fetch_kwargs()
        total = _estimate_steps(kwargs)

        with db.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET status='running', started_at=?, steps_total=? "
                "WHERE id=?",
                (_now_iso(), total, job_id),
            )
        self._log(job_id, "info", f"running fetch_batch with {total} step(s)")

        def on_ok(date: str, cycle: str, fxx: int, path: Path) -> None:
            if self.is_cancelled(job_id):
                return
            size = 0
            try:
                size = int(Path(path).stat().st_size)
            except OSError:
                pass
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO job_steps (job_id, date, cycle, fxx, status, "
                    "path) VALUES (?, ?, ?, ?, 'ok', ?)",
                    (job_id, date, cycle, fxx, str(path)),
                )
                conn.execute(
                    "UPDATE jobs SET steps_done = steps_done + 1, "
                    "bytes_downloaded = bytes_downloaded + ? WHERE id=?",
                    (size, job_id),
                )
            self._log(job_id, "info", f"  ok  {date} {cycle}z f{fxx:03d}  → {path}")

        def on_fail(date: str, cycle: str, fxx: int,
                    errors: list[tuple[str, Exception]]) -> None:
            summary = "; ".join(f"{n}: {e.__class__.__name__}" for n, e in errors)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO job_steps (job_id, date, cycle, fxx, status, "
                    "error) VALUES (?, ?, ?, ?, 'failed', ?)",
                    (job_id, date, cycle, fxx, summary),
                )
                conn.execute(
                    "UPDATE jobs SET steps_failed = steps_failed + 1 WHERE id=?",
                    (job_id,),
                )
            self._log(job_id, "warn", f"  FAIL {date} {cycle}z f{fxx:03d}  ({summary})")

        kwargs["on_step_ok"] = on_ok
        kwargs["on_step_fail"] = on_fail

        try:
            outputs = batch.fetch_batch(**kwargs)
        except BaseException as exc:
            tb = traceback.format_exc()
            self._log(job_id, "error", tb)
            self._finalize(job_id, status="failed", error=str(exc))
            return

        self._log(job_id, "info", f"done — {len(outputs)} file(s) downloaded")
        self._finalize(job_id, status="succeeded")

    # ------------------------------------------------------------------ internals

    def _load_form(self, job_id: int) -> SubmitForm | None:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT form_json FROM jobs WHERE id=?", (job_id,),
            ).fetchone()
        if row is None:
            return None
        return SubmitForm.from_json(row["form_json"])

    def _finalize(self, job_id: int, *, status: str, error: str | None = None) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                (status, _now_iso(), job_id),
            )
        if error:
            self._log(job_id, "error", f"finalized as {status}: {error}")
        else:
            self._log(job_id, "info", f"finalized as {status}")

    def _log(self, job_id: int, level: str, message: str) -> None:
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
                    (job_id, level, message),
                )
        except Exception:
            pass

    # ------------------------------------------------------------------ shutdown

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


def _estimate_steps(kwargs: dict[str, Any]) -> int:
    ts = kwargs.get("timestamps") or []
    ext = int(kwargs.get("ext", 24))
    interval = max(1, int(kwargs.get("interval", 3)))
    per_cycle = len(range(0, ext + 1, interval))
    return len(ts) * per_cycle


_RUNNER: JobRunner | None = None
_RUNNER_LOCK = threading.Lock()


def get_runner() -> JobRunner:
    """Module-level singleton so every route hits the same pool."""
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            _RUNNER = JobRunner()
        return _RUNNER
