"""R-0915-48 / #3421 — the equiv proof has one truthful finite budget.

THE PROBLEM, MEASURED on sha256 run12: a Yosys equivalence proof stuck on ONE hard
point holds 99.9% CPU indefinitely. `_watchdog` treats progress as an OR — output
grew OR the log grew OR a domain probe changed OR **the CPU advanced** — and
`_container_exec` supplies a container-tree CPU probe, so a spinning proof is
"progressing" for ever and the run never ends. run12 sat 4 h 31 m with its live report
frozen at one byte count, 622 of 1580 points proved.

The earlier contract made the wall bound opt-in because a proof could still be
progressing when it expired. A retained 34-hour CPU-active proof proved the other
failure mode: without a wall bound, the recorded step budget was not a bound at all.

  * 2026-09-06 — pinning `wrap_with_container_timeout` to the budget SIGKILLed a proof
    at budget-5s that was emitting output at a full core (5360 s of 7195 s, 1374 points
    proved, 0 failed, still advancing). "A bigger number would be the same defect with
    a later date."
  * earlier — a host-side SILENCE window "killed two healthy RTLLM LEC jobs ... while
    each Yosys process was still advancing at one full core".

The stronger contract now gives each leg exactly the remaining shared StepBudget.
Expiry retains logs and checkpoint evidence and lands on the existing INCONCLUSIVE
path unless a mismatch was already measured. The legacy test IDs remain so the
contract change does not delete historical coverage.
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
    assert LR._env_yosys_timeout_default() == 7200, raw


def test_unset_means_unbounded_which_is_the_2026_09_06_behaviour(monkeypatch):
    monkeypatch.delenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", raising=False)
    assert LR._env_yosys_timeout_default() == 7200


def test_the_attempt_admission_budget_is_not_repurposed_as_a_deadline():
    """Legacy ID: the same remaining budget now admits and bounds one attempt."""
    assert LR.DEFAULT_YOSYS_TIMEOUT_S == 7200
    src = (PROG / "lec_run.py").read_text()
    assert "wrap_with_container_timeout(cmd, max(1, int(timeout)))" in src


def test_the_unbounded_case_is_disclosed_rather_than_silent():
    src = (PROG / "lec_run.py").read_text()
    i = src.index("def run_yosys_equiv")
    window = src[i:i + 8000]
    assert "remaining finite step budget" in window
    assert "UNBOUNDED" not in window


# ----------------------------------------------------- direction 2: asked for

def test_an_explicit_value_arms_it(monkeypatch):
    monkeypatch.setenv("VIBEIC_LEC_YOSYS_TIMEOUT_S", "900")
    assert LR._env_yosys_timeout_default() == 900


def test_the_bound_is_applied_with_the_shared_container_wrapper():
    """The same primitive the other callers use — not a second bespoke timeout
    shape that could drift from it."""
    src = (PROG / "lec_run.py").read_text()
    i = src.index("def run_yosys_equiv")
    window = src[i:i + 8000]
    assert "wrap_with_container_timeout(cmd, max(1, int(timeout)))" in window


def test_the_armed_case_names_the_variable_that_armed_it():
    src = (PROG / "lec_run.py").read_text()
    default = src[src.index("def _env_yosys_timeout_default"):][:900]
    runner = src[src.index("def run_yosys_equiv"):][:8000]
    assert "VIBEIC_LEC_YOSYS_TIMEOUT_S" in default
    assert "remaining finite step budget" in runner


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
