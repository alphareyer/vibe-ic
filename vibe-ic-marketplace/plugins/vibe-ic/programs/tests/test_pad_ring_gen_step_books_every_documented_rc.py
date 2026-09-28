#!/usr/bin/env python3
"""Step 15.5ic books every exit code its programs document, and never raises.

Lane llf (W7a) found that `step_pad_ring_gen` computed a NOT_MEASURED reason
class for rc 2 and other codes and then built the StepResult WITHOUT it:
`StepResult` refuses a NOT_MEASURED with no reason, so the step RAISED
ValueError instead of reporting. Each program's own contract:

  pad_assignment_gen            1 REFUSE (answers owed)   2 NOT_ASKED
  pad_ring_gen                  1 refusal                 2 SKIP (inputs absent /
                                                            rotation it cannot honour)
  pad_ring_check                1 wrong or silent report  2 disclosed absence
  pad_bterm_coincidence_check   1 a net could not be decided
                                2 nothing to decide (no pad terminal)

A finding stays FAIL; a documented could-not-measure is NOT_MEASURED with the
reason that says why; an exit no program documents is an execution error.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
import json

import pytest

import _plugin_tree  # noqa: F401 -- puts programs/ on sys.path
import phase3_one_shot_runner as R
import verdict as _V
import pad_ring_gen as GEN
import test_pad_ring as PAD

NM = _V.Verdict.NOT_MEASURED.value
FAIL = _V.Verdict.FAIL.value
RC = _V.ReasonClass

PROGRAMS = ["pad_assignment_gen.py", "pad_ring_gen.py", "pad_ring_check.py",
            "pad_bterm_coincidence_check.py"]


def _pdk(tmp_path: Path):
    return SimpleNamespace(name="tpdk", tech_lef=str(tmp_path / "t.tlef"))


def _drive(tmp_path, monkeypatch, failing: str, rc: int):
    """Run the step with every program exiting 0 except `failing` -> `rc`."""
    monkeypatch.setattr(R, "_padring_pdk_root_and_tree",
                        lambda pdk, container: (str(tmp_path), "tpdk"))
    ran = []

    def run(argv, **_k):
        name = Path(argv[1]).name
        ran.append(name)
        code = rc if name == failing else 0
        return SimpleNamespace(returncode=code,
                               stdout=f"{name} said rc {code}", stderr="")
    monkeypatch.setattr(R._pr, "run", run)
    result = R.step_pad_ring_gen(tmp_path, None, _pdk(tmp_path))
    return result, ran


CASES = [
    ("pad_assignment_gen.py", 1, FAIL, ""),
    ("pad_assignment_gen.py", 2, NM, RC.INPUT_ABSENT.value),
    ("pad_ring_gen.py", 1, FAIL, ""),
    ("pad_ring_gen.py", 2, NM, RC.INPUT_ABSENT.value),
    ("pad_ring_check.py", 1, FAIL, ""),
    ("pad_ring_check.py", 2, NM, RC.INPUT_ABSENT.value),
    ("pad_bterm_coincidence_check.py", 1, NM, RC.INCONCLUSIVE.value),
    ("pad_bterm_coincidence_check.py", 2, NM, RC.NO_POPULATION.value),
]


@pytest.mark.parametrize("program,rc,status,reason", CASES)
def test_each_documented_rc_is_booked_with_its_reason(
        tmp_path, monkeypatch, program, rc, status, reason):
    result, ran = _drive(tmp_path, monkeypatch, program, rc)
    assert (result.status, result.reason_class) == (status, reason)
    # the producer's own words and its rc are the detail, and nothing after
    # the refusing program ran
    assert f"{program}: rc={rc}" in result.detail
    assert f"{program} said rc {rc}" in result.detail
    assert ran == PROGRAMS[:PROGRAMS.index(program) + 1]


@pytest.mark.parametrize("program", PROGRAMS)
@pytest.mark.parametrize("rc", [3, 137, -9])
def test_an_undocumented_exit_is_an_execution_error_not_an_exception(
        tmp_path, monkeypatch, program, rc):
    result, _ = _drive(tmp_path, monkeypatch, program, rc)
    assert (result.status, result.reason_class) == (
        NM, RC.EXECUTION_ERROR.value)
    assert f"{program}: rc={rc}" in result.detail


def test_a_missing_program_is_tool_absent(tmp_path, monkeypatch):
    real = R.PROGRAMS_DIR
    fake = tmp_path / "programs"
    fake.mkdir()
    for name in PROGRAMS[:2]:
        (fake / name).write_text("")
    monkeypatch.setattr(R, "PROGRAMS_DIR", fake)
    result, ran = _drive(tmp_path, monkeypatch, "", 0)
    assert (result.status, result.reason_class) == (NM, RC.TOOL_ABSENT.value)
    assert "pad_ring_check.py: program absent" in result.detail
    assert ran == PROGRAMS[:2]
    assert real.is_dir()


def test_all_zero_without_the_declared_outputs_is_fail(tmp_path, monkeypatch):
    """Control: the existing rule stands -- rc 0 everywhere with the step's
    declared outputs absent is FAIL, and a FAIL carries no reason class."""
    result, ran = _drive(tmp_path, monkeypatch, "", 0)
    assert ran == PROGRAMS
    assert (result.status, result.reason_class) == (FAIL, "")
    assert "required output(s) are absent" in result.detail


@pytest.mark.parametrize("rotation,rule,reason", [
    (None, "REQUIRED_INPUT_ABSENT", "input_absent"),
    ("R90", "PAD_ROTATION_VERTICAL_NOT_HONOURED", "unsupported_request"),
])
def test_pad_ring_rc2_uses_the_producers_named_report(
        tmp_path, monkeypatch, rotation, rule, reason):
    """The same rc=2 carries two different producer explanations."""
    cfg = None if rotation is None else PAD._config(PAD_ROTATION_VERTICAL=rotation)
    root = PAD._project(tmp_path, config=cfg)
    monkeypatch.setattr(R, "_padring_pdk_root_and_tree",
                        lambda pdk, container: (str(root / "pdk"), "proc"))

    def run(argv, **_kw):
        name = Path(argv[1]).name
        if name == "pad_assignment_gen.py":
            return SimpleNamespace(returncode=0, stdout="assignment ready", stderr="")
        assert name == "pad_ring_gen.py"
        output = StringIO()
        with redirect_stdout(output):
            rc = GEN.main(argv[2:])
        return SimpleNamespace(returncode=rc, stdout=output.getvalue(), stderr="")

    monkeypatch.setattr(R._pr, "run", run)
    result = R.step_pad_ring_gen(root, None, _pdk(root))
    producer = json.loads((root / "reports/phase3/padring.json").read_text())
    assert producer["verdict"] == "SKIP"
    assert rule in {f["rule"] for f in producer["findings"]}
    assert (result.status, result.reason_class) == (NM, reason)
