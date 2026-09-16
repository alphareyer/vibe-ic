"""Step 11's six outputs are owed by a design that declares DFT, and by no other.

MEASURED 2026-09-15 (lane icspm3) on `spm` x gf180mcuD at main 972c21233.
Step 11 declared six `required_outputs` unconditionally; the run produced none
of them and the step read MISSING — on a run where the runner had READ the
design's L20 and disclosed the skip in its own log::

    SKIP step11_dft_insertion: DFT insertion and ATPG disclosed-skipped: L20
    does not authorize automatic scan insertion (verdict=SKIP; reason=no DFT
    requirement derivable from the design's own inputs and L20 asserts none);
    LEC still runs on the pre-DFT netlist

and had written `phase2/stage2/dft/dft_atpg_not_run.json` with
`gate_reason: l20_dft_contract`. Three ordering violations followed — FS1, 13
and 14 "marked done while dependency [11] DFT insertion = MISSING" — and they
were the WHOLE of what kept `stage2_compliance`, `stage3_compliance` and
`stage4_compliance` red on that run. The RUN honoured the declaration and the
FLOW did not.

BOTH DIRECTIONS ARE CASES HERE, because a rule that only ever excuses is not a
rule:

  * a design that DECLARES DFT still owes all six — a scan chain, a TAP or a
    BIST block anywhere in L20 makes the condition MET;
  * a design that declares none is SKIPPED-CONDITION **with the declaration
    cited**, never a waiver and never a hand-written file;
  * and every way the declaration can be ABSENT rather than negative — no
    document, unparseable, a field missing — RUNS the step, because absence of
    a declaration is not a declaration of absence. A phase 1 that fell over
    cannot excuse DFT.

chip-AGNOSTIC: synthetic L20 documents in tmp_path, plus assertions over the
shipped yaml.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

STEP11_OUTPUTS = (
    "phase2/stage2/dft/scan_netlist.v",
    "phase2/stage2/dft/atpg_coverage.rpt",
    "phase2/stage2/dft/transition_atpg_plan.md",
    "reports/phase2/dft/coverage.json",
    "reports/phase2/dft/bsdl_plan.json",
    "phase2/stage2/dft/coverage.yml",
)

#: The value each field must carry for the design to have declared DFT absent.
ABSENT = {"dft_present": False, "scan_chains": [], "bist_mbist": [],
          "jtag_tap": None}


def _F():
    import flow_compliance_check as F  # noqa: PLC0415
    return F


def _step11():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(FLOW_YAML.read_text(errors="replace"))

    def walk(n):
        if isinstance(n, dict):
            if "id" in n and ("required_outputs" in n or "gate" in n):
                yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    for s in walk(doc):
        if str(s.get("id")) == "11":
            return s
    raise AssertionError("step 11 is not in the shipped flow")


def _project(tmp_path, fields, *, write=True, body=None):
    # R-0915-64 — A DESIGN THAT DECLARES NO DFT HAS INPUT DOCUMENTS THAT SAY
    # NOTHING ABOUT DFT, and this fixture used to have no input at all. Since
    # R-0915-64 the declarer requires the design's OWN INPUT to corroborate the
    # L-doc (documents scanned, terms searched, zero hits), because a GENERATED
    # skeleton's initialisers are not an input statement. An empty corpus is a
    # scan with no denominator and declares nothing, so without this the
    # fixture describes a project that cannot exist.
    #
    # ONLY THE FIXTURE MOVES. Every assertion below is untouched: these cases
    # exist to prove the L-DOC half decides, and they still do — the corpus
    # here is deliberately silent about DFT so the L20 fields remain the only
    # thing that varies between them.
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L1_product_metadata.md").write_text(
        "# Part\nA small core that multiplies two numbers.\n",
        encoding="utf-8")
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    if write:
        doc = body if body is not None else {
            "doc_id": "L20", "doc_name": "L20_DFT_SCAN_TOPOLOGY",
            "applicability": "APPLICABLE", "fields": dict(fields)}
        (gd / "L20_DFT_SCAN_TOPOLOGY.json").write_text(
            doc if isinstance(doc, str) else json.dumps(doc))
    return tmp_path


# ── the shipped flow says it ──────────────────────────────────────────────

def test_step_11_is_conditioned_on_the_design_s_own_L20_declaration():
    step = _step11()
    cond = (step.get("condition") or {}).get("l_doc_declares")
    assert isinstance(cond, dict), "step 11 carries no l_doc_declares condition"
    assert cond.get("l_doc") == "L20"
    assert cond.get("all_absent") == ABSENT, (
        "the clause must require EVERY DFT declaration to be absent; one "
        "field alone could be a skeleton's default", cond.get("all_absent"))
    assert step.get("condition_kind") == "design_dependent"


def test_step_11_still_declares_all_six_outputs():
    """The repair is a CONDITION, not a shrunken declaration. A design that
    declares DFT owes exactly what it owed before."""
    outs = [str(o) for o in (_step11().get("required_outputs") or [])]
    for rel in STEP11_OUTPUTS:
        assert rel in outs, rel
    assert len(outs) == len(STEP11_OUTPUTS), outs


# ── direction 1: a design that DECLARES DFT still owes the six ────────────

@pytest.mark.parametrize("field,asserting", [
    ("dft_present", True),
    ("scan_chains", [{"name": "chain0", "length": 64}]),
    ("bist_mbist", [{"kind": "mbist"}]),
    ("jtag_tap", {"ir_width": 4}),
])
def test_one_field_asserting_DFT_makes_the_step_RUN(tmp_path, field,
                                                    asserting):
    F = _F()
    fields = dict(ABSENT)
    fields[field] = asserting
    proj = _project(tmp_path, fields)
    assert F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"]) is None
    assert F._check_condition(proj, _step11()["condition"]) is True


# ── direction 2: a design that declares none is N/A, WITH THE CITATION ────

def test_a_design_declaring_no_DFT_stands_the_step_down(tmp_path):
    F = _F()
    proj = _project(tmp_path, ABSENT)
    got = F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"])
    assert got is not None
    # R-0915-64 widened the return to (cited, detail, input-scan evidence).
    cited, detail, _evidence = got
    assert cited == "phase1/generated_docs/L20_DFT_SCAN_TOPOLOGY.json"
    for key in ABSENT:
        assert key in detail, (key, detail)
    assert F._check_condition(proj, _step11()["condition"]) is False


def test_the_skip_is_not_silent_it_names_the_document(tmp_path):
    """A design-declared skip must be tellable from a trigger file nobody
    authored: the two need different actions from a reviewer."""
    F = _F()
    proj = _project(tmp_path, ABSENT)
    # R-0915-64 widened the return to (cited, detail, input-scan evidence).
    # Unpacking only; both assertions below are the ones this case has always
    # made about the L-doc citation.
    cited, detail, _evidence = F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"])
    assert cited.endswith("L20_DFT_SCAN_TOPOLOGY.json")
    assert "dft_present=False" in detail


# ── absence of a declaration is not a declaration of absence ──────────────

def test_no_L20_at_all_runs_the_step(tmp_path):
    F = _F()
    proj = _project(tmp_path, ABSENT, write=False)
    assert F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"]) is None
    assert F._check_condition(proj, _step11()["condition"]) is True


def test_an_unparseable_L20_runs_the_step(tmp_path):
    F = _F()
    proj = _project(tmp_path, ABSENT, body="{ this is not json")
    assert F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"]) is None
    assert F._check_condition(proj, _step11()["condition"]) is True


@pytest.mark.parametrize("missing", sorted(ABSENT))
def test_a_partly_filled_L20_runs_the_step(tmp_path, missing):
    """A field that is NOT THERE is not a field declaring absence."""
    F = _F()
    fields = {k: v for k, v in ABSENT.items() if k != missing}
    proj = _project(tmp_path, fields)
    assert F._l_doc_declares_absence(
        proj, _step11()["condition"]["l_doc_declares"]) is None
    assert F._check_condition(proj, _step11()["condition"]) is True


def test_a_clause_this_predicate_does_not_understand_runs_the_step(tmp_path):
    F = _F()
    proj = _project(tmp_path, ABSENT)
    for spec in (None, {}, "L20", {"l_doc": "L20"},
                 {"all_absent": ABSENT}, {"l_doc": "L20", "all_absent": {}}):
        assert F._l_doc_declares_absence(proj, spec) is None, spec


def test_SKIPPED_CONDITION_is_an_EXCUSED_status_so_the_cascade_clears():
    """WHY THIS CLOSES THE THREE ORDERING VIOLATIONS, asserted rather than
    assumed: the ordering guard excuses a dependency whose status is in
    `_NOT_APPLICABLE`, and SKIPPED-CONDITION is one of those."""
    import flow_step_execution_coverage_check as C  # noqa: PLC0415
    assert "SKIPPED-CONDITION" in C._NOT_APPLICABLE
    assert "MISSING" not in C._NOT_APPLICABLE


def test_step_11_names_no_condition_owner_and_why(tmp_path):
    """MEASURED, not omitted. With `condition_owner: {step: D1, declaration:
    "L20.fields..."}` the audit refused the row --

        blocked-by-upstream(step D1): condition-owner configuration is
        INVALID: Step D1 does not define non-empty declaration '...'

    -- and demoted it back to MISSING. The owner mechanism resolves a
    declaration through `condition_declarations`, a `files_exist` record, and
    it exists for the one ambiguity a FILE predicate cannot resolve: neither
    marker existing can mean either "the other route was selected" or "the
    owner failed and selected nothing". This predicate carries its own
    discriminator — the three states are asserted by the cases above — so an
    owner here would name a record that does not exist.
    """
    step = _step11()
    assert "condition_owner" not in step, (
        "step 11's condition is resolved by the design's own L-doc; an owner "
        "would have to name a condition_declarations record and none applies")
    # and the three states this replaces an owner with are all exercised:
    F = _F()
    spec = step["condition"]["l_doc_declares"]
    declared_absent = _project(tmp_path / "a", ABSENT)
    declared_present = _project(tmp_path / "b", {**ABSENT, "dft_present": True})
    not_declared = _project(tmp_path / "c", ABSENT, write=False)
    assert F._l_doc_declares_absence(declared_absent, spec) is not None
    assert F._l_doc_declares_absence(declared_present, spec) is None
    assert F._l_doc_declares_absence(not_declared, spec) is None


# ── the at-speed ATPG steps are the same class and take the same clause ───

#: Step 11 plus the three at-speed ATPG steps. All four are DFT, all four are
#: owed by a design that declares DFT and by no other, and MEASURED on spm x
#: gf180mcuD at main 066027775 all four read MISSING on every run.
DFT_STEPS = ("11", "DT1", "DT2", "DT3")


def _step(sid):
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(FLOW_YAML.read_text(errors="replace"))

    def walk(n):
        if isinstance(n, dict):
            if "id" in n and ("required_outputs" in n or "gate" in n):
                yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    for s in walk(doc):
        if str(s.get("id")) == sid:
            return s
    raise AssertionError(f"step {sid} is not in the shipped flow")


@pytest.mark.parametrize("sid", DFT_STEPS)
def test_every_dft_step_reads_the_same_declaration(sid):
    cond = (_step(sid).get("condition") or {}).get("l_doc_declares")
    assert isinstance(cond, dict), f"step {sid} carries no l_doc_declares"
    assert cond.get("l_doc") == "L20"
    assert cond.get("all_absent") == ABSENT, (sid, cond.get("all_absent"))


@pytest.mark.parametrize("sid", DFT_STEPS)
def test_every_dft_step_stands_down_only_on_the_declaration(tmp_path, sid):
    """Both directions, per step. Declared-absent stands the step down and
    cites the document; ONE field asserting DFT runs it; a document that is
    not there runs it."""
    F = _F()
    spec = _step(sid)["condition"]["l_doc_declares"]
    absent = _project(tmp_path / f"{sid}-absent", ABSENT)
    got = F._l_doc_declares_absence(absent, spec)
    assert got is not None and got[0].endswith("L20_DFT_SCAN_TOPOLOGY.json")

    for field, asserting in (("dft_present", True),
                             ("scan_chains", [{"name": "c0"}]),
                             ("bist_mbist", [{"kind": "mbist"}]),
                             ("jtag_tap", {"ir_width": 4})):
        proj = _project(tmp_path / f"{sid}-{field}", {**ABSENT, field: asserting})
        assert F._l_doc_declares_absence(proj, spec) is None, (sid, field)

    nodoc = _project(tmp_path / f"{sid}-nodoc", ABSENT, write=False)
    assert F._l_doc_declares_absence(nodoc, spec) is None


@pytest.mark.parametrize("sid", ("DT1", "DT2", "DT3"))
def test_the_at_speed_steps_keep_their_dependency_triggers(sid):
    """The clause is ADDED beside the existing `files_exist` trigger, never
    instead of it: on a design that declares DFT the step is selected exactly
    as it was before."""
    cond = _step(sid)["condition"]
    assert cond.get("files_exist"), sid
    assert "l_doc_declares" in cond, sid


@pytest.mark.parametrize("sid", ("DT1", "DT2", "DT3"))
def test_a_dft_declaring_design_still_reaches_the_files_exist_trigger(
        tmp_path, sid):
    F = _F()
    cond = _step(sid)["condition"]
    proj = _project(tmp_path / sid, {**ABSENT, "dft_present": True})
    # The L-doc predicate declines, so the answer is the one the step had
    # before this clause existed: its own files_exist trigger, unsatisfied on
    # an empty tree.
    assert F._l_doc_declares_absence(proj, cond["l_doc_declares"]) is None
    assert F._check_condition(proj, cond) is False
    for pat in cond["files_exist"]:
        (proj / pat).parent.mkdir(parents=True, exist_ok=True)
        (proj / pat).write_text("x")
    assert F._check_condition(proj, cond) is True
