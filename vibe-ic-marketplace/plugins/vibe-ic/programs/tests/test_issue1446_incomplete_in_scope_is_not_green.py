#!/usr/bin/env python3
"""vibe-ic#1446 — a P0 that measured NOTHING over a chain that never ran.

THE INPUT, on `--phase 2 --strict-structural` — the mode that narrows the
verdict scope to the P0 umbrella alone:

    [INCOMPLETE] Step P0: Structural-RTL gates
                 (P0 umbrella, 0 of 246 checkers returned a verdict)
    ✗ [P0] … = INCOMPLETE marked done while dependency [D1] … = MISSING
    Overall: PASS   rc=0

Zero of 246 checkers answered, the Phase-1 chain under them never ran, and the
only step inside the verdict scope published green.

WHY IT TOOK A CONJUNCTION TO FIX. Each half is already settled, in the other
direction, by a landed decision this file must not reopen:

  * INCOMPLETE ALONE MUST STAY GREEN. `test_p0_umbrella_verdict_coverage`
    asserts "INCOMPLETE is a disclosure tier, not a failure — it must not turn a
    run red on its own"; `test_issue497_step2_consumers_read_records` asserts
    "gates that never ran must not force the verdict". Both run over a SATISFIED
    `blocks_on` chain.
  * A BROKEN ANCESTRY ALONE MUST STAY GREEN, which is vibe-ic#1429: a terminal
    that RAN, AUDITED and PASSED, then had its PASS voided by an out-of-scope
    dependency (`PASS_VOIDED_BY_DEPENDENCY`), is informational in this mode. The
    void is about CERTIFICATION; the gates did look and saw clean.

So neither "INCOMPLETE gates" nor "the ordering guard reads the terminal" is
correct on its own — each was measured here and each breaks three landed tests.
What gates is the pair: nothing was measured, AND the inputs that would have
been measured were never produced. This file pins the conjunction and BOTH
single-condition controls, so a future repair of one half cannot silently take
the other with it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "flow_compliance_check.py"

_L_DOCS = (
    "L1_DATASHEET", "L2_FRS", "L3_CMD_PROTOCOL", "L4_REGMAP", "L5_ADI_SPEC",
    "L6_CONTROL_LOGIC", "L7_TEST_DEBUG", "L8_TIMING_WAVEFORM",
    "L8_RTL_CONSTANTS", "L9_INTEGRATION_SPEC", "L10_TEST_CASES",
    "L11_OTP_CONTENT", "L12_BEHAVIORAL_SEQUENCES", "L13_BRINGUP",
)


def _import_fcc():
    """A fresh module object per test, so a monkeypatched gate runner cannot
    leak between cases."""
    spec = importlib.util.spec_from_file_location("fcc_issue1446", PROG)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fcc_issue1446"] = mod
    spec.loader.exec_module(mod)
    return mod


def _bare_project(tmp_path: Path) -> Path:
    """RTL and nothing else — P0's declared ancestry never ran."""
    project = tmp_path / "proj"
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.sv").write_text("module core; endmodule\n")
    return project


def _close_ancestry(project: Path) -> Path:
    """Stage every artefact step D1 declares, so the chain under P0 is closed.

    D1 holds ALL of its `required_outputs` ("satisfied: N/20 — the gate passed,
    but every declared output must be produced"), so this has to stage the
    L-docs, the coverage report, the expert-track report AND the extraction
    pattern catalogue. If D1 gains a 21st,
    `test_the_ancestry_control_really_closes_the_chain` below goes red and names
    it, rather than this helper quietly ceasing to close anything.

    That is not hypothetical: #1348 added the 19th
    (`phase1/extraction_patterns.json`) and the control went red naming it
    (vibe-ic#1351), which is the mechanism this docstring promises, working.
    """
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    for name in _L_DOCS:
        (gd / f"{name}.json").write_text(
            json.dumps({"schema": name, "generated_by": "test fixture"}))
    # The 20th D1 output, added after this closed-ancestry control was written.
    # Presence is the contract under test here; physical floorplan semantics
    # remain owned by l9_floorplan_contract_check's own fixtures.
    (gd / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({
        "schema": "L19_CONSTRAINTS_PDK", "generated_by": "test fixture"}))
    (gd / "L21_POWER_INTENT.json").write_text(json.dumps(
        {"schema": "L21_POWER_INTENT", "generated_by": "test fixture",
         "supply_pins": [], "external_supplies": [], "pads": []}))
    rp = project / "reports" / "phase1"
    rp.mkdir(parents=True, exist_ok=True)
    (rp / "extraction_coverage_report.md").write_text("# coverage\n\n100%\n")
    (rp / "extraction_coverage_report.json").write_text(
        json.dumps({"coverage_pct": 100}))
    ra = project / "reports" / "audit" / "phase1"
    ra.mkdir(parents=True, exist_ok=True)
    # The producer record is written AFTER the answer pack below, because
    # `check_report` binds the two: the digest it stores must be of the answer
    # this tree actually carries. See `_stage_producer_report`.
    # AND THE ANSWER THE SECOND PASS CONSUMES. Staging the REPORT alone does
    # not close this chain, and MEASURED on live main 7903c1972305 (2026-09-03,
    # pinned image sha256:66c33ff2..., host load 5.5) that is why both arms of
    # this file were red:
    #
    #   flow_compliance_check --phase 2 --strict-structural  ->  [FAIL] Step D1
    #   of D1's five blocking clauses:
    #     phase1_all_l_docs_present_check      rc 0
    #     analog_a0_skip_forbidden_check       rc 0
    #     l_doc_todo_stub_count_check          rc 0
    #     phase1_coverage_report_present_check rc 2
    #     phase1_expert_parse_track .          rc 1   <-- this
    #
    # `phase1_expert_parse_track` is a TWO-PASS protocol: a program cannot
    # spawn a subagent, so pass 1 writes the hand-off pack, reports
    # HANDOFF_EMITTED, and exits 1 by design — AND OVERWRITES the report this
    # fixture staged. Every headless invocation is pass 1, so D1 could not
    # pass on any tree, fixture or real. `1aa24ef268` (#2014) fixed exactly
    # this shape in `phase1_one_shot_runner`'s disposition layer and did not
    # reach this gate clause, which calls the program directly.
    #
    # A "closed ancestry" means a Phase 1 that really RAN, and a Phase 1 that
    # really ran has been through both passes. So the fixture stages the
    # SECOND pass's input: the agent answer the consumer reads back. Measured
    # end state — CONSUMED, 1 expectation, agreed 1, verdict PASS, rc 0.
    #
    # NOT DONE HERE, deliberately: downgrading D1's
    # `program_exit_zero: phase1_expert_parse_track .` to advisory. That is a
    # relabel, and it would change the verdict of every REAL run, not just
    # this fixture's. Nor is `CONSUMED_EMPTY` used — measured, it is also rc 1,
    # and #312 separated it from CONSUMED on purpose: an empty reading and a
    # full one produce the same zero findings and only one is coverage.
    pack = ra / "expert_parse_track_pack"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "l_doc_expectations.json").write_text(json.dumps({
        "expectations": [{
            "id": "ancestry-fixture-l1-schema",
            "layer": "L1",
            "field_path": "schema",
            "requirement": "L1 names its own schema",
            # `expected_tokens` is what makes an expectation DECIDABLE:
            # `converge_ai_expectation` refuses a prose-only one rather than
            # counting it as agreed. The token is satisfied by the L-doc stubs
            # staged above, so this answer is met by THIS fixture's tree and
            # not by assertion.
            "expected_tokens": ["L1_"],
            "evidence": ["staged by _close_ancestry: this fixture models a "
                         "Phase 1 that completed BOTH passes of the expert "
                         "track, not one that emitted a hand-off and stopped"],
            "expert_source": "test fixture"}]}))
    _stage_producer_report(project, ra, pack / "l_doc_expectations.json")
    # The 19th (#1348). `phase1_doc_one_shot_runner._seed_canonical_from_
    # backfilled_subset` returns WITHOUT writing when nothing was backfilled,
    # and on a tree with no `input/docs` nothing can be — so a hand-staged
    # Phase 1 stages it. An object holding only the provenance key is that
    # seeder's own empty shape and parses as a catalogue with no entries, not as
    # MALFORMED (`extraction_coverage_check._load_explicit_patterns` wants a
    # top-level object and skips non-list values).
    (project / "phase1").mkdir(parents=True, exist_ok=True)
    (project / "phase1" / "extraction_patterns.json").write_text(json.dumps({
        "_comment": ("Canonical extraction patterns. No auto-discovered "
                     "literal was backfilled into a typed L doc on this tree, "
                     "so the catalogue is empty; staged by test fixture.")}))
    return project


def _stage_producer_report(project: Path, audit_dir: Path, answer: Path) -> None:
    """The `phase1/expert_parse_track.json` a Phase 1 that REALLY RAN leaves.

    #2206 turned D1's clause `phase1_expert_parse_track . --check-report` into a
    pure READER: it no longer runs the track, it reads the producer's record and
    refuses credit for an audit-time second pass. So the record has to identify
    the reading — the producer invocation, its return code, WHICH Phase-1 root
    was judged, and WHICH answer was consumed — and a hand-staged Phase 1 must
    stage all four or D1 is FAIL, not closed.

    MEASURED on live main 72bd2679bc before this: the old two-key stub named the
    program as `phase1_expert_parse_track.py` while `PROGRAM` is
    `phase1_expert_parse_track`, so the very first clause refused it —

        [FAIL] Step D1: Phase 1 Doc Extraction
          program failed: phase1_expert_parse_track . --check-report
          INCOMPLETE: ... report unavailable or stale: missing expert-track
          producer record

    — and both of this file's CONTROLS (`..._over_a_closed_chain_stays_green`
    and `..._ancestry_control_really_closes_the_chain`) went red for that, not
    for anything either of them is about.

    NOTHING IS PINNED HERE. The program name, the consumed-status word and the
    root identity are all READ FROM THE PROGRAM and computed over this fixture's
    own tree, so the next contract change reddens the ancestry control and names
    itself, exactly as this helper's caller promises. A literal copy would go
    green against a program that had been deleted.
    """
    spec = importlib.util.spec_from_file_location(
        "peptrack_issue1446", PROG.parent / "phase1_expert_parse_track.py")
    track = importlib.util.module_from_spec(spec)
    sys.modules["peptrack_issue1446"] = track
    spec.loader.exec_module(track)

    root = track.phase1_root_identity(project)
    assert root.get("status") == "OK", (
        "PRECONDITION: the staged L-docs must be a readable Phase-1 root, "
        f"otherwise this fixture closes nothing: {root}")
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "expert_parse_track.json").write_text(json.dumps({
        "program": track.PROGRAM,
        "verdict": "PASS",
        "findings": [],
        "producer": {"invocation_id": "issue1446-closed-ancestry-fixture",
                     "invoked_by": "test fixture",
                     "returncode": 0},
        "phase1_root": root,
        "ai_subtrack": {
            "status": track.AI_CONSUMED,
            "answer_sha256": hashlib.sha256(answer.read_bytes()).hexdigest()},
        "execution": {"complete": True, "observed_ai_consumed": 1},
        "generated_by": "test fixture"}))


def _stub_p0(monkeypatch, mod, *, passing: int):
    """P0 publishes `passing` records, all PASS, no FAIL.

    `passing=0` is NOT "the gates passed" — no record at all is what the
    empty-denominator guard reads as INCOMPLETE. That is the single variable
    separating each red case below from its control.
    """
    records = [mod._p0_gate_record(f"synthetic_gate_{i}", "PASS", "",
                                   {"exit_code": 0})
               for i in range(passing)]

    def _stub(_project, **kwargs):
        out = kwargs.get("records_out")
        if out is not None:
            out.extend(records)
        return (True, [], [], [])

    monkeypatch.setattr(mod, "_run_structural_rtl_gates", _stub)


def _status(out: str, step_id: str) -> str:
    m = re.search(rf"^\s*\S*\s*\[([\w-]+)\s*\] Step\s+{re.escape(step_id)}:",
                  out, re.M)
    assert m, f"PRECONDITION: step {step_id} must appear in the report:\n{out}"
    return m.group(1)


def _no_verdict_word(status):
    """RB2-03 (#2063) — "the step measured nothing in its own scope", asked of
    the TIER rather than of one spelling. `verdict` owns the set;
    `INCOMPLETE` and `NOT-MEASURED` are both in it and are adjudicated
    identically, so every property this module tests holds of either."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    import verdict as _T
    return _T.says_nothing_was_measured(status)


def _audit(mod, project, *flags):
    return mod.main([str(project), "--phase", "2", "--strict-structural",
                     *flags])


# ══ 1. THE DEFECT — both conditions present ═══════════════════════════════

def test_a_no_verdict_p0_over_a_broken_chain_is_not_green(
        tmp_path, monkeypatch, capsys):
    project = _bare_project(tmp_path)
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=0)

    rc = _audit(mod, project)
    out = capsys.readouterr().out

    # PRECONDITIONS: both halves must actually be present, or this case is
    # passing for a reason it does not name.
    # RB2-03 (#2063): the P0 word for a 0-of-N population is now
    # `NOT-MEASURED`, a sibling of `INCOMPLETE` in
    # `verdict.NO_VERDICT_IN_SCOPE` and adjudicated identically.
    # The precondition is asked of the TIER, not of one spelling, so a future
    # word in that set cannot walk past this case the way NOT-MEASURED did.
    assert _no_verdict_word(_status(out, "P0")), out
    # R-0915-85 — THE CONTRADICTION THIS PRECONDITION LOOKED FOR IS DISSOLVED,
    # not lost. `[P0] … marked done while dependency [D1] … = FAIL` could only
    # be raised because `INCOMPLETE` answered True to "does this step claim it
    # is done" — it was in neither negative set, so a step that had measured
    # nothing was adjudicated as claiming to have delivered a result.
    # `NOT_MEASURED` says the opposite in the word itself, so there is no
    # contradiction left to detect: the step does not claim to be done, and
    # the guard correctly raises nothing. What must NOT happen is the run
    # going green on the back of that, which is what the two lines below own.
    assert not re.search(r"\[P0\].*marked done while dependency", out), out
    assert re.search(r"D1.*(FAIL|missing)", out), out

    # R-0915-85 — STILL NOT GREEN, and now it says WHY in the word itself.
    # This case used to read `Overall: FAIL`, produced by the ordering guard
    # forcing the verdict. The run did not FAIL: nothing about the design was
    # examined. `NOT_MEASURED` is that sentence, it is still rc 1, and it is
    # the rung this file's whole subject ("a no-verdict P0 in scope is not
    # green") was asking for before there was a word for it.
    assert rc == 1, out
    assert "Overall: NOT_MEASURED" in out, out


def test_the_violation_that_gates_is_named_as_gating(
        tmp_path, monkeypatch, capsys):
    """SCOPED, NOT SUPPRESSED, in the other direction too: when the P0
    violation DOES reach the verdict it must not be filed under the
    "reported, NOT gating" tail, or the report contradicts its own verdict."""
    project = _bare_project(tmp_path)
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=0)

    rep = tmp_path / "gating.json"
    mod.main([str(project), "--phase", "2", "--strict-structural",
              "--json", str(rep)])
    out = capsys.readouterr().out

    doc = json.loads(rep.read_text())
    gating = doc["ordering_violations_gating"]
    every = doc["ordering_violations"]
    info = [ln for ln in every if ln not in gating]
    # R-0915-85 — THE SUBJECT, RESTATED OVER WHAT SURVIVES. This case was
    # written when a no-verdict P0 raised a violation that had to reach the
    # verdict rather than the "reported, NOT gating" tail. `NOT_MEASURED` is
    # not a done-claim, so this fixture raises no P0 violation at all and
    # there is nothing to mis-file. What #2092 settled is still testable and
    # is what is tested: the printed disclosure and the recorded one are TWO
    # PROJECTIONS OF ONE PREDICATE, so a violation may never be in both, and
    # every violation must be in exactly one.
    assert set(gating) <= set(every), (gating, every)
    assert len(gating) + len(info) == len(every), (gating, info, every)
    assert not (set(gating) & set(info)), (gating, info)
    # The stdout disclosure counts the SAME partition, so the printed and the
    # recorded account cannot drift.
    assert f"{len(info)} of {len(every)} " in out, out
    # And the run is still off PASS, named by the step that measured nothing.
    assert doc["overall"] == "NOT_MEASURED", doc["overall"]


# ══ 2. CONTROL A — INCOMPLETE alone must stay green ═══════════════════════
#
# The half owned by #599 / #497: "gates that never ran must not force the
# verdict". Same no-record P0, chain CLOSED.

def test_an_incomplete_p0_over_a_closed_chain_stays_green(
        tmp_path, monkeypatch, capsys):
    project = _close_ancestry(_bare_project(tmp_path))
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=0)

    rc = _audit(mod, project)
    out = capsys.readouterr().out

    assert _no_verdict_word(_status(out, "P0")), (
        "PRECONDITION: this control is only meaningful while P0 still "
        "measured nothing — it is the OTHER condition that is supposed to have "
        "changed:\n" + out)
    # R-0915-85 OVERTURNS #599's HALF OF THIS CONTROL, deliberately, and this
    # is the site that says so.
    #
    # #599 ruled that INCOMPLETE "is a disclosure tier, not a failure" and must
    # not turn a run red on its own. That reading is what sha256 run16 was
    # built on: the ONLY step in verdict scope measured nothing, the run
    # answered rc 0, and phase 3 launched on an unproven netlist. The ruling
    # keeps the substance of #599 — the run is NOT FAIL, nothing failed — and
    # changes the exit: a run whose scope holds a step nobody measured cannot
    # be PASS either. `run_verdict`'s precedence puts it at NOT_MEASURED, rc 1.
    #
    # The CHAIN is still the other variable and is still closed here, which is
    # what keeps this a control: the verdict below is NOT_MEASURED, never FAIL,
    # and the ordering guard contributes nothing.
    assert rc == 1, (
        "a closed chain removes the ordering violation, not the hole: P0 "
        "measured nothing, so the run is NOT_MEASURED:\n" + out)
    assert "Overall: NOT_MEASURED" in out, out
    assert not re.search(r"\[P0\].*marked done while dependency", out), out


def test_the_ancestry_control_really_closes_the_chain(
        tmp_path, monkeypatch, capsys):
    """`_close_ancestry` is a CLAIM, and a stale fixture does not fail — it
    just stops testing anything. vibe-ic#1446 was masked for exactly this
    reason: D1 gained an 18th `required_outputs` entry 25 seconds after the
    helper that stages them landed, so the "closed chain" fixture silently
    stopped closing the chain. Checked here so the next entry reddens a test
    that NAMES the missing artefact."""
    project = _close_ancestry(_bare_project(tmp_path))
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=0)

    _audit(mod, project)
    out = capsys.readouterr().out

    assert _status(out, "D1") != "FAIL", (
        "`_close_ancestry` no longer closes P0's ancestry — D1 gained a "
        "`required_outputs` entry the helper does not stage. The report names "
        "it on D1's `required_outputs missing:` line:\n" + out)
    assert not re.search(r"\[P0\].*marked done while dependency", out), out


# ══ 3. CONTROL B — a broken chain alone must stay green (vibe-ic#1429) ═════

def test_a_voided_but_measured_p0_over_a_broken_chain_stays_green(
        tmp_path, monkeypatch, capsys):
    """THE #1429 CASE, and the reason this fix is a conjunction rather than
    "the ordering guard reads the terminal".

    Same broken chain, same violation naming P0 as the terminal — but P0's
    gates RAN and PASSED, so the step is PASS_VOIDED_BY_DEPENDENCY, not
    INCOMPLETE. A void is a statement about certification; #1429 settled that
    it is informational in the mode that declares step-level state
    informational, and this must stay green."""
    project = _bare_project(tmp_path)
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=2)

    rc = _audit(mod, project)
    out = capsys.readouterr().out

    # PRECONDITION: the violation is still there — only P0's own tier differs.
    assert re.search(r"\[P0\].*marked done while dependency", out), (
        "PRECONDITION: this control needs the SAME violation as the defect "
        "case, so that P0's tier is the only variable:\n" + out)
    # R-0915-85 — P0's gates RAN and PASSED, and the cascade then voided it
    # off `D1 = FAIL(missing_artefact)`, so its own tier is now
    # NOT_MEASURED(upstream_failed) rather than the deleted
    # PASS_VOIDED_BY_DEPENDENCY. The two are the SAME fact under one word.
    assert _status(out, "P0") == "NOT_MEASURED", out
    # #1429's rule survives where it was measured: the violation is REPORTED
    # and does NOT gate. What it must not do is read its own cascade back as
    # evidence — a terminal this very violation voided is not a terminal that
    # "returned no verdict of its own", and the reason_class is what keeps
    # them apart now that the word does not.
    # THE CANONICAL AUDIT, which this pass writes like any other. R-0915-150 ruled
    # reader-side: every pass keeps writing `reports/audit/phase23_completion_audit.json`
    # and stamps `scope`, so consumers like this one are untouched, and the four readers
    # that treat that document as THE RUN'S VERDICT are the ones that refuse a scoped one.
    # An earlier cut of this branch had a scoped pass write elsewhere and I re-pointed this
    # reader at it; the measurement that overturned that choice was 16 cases across 5
    # shipped test files breaking exactly here.
    assert "[P0]" not in " ".join(json.loads(
        (project / "reports" / "audit"
         / "phase23_completion_audit.json").read_text(encoding="utf-8")
    ).get("ordering_violations_gating", [])), out
    # The run is still not green — but because P0 certifies nothing, not
    # because the guard forced it. FAIL would say the design failed; it did
    # not, and D1's absence is the only measured fact.
    assert rc == 1, (
        "a P0 voided off a FAILED dependency certifies nothing, so the run "
        "cannot be PASS (R-0915-85):\n" + out)
    assert "Overall: NOT_MEASURED" in out, out


def test_the_step_level_violation_never_gates_either_way(
        tmp_path, monkeypatch, capsys):
    """`[1] Spec-to-RTL = PASS marked done while [D1] = MISSING` is #1429's own
    worked example. Its terminal is step-level, so it is outside the scope in
    BOTH arms and must never appear in the gating subset."""
    for passing in (0, 2):
        project = _bare_project(tmp_path / f"arm{passing}")
        mod = _import_fcc()
        _stub_p0(monkeypatch, mod, passing=passing)
        rep = tmp_path / f"arm{passing}.json"
        mod.main([str(project), "--phase", "2", "--strict-structural",
                  "--json", str(rep)])
        capsys.readouterr()
        doc = json.loads(rep.read_text())
        assert any("[1]" in ln for ln in doc["ordering_violations"]), (
            f"PRECONDITION (passing={passing}): the step-1 violation must be "
            f"REPORTED:\n{doc['ordering_violations']}")
        assert not [ln for ln in doc["ordering_violations_gating"]
                    if ln.startswith("[1]")], (
            f"passing={passing}: a step-level terminal must not gate "
            f"--strict-structural; gating={doc['ordering_violations_gating']!r}")


# ══ 4. THE SUBSET INVARIANT — gating is never wider than reported ═════════

def test_the_gating_subset_is_never_larger_than_what_was_reported(
        tmp_path, monkeypatch, capsys):
    """Adding a second way in must not let the gating list outgrow the
    disclosure list — every gating line has to be a line the reader saw."""
    for passing in (0, 2):
        project = _bare_project(tmp_path / f"sub{passing}")
        mod = _import_fcc()
        _stub_p0(monkeypatch, mod, passing=passing)
        rep = tmp_path / f"sub{passing}.json"
        mod.main([str(project), "--phase", "2", "--strict-structural",
                  "--json", str(rep)])
        capsys.readouterr()
        doc = json.loads(rep.read_text())
        reported = doc["ordering_violations"]
        gating = doc["ordering_violations_gating"]
        assert len(gating) <= len(reported), (passing, gating, reported)
        for line in gating:
            assert line in reported, (passing, line, reported)


def test_default_strict_mode_still_gates_on_every_violation(
        tmp_path, monkeypatch, capsys):
    """The complement: outside `--phase 2 --strict-structural` the scope is the
    whole run, so both readings admit everything and nothing here changes."""
    project = _bare_project(tmp_path)
    mod = _import_fcc()
    _stub_p0(monkeypatch, mod, passing=2)

    rc = mod.main([str(project), "--strict"])
    out = capsys.readouterr().out

    assert "Step-execution ordering violations" in out, out
    assert "NOT gating" not in out, (
        "in full-scope mode every violation gates, so the degrade-loudly line "
        "must not appear:\n" + out)
    assert rc == 1, out
