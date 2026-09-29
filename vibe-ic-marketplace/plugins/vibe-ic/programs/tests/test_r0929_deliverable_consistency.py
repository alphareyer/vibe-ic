#!/usr/bin/env python3
"""R-0929-DELIVERABLE-CONSISTENCY(-2) — derived answers follow the owner,
decided from their recorded STRUCTURE, never from their prose.

WHAT WAS MEASURED. Two designs' `input/step_0_5ic_answers.json` carry the
owner-attested `deliverable = DIE` (R-0915-95) beside a DERIVED
`synthesis_area_budget` rationale, written while the deliverable was still an
agent's HARDMACRO, that says "declares deliverable=HARDMACRO". Nothing
produced that text, so nothing re-derived it when the owner ruled, and no check
compared it with the answer it depended on.

The first gate read that prose for "deliverable=X" and review_wave58 (DELIVC)
measured it wrong both ways: owner contrast wording FAILed and real claims
("hard macro", "交付物為 HARDMACRO") PASSed. R-0929-DELIVERABLE-CONSISTENCY-2
replaces it: every derived answer carries `answer_provenance.<key>` =
{answered_by: program, producer, derived_from_attested: {deliverable: <v>},
inputs, inputs_sha256}; the gate FAILs one with no such record, or whose
recorded attested value differs from the owner's current one; it never reads
prose and never gates an owner-attested field.

EVERY PROPERTY BELOW IS ASSERTED IN THE DIRECTION THAT COULD FAIL.

  1. GATE, NO PROVENANCE  the old hand-written answer FAILs, at the answers
                      file and at the declaration generated from it.
  2. GATE, STALE      a recorded deliverable that differs from the owner's
                      FAILs, naming both values; so do a lost attestation and
                      changed inputs.
  3. PROSE IS NOT READ  the same sentences (contrast wording and real claims
                      alike) PASS under current provenance and FAIL under stale
                      provenance: the verdict is the structure's.
  4. OWNER FIELDS     deliverable_rationale (any wording) and an
                      owner-attested derived-key answer are never gated.
  5. PRODUCER         regenerates with provenance, keeps the owner's fields
                      byte-identical, passes the checker, second run is a no-op;
                      a FIXED die gives LIMIT with the rectangle and no outcome
                      premise, for both deliverables.
  6. REFUSALS         no owner attestation / fixes-and-declines write nothing.

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
NO_PROV = "DERIVED_ANSWER_WITHOUT_PROVENANCE"
STALE_RULE = "DERIVED_ANSWER_STALE"
FIELD = "answers.synthesis_area_budget"
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


def _derived_rules(res_or_list):
    rows = res_or_list["refusals"] if isinstance(res_or_list, dict) \
        else res_or_list
    return [r for r in rows if r["rule"] in (NO_PROV, STALE_RULE)]


def _regenerated(tmp_path, **kw):
    proj = _project(tmp_path, **kw)
    assert _gen().main([str(proj)]) == 0
    return proj, _load(proj)


# --------------------------------------------------------------------------- #
# 1. an answer with no producer provenance is refused, at source and at copy
# --------------------------------------------------------------------------- #
def test_the_old_hand_written_answer_has_no_provenance_and_is_refused():
    got = TD.derived_answer_refusals(_answers(), source=ANSWERS_REL)
    assert [(r["rule"], r["field"]) for r in got] == [(NO_PROV, FIELD)], got
    r = got[0]
    assert r["producer"] == "area_budget_basis_gen"
    for needle in (ANSWERS_REL, FIELD, "area_budget_basis_gen",
                   "R-0929-DELIVERABLE-CONSISTENCY-2",
                   "no `answer_provenance.synthesis_area_budget` record"):
        assert needle in r["message"], needle


def test_the_checker_fails_on_the_answers_file_and_on_the_declaration(
        tmp_path):
    proj = _project(tmp_path)
    _declare(proj)
    res = CHECK.evaluate(proj)
    assert res["verdict"] == "FAIL"
    mine = _derived_rules(res)
    assert {(r["path"], r["rule"]) for r in mine} == {
        (ANSWERS_REL, NO_PROV), (TD.DECLARATION_REL, NO_PROV)}
    assert CHECK.main([str(proj), "--json", str(proj / "chk.json")]) == 1


@pytest.mark.parametrize("breakage,needle", [
    (lambda rec: rec.update(answered_by="agent"), "answered_by is 'agent'"),
    (lambda rec: rec.update(producer="somebody_else"), "producer is"),
    (lambda rec: rec.pop("derived_from_attested"), "no `derived_from_attested`"),
    (lambda rec: rec.update(derived_from_attested={"top_cell": "widget"}),
     "omits `deliverable`"),
    (lambda rec: rec.pop("inputs"), "no `inputs` list"),
    (lambda rec: rec.update(inputs_sha256="abc"), "no `inputs_sha256`"),
])
def test_each_missing_part_of_the_provenance_is_named(tmp_path, breakage,
                                                        needle):
    proj, doc = _regenerated(tmp_path)
    assert TD.derived_answer_refusals(doc, proj) == []
    breakage(doc["answer_provenance"]["synthesis_area_budget"])
    got = TD.derived_answer_refusals(doc, proj)
    assert [r["rule"] for r in got] == [NO_PROV], got
    assert needle in got[0]["message"]


# --------------------------------------------------------------------------- #
# 2. stale: the recorded attested value is not the owner's current one
# --------------------------------------------------------------------------- #
def test_a_recorded_deliverable_the_owner_no_longer_attests_is_stale(
        tmp_path):
    proj, doc = _regenerated(tmp_path)
    doc["answer_provenance"]["synthesis_area_budget"][
        "derived_from_attested"]["deliverable"] = "HARDMACRO"
    got = TD.derived_answer_refusals(doc, proj, source=ANSWERS_REL)
    assert [(r["rule"], r["depends_on"], r["recorded"], r["attested"])
            for r in got] == [(STALE_RULE, "deliverable", "HARDMACRO", "DIE")]
    assert "'HARDMACRO'" in got[0]["message"]
    assert "'DIE'" in got[0]["message"]


def test_an_owner_answer_that_changed_after_derivation_is_stale(tmp_path):
    """The measured case, in the direction the owner moved: derived under one
    attested value, the owner then attests the other."""
    proj, doc = _regenerated(tmp_path, answers=_answers(
        deliverable="HARDMACRO", rationale="stale"))
    doc["answers"]["deliverable"] = "DIE"
    got = TD.derived_answer_refusals(doc, proj)
    assert [(r["rule"], r["recorded"], r["attested"]) for r in got] == [
        (STALE_RULE, "HARDMACRO", "DIE")]


def test_a_lost_owner_attestation_makes_the_derived_answer_stale(tmp_path):
    proj, doc = _regenerated(tmp_path)
    doc["answer_provenance"]["deliverable"]["answered_by"] = "agent"
    got = TD.derived_answer_refusals(doc, proj)
    assert [r["rule"] for r in got] == [STALE_RULE]
    assert got[0]["attested"] is None


def test_changed_inputs_make_the_derived_answer_stale(tmp_path):
    proj, doc = _regenerated(tmp_path)
    l7 = proj / "input" / "docs" / "L7_verification_plan.md"
    l7.write_text(l7.read_text("utf-8").replace("1,300", "1,400"), "utf-8")
    got = TD.derived_answer_refusals(doc, proj)
    assert [r["rule"] for r in got] == [STALE_RULE]
    assert "have changed since" in got[0]["message"]
    # without a project the digest cannot be recomputed; the rest still holds
    assert TD.derived_answer_refusals(doc) == []


# --------------------------------------------------------------------------- #
# 3. the verdict is the structure's; no sentence is read
# --------------------------------------------------------------------------- #
PROSE = [
    # review_wave58 false FAILs of the prose gate (contrast wording)
    "The owner chose the IC path (deliverable=DIE), not the IP path "
    "(deliverable=HARDMACRO).",
    "The IC path is taken instead of a HARDMACRO deliverable.",
    "An earlier agent reading said deliverable=HARDMACRO; R-0915-95 overruled it.",
    "If deliverable=HARDMACRO the pad fields are not applicable; here it is DIE.",
    "Unlike a HARDMACRO deliverable, a DIE carries its own pad ring.",
    # review_wave58 false PASSes of the prose gate (real claims)
    "The design declares HARDMACRO as the deliverable.",
    "The deliverable is a hard macro.",
    "The design walks the IP path, so it takes no operator slot.",
    "本設計交付物為 HARDMACRO。",
    "delivered as a HARDMACRO",
]


@pytest.mark.parametrize("text", PROSE)
def test_prose_decides_nothing(tmp_path, text):
    proj, doc = _regenerated(tmp_path)
    doc["answers"]["synthesis_area_budget"]["rationale"] = text
    # current provenance: PASS whatever the sentence says ...
    assert TD.derived_answer_refusals(doc) == [], text
    # ... stale provenance: FAIL whatever the sentence says
    doc["answer_provenance"]["synthesis_area_budget"][
        "derived_from_attested"]["deliverable"] = "HARDMACRO"
    assert [r["rule"] for r in TD.derived_answer_refusals(doc)] == [
        STALE_RULE], text


# --------------------------------------------------------------------------- #
# 4. owner-attested fields are never gated as derived text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("owner_text", [
    OWNER_RATIONALE,
    "DIE. R-0915-95: the agent's reading (deliverable=HARDMACRO) is "
    "overruled; the IC path is taken.",
    "DIE — the IC path, not a HARDMACRO deliverable.",
    "IC path (DIE deliverable) vs IP path (HARDMACRO deliverable): the owner "
    "chose the IC path.",
])
def test_the_owners_deliverable_rationale_is_never_gated(tmp_path,
                                                          owner_text):
    proj, doc = _regenerated(tmp_path)
    doc["answers"]["deliverable_rationale"] = owner_text
    assert TD.derived_answer_refusals(doc, proj) == []
    del doc["answers"]["synthesis_area_budget"]
    assert TD.derived_answer_refusals(doc, proj) == []


def test_an_owner_attested_area_answer_is_not_derived():
    doc = _answers()
    doc["answer_provenance"]["synthesis_area_budget"] = {
        "answered_by": "owner", "citation": "owner ruling R-0000-X"}
    assert TD.derived_answer_refusals(doc) == []


def test_an_unanswered_derived_key_is_not_gated():
    doc = _answers()
    doc["answers"]["synthesis_area_budget"] = "NOT_DETERMINED"
    assert TD.derived_answer_refusals(doc) == []


# --------------------------------------------------------------------------- #
# 5. the producer regenerates with provenance; the result passes
# --------------------------------------------------------------------------- #
def _owner_fields(doc):
    return (json.dumps(doc["answers"]["deliverable"]),
            json.dumps(doc["answers"]["deliverable_rationale"],
                       ensure_ascii=False),
            json.dumps(doc["answer_provenance"]["deliverable"],
                       ensure_ascii=False, sort_keys=True))


def test_the_producer_writes_the_die_basis_with_provenance(tmp_path):
    proj = _project(tmp_path)
    before = _load(proj)
    assert _gen().main([str(proj)]) == 0
    after = _load(proj)
    budget = after["answers"]["synthesis_area_budget"]
    text = budget["rationale"]
    assert STALE_SENTENCE_FRAGMENT not in text
    assert budget["status"] == "NOT_APPLICABLE"
    assert "The deliverable is DIE" in text and "R-0915-95" in text
    assert "pad-limited die, die = core + pad ring + power ring" in text
    assert "pad-ring perimeter when the pad count dominates" in text
    assert "NOT_APPLICABLE disposes of the die LIMIT only" in text
    assert "input/docs/L1_product_metadata.md:7" in text
    assert "input/docs/L9_constraints_floorplan.md:3-4" in text
    assert "input/docs/L1_product_metadata.md:4" in text
    gate = budget["stdcell_area_gate"]
    assert [(g["source"], g["line"], g["value_um2"]) for g in gate] == [
        ("input/docs/L7_verification_plan.md", 15, 1300.0)]
    assert "input/docs/L7_verification_plan.md:8 (4,000 µm²)" in text
    # the owner's fields: untouched
    assert _owner_fields(after) == _owner_fields(before)
    # the structure the gate reads
    prov = after["answer_provenance"]["synthesis_area_budget"]
    assert prov["answered_by"] == "program"
    assert prov["producer"] == "area_budget_basis_gen"
    assert prov["derived_from_attested"] == {"deliverable": "DIE"}
    assert prov["inputs"] == sorted(
        f"input/docs/{n}" for n in ("L1_product_metadata.md",
                                    "L7_verification_plan.md",
                                    "L9_constraints_floorplan.md"))
    assert prov["inputs_sha256"] == TD.derived_inputs_sha256(
        proj, prov["inputs"])
    rev = after[TD.REVISIONS_KEY]
    assert len(rev) == 1
    assert rev[0]["rulings"] == ["R-0929-DELIVERABLE-CONSISTENCY",
                                 "R-0929-DELIVERABLE-CONSISTENCY-2",
                                 "R-0915-95"]
    assert "carried no producer provenance" in rev[0]["reason"]
    assert rev[0]["previous"]["provenance"] is None
    assert TD.derived_answer_refusals(after, proj, source=ANSWERS_REL) == []


def test_the_regenerated_answers_pass_the_checker(tmp_path):
    proj, _ = _regenerated(tmp_path)
    _declare(proj)
    res = CHECK.evaluate(proj)
    assert _derived_rules(res) == []
    assert res["verdict"] == "PASS", res["refusals"]
    decl = json.loads((proj / TD.DECLARATION_REL).read_text("utf-8"))
    assert TD.area_budget_resolution(decl)["status"] == "NOT_APPLICABLE"


def test_a_second_run_writes_nothing(tmp_path):
    proj, _ = _regenerated(tmp_path)
    first = (proj / ANSWERS_REL).read_bytes()
    assert _gen().main([str(proj)]) == 0
    assert (proj / ANSWERS_REL).read_bytes() == first


def test_a_stale_record_is_regenerated_with_its_reason(tmp_path):
    proj, doc = _regenerated(tmp_path)
    doc["answer_provenance"]["synthesis_area_budget"][
        "derived_from_attested"]["deliverable"] = "HARDMACRO"
    (proj / ANSWERS_REL).write_text(json.dumps(doc, ensure_ascii=False),
                                    "utf-8")
    assert _gen().main([str(proj)]) == 0
    after = _load(proj)
    assert "rendered from deliverable='HARDMACRO'" in \
        after[TD.REVISIONS_KEY][-1]["reason"]
    assert TD.derived_answer_refusals(after, proj) == []


def test_out_writes_a_copy_and_leaves_the_source(tmp_path):
    proj = _project(tmp_path)
    src = (proj / ANSWERS_REL).read_bytes()
    out = tmp_path / "regen.json"
    assert _gen().main([str(proj), "--out", str(out)]) == 0
    assert (proj / ANSWERS_REL).read_bytes() == src
    regen = json.loads(out.read_text("utf-8"))
    assert TD.derived_answer_refusals(regen, proj) == []


def test_a_hardmacro_attestation_keeps_the_hardmacro_meaning(tmp_path):
    proj, after = _regenerated(tmp_path, answers=_answers(
        deliverable="HARDMACRO", rationale="stale"))
    text = after["answers"]["synthesis_area_budget"]["rationale"]
    assert "The deliverable is HARDMACRO" in text
    assert "takes no operator slot whose geometry could supply a ceiling" in \
        text
    assert "pad ring + power ring" not in text
    assert after["answer_provenance"]["synthesis_area_budget"][
        "derived_from_attested"] == {"deliverable": "HARDMACRO"}
    assert TD.derived_answer_refusals(after, proj) == []


L9_FIXED = """# L9 Constraints

### 9.2.3 Floorplan
| Item | Value |
|---|---|
| Core die (no seal ring) | 300 × 200 µm |
"""


@pytest.mark.parametrize("deliverable", ["DIE", "HARDMACRO"])
def test_a_fixed_die_is_a_limit_and_never_an_outcome(tmp_path, deliverable):
    """R-0929-DELIVERABLE-CONSISTENCY-2 / review_wave58 MAJOR 4: an input that
    FIXES the die (and declines nothing) gives LIMIT with that rectangle, and
    the rationale carries no outcome / pad-limited premise."""
    proj = _project(tmp_path, answers=_answers(deliverable=deliverable,
                                               rationale="old"), l9=L9_FIXED)
    for name in ("L1_product_metadata.md",):
        (proj / "input" / "docs" / name).write_text("# L1\n", "utf-8")
    assert _gen().main([str(proj)]) == 0
    budget = _load(proj)["answers"]["synthesis_area_budget"]
    assert budget["status"] == "LIMIT"
    assert budget["max_die_dimensions_um"] == [300.0, 200.0]
    text = budget["rationale"]
    assert "300x200" in text
    for forbidden in ("OUTCOME", "outcome", "pad-limited",
                      "sized by the pad-ring perimeter", "NOT_APPLICABLE"):
        assert forbidden not in text, forbidden
    assert f"The deliverable is {deliverable}" in text
    assert TD.derived_answer_refusals(_load(proj), proj) == []


@pytest.mark.parametrize("deliverable", ["DIE", "HARDMACRO"])
def test_the_premise_follows_the_disposition(deliverable):
    gen = _gen()
    declined = gen.premise(deliverable, "R-0915-95")
    fixed = gen.premise(deliverable, "R-0915-95", "300x200")
    assert f"The deliverable is {deliverable}" in declined
    assert "outcome" in declined.lower()
    assert "outcome" not in fixed.lower() and "300x200" in fixed


# --------------------------------------------------------------------------- #
# 6. refusals write nothing
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


def test_the_gate_reads_no_prose():
    """The structural rule, stated where the author sees it: the gate's source
    carries no regular expression over a choice value and no sentence split."""
    import inspect
    src = inspect.getsource(TD.derived_answer_refusals)
    for token in ("sentence_scope", "finditer", "HARDMACRO", "rationale"):
        assert token not in src, token


def test_the_producer_names_no_design():
    """chip-AGNOSTIC: no literal design or technology token in the producer."""
    src = (_PROGRAMS / "area_budget_basis_gen.py").read_text("utf-8").lower()
    for token in ("sky130", "gf180", "spm", "subservient", "openmpw"):
        assert token not in src, token
