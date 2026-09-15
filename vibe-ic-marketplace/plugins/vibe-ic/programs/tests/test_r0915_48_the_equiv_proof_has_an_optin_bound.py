"""R-0915-48 — the equiv proof gets a bound the operator ASKS FOR, and only then.

THE PROBLEM, MEASURED on sha256 run12: a Yosys equivalence proof stuck on ONE hard
point holds 99.9% CPU indefinitely. `_watchdog` treats progress as an OR — output
grew OR the log grew OR a domain probe changed OR **the CPU advanced** — and
`_container_exec` supplies a container-tree CPU probe, so a spinning proof is
"progressing" for ever and the run never ends. run12 sat 4 h 31 m with its live report
frozen at one byte count, 622 of 1580 points proved.

WHY THE BOUND IS OPT-IN AND MUST NOT BECOME THE DEFAULT. This file has removed a bound
on this leg TWICE, each time with its own measurement, and both are in its comments:

  * 2026-09-06 — pinning `wrap_with_container_timeout` to the budget SIGKILLed a proof
    at budget-5s that was emitting output at a full core (5360 s of 7195 s, 1374 points
    proved, 0 failed, still advancing). "A bigger number would be the same defect with
    a later date."
  * earlier — a host-side SILENCE window "killed two healthy RTLLM LEC jobs ... while
    each Yosys process was still advancing at one full core".

So a wall clock and a quiet-window have each been tried and withdrawn on evidence. A
third default would be the same defect a third time. What CAN be added without
reverting either is a backstop that is off unless asked for: unset, the leg behaves
exactly as it does today and says so; set, it carries a container-side backstop and
an expiry lands on the container-timeout path that already exists — `_TIMEOUT_MARKER`,
read as INCONCLUSIVE / SKIPPED-CONDITION with the proof state reached, never a FAIL
and never a PASS.
"""
import os
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import lec_run as LR  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", raising=False)


# ------------------------------------------------- direction 1: NOT asked for

@pytest.mark.parametrize("raw", ["", "   ", "0", "-5", "abc", "1.5"])
def test_only_an_explicit_positive_value_arms_the_bound(monkeypatch, raw):
    monkeypatch.setenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", raw)
    assert LR._env_lec_hard_bound_s() is None, raw


def test_unset_means_unbounded_which_is_the_2026_09_06_behaviour(monkeypatch):
    monkeypatch.delenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", raising=False)
    assert LR._env_lec_hard_bound_s() is None


def test_the_attempt_admission_budget_is_not_repurposed_as_a_deadline():
    """`DEFAULT_YOSYS_TIMEOUT_S` is what admits the NEXT attempt; `_docker`'s own
    docstring says it is "not a fresh retry budget" and no runtime deadline. Reusing
    its 7200 as the backstop is exactly what 2026-09-06 removed."""
    assert LR.DEFAULT_YOSYS_TIMEOUT_S == 7200
    assert LR._env_lec_hard_bound_s() is None


def test_the_unbounded_case_is_disclosed_rather_than_silent():
    src = (PROG / "lec_run.py").read_text()
    i = src.index("_hard_bound_s = _env_lec_hard_bound_s()")
    window = src[i:i + 900]
    assert "UNBOUNDED" in window
    assert "not an oversight" in window


# ----------------------------------------------------- direction 2: asked for

def test_an_explicit_value_arms_it(monkeypatch):
    monkeypatch.setenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", "900")
    assert LR._env_lec_hard_bound_s() == 900


def test_the_bound_is_applied_with_the_shared_container_wrapper():
    """The same primitive the other callers use — not a second bespoke timeout
    shape that could drift from it."""
    src = (PROG / "lec_run.py").read_text()
    i = src.index("_hard_bound_s = _env_lec_hard_bound_s()")
    window = src[i:i + 500]
    assert "wrap_with_container_timeout(cmd, _hard_bound_s)" in window
    assert "if _hard_bound_s is not None" in window


def test_the_armed_case_names_the_variable_that_armed_it():
    src = (PROG / "lec_run.py").read_text()
    i = src.index("_hard_bound_s = _env_lec_hard_bound_s()")
    window = src[i:i + 900]
    assert "VIBEIC_LEC_YOSYS_TIMEOUT_S" in window
    assert "OPT-IN" in window


# ------------------------------------------- what an expiry is allowed to mean

def test_an_expiry_lands_on_the_existing_no_verdict_path_not_a_fail():
    """rc 124/137 is already parsed as budget exhaustion -> INCONCLUSIVE /
    SKIPPED-CONDITION. The bound must not invent a second meaning."""
    assert LR._CONTAINER_TIMEOUT_RCS == (124, 137)
    src = (PROG / "lec_run.py").read_text()
    i = src.index("if launched and getattr(r, \"returncode\", 0) in _CONTAINER_TIMEOUT_RCS:")
    window = src[i:i + 1800]
    assert "_TIMEOUT_MARKER" in window


def test_the_stall_report_still_carries_the_proof_state():
    """"it stopped" is not a finding without how far it got."""
    src = (PROG / "lec_run.py").read_text()
    i = src.index("_PROGRESS_STALL_RCS:")
    window = src[i:i + 900]
    assert "lec_proved_points_from_output(out)" in window
    assert "proved" in window and "unproven" in window


def test_a_completed_proof_is_untouched_by_any_of_this():
    """The negative control: neither marker may be attached on a normal return,
    so a proof that finishes inside the bound reads exactly as before."""
    src = (PROG / "lec_run.py").read_text()
    i = src.index("if launched and getattr(r, \"returncode\", 0) in _CONTAINER_TIMEOUT_RCS:")
    # both marker attachments are guarded by a returncode test
    assert "elif launched and getattr(r, \"returncode\", 0) in _PROGRESS_STALL_RCS:" in src[i:i + 2600]
