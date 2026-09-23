"""R-0915-141, the step-38 half, applied to step 2 — a step declares what the RUN
produces, never its own gate's verdict target.

MEASURED on spm run23: D1 credited (the expert pass consumed), and step 2 stopped
at "AUDIT-CREATED OUTPUT REFUSED: ['reports/phase1/gates/stage_phase1_compliance.json']"
(3/4 satisfied). That path is the `--json` target of step 2's OWN nested
`flow_compliance_check --stage-id stage_phase1` clause, and it was also in step 2's
`required_outputs`, so whenever the nested pass wrote it the audit's refusal of
self-certified evidence matched BY CONSTRUCTION.

R-0915-141 fixed that shape for steps 36 and 38. Step 36 could name a separate
producer; step 2's document has none -- it IS the audit's verdict -- so the rule
that transfers is step 38's: the path leaves the declared set, the clause still
runs the nested pass and writes its receipt there, and step 2's tier comes from
that pass's own verdict. NO self-crediting rule is added: a failed or killed
nested pass still cannot pass the step.

Each test reads step 2's REAL declaration out of the shipped flow and replaces
only its gate with the nested clause, played by a stand-in program on disk named
by ABSOLUTE path (which `_resolve_program_cmd` honours) and writing the report the
real audit writes, so `check_step` classifies it end to end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

_T = FCC._T
_PASS = _T.Verdict.PASS.value
_FAIL = _T.Verdict.FAIL.value
_RECEIPT = "reports/phase1/gates/stage_phase1_compliance.json"
_FLOW = PLUGIN / "flow" / "phase1_phase2_phase3.yaml"

_NESTED = '''import json, os, signal, sys
from pathlib import Path
MODE = {mode!r}
argv = sys.argv[1:]
if MODE == "kill":
    os.kill(os.getpid(), signal.SIGKILL)
overall = "PASS" if MODE == "pass" else "FAIL"
p = Path(argv[argv.index("--json") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({{
    "program": "flow_compliance_check", "overall": overall,
    "counts": {{"PASS": 2 if overall == "PASS" else 1,
               "FAIL": 0 if overall == "PASS" else 1}},
    "steps": [],
    "invocation": os.environ.get("VIBEIC_FCC_INVOCATION", ""),
    "invoked_as": os.environ.get("VIBEIC_FCC_ROLE", "producer")}}))
print("Overall: " + overall + "  (strict=True)")
sys.exit(0 if overall == "PASS" else 1)
'''


def _step2() -> dict:
    flow = yaml.safe_load(_FLOW.read_text())
    return next(s for s in flow["steps"] if str(s.get("id")) == "2")


def _check(tmp_path: Path, mode: str, already_there=None):
    """Step 2 as the flow declares it, its gate reduced to the nested clause.

    Every OTHER declared output is present as the run left it, so the only
    output in question is the nested gate's receipt."""
    project = tmp_path / "proj"
    project.mkdir()
    step = dict(_step2())
    for rel in step.get("required_outputs") or []:
        if rel == _RECEIPT:
            continue
        f = project / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"verdict": "PASS"}')
    if already_there is not None:
        f = project / _RECEIPT
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(already_there))
    prog = tmp_path / "gates" / "flow_compliance_check.py"
    prog.parent.mkdir(parents=True, exist_ok=True)
    prog.write_text(_NESTED.format(mode=mode))
    step["blocks_on"] = []
    step["required_inputs"] = []
    step["gate"] = {"all_of": [{"advisory_program_exit_zero":
                                f"{prog} . --stage-id stage_phase1 --strict "
                                f"--json {_RECEIPT}"}]}
    return FCC.check_step(project, step, {})


def test_step_2_does_not_declare_its_nested_gates_verdict_target():
    step = _step2()
    assert _RECEIPT not in (step.get("required_outputs") or []), step[
        "required_outputs"]
    assert _RECEIPT in FCC._gate_json_targets(step), (
        "the nested clause must still write its receipt there")


def test_a_nested_pass_that_passes_is_not_refused_as_self_certified(tmp_path):
    """spm run23's shape: the audit's own nested pass writes the receipt."""
    r = _check(tmp_path, "pass")
    assert r.status == _PASS, (r.status, r.reason_class, r.reasons)
    assert not any("AUDIT-CREATED OUTPUT REFUSED" in x for x in r.reasons), (
        r.reasons)


def test_a_nested_pass_that_fails_still_fails_step_2(tmp_path):
    r = _check(tmp_path, "fail")
    assert r.status == _FAIL, (r.status, r.reason_class, r.reasons)
    assert any("advisory gate refusal" in x for x in r.reasons), r.reasons


def test_a_killed_nested_pass_does_not_pass_step_2(tmp_path):
    r = _check(tmp_path, "kill")
    assert r.status != _PASS, (r.status, r.reason_class, r.reasons)


def test_a_foreign_receipt_does_not_pass_step_2_for_a_failing_nested_pass(
        tmp_path):
    """A PASS document someone else left at the path is not step 2's evidence:
    the nested pass this step ran failed, and that is what the step reads."""
    foreign = {"program": "flow_compliance_check", "overall": "PASS",
               "counts": {"PASS": 2, "FAIL": 0}, "steps": [],
               "invocation": "1-1", "invoked_as": "producer"}
    r = _check(tmp_path, "fail", already_there=foreign)
    assert r.status == _FAIL, (r.status, r.reason_class, r.reasons)
