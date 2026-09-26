"""A calibration taken under a caller's fakes is never served to anyone else.

`instrument_calibration.check()` memoises one calibration per instrument per
process. Before this file, it memoised WHATEVER the first call measured: a test
that monkeypatched an instrument's collaborator and let calibration run under
that patch left a verdict computed from FAKES in the cache, and every later
caller — the real one included — was handed it. The outcome depended on test
order (lane rfa, T118, left it open as a latent finding).

The rule the module follows now: a calibration is stored only when what it
measured is the REGISTERED instrument — its own judge, its own samples, its own
expectation — running over collaborators nobody replaced. Anything else is
measured for the caller who asked and then forgotten. A stored REAL calibration
is still what a later caller gets, fakes or not: it certifies the instrument,
and a test that fakes a collaborator after calibrating (lane rfa's `_call`) is
testing the program's control flow, not re-certifying the reader.

Each test below runs BOTH orders explicitly, so it does not depend on the
order pytest picks.
"""
from __future__ import annotations

import subprocess
import subprocess as _sp
import sys
from pathlib import Path
from unittest import mock

import pytest

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))

import instrument_calibration as C  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402

ROUTE = "phase3_one_shot_runner::antenna_routing_incomplete"


def _name_judged_by(judge) -> str:
    names = [n for n, i in C.INSTRUMENTS.items() if i.judge is judge]
    assert len(names) == 1, names
    return names[0]


def _fresh(name: str) -> None:
    C._CACHE.pop(name, None)


def _always_fires(_log):
    return True


# ── a faked collaborator, reached through a function-local import ──────────

def test_a_fake_first_does_not_poison_the_real_caller(monkeypatch):
    """Order 1: calibrate under a fake, then for real."""
    _fresh(ROUTE)
    with monkeypatch.context() as m:
        m.setattr(R, "antenna_routing_incomplete", _always_fires)
        under_fake = C.check(ROUTE)
    # the witness: the fake really reached the judge
    assert under_fake.state == C.MISCALIBRATED
    assert "negative" in under_fake.failed_sides
    real = C.check(ROUTE)
    assert real.state == C.CALIBRATED, real.detail
    assert real.negative_outcome is None


def test_a_real_calibration_survives_a_later_fake(monkeypatch):
    """Order 2: calibrate for real, then under a fake, then for real."""
    _fresh(ROUTE)
    first = C.check(ROUTE)
    assert first.state == C.CALIBRATED
    with monkeypatch.context() as m:
        m.setattr(R, "antenna_routing_incomplete", _always_fires)
        # the stored REAL calibration is what a faking caller is served
        assert C.check(ROUTE) is first
    assert C.check(ROUTE) is first


def test_a_mock_is_a_fake_too():
    _fresh(ROUTE)
    with mock.patch.object(R, "antenna_routing_incomplete",
                           mock.MagicMock(return_value=True)):
        assert C.check(ROUTE).state == C.MISCALIBRATED
    assert C.check(ROUTE).state == C.CALIBRATED


# ── a faked collaborator in a module the instrument only names ─────────────

def test_a_fake_in_a_module_reached_only_by_attribute(monkeypatch):
    """`subprocess.run` faked and called through a module-level alias
    (`import subprocess as _sp`, the shape `import X as R` takes all over this
    tree): the subprocess module's own code never runs and no code object
    names it, so it is reached only because the caller's globals bind it. (No
    calibrated instrument in this tree calls `subprocess.run` in its pair —
    measured by faking it under every one — so this is asserted on the reach
    itself.)"""
    def _silent(*_a, **_k):
        return None             # nothing of subprocess's own may run

    def _calls_run():
        return _sp.run(["true"])

    monkeypatch.setattr(subprocess, "run", _silent)
    _, reached = C._reaching(_calls_run)
    assert "subprocess.run" in C._foreign_bindings(reached)
    monkeypatch.undo()
    _, reached = C._reaching(_calls_run)
    assert "subprocess.run" not in C._foreign_bindings(reached)


def test_a_fake_on_an_imported_name_is_read_against_the_import():
    """The runner binds `_drop_include_hubs` by `from _rtl_include_hub import
    drop_include_hubs as ...` and `_decide_synth_frontend` by `... =
    _sf.decide_synth_frontend`: both genuine, read from its source; a
    top-level function from anywhere else under either name is not."""
    home = "phase3_one_shot_runner"
    decls = C._declared_bindings(home)
    assert ("from", "_rtl_include_hub", "drop_include_hubs") in \
        decls["_drop_include_hubs"]
    assert ("alias", "_sf.decide_synth_frontend") in \
        decls["_decide_synth_frontend"]
    for name in ("_drop_include_hubs", "_decide_synth_frontend"):
        assert not C._is_foreign(getattr(R, name), home, name)
        assert C._is_foreign(_always_fires, home, name)
    # and a name the module DEFINES may hold only the module's own function
    assert ("def",) in C._declared_bindings(home)["antenna_routing_incomplete"]
    assert C._is_foreign(_always_fires, home, "antenna_routing_incomplete")


# ── the module's own state, and the instrument's own fields ────────────────

def test_a_moved_fixture_directory_is_not_cached(monkeypatch, tmp_path):
    _fresh(ROUTE)
    with monkeypatch.context() as m:
        m.setattr(C, "FIXTURES", tmp_path)
        assert C.check(ROUTE).state == C.MISCALIBRATED
    assert C.check(ROUTE).state == C.CALIBRATED


@pytest.mark.parametrize("order", ["replaced_first", "real_first"])
def test_a_replaced_judge_is_measured_and_never_stored(order):
    inst = C.INSTRUMENTS[ROUTE]
    original = inst.judge
    _fresh(ROUTE)
    if order == "real_first":
        assert C.check(ROUTE).state == C.CALIBRATED
    object.__setattr__(inst, "judge", lambda _artefact: None)
    try:
        replaced = C.check(ROUTE)
    finally:
        object.__setattr__(inst, "judge", original)
    # the judge that was asked about is the one that was measured
    assert replaced.state == C.MISCALIBRATED
    assert replaced.failed_sides == ("positive",)
    assert C.check(ROUTE).state == C.CALIBRATED


# ── an environment's start-up bindings are not a caller's fakes ────────────

def _installed_hook():
    """The shape the EDA image's `sitecustomize` leaves in `sys.excepthook`:
    apport's `install.<locals>.partial_apport_excepthook`, a closure defined
    outside the interpreter's library. The host has no such hook."""
    def partial_excepthook(*_exc):
        return None
    return partial_excepthook


def test_a_start_up_hook_the_pair_never_calls_does_not_block_the_cache(
        monkeypatch):
    """F8b: with apport's hook in `sys.excepthook`, every calibration in the
    image was measured and never stored, so a caller that pre-calibrated and
    then faked a collaborator (test_t63c's `_measure`) was re-calibrated under
    its own fake and refused as Uncalibrated -- in the image only."""
    hook = _installed_hook()
    monkeypatch.setattr(sys, "excepthook", hook)
    # the witness: read alone, the hook IS a foreign binding of `sys`
    assert C._is_foreign(hook, "sys", "excepthook")
    _fresh(ROUTE)
    real = C.check(ROUTE)
    assert real.state == C.CALIBRATED, real.detail
    assert C._CACHE.get(ROUTE) is real
    # and the caller who fakes afterwards is served the real calibration
    with monkeypatch.context() as m:
        m.setattr(R, "antenna_routing_incomplete", _always_fires)
        assert C.check(ROUTE) is real


def test_a_start_up_hook_does_not_hide_a_fake_the_pair_calls(monkeypatch):
    """The other side: the fake the judge DOES call is still never stored,
    hook or no hook."""
    monkeypatch.setattr(sys, "excepthook", _installed_hook())
    _fresh(ROUTE)
    with monkeypatch.context() as m:
        m.setattr(R, "antenna_routing_incomplete", _always_fires)
        assert C.check(ROUTE).state == C.MISCALIBRATED
    assert ROUTE not in C._CACHE
    assert C.check(ROUTE).state == C.CALIBRATED


# ── the positive control: nothing in the real tree reads as a fake ─────────

def test_every_real_calibration_is_stored():
    """If the fake detector fired on the real tree, nothing would be cached and
    every caller would re-run every pair (and a pre-calibrating test like lane
    rfa's `_call` would calibrate again under its own fakes)."""
    for name in C.INSTRUMENTS:
        _fresh(name)
    cals = C.check_all()
    assert set(cals) == set(C.INSTRUMENTS)
    missing = sorted(n for n in C.INSTRUMENTS if C._CACHE.get(n) is not cals[n])
    assert missing == [], missing
