#!/usr/bin/env python3
"""test_issue2190_split_finding_rendering.py

Every finding raised on a SPLIT expectation must name the branches it actually
asked and the tokens it actually compared (vibe-ic#2190).

WHAT WAS MEASURED, ON PRISTINE MAIN
-----------------------------------
Measured 2026-09-08, lane cz2190, host 8HD-4, on main
``ab8d9ce834f0d7acb1d4be8ff7218bae299c87b4`` / tree
``16de35f58e00b4adb967fdf99f0931112dc7397d``.

``_converge_split`` decides a split correctly, per branch, and then leaves the
PARENT row's ``layer``, ``field_path`` and ``expected_tokens`` empty — that is
the documented ``SPLIT_MARKER`` contract, and it is right: the parent addresses
no single field and no single layer. The three finding emitters read those
parent fields as if a single expectation had supplied them, so every finding
raised on a split rendered::

    "layer": null,
    "message": "The AI sub-track asked None for: <requirement>. That layer does
                not carry it, and ['L4_REGMAP'] DOES — every one of the 0
                expected token(s). ... or — if the layer contract really does
                put it in None — record that as a layer-contract defect."

Three falsehoods in one sentence, and the third is the load-bearing one:
``every one of the 0 expected token(s)`` is a VACUOUS UNIVERSAL printed as the
positive evidence that the fact WAS extracted, and the sentence built on it
then instructs the reader not to repair an extractor. The claim underneath is
sound — the parent's ``owning_layers`` is computed per failing branch — but a
reader auditing the finding cannot tell a real re-scope from a vacuous one,
which is the confusion #2127 existed to remove.

A UNION IS NOT AN OWNER
-----------------------
Also measured here, and the reason a bigger number would not have been a fix:
the parent's ``owning_layers`` is a UNION across the disagreeing branches. On
the two-branch fixture below it is ``['L19_CONSTRAINTS_PDK', 'L4_REGMAP']``
while L4 carries one half and L19 the other and NEITHER carries both.
Substituting a real token count into that same union sentence would have
replaced a vacuous claim with a FALSE one. Only the per-branch form is true.

THE THIRD SHAPE, measured while reproducing: a split whose absent branches are
MIXED — one layer the taxonomy does not declare, one it declares and this root
does not carry — joined both names into ``layer`` and printed them as ONE
quoted name, then asserted "the L-doc taxonomy declares no layer of that name"
about both. The second half of that is #312's disagreement, not this refusal.

THE CONTROL IS THE POINT
------------------------
The defect is split-specific: 10/10 split findings vs 0/84 single findings in
the run that filed the issue. So every assertion here is PAIRED with a SINGLE
expectation of the same shape whose message is pinned VERBATIM — if the repair
had reached the single form, those three strings would move.

#2150 subsequently added carriage diagnostics to SINGLE unmet findings. The
single-form control pins that complete composed contract below, including its
content-gap explanation; the split repair must not erase that diagnosis.

Every fixture is synthesised here from neutral parts. No design, PDK, vendor or
IP-model identifier appears anywhere in this file.

Run: python3 -m pytest programs/tests/test_issue2190_split_finding_rendering.py -q
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

The block exposes a control surface of four addressable words, and its
constraint budget is stated by the integrating system rather than here.
"""

#: HALF the fact.
_LAYER_A = {"doc_id": "L4", "records": [{"name": "STATUS_WORD",
                                         "offset": "0x0"}]}
#: THE OTHER HALF, in a different layer. Neither carries both.
_LAYER_B = {"doc_id": "L19",
            "fields": {"notes": "budget deferred to the integrating system"}}

_TOKEN_A = "STATUS_WORD"
_TOKEN_B = "deferred to the integrating system"

#: A layer name no taxonomy entry declares.
_UNDECLARED = "L91_NOT_A_DECLARED_LAYER"
#: A layer the taxonomy DOES declare and this root does not carry.
_DECLARED_ABSENT = "L21_POWER_INTENT"


def _project(tmp_path, name="proj"):
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    (p / "phase1" / "generated_docs" / "L1_DATASHEET.json").write_text(
        json.dumps({"doc_id": "L1", "word_count": 4}))
    (p / "phase1" / "generated_docs" / "L4_REGMAP.json").write_text(
        json.dumps(_LAYER_A))
    (p / "phase1" / "generated_docs" / "L19_CONSTRAINTS_PDK.json").write_text(
        json.dumps(_LAYER_B))
    return p


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
    return _pr.run(
        [sys.executable, str(_PROGRAMS / "phase1_expert_parse_track.py"),
         str(project)], capture_output=True, text=True, env=env)


def _report(project: Path):
    return json.loads(
        _pl.report_path(project, "phase1/expert_parse_track.json").read_text())


def _finding(rep, exp_id):
    """The ONE finding this expectation produced, by id. Asserted to be one:
    a shape that produced two would make every message assertion below a
    statement about whichever happened to sort first."""
    hits = [f for f in rep["findings"] if f["rule"].endswith("::" + exp_id)]
    assert len(hits) == 1, [f["rule"] for f in rep["findings"]]
    return hits[0]


# ── the fixtures: three split shapes, three single shapes ──────────────────

#: SPLIT that misscopes. Both branches ask a layer that does not carry their
#: half, and a DIFFERENT layer carries each half. The parent's `owning_layers`
#: is the union of the two, and neither member of it carries both tokens.
_SPLIT_MISSCOPE = {
    "id": "split-misscope",
    "requirement": "the control surface and where its budget is set",
    "evidence": ["input spec"],
    "sub_expectations": [
        {"layer": "L1_DATASHEET", "field_path": "word_count",
         "expected_tokens": [_TOKEN_A]},
        {"layer": "L1_DATASHEET", "field_path": "word_count",
         "expected_tokens": [_TOKEN_B]},
    ]}

#: SPLIT that disagrees with the design: one branch's token is carried nowhere.
_SPLIT_UNMET = {
    "id": "split-unmet",
    "requirement": "the control surface and where its budget is set",
    "evidence": ["input spec"],
    "sub_expectations": [
        {"layer": "L4_REGMAP", "field_path": "records",
         "expected_tokens": [_TOKEN_A]},
        {"layer": "L19_CONSTRAINTS_PDK", "field_path": "fields.notes",
         "expected_tokens": ["NO_LAYER_CARRIES_THIS"]},
    ]}

#: SPLIT one of whose branches names a layer the taxonomy does not declare.
_SPLIT_ABSENT = {
    "id": "split-absent",
    "requirement": "the control surface and where its budget is set",
    "evidence": ["input spec"],
    "sub_expectations": [
        {"layer": "L4_REGMAP", "field_path": "records",
         "expected_tokens": [_TOKEN_A]},
        {"layer": _UNDECLARED, "field_path": "fields.notes",
         "expected_tokens": [_TOKEN_B]},
    ]}

#: SPLIT whose two absent branches are of DIFFERENT kinds.
_SPLIT_MIXED_ABSENT = {
    "id": "split-mixed-absent",
    "requirement": "the control surface and where its budget is set",
    "evidence": ["input spec"],
    "sub_expectations": [
        {"layer": _UNDECLARED, "field_path": "fields",
         "expected_tokens": [_TOKEN_A]},
        {"layer": _DECLARED_ABSENT, "field_path": "fields",
         "expected_tokens": [_TOKEN_B]},
    ]}

#: THE CONTROLS — one SINGLE expectation per emitter.
_SINGLE_MISSCOPE = {"id": "single-misscope", "layer": "L1_DATASHEET",
                    "field_path": "word_count",
                    "requirement": "the control surface",
                    "evidence": ["input spec"],
                    "expected_tokens": [_TOKEN_A]}
_SINGLE_UNMET = {"id": "single-unmet", "layer": "L4_REGMAP",
                 "field_path": "records",
                 "requirement": "the control surface",
                 "evidence": ["input spec"],
                 "expected_tokens": ["NO_LAYER_CARRIES_THIS"]}
_SINGLE_ABSENT = {"id": "single-absent", "layer": _UNDECLARED,
                  "field_path": "fields",
                  "requirement": "the control surface",
                  "evidence": ["input spec"],
                  "expected_tokens": [_TOKEN_A]}

_ALL = [_SPLIT_MISSCOPE, _SPLIT_UNMET, _SPLIT_ABSENT, _SPLIT_MIXED_ABSENT,
        _SINGLE_MISSCOPE, _SINGLE_UNMET, _SINGLE_ABSENT]


def _run_all(tmp_path):
    """ONE run carrying every shape, so nothing but the grammar differs
    between the split rows and their single controls."""
    p = _project(tmp_path)
    _answer(p, _ALL)
    _run_track(p)
    return _report(p)


# ── ARM 1: the split findings must say what was actually asked ─────────────

def test_a_split_misscope_names_each_disagreeing_branch_by_address(tmp_path):
    """A1. `asked None` named neither of the two layers the branches asked."""
    f = _finding(_run_all(tmp_path), "split-misscope")
    assert f["rule"].startswith(T.RULE_AI_MISSCOPED), f["rule"]
    assert "asked None" not in f["message"], f["message"]
    for i in (0, 1):
        assert f"[split-misscope#{i}] asked L1_DATASHEET.word_count" \
            in f["message"], f["message"]
    # PER BRANCH, never the union: the parent's `owning_layers` holds both
    # layers and NEITHER of them carries both tokens.
    assert "['L4_REGMAP'] carries every one of them" in f["message"]
    assert "['L19_CONSTRAINTS_PDK'] carries every one of them" in f["message"]
    assert "['L19_CONSTRAINTS_PDK', 'L4_REGMAP'] carries" not in f["message"]


def test_a_split_misscope_states_each_branch_s_own_token_count(tmp_path):
    """A2. THE LOAD-BEARING ONE. A split parent's `expected_tokens` is `[]` by
    construction, so `every one of the 0 expected token(s)` is a vacuous
    universal that can never read otherwise — printed as the evidence the fact
    WAS extracted."""
    f = _finding(_run_all(tmp_path), "split-misscope")
    assert "0 expected token(s)" not in f["message"], f["message"]
    assert f["message"].count("for 1 expected token(s)") == 2, f["message"]


def test_a_split_misscope_drops_the_clause_with_no_referent(tmp_path):
    """A3. "if the layer contract really does put it in None" instructs the
    reader to check a layer contract for a layer that is not one."""
    f = _finding(_run_all(tmp_path), "split-misscope")
    assert "put it in None" not in f["message"], f["message"]
    # The remedy itself must survive — dropping the sentence is not the fix.
    assert "layer-contract defect in its own right" in f["message"]


def test_a_split_unmet_names_the_branch_addresses_not_None(tmp_path):
    """A4. The UNMET emitter takes a different message branch and rendered the
    same null: "expected None to carry"."""
    f = _finding(_run_all(tmp_path), "split-unmet")
    assert f["rule"].startswith(T.RULE_AI_UNMET), f["rule"]
    assert f["about"] == "design"
    assert "expected None to carry" not in f["message"], f["message"]
    assert "L4_REGMAP.records" in f["message"]
    assert "L19_CONSTRAINTS_PDK.fields.notes" in f["message"]
    # A CONJUNCTION, and the finding has to say so — a reader told two
    # addresses without it cannot tell an "every" from an "any".
    assert "must agree" in f["message"]


def test_a_split_layer_absent_states_the_refused_branch_s_own_token_count(
        tmp_path):
    """A5. Same vacuous universal, third emitter."""
    f = _finding(_run_all(tmp_path), "split-absent")
    assert f["rule"].startswith(T.RULE_AI_LAYER_ABSENT), f["rule"]
    assert "0 expected token(s)" not in f["message"], f["message"]
    assert f"[split-absent#1] {_UNDECLARED}.fields.notes, 1 expected " \
        f"token(s)" in f["message"], f["message"]
    assert "carries every one of its 1 expected token(s)" in f["message"]


def test_a_split_layer_absent_does_not_call_a_declared_layer_undeclared(
        tmp_path):
    """A6. The MIXED shape. `_converge_split` joins EVERY absent branch's layer
    into the parent's `layer`, and the parent's `layer_in_taxonomy` is
    `all(...)` over them — so ONE undeclared branch refuses the row and the
    single form then asserted "the taxonomy declares no layer of that name"
    about branches whose layer the taxonomy DOES declare. That second half is
    #312's disagreement, not this refusal, and the two need different people.
    """
    f = _finding(_run_all(tmp_path), "split-mixed-absent")
    assert f["rule"].startswith(T.RULE_AI_LAYER_ABSENT), f["rule"]
    # The undeclared branch is the one the refusal is about, and it is ONE.
    assert "1 of its branch(es) address a layer name the L-doc taxonomy " \
        "does not declare" in f["message"], f["message"]
    assert f"[split-mixed-absent#0] {_UNDECLARED}.fields" in f["message"]
    # The declared-but-absent branch is named as the DIFFERENT fact it is.
    assert f"[split-mixed-absent#1] {_DECLARED_ABSENT}.fields" in f["message"]
    assert "the taxonomy DOES declare and this root does not carry" \
        in f["message"], f["message"]
    # And the composite pseudo-name is never printed as one quoted layer.
    assert f"'{_UNDECLARED}, {_DECLARED_ABSENT}'" not in f["message"]


def test_no_split_finding_prints_a_vacuous_universal(tmp_path):
    """A7. MEMBERSHIP over every split row in one run, so a fourth emitter
    added later cannot reintroduce the shape unnoticed."""
    rep = _run_all(tmp_path)
    split_ids = {e["id"] for e in _ALL if "sub_expectations" in e}
    seen = set()
    for f in rep["findings"]:
        exp_id = f["rule"].split("::", 1)[-1].split("#", 1)[0]
        if exp_id not in split_ids:
            continue
        seen.add(exp_id)
        # ANY zero-token universal, however the sentence around it is spelled
        # — pinning only the phrasing this defect happened to use would leave
        # the next spelling of it unmeasured. (Measured: mutation M2, which
        # put the parent's zero back through a differently-worded clause,
        # reddened only the per-branch assertion until this line was widened.)
        assert "0 expected token(s)" not in f["message"], f
        assert "asked None" not in f["message"], f
        assert "expected None to carry" not in f["message"], f
        assert "put it in None" not in f["message"], f
        # `None` never stands where a layer name belongs, in any emitter.
        assert " None " not in f["message"].replace("None of ", ""), f
    # Every split fixture actually produced a finding: without this the loop
    # above passes over an empty set.
    assert seen == split_ids, (seen, split_ids)


def test_every_split_finding_carries_its_branches_machine_readably(tmp_path):
    """A8. The prose is for a reader; a triage tool needs the addresses as
    data. The parent's `layer`/`field_path` cannot supply them — by contract —
    so the branches are carried BESIDE those fields, never instead of them."""
    rep = _run_all(tmp_path)
    f = _finding(rep, "split-misscope")
    assert [b["layer"] for b in f["split_branches"]] == \
        ["L1_DATASHEET", "L1_DATASHEET"], f
    assert [b["expected_token_count"] for b in f["split_branches"]] == [1, 1]
    assert [b["owning_layers"] for b in f["split_branches"]] == \
        [["L4_REGMAP"], ["L19_CONSTRAINTS_PDK"]]
    assert [b["id"] for b in f["split_branches"]] == \
        ["split-misscope#0", "split-misscope#1"]
    # All three emitters, not just the one.
    for exp_id in ("split-unmet", "split-absent", "split-mixed-absent"):
        rows = _finding(rep, exp_id)["split_branches"]
        assert len(rows) == 2, (exp_id, rows)
        assert all(r["expected_token_count"] == 1 for r in rows), rows


# ── ARM 2: the CONTROL — a SINGLE expectation is rendered EXACTLY as before ─
#
# VERBATIM. A substring assertion would survive the split form being applied
# to a single expectation as long as one phrase happened to be shared; the
# whole string will not.

_SINGLE_MISSCOPE_MESSAGE = (
    "The AI sub-track asked L1_DATASHEET.word_count for: the control surface. "
    "That layer does not carry it, and ['L4_REGMAP'] DOES — every one of the "
    "1 expected token(s). So the fact was extracted and the expectation named "
    "the wrong layer; this is NOT a missing extraction and must not be "
    "repaired in an extractor. Either re-scope the expectation to the layer "
    "that owns the fact, or — if the layer contract really does put it in "
    "L1_DATASHEET — record that as a layer-contract defect in its own right.")

_SINGLE_UNMET_MESSAGE = (
    "The AI sub-track, reading the same design input independently, expected "
    "L4_REGMAP.records to carry: the control surface. The program track "
    "produced: L4_REGMAP.records does not carry ['NO_LAYER_CARRIES_THIS'] "
    "(checked 1 expected token(s)). Grounds: input spec. "
    "Carriage: the missing token(s) are in NO other layer either, "
    "so this is a content gap and not a scoping one.")

_SINGLE_ABSENT_MESSAGE = (
    f"The AI sub-track addressed layer '{_UNDECLARED}', and the L-doc "
    f"taxonomy declares no layer of that name — so no Phase-1 root of any "
    f"design carries it and no extractor can produce it. This root carries "
    f"['L19_CONSTRAINTS_PDK', 'L1_DATASHEET', 'L4_REGMAP']. The miss is a "
    f"fact about the EXPECTATION, not about the design; ['L4_REGMAP'] "
    f"carries every one of the 1 expected token(s), so the repair is to "
    f"re-scope the expectation there.")


def test_a_single_misscope_message_is_unchanged_verbatim(tmp_path):
    """B1."""
    f = _finding(_run_all(tmp_path), "single-misscope")
    assert f["rule"].startswith(T.RULE_AI_MISSCOPED), f["rule"]
    assert f["layer"] == "L1_DATASHEET" and f["field_path"] == "word_count"
    assert f["message"] == _SINGLE_MISSCOPE_MESSAGE, f["message"]


def test_a_single_unmet_message_is_unchanged_verbatim(tmp_path):
    """B2."""
    f = _finding(_run_all(tmp_path), "single-unmet")
    assert f["rule"].startswith(T.RULE_AI_UNMET), f["rule"]
    assert f["message"] == _SINGLE_UNMET_MESSAGE, f["message"]


def test_a_single_layer_absent_message_is_unchanged_verbatim(tmp_path):
    """B3."""
    f = _finding(_run_all(tmp_path), "single-absent")
    assert f["rule"].startswith(T.RULE_AI_LAYER_ABSENT), f["rule"]
    assert f["message"] == _SINGLE_ABSENT_MESSAGE, f["message"]


def test_a_single_finding_carries_no_split_branch_rows(tmp_path):
    """B4. The new key is a fact about a split. A single expectation carrying
    an empty one would read as a split with no branches."""
    rep = _run_all(tmp_path)
    for exp_id in ("single-misscope", "single-unmet", "single-absent"):
        assert "split_branches" not in _finding(rep, exp_id), exp_id


# ── the verdict itself did not move ────────────────────────────────────────

def test_the_rendering_repair_moved_no_verdict(tmp_path):
    """MEMBERSHIP over the RULES. This landing changes what a finding SAYS.
    If it changed which findings exist, or which of them is `about: design`,
    it would be a different change wearing this issue's number."""
    rep = _run_all(tmp_path)
    assert sorted(f["rule"] for f in rep["findings"]
                  if "::" in f["rule"]) == [
        f"{T.RULE_AI_LAYER_ABSENT}::single-absent",
        f"{T.RULE_AI_LAYER_ABSENT}::split-absent",
        f"{T.RULE_AI_LAYER_ABSENT}::split-mixed-absent",
        f"{T.RULE_AI_MISSCOPED}::single-misscope",
        f"{T.RULE_AI_MISSCOPED}::split-misscope",
        f"{T.RULE_AI_UNMET}::single-unmet",
        f"{T.RULE_AI_UNMET}::split-unmet",
    ], [f["rule"] for f in rep["findings"]]
    assert {f["rule"].split("::")[1]: f["about"] for f in rep["findings"]
            if "::" in f["rule"]} == {
        "single-absent": "track", "split-absent": "track",
        "split-mixed-absent": "track", "single-misscope": "track",
        "split-misscope": "track", "single-unmet": "design",
        "split-unmet": "design"}
