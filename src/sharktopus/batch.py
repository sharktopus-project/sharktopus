"""Batch-download orchestrator — Layer 2 start.

Drop-in replacement for CONVECT's ``menu_gfs.download_batch``:
iterates over a list of cycle timestamps, within each cycle iterates
over the requested forecast steps, and for each step tries the sources
in ``priority`` order until one succeeds. Signature mirrors
``download_batch_cli.py`` (``lat_s/lat_n/lon_w/lon_e`` separate floats
rather than a tuple) so callers migrating from CONVECT don't have to
rewrite their call sites.

Source registry is a plain dict keyed by name. Sources currently
registered:

* ``nomads`` → :mod:`sharktopus.sources.nomads`
* ``nomads_filter`` → :mod:`sharktopus.sources.nomads_filter`

The other CONVECT source names (``aws``, ``gcloud``, ``azure``,
``lambda``, ``azure_func``, ``gcloud_run``, ``ncep_ftp``, ``rda``)
register themselves as they're ported.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import grib
from .sources import SourceUnavailable, nomads, nomads_filter

__all__ = [
    "SourceRegistry",
    "fetch_batch",
    "generate_timestamps",
    "register_source",
    "registered_sources",
]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SourceFn = Callable[..., Path]


class SourceRegistry(dict[str, _SourceFn]):
    """Name → ``fetch_step`` callable. Just a dict, subclassed for typing."""


_REGISTRY = SourceRegistry(
    nomads=nomads.fetch_step,
    nomads_filter=nomads_filter.fetch_step,
)


def register_source(name: str, fetch_step: _SourceFn) -> None:
    """Register a source so :func:`fetch_batch` can route priority to it."""
    _REGISTRY[name] = fetch_step


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)


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

def fetch_batch(
    *,
    timestamps: Sequence[str],
    lat_s: float,
    lat_n: float,
    lon_w: float,
    lon_e: float,
    ext: int = 24,
    interval: int = 3,
    priority: Sequence[str] = ("nomads_filter", "nomads"),
    variables: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    product: str = "pgrb2.0p25",
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    on_step_ok: Callable[[str, str, int, Path], None] | None = None,
    on_step_fail: Callable[[str, str, int, list[tuple[str, Exception]]], None] | None = None,
) -> list[Path]:
    """Download every ``(cycle, fxx)`` step implied by the inputs.

    Parameters mirror CONVECT's ``download_batch``:

    * each element of *timestamps* is a ``YYYYMMDDHH`` string
      (date + cycle concatenated).
    * *ext* is the forecast horizon in hours (so fxx runs 0..ext).
    * *interval* is the step between fxx values.
    * *priority* is the list of source names (registered via
      :func:`register_source`) tried in order per step.

    ``nomads_filter`` needs *variables* and *levels*; if it's in the
    priority list they're required. Other sources consume the bbox plus
    whatever they support.

    Returns the list of produced Paths (one per successful step). Steps
    where every source in the priority list raised
    :class:`~sharktopus.sources.SourceUnavailable` are reported via
    *on_step_fail* and skipped; any other exception is re-raised.
    """
    if not timestamps:
        raise ValueError("timestamps must be non-empty")
    if ext < 0:
        raise ValueError(f"ext must be >= 0, got {ext}")
    if interval <= 0:
        raise ValueError(f"interval must be > 0, got {interval}")
    if not priority:
        raise ValueError("priority must be non-empty")
    unknown = set(priority) - set(_REGISTRY)
    if unknown:
        raise ValueError(
            f"unknown source(s) in priority: {sorted(unknown)}. "
            f"registered: {registered_sources()}"
        )

    bbox = (float(lon_w), float(lon_e), float(lat_s), float(lat_n))
    # Shared kwargs. Each source's fetch_step ignores keys it doesn't
    # know (they're all keyword-only) by us building per-source kwargs.
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

    fxx_range = range(0, ext + 1, interval)
    outputs: list[Path] = []

    for stamp in timestamps:
        if len(stamp) != 10 or not stamp.isdigit():
            raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
        date, cycle = stamp[:8], stamp[8:]

        for fxx in fxx_range:
            errors: list[tuple[str, Exception]] = []
            ok: Path | None = None
            for name in priority:
                fetch = _REGISTRY[name]
                kwargs = dict(common)
                if name == "nomads_filter":
                    if variables is None or levels is None:
                        raise ValueError(
                            "'nomads_filter' in priority but variables/levels not set"
                        )
                    kwargs["variables"] = list(variables)
                    kwargs["levels"] = list(levels)
                try:
                    ok = fetch(date, cycle, fxx, **kwargs)
                    break
                except SourceUnavailable as e:
                    errors.append((name, e))
                    continue
            if ok is not None:
                outputs.append(ok)
                if on_step_ok is not None:
                    on_step_ok(date, cycle, fxx, ok)
            else:
                if on_step_fail is not None:
                    on_step_fail(date, cycle, fxx, errors)
                # CONVECT's download_batch logs and moves on; we do the
                # same so partial runs succeed.

    return outputs
