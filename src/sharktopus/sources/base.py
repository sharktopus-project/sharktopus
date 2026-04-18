"""Shared helpers for :mod:`sharktopus.sources`.

Everything that's common across mirrors lives here: the
:class:`SourceUnavailable` exception, input validators, the canonical
output filename, and a plain-stdlib HTTP streamer with retry.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

__all__ = [
    "SourceUnavailable",
    "canonical_filename",
    "check_retention",
    "supports_date",
    "validate_cycle",
    "validate_date",
    "stream_download",
]


class SourceUnavailable(RuntimeError):
    """This mirror cannot serve the requested step.

    Raised on HTTP 404, NOMADS retention window exceeded, connection
    errors that exhaust retries, and any other signal that the caller
    should try a different source.
    """


_VALID_CYCLES = frozenset({"00", "06", "12", "18"})


def validate_cycle(cycle: str) -> str:
    """Return *cycle* if it is one of ``"00"/"06"/"12"/"18"``; raise otherwise."""
    if cycle not in _VALID_CYCLES:
        raise ValueError(f"cycle must be one of {sorted(_VALID_CYCLES)}, got {cycle!r}")
    return cycle


def validate_date(date: str) -> datetime:
    """Parse ``YYYYMMDD`` into a :class:`datetime` (UTC midnight)."""
    try:
        return datetime.strptime(date, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"date must be YYYYMMDD, got {date!r}") from e


def canonical_filename(cycle: str, fxx: int, product: str = "pgrb2.0p25") -> str:
    """Build the canonical GFS filename used by NOAA mirrors.

    Example: ``canonical_filename("00", 6)`` returns
    ``"gfs.t00z.pgrb2.0p25.f006"``.
    """
    validate_cycle(cycle)
    if fxx < 0:
        raise ValueError(f"fxx must be >= 0, got {fxx}")
    return f"gfs.t{cycle}z.{product}.f{fxx:03d}"


def stream_download(
    url: str,
    dst: str | Path,
    *,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 10.0,
    chunk_size: int = 1 << 15,  # 32 KiB
    headers: dict[str, str] | None = None,
    opener: Callable[..., "urllib.request.OpenerDirector"] | None = None,
) -> Path:
    """Download *url* into *dst* with streaming and retry.

    Uses :mod:`urllib.request` (no third-party deps). On HTTP 404, raises
    :class:`SourceUnavailable` immediately without retrying. On transient
    errors (connection reset, timeout, 5xx) retries up to *max_retries*
    times with *retry_wait* seconds between attempts. Writes to
    ``dst + ".part"`` and renames atomically on success.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            _open = opener or urllib.request.urlopen
            with _open(req, timeout=timeout) as resp, open(part, "wb") as out:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
            part.replace(dst)
            return dst
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _cleanup(part)
                raise SourceUnavailable(f"{url} → HTTP 404") from e
            last_exc = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
        _cleanup(part)
        if attempt < max_retries:
            time.sleep(retry_wait)
    raise SourceUnavailable(
        f"{url} unreachable after {max_retries} attempts: {last_exc}"
    ) from last_exc


def _cleanup(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def check_retention(date: str, *, days: int, now: datetime | None = None) -> None:
    """Raise :class:`SourceUnavailable` if *date* is older than *days* from now.

    NOMADS keeps ~10 days, RDA keeps everything, etc. Call from the source
    module before building URLs.
    """
    dt = validate_date(date)
    now = now or datetime.now(tz=timezone.utc)
    if dt < now - timedelta(days=days):
        raise SourceUnavailable(
            f"date {date} is older than retention window ({days} days)"
        )


def supports_date(
    date: str,
    *,
    earliest: datetime | None,
    retention_days: int | None,
    now: datetime | None = None,
) -> bool:
    """Return ``True`` if *date* falls inside a source's serving window.

    *earliest* is the oldest date the mirror ever published (``None`` =
    no lower bound known). *retention_days* is the rolling window size
    in days (``None`` = the mirror keeps data indefinitely).

    Used by per-source ``supports()`` helpers so ``batch.available_sources``
    can filter the default priority list before hitting the network.
    """
    dt = validate_date(date)
    if earliest is not None and dt < earliest:
        return False
    if retention_days is not None:
        now = now or datetime.now(tz=timezone.utc)
        if dt < now - timedelta(days=retention_days):
            return False
    return True
