"""FX_PADRING_RC_REASON — a refusal is recorded, never a runner crash.

`StepResult.__post_init__` runs `verdict.validate_step_row`: a NOT_MEASURED row
with no `reason_class` raises ValueError, and a word outside the five step
verdicts raises UnknownVerdictWord. Found by llf (LLV1_W7a, "Found, not
changed") and swept across the phase runners (every `StepResult(...)` whose
status can be NOT_MEASURED on some branch):

  * `step_pad_ring_gen` COMPUTED the reason class for a producer/gate that
    exits 2 (not_executed) or with any other non-0/1 code (tool_absent), and
    never passed it, so the row it built raised ValueError: the step-15.5ic
    refusal crashed phase 3 instead of being recorded. Its "program absent"
    branch used the deleted word ENV_UNAVAILABLE (UnknownVerdictWord); the
    word that replaces it is NOT_MEASURED / tool_absent.
  * `_step_gds_direct(candidate=True)` outside the private SDR scratch built a
    NOT_MEASURED row with no reason class (ValueError).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as runner  # noqa: E402
import verdict as V  # noqa: E402


def _outcome(build):
    """(status, reason_class) of the row a step builds, or ("CRASH", <exception>)
    when building it raised -- the value this item is about."""
    try:
        row = build()
    except Exception as exc:  # noqa: BLE001 -- the crash IS the observation
        return ("CRASH", type(exc).__name__), None
    return (row.status, row.reason_class), row


def _pad_ring(tmp_path, monkeypatch, rc_first):
    calls = []

    def run(argv, **_k):
        calls.append(Path(argv[1]).name)
        return SimpleNamespace(returncode=rc_first if len(calls) == 1 else 0,
                               stdout="", stderr="refused by name")
    monkeypatch.setattr(runner._pr, "run", run)
    project = tmp_path / "proj"
    project.mkdir()
    outcome, row = _outcome(lambda: runner.step_pad_ring_gen(project))
    return outcome, row, calls


@pytest.mark.parametrize("rc, reason", [
    (2, V.ReasonClass.NOT_EXECUTED.value),
    (7, V.ReasonClass.TOOL_ABSENT.value),       # a code the step does not know
    (127, V.ReasonClass.TOOL_ABSENT.value)])
def test_a_pad_ring_refusal_is_recorded_with_its_reason(tmp_path, monkeypatch, rc, reason):
    outcome, row, calls = _pad_ring(tmp_path, monkeypatch, rc)
    assert outcome == ("NOT_MEASURED", reason)
    assert calls == ["pad_assignment_gen.py"]              # the chain stopped there
    assert f"pad_assignment_gen.py: rc={rc}" in row.detail


def test_a_pad_ring_producer_failure_stays_fail(tmp_path, monkeypatch):
    outcome, _row, _calls = _pad_ring(tmp_path, monkeypatch, 1)
    assert outcome == ("FAIL", "")


def test_an_absent_pad_ring_program_is_tool_absent_not_a_deleted_word(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROGRAMS_DIR", tmp_path / "no_programs")
    project = tmp_path / "proj"
    project.mkdir()
    outcome, row = _outcome(lambda: runner.step_pad_ring_gen(project))
    assert outcome == ("NOT_MEASURED", V.ReasonClass.TOOL_ABSENT.value)
    assert "pad_assignment_gen.py: program absent" in row.detail


def test_a_candidate_gds_outside_the_sdr_scratch_is_recorded_not_executed(tmp_path):
    outcome, row = _outcome(lambda: runner._step_gds_direct(
        tmp_path / "proj", "top", SimpleNamespace(name="p"), "c", candidate=True))
    assert outcome == ("NOT_MEASURED", V.ReasonClass.NOT_EXECUTED.value)
    assert "private SDR scratch" in row.detail
