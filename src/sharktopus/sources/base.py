"""Shared helpers for :mod:`sharktopus.sources`.

Everything that's common across mirrors lives here: the
:class:`SourceUnavailable` exception, input validators, the canonical
output filename, and a plain-stdlib HTTP streamer with retry.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

__all__ = [
    "SourceUnavailable",
    "canonical_filename",
    "check_retention",
    "fetch_text",
    "head_size",
    "stream_byte_ranges",
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


def fetch_text(
    url: str,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
    retry_wait: float = 5.0,
    headers: dict[str, str] | None = None,
    opener: Callable[..., "urllib.request.OpenerDirector"] | None = None,
) -> str:
    """Fetch *url* and return the response body as text (UTF-8).

    Used for ``.idx`` files, which are tiny (< 50 KB). HTTP 404 raises
    :class:`SourceUnavailable`; other transient errors are retried.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            _open = opener or urllib.request.urlopen
            with _open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SourceUnavailable(f"{url} → HTTP 404") from e
            last_exc = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
        if attempt < max_retries:
            time.sleep(retry_wait)
    raise SourceUnavailable(
        f"{url} unreachable after {max_retries} attempts: {last_exc}"
    ) from last_exc


def head_size(
    url: str,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
    retry_wait: float = 5.0,
    headers: dict[str, str] | None = None,
    opener: Callable[..., "urllib.request.OpenerDirector"] | None = None,
) -> int:
    """Return the total byte length of *url* via HTTP HEAD.

    Needed to compute the end offset of the last wanted GRIB2 record
    (the .idx only publishes the *start* offset of each). Falls back to
    a ``Range: bytes=0-0`` GET if the server rejects HEAD — some S3-like
    mirrors do.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {}, method="HEAD")
            _open = opener or urllib.request.urlopen
            with _open(req, timeout=timeout) as resp:
                length = resp.headers.get("Content-Length")
                if length is not None:
                    return int(length)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SourceUnavailable(f"{url} → HTTP 404") from e
            last_exc = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e

        try:
            req = urllib.request.Request(
                url, headers={**(headers or {}), "Range": "bytes=0-0"}
            )
            _open = opener or urllib.request.urlopen
            with _open(req, timeout=timeout) as resp:
                cr = resp.headers.get("Content-Range")
                if cr and "/" in cr:
                    total = cr.rsplit("/", 1)[-1].strip()
                    if total.isdigit():
                        return int(total)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SourceUnavailable(f"{url} → HTTP 404") from e
            last_exc = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
        if attempt < max_retries:
            time.sleep(retry_wait)
    raise SourceUnavailable(
        f"HEAD {url} failed after {max_retries} attempts: {last_exc}"
    ) from last_exc


def stream_byte_ranges(
    url: str,
    ranges: Sequence[tuple[int, int]],
    dst: str | Path,
    *,
    max_workers: int = 4,
    timeout: float = 60.0,
    max_retries: int = 3,
    retry_wait: float = 5.0,
    headers: dict[str, str] | None = None,
    opener: Callable[..., "urllib.request.OpenerDirector"] | None = None,
) -> Path:
    """Download *ranges* of *url* in parallel and concatenate into *dst*.

    Each ``(start, end)`` tuple becomes one ``Range: bytes=start-end``
    GET. Parts are downloaded concurrently with a bounded thread pool
    and then concatenated in original *ranges* order so the resulting
    file is a valid GRIB2 stream (records preserved in record-number
    order).

    Atomic write via ``dst + ".part"``. HTTP 404 on any range surfaces
    as :class:`SourceUnavailable`.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")

    if not ranges:
        raise ValueError("ranges must be non-empty")

    def _fetch_one(i: int, start: int, end: int) -> tuple[int, bytes]:
        _headers = {**(headers or {}), "Range": f"bytes={start}-{end}"}
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=_headers)
                _open = opener or urllib.request.urlopen
                with _open(req, timeout=timeout) as resp:
                    return i, resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise SourceUnavailable(f"{url} → HTTP 404") from e
                last_exc = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_exc = e
            if attempt < max_retries:
                time.sleep(retry_wait)
        raise SourceUnavailable(
            f"range {start}-{end} of {url} failed: {last_exc}"
        ) from last_exc

    results: dict[int, bytes] = {}
    n_workers = max(1, min(int(max_workers), len(ranges)))
    try:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [
                pool.submit(_fetch_one, i, s, e)
                for i, (s, e) in enumerate(ranges)
            ]
            for fut in as_completed(futures):
                i, data = fut.result()
                results[i] = data
        with open(part, "wb") as out:
            for i in range(len(ranges)):
                out.write(results[i])
        part.replace(dst)
        return dst
    except Exception:
        _cleanup(part)
        raise


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
