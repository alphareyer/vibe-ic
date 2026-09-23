"""A compliance receipt says which scope it judged — not "all" over one stage.

R-0915-147. `flow_compliance_check` stamped its receipt with `args.phase`, which
DEFAULTS to "all", and recorded the `--stage` / `--stage-id` narrowing NOWHERE. So a
stage-scoped pass published a whole-flow label over a single stage's population.

MEASURED on spm run22, READ-ONLY, on five shipped receipts:

    reports/phase1/gates/stage_phase1_compliance.json    2 steps   phase="all"
    reports/phase2/gates/stage1_compliance.json          7 steps   phase="all"
    reports/phase2/gates/stage2_compliance.json         13 steps   phase="all"
    reports/phase3/gates/stage3_compliance.json         20 steps   phase="all"
    reports/phase3/gates/stage4_compliance.json         10 steps   phase="all"

— every one stamped whole-flow, none carrying a `stage` key at all, while the flow
has 70 steps. That is how a reader comparing a stage's own stdout against its receipt
cannot tell whether the two describe the same population, which is the disagreement
this was found through (a stage1 pass printing PASS beside a receipt saying FAIL).

The receipt now carries the scope it actually judged: the phase and stage arguments
as given, the step ids in the population, the flow's own total, and whether the
population WAS the whole flow — derived against the flow size read BEFORE narrowing,
never from `args.phase`, because the argument is the thing that was already wrong.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
FLOW = PLUGIN / "flow" / "phase1_phase2_phase3.yaml"


def _flow_step_ids() -> list:
    doc = yaml.safe_load(FLOW.read_text())
    return [str(s.get("id")) for s in doc["steps"] if isinstance(s, dict)]


@pytest.fixture()
def project(tmp_path):
    rtl = tmp_path / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "d.v").write_text("module d(); endmodule\n")
    return tmp_path


def _run(project: Path, *args: str) -> dict:
    out = project / "reports/zz_scope.json"
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         *args, "--json", str(out)],
        capture_output=True, text=True, timeout=2400)
    assert out.is_file(), "the pass wrote no receipt"
    return json.loads(out.read_text())


def test_a_stage_scoped_pass_does_not_claim_the_whole_flow(project):
    doc = _run(project, "--stage", "4")
    scope = doc["scope"]
    assert scope["stage"] == "4", scope
    assert scope["whole_flow"] is False, scope
    assert 0 < scope["step_count"] < scope["flow_step_total"], scope
    assert len(scope["steps_judged"]) == scope["step_count"]
    # and every judged step really belongs to that stage
    doc_flow = yaml.safe_load(FLOW.read_text())
    by_id = {str(s.get("id")): s for s in doc_flow["steps"] if isinstance(s, dict)}
    assert all(by_id[sid].get("stage") == "stage4"
               for sid in scope["steps_judged"]), scope["steps_judged"]


def test_a_stage_id_scoped_pass_records_the_id_it_was_given(project):
    doc = _run(project, "--stage-id", "stage4")
    scope = doc["scope"]
    assert scope["stage_id"] == "stage4" and scope["stage"] is None, scope
    assert scope["whole_flow"] is False


def test_a_whole_flow_pass_says_so(project):
    """THE OTHER DIRECTION: an unscoped pass must still be recognisable as one, or
    the fix has only moved the lie."""
    doc = _run(project)
    scope = doc["scope"]
    assert scope["stage"] is None and scope["stage_id"] is None, scope
    assert scope["whole_flow"] is True, scope
    assert scope["step_count"] == scope["flow_step_total"] == len(_flow_step_ids())


def test_the_scope_is_not_derived_from_the_phase_argument(project):
    """`phase` is an ARGUMENT and it defaults to "all"; the scope must be measured
    from the population. A stage-scoped pass carries phase="all" AND
    whole_flow=False, and the two disagreeing is the point."""
    doc = _run(project, "--stage", "4")
    assert doc["phase"] == "all", doc["phase"]
    assert doc["scope"]["whole_flow"] is False
    assert doc["scope"]["phase"] == "all", (
        "the argument is recorded as given — the scope block does not rewrite it, "
        "it stands beside it so a reader can see both")


def test_the_flow_total_is_taken_before_narrowing():
    """SOURCE PIN on the bug a later refactor would re-introduce: `steps` is rebound
    by the --stage filter, so the total must be captured BEFORE it."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    total_at = src.index("_flow_step_total = len(")
    narrow_at = src.index('steps = [s for s in steps if s.get("stage") == target_stage]')
    assert total_at < narrow_at, (
        "the flow total is captured AFTER the stage filter rebinds `steps`, so "
        "`whole_flow` compares the population with itself and every scoped pass "
        "claims to be whole-flow again")
