#!/usr/bin/env python3
"""U20 (IC_BLOCKER_AUDIT §2) — the persisted stage2_compliance.json must match the
audit verdict.

MEASURED on spm v5 (IC path, deliverable DIE; 8HD-4 `spmic5/run_v5`, read-only
copy). Step 15 declares `reports/phase2/gates/stage2_compliance.json` as a
required output, and its gate re-runs `stage2_compliance . --json <that path>`:

    on disk (11:21, invoked_as producer)   overall NOT_MEASURED
    whole-flow audit (11:56), step 15      stage2_compliance rc=0
                                           structured_verdict PASS -> step PASS

The audit re-ran the stage audit into a scratch receipt (R-0915-126: the auditor
never overwrites a document the run produced), read PASS there, and passed step 15
— while the document step 15 DECLARES, the one a reader or a later consumer opens,
still says NOT_MEASURED. Two verdicts for one question, and the run's own record is
the one the audit did not publish.

THE RULE: when a step's declared output is a stage-compliance receipt (a document
`flow_compliance_check` wrote: `program` + `overall`) and this audit's re-run of the
step's own clause wrote a receipt whose `overall` DIFFERS from the one on disk, the
step does not PASS on it: it reads NOT_MEASURED (missing_artefact — the run owes a
current receipt), naming both words and the path. The run's document is still left
byte-for-byte alone. Agreement changes nothing; a document some other program wrote
is not compared; a step whose gate did not run the clause is not compared.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import flow_compliance_check as F        # noqa: E402

_T = F._T
PASS = _T.Verdict.PASS.value
NM = _T.Verdict.NOT_MEASURED.value
REL = "reports/phase2/gates/stage2_compliance.json"

#: The persisted document's shape, from spm v5 (fields that matter here).
def _receipt(overall: str, program: str = "flow_compliance_check") -> dict:
    return {"program": program, "invoked_as": "producer", "overall": overall,
            "scope": {"phase": "all", "stage": "2", "whole_flow": False}}


#: A stand-in stage audit: writes the receipt the real one writes, exits 0.
_STAGE = '''import json, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(argv[argv.index("--json") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({DOC}))
print("Overall: PASS  (strict=True)")
sys.exit(0)
'''


def _check(tmp_path: Path, on_disk: dict, fresh: dict, declare: bool = True):
    project = tmp_path / "proj"
    (project / "reports/phase2/gates").mkdir(parents=True)
    (project / REL).write_text(json.dumps(on_disk))
    prog = tmp_path / "gates" / "stage2_compliance.py"
    prog.parent.mkdir(parents=True)
    prog.write_text(_STAGE.replace("{DOC}", repr(fresh)))
    step = {"id": 15, "name": "floorplan", "stage": "stage3",
            "gate": {"all_of": [{"advisory_program_exit_zero":
                                 f"{prog} . --json {REL}"}]}}
    if declare:
        step["required_outputs"] = [REL]
        step["programs"] = ["stage2_compliance"]
    before = (project / REL).read_bytes()
    res = F.check_step(project, step, {})
    assert (project / REL).read_bytes() == before, \
        "R-0915-126: the run's document must be left byte-for-byte alone"
    return res


def test_the_spm_v5_shape_does_not_pass(tmp_path):
    res = _check(tmp_path, _receipt("NOT_MEASURED"), _receipt("PASS"))
    assert res.status == NM, (res.status, res.reason_class, res.reasons)
    assert res.reason_class == _T.ReasonClass.MISSING_ARTEFACT.value, \
        res.reason_class
    text = "\n".join(res.reasons)
    assert REL in text and "NOT_MEASURED" in text and "PASS" in text, \
        res.reasons


def test_agreement_changes_nothing(tmp_path):
    res = _check(tmp_path, _receipt("PASS"), _receipt("PASS"))
    assert res.status == PASS, (res.status, res.reasons)


def test_a_document_another_program_wrote_is_not_compared(tmp_path):
    res = _check(tmp_path, _receipt("NOT_MEASURED", program="someone_else"),
                 _receipt("PASS"))
    assert res.status == PASS, (res.status, res.reasons)


def test_an_undeclared_receipt_is_not_the_steps_output(tmp_path):
    """Only the step's OWN declared output speaks for the step."""
    res = _check(tmp_path, _receipt("NOT_MEASURED"), _receipt("PASS"),
                 declare=False)
    assert res.status == PASS, (res.status, res.reasons)
