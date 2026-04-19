"""Tests for :mod:`sharktopus.batch.queue` — priority queue with per-source heaps."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from sharktopus.batch.queue import MultiSourceQueue, Step


def _k(date: str, cycle: str = "00", fxx: int = 0) -> tuple[str, str, int]:
    return (date, cycle, fxx)


# ---------------------------------------------------------------------------
# Basic push / pop / ordering
# ---------------------------------------------------------------------------

def test_empty_sources_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        MultiSourceQueue([])


def test_pop_unknown_source_raises():
    q = MultiSourceQueue(["aws"])
    with pytest.raises(KeyError):
        q.pop("gcloud")


def test_push_and_pop_returns_earliest_first():
    q = MultiSourceQueue(["aws"])
    q.push(Step(_k("20240103")))
    q.push(Step(_k("20240101")))
    q.push(Step(_k("20240102")))
    assert q.pop("aws").key == _k("20240101")
    assert q.pop("aws").key == _k("20240102")
    assert q.pop("aws").key == _k("20240103")


def test_pop_returns_none_when_empty_and_stopped():
    q = MultiSourceQueue(["aws"])
    q.stop()
    assert q.pop("aws") is None


def test_pending_counter():
    q = MultiSourceQueue(["aws"])
    assert q.pending == 0
    q.push(Step(_k("20240101")))
    q.push(Step(_k("20240102")))
    assert q.pending == 2
    s = q.pop("aws")
    q.mark_done(s)
    assert q.pending == 1
    q.mark_done(q.pop("aws"))
    assert q.pending == 0


def test_mark_done_auto_stops_when_pending_zero():
    q = MultiSourceQueue(["aws"])
    q.push(Step(_k("20240101")))
    s = q.pop("aws")
    q.mark_done(s)
    # Now pending=0, queue should auto-stop; pop returns None.
    assert q.pop("aws") is None


# ---------------------------------------------------------------------------
# Blacklist / re-enqueue / multi-source
# ---------------------------------------------------------------------------

def test_push_skips_blacklisted_heaps():
    q = MultiSourceQueue(["aws", "gcloud"])
    q.push(Step(_k("20240101"), blacklist=frozenset({"aws"})))
    # aws has nothing eligible; gcloud has one.
    # With only one item in queue and pending=1, aws should still block
    # (not stopped yet). Use stop+pop to verify aws heap is empty.
    q.stop()
    assert q.pop("aws") is None
    # But before stop, gcloud should've returned it. Rebuild:
    q2 = MultiSourceQueue(["aws", "gcloud"])
    q2.push(Step(_k("20240101"), blacklist=frozenset({"aws"})))
    assert q2.pop("gcloud").key == _k("20240101")


def test_blacklist_covering_all_sources_is_final_failure():
    q = MultiSourceQueue(["aws", "gcloud"])
    q.push(Step(_k("20240101"), blacklist=frozenset({"aws", "gcloud"})))
    # Blacklist covers everything → pending should already be 0.
    assert q.pending == 0
    # And queue stopped; pop returns None.
    assert q.pop("aws") is None


def test_reenqueue_bumps_version_and_obsolete_copies_are_discarded():
    """After re-enqueue, the stale copy in the failed heap is discarded on pop."""
    q = MultiSourceQueue(["aws", "gcloud"])
    original = Step(_k("20240101"))
    q.push(original)
    # Simulate aws worker popping, failing, and re-enqueueing.
    s = q.pop("aws")
    assert s.key == _k("20240101")
    q.push(replace(s, blacklist=frozenset({"aws"})))
    # Now gcloud must get it; aws must not see it again (version obsolete).
    # aws's internal heap might still have the v1 copy — stop and verify
    # pop returns None instead of the stale copy.
    s2 = q.pop("gcloud")
    assert s2.key == _k("20240101")
    assert "aws" in s2.blacklist
    q.mark_done(s2)
    # Queue is done now.
    assert q.pop("aws") is None


def test_mark_done_invalidates_other_heaps():
    """A step pushed to multiple heaps is only returned by one."""
    q = MultiSourceQueue(["aws", "gcloud"])
    q.push(Step(_k("20240101")))
    # aws gets it first.
    s = q.pop("aws")
    q.mark_done(s)
    # gcloud's heap still has a copy, but pop must drain it and see
    # pending=0 → stopped → None.
    assert q.pop("gcloud") is None


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_pop_delivers_each_step_once():
    """N workers across M sources race to pop K steps; none is delivered twice."""
    q = MultiSourceQueue(["aws", "gcloud", "azure"])
    K = 50
    for i in range(K):
        q.push(Step((f"2024{1 + i:04d}", "00", 0)))

    seen: list[tuple] = []
    seen_lock = threading.Lock()

    def worker(source: str):
        while True:
            s = q.pop(source)
            if s is None:
                return
            with seen_lock:
                seen.append(s.key)
            q.mark_done(s)

    threads = [
        threading.Thread(target=worker, args=(src,))
        for src in ["aws", "aws", "gcloud", "gcloud", "azure"]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "worker deadlocked"

    assert len(seen) == K, f"expected {K} steps, got {len(seen)}"
    assert len(set(seen)) == K, "some step delivered twice"


def test_concurrent_pop_single_step_delivered_once():
    """A single step pushed to many heaps is popped by exactly one worker.

    Regression test: an earlier version only had version/done checks, so
    when the same Step sat at the top of every heap, multiple workers
    could claim it concurrently (each popping from its own heap before
    mark_done ran). An in-progress set now prevents the race.
    """
    q = MultiSourceQueue(["aws", "gcloud", "azure"])
    q.push(Step(_k("20240101")))

    claimed: list = []
    lock = threading.Lock()
    start = threading.Event()

    def worker(source: str):
        start.wait()
        s = q.pop(source)
        if s is not None:
            with lock:
                claimed.append((source, s.key))
            q.mark_done(s)

    threads = [
        threading.Thread(target=worker, args=(src,))
        for src in ["aws", "gcloud", "azure"]
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=2)
        assert not t.is_alive()

    assert len(claimed) == 1, f"expected exactly one claim, got {claimed!r}"


def test_wakeup_on_push():
    """A worker blocked on pop must unblock when a push makes a step available."""
    q = MultiSourceQueue(["aws"])
    result: dict = {}

    def worker():
        result["step"] = q.pop("aws")

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)  # let the worker reach the wait()
    q.push(Step(_k("20240101")))
    t.join(timeout=1)
    assert not t.is_alive()
    assert result["step"].key == _k("20240101")
    q.mark_done(result["step"])


def test_stop_wakes_all_waiting_workers():
    """stop() must wake every worker blocked on an empty heap."""
    q = MultiSourceQueue(["aws", "gcloud"])
    done = threading.Barrier(3)
    results: list = []

    def worker(src: str):
        s = q.pop(src)
        results.append((src, s))
        done.wait(timeout=2)

    ts = [threading.Thread(target=worker, args=(s,)) for s in ["aws", "gcloud"]]
    for t in ts:
        t.start()
    time.sleep(0.05)
    q.stop()
    done.wait(timeout=2)
    for t in ts:
        t.join(timeout=1)
        assert not t.is_alive()
    assert all(s is None for _, s in results)
