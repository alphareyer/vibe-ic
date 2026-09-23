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


# ── R-0915-150: a pass's own receipt, and nobody else's ─────────────────────
#
# MEASURED END TO END with icspm5's three-pass driver on a `cp -a` of spm run22
# (stage1 receipt present at 08:52:02), three sequential top-level invocations:
#
#   PLAIN MAIN f7d03cc8b                    THIS TIP
#     stage1 exits 12:58:10                   stage1 exits 13:07:58
#       its own receipt written 12:58:08        its own receipt written 13:07:57
#       overall NOT_MEASURED                   overall NOT_MEASURED
#     stage2 pass REPUBLISHES it 12:58:50     stage2 pass leaves it alone
#       overall becomes FAIL      (pass 2)    stage4 pass leaves it alone
#     stage4 pass republishes     (pass 3)    every receipt: republished = False,
#       12:59:45                               written inside its OWN pass's window
#     canonical completion audit REWRITTEN    canonical audit BYTE-IDENTICAL to the
#       by the scoped passes (md5 differs)      original; each scoped pass writes
#                                               its own .scoped-<stage>.json
#
# So a reader of main's stage1 receipt gets FAIL for a stage whose own pass said
# NOT_MEASURED, authored while a different stage was being judged. The mechanism is
# the R-0915-138 republish — mine — reaching across invocations it was never about.


def test_a_scoped_pass_leaves_the_canonical_completion_audit_alone(project):
    """A stage-scoped pass must not publish the WHOLE run's completion audit.

    Its readers take that document as the run's verdict:
    `benchmark_evidence_publish._audit_verdict` and its convergence guard, and
    phase3's `_derive_headline_verdict`, which copies it into phase3_one_shot.json.
    The owner's bar for spm is "completion audit 0 failed gates", and that sentence
    is only true of the whole flow.
    """
    canonical = project / "reports/audit/phase23_completion_audit.json"
    for stage in ("stage1", "stage2"):
        subprocess.run(
            [sys.executable, str(PROGRAMS / "flow_compliance_check.py"),
             str(project), "--stage-id", stage,
             "--json", str(project / f"reports/phase2/gates/{stage}_compliance.json")],
            capture_output=True, text=True, timeout=2400)
    assert not canonical.exists(), (
        "a stage-scoped pass published the whole run's completion audit; every "
        "reader of that path then treats one stage's tally as the run's verdict")
    scoped = sorted((project / "reports/audit").glob(
        "phase23_completion_audit.scoped-*.json"))
    assert scoped, "the scoped pass wrote no audit at all — it must write its own"
    for path in scoped:
        doc = json.loads(path.read_text())
        assert doc["scope"]["whole_flow"] is False, (path.name, doc["scope"])


def test_a_whole_flow_pass_does_publish_the_canonical_audit(project):
    """THE OTHER DIRECTION: the canonical document must still be published by the
    pass that is entitled to — otherwise the fix has only removed the audit."""
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         "--json", str(project / "reports/zz_whole.json")],
        capture_output=True, text=True, timeout=2400)
    canonical = project / "reports/audit/phase23_completion_audit.json"
    assert canonical.is_file(), "the whole-flow pass published no completion audit"
    doc = json.loads(canonical.read_text())
    assert doc["scope"]["whole_flow"] is True, doc["scope"]
    assert not sorted((project / "reports/audit").glob(
        "phase23_completion_audit.scoped-*.json")), (
        "a whole-flow pass also wrote a scoped copy; there is one canonical audit")


def test_the_republish_leaves_another_invocations_receipt_alone(tmp_path):
    """THE MECHANISM, through the shipped function.

    R-0915-138 supersedes the audit's OWN earlier publication inside one invocation.
    A receipt written by a DIFFERENT invocation is somebody else's measurement of a
    different population, so it must be left untouched.
    """
    import flow_compliance_check as FCC
    rel = "reports/phase2/gates/stage1_compliance.json"
    doc = tmp_path / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(json.dumps({"program": "flow_compliance_check",
                               "overall": "NOT_MEASURED", "steps": [{"id": "1"}]}) + "\n")
    argv = ["stage1_compliance", ".", "--json", rel]
    before = doc.read_bytes()

    FCC._THIS_INVOCATION_PUBLISHED.clear()
    assert FCC._publish_over_the_audits_own_document(argv, tmp_path) is None, (
        "the republish claimed a receipt this invocation never wrote")
    assert doc.read_bytes() == before, "and it must not have touched the bytes"

    # the same call, once THIS invocation owns that receipt: R-0915-138 applies
    FCC._THIS_INVOCATION_PUBLISHED.add(FCC._resolved_key(doc))
    prov = FCC._publish_over_the_audits_own_document(argv, tmp_path)
    assert prov is not None, (
        "the audit can no longer supersede its OWN publication, which is the "
        "mechanism R-0915-138 exists for")
    assert prov["supersedes"]["verdict"] == "NOT_MEASURED", prov
    FCC._THIS_INVOCATION_PUBLISHED.clear()


def test_a_pass_records_the_receipt_it_publishes(project):
    """`--json` is the pass's own receipt: it is written by that pass AND registered,
    so a later clause in the SAME invocation may supersede it while another
    invocation's may not."""
    import flow_compliance_check as FCC
    out = project / "reports/zz_own.json"
    FCC._THIS_INVOCATION_PUBLISHED.clear()
    FCC.main([str(project), "--stage-id", "stage1", "--json", str(out)])
    assert out.is_file(), "the pass did not write its own receipt"
    assert FCC._resolved_key(out) in FCC._THIS_INVOCATION_PUBLISHED, (
        "the pass wrote its receipt without recording it, so its own later pass "
        "could not supersede it")
    FCC._THIS_INVOCATION_PUBLISHED.clear()


def test_whole_flow_is_membership_not_a_count(project, tmp_path):
    """A COUNT is inflated by rows the flow does not declare — the synthetic P0
    umbrella and the pre-PnR rows. On a flow definition without P0, a `--stage 2`
    pass could reach the count and call itself whole-flow with step 1 never judged."""
    doc = yaml.safe_load(FLOW.read_text())
    small = tmp_path / "small_flow.yaml"
    small.write_text(yaml.safe_dump(
        {"flow": "zz", "stages": [{"id": "stage1"}, {"id": "stage2"}],
         "steps": [{"id": "1", "name": "a", "stage": "stage1", "blocks_on": []},
                   {"id": "2", "name": "b", "stage": "stage2", "blocks_on": []},
                   {"id": "3", "name": "c", "stage": "stage2", "blocks_on": []}]},
        sort_keys=False))
    out = project / "reports/zz_small.json"
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         "--flow-def", str(small), "--stage-id", "stage2", "--json", str(out)],
        capture_output=True, text=True, timeout=1200)
    assert out.is_file()
    scope = json.loads(out.read_text())["scope"]
    assert scope["whole_flow"] is False, (
        f"a stage-2 pass over a 3-step flow judged {scope['step_count']} row(s) and "
        f"called itself whole-flow; step 1 was never judged. {scope}")
    assert "1" not in scope["steps_judged"], scope
