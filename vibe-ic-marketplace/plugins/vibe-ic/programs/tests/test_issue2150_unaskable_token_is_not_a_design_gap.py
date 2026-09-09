#!/usr/bin/env python3
"""test_issue2150_unaskable_token_is_not_a_design_gap.py

An expected token THIS COMPARATOR CANNOT ASK FOR is a fact about the
expectation, never about the design (vibe-ic#2150 item 2).

WHAT WAS MEASURED, ON LIVE MAIN 610cae2cc
-----------------------------------------
#2150 states a remainder of "20 rows still disagree, 3 unreachable". Re-run
against the surviving Phase-1 root at 610cae2cc the count reproduces exactly —
14 AGREE / 20 DISAGREE / 12 WITHDRAWN — and the 20 have FOUR root causes, not
twenty. FOUR of the twenty are decided by a single token the comparator never
actually put to any document:

  * `present_in_units` -> `phrase_present` reduces BOTH sides with `_norm`,
    which lower-cases and collapses every run of non-`[a-z0-9]` to one space.
    A token with no `[a-z0-9]` character normalises to the EMPTY string, and
    `phrase_present` returns False on `if not want` BEFORE it reads the
    haystack. The answer is False for every possible document, INCLUDING one
    that states the token verbatim. One surviving row expects a token written
    wholly in CJK; the design input states it verbatim, and the row was
    published as a gap in the design.
  * `_norm` also deletes an ELLIPSIS, so `"writes ... are ignored"` silently
    becomes a demand for the contiguous phrase `writes are ignored`. Three
    surviving rows carry such a token. One of them, `l9-idle-gated-writes-are-
    ignored`, was then WITHDRAWN by the #2150 decision table with the reason
    "neither the input nor any layer states [...] in this form" — while the
    design input states "writes to these registers are ignored" five times.
    A TRUE expectation deleted on the strength of a comparator defect.

ONE such token is enough to decide a whole row: `met` is `not missing`, and
the owning-layer answer is the INTERSECTION over the tokens, so a token no
document can carry empties `owning_layers` and the row falls past the misscope
branch into the design column.

MEASURED, base vs fix, on that root with the same 46-row answer: 43 findings
before and 43 after; `about` moves 20 design / 23 track -> 16 design / 27
track; the four moved rows are exactly the four named above; the denominator
(46), the verdict and `blocking` are unchanged; and NO row becomes an
agreement.

WHY A REFUSAL AND NOT A WIDENING
--------------------------------
Teaching `_norm` to keep CJK was measured and it moves NOTHING here: under a
CJK-preserving normaliser no emitted layer carries that token either, because
this root's L-docs are English-only. And an ellipsis cannot be honoured at all
without making the comparator WEAKER the more an author writes — the property
`_converge_split` already refuses for a disjunction. So the repair is to stop
answering, exactly as #2191 stopped answering an ownership question that could
not be asked.

WHAT THIS FILE PINS
-------------------
  * the unreachability CLAIM itself, proved rather than asserted: a haystack
    that states the token verbatim still answers False;
  * the refusal, BY NAME and `about: "track"`, in a real run;
  * the ANTI-CHEAT, in the same run: an askable token that is genuinely
    missing is STILL `about: "design"` under `RULE_AI_UNMET`. A guard that
    swallowed real gaps would be a much worse defect than the one it closes;
  * that the guard does not manufacture agreements — `met` is untouched and
    the denominator does not move;
  * PER BRANCH for a split, naming the branch;
  * the authoring-time half: the schema an author is handed DECLARES the token
    language, so the two shapes cannot be written in the first place.

Every test is PAIRED: each refusal has an acceptance beside it, so a check
that always fired would fail as loudly as one that never fired.

Every fixture is synthesised here from neutral parts. No design, PDK, vendor
or IP-model identifier appears anywhere in this file.

Run: python3 -m pytest programs/tests/test_issue2150_unaskable_token_is_not_a_design_gap.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase1_expert_parse_track as T   # noqa: E402
import _path_layout as _pl              # noqa: E402
import _progress_run as _pr             # noqa: E402


_INPUT_DOC = """# Block specification

While the unit is busy, writes to these words are ignored. The block exposes
a control surface of four addressable words.
"""

#: A layer the fixture root contains that carries the askable tokens.
_PRESENT_LAYER = {
    "doc_id": "L9",
    "records": [
        {"name": "STATUS_WORD", "access": "read-only"},
        {"name": "TRIM_WORD", "access": "read-write"},
    ],
}

#: A token with NO `[a-z0-9]` character. Neutral punctuation, not any
#: design's vocabulary — the property under test is the normaliser's, and it
#: holds for CJK, Greek, Cyrillic and punctuation alike.
_NO_MATCHABLE = "———"
_ELLIPSIS_TOKEN = "writes ... are ignored"


def _project(tmp_path, name="proj"):
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    (p / "phase1" / "generated_docs" / "L1_DATASHEET.json").write_text(
        json.dumps({"doc_id": "L1", "fields": {"word_count": 4}}))
    (p / "phase1" / "generated_docs" / "L9_INTEGRATION_SPEC.json").write_text(
        json.dumps(_PRESENT_LAYER))
    return p


def _pack_dir(project: Path) -> Path:
    return _pl.report_path(project, "phase1/expert_parse_track").parent \
        / "expert_parse_track_pack"


def _answer(project: Path, expectations):
    d = _pack_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / "l_doc_expectations.json").write_text(
        json.dumps({"expectations": expectations}, ensure_ascii=False))


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


def _by_rule(rep, prefix):
    return [f for f in rep["findings"]
            if f["rule"] == prefix or f["rule"].startswith(prefix + "::")]


# ── 1. the unreachability CLAIM, proved rather than asserted ────────────────

def test_a_token_with_no_matchable_character_is_false_against_a_document_that_states_it():
    """The proof #2150 item 2 asks for: the condition that cannot be satisfied.

    Not "we searched and did not find it" — the search never happens. The
    phrase normalises to nothing and `phrase_present` returns on the empty
    phrase, so there is no document in the world that answers True.
    """
    assert T._norm(_NO_MATCHABLE) == ""
    verbatim = f"the specification says {_NO_MATCHABLE} right here"
    assert _NO_MATCHABLE in verbatim, "the fixture must state the token"
    assert T.phrase_present(_NO_MATCHABLE, verbatim) is False
    # and it stays False however the document is written
    for hay in ("", _NO_MATCHABLE, _NO_MATCHABLE * 50, "a b c"):
        assert T.phrase_present(_NO_MATCHABLE, hay) is False


def test_an_askable_token_is_still_found_so_the_proof_is_not_vacuous():
    """The paired acceptance. Without it the test above would also pass
    against a `phrase_present` that returned False for everything."""
    assert T.phrase_present("trim word", "the TRIM_WORD latches the code")


def test_an_ellipsis_is_erased_not_honoured_as_a_wildcard():
    """The author wrote a pattern; the comparator compares a phrase."""
    stated = "while the unit is busy, writes to these words are ignored"
    assert T.phrase_present(_ELLIPSIS_TOKEN, stated) is False
    # ... and what it DID ask is the elided concatenation, which the document
    # does not state either. Both halves matter: the second is what makes the
    # refusal honest rather than a claim that the fact is absent.
    assert T._norm(_ELLIPSIS_TOKEN).split() == ["writes", "are", "ignored"]
    assert T.phrase_present("writes are ignored", stated) is False
    assert T.phrase_present("words are ignored", stated) is True


# ── 2. the classifier, both directions ──────────────────────────────────────

def test_unaskable_tokens_names_the_reason_and_what_was_asked_instead():
    out = {u["token"]: u for u in T.unaskable_tokens(
        [_NO_MATCHABLE, _ELLIPSIS_TOKEN, "read-only", "1.8 V"])}
    assert set(out) == {_NO_MATCHABLE, _ELLIPSIS_TOKEN}, \
        "an askable token must never be refused"
    assert out[_NO_MATCHABLE]["reason"] == T.TOKEN_NO_MATCHABLE_CHARACTER
    assert out[_ELLIPSIS_TOKEN]["reason"] == T.TOKEN_ELLIPSIS_ERASED
    # the refusal is falsifiable from the report alone
    assert out[_ELLIPSIS_TOKEN]["asked_instead"] == "writes are ignored"


def test_unaskable_tokens_is_empty_for_an_ordinary_list():
    assert T.unaskable_tokens(["read-only", "0x1c", "TRIM_SEL", "1.8 V"]) == []
    assert T.unaskable_tokens([]) == []
    assert T.unaskable_tokens(None) == []


def test_the_answer_does_not_depend_on_any_design():
    """It reads no document, so it is the same for every chip. That is what
    makes it a fact about the expectation."""
    a = T.unaskable_tokens([_ELLIPSIS_TOKEN])
    b = T.unaskable_tokens([_ELLIPSIS_TOKEN])
    assert a == b and len(a) == 1


# ── 3. the refusal in a REAL RUN, with its anti-cheat beside it ─────────────

@pytest.fixture()
def run_with_both(tmp_path):
    """ONE run carrying an unaskable row AND a genuinely-missing askable row."""
    project = _project(tmp_path)
    _answer(project, [
        {"id": "unaskable-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer must record the write-gating rule",
         "evidence": ["the input states it"],
         "expected_tokens": ["read-only", _ELLIPSIS_TOKEN]},
        {"id": "real-gap-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer must record the calibration ladder",
         "evidence": ["the input states it"],
         "expected_tokens": ["calibration ladder"]},
        {"id": "agreeing-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer must record the access attributes",
         "evidence": ["the input states it"],
         "expected_tokens": ["read-only", "read-write"]},
    ])
    rc, out, err = _run_track(project)
    return project, _report(project), rc, out


def test_the_unaskable_row_is_refused_by_name_and_is_about_the_track(run_with_both):
    _, rep, _, _ = run_with_both
    refused = _by_rule(rep, T.RULE_AI_TOKEN_UNASKABLE)
    assert [f["rule"] for f in refused] == [
        f"{T.RULE_AI_TOKEN_UNASKABLE}::unaskable-row"]
    f = refused[0]
    assert f["about"] == "track"
    assert [u["token"] for u in f["tokens_unaskable"]] == [_ELLIPSIS_TOKEN]
    # the finding must say what was asked instead, or the author cannot repair
    assert "writes are ignored" in f["message"]


def test_the_unaskable_row_is_NOT_also_published_as_a_design_gap(run_with_both):
    """The whole point. Before this landing the row arrived here."""
    _, rep, _, _ = run_with_both
    unmet = {f["rule"] for f in _by_rule(rep, T.RULE_AI_UNMET)}
    assert f"{T.RULE_AI_UNMET}::unaskable-row" not in unmet


def test_a_real_design_gap_in_the_same_run_is_untouched(run_with_both):
    """THE ANTI-CHEAT. A guard that swallowed genuine gaps would pass every
    check above while being a far worse defect than the one it closes."""
    _, rep, _, _ = run_with_both
    unmet = _by_rule(rep, T.RULE_AI_UNMET)
    assert [f["rule"] for f in unmet] == [f"{T.RULE_AI_UNMET}::real-gap-row"]
    assert unmet[0]["about"] == "design"
    assert not _by_rule(rep, f"{T.RULE_AI_TOKEN_UNASKABLE}::real-gap-row")


def test_the_guard_manufactures_no_agreement(run_with_both):
    """`met` is untouched and the denominator does not move: this landing
    changes WHERE a row is reported, never whether it agreed."""
    _, rep, _, _ = run_with_both
    assert rep["denominator"]["ai"] == 3
    states = {e["id"]: e for e in rep["ai_subtrack"]["converged"]}
    assert states["unaskable-row"]["met"] is False
    assert states["agreeing-row"]["met"] is True
    assert states["agreeing-row"]["tokens_unaskable"] == []
    assert [u["token"] for u in states["unaskable-row"]["tokens_unaskable"]] \
        == [_ELLIPSIS_TOKEN]


# ── 4. a SPLIT is refused PER BRANCH ────────────────────────────────────────

def test_a_split_names_the_branch_whose_token_cannot_be_asked(tmp_path):
    project = _project(tmp_path, "split")
    _answer(project, [{
        "id": "split-row",
        "requirement": "one fact, carried by two layers",
        "evidence": ["the input states it"],
        "sub_expectations": [
            {"layer": "L9_INTEGRATION_SPEC", "expected_tokens": ["read-only"]},
            {"layer": "L1_DATASHEET", "expected_tokens": [_NO_MATCHABLE]},
        ],
    }])
    _run_track(project)
    rep = _report(project)
    refused = _by_rule(rep, T.RULE_AI_TOKEN_UNASKABLE)
    assert [f["rule"] for f in refused] == [
        f"{T.RULE_AI_TOKEN_UNASKABLE}::split-row"]
    entries = refused[0]["tokens_unaskable"]
    assert [u["branch"] for u in entries] == ["split-row#1"], \
        "a refusal naming only the parent sends the author to re-read every branch"
    assert "split-row#1" in refused[0]["message"]
    assert not _by_rule(rep, f"{T.RULE_AI_UNMET}::split-row")


def test_a_split_whose_branches_are_all_askable_is_not_refused(tmp_path):
    """The paired acceptance for the split path."""
    project = _project(tmp_path, "split_ok")
    _answer(project, [{
        "id": "split-ok",
        "requirement": "one fact, carried by two layers",
        "evidence": ["the input states it"],
        "sub_expectations": [
            {"layer": "L9_INTEGRATION_SPEC", "expected_tokens": ["read-only"]},
            {"layer": "L1_DATASHEET", "expected_tokens": ["word count"]},
        ],
    }])
    _run_track(project)
    rep = _report(project)
    assert _by_rule(rep, T.RULE_AI_TOKEN_UNASKABLE) == []


# ── 5. the AUTHORING-time half (#2150 F5, second half) ──────────────────────

def test_the_authoring_schema_declares_the_expected_token_language(tmp_path):
    """F5 as first landed declared a vocabulary for `field_path` — the field
    an expectation only DECORATES with — and nothing at all for
    `expected_tokens`, the field every verdict rests on."""
    project = _project(tmp_path, "schema")
    schema = T.authoring_schema(project)
    assert schema["status"] == "OK"
    rule = schema["expected_tokens_rule"]
    joined = " ".join(rule["rules"]).lower()
    assert "ellipsis" in joined
    assert "a-za-z0-9" in joined.replace(" ", "")
    # the declaration and the refusal are ONE declaration read at two moments:
    # every shape the schema forbids must be a shape `unaskable_tokens` names.
    assert {u["reason"] for u in T.unaskable_tokens(
        [_NO_MATCHABLE, _ELLIPSIS_TOKEN])} == {
            T.TOKEN_NO_MATCHABLE_CHARACTER, T.TOKEN_ELLIPSIS_ERASED}


def test_the_handoff_pack_hands_the_author_that_declaration(tmp_path):
    """A grammar the author is never shown is a grammar nobody writes — the
    reason the split form was advertised in the pack too."""
    import ic_expert_backup_pack as pack
    project = _project(tmp_path, "pack")
    out = tmp_path / "packout"
    out.mkdir()
    handoff = pack.assemble(
        prompt=_INPUT_DOC, iface=None, target=None, expert_skills=[],
        verify_gates=["phase1_expert_parse_track"], out_dir=out, k=1,
        output_target="l_doc_expectations.json",
        authoring_schema=T.authoring_schema(project))
    shape = handoff["answer_contract"]["shape"]["expectations"][0]
    assert isinstance(shape["expected_tokens"], dict), \
        "the author must be handed the token language, not a prose placeholder"
    assert "ellipsis" in " ".join(shape["expected_tokens"]["rules"]).lower()
