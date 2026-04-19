"""Priority queue with per-source heaps and lazy invalidation.

Used by :func:`sharktopus.batch.fetch_batch` in *spread* mode to
orchestrate a single logical stream of download jobs across several
independent source executors (one thread per ``source × worker``
slot), while preserving three invariants:

1. **Global ordering.** The next step any worker pulls is always the
   oldest pending ``(date, cycle, fxx)``. WRF runs sequentially
   date-after-date, so stalling the earliest date stalls the entire
   pipeline; everyone takes from the same logical front.
2. **No fallback bypass of rate-limits.** When a step fails in
   source A, the worker does **not** call source B directly — it
   re-enqueues the step with A added to the blacklist and returns to
   its own queue. Source B's own workers pick the step up at their
   own pace, so B's published concurrency ceiling is never exceeded.
3. **O(log N) per operation.** Multiple heaps (one per source) hold
   references to the same step; when a step is re-enqueued with a
   new blacklist, the old copies become obsolete but are lazily
   discarded on pop via a version check.
4. **Single claim.** The same step sits at the top of several heaps
   until one worker wins the race to :meth:`pop` it — an in-progress
   set ensures concurrent pops from other sources skip that key
   until the first attempt resolves (success → ``mark_done``; failure
   → ``push`` with the source blacklisted).

The queue is source-agnostic: it knows a set of source names at
construction time and nothing else. Workers identify themselves on
every :meth:`pop` so the queue can hand them only steps their source
is still eligible for.
"""

from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field, replace
from typing import Sequence

__all__ = ["Step", "MultiSourceQueue"]


@dataclass(order=True, frozen=True)
class Step:
    """One unit of work: ``(date, cycle, fxx)`` with attempt metadata.

    Only ``key`` participates in ordering — versions and blacklists
    are metadata invisible to the heap's sort. Two steps for the
    same key compare equal (the latest-version one wins via the
    version check on pop).
    """

    key: tuple[str, str, int]            # (date, cycle, fxx) — sort key
    version: int = field(compare=False, default=1)
    blacklist: frozenset[str] = field(
        compare=False, default_factory=frozenset
    )


class MultiSourceQueue:
    """Priority queue sharded by source with lazy invalidation.

    Construct with the set of *sources* that will pull from it. Each
    source gets its own heap; a step is inserted into every heap
    whose source is not in the step's blacklist. :meth:`pop` blocks
    until an eligible step becomes available, returning ``None`` if
    the queue has been stopped (all pending steps completed or
    explicitly failed).

    Thread-safe: uses one :class:`threading.Lock` shared across
    per-source :class:`threading.Condition` objects, so a push wakes
    only the workers whose sources are actually eligible for the new
    step.

    All operations are O(log N) amortized (N = pending steps):

    * :meth:`push` — ``O(S · log N)`` where S ≤ sources available.
    * :meth:`pop`  — ``O(log N)`` amortized; the while-loop that
      drains obsolete heap tops is bounded in total by the number
      of re-enqueues (one per source per step at worst).
    * :meth:`mark_done` / :meth:`stop` — ``O(1)`` plus notify.
    """

    def __init__(self, sources: Sequence[str]):
        if not sources:
            raise ValueError("sources must be non-empty")
        # Lazy deduplication — callers sometimes pass priority lists
        # with duplicates; collapse them so the heap count matches
        # the actual distinct workers.
        self._sources: tuple[str, ...] = tuple(dict.fromkeys(sources))
        self._heaps: dict[str, list[Step]] = {s: [] for s in self._sources}
        self._version: dict[tuple, int] = {}
        self._done: set[tuple] = set()
        # Keys currently held by some worker. Prevents two sources from
        # racing to claim the same step when it sits at the top of both
        # heaps. Cleared on mark_done (success) or on push (re-enqueue
        # after failure).
        self._in_progress: set[tuple] = set()
        self._pending: int = 0
        self._stopped: bool = False
        self._lock = threading.Lock()
        self._cv: dict[str, threading.Condition] = {
            s: threading.Condition(self._lock) for s in self._sources
        }

    @property
    def sources(self) -> tuple[str, ...]:
        return self._sources

    @property
    def pending(self) -> int:
        """Count of keys not yet completed or marked as final failures.

        Useful for tests and for the orchestrator to decide when to
        stop. Not safe to rely on while other threads are pushing;
        read it under a known-quiescent state.
        """
        with self._lock:
            return self._pending

    def push(self, step: Step) -> None:
        """Insert *step* into every heap whose source is eligible.

        If *step*'s key has never been seen, ``pending`` increments.
        Otherwise this is a re-enqueue (after a failure in some
        source); the step's version is bumped so older copies in
        other heaps become obsolete at the next :meth:`pop`.

        If the blacklist already covers every known source, the
        step is marked done immediately with no heap insertion —
        there's nobody left to try, so it's a final failure.
        """
        with self._lock:
            cur = self._version.get(step.key, 0)
            new_version = cur + 1
            step = replace(step, version=new_version)
            self._version[step.key] = new_version
            # Re-enqueue after failure: worker releasing the claim.
            self._in_progress.discard(step.key)
            if cur == 0:
                self._pending += 1

            eligible = [s for s in self._sources if s not in step.blacklist]
            if not eligible:
                # Nobody can try this step — final failure.
                self._done.add(step.key)
                self._pending -= 1
                if self._pending == 0:
                    self._stopped = True
                    for cv in self._cv.values():
                        cv.notify_all()
                return

            for source in eligible:
                heapq.heappush(self._heaps[source], step)
                self._cv[source].notify()

    def pop(self, source: str) -> Step | None:
        """Return the next step *source* is eligible to attempt.

        Blocks until either a heap-eligible step is available or the
        queue is stopped. Returns ``None`` when stopped. Drains any
        obsolete (version-mismatched) or already-completed entries
        from the top of the heap before returning — cheap, because
        each obsolete entry is popped at most once.
        """
        if source not in self._heaps:
            raise KeyError(f"source {source!r} not registered with queue")

        cv = self._cv[source]
        with cv:
            heap = self._heaps[source]
            while True:
                while heap and (
                    heap[0].version != self._version.get(heap[0].key)
                    or heap[0].key in self._done
                    or heap[0].key in self._in_progress
                ):
                    heapq.heappop(heap)
                if heap:
                    step = heapq.heappop(heap)
                    self._in_progress.add(step.key)
                    return step
                if self._stopped:
                    return None
                cv.wait()

    def mark_done(self, step: Step) -> None:
        """Mark *step*'s key as completed. Lazy-invalidates other heaps."""
        with self._lock:
            if step.key in self._done:
                return
            self._done.add(step.key)
            self._in_progress.discard(step.key)
            self._pending -= 1
            if self._pending == 0:
                self._stopped = True
                for cv in self._cv.values():
                    cv.notify_all()

    def stop(self) -> None:
        """Force-stop the queue. All subsequent :meth:`pop` calls return ``None``."""
        with self._lock:
            self._stopped = True
            for cv in self._cv.values():
                cv.notify_all()
