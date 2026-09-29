#!/usr/bin/env python3
"""test_expert_track_mapping_expected_tokens_is_refused.py

An `expected_tokens` value that is not a LIST is a malformed answer, never a
list of tokens the design failed to carry.

WHAT WAS MEASURED
-----------------
The hand-off's `answer_contract` shows `expected_tokens` as an OBJECT: the
token language (`matcher`, `rules`, `separators_are_not_a_disagreement`),
which a landed test pins so the author is told what the comparator can ask.
An IC-expert answer for a real Phase-1 root (20 expectations, every layer and
field_path checked against authoring_schema.json) mirrored that object and
wrote `"expected_tokens": {"matcher": "phrase_present", "tokens": [...]}`.

`_converge_one` built its token list with
`[t for t in (exp.get("expected_tokens") or []) if isinstance(t, str)]`.
Iterating a mapping yields its KEYS, so every row asked the layer for the
literal words `matcher` and `tokens`, and all 20 rows were published as
`EXPERT_TRACK_AI_EXPECTATION_UNMET` with `about: "design"` and the text "the
missing token(s) are in NO other layer either, so this is a content gap".
Twenty design gaps reported on the strength of two JSON key names, while the
tokens the author actually wrote were never put to any document.

THE RULE NOW
------------
* `expected_tokens` must be a JSON list. Any other type makes the
  expectation UNUSABLE, refused BY NAME under `RULE_AI_UNUSABLE`,
  `about: "track"`, stating the type it received. It is never converged and
  never reported as a design gap. The same holds per branch of a split.
* The pack's answer contract states the rule in words, beside the token
  language it already carries.

Paired: a list-form row with the same tokens still AGREES, and a list-form
row with a genuinely absent token is still a design gap, so the refusal can
neither swallow real gaps nor manufacture agreement.

Neutral synthetic fixtures only; no design, PDK, vendor or IP identifier.
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

The block exposes a control surface of two addressable words: a status word
that is read-only and a trim word that is read-write.
"""

_L9 = {"doc_id": "L9", "records": [
    {"name": "STATUS_WORD", "access": "read-only"},
    {"name": "TRIM_WORD", "access": "read-write"}]}

#: The shape the contract's object invites. Keys chosen to be words no layer
#: carries, so the pre-fix comparator reports them missing.
_MAPPING = {"matcher": "phrase_present", "tokens": ["read-only", "read-write"]}


def _project(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    (p / "phase1" / "generated_docs" / "L1_DATASHEET.json").write_text(
        json.dumps({"doc_id": "L1", "fields": {"word_count": 2}}))
    (p / "phase1" / "generated_docs" / "L9_INTEGRATION_SPEC.json").write_text(
        json.dumps(_L9))
    return p


def _run(project: Path, expectations) -> dict:
    pack = _pl.report_path(project, "phase1/expert_parse_track").parent \
        / "expert_parse_track_pack"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "l_doc_expectations.json").write_text(
        json.dumps({"expectations": expectations}))
    env = dict(os.environ)
    env["VIBE_IC_DISABLE_LLM_CONFIRM"] = "1"
    _pr.run([sys.executable, str(_PROGRAMS / "phase1_expert_parse_track.py"),
             str(project)], capture_output=True, text=True, env=env)
    return json.loads(
        _pl.report_path(project, "phase1/expert_parse_track.json").read_text())


def _rules(rep: dict, prefix: str):
    return sorted(f["rule"] for f in rep["findings"]
                  if f["rule"] == prefix or f["rule"].startswith(prefix + "::"))


def _finding(rep: dict, rule: str) -> dict:
    hits = [f for f in rep["findings"] if f["rule"] == rule]
    assert len(hits) == 1, (rule, [f["rule"] for f in rep["findings"]])
    return hits[0]


def _one_run(tmp_path: Path) -> dict:
    return _run(_project(tmp_path, "single"), [
        {"id": "mapping-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer records both access attributes",
         "evidence": ["spec.md:3"], "expected_tokens": _MAPPING},
        {"id": "list-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer records both access attributes",
         "evidence": ["spec.md:3"],
         "expected_tokens": ["read-only", "read-write"]},
        {"id": "real-gap-row", "layer": "L9_INTEGRATION_SPEC",
         "requirement": "the layer records the calibration ladder",
         "evidence": ["spec.md:1"], "expected_tokens": ["calibration ladder"]},
    ])


def test_a_mapping_is_not_reported_as_a_design_gap(tmp_path):
    rep = _one_run(tmp_path)
    assert _rules(rep, T.RULE_AI_UNMET) == [f"{T.RULE_AI_UNMET}::real-gap-row"]


def test_a_mapping_is_refused_by_name_about_the_track(tmp_path):
    rep = _one_run(tmp_path)
    f = _finding(rep, f"{T.RULE_AI_UNUSABLE}::mapping-row")
    assert f["about"] == "track"
    assert "list" in f["message"] and "dict" in f["message"], f["message"]
    states = {e["id"]: e for e in rep["ai_subtrack"]["converged"]}
    assert states["mapping-row"]["usable"] is False
    assert states["mapping-row"]["met"] is False
    # the mapping's keys were never taken for tokens
    assert "matcher" not in states["mapping-row"]["expected_tokens"]


def test_the_list_form_still_agrees_and_a_real_gap_is_still_a_design_gap(tmp_path):
    rep = _one_run(tmp_path)
    states = {e["id"]: e for e in rep["ai_subtrack"]["converged"]}
    assert states["list-row"]["usable"] is True
    assert states["list-row"]["met"] is True
    assert _finding(rep, f"{T.RULE_AI_UNMET}::real-gap-row")["about"] == "design"
    assert rep["denominator"]["ai"] == 3


def test_a_split_branch_carrying_a_mapping_is_refused(tmp_path):
    rep = _run(_project(tmp_path, "split"), [{
        "id": "split-row", "requirement": "one fact in two layers",
        "evidence": ["spec.md:3"],
        "sub_expectations": [
            {"layer": "L9_INTEGRATION_SPEC", "expected_tokens": _MAPPING},
            {"layer": "L9_INTEGRATION_SPEC", "expected_tokens": ["read-only"]},
        ]}])
    assert _rules(rep, T.RULE_AI_UNMET) == []
    f = _finding(rep, f"{T.RULE_AI_UNUSABLE}::split-row")
    assert f["about"] == "track"
    assert "list" in f["message"], f["message"]


def test_the_answer_contract_states_the_list_rule(tmp_path):
    import ic_expert_backup_pack as pack
    project = _project(tmp_path, "pack")
    out = tmp_path / "packout"
    out.mkdir()
    handoff = pack.assemble(
        prompt=_INPUT_DOC, iface=None, target=None, expert_skills=[],
        verify_gates=["phase1_expert_parse_track"], out_dir=out, k=1,
        output_target="l_doc_expectations.json",
        authoring_schema=T.authoring_schema(project))
    ac = handoff["answer_contract"]
    # the token language stays an object (landed contract, #2150) ...
    assert isinstance(ac["shape"]["expectations"][0]["expected_tokens"], dict)
    # ... and the value an author WRITES is stated as a list of strings
    stated = [r for r in ac["rules"]
              if "expected_tokens" in r and "list" in r and "string" in r]
    assert stated, ac["rules"]
