"""Plain dataclasses + form parsing for the web UI.

Avoids adding pydantic as a hard runtime dep — FastAPI already ships
its own copy, so we could lean on it, but keeping the UI data model
framework-agnostic means the same classes can back the CLI in the
future without picking up pydantic for everyone who just types
``sharktopus --start 2024010200 ...``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "SubmitForm",
    "JobRow",
    "parse_submit_form",
]


_TS_RE = re.compile(r"^\d{10}$")


def _earliest_ts() -> str:
    """Return the earliest YYYYMMDDHH supported by any registered source.

    Falls back to ``2015011500`` (the RDA ds084.1 floor) if the
    ``sharktopus`` package can't be imported, so the validation layer
    stays usable in unit tests that stub the environment.
    """
    try:
        from sharktopus import batch as _batch, sources as _src  # type: ignore
    except Exception:
        return "2015011500"
    earliest = None
    for name in _batch.registered_sources():
        mod = getattr(_src, name, None)
        e = getattr(mod, "EARLIEST", None)
        if e is not None and (earliest is None or e < earliest):
            earliest = e
    if earliest is None:
        return "2015011500"
    return earliest.strftime("%Y%m%d%H")


@dataclass
class SubmitForm:
    """Validated payload from the Submit page."""

    name: str = ""
    mode: str = "range"
    start: str | None = None
    end: str | None = None
    step: int = 6
    timestamps: list[str] = field(default_factory=list)
    ext: int = 24
    interval: int = 3
    lat_s: float = 0.0
    lat_n: float = 0.0
    lon_w: float = 0.0
    lon_e: float = 0.0
    pad_lon: float | None = None
    pad_lat: float | None = None
    product: str = "pgrb2.0p25"
    priority: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    dest: str | None = None
    root: str | None = None
    max_workers: int | None = None
    spread: str = "auto"
    lon_convention: str = "-180..180"

    def to_fetch_kwargs(self) -> dict[str, Any]:
        """Return a kwargs dict ready for :func:`sharktopus.fetch_batch`."""
        if self.mode == "list":
            ts = [t for t in self.timestamps if t]
        else:
            from sharktopus.batch import generate_timestamps
            ts = generate_timestamps(self.start, self.end, int(self.step))
        kwargs: dict[str, Any] = {
            "timestamps": ts,
            "lat_s": float(self.lat_s),
            "lat_n": float(self.lat_n),
            "lon_w": float(self.lon_w),
            "lon_e": float(self.lon_e),
            "ext": int(self.ext),
            "interval": int(self.interval),
            "product": self.product or "pgrb2.0p25",
        }
        if self.priority:
            kwargs["priority"] = tuple(self.priority)
        if self.variables:
            kwargs["variables"] = list(self.variables)
        if self.levels:
            kwargs["levels"] = list(self.levels)
        if self.dest:
            kwargs["dest"] = self.dest
        if self.root:
            kwargs["root"] = self.root
        if self.pad_lon is not None:
            kwargs["pad_lon"] = float(self.pad_lon)
        if self.pad_lat is not None:
            kwargs["pad_lat"] = float(self.pad_lat)
        if self.max_workers is not None:
            kwargs["max_workers"] = int(self.max_workers)
        if self.spread in ("spread", "classic"):
            kwargs["spread"] = (self.spread == "spread")
        return kwargs

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> "SubmitForm":
        data = json.loads(payload)
        return cls(**{k: data.get(k, getattr(cls, k, None)) for k in cls.__dataclass_fields__})


def _as_int(v: Any, default: int) -> int:
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_int_opt(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_float_opt(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split_list(v: Any) -> list[str]:
    """Split a form value into a list of strings.

    Mirrors :mod:`sharktopus.io.config`'s behavior: comma (or
    newline/semicolon) wins when present so multi-word tokens like
    ``"500 mb"`` stay intact. If no separator of that kind appears,
    whitespace is used instead — so ``"aws_crop gcloud"`` → two items.
    """
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if any(sep in s for sep in (",", "\n", ";")):
        parts = [p.strip() for p in re.split(r"[,\n;]", s)]
    else:
        parts = s.split()
    return [p for p in parts if p]


def parse_submit_form(form: dict[str, Any]) -> tuple[SubmitForm, list[str]]:
    """Turn a raw HTML form dict into a :class:`SubmitForm` + error list.

    The caller is expected to re-render the form with errors when the
    list is non-empty; otherwise the returned form is safe to pass to
    :meth:`SubmitForm.to_fetch_kwargs`.
    """
    errors: list[str] = []
    mode = (form.get("mode") or "range").strip() or "range"
    if mode not in ("range", "list"):
        errors.append(f"mode must be 'range' or 'list', got {mode!r}")
        mode = "range"

    timestamps = _split_list(form.get("timestamps"))
    for ts in timestamps:
        if not _TS_RE.match(ts):
            errors.append(f"timestamp {ts!r} is not YYYYMMDDHH")

    priority = _split_list(form.get("priority"))
    variables = _split_list(form.get("variables"))
    levels = _split_list(form.get("levels"))

    sf = SubmitForm(
        name=(form.get("name") or "").strip(),
        mode=mode,
        start=(form.get("start") or "").strip() or None,
        end=(form.get("end") or "").strip() or None,
        step=_as_int(form.get("step"), 6),
        timestamps=timestamps,
        ext=_as_int(form.get("ext"), 24),
        interval=_as_int(form.get("interval"), 3),
        lat_s=_as_float(form.get("lat_s")),
        lat_n=_as_float(form.get("lat_n")),
        lon_w=_as_float(form.get("lon_w")),
        lon_e=_as_float(form.get("lon_e")),
        pad_lon=_as_float_opt(form.get("pad_lon")),
        pad_lat=_as_float_opt(form.get("pad_lat")),
        product=(form.get("product") or "pgrb2.0p25").strip(),
        priority=priority,
        variables=variables,
        levels=levels,
        dest=(form.get("dest") or "").strip() or None,
        root=(form.get("root") or "").strip() or None,
        max_workers=_as_int_opt(form.get("max_workers")),
        spread=(form.get("spread") or "auto").strip(),
        lon_convention=(form.get("lon_convention") or "-180..180").strip(),
    )

    earliest = _earliest_ts()

    if mode == "range":
        if not (sf.start and sf.end):
            errors.append("range mode needs both start and end")
        else:
            start_ok = bool(_TS_RE.match(sf.start))
            end_ok = bool(_TS_RE.match(sf.end))
            if not start_ok:
                errors.append(f"start {sf.start!r} is not YYYYMMDDHH")
            if not end_ok:
                errors.append(f"end {sf.end!r} is not YYYYMMDDHH")
            if start_ok and end_ok:
                if sf.start >= sf.end:
                    errors.append(
                        f"start ({sf.start}) must be earlier than end ({sf.end})"
                    )
                if sf.start < earliest:
                    errors.append(
                        f"start {sf.start} is before earliest supported GFS cycle {earliest} (RDA floor)"
                    )
    else:
        if not timestamps:
            errors.append("list mode needs at least one timestamp")
        else:
            for ts in timestamps:
                if _TS_RE.match(ts) and ts < earliest:
                    errors.append(
                        f"timestamp {ts} is before earliest supported GFS cycle {earliest} (RDA floor)"
                    )

    if sf.lat_s < -90 or sf.lat_s > 90:
        errors.append(f"lat_s {sf.lat_s} must be in [-90, 90]")
    if sf.lat_n < -90 or sf.lat_n > 90:
        errors.append(f"lat_n {sf.lat_n} must be in [-90, 90]")
    if sf.lat_s >= sf.lat_n:
        errors.append("lat_s must be south of lat_n")

    if sf.lon_convention not in ("-180..180", "0..360"):
        errors.append(
            f"lon_convention must be '-180..180' or '0..360', got {sf.lon_convention!r}"
        )
        lon_min, lon_max = -180.0, 180.0
    elif sf.lon_convention == "0..360":
        lon_min, lon_max = 0.0, 360.0
    else:
        lon_min, lon_max = -180.0, 180.0
    if sf.lon_w < lon_min or sf.lon_w > lon_max:
        errors.append(
            f"lon_w {sf.lon_w} must be in [{lon_min}, {lon_max}] "
            f"({sf.lon_convention})"
        )
    if sf.lon_e < lon_min or sf.lon_e > lon_max:
        errors.append(
            f"lon_e {sf.lon_e} must be in [{lon_min}, {lon_max}] "
            f"({sf.lon_convention})"
        )
    if sf.lon_w >= sf.lon_e:
        errors.append("lon_w must be west of lon_e")

    return sf, errors


@dataclass
class JobRow:
    """Projection of the ``jobs`` table used by the Jobs and Dashboard pages."""

    id: int
    name: str
    status: str
    steps_total: int
    steps_done: int
    steps_failed: int
    bytes_downloaded: int
    started_at: str | None
    finished_at: str | None
    created_at: str
    priority: str | None

    @classmethod
    def from_row(cls, row: Any) -> "JobRow":
        return cls(
            id=int(row["id"]),
            name=row["name"] or f"job-{row['id']}",
            status=row["status"],
            steps_total=int(row["steps_total"] or 0),
            steps_done=int(row["steps_done"] or 0),
            steps_failed=int(row["steps_failed"] or 0),
            bytes_downloaded=int(row["bytes_downloaded"] or 0),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            priority=row["priority"],
        )

    @property
    def percent(self) -> float:
        if not self.steps_total:
            return 0.0
        return 100.0 * self.steps_done / self.steps_total

    @property
    def duration(self) -> str:
        if not self.started_at:
            return "—"
        from datetime import datetime, timezone
        try:
            t0 = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        except ValueError:
            return "—"
        t1_raw = self.finished_at
        if t1_raw:
            try:
                t1 = datetime.fromisoformat(t1_raw.replace("Z", "+00:00"))
            except ValueError:
                t1 = datetime.now(timezone.utc)
        else:
            t1 = datetime.now(timezone.utc)
        sec = (t1 - t0).total_seconds()
        if sec < 60:
            return f"{int(sec)}s"
        if sec < 3600:
            return f"{int(sec // 60)}m {int(sec % 60)}s"
        return f"{int(sec // 3600)}h {int((sec % 3600) // 60)}m"
