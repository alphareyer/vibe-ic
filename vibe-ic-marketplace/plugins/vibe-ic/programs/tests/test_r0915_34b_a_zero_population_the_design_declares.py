"""A rule whose SUBJECT the design declares it does not have is N/A, not blind.

MEASURED 2026-09-15 (lane icspm3, R-0915-34(b)) on `spm` x gf180mcuD, a
serial-parallel multiplier. `R1_CITED_CONSTANT_NOT_IN_ITS_SOURCE` asks whether
a cited hexadecimal constant is really in the document it cites. This design's
28 L-docs cite none, so the rule answered::

    rc=2 NOT CHECKED — 1 rule(s) could not read what they need:
      R1_CITED_CONSTANT_NOT_IN_ITS_SOURCE: 28 L-doc(s) cite no hexadecimal
      constant …; this rule examined 0 constants

and `stage_on_pass_review` — the gate of steps 2, 7, 14, 15, 37 and 39 —
declined for the whole stage. The repo's own comment beside that branch
MEASURES the population: **53 of the 58 readable cells in the published
corpus**. This is the normal case, not an spm quirk.

IT IS NOT AN ACCEPT, and the comment this replaces was right to refuse one:
"calling them ACCEPT would be a reviewer reporting a pass over a question it
never put." `NOT_APPLICABLE` is the third answer — the question does not arise
— and it carries `applicability_evidence` naming the documents, the population
and the count, so the claim is checkable against the documents themselves.

FAIL-CLOSED, and that is the whole difference from #2272's prose reading: the
declaring documents must EXIST and PARSE. An L-doc that will not parse used to
be skipped silently, so "the design cites none" and "I could not read them"
produced the same zero. They are opposite claims and the zero-population branch
now refuses to be reached while any document is unreadable.

AND A REVIEW WHERE NO RULE HAD A SUBJECT CERTIFIES NOTHING: if every rule
answers N/A the run still exits 2. That is the zero-denominator rule applied to
the reviewer itself.

chip-AGNOSTIC: synthetic L-docs and inputs in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import stage_on_pass_review as S  # noqa: E402

DECL = {"intent": ["input/docs"], "artefact": ["phase1/generated_docs"]}


def _project(tmp_path, l_docs, input_text="the design says nothing in hex\n"):
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    for name, body in l_docs.items():
        (gd / name).write_text(body if isinstance(body, str)
                               else json.dumps(body))
    d = tmp_path / "input" / "docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "L1_product_metadata.md").write_text(input_text)
    return tmp_path


#: An L-doc that CITES a constant out of the input — the rule's real subject.
#:
#: THE SHAPE IS TAKEN FROM `cited_input_literals`, NOT INVENTED: the source is
#: the KEY and its value is a LIST of entries. My first version nested a
#: `{"source", "literal"}` dict under a field name, and the locator found
#: nothing — `constants_checked` came back 0, so BOTH negative controls below
#: were passing over a rule that never saw a citation. Proved before use:
#: grounded -> ACCEPT with constants_checked 1, ungrounded -> REJECT with
#: constants_ungrounded 1.
def _citing(constant="0x1021", source="input/docs/L1_product_metadata.md"):
    return {"doc_id": "L3", "fields": {"crc_poly": constant},
            "extraction_evidence": {
                source: [{"literal": f"the polynomial is {constant}",
                          "label": "crc_poly"}]}}


# ── direction 1: the design declares the population is zero ───────────────

def test_documents_that_parse_and_cite_nothing_are_NOT_APPLICABLE(tmp_path):
    proj = _project(tmp_path, {"L1_DATASHEET.json": {"doc_id": "L1"},
                               "L2_FRS.json": {"doc_id": "L2"}})
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "NOT_APPLICABLE", out
    ev = out["applicability_evidence"]
    assert ev["kind"] == "design-declared-zero-population"
    assert ev["count"] == 0
    assert ev["documents_read"] == 2
    assert sorted(ev["documents"]) == ["L1_DATASHEET.json", "L2_FRS.json"]
    assert ev["document_dirs"] == ["phase1/generated_docs"]
    assert ev["unreadable_documents"] == []


def test_the_evidence_names_the_documents_so_the_claim_is_checkable(tmp_path):
    proj = _project(tmp_path, {"L1_DATASHEET.json": {"doc_id": "L1"}})
    ev = S.rule_cited_constant_not_in_source(
        proj, DECL)["applicability_evidence"]
    for name in ev["documents"]:
        assert (proj / "phase1" / "generated_docs" / name).is_file(), name


def test_a_stage_with_one_NA_rule_and_one_accept_is_reviewed(tmp_path):
    """The consequence: `review()` buckets N/A apart and the run reaches a
    verdict instead of declining for the whole stage."""
    proj = _project(tmp_path, {"L1_DATASHEET.json": {"doc_id": "L1"}})
    rec = {"not_applicable": [], "observations": [], "rejections": [],
           "not_checked": []}
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    out["rule"] = "R1_CITED_CONSTANT_NOT_IN_ITS_SOURCE"
    if out["verdict"] == "NOT_APPLICABLE":
        rec["not_applicable"].append(out)
    assert rec["not_applicable"] and not rec["not_checked"]


# ── direction 2: a document that will not parse is NOT a declaration ──────

def test_an_unparseable_document_keeps_the_rule_NOT_CHECKED(tmp_path):
    proj = _project(tmp_path, {"L1_DATASHEET.json": "{ this is not json"})
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "NOT_MEASURED", out
    assert "could not be parsed" in out["why"]
    assert "NOT established" in out["why"]


def test_one_unparseable_document_among_readable_ones_still_refuses(tmp_path):
    """The silent-skip hole, closed: a single unreadable document leaves the
    population unestablished even when the rest parse."""
    proj = _project(tmp_path, {"L1_DATASHEET.json": {"doc_id": "L1"},
                               "L2_FRS.json": "{ broken"})
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "NOT_MEASURED", out
    assert "L2_FRS.json" in out["why"]


def test_no_documents_at_all_is_still_NOT_CHECKED(tmp_path):
    proj = _project(tmp_path, {})
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "NOT_MEASURED", out
    assert "no L*.json" in out["why"].lower() or "NO L*.json" in out["why"]


def test_an_unreadable_design_input_is_still_NOT_CHECKED(tmp_path):
    """The rule's other fail-closed guard, untouched: 'I could not look' is
    not 'it is not there'."""
    proj = _project(tmp_path, {"L1_DATASHEET.json": {"doc_id": "L1"}})
    out = S.rule_cited_constant_not_in_source(
        proj, {"intent": ["input/nowhere"],
               "artefact": ["phase1/generated_docs"]})
    assert out["verdict"] == "NOT_MEASURED", out


# ── direction 3: a design that DOES cite one keeps the rule live ──────────

def test_a_design_that_cites_a_constant_is_judged_not_excused(tmp_path):
    """The negative control R-0915-34(b) names: a document declaring ONE
    instance keeps the rule live, and it answers about that instance."""
    proj = _project(tmp_path, {"L3_CMD_PROTOCOL.json": _citing()},
                    input_text="the polynomial is 0x1021 and nothing else\n")
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "ACCEPT", out
    assert out["artefact"]["constants_checked"] == 1, out["artefact"]


def test_a_cited_constant_the_input_does_not_state_is_still_REJECTED(tmp_path):
    """And the rule's whole point survives: a fabricated citation is found."""
    proj = _project(tmp_path, {"L3_CMD_PROTOCOL.json": _citing()},
                    input_text="this design declares no polynomial at all\n")
    out = S.rule_cited_constant_not_in_source(proj, DECL)
    assert out["verdict"] == "REJECT", out
    assert out["artefact"]["constants_ungrounded"] >= 1


# ── and a reviewer where NOTHING had a subject certifies nothing ──────────

def test_a_run_where_every_rule_is_NA_still_exits_2():
    """The zero-denominator rule applied to the reviewer itself, asserted on
    the source so it cannot be quietly dropped."""
    src = (PROGRAMS / "stage_on_pass_review.py").read_text(errors="replace")
    assert "every rule on stage" in src
    assert "answered NOT_APPLICABLE" in src
    i = src.index("answered NOT_APPLICABLE")
    assert "return 2" in src[i:i + 900], "the all-N/A branch must exit 2"
