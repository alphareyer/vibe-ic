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


def test_every_pass_publishes_the_canonical_audit_with_its_scope(project):
    """ONE CANONICAL AUDIT, saying what it judged.

    My first cut had a scoped pass write its audit elsewhere so a reader would find a
    whole-flow document or none. MEASURED: that broke 16 cases across 5 shipped test
    files, because "run a scoped pass, then read this document" is a common and
    legitimate shape — and restoring the canonical write turned all of them green. So the
    document stays where every consumer expects it, and says `scope.whole_flow` so the
    four readers that treat it as THE RUN'S VERDICT can refuse it.
    """
    canonical = project / "reports/audit/phase23_completion_audit.json"
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         "--stage-id", "stage4",
         "--json", str(project / "reports/zz_s4.json")],
        capture_output=True, text=True, timeout=2400)
    assert canonical.is_file(), "a scoped pass left no completion audit at all"
    doc = json.loads(canonical.read_text())
    assert doc["scope"]["whole_flow"] is False, doc["scope"]
    assert doc["scope"]["stage_id"] == "stage4"
    assert doc["invocation"], doc.get("invocation")
    assert not (project / "reports/audit/scoped").exists(), (
        "the scoped subtree is back; every consumer of the canonical path loses its "
        "audit again")


def test_a_whole_flow_pass_says_whole_flow(project):
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         "--json", str(project / "reports/zz_whole.json")],
        capture_output=True, text=True, timeout=2400)
    doc = json.loads(
        (project / "reports/audit/phase23_completion_audit.json").read_text())
    assert doc["scope"]["whole_flow"] is True, doc["scope"]


def test_the_scope_predicate_answers_in_every_direction():
    """ONE PREDICATE, SHARED by the four readers — four copies of this rule would drift
    the way two copies of the verdict rule already did."""
    from _audit_scope import audit_scope_is_whole_flow as q
    assert q({"scope": {"whole_flow": True}})[0] is True
    whole, signal, why = q({"scope": {"whole_flow": False, "step_count": 32,
                                     "flow_step_total": 70, "phase": "2"}})
    assert whole is False and "32 of 70" in why and signal == "scope.whole_flow"
    # a document predating R-0915-147 cannot state its population: the weaker signal is
    # used AND disclosed
    whole2, signal2, _ = q({"phase": "all"})
    assert whole2 is True and "weaker signal" in signal2
    assert q({"phase": "2"})[0] is False
    assert q(None)[0] is False


def test_the_publish_path_refuses_a_scoped_audit_as_the_runs_verdict(tmp_path):
    """READER 1 and 2: `benchmark_evidence_publish._audit_verdict` and, through it, the
    convergence guard. REFUSED rather than downgraded — its caller is publishing evidence,
    and an unanswerable question must stop a publication rather than colour it."""
    import benchmark_evidence_publish as B
    audit = tmp_path / "reports/audit/phase23_completion_audit.json"
    audit.parent.mkdir(parents=True, exist_ok=True)

    audit.write_text(json.dumps({"verdict": "PASS", "scope": {
        "whole_flow": True, "step_count": 70, "flow_step_total": 70}}))
    assert B._audit_verdict(tmp_path, None)[0] == "PASS"

    audit.write_text(json.dumps({"verdict": "PASS", "scope": {
        "whole_flow": False, "step_count": 32, "flow_step_total": 70, "phase": "2"}}))
    try:
        B._audit_verdict(tmp_path, None)
        raise AssertionError("a scoped audit was accepted as the run's verdict")
    except B.Refuse as exc:
        assert "not this run's whole-flow verdict" in str(exc), str(exc)

    # legacy, no scope block: the weaker signal, and it still answers
    audit.write_text(json.dumps({"verdict": "PASS", "phase": "all"}))
    assert B._audit_verdict(tmp_path, None)[0] == "PASS"


def test_phase3_does_not_carry_a_scoped_audit_as_the_runs(tmp_path):
    """READER 3: `_derive_headline_verdict` copies what it finds into
    phase3_one_shot.json as `completion_audit_verdict`, and the front door reads THAT as
    the run's audit axis. A scoped document must not travel under that name."""
    import phase3_one_shot_runner as R
    import _path_layout as _pl
    audit = _pl.report_path(tmp_path, "phase23_completion_audit.json")
    audit.parent.mkdir(parents=True, exist_ok=True)

    audit.write_text(json.dumps({"verdict": "FAIL", "scope": {
        "whole_flow": True, "step_count": 70, "flow_step_total": 70}}))
    _headline, carried, _note = R._derive_headline_verdict(tmp_path, "PASS")
    assert carried == "FAIL", carried

    audit.write_text(json.dumps({"verdict": "FAIL", "scope": {
        "whole_flow": False, "step_count": 10, "flow_step_total": 70,
        "stage_id": "stage4"}}))
    headline, carried2, note = R._derive_headline_verdict(tmp_path, "PASS")
    assert carried2 is None, carried2
    assert headline == "PASS", headline
    assert "not this run's whole-flow audit" in note and "10 of 70" in note, note


def test_the_fpga_guard_requires_its_own_pass_and_its_own_population():
    """READER 4. Source-pinned because driving a burn needs the board: the guard dates the
    document against its own pass AND checks the scope is the population it asked about."""
    src = (PLUGIN / "mcp-eda/src/devices/fpga/terasic-de10lite"
           / "driver.py").read_text()
    started = src.index("_pass_started = time.time()")
    check = src.index('_scope.get("phase")) != "2"', started)
    assert started < check
    window = src[started:check + 400]
    assert "st_mtime + 1.0 < _pass_started" in window, (
        "the guard no longer dates the audit against its own pass")
    assert '_scope.get("whole_flow") is True' in window, (
        "the guard accepts a whole-flow audit as an answer about its 32 steps")


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


def test_two_identical_scoped_passes_produce_the_same_design_digest(project):
    """A scoped audit carries `run_at` and `invocation`, so its bytes differ on every
    pass. The digest's premise is that a re-run over an UNCHANGED design yields the SAME
    sha256 — so the auditor's own output must not be a design input.

    MEASURED before the exclusion: two FPGA pre-burn guard passes (`--phase 2`) on an
    unchanged tree produced different `design_input_digest.sha256` values.
    """
    digests = []
    for _ in range(2):
        out = project / "reports/zz_p2.json"
        subprocess.run(
            [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
             "--phase", "2", "--strict-structural", "--json", str(out)],
            capture_output=True, text=True, timeout=2400)
        # THE DIGEST LIVES IN THE AUDIT DOCUMENT, not in the compliance report —
        # measured rather than assumed, which cost me a red here.
        audit = project / "reports/audit/phase23_completion_audit.json"
        adoc = json.loads(audit.read_text())
        digests.append((adoc.get("design_input_digest") or {}).get("sha256"))
    assert digests[0] and digests[1], digests
    assert digests[0] == digests[1], (
        f"two identical scoped passes over an unchanged tree produced different design "
        f"digests ({digests}); the auditor's own scoped output is being counted as a "
        f"design input")


def _seeded_step2_project(tmp_path: Path) -> Path:
    """A project where step 2's outputs EXIST, so its advisory clause actually runs.

    On a bare fixture step 2 returns MISSING early and the republish never happens, so
    the pair the finding is about never forms — which is why the bare fixture hid it.
    """
    proj = tmp_path / "seeded"
    rtl = proj / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "d.v").write_text("module d(); endmodule\n")
    for rel, body in (
            ("reports/phase1/gates/stage_phase1_compliance.json",
             {"program": "flow_compliance_check", "overall": "PASS", "steps": []}),
            ("reports/crosslayer/rewrite_equivalence.json", {"rewrites": []}),
            ("reports/crosslayer/rewrite_equivalence_check.json",
             {"program": "crosslayer_rewrite_fidelity", "verdict": "PASS"}),
            ("reports/phase2/lint/rtl_hygiene.json", {"verdict": "PASS"}),
            ("reports/phase2/lint/rom_init_lint.json", {"verdict": "PASS"})):
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(body) + "\n")
    return proj


def _pass(project: Path, *scope_argv: str) -> dict:
    """One flow_compliance_check pass; returns the audit it wrote."""
    out = project / "reports/zz_pass.json"
    subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(project),
         *scope_argv, "--json", str(out)],
        capture_output=True, text=True, timeout=2400)
    # ONE CANONICAL DOCUMENT for every pass, scoped or not — and its `ruler_flags`
    # ASSERTED to be this pass's, because a nested clause also writes here and reading
    # somebody else's document is how the earlier cut of this arm measured nothing.
    doc_path = project / "reports/audit/phase23_completion_audit.json"
    assert doc_path.is_file(), f"the pass wrote no audit at {doc_path}"
    doc = json.loads(doc_path.read_text())
    flags = ((doc.get("tally_delta") or {}).get("current") or {}).get("ruler_flags")
    if flags:
        want = scope_argv[1] if scope_argv else "all"
        assert str(flags.get("phase")) == want, (
            f"read an audit written by a different pass: ruler_flags={flags}")
    return doc


def _moved(audit: dict) -> object:
    """`design_moved` lives in `tally_delta` — the CLASSIFICATION block — not in
    `design_input_digest`, which only carries this pass's own hash.

    Read from the wrong block my first cut returned None for every sequence, so the
    four-sequence arm passed VACUOUSLY: `None is not True` is satisfied by a key that
    does not exist. The RTL-edit arm is what exposed it, which is the whole reason a
    positive direction sits beside the negative one."""
    return ((audit.get("tally_delta") or {}).get("design_moved"))


def _classification(audit: dict) -> object:
    return ((audit.get("tally_delta") or {}).get("classification"))


#: The outcomes that are honest on an unchanged tree. DESIGN_CHANGE is not one of them.
#: NOT_COMPARABLE IS: with ONE canonical audit, the document on disk when a pass reads its
#: prior is whatever the last NESTED clause wrote during the step loop (a 2-step
#: `stage_phase1` audit), while the document the pass LEAVES is its own (70 steps,
#: whole_flow=true — measured). So a whole-flow pass compares itself against a different
#: population and correctly refuses the comparison. That is how main behaves, and refusing
#: to compare is not a claim about the design.
_HONEST_ON_UNCHANGED = {"NOT_COMPARABLE", "UNCHANGED", "MEASUREMENT_CHANGE",
                        "UNEXPLAINED_TALLY_MOVE", "NOT_ATTRIBUTABLE"}


def test_no_sequence_of_passes_calls_an_unchanged_tree_a_design_change(tmp_path):
    """THE FOUR SEQUENCES, on a tree where step 2's outputs exist so its clause really
    republishes. W = whole flow, S = scoped (`--phase 2`, the FPGA guard's shape).

    Each pass writes an audit, a receipt, a superseded copy and a report. None of that is
    a design input, so no sequence may read the tree as MOVED — DESIGN_CHANGE on an
    unchanged tree is the defect (it suppresses the TALLY_DELTA warning and says "this
    movement is about the design"), and `design_moved=True` is the fact behind it.

    The classification is asserted to be a KNOWN one, so a missing key fails this arm
    rather than passing it — the vacuity that let an earlier cut of this test report a
    clean result while reading a field that did not exist.
    """
    for label, seq in (("W->S->W", ((), ("--phase", "2"), ())),
                       ("S->W->S", (("--phase", "2"), (), ("--phase", "2"))),
                       ("S->S", (("--phase", "2"), ("--phase", "2"))),
                       ("W->W", ((), ()))):
        proj = _seeded_step2_project(tmp_path / label.replace("->", "_"))
        audits = [_pass(proj, *argv) for argv in seq]
        delta = audits[-1].get("tally_delta") or {}
        assert delta.get("classification") in _HONEST_ON_UNCHANGED, (
            f"{label}: classification={delta.get('classification')!r} — DESIGN_CHANGE on "
            f"an unchanged tree means an auditor output is counted as a design input, and "
            f"an unknown/missing classification means this arm measured nothing")
        assert delta.get("design_moved") is not True, (
            f"{label}: design_moved=True on an UNCHANGED tree. {delta.get('statement')}")


def test_the_published_design_hash_is_invariant_across_auditor_passes(tmp_path):
    """END TO END, on the number the audit PUBLISHES.

    Not `build_digest(scan, [])` — my first attempt did that and failed for a reason worth
    recording: auditor writes OUTSIDE `reports/audit/` (70 `reports/metrics/*.json`, the
    per-check receipts) are excluded by the FOOTPRINT the program computes, not by
    `is_auditor_output`. Passing an empty footprint measured a digest no pass ever
    publishes.

    So this reads `design_input_digest.sha256` from the document each pass actually wrote,
    keeping the first one aside before the second overwrites it. Two auditor passes over an
    unchanged design must publish the SAME hash; one RTL edit must change it.
    """
    proj = _seeded_step2_project(tmp_path / "published")
    canonical = proj / "reports/audit/phase23_completion_audit.json"

    def _run_and_take() -> str:
        subprocess.run(
            [sys.executable, str(PROGRAMS / "flow_compliance_check.py"), str(proj),
             "--json", str(proj / "reports/zz_pass.json")],
            capture_output=True, text=True, timeout=2400)
        doc = json.loads(canonical.read_text())
        block = doc.get("design_input_digest") or {}
        assert block.get("sha256"), f"the pass published no design hash: {block}"
        return block["sha256"]

    first = _run_and_take()
    second = _run_and_take()
    assert first == second, (
        f"two auditor passes over an UNCHANGED design published different design hashes "
        f"({first[:12]}... vs {second[:12]}...); an auditor output is being counted as a "
        f"design input")

    (proj / "phase2/stage1/rtl/d.v").write_text(
        "module d(); wire unused_probe; endmodule\n")
    third = _run_and_take()
    assert third != second, (
        "an RTL edit did not change the published design hash; the exclusion is too wide")


def test_the_auditor_output_rule_names_each_shape(tmp_path):
    """The three shapes, and the one that must NOT be excluded."""
    import design_input_digest as D
    for rel, body in (
            ("reports/audit/phase23_completion_audit.json", '{"verdict": "PASS"}'),
            ("reports/phase1/gates/stage_phase1_compliance.superseded-2.json", "{}"),
            ("reports/phase1/gates/stage_phase1_compliance.json",
             '{"program": "flow_compliance_check"}')):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        assert D.is_auditor_output(tmp_path, f) is True, rel
    # a design source of the same shape is NOT an auditor output
    src = tmp_path / "input/rtl/core.v"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("module core(); endmodule\n")
    assert D.is_auditor_output(tmp_path, src) is False
    other = tmp_path / "reports/phase3/drc_signoff.json"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text('{"program": "drc_report_check", "verdict": "PASS"}')
    assert D.is_auditor_output(tmp_path, other) is False, (
        "another program's receipt is not the auditor's output")
