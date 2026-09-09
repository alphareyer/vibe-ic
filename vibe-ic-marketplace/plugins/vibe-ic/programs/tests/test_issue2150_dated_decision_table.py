#!/usr/bin/env python3
"""test_issue2150_dated_decision_table.py

vibe-ic#2150 item 6 — A DECISION TABLE IS DATED BEFORE IT IS USED.

Each mechanism is PAIRED: a check that only ever refuses is as broken as one
that only ever accepts, so every refusal here has an acceptance beside it that
must survive the same code.

WHAT WAS MEASURED. #2132's 30-row decision table, computed by reading ONE
Phase-1 root and then applied to the opentitan_aes artefact, names two leaves
that artefact does not declare and three fields introduced by work that landed
AFTER the emitter which wrote those layers (artefact plugin v1.17.38) — fields
that could not exist in it. None of that is a defect in the table. It is a
table computed on a NEWER root judging an OLDER artefact, and the only reason
it surfaced at all is that #2127's field-path guard refused the paths. Without
a stamp the five would have been applied silently and read as decisions about
this design.

The other half of #2150 — the SPLIT expectation grammar and the authoring
schema shipped in the hand-off pack — landed separately (`20ccb1b6cc`,
`fd8cbb30c8`) and is pinned by `test_issue2150_split_expectation_grammar.py`
and `test_issue2150_expectation_authoring_schema.py`. This file pins only the
dated table, which those landings did not carry.

Every fixture is synthesised here from neutral parts. No design, PDK, vendor or
IP-model identifier appears anywhere in this file.

Run: python3 -m pytest programs/tests/test_issue2150_dated_decision_table.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase1_expert_parse_track as T          # noqa: E402
import expert_decision_table as DT             # noqa: E402
import _path_layout as _pl                     # noqa: E402
import _progress_run as _pr                    # noqa: E402


_INPUT_DOC = """# Block specification

The block exposes four addressable words and one sideband strobe. Word zero
reports the busy flag; word three latches the trim code.
"""

_L1 = {"doc_id": "L1", "fields": {"identity": {"module": "WIDGET",
                                               "strobe": "sideband_i"}}}
_L2 = {"doc_id": "L2", "fields": {"rules": ["a write while busy is ignored"]}}
_L4 = {"doc_id": "L4", "records": [{"name": "STATUS_WORD", "offset": "0x0"},
                                   {"name": "TRIM_WORD", "offset": "0xc"}]}


def _project(tmp_path, name="proj", layers=None, gen_version="1.0.0"):
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    for stem, blob in (layers or {"L1_DATASHEET": _L1, "L2_FRS": _L2,
                                  "L4_REGMAP": _L4}).items():
        b = dict(blob)
        b["_generator"] = {"plugin": "vibe-ic", "plugin_version": gen_version}
        (p / "phase1" / "generated_docs" / f"{stem}.json").write_text(
            json.dumps(b))
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
    cp = _pr.run([sys.executable,
                  str(_PROGRAMS / "phase1_expert_parse_track.py"),
                  str(project)], capture_output=True, text=True, env=env)
    return cp.returncode, cp.stdout, cp.stderr


def _report(project: Path):
    return json.loads(
        _pl.report_path(project, "phase1/expert_parse_track.json").read_text())


def _rules(rep, prefix):
    return sorted(f["rule"] for f in rep["findings"]
                  if f["rule"] == prefix or f["rule"].startswith(prefix + "::"))


# ── ITEM 6: the DATED decision table ───────────────────────────────────────

def test_a_table_stamped_on_this_root_is_usable(tmp_path):
    """GREEN ARM."""
    p = _project(tmp_path)
    t = {"schema": DT.SCHEMA, "stamp": DT.stamp_for(p), "rows": {"a": 1}}
    v = DT.applicability(p, t)
    assert v["state"] == DT.USABLE and v["state"] in DT.APPLICABLE


def test_a_table_stamped_on_another_root_is_refused(tmp_path):
    """RED ARM. Every OWNER cell is a claim about different documents."""
    p = _project(tmp_path)
    t = {"stamp": {**DT.stamp_for(p), "phase1_root_digest": "0" * 64}}
    v = DT.applicability(p, t)
    assert v["state"] == DT.ROOT_MISMATCH and v["state"] not in DT.APPLICABLE


def test_an_undated_table_is_refused(tmp_path):
    """An undated table that happens to fit is indistinguishable from one that
    does not, and the difference is every cell in it."""
    p = _project(tmp_path)
    assert DT.applicability(p, {"rows": {}})["state"] == DT.NOT_STAMPED


def test_newer_and_older_are_DIFFERENT_refusals(tmp_path):
    """The measured case is an OLDER artefact under a NEWER table, where the
    remedy is to re-run Phase 1 — not to recompute the table. One word for both
    would send the reader to the wrong repair."""
    p = _project(tmp_path, gen_version="1.17.38")
    newer = DT.applicability(p, {"stamp": {"phase1_root_digest": "0" * 64,
                                           "plugin_version": "1.18.81"}})
    older = DT.applicability(p, {"stamp": {"phase1_root_digest": "0" * 64,
                                           "plugin_version": "1.10.0"}})
    assert newer["state"] == DT.TABLE_NEWER
    assert older["state"] == DT.TABLE_OLDER
    assert "re-run Phase 1" in newer["reason"]
    assert "recompute the table" in older["reason"]


def test_the_digest_moves_with_CONTENT_and_with_MEMBERSHIP(tmp_path):
    """A root with the same files and different content is a different root,
    and so is one with an extra layer. Either alone would let a table be
    applied to a root it never saw."""
    a = _project(tmp_path, "a")
    b = _project(tmp_path, "b")
    assert DT.phase1_root_digest(a) == DT.phase1_root_digest(b)
    (b / "phase1" / "generated_docs" / "L2_FRS.json").write_text(
        json.dumps({"doc_id": "L2", "fields": {"rules": ["changed"]}}))
    assert DT.phase1_root_digest(a) != DT.phase1_root_digest(b)
    c = _project(tmp_path, "c")
    (c / "phase1" / "generated_docs" / "L7_TEST_DEBUG.json").write_text("{}")
    assert DT.phase1_root_digest(a) != DT.phase1_root_digest(c)


def test_a_project_with_no_layers_is_NOT_an_empty_root(tmp_path):
    """`None`, never a digest of nothing: "I could not look" is not "I looked
    and it was empty", and a digest of nothing would COMPARE EQUAL between two
    unrelated empty projects."""
    p = tmp_path / "bare"
    (p / "input").mkdir(parents=True)
    assert DT.phase1_root_digest(p) is None
    assert DT.applicability(p, {"stamp": {"phase1_root_digest": "x"}})["state"] \
        == DT.NO_ARTEFACT


def test_the_artefact_version_is_read_from_the_layers(tmp_path):
    """Not from the running plugin: the question is what WROTE this artefact,
    and the running version answers a different one."""
    p = _project(tmp_path, gen_version="1.17.38")
    assert DT.artefact_plugin_version(p) == "1.17.38"


def test_one_hand_staged_layer_cannot_re_date_the_root(tmp_path):
    """The majority version wins, so a single file copied in from another run
    does not silently move the artefact's date."""
    p = _project(tmp_path, gen_version="1.17.38")
    odd = json.loads(
        (p / "phase1" / "generated_docs" / "L2_FRS.json").read_text())
    odd["_generator"]["plugin_version"] = "9.9.9"
    (p / "phase1" / "generated_docs" / "L2_FRS.json").write_text(json.dumps(odd))
    assert DT.artefact_plugin_version(p) == "1.17.38"


def test_a_stale_table_beside_the_pack_becomes_a_NAMED_finding(tmp_path):
    """RED ARM, end to end through the track."""
    p = _project(tmp_path)
    _answer(p, [{"id": "e", "layer": "L4_REGMAP", "field_path": "records",
                 "requirement": "r", "evidence": ["i"],
                 "expected_tokens": ["STATUS_WORD"]}])
    (_pack_dir(p) / "decision_table.json").write_text(json.dumps(
        {"stamp": {"phase1_root_digest": "0" * 64, "plugin_version": "9.9.9"},
         "rows": {"e": "re-point"}}))
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, T.RULE_DECISION_TABLE_NOT_APPLICABLE) == [
        T.RULE_DECISION_TABLE_NOT_APPLICABLE], rep["findings"]
    assert rep["decision_table"]["present"] is True
    assert rep["decision_table"]["applied"] is False
    assert rep["decision_table"]["state"] == DT.TABLE_NEWER


def test_a_current_table_beside_the_pack_raises_no_finding(tmp_path):
    """GREEN ARM — same code path, a table stamped on this root."""
    p = _project(tmp_path)
    _answer(p, [{"id": "e", "layer": "L4_REGMAP", "field_path": "records",
                 "requirement": "r", "evidence": ["i"],
                 "expected_tokens": ["STATUS_WORD"]}])
    (_pack_dir(p) / "decision_table.json").write_text(json.dumps(
        {"stamp": DT.stamp_for(p), "rows": {"e": "re-point"}}))
    _run_track(p)
    rep = _report(p)
    assert _rules(rep, T.RULE_DECISION_TABLE_NOT_APPLICABLE) == []
    assert rep["decision_table"]["applied"] is True


def test_no_table_is_recorded_as_no_table_not_as_a_clean_one(tmp_path):
    """The third state. `present: False` with a reason, never a silent absence
    that reads like a table that passed."""
    p = _project(tmp_path)
    _answer(p, [{"id": "e", "layer": "L4_REGMAP", "field_path": "records",
                 "requirement": "r", "evidence": ["i"],
                 "expected_tokens": ["STATUS_WORD"]}])
    _run_track(p)
    dt = _report(p)["decision_table"]
    assert dt["present"] is False
    assert "no decision table" in dt["reason"]
    assert _rules(_report(p), T.RULE_DECISION_TABLE_NOT_APPLICABLE) == []
