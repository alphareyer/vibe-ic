#!/usr/bin/env python3
"""R-0929-DELIVERABLE-CONSISTENCY — derived answer text follows the owner.

WHAT WAS MEASURED. Two designs' `input/step_0_5ic_answers.json` carry the
owner-attested `deliverable = DIE` (R-0915-95) beside a DERIVED
`synthesis_area_budget` rationale, written while the deliverable was still an
agent's HARDMACRO, that says "declares deliverable=HARDMACRO". Nothing
produced that text, so nothing re-derived it when the owner ruled, and no check
compared it with the answer it depended on.

EVERY PROPERTY BELOW IS ASSERTED IN THE DIRECTION THAT COULD FAIL.

  1. GATE, NEGATIVE   the stale sentence FAILs the step-0.5ic checker, named by
                      file, field and sentence, in the answers file AND in the
                      declaration generated from it.
  2. GATE, DISCRIMINATION  a MENTION is not an assertion: "not the IP path (a
                      HARDMACRO whose ...)", "argued HARDMACRO", a list of the
                      choices and a key named `deliverable_rationale` pass.
  3. PRODUCER         the answer is regenerated from the design's documents
                      with the premise READ from the attested deliverable: the
                      stale sentence is gone, the DIE basis (pad-limited die:
                      core + pad ring + power ring) is present, the std-cell
                      gate row is carried, the owner's answer is byte-for-byte
                      untouched, and the revision records its reason and both
                      rulings. The regenerated file PASSes the gate.
  4. BOTH ROUTES      a HARDMACRO attestation keeps the HARDMACRO meaning; the
                      premise follows the value, not a literal.
  5. REFUSALS         no owner attestation, or an input that fixes and declines
                      a die, writes nothing.

chip-AGNOSTIC: no vendor, foundry, process node, SKU or design name. The
producer is imported inside each test so this file COLLECTS on a tree without
it and every case fails for its own reason.
"""
from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _tapeout_declaration as TD                              # noqa: E402
import tapeout_declaration_check as CHECK                      # noqa: E402
import tapeout_declaration_gen as GEN                          # noqa: E402

ANSWERS_REL = "input/step_0_5ic_answers.json"
RULE = "DERIVED_TEXT_CONTRADICTS_OWNER_ANSWER"
CITATION = "R-0915-95 (2026-09-17) — owner ruling: the IC path."
STALE = ("The input states NO die or core rectangle to fit inside, so there "
         "is no max_die_dimensions_um for a LIMIT to carry. It declines the "
         "die size twice -- input/docs/L1_product_metadata.md:6 and "
         "input/docs/L9_constraints_floorplan.md:1-2 (die size '不指定') -- "
         "and declares deliverable=HARDMACRO, so it takes no operator slot "
         "whose geometry could supply one. The one absolute area threshold "
         "the input DOES gate on is a STD-CELL area.")
STALE_SENTENCE_FRAGMENT = "declares deliverable=HARDMACRO"
OWNER_RATIONALE = (
    "DIE. Owner ruling (R-0915-95): the IC path -- a tapeout-able die with its "
    "own pad ring -- not the IP path (a HARDMACRO whose die-level rules are "
    "handed to an integrator). Earlier rationales argued HARDMACRO from the "
    "input's silence about a pad ring; the owner's declaration decides.")

L1 = """# L1 Product metadata

## 1.3 Area
整個 block 以 std-cell 實作,實際 die area 由 baseline 跑出後決定。

## 1.5 不在 L1 約束的事
- ❌ 不指定 die size(由 Plugin 依 floorplan target 推算)
"""
L7 = """# L7 Verification plan

### 7.4.1 Baseline (top=widget, lib-a, TT corner)

| 指標 | 值 |
|---|---|
| stdcell area | **1,000 µm²** |
| die area | **4,000 µm²** |

### 7.4.2 Sign-off Acceptance Range

| 指標 | 接受區間 | 絕對門檻 | 是否 sign-off gate |
|---|---|---|---|
| stdcell count(資訊性) | baseline × [0.5, 2.0] | [50, 200] | ❌ 否 |
| stdcell area | **(0, baseline × 1.3]** | ≤ 1,300 µm² | ✅ 是 |
"""
L9 = """# L9 Constraints

### 9.2.3 Die size
- 不指定。由 Plugin 依照 `FP_CORE_UTIL` 與 pad ring 推算決定。
"""


def _gen():
    return importlib.import_module("area_budget_basis_gen")


def _answers(deliverable="DIE", owner=True, rationale=STALE):
    doc = {
        "schema": "vibe-ic/step_0_5ic_answers/1",
        "_comment": ["The design's own side of step 0.5ic; nothing in this "
                     "file is inferred by any program."],
        "operator_template": {"path": None, "slot": None,
                              "absent_reason": "no operator for this block"},
        "answers": {
            "deliverable": deliverable,
            "deliverable_rationale": OWNER_RATIONALE,
            "top_cell": "widget",
            "synthesis_area_budget": {"status": "NOT_APPLICABLE",
                                      "rationale": rationale},
        },
        "derived_from": [
            "input/docs/L2_architecture.md:3 -- COUNTER-EVIDENCE, weighed in "
            "deliverable_rationale: a capability claim, not a pad ring"],
    }
    if owner:
        doc["answer_provenance"] = {
            "deliverable": {"answered_by": "owner", "citation": CITATION}}
    return doc


def _project(tmp_path, answers=None, l9=L9):
    proj = tmp_path / "proj"
    docs = proj / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L1_product_metadata.md").write_text(L1, encoding="utf-8")
    (docs / "L7_verification_plan.md").write_text(L7, encoding="utf-8")
    (docs / "L9_constraints_floorplan.md").write_text(l9, encoding="utf-8")
    (proj / ANSWERS_REL).write_text(
        json.dumps(answers or _answers(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return proj


def _load(proj):
    return json.loads((proj / ANSWERS_REL).read_text(encoding="utf-8"))


def _declare(proj):
    assert GEN.main([str(proj), "--answers", str(proj / ANSWERS_REL),
                     "--json", str(proj / "gen.json")]) == 0


# --------------------------------------------------------------------------- #
# 1. the gate refuses the stale sentence, at its source and at its copy
# --------------------------------------------------------------------------- #
def test_the_stale_rationale_is_refused_by_sentence_and_field():
    got = TD.derived_text_contradictions(_answers(), source=ANSWERS_REL)
    assert len(got) == 1, got
    r = got[0]
    assert r["rule"] == RULE
    assert r["field"] == "answers.synthesis_area_budget.rationale"
    assert STALE_SENTENCE_FRAGMENT in r["sentence"]
    assert "The one absolute area threshold" not in r["sentence"]
    assert r["claimed"] == "HARDMACRO" and r["attested"] == "DIE"
    assert r["producer"] == "area_budget_basis_gen"
    for needle in (ANSWERS_REL, r["sentence"], "area_budget_basis_gen",
                   "R-0929-DELIVERABLE-CONSISTENCY"):
        assert needle in r["message"]


def test_the_checker_fails_on_the_answers_file_and_on_the_declaration(
        tmp_path):
    proj = _project(tmp_path)
    _declare(proj)
    res = CHECK.evaluate(proj)
    assert res["verdict"] == "FAIL"
    mine = [r for r in res["refusals"] if r["rule"] == RULE]
    assert {r["path"] for r in mine} == {ANSWERS_REL, TD.DECLARATION_REL}
    assert {r["field"] for r in mine} == {
        "answers.synthesis_area_budget.rationale",
        "synthesis_area_budget.rationale"}
    assert CHECK.main([str(proj), "--json", str(proj / "chk.json")]) == 1


@pytest.mark.parametrize("text", [
    "The deliverable is a HARDMACRO, so no slot applies.",
    "`deliverable`: `HARDMACRO` per the earlier reading.",
    "A HARDMACRO deliverable needs no pad ring.",
    "deliverable 為 HARDMACRO。",
])
def test_every_assertion_shape_is_refused(text):
    got = TD.derived_text_contradictions(_answers(rationale=text))
    assert [r["claimed"] for r in got] == ["HARDMACRO"], text


def test_a_derived_structured_field_naming_another_deliverable_is_refused():
    doc = _answers(rationale="No die ceiling is stated.")
    doc["answers"]["synthesis_area_budget"]["basis"] = {
        "deliverable": "HARDMACRO"}
    got = TD.derived_text_contradictions(doc)
    assert [r["field"] for r in got] == [
        "answers.synthesis_area_budget.basis.deliverable"]


# --------------------------------------------------------------------------- #
# 2. a mention is not an assertion; the owner's own fields are the reference
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    OWNER_RATIONALE,
    "Declare answers.deliverable (DIE or HARDMACRO) with its provenance.",
    "Allowed: deliverable = DIE or HARDMACRO.",
    "deliverable_rationale: see the owner ruling on HARDMACRO vs DIE.",
    "The deliverable is DIE; the die area is an outcome of the run.",
])
def test_a_mention_or_a_consistent_claim_is_not_refused(text):
    assert TD.derived_text_contradictions(_answers(rationale=text)) == []


def test_a_list_of_the_choices_asserts_nothing_either_way():
    doc = _answers(deliverable="HARDMACRO",
                   rationale="Allowed: deliverable = DIE or HARDMACRO.")
    assert TD.derived_text_contradictions(doc) == []


def test_an_unattested_deliverable_has_nothing_to_contradict():
    """Silence is not refused here; `owner_attestation_refusals` owns it."""
    assert TD.derived_text_contradictions(_answers(owner=False)) == []


def test_the_provenance_map_and_the_revision_log_are_not_derived_text():
    doc = _answers(rationale="No die ceiling is stated.")
    doc["answer_provenance"]["deliverable"]["citation"] += (
        " It changed deliverable=HARDMACRO to DIE.")
    doc[TD.REVISIONS_KEY] = [{"resolved_contradictions": [
        {"sentence": "declares deliverable=HARDMACRO"}]}]
    assert TD.derived_text_contradictions(doc) == []


# --------------------------------------------------------------------------- #
# 3. the producer regenerates the answer; the regenerated file passes
# --------------------------------------------------------------------------- #
def test_the_producer_replaces_the_stale_premise_with_the_die_basis(tmp_path):
    proj = _project(tmp_path)
    before = _load(proj)
    assert _gen().main([str(proj)]) == 0
    after = _load(proj)
    budget = after["answers"]["synthesis_area_budget"]
    text = budget["rationale"]
    assert STALE_SENTENCE_FRAGMENT not in text
    assert budget["status"] == "NOT_APPLICABLE"
    # the ruling's DIE basis
    assert "The deliverable is DIE" in text
    assert "R-0915-95" in text
    assert "pad-limited die, die = core + pad ring + power ring" in text
    assert "pad-ring perimeter when the pad count dominates" in text
    assert "NOT_APPLICABLE disposes of the die LIMIT only" in text
    # the input's declines and the outcome statement, cited by line
    assert "input/docs/L1_product_metadata.md:7" in text
    assert "input/docs/L9_constraints_floorplan.md:3-4" in text
    assert "input/docs/L1_product_metadata.md:4" in text
    # the std-cell gate still applies and is carried, row and all
    gate = budget["stdcell_area_gate"]
    assert [(g["source"], g["line"], g["value_um2"]) for g in gate] == [
        ("input/docs/L7_verification_plan.md", 15, 1300.0)]
    assert "stdcell area" in gate[0]["row"]
    assert "input/docs/L7_verification_plan.md:15" in text
    assert "1,300 µm²" in text and "1,000 µm²" in text
    # the baseline die-area figure is a measurement, not a gate
    assert "input/docs/L7_verification_plan.md:8 (4,000 µm²)" in text
    # the owner's answer is never rewritten
    assert after["answers"]["deliverable"] == before["answers"]["deliverable"]
    assert (after["answer_provenance"]["deliverable"]
            == before["answer_provenance"]["deliverable"])
    assert (after["answers"]["deliverable_rationale"]
            == before["answers"]["deliverable_rationale"])
    # WHO answered, and why it changed
    prov = after["answer_provenance"]["synthesis_area_budget"]
    assert prov["answered_by"] == "program"
    assert prov["producer"] == "area_budget_basis_gen"
    rev = after[TD.REVISIONS_KEY]
    assert len(rev) == 1
    assert rev[0]["rulings"] == ["R-0929-DELIVERABLE-CONSISTENCY",
                                 "R-0915-95"]
    assert "contradicted the owner-attested deliverable=DIE" in \
        rev[0]["reason"]
    assert [c["claimed"] for c in rev[0]["resolved_contradictions"]] == [
        "HARDMACRO"]
    assert STALE_SENTENCE_FRAGMENT in \
        rev[0]["resolved_contradictions"][0]["sentence"]
    assert TD.derived_text_contradictions(after, source=ANSWERS_REL) == []


def test_the_regenerated_answers_pass_the_checker(tmp_path):
    proj = _project(tmp_path)
    assert _gen().main([str(proj)]) == 0
    _declare(proj)
    res = CHECK.evaluate(proj)
    assert [r for r in res["refusals"] if r["rule"] == RULE] == []
    assert res["verdict"] == "PASS", res["refusals"]
    decl = json.loads((proj / TD.DECLARATION_REL).read_text("utf-8"))
    assert decl["synthesis_area_budget"]["stdcell_area_gate"]
    assert TD.area_budget_resolution(decl)["status"] == "NOT_APPLICABLE"


def test_a_second_run_writes_nothing(tmp_path):
    proj = _project(tmp_path)
    assert _gen().main([str(proj)]) == 0
    first = (proj / ANSWERS_REL).read_bytes()
    assert _gen().main([str(proj)]) == 0
    assert (proj / ANSWERS_REL).read_bytes() == first


def test_out_writes_a_copy_and_leaves_the_source(tmp_path):
    proj = _project(tmp_path)
    src = (proj / ANSWERS_REL).read_bytes()
    out = tmp_path / "regen.json"
    assert _gen().main([str(proj), "--out", str(out)]) == 0
    assert (proj / ANSWERS_REL).read_bytes() == src
    regen = json.loads(out.read_text("utf-8"))
    assert STALE_SENTENCE_FRAGMENT not in \
        regen["answers"]["synthesis_area_budget"]["rationale"]
    assert TD.derived_text_contradictions(regen) == []


# --------------------------------------------------------------------------- #
# 4. the premise follows the attested value
# --------------------------------------------------------------------------- #
def test_a_hardmacro_attestation_keeps_the_hardmacro_meaning(tmp_path):
    proj = _project(tmp_path, answers=_answers(
        deliverable="HARDMACRO",
        rationale="The design declares deliverable=DIE."))
    assert TD.derived_text_contradictions(_load(proj))  # stale the other way
    assert _gen().main([str(proj)]) == 0
    after = _load(proj)
    text = after["answers"]["synthesis_area_budget"]["rationale"]
    assert "The deliverable is HARDMACRO" in text
    assert "takes no operator slot whose geometry could supply a ceiling" in \
        text
    assert "pad ring + power ring" not in text
    assert after["answers"]["synthesis_area_budget"]["stdcell_area_gate"]
    assert TD.derived_text_contradictions(after) == []


@pytest.mark.parametrize("deliverable", ["DIE", "HARDMACRO"])
def test_the_premise_names_exactly_the_attested_deliverable(deliverable):
    gen = _gen()
    text = gen.premise(deliverable, "R-0915-95")
    doc = _answers(deliverable=deliverable, rationale=text)
    assert TD.derived_text_contradictions(doc) == []
    other = "HARDMACRO" if deliverable == "DIE" else "DIE"
    assert TD.derived_text_contradictions(
        _answers(deliverable=other, rationale=text))


# --------------------------------------------------------------------------- #
# 5. refusals write nothing
# --------------------------------------------------------------------------- #
def test_no_owner_attestation_no_premise(tmp_path):
    proj = _project(tmp_path, answers=_answers(owner=False))
    before = (proj / ANSWERS_REL).read_bytes()
    assert _gen().main([str(proj)]) == 1
    assert (proj / ANSWERS_REL).read_bytes() == before


def test_an_input_that_fixes_and_declines_a_die_is_refused(tmp_path):
    l9 = L9 + "\nDIE_AREA = [0, 0, 300, 200] µm\n"
    proj = _project(tmp_path, l9=l9)
    before = (proj / ANSWERS_REL).read_bytes()
    assert _gen().main([str(proj)]) == 1
    assert (proj / ANSWERS_REL).read_bytes() == before


def test_an_input_that_states_nothing_stays_unanswered(tmp_path):
    proj = _project(tmp_path, l9="# L9\n")
    for name in ("L1_product_metadata.md",):
        (proj / "input" / "docs" / name).write_text("# L1\n", "utf-8")
    before = (proj / ANSWERS_REL).read_bytes()
    assert _gen().main([str(proj)]) == 2
    assert (proj / ANSWERS_REL).read_bytes() == before


def test_the_producer_names_no_design():
    """chip-AGNOSTIC, stated where the author sees it: no literal design or
    technology token in the producer's logic."""
    src = (_PROGRAMS / "area_budget_basis_gen.py").read_text("utf-8").lower()
    for token in ("sky130", "gf180", "spm", "subservient", "openmpw"):
        assert token not in src, token
