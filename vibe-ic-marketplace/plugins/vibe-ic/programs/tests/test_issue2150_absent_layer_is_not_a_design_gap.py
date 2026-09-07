#!/usr/bin/env python3
"""test_issue2150_absent_layer_is_not_a_design_gap.py

An expectation naming a layer THE TAXONOMY DOES NOT DECLARE is a fact about
the expectation, never about the design — and a DECLARED layer the root does
not carry stays the disagreement #312 made it (vibe-ic#2150).

THE READING THAT WAS TRIED FIRST, AND IS WRONG
----------------------------------------------
`converge_ai_expectation` returns early when `resolve_layer_file` finds no such
layer, and `evaluate` then falls through every clause to the last one, which is
`about: "design"`. Measured on the surviving opentitan_aes Phase-1 root (24
files / 23 codes), an expectation naming `L24_SIGNOFF` — which that root does
not carry — is published as

    about="design"   EXPERT_TRACK_AI_EXPECTATION_UNMET

and the first repair drafted here refused every such row as a track finding.
That repair is wrong, and its own sibling tests said so:
`test_issue312_ai_subtrack_convergence::test_a_layer_the_program_track_never_
wrote_is_a_disagreement` pins that case as a DISAGREEMENT on the stated grounds
that "silence here would be the original defect exactly". It is right.
`l_doc_taxonomy` declares 28 layers and `L24_SIGNOFF` is one of them, so a root
without it is the PROGRAM TRACK not emitting a document its own contract
applies — a real disagreement, and not this landing's business.

WHAT IS ACTUALLY WRONG
----------------------
A layer the taxonomy does NOT declare. Then nothing was pointed at: no Phase-1
root of any design carries such a document and no extractor can produce one, so
recording it as a design gap says the tree lacks a fact whose home the tree has
never had. That is #2150 F11's shape — a decision table computed over a
DIFFERENT Phase-1 root, whose OWNER cells name leaves the judged artefact
lacks; such a table does not disagree with this root, it answers a different
question, silently.

WHAT THIS FILE PINS
-------------------
  * the refusal, BY NAME and `about: "track"`, for an UNDECLARED layer only;
  * that a DECLARED layer absent from the root keeps #312's verdict exactly —
    same rule, same `about`, same sentence — so this landing cannot be read as
    having quietly reclassified real disagreements;
  * that it did NOT swallow real design findings: a miss in a layer that IS
    present is still `about: "design"`, in the same run;
  * `owning_layers` is computed for an absent layer either way, so a fact that
    lives wholly in a layer the root HAS is a re-scope with a named
    destination rather than an unanswerable miss;
  * `layer_present` and `layer_in_taxonomy` are THREE-state facts (True /
    False / NOT_MEASURED) — an expectation refused before the root was
    consulted was never checked against one, and recording that as False would
    report a lookup that did not happen;
  * `phase1_root_identity` identifies the root by MEMBERSHIP and by CONTENT,
    kept separable, names the DECLARED layers it lacks, and reports
    NOT_MEASURED rather than presenting an empty root as one that was read.

Every test is PAIRED: each refusal has an acceptance beside it, so a check that
always fired would fail as loudly as one that never fired.

Every fixture is synthesised here from neutral parts. No design, PDK, vendor or
IP-model identifier appears anywhere in this file.

Run: python3 -m pytest programs/tests/test_issue2150_absent_layer_is_not_a_design_gap.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase1_expert_parse_track as T   # noqa: E402
import _path_layout as _pl              # noqa: E402
import _progress_run as _pr             # noqa: E402


_INPUT_DOC = """# Block specification

The block exposes a control surface of four addressable words. Word zero is
read-only and reports the busy flag; word three latches the trim code.
"""

#: A layer the fixture root DOES contain, carrying the whole fact.
_OWNING_LAYER = {
    "doc_id": "L4",
    "records": [
        {"name": "STATUS_WORD", "offset": "0x0", "access": "read-only"},
        {"name": "TRIM_WORD", "offset": "0xc", "access": "read-write"},
    ],
}

#: A layer the fixture root DOES contain, carrying none of the tokens.
_PRESENT_BUT_SILENT_LAYER = {"doc_id": "L9",
                             "fields": {"top_ports": ["clk", "rst_n"]}}


def _project(tmp_path, name="proj", layers=None):
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    (p / "phase1" / "generated_docs" / "L1_DATASHEET.json").write_text(
        json.dumps({"doc_id": "L1", "fields": {"word_count": 4}}))
    for stem, blob in (layers or {}).items():
        (p / "phase1" / "generated_docs" / f"{stem}.json").write_text(
            json.dumps(blob))
    return p


def _root(tmp_path, name="proj"):
    """A root with L1, L9 and L4 — and deliberately NO L24."""
    return _project(tmp_path, name, layers={
        "L9_INTEGRATION_SPEC": _PRESENT_BUT_SILENT_LAYER,
        "L4_REGMAP": _OWNING_LAYER,
    })


def _pack_dir(project: Path) -> Path:
    return _pl.report_path(project, "phase1/expert_parse_track").parent \
        / "expert_parse_track_pack"


def _answer(project: Path, expectations):
    d = _pack_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / "l_doc_expectations.json").write_text(
        json.dumps({"expectations": expectations}))


def _run_track(project: Path):
    env = dict(os.environ)
    env["VIBE_IC_DISABLE_LLM_CONFIRM"] = "1"
    cp = _pr.run(
        [sys.executable, str(_PROGRAMS / "phase1_expert_parse_track.py"),
         str(project)], capture_output=True, text=True, env=env)
    return cp.returncode, cp.stdout, cp.stderr


def _report(project: Path):
    return json.loads(
        _pl.report_path(project, "phase1/expert_parse_track.json").read_text())


def _rules(rep, prefix):
    """Findings under exactly this rule id, anchored on `::`."""
    return sorted(f["rule"] for f in rep["findings"]
                  if f["rule"] == prefix or f["rule"].startswith(prefix + "::"))


#: A layer name the L-doc taxonomy does not declare. Neutral and obviously
#: not a real layer, so nobody reads it as an omission from the roster.
_UNDECLARED_LAYER = "L91_NOT_A_DECLARED_LAYER"

#: A layer the taxonomy DOES declare and this fixture root does not carry.
_DECLARED_BUT_ABSENT = "L21_POWER_INTENT"


#: The new rule id, read so the PRE-FIX tree can run the tests that bound this
#: landing. A control arm that dies on `AttributeError` observed nothing: it
#: has to reach the assertion and answer it WRONGLY (or, for a bounding
#: control, answer it RIGHTLY on both arms) for the arm to mean anything.
_LAYER_ABSENT = getattr(T, "RULE_AI_LAYER_ABSENT",
                        "EXPERT_TRACK_AI_EXPECTATION_LAYER_ABSENT")


def _exp(eid, layer, field_path, tokens):
    return {"id": eid, "layer": layer, "field_path": field_path,
            "requirement": "the layer states the control-surface offsets",
            "evidence": ["input spec: four addressable words"],
            "expected_tokens": tokens}


# ── the refusal ─────────────────────────────────────────────────────────────

def test_an_undeclared_layer_is_refused_by_name_as_a_track_finding(tmp_path):
    """RED ARM. A layer no taxonomy entry declares is not a design gap."""
    p = _root(tmp_path)
    _answer(p, [_exp("e-undeclared", _UNDECLARED_LAYER, "signoff.status",
                     ["NOT_A_TOKEN_ANY_LAYER_CARRIES"])])
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, T.RULE_AI_LAYER_ABSENT) == [
        f"{T.RULE_AI_LAYER_ABSENT}::e-undeclared"], rep["findings"]
    f = [x for x in rep["findings"]
         if x["rule"].startswith(T.RULE_AI_LAYER_ABSENT)][0]
    assert f["about"] == "track"
    assert f["layer"] == _UNDECLARED_LAYER
    # It names what the root DOES carry, so the reader can re-point the one
    # expectation without opening the root by hand.
    assert "L4_REGMAP" in f["message"] and "L9_INTEGRATION_SPEC" in f["message"]


def test_the_undeclared_layer_row_is_not_also_a_design_gap(tmp_path):
    """The whole point: it must not ALSO appear as a design finding."""
    p = _root(tmp_path)
    _answer(p, [_exp("e-undeclared", _UNDECLARED_LAYER, "signoff.status",
                     ["NOT_A_TOKEN_ANY_LAYER_CARRIES"])])
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, T.RULE_AI_UNMET) == [], rep["findings"]
    design = [x for x in rep["findings"] if x.get("about") == "design"]
    assert design == [], design


def test_a_declared_layer_absent_from_the_root_keeps_312s_verdict(tmp_path):
    """THE CONTROL THAT BOUNDS THIS LANDING.

    `l_doc_taxonomy` declares L21_POWER_INTENT and this root does not carry
    it. #312 pinned that as a disagreement because silence would be the
    original defect. It must still be one, `about: "design"`, under the same
    rule, with the same sentence — this landing reclassifies UNDECLARED
    layers and nothing else."""
    p = _root(tmp_path)
    _answer(p, [_exp("e-declared-absent", _DECLARED_BUT_ABSENT, "rails",
                     ["NOT_A_TOKEN_ANY_LAYER_CARRIES"])])
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, _LAYER_ABSENT) == [], rep["findings"]
    assert _rules(rep, T.RULE_AI_UNMET) == [
        f"{T.RULE_AI_UNMET}::e-declared-absent"], rep["findings"]
    f = [x for x in rep["findings"] if x["rule"].startswith(T.RULE_AI_UNMET)][0]
    assert f["about"] == "design"
    assert f"no {_DECLARED_BUT_ABSENT} layer at all" in f["message"]


def test_the_taxonomy_answer_is_recorded_for_both_kinds(tmp_path):
    """The two cases are told apart by a RECORDED fact, not by the message."""
    p = _root(tmp_path)
    undeclared = T.converge_ai_expectation(
        p, _exp("u", _UNDECLARED_LAYER, "x", ["t"]))
    declared = T.converge_ai_expectation(
        p, _exp("d", _DECLARED_BUT_ABSENT, "x", ["t"]))
    present = T.converge_ai_expectation(
        p, _exp("p", "L9_INTEGRATION_SPEC", "fields.top_ports", ["clk"]))
    assert undeclared["layer_in_taxonomy"] is False
    assert declared["layer_in_taxonomy"] is True
    # Never consulted when the question did not arise.
    assert present["layer_in_taxonomy"] == "NOT_MEASURED"


def test_a_present_layer_that_misses_is_still_a_design_finding(tmp_path):
    """GREEN ARM / negative control. The refusal did not swallow real gaps.

    Same run, same fixture: one row addresses a layer the root HAS and the
    fact is in NO layer. That is a genuine disagreement about the design and
    it must survive this landing unchanged."""
    p = _root(tmp_path)
    _answer(p, [
        _exp("e-absent", _UNDECLARED_LAYER, "signoff.status",
             ["NOWHERE_TOKEN_A"]),
        _exp("e-present", "L9_INTEGRATION_SPEC", "fields.top_ports",
             ["NOWHERE_TOKEN_B"]),
    ])
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, _LAYER_ABSENT) == [
        f"{_LAYER_ABSENT}::e-absent"]
    assert _rules(rep, T.RULE_AI_UNMET) == [
        f"{T.RULE_AI_UNMET}::e-present"], rep["findings"]


def test_an_absent_layer_still_names_the_layer_that_owns_the_fact(tmp_path):
    """A re-scope with a named destination, not an unanswerable miss."""
    p = _root(tmp_path)
    _answer(p, [_exp("e-rescope", _UNDECLARED_LAYER, "signoff.registers",
                     ["STATUS_WORD", "TRIM_WORD"])])
    _run_track(p)
    rep = _report(p)
    f = [x for x in rep["findings"]
         if x["rule"].startswith(T.RULE_AI_LAYER_ABSENT)][0]
    assert f["owning_layers"] == ["L4_REGMAP"], f
    assert "L4_REGMAP" in f["message"]


def test_a_missing_layer_does_not_become_a_misscope_finding(tmp_path):
    """MEMBERSHIP. The row is refused ONCE, under its own rule.

    An absent layer whose fact another layer owns must not be filed under both
    LAYER_ABSENT and MISSCOPED: two findings for one row is a count nobody can
    reconcile with the corpus."""
    p = _root(tmp_path)
    _answer(p, [_exp("e-rescope", _UNDECLARED_LAYER, "signoff.registers",
                     ["STATUS_WORD", "TRIM_WORD"])])
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, T.RULE_AI_MISSCOPED) == [], rep["findings"]
    assert len([x for x in rep["findings"]
                if "e-rescope" in x["rule"]]) == 1, rep["findings"]


# ── `layer_present` is three states, not two ───────────────────────────────

def test_layer_present_is_true_false_and_not_measured(tmp_path):
    p = _root(tmp_path)
    present = T.converge_ai_expectation(
        p, _exp("a", "L9_INTEGRATION_SPEC", "fields.top_ports", ["clk"]))
    absent = T.converge_ai_expectation(
        p, _exp("b", _UNDECLARED_LAYER, "signoff.status", ["x"]))
    # Refused BEFORE the root is consulted: no id, so nothing was looked up.
    never = T.converge_ai_expectation(
        p, {"layer": "L9_INTEGRATION_SPEC", "expected_tokens": ["clk"]})
    assert present["layer_present"] is True
    assert absent["layer_present"] is False
    assert never["layer_present"] == "NOT_MEASURED"


def test_the_layer_lookup_tolerates_the_bare_layer_spelling(tmp_path):
    """PAIRED with the refusal: `L9` and `L9_INTEGRATION_SPEC` are one layer.

    A refusal that fired on a spelling difference would refuse the very repair
    it exists to ask for."""
    p = _root(tmp_path)
    assert T.converge_ai_expectation(
        p, _exp("c", "L9", "fields.top_ports", ["clk"]))["layer_present"] \
        is True


# ── the root's own identity ────────────────────────────────────────────────

def test_phase1_root_identity_reports_membership_and_content(tmp_path):
    p = _root(tmp_path)
    ident = T.phase1_root_identity(p)
    assert ident["status"] == "OK"
    assert ident["layers"] == ["L1_DATASHEET", "L4_REGMAP",
                               "L9_INTEGRATION_SPEC"]
    assert ident["layer_count"] == 3
    assert len(ident["digest"]) == 64
    # The DECLARED layers this root does not carry — the half that makes a
    # cross-root artefact readable rather than silently mis-addressed.
    assert ident["taxonomy_layer_count"] == len(T.taxonomy_layer_stems())
    assert "L21_POWER_INTENT" in ident["taxonomy_layers_absent"]
    assert "L1_DATASHEET" not in ident["taxonomy_layers_absent"]


def test_a_root_with_no_l_docs_is_not_measured_not_empty(tmp_path):
    """'Could not read it' is not 'read it and it was empty'."""
    p = tmp_path / "bare"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    ident = T.phase1_root_identity(p)
    assert ident["status"] == "NOT_MEASURED"
    assert ident["layers"] == []
    assert ident["digest"] is None


def test_membership_and_content_move_independently(tmp_path):
    """The two halves are separable — the reason both are recorded.

    A root that GREW and a root that CHANGED are different events, and a
    single digest cannot tell a reader which one happened."""
    a = _root(tmp_path, "a")
    b = _root(tmp_path, "b")
    c = _root(tmp_path, "c")
    # b: same membership, different CONTENT.
    (b / "phase1" / "generated_docs" / "L4_REGMAP.json").write_text(
        json.dumps({"doc_id": "L4", "records": []}))
    # c: same content for the shared layers, one MORE layer.
    (c / "phase1" / "generated_docs" / "L7_TEST_DEBUG.json").write_text(
        json.dumps({"doc_id": "L7"}))

    ia, ib, ic = (T.phase1_root_identity(x) for x in (a, b, c))
    assert ia["layers"] == ib["layers"] and ia["digest"] != ib["digest"]
    assert ia["layers"] != ic["layers"] and ia["digest"] != ic["digest"]


def test_the_report_carries_the_root_it_judged(tmp_path):
    p = _root(tmp_path)
    _answer(p, [_exp("e", "L9_INTEGRATION_SPEC", "fields.top_ports", ["clk"])])
    _run_track(p)
    rep = _report(p)
    assert rep["phase1_root"]["status"] == "OK"
    assert rep["phase1_root"]["layers"] == ["L1_DATASHEET", "L4_REGMAP",
                                            "L9_INTEGRATION_SPEC"]
    assert rep["phase1_root"]["digest"] == T.phase1_root_identity(p)["digest"]
