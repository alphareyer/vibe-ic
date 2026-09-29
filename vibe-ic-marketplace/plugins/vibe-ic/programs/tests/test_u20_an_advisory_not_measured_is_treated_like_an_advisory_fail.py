#!/usr/bin/env python3
"""U20 (IC_BLOCKER_AUDIT §2) — an advisory NOT_MEASURED is treated like an
advisory FAIL, consistently.

MEASURED on spm v5 (IC path, deliverable DIE; 8HD-4 `spmic5/run_v5`, read-only
copies), step 7 and its advisory clause
`stage1_compliance . --json reports/phase2/gates/stage1_compliance.json`, which
both the gate module (`ENFORCEMENT: advisory`) and the canonical flow
(`advisory_program_exit_zero`) declare advisory:

  * whole-flow audit — stage1_compliance returned FAIL (rc 1): the record reads
    enforcement BLOCKING, the two-source rule honours the advisory declaration,
    and step 7 reads PASS with the refusal disclosed;
  * stage-2 scoped audit (the persisted stage2_compliance.json) — the SAME clause
    returned NOT_MEASURED: the record reads DISCLOSED_INCOMPLETE, the INCOMPLETE
    hint is emitted unconditionally, and step 7 reads NOT_MEASURED.

The less severe outcome of one advisory gate set a stricter tier than the more
severe one. Whether a two-source-advisory gate's non-green answer may move its
host step is ONE policy; it was applied to FAIL and not to NOT_MEASURED.

THE RULE: for a gate that BOTH sources declare advisory (and that declares no
non-waiverable finding), a NOT_MEASURED is recorded and disclosed exactly as a
refusal is — visible on the row, lossless in the record — and does not set the
step's tier. Every other advisory NOT_MEASURED keeps its INCOMPLETE disposition,
just as every other advisory FAIL keeps BLOCKING. Checked both ways below: for
each gate, FAIL and NOT_MEASURED land on the same side.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import flow_compliance_check as F        # noqa: E402

_T = F._T
PASS = _T.Verdict.PASS.value
FAIL = _T.Verdict.FAIL.value
NM = _T.Verdict.NOT_MEASURED.value

#: A stand-in for a nested stage audit: writes the report the real one writes
#: (`overall` + `verdict`) and exits 1, as the real one does on a non-PASS.
_NESTED = '''import json, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(argv[argv.index("--json") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({REPORT}))
print("Overall: " + {WORD!r} + "  (strict=True)")
sys.exit(1)
'''

_OUTCOMES = {
    "FAIL": {"overall": "FAIL", "verdict": "FAIL"},
    # the spm v5 stage-2 shape: nested NOT_MEASURED with a stated class
    "NOT_MEASURED": {"overall": "NOT_MEASURED", "verdict": "NOT_MEASURED",
                     "reason_class": "EXECUTION_ERROR"},
}


def _check(tmp_path: Path, gate: str, outcome: str):
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    prog = tmp_path / "gates" / f"{gate}.py"
    prog.parent.mkdir(parents=True, exist_ok=True)
    prog.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES[outcome]))
                    .replace("{WORD!r}", repr(outcome)))
    cmd = f"{prog} . --json reports/phase2/gates/{gate}.json"
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [{"advisory_program_exit_zero": cmd}]}}
    return F.check_step(project, step, {})


def test_the_gate_under_test_is_two_source_advisory():
    """The premise: stage1_compliance is advisory by BOTH declarations."""
    assert F._gate_is_two_source_advisory("stage1_compliance")
    assert not F._gate_is_two_source_advisory("probe_nested_audit")


def test_a_two_source_advisory_not_measured_reads_like_its_fail(tmp_path):
    """The recorded defect: FAIL -> PASS (disclosed), NOT_MEASURED -> NM."""
    on_fail = _check(tmp_path / "f", "stage1_compliance", "FAIL")
    on_nm = _check(tmp_path / "n", "stage1_compliance", "NOT_MEASURED")
    assert on_fail.status == PASS, (on_fail.status, on_fail.reasons)
    assert on_nm.status == on_fail.status, (on_nm.status, on_nm.reason_class,
                                            on_nm.reasons)


def test_the_advisory_not_measured_is_still_disclosed_and_recorded(tmp_path):
    r = _check(tmp_path, "stage1_compliance", "NOT_MEASURED")
    recs = r.advisory_gate_records
    assert len(recs) == 1, recs
    assert recs[0]["verdict"] == "NOT_MEASURED", recs
    assert recs[0]["enforcement"] == "DISCLOSED_INCOMPLETE", recs
    text = "\n".join(r.reasons)
    assert "NOT_MEASURED" in text and "stage1_compliance" in text, r.reasons
    assert "advisory" in text.lower(), r.reasons


def test_a_gate_advisory_by_one_source_only_keeps_both_dispositions(tmp_path):
    """The other side of the same rule: FAIL blocks, and so NOT_MEASURED keeps
    its INCOMPLETE disposition."""
    on_fail = _check(tmp_path / "f", "probe_nested_audit", "FAIL")
    on_nm = _check(tmp_path / "n", "probe_nested_audit", "NOT_MEASURED")
    assert on_fail.status == FAIL, (on_fail.status, on_fail.reasons)
    assert on_nm.status == NM, (on_nm.status, on_nm.reasons)


def test_an_advisory_not_measured_does_not_launder_a_sibling(tmp_path):
    """The disclosure must ride the held-out advisory channel: a genuinely
    incomplete sibling clause still sets the step NOT_MEASURED."""
    project = tmp_path / "proj"
    project.mkdir()
    adv = tmp_path / "gates" / "stage1_compliance.py"
    adv.parent.mkdir(parents=True)
    adv.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES["NOT_MEASURED"]))
                   .replace("{WORD!r}", repr("NOT_MEASURED")))
    sib = tmp_path / "gates" / "probe_nested_audit.py"
    sib.write_text(_NESTED.replace("{REPORT}", repr(_OUTCOMES["NOT_MEASURED"]))
                   .replace("{WORD!r}", repr("NOT_MEASURED")))
    step = {"id": 7, "name": "constraints", "stage": "stage2",
            "gate": {"all_of": [
                {"advisory_program_exit_zero":
                 f"{adv} . --json reports/a.json"},
                {"advisory_program_exit_zero":
                 f"{sib} . --json reports/b.json"}]}}
    r = F.check_step(project, step, {})
    assert r.status == NM, (r.status, r.reasons)
