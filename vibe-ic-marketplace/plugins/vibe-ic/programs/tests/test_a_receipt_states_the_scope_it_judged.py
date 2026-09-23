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
    scoped = sorted((project / "reports/audit/scoped").glob("*.json"))
    assert scoped, "the scoped pass wrote no audit at all — it must write its own"
    for path in scoped:
        doc = json.loads(path.read_text())
        assert doc["scope"]["whole_flow"] is False, (path.name, doc["scope"])
        assert doc["invocation"], path.name
        # NAMED BY THE POPULATION, not by a flag: a whole-flow pass leaves several of
        # these from its nested stage clauses, and they judged different step sets.
        assert ("stage-" in path.name or "phase-" in path.name), path.name


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
    assert not sorted((project / "reports/audit/scoped").glob("*.json")), (
        "a whole-flow pass also wrote a scoped copy; there is one canonical audit")


def test_the_republish_names_the_invocation_it_superseded(tmp_path):
    """WHO wrote a receipt, and over what, is recorded ON THE DOCUMENT.

    My first cut gated the republish on a set in the parent's memory, filled after the
    step loop. Every `stageN_compliance` clause is a separate SUBPROCESS with its own
    empty set, so the republish could never fire in a real run and R-0915-138 was
    silently reverted — three of its own tests went red, and my positive arm passed
    only because it filled the set by hand.

    Authorship therefore lives where a subprocess can read it: the receipt carries the
    invocation that wrote it, and a republish names both sides.
    """
    import flow_compliance_check as FCC
    rel = "reports/phase2/gates/stage1_compliance.json"
    doc = tmp_path / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(json.dumps({
        "program": "flow_compliance_check", "overall": "NOT_MEASURED",
        "steps": [{"id": "1"}], "invocation": "some-earlier-invocation",
        "scope": {"stage_id": "stage1", "step_count": 7, "whole_flow": False},
    }) + "\n")

    prov = FCC._publish_over_the_audits_own_document(
        ["stage1_compliance", ".", "--json", rel], tmp_path)
    assert prov is not None, (
        "the republish declined the audit's own compliance report; R-0915-138 exists "
        "to supersede it WITH provenance, not to leave a stale verdict standing")
    assert prov["supersedes"]["verdict"] == "NOT_MEASURED", prov
    assert prov["supersedes"]["by_invocation"] == "some-earlier-invocation", prov
    assert prov["supersedes"]["scope"]["stage_id"] == "stage1", prov
    assert prov["by_invocation"] and prov["by_invocation"] != \
        "some-earlier-invocation", prov
    # and the superseded copy is kept beside it, as R-0915-138 requires
    assert (tmp_path / prov["supersedes"]["kept_at"]).is_file()


def test_the_invocation_id_is_inherited_by_a_nested_clause():
    """A nested clause must resolve the SAME invocation id as its parent, or the
    document cannot say which invocation published it. Inherited through the
    environment, which is what a subprocess can actually see."""
    import flow_compliance_check as FCC
    import os as _os
    mine = FCC._invocation_id()
    assert _os.environ.get(FCC._INVOCATION_ENV) == mine, (
        "the id is not exported, so a child would mint a different one")
    # a child process resolving it must get the same string
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(PROGRAMS)!r});"
         " import flow_compliance_check as F; print(F._invocation_id())"],
        capture_output=True, text=True, timeout=300)
    assert out.stdout.strip() == mine, (out.stdout, out.stderr[-200:])


def test_a_pass_stamps_its_receipt_with_its_own_invocation(project):
    """The pass writes its own receipt AND says who wrote it, so a later reader can
    tell this stage's own pass from a clause inside somebody else's."""
    import flow_compliance_check as FCC
    out = project / "reports/zz_own.json"
    FCC.main([str(project), "--stage-id", "stage1", "--json", str(out)])
    assert out.is_file(), "the pass did not write its own receipt"
    doc = json.loads(out.read_text())
    assert doc["invocation"], doc.get("invocation")
    assert doc["scope"]["stage_id"] == "stage1"


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


def test_a_scoped_audit_is_replaced_not_accumulated(project):
    """One file per POPULATION, replaced. Accumulating one per invocation would turn
    `reports/audit/` into a pile a reader has to date-sort, which is the problem this
    is meant to remove."""
    for _ in range(3):
        subprocess.run(
            [sys.executable, str(PROGRAMS / "flow_compliance_check.py"),
             str(project), "--stage-id", "stage4",
             "--json", str(project / "reports/zz_s4.json")],
            capture_output=True, text=True, timeout=2400)
    files = sorted((project / "reports/audit/scoped").glob("*.json"))
    assert [f.name for f in files] == [
        "phase23_completion_audit.stage-stage4.json"], [f.name for f in files]
    assert not list((project / "reports/audit/scoped").glob("*.tmp")), (
        "an atomic write left its temp file behind")


def test_the_bubble_up_corpus_does_not_read_a_scoped_audit(project):
    """A reader that rglobs `reports/audit` must not take a stage's audit as the
    run's evidence — the whole reason the scoped files live in their own subtree."""
    import step_internal_fail_bubble_up_check as B
    assert B._is_a_scoped_audit(
        Path("reports/audit/scoped/phase23_completion_audit.stage-stage4.json"))
    assert not B._is_a_scoped_audit(
        Path("reports/audit/phase23_completion_audit.json"))
    assert not B._is_a_scoped_audit(Path("reports/orchestrator/phase3_one_shot.json"))


def test_the_fpga_pre_burn_guard_reads_its_own_pass_not_the_whole_runs_audit():
    """H1, at the consumer. The guard runs `--phase 2` (32 of 70 steps) and used to
    read the CANONICAL audit as its primary verdict, falling back to stdout only when
    that file was absent — so in `design_one_shot_runner` every burn was blocked by
    the whole-flow `--strict` pass that precedes it, and through the MCP program tool
    an OLD canonical PASS let a structurally failing design burn.

    Source-pinned because driving a real burn needs the board: the guard must name the
    SCOPED path, and must not accept the canonical document without dating it against
    its own pass.
    """
    src = (PLUGIN / "mcp-eda/src/devices/fpga/terasic-de10lite"
           / "driver.py").read_text()
    assert 'os.path.join(\n        project_root, "reports", "audit", "scoped",' in src \
        or '"reports", "audit", "scoped",' in src, (
        "the pre-burn guard does not read its own pass's scoped audit")
    assert "_pass_started" in src, (
        "the guard does not date the audit against its own pass, so a stale one can "
        "still decide a burn")
    # ORDER, not a window: the pass is dated BEFORE it runs, and its own scoped audit
    # is consulted BEFORE any canonical path appears in the verdict logic.
    started_at = src.index("_pass_started = time.time()")
    scoped_at = src.index("phase23_completion_audit.phase-2.json")
    assert started_at < scoped_at, "the guard reads its audit before dating the pass"
    # and every canonical read that remains is gated on the same timestamp
    canonical_at = src.index('"reports", "audit",\n                             '
                             '"phase23_completion_audit.json"')
    assert scoped_at < canonical_at, (
        "the canonical whole-run audit is consulted before this pass's own")
    tail = src[canonical_at - 600:canonical_at + 600]
    assert "_pass_started" in tail, (
        "a canonical read survives with no freshness gate, so another population's "
        "verdict can still decide a burn")
