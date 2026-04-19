"""Timestamp expansion and job-list construction.

Turns the caller's high-level inputs (a list of cycle timestamps plus
a forecast horizon) into the flat ``[(date, cycle, fxx), ...]`` job
list the orchestrator iterates over.

Kept separate from :mod:`sharktopus.batch.orchestrator` so the
expansion rules can be tested in isolation, and so callers who build
their own scheduler (e.g. only odd forecast hours, or a sparse subset)
can skip :func:`generate_timestamps` while still calling
:func:`build_jobs`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

__all__ = ["build_jobs", "generate_timestamps"]


def _parse_stamp(stamp: str) -> datetime:
    if len(stamp) != 10 or not stamp.isdigit():
        raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
    return datetime.strptime(stamp, "%Y%m%d%H")


def generate_timestamps(start: str, end: str, step: int = 6) -> list[str]:
    """Return every ``YYYYMMDDHH`` cycle from *start* to *end* inclusive.

    *step* is in hours and must be positive. CONVECT uses 6 as the
    default (four GFS cycles per day); we honour that.
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


def build_jobs(
    timestamps: Sequence[str],
    ext: int,
    interval: int,
) -> list[tuple[str, str, int]]:
    """Expand cycle timestamps + forecast horizon into a flat job list.

    Each element is ``(date, cycle, fxx)`` ready to hand to a source's
    ``fetch_step``. *ext* is the horizon in hours; *interval* the step.
    Validation fires before any download, so callers learn about a typo
    in one timestamp before any network traffic happens.
    """
    if ext < 0:
        raise ValueError(f"ext must be >= 0, got {ext}")
    if interval <= 0:
        raise ValueError(f"interval must be > 0, got {interval}")

    fxx_range = list(range(0, ext + 1, interval))
    jobs: list[tuple[str, str, int]] = []
    for stamp in timestamps:
        if len(stamp) != 10 or not stamp.isdigit():
            raise ValueError(f"timestamp must be YYYYMMDDHH, got {stamp!r}")
        date, cycle = stamp[:8], stamp[8:]
        for fxx in fxx_range:
            jobs.append((date, cycle, fxx))
    return jobs
