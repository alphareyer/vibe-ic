"""The producer pass runs in FLOW ORDER, and asks the document who wrote it.

R-0915-160, both halves of one defect: a clause that ran too early published a false claim,
and nothing would ever regenerate the document carrying it.

(a) ORDER. `declared_producer_clauses`' own docstring has always said "in flow order". It was
    not: the gate channel is collected in one pass and `program_outputs` in a second, and the
    two were CONCATENATED, so every clause of the second channel ran after every clause of the
    first whatever step it belonged to. MEASURED on the shipped flow -- 27 clauses, indices
    0..24 already in perfect flow order, and exactly ONE `program_outputs` clause (step 31's
    `perc_corpus_sweep` -> `reports/phase3/perc_sweep.json`) landing last, after step 37's.

    Step 37's clause runs `stage3_compliance`, which publishes a verdict about STAGE 3 -- and
    step 31 is in stage 3. So it judged step 31 before step 31's own artefact existed, and the
    published report recorded the declared gate `sweep_reach_check` as
    `DESIGN_DECLARED_NA / NOT_RUN_DECLARED`: "this design does not have that", about an
    artefact the same pass writes four clauses later. A FALSE NA, not a stale number.

(b) REGENERATION. `owed()` asked only "is the target absent?" and "does the audit's note claim
    it?", so an `invoked_as: audit` document with no note answered "the run already produced
    it" -- and the false-NA report above survived every later run. The stamp now answers
    first, in the same precedence as `flow_compliance_check.authorship_answer`, and the note
    is consulted only for documents that state no role.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))

import flow_declared_producer_run as FDP     # noqa: E402
import flow_compliance_check as FCC          # noqa: E402
import _gate_authorship as GA                # noqa: E402


# ── (a) flow order, derived from the flow ────────────────────────────────────

def _flow_steps() -> list:
    doc = yaml.safe_load((PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())
    return list(FDP._iter_steps(doc))


def _flow_step_order() -> dict:
    """Step id -> position, read from the flow definition itself."""
    return {str(s.get("id")): i for i, s in enumerate(_flow_steps())}


def _flow_step_stage() -> dict:
    """Step id -> the stage the flow files it under (`stage3`, `stage4`, ...)."""
    return {str(s.get("id")): s.get("stage") for s in _flow_steps()}


_STAGE_REPORT = re.compile(r"\b(stage\d+)_compliance\b")


def _stage_report_steps() -> dict:
    """Stage -> the ids of the steps whose clauses run that stage's compliance report.

    Read from the flow, never named: v1.24.73 (#2635) moved step 31 from stage 3 into stage
    4 and behind step 37, so WHICH report judges step 31 is a fact of the flow, not of this
    file.
    """
    out: dict = {}
    for s in _flow_steps():
        text = json.dumps(s.get("gate")) + json.dumps(s.get("programs"))
        for stage in set(_STAGE_REPORT.findall(text)):
            out.setdefault(stage, []).append(str(s.get("id")))
    return out


def test_the_clauses_run_in_the_order_the_flow_states_them():
    """Derived from the flow, so a NEW `program_outputs` clause arrives in place.

    Nothing here names a step or a path: the expected order IS the flow's order, so this arm
    cannot go stale when the flow grows, and a clause collected into the wrong channel shows
    up as an inversion rather than as a number someone has to notice.
    """
    order = _flow_step_order()
    clauses = FDP.declared_producer_clauses()
    assert clauses, "no declared-producer clauses at all; the arm below cannot fail"

    positions = [order.get(str(c["step"])) for c in clauses]
    assert all(p is not None for p in positions), (
        [c["step"] for c, p in zip(clauses, positions) if p is None])
    assert positions == sorted(positions), (
        "the clause list is not in flow order; these ran before an earlier step's: "
        + ", ".join(
            f"[{i}] step {clauses[i]['step']} ({clauses[i]['target']})"
            for i in range(len(positions))
            if any(positions[i] > positions[j] for j in range(i + 1, len(positions)))))


def test_a_steps_own_clauses_stay_in_the_order_the_flow_declares_them():
    """The sort is STABLE, so within a step the gate channel keeps its place first."""
    clauses = FDP.declared_producer_clauses()
    by_step: dict = {}
    for i, c in enumerate(clauses):
        by_step.setdefault(str(c["step"]), []).append(i)
    for sid, idxs in by_step.items():
        assert idxs == list(range(idxs[0], idxs[0] + len(idxs))), (
            f"step {sid}'s clauses are not contiguous: {idxs} -- a stable merge keeps each "
            f"step's clauses together")


def test_the_one_program_outputs_clause_sits_with_its_own_step():
    """The measured instance, named: step 31's `perc_sweep.json` producer.

    Not a restatement of the sort -- it pins the case the ruling is about, so if the two
    channels are concatenated again this says which clause moved and where to.

    WHICH REPORT JUDGES STEP 31 IS READ FROM THE FLOW. When R-0915-160 was measured step 31
    was in stage 3, before step 37, and step 37's `stage3_compliance` clause was the report
    that judged it too early. v1.24.73 (#2635, "GDS only after gates") filed step 31 under
    stage 4 and placed it AFTER step 37, so `stage3_compliance` no longer judges it at all and
    `perc_sweep` now legitimately runs after that clause. The property is unchanged: the
    report of step 31's OWN stage must run after step 31's producer, and no stage report in
    the producer pass may run before a clause of a step in its own stage.
    """
    clauses = FDP.declared_producer_clauses()
    order = _flow_step_order()
    stage_of = _flow_step_stage()
    idx = {c["target"]: i for i, c in enumerate(clauses)}
    sweep = idx.get("reports/phase3/perc_sweep.json")
    assert sweep is not None, sorted(idx)

    # and it really is the step-31 clause, beside step 31's others
    assert str(clauses[sweep]["step"]) == "31"
    neighbours = {str(clauses[i]["step"])
                  for i in range(max(0, sweep - 3), sweep + 1)}
    assert neighbours == {"31"}, neighbours

    # the report that judges step 31 is its own stage's, and it runs after step 31
    stage = stage_of.get("31")
    judges = _stage_report_steps().get(stage or "")
    assert judges, (
        f"no step runs {stage}_compliance, so nothing judges step 31's stage and the arm "
        f"below cannot fail; re-measure this test's premise")
    for sid in judges:
        assert order[sid] > order["31"], (
            f"step {sid} runs {stage}_compliance at flow position {order[sid]}, before step "
            f"31 at {order['31']} -- that report judges step 31 before its artefact exists "
            f"and records sweep_reach_check as DESIGN_DECLARED_NA")
    compl = idx.get(f"reports/phase3/gates/{stage}_compliance.json")
    if compl is not None:
        assert sweep < compl, (
            f"step 31's perc_sweep producer runs at [{sweep}], after the {stage} compliance "
            f"clause at [{compl}]")

    # every stage report in the pass runs after every clause of its own stage's steps
    reports = [(k, m.group(1)) for k, c in enumerate(clauses)
               if (m := re.search(r"(stage\d+)_compliance\.json$", c["target"]))]
    assert reports, "no stage-compliance clause in the pass; the arm below cannot fail"
    for k, st in reports:
        before = [i for i, c in enumerate(clauses[:k])
                  if stage_of.get(str(c["step"])) == st]
        late = [f"[{i}] step {c['step']} ({c['target']})"
                for i, c in enumerate(clauses)
                if i > k and stage_of.get(str(c["step"])) == st]
        assert before, (
            f"no {st} clause precedes {st}_compliance at [{k}]; the ordering arm is vacuous")
        assert not late, (
            f"{st}_compliance at [{k}] judges {st} before these of its producers ran: "
            + ", ".join(late))


# ── (b) owed() asks the document ─────────────────────────────────────────────

@pytest.fixture()
def clause():
    got = [c for c in FDP.declared_producer_clauses()
           if "stage3_compliance" in c.get("target", "")]
    assert got, "the flow no longer declares that clause; re-measure this arm's premise"
    return got[0]


def _staged(tmp_path: Path, clause: dict, doc) -> Path:
    """A project where the step RAN (a sibling is on disk) and the target holds `doc`."""
    sibs = [s for s in (clause.get("siblings") or []) if s]
    assert sibs, "the step declares no sibling output, so `owed` takes its did-not-run branch"
    sib = tmp_path / sibs[0].replace("*", "x")
    sib.parent.mkdir(parents=True, exist_ok=True)
    sib.write_bytes(b"\x00" * 64)
    target = tmp_path / clause["target"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc) + "\n")
    return target


_BASE = {"program": "flow_compliance_check", "steps": [{"id": "37"}]}


def test_an_audit_stamped_document_is_still_owed(tmp_path, clause):
    _staged(tmp_path, clause, dict(_BASE, **{GA.DOC_KEY: GA.ROLE_AUDIT}))
    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert [r["target"] for r in to_run] == [clause["target"]], (to_run, skipped)
    assert "invoked_as: audit" in to_run[0]["why"], to_run[0]["why"]


def test_a_producer_stamped_document_is_the_runs_and_is_left_alone(tmp_path, clause):
    target = _staged(tmp_path, clause, dict(_BASE, **{GA.DOC_KEY: GA.ROLE_PRODUCER}))
    before = target.read_bytes()
    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert to_run == [], to_run
    assert [r["target"] for r in skipped] == [clause["target"]]
    assert "the document says so" in skipped[0]["why"], skipped[0]["why"]
    assert target.read_bytes() == before


def test_a_role_less_document_keeps_todays_note_rule(tmp_path, clause):
    """Both directions of the fall-back, because silence is not a claim either way."""
    _staged(tmp_path, clause, dict(_BASE))

    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert to_run == [] and skipped, (to_run, skipped)
    assert skipped[0]["why"] == "the run already produced it"

    # now the audit's own note claims it -- driven through the real recorder
    FCC._record_audit_created(tmp_path, clause["step"], [clause["target"]])
    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert [r["target"] for r in to_run] == [clause["target"]], (to_run, skipped)
    assert "authorship note" in to_run[0]["why"], to_run[0]["why"]


def test_an_unreadable_document_leaves_the_runs_artefact_alone(tmp_path, clause):
    """A parse failure must not manufacture work; it says nothing, like an absent stamp."""
    target = _staged(tmp_path, clause, dict(_BASE))
    target.write_text("{not json at all")
    assert FDP._document_role(target) is None
    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert to_run == [] and skipped, (to_run, skipped)


def test_the_stamp_answers_before_the_note(tmp_path, clause):
    """Precedence, pinned: a `producer` stamp beats a stale note claiming the same path.

    This is the same order as `authorship_answer` -- the stamp is the write saying what it
    was, the note is bookkeeping about it -- and it is what stops a leftover note from
    making the run redo its own work.
    """
    _staged(tmp_path, clause, dict(_BASE, **{GA.DOC_KEY: GA.ROLE_PRODUCER}))
    FCC._record_audit_created(tmp_path, clause["step"], [clause["target"]])
    assert FCC._prior_audit_created(tmp_path, clause["step"], [clause["target"]]), (
        "the note was not recorded, so this arm is not testing precedence")

    to_run, skipped = FDP.owed(tmp_path, [clause])
    assert to_run == [], (
        "a stale note outranked the document's own statement that the RUN wrote it")
    assert "the document says so" in skipped[0]["why"]


# ── what the order is FOR: the published report judges the gate ──────────────

_STEP_31_SIBLINGS = (
    "reports/phase3/drc_signoff.rpt", "reports/phase3/lvs.rpt",
    "reports/phase3/erc.rpt", "reports/phase3/lvs.json",
    "reports/phase2/gates/erc_density.json",
    "reports/phase3/magic_illegal_overlap.json",
    "reports/phase3/lvs_verdict.json", "reports/phase3/drc_signoff.json",
)


def _step31_stage_program() -> Path:
    """The compliance program of the stage the FLOW files step 31 under.

    It was `stage3_compliance` until v1.24.73 (#2635) moved step 31 into stage 4; after that
    `stage3_compliance` prints no `sweep_reach_check` line at all, and a differential over it
    measures nothing.
    """
    stage = _flow_step_stage().get("31")
    assert stage, "the flow no longer has a step 31; re-measure this arm's premise"
    prog = PROGRAMS / f"{stage}_compliance.py"
    assert prog.is_file(), prog
    return prog


def _stage3_report(tmp_path: Path, *, with_sweep: bool) -> str:
    """Run the REAL compliance of step 31's stage over a project with/without perc_sweep.json."""
    import subprocess

    proj = tmp_path / ("with" if with_sweep else "without")
    (proj / "input").mkdir(parents=True)
    (proj / "input" / "spec.md").write_text("# a counter\n")
    for rel in _STEP_31_SIBLINGS:
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"verdict": "PASS"}) if rel.endswith(".json")
                     else "PASS\n")
    if with_sweep:
        sweep = proj / "reports/phase3/perc_sweep.json"
        sweep.parent.mkdir(parents=True, exist_ok=True)
        sweep.write_text(json.dumps({"verdict": "PASS", "reach": []}) + "\n")

    r = subprocess.run(
        [sys.executable, str(_step31_stage_program()), str(proj)],
        capture_output=True, text=True, cwd=str(proj), timeout=1800)
    return r.stdout + r.stderr


def test_the_stage_three_report_judges_the_gate_once_its_artefact_exists(tmp_path):
    """THE COST OF THE ORDER, as a differential on one staged project.

    A stage report publishes a verdict about its stage, which contains step 31 -- stage 3
    when this was measured, stage 4 since v1.24.73 (#2635); the stage is read from the flow.
    Run that stage's real compliance with step 31's `perc_sweep.json` absent -- the state the
    pass was in when the report ran BEFORE step 31 -- and with it present, which is what flow
    order guarantees. The gate `sweep_reach_check` must be JUDGED in the second, not written off
    as not applicable to this design.
    """
    without = _stage3_report(tmp_path, with_sweep=False)
    with_it = _stage3_report(tmp_path, with_sweep=True)

    def line(out: str) -> str:
        got = [l.strip() for l in out.splitlines()
               if "sweep_reach_check" in l and "GATE EVIDENCE" in l]
        return got[0] if got else ""

    a, b = line(without), line(with_it)
    assert a and b, (a, b)
    assert "DESIGN_DECLARED_NA" in a and "NOT_RUN_DECLARED" in a, (
        f"the premise no longer holds: with perc_sweep.json absent the report does not "
        f"record a false NA -- {a}")
    assert "DESIGN_DECLARED_NA" not in b and "NOT_RUN_DECLARED" not in b, (
        f"perc_sweep.json is on disk and the report STILL records sweep_reach_check as not "
        f"applicable to this design -- {b}")
    assert "NOT_APPLICABLE" not in b, b
