"""Batch-download orchestrator — Layer 2.

Drop-in replacement for CONVECT's ``menu_gfs.download_batch``:
iterates over a list of cycle timestamps, within each cycle iterates
over the requested forecast steps, and for each step tries the sources
in ``priority`` order until one succeeds. Signature mirrors
``download_batch_cli.py`` (``lat_s/lat_n/lon_w/lon_e`` separate floats
rather than a tuple) so callers migrating from CONVECT don't have to
rewrite their call sites.

Source registry is a plain dict keyed by name. Built-in registrations:

* ``nomads`` — :mod:`sharktopus.sources.nomads` (full-file, NOAA)
* ``aws`` — :mod:`sharktopus.sources.aws` (AWS Open Data mirror)
* ``gcloud`` — :mod:`sharktopus.sources.gcloud` (Google Cloud mirror)
* ``azure`` — :mod:`sharktopus.sources.azure` (Azure Blob mirror)
* ``rda`` — :mod:`sharktopus.sources.rda` (NCAR long-term archive)
* ``nomads_filter`` — :mod:`sharktopus.sources.nomads_filter`
  (server-side subset; opt-in only)

Priority — :data:`DEFAULT_PRIORITY` is the preferred order when the
caller doesn't pass ``priority=``. :func:`available_sources` filters
it to the mirrors that can actually serve a given date. ``fetch_batch``
wires those together so the common case is just
``fetch_batch(timestamps=..., lat_s=..., ...)``.

Concurrency — each source publishes a ``DEFAULT_MAX_WORKERS`` tuned
below its mirror's observed throttling threshold. :func:`fetch_batch`
runs steps in parallel with a ``ThreadPoolExecutor`` sized to the
*minimum* across the priority list (so the slowest-throttled mirror
paces the whole pool, not the fastest one). Callers who know better
can override via ``max_workers=...``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import grib, wrf
from .sources import SourceUnavailable, aws, azure, gcloud, nomads, nomads_filter, rda

__all__ = [
    "DEFAULT_PRIORITY",
    "SourceRegistry",
    "available_sources",
    "default_max_workers",
    "fetch_batch",
    "generate_timestamps",
    "register_source",
    "registered_sources",
    "source_default_workers",
    "source_supports",
]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SourceFn = Callable[..., Path]
_SupportsFn = Callable[..., bool]


class SourceRegistry(dict[str, _SourceFn]):
    """Name → ``fetch_step`` callable. Just a dict, subclassed for typing."""


_REGISTRY = SourceRegistry(
    nomads=nomads.fetch_step,
    nomads_filter=nomads_filter.fetch_step,
    aws=aws.fetch_step,
    gcloud=gcloud.fetch_step,
    azure=azure.fetch_step,
    rda=rda.fetch_step,
)

# Per-source concurrency ceiling (read from each module).
_WORKER_DEFAULTS: dict[str, int] = {
    "nomads": nomads.DEFAULT_MAX_WORKERS,
    "nomads_filter": nomads_filter.DEFAULT_MAX_WORKERS,
    "aws": aws.DEFAULT_MAX_WORKERS,
    "gcloud": gcloud.DEFAULT_MAX_WORKERS,
    "azure": azure.DEFAULT_MAX_WORKERS,
    "rda": rda.DEFAULT_MAX_WORKERS,
}

# Per-source supports(date, cycle=None, *, now=None) -> bool.
_SUPPORTS: dict[str, _SupportsFn] = {
    "nomads": nomads.supports,
    "nomads_filter": nomads_filter.supports,
    "aws": aws.supports,
    "gcloud": gcloud.supports,
    "azure": azure.supports,
    "rda": rda.supports,
}


# Default preference order when the caller doesn't pass ``priority=``.
# Ordering reflects both availability and cost:
#   * Cloud mirrors (gcloud/aws/azure) go first — high parallelism, no
#     rolling-window surprises, and consistently fast.
#   * rda picks up pre-2021 dates the cloud mirrors don't have.
#   * nomads last because the origin infrastructure is the most
#     rate-limited and useful mostly when the cycle is fresh enough
#     that cloud mirrors haven't staged it yet.
# ``nomads_filter`` is intentionally NOT here — its value is server-side
# subsetting, which requires the caller to pass ``variables`` +
# ``levels``. Include it explicitly in ``priority=`` when you want it.
DEFAULT_PRIORITY: tuple[str, ...] = ("gcloud", "aws", "azure", "rda", "nomads")


def register_source(
    name: str,
    fetch_step: _SourceFn,
    *,
    max_workers: int = 1,
    supports: _SupportsFn | None = None,
) -> None:
    """Register a source so :func:`fetch_batch` can route priority to it.

    *max_workers* becomes this source's published throttle ceiling (used
    by :func:`default_max_workers`). Default is 1 (serial) — you must
    opt in to parallelism explicitly once you've verified your mirror
    won't 429 / 503 under load.

    *supports* is a ``(date, cycle=None, *, now=None) -> bool`` callable
    that decides whether this source has a given date. The default
    returns ``True`` (always available), which is fine for tests and
    custom mirrors with no known lower bound.
    """
    _REGISTRY[name] = fetch_step
    _WORKER_DEFAULTS[name] = int(max_workers)
    _SUPPORTS[name] = supports if supports is not None else _always_true


def _always_true(*_a: Any, **_kw: Any) -> bool:
    return True


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)


def source_default_workers(name: str) -> int:
    """Return the published throttle ceiling for *name* (falls back to 1)."""
    return _WORKER_DEFAULTS.get(name, 1)


def source_supports(
    name: str,
    date: str,
    cycle: str | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return ``True`` if source *name* can serve *date* (cycle optional)."""
    fn = _SUPPORTS.get(name)
    if fn is None:
        return False
    return fn(date, cycle, now=now)


def default_max_workers(priority: Sequence[str]) -> int:
    """Conservative pool size for a priority list.

    Returns the *minimum* ``DEFAULT_MAX_WORKERS`` across all sources in
    the list — any step may fall back to any of them, so we must size
    the pool for the most-throttled mirror. Never returns less than 1.
    """
    if not priority:
        return 1
    return max(1, min(source_default_workers(n) for n in priority))


def available_sources(
    date: str,
    cycle: str | None = None,
    *,
    now: datetime | None = None,
    candidates: Sequence[str] | None = None,
) -> list[str]:
    """Return the subset of *candidates* (default :data:`DEFAULT_PRIORITY`) that can serve *date*.

    Preserves the order of *candidates* so the return value is a valid
    priority list directly. Pass ``candidates=registered_sources()`` to
    scan every registered mirror, not just the default preference.
    """
    names = tuple(candidates) if candidates is not None else DEFAULT_PRIORITY
    return [n for n in names if source_supports(n, date, cycle, now=now)]


# ---------------------------------------------------------------------------
# Timestamp expansion (YYYYMMDDHH strings)
# ---------------------------------------------------------------------------

def _parse_stamp(stamp: str) -> datetime:
    if len(stamp) != 10 or not stamp.isdigit():
        raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
    return datetime.strptime(stamp, "%Y%m%d%H")


def generate_timestamps(start: str, end: str, step: int = 6) -> list[str]:
    """Return every ``YYYYMMDDHH`` cycle from *start* to *end* inclusive.

    *step* is in hours and must be positive. CONVECT uses 6 as the
    default (four GFS cycles per day); we honor that.
    """
    if step <= 0:
        raise ValueError(f"step must be > 0, got {step}")
    s = _parse_stamp(start)
    e = _parse_stamp(end)
    if e < s:
        raise ValueError(f"end ({end}) < start ({start})")
    out: list[str] = []
    t = s
    dt = timedelta(hours=step)
    while t <= e:
        out.append(t.strftime("%Y%m%d%H"))
        t += dt
    return out


# ---------------------------------------------------------------------------
# Batch orchestrator
# ---------------------------------------------------------------------------

def _one_step(
    date: str, cycle: str, fxx: int,
    priority: Sequence[str],
    common: dict[str, Any],
    variables: list[str] | None,
    levels: list[str] | None,
) -> tuple[Path | None, list[tuple[str, Exception]]]:
    """Try the priority list for one (date, cycle, fxx); return (path_or_None, errors)."""
    errors: list[tuple[str, Exception]] = []
    for name in priority:
        fetch = _REGISTRY[name]
        kwargs = dict(common)
        if name == "nomads_filter":
            kwargs["variables"] = list(variables or [])
            kwargs["levels"] = list(levels or [])
        try:
            return fetch(date, cycle, fxx, **kwargs), errors
        except SourceUnavailable as e:
            errors.append((name, e))
            continue
    return None, errors


def fetch_batch(
    *,
    timestamps: Sequence[str],
    lat_s: float,
    lat_n: float,
    lon_w: float,
    lon_e: float,
    ext: int = 24,
    interval: int = 3,
    priority: Sequence[str] | None = None,
    variables: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    max_workers: int | None = None,
    now: datetime | None = None,
    on_step_ok: Callable[[str, str, int, Path], None] | None = None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None = None,
) -> list[Path]:
    """Download every ``(cycle, fxx)`` step implied by the inputs.

    Parameters mirror CONVECT's ``download_batch``:

    * each element of *timestamps* is a ``YYYYMMDDHH`` string
      (date + cycle concatenated).
    * *ext* is the forecast horizon in hours (so fxx runs 0..ext).
    * *interval* is the step between fxx values.
    * *priority* is the ordered list of source names tried per step.
      ``None`` (the default) means "derive from :data:`DEFAULT_PRIORITY`
      filtered to sources that can serve the first timestamp" — so
      recent dates get the full cloud-mirror fan-out while pre-2021
      dates automatically fall through to RDA.

    ``nomads_filter`` needs *variables* and *levels*; if it's in the
    priority list they're required. Other sources consume the bbox plus
    whatever they support.

    *max_workers* controls step-level parallelism. When omitted, uses
    :func:`default_max_workers` for the priority list — the minimum
    throttle ceiling across the listed sources. Set to 1 for fully
    serial downloads (useful when debugging or on very slow disks).

    *now* lets tests freeze the clock used by availability filtering.
    Leave it ``None`` in production.

    Returns the list of produced Paths in completion order (not request
    order). Steps where every source in the priority list raised
    :class:`~sharktopus.sources.SourceUnavailable` are reported via
    *on_step_fail* and skipped; any other exception is re-raised.
    """
    if not timestamps:
        raise ValueError("timestamps must be non-empty")
    if ext < 0:
        raise ValueError(f"ext must be >= 0, got {ext}")
    if interval <= 0:
        raise ValueError(f"interval must be > 0, got {interval}")

    if priority is None:
        first_date = str(timestamps[0])[:8]
        first_cycle = str(timestamps[0])[8:10] if len(str(timestamps[0])) >= 10 else None
        priority = available_sources(first_date, first_cycle, now=now)
        if not priority:
            raise SourceUnavailable(
                f"no registered source can serve {first_date}. "
                f"Checked: {list(DEFAULT_PRIORITY)}"
            )
    if not priority:
        raise ValueError("priority must be non-empty")
    unknown = set(priority) - set(_REGISTRY)
    if unknown:
        raise ValueError(
            f"unknown source(s) in priority: {sorted(unknown)}. "
            f"registered: {registered_sources()}"
        )
    if "nomads_filter" in priority:
        # Fall back to the WRF-canonical set when the caller hasn't
        # narrowed it down. Treat variables / levels independently so a
        # caller can override just one (e.g. add GUST to DEFAULT_VARS
        # while keeping the canonical level list).
        if variables is None:
            variables = wrf.DEFAULT_VARS
        if levels is None:
            levels = wrf.DEFAULT_LEVELS

    bbox = (float(lon_w), float(lon_e), float(lat_s), float(lat_n))
    common: dict[str, Any] = {
        "bbox": bbox,
        "pad_lon": pad_lon,
        "pad_lat": pad_lat,
        "product": product,
    }
    if dest is not None:
        common["dest"] = dest
    if root is not None:
        common["root"] = root

    var_list = list(variables) if variables is not None else None
    lev_list = list(levels) if levels is not None else None

    fxx_range = list(range(0, ext + 1, interval))
    jobs: list[tuple[str, str, int]] = []
    for stamp in timestamps:
        if len(stamp) != 10 or not stamp.isdigit():
            raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
        date, cycle = stamp[:8], stamp[8:]
        for fxx in fxx_range:
            jobs.append((date, cycle, fxx))

    n_workers = max_workers if max_workers is not None else default_max_workers(priority)
    n_workers = max(1, min(n_workers, len(jobs)))

    outputs: list[Path] = []

    if n_workers == 1:
        # Serial path — preserves strict ordering, useful for tests and
        # slow-disk scenarios. No thread overhead either.
        for date, cycle, fxx in jobs:
            ok, errors = _one_step(date, cycle, fxx, priority, common, var_list, lev_list)
            if ok is not None:
                outputs.append(ok)
                if on_step_ok is not None:
                    on_step_ok(date, cycle, fxx, ok)
            elif on_step_fail is not None:
                on_step_fail(date, cycle, fxx, errors)
        return outputs

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_one_step, date, cycle, fxx, priority, common, var_list, lev_list):
                (date, cycle, fxx)
            for date, cycle, fxx in jobs
        }
        for fut in as_completed(futures):
            date, cycle, fxx = futures[fut]
            ok, errors = fut.result()
            if ok is not None:
                outputs.append(ok)
                if on_step_ok is not None:
                    on_step_ok(date, cycle, fxx, ok)
            elif on_step_fail is not None:
                on_step_fail(date, cycle, fxx, errors)

    return outputs
