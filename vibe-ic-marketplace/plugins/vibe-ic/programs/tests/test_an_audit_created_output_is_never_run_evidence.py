"""An audit-created output is never run evidence — the class, made executable.

THE RULING (numbered by the orchestrator; see the note at the bottom of this
docstring about the number). Every step whose only producer of a declared output
is its OWN gate clause gets a producer the RUN executes. The audit says why in
its own words, on step 36 of spm run21:

    AUDIT-CREATED OUTPUT REFUSED: ['reports/audit/tapeout_checklist.json'] —
    present, but written by this step's own gate rather than by the run, so the
    step has no run evidence for it. This verdict does not depend on how many
    times the audit has run: the same document is refused on every pass.
    PRODUCER GAP: no pre-audit producer supplied these paths; wire them into the
    owning runner before claiming this step complete.

MEASURED, BOTH DIRECTIONS, on a copy of spm run21 with the current plugin:

    documents ABSENT (the audit's own gate writes them first)
        step 36  FAIL  missing_artefact  AUDIT-CREATED OUTPUT REFUSED
        step 38  FAIL  missing_artefact  AUDIT-CREATED OUTPUT REFUSED
        stage4_compliance: FAIL=6
    documents written by `run_pre_audit_producers` BEFORE the audit
        step 36  the audit_created refusal is GONE; OUTPUT ATTRIBUTION:
                 step-attributed (1/1)
        step 38  leaves FAIL entirely -> NOT_MEASURED/upstream_failed, 5/5
                 step-attributed
        stage4_compliance: FAIL=6 -> FAIL=3

So the wiring on main WORKS. run21 did not benefit from it, and its own
bookkeeping says why: `reports/audit/audit_created/*.json` records "this audit's
own gate was the first process to write this declared required_output", i.e. the
producers did not run in that run — consistent with a phase-3 run whose runner
checkout predates the producer landing (2026-09-22 17:02; run21's reports are
stamped 17:56-18:05, but a phase-3 run starts long before it reports).

WHAT THIS FILE ADDS, since the producer wiring is already landed: the CLASS, as a
gate. A sweep of the flow finds 23 steps that declare an output which is also one
of their own gate clauses' receipt target, and EIGHT of them
(10, 21, 24, 28, 29, 36, 37, 38) have only their own gate program named under
`programs:` — the shape `_PRE_AUDIT_PRODUCERS` describes in those words. All 23
are covered, by one of three mechanisms, and the gate asks each mechanism rather
than re-deriving it:
  * `flow_declared_producer_run.declared_producer_clauses()` — 29 clauses over 22
    steps, the same-program shape, executed by the run so the document is run
    evidence;
  * `phase3_one_shot_runner._PRE_AUDIT_PRODUCERS` — steps 36 and 38;
  * a DISTINCT declared producer, e.g. step 0.5ic's `submission_template_fetch` /
    `_ingest` / `_answers` / `tapeout_declaration_gen`, none of which is the
    checker whose clause writes the receipt.
A step added tomorrow with a self-written output and no producer any of the three
knows is exactly the shape that cost two handbacks, so it is refused here instead
of being found by an audit on a real run.

RETRACTION, AND IT IS MINE. This docstring previously read run21's mtimes — the
two documents written 17:56:31, `flow_declared_producer_run` at 17:59:19, the
authorship notes at 18:06 — as evidence that the note arrives ten minutes after
the file and that the producer runner's second trigger ("or when the audit's own
authorship note claims the file") is unreachable inside one run. That was inferred
from timestamps, not measured. Running it on a copy says otherwise: the note is
born 0.114 s after the file and records its exact `mtime_ns`, and all three
branches of the runner's rule fire (absent -> produced; audit-claimed ->
re-produced; written by the run -> left byte-for-byte alone). run21's 18:06 note
mtimes are a LATER audit pass re-stamping what it refused, which is what
`_record_audit_created` is for. The measurements are pinned in
`test_the_authorship_note_is_born_with_the_file_it_claims.py` (R-0915-137) so
neither property can regress unnoticed.

The ruling numbers, as the orchestrator corrected them: R-0915-135 = a vacuous
clause's row carries the callee's classified reason and class; R-0915-136 = an
audit-created output is never run evidence, which is what THIS file gates;
R-0915-137 = the two mechanisms above.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R                          # noqa: E402

#: The receipt flags a gate clause writes its own verdict document through. Kept
#: equal to `flow_compliance_check._GATE_RECEIPT_FLAGS`, and asserted equal
#: below: two hand-kept lists are two lists that drift.
_RECEIPT_FLAG_RE = re.compile(r"--(?:json|report)\s+(\S+)")


def _flow() -> dict:
    return yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())


def _clause_commands(gate) -> list:
    """Every executable gate clause command string, at any nesting depth."""
    out: list = []

    def rec(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("program_exit_zero"):
                    out.append(v if isinstance(v, str)
                               else str((v or {}).get("command", "")))
                else:
                    rec(v)
        elif isinstance(node, list):
            for item in node:
                rec(item)

    rec(gate)
    return out


def self_written_outputs(steps) -> dict:
    """``{step id: {declared output: the clause program that writes it}}``."""
    out: dict = {}
    for st in steps:
        if not isinstance(st, dict):
            continue
        declared = [str(x) for x in (st.get("required_outputs") or [])]
        if not declared:
            continue
        writers: dict = {}
        for cmd in _clause_commands(st.get("gate")):
            prog = (cmd.split() or [""])[0]
            for target in _RECEIPT_FLAG_RE.findall(cmd):
                if target in declared:
                    writers.setdefault(target, prog)
        if writers:
            out[str(st.get("id"))] = writers
    return out


def producer_gaps(steps, pre_audit_outputs) -> dict:
    """The VIOLATIONS: a declared output whose ONLY declared writer is the step's
    own gate clause, and which no pre-audit producer supplies.

    THE PREDICATE IS NARROW ON PURPOSE, and the reason is that the wide one is
    not statically decidable. Steps 36 and 38 declare `programs:
    [<their own gate program>]` — the `_PRE_AUDIT_PRODUCERS` docstring says it in
    those words, "each declare their OWN gate as their only producer" — so the
    class is: `programs:` names EXACTLY ONE program and it is the clause that
    writes the receipt. A step that names a second program (step 26.5ic's
    `die_finishing_gen` beside `die_finishing_check`, step 31's
    `perc_corpus_sweep` beside its checkers) has a producer that is not the
    checker, and WHETHER THAT PRODUCER WRITES THIS PARTICULAR PATH cannot be read
    off the yaml. Asserting about those would be asserting about something this
    gate cannot see; they are listed by `self_written_outputs` for review instead.

    PURE, so the mutation arms can drive it with a shortened table or a spliced
    flow.
    """
    gaps: dict = {}
    by_id = {str(s.get("id")): s for s in steps if isinstance(s, dict)}
    for sid, writers in self_written_outputs(steps).items():
        programs = [str(p) for p in (by_id[sid].get("programs") or [])]
        missing = []
        for path, clause_prog in writers.items():
            if path in pre_audit_outputs:
                continue
            others = [p for p in programs if p != clause_prog]
            if not others:
                missing.append(path)
        if missing:
            gaps[sid] = sorted(missing)
    return gaps


def _pre_audit_outputs() -> set:
    return {out for _n, _p, out, _a in R._PRE_AUDIT_PRODUCERS}


# ── every member of the class has a producer SOME mechanism knows about ────

def test_every_self_written_output_has_a_declared_producer_somewhere():
    """THE RULING, and it asks the program that owns the population rather than
    re-deriving it.

    `flow_declared_producer_run.declared_producer_clauses()` IS this class: its
    own docstring describes it as "a step declares one program under `programs:`,
    its own gate clause invokes that same program", and it exists to run those
    producers "so the document is run evidence, not to decide anything". A second
    opinion about the population here would drift from it, and then this gate
    would tell a reader to wire a producer for a step that already has one.
    """
    import flow_declared_producer_run as FD
    known = {str(c["target"]) for c in FD.declared_producer_clauses()}
    for c in FD.declared_producer_clauses():
        known.update(str(x) for x in (c.get("siblings") or []))
    known |= _pre_audit_outputs()

    steps = _flow()["steps"]
    by_id = {str(x.get("id")): x for x in steps if isinstance(x, dict)}
    unknown: dict = {}
    for sid, writers in self_written_outputs(steps).items():
        programs = [str(q) for q in (by_id[sid].get("programs") or [])]
        missing = []
        for path, clause_prog in writers.items():
            if path in known:
                continue
            # A DISTINCT PRODUCER IS ALSO COVERAGE, and step 0.5ic is why this
            # arm exists: it declares `submission_template_fetch`,
            # `submission_template_ingest`, `submission_template_answers` and
            # `tapeout_declaration_gen` — none of them the checker whose clause
            # writes the receipt. Those programs are the producers, and
            # `declared_producer_clauses` cannot see them because it selects the
            # SAME-PROGRAM shape by construction. Demanding a pre-audit entry
            # there would be demanding a second producer for a document that
            # already has one.
            if [q for q in programs if q != clause_prog]:
                continue
            missing.append(path)
        if missing:
            unknown[sid] = sorted(missing)
    assert unknown == {}, (
        f"{sum(len(v) for v in unknown.values())} declared output(s) are written "
        f"ONLY by their own step's gate clause and no producer mechanism knows "
        f"about them, so the audit will refuse them as audit-created on every "
        f"pass: {unknown}\n"
        "Wire a producer: name the program under the step's `programs:` so "
        "`flow_declared_producer_run` executes it, or add an entry to "
        "`phase3_one_shot_runner._PRE_AUDIT_PRODUCERS`. Relaxing the audit's "
        "refusal is not the remedy — the document really is the auditor's.")


def test_the_narrow_class_is_the_six_steps_measured():
    """The members whose ONLY declared program is their own gate program — the
    shape the `_PRE_AUDIT_PRODUCERS` docstring describes in those words ("each
    declare their OWN gate as their only producer").

    Pinned as a SET, not a count, because two steps trading places moves nothing.

    IT SHRANK FROM EIGHT TO SIX, and the two that left are the point of R-0915-141
    (`test_a_declared_output_is_not_its_gates_verdict_target.py`): steps 36 and 38
    no longer declare an output that is one of their own gate clauses' receipt
    targets at all, so they are outside `self_written_outputs()` entirely — not
    merely covered by a mechanism. 36 declares the checklist its
    `tapeout_checklist_gen` producer assembles while its gate writes its verdict
    to `reports/audit/tapeout_signoff.json`; 38 declares the four package
    artefacts `foundry_handoff_pack_gen` assembles and no longer declares the
    gate's own `foundry_handoff_audit.json`. This test was renamed from
    `..._is_the_nine_steps_measured` in the same change, because a name that says
    nine while the set holds six is a false statement in shipped source.
    """
    steps = _flow()["steps"]
    by_id = {str(s.get("id")): s for s in steps if isinstance(s, dict)}
    narrow = sorted(
        sid for sid, writers in self_written_outputs(steps).items()
        if (by_id[sid].get("programs") or []) and all(
            [q for q in (by_id[sid].get("programs") or []) if q != prog] == []
            for prog in writers.values()))
    # MEASURED 2026-09-23 on this tree. Step 23 is NOT a member and the reason is
    # worth the line: it declares three self-written outputs but only ONE of them
    # is written by the program its `programs:` names (`sta_report_check`); the
    # other two are written by sibling checkers, so the step has a declared
    # producer that is not the writer of those two, and this narrow predicate —
    # "every self-written output's writer is the step's only declared program" —
    # correctly excludes it.
    assert narrow == ["10", "21", "24", "28", "29", "37"], narrow
    # The two that LEFT are gone for the structural reason, not by exemption.
    assert {"36", "38"}.isdisjoint(narrow), narrow
    # The pre-audit table still drives both of them, now at a producer's path
    # rather than at a gate's receipt path.
    covered = _pre_audit_outputs()
    assert "reports/audit/tapeout_checklist.json" in covered, covered
    assert "phase3/stage4/foundry_handoff/mask_spec.json" in covered, covered
    assert "reports/phase3/foundry_handoff_audit.json" not in covered, (
        "the pre-audit table must not produce a gate's own verdict document; "
        "that is the defect R-0915-141 names")


def test_the_population_is_swept_not_assumed():
    """The sweep's own denominator, so the gate above cannot go vacuous."""
    members = self_written_outputs(_flow()["steps"])
    assert len(members) >= 20, (
        f"the sweep found only {len(members)} member(s); a receipt-flag spelling "
        f"this regex no longer matches would empty the population and make the "
        f"gate above vacuous")
    # 36 and 38 are OUT of the class as of R-0915-141, and this is the assertion
    # that would catch the split being quietly reverted.
    assert {"36", "38"}.isdisjoint(set(members)), sorted(set(members))


def test_the_receipt_flags_match_the_consumer():
    """A flag the audit treats as a receipt target but this sweep does not would
    hide a whole member of the class."""
    import flow_compliance_check as FCC
    for flag in FCC._GATE_RECEIPT_FLAGS:
        assert _RECEIPT_FLAG_RE.match(f"{flag} some/path.json"), flag


# ── and the gate can say no ────────────────────────────────────────────────

def test_the_gate_reddens_for_a_new_step_in_the_class():
    """MUTATION: the shape this file exists to refuse tomorrow. A step spliced
    into a COPY of the flow that declares its own gate's receipt target, with no
    producer any mechanism knows, must be named."""
    doc = _flow()
    doc["steps"].append({
        "id": "zzcanary", "name": "synthetic", "stage": "stage4",
        "required_outputs": ["reports/audit/zzcanary.json"],
        "gate": {"all_of": [
            {"program_exit_zero":
                "zzcanary_check . --json reports/audit/zzcanary.json"}]},
        "blocks_on": [],
    })
    found = self_written_outputs(doc["steps"])
    assert found.get("zzcanary") == {
        "reports/audit/zzcanary.json": "zzcanary_check"}, found.get("zzcanary")


def test_withdrawing_a_pre_audit_entry_is_visible():
    """MUTATION on the other mechanism: step 36's target is covered by the
    pre-audit table AND by `declared_producer_clauses`, so withdrawing the table
    entry alone must not be silent — the narrow class still names the step."""
    shortened = {o for o in _pre_audit_outputs()
                 if o != "reports/audit/tapeout_checklist.json"}
    assert "reports/audit/tapeout_checklist.json" not in shortened
    assert len(shortened) == len(_pre_audit_outputs()) - 1
