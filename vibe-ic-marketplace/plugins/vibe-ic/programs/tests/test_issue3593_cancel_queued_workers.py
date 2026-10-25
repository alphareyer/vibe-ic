"""Issue #3593: interrupted coordinators cancel work that has not started."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import benchmark_dispatch as bd  # noqa: E402


class _Future:
    def __init__(self, value):
        self.value = value
        self.cancelled = False

    def result(self):
        if self.value is KeyboardInterrupt:
            raise KeyboardInterrupt("coordinator interrupted")
        return self.value

    def cancel(self):
        self.cancelled = True
        return True


class _Pool:
    def __init__(self, max_workers):
        self.max_workers = max_workers
        self.futures = []
        self.shutdown_calls = []

    def submit(self, worker, row):
        future = _Future(worker(row))
        self.futures.append(future)
        return future

    def shutdown(self, *, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


def test_interruption_cancels_every_future_before_cancel_pending_shutdown(monkeypatch):
    pool = _Pool(2)
    monkeypatch.setattr(bd.concurrent.futures, "ThreadPoolExecutor",
                        lambda max_workers: pool)
    monkeypatch.setattr(bd.concurrent.futures, "as_completed",
                        lambda futures: iter(futures))

    with pytest.raises(KeyboardInterrupt, match="coordinator interrupted"):
        bd._ordered_parallel_map(range(6),
                                 lambda row: KeyboardInterrupt if row == 0 else row,
                                 jobs=2)

    assert len(pool.futures) == 6
    assert all(future.cancelled for future in pool.futures)
    assert pool.shutdown_calls == [(True, True)]


def test_success_keeps_input_order_and_uses_normal_waiting_shutdown(monkeypatch):
    pool = _Pool(2)
    monkeypatch.setattr(bd.concurrent.futures, "ThreadPoolExecutor",
                        lambda max_workers: pool)
    monkeypatch.setattr(bd.concurrent.futures, "as_completed",
                        lambda futures: iter(reversed(list(futures))))

    assert bd._ordered_parallel_map([2, 4, 6], lambda row: row * 10, jobs=2) == [20, 40, 60]
    assert not any(future.cancelled for future in pool.futures)
    assert pool.shutdown_calls == [(True, False)]
