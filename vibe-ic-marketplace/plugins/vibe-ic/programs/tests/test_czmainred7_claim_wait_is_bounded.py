#!/usr/bin/env python3
"""The checkout claim's wait must be bounded by ITERATION, not only by a clock.

WHY THIS FILE EXISTS
====================
`_CheckoutClaim.__enter__` polled for the lock in a `while True:` whose only
stop was `time.monotonic() >= deadline`. Five shipped tests were RED on that
one line (`gate_host_independence_check.py:1311`), all of them saying the same
thing: the shipped `programs/` tree must carry no unguarded polling loop, and
this was the one offender. MEASURED 2026-09-08 on 8HD-8 at main `8862fd37`
inside the pinned image — 5/5 red, and red on 8hd-3 too (lane czmainred6), so
the subject was the TREE, not either machine.

Those five ask the question of the whole corpus. This file asks it of THIS
loop, so a future refactor that reintroduces the unbounded shape is named here
by behaviour and not only by a corpus scan:

  * the wait TERMINATES and reports `gave_up` when the lock is never granted —
    a clock is not the thing being tested, so the clock is not what stops it;
  * the loop is driven by `_watchdog.loop_guard`, i.e. it has a hard iteration
    cap, proven by counting the guard's own iterations rather than by reading
    the source;
  * the grant path is unchanged: a free lock is taken on the first poll.

chip-AGNOSTIC: lock plumbing only. No design, PDK, vendor or tool literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _watchdog as _wd                       # noqa: E402
import gate_host_independence_check as G      # noqa: E402


def test_a_free_lock_is_granted_on_the_first_poll(tmp_path):
    claim = G._CheckoutClaim(tmp_path, True, wait_s=5.0, lock_root=tmp_path)
    with claim as c:
        assert c.held is True, c.why
        assert c.why == "held"


def test_a_held_lock_gives_up_and_the_loop_is_iteration_bounded(tmp_path,
                                                                monkeypatch):
    """A second claim on a lock the first still holds must RETURN, and the
    number of polls it makes must be capped by `loop_guard`, not by the clock.

    The clock is frozen on purpose: with `time.monotonic` constant the
    `deadline` test can never fire, so the ONLY thing that can end this loop is
    the iteration cap. Against the pre-fix `while True:` this hangs forever —
    which is exactly the property the five corpus tests were complaining
    about, stated as behaviour."""
    holder = G._CheckoutClaim(tmp_path, True, wait_s=5.0, lock_root=tmp_path)
    seen = {"iters": 0, "guard": None}
    real_guard = _wd.loop_guard

    def counting_guard(name, **kw):
        g = real_guard(name, **kw)
        seen["guard"] = g
        return g

    with holder as h:
        assert h.held is True, h.why
        monkeypatch.setattr(G.time, "monotonic", lambda: 1000.0)  # frozen
        monkeypatch.setattr(G.time, "sleep", lambda s: None)      # no wall time
        monkeypatch.setattr(G._wd, "loop_guard", counting_guard)
        second = G._CheckoutClaim(tmp_path, True, wait_s=5.0,
                                  lock_root=tmp_path)
        with second as s:
            assert s.held is False
            assert "another driver held a conflicting claim" in s.why
    g = seen["guard"]
    assert g is not None, "the wait did not go through loop_guard at all"
    assert g.reason == "max_iter", (
        "a frozen clock leaves the ITERATION CAP as the only stop; the loop "
        "ended for another reason: %r" % (g.reason,))
    assert 0 < g.iterations <= g.max_iter
    # The cap is derived from the wait the caller asked for and the poll
    # interval, so the two cannot drift apart unnoticed.
    assert g.max_iter == int(5.0 / G._CLAIM_POLL_S) + 2


# ---------------------------------------------------------------------------
# The route pin is new shipped test infrastructure, so it is itself tested —
# in BOTH directions. A pin that silently failed to pin would turn seven
# host-dependent tests into seven tests that merely look pinned.
# ---------------------------------------------------------------------------
import _container_exec as _ce                 # noqa: E402
import _container_route as _route             # noqa: E402


def test_pin_container_route_makes_the_route_a_container_one(monkeypatch):
    _route.pin_container_route(monkeypatch)
    assert _ce.no_container_route() is False
    assert _ce.local_exec_mode("t_pin_c") is False
    argv = _ce.exec_argv("some_container", "true", tag="t_pin_c")
    assert argv[0] == "docker" and argv[1] == "exec", argv


def test_pin_local_route_makes_the_route_a_local_one(monkeypatch):
    _route.pin_local_route(monkeypatch)
    assert _ce.no_container_route() is True
    assert _ce.local_exec_mode("t_pin_l") is True
    argv = _ce.exec_argv("some_container", "true", tag="t_pin_l")
    assert argv[0] != "docker", argv


def test_the_pin_leaves_every_other_lookup_alone(monkeypatch):
    """It answers for `docker` and delegates the rest, so a test that pins the
    route does not accidentally blind an unrelated tool probe."""
    _route.pin_container_route(monkeypatch)
    import shutil
    assert _ce.shutil.which("sh") == shutil.which("sh")
