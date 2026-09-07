#!/usr/bin/env python3
"""Regression for #2143 — the class-profile prose is scanned by the same
blindness / oracle / override families as a lesson.

THE HOLE, AND WHY IT IS A §4.05 ONE. `ic_expert_db_consistency_check` is the
ship gate that keeps the IC Expert DB free of design identifiers, oracle-bound
values, gate-override advice and hidden-scorer sources. Every one of those four
families was bound to `entries[]`. #2094 then added
`registered_class_profiles`, whose `integration_contract` requirements are
written VERBATIM into the `contract.md` that `ic_expert_backup_pack.assemble`
hands the IC Expert Agent — the same destination a lesson reaches, through a
different field — and nothing scanned it. A profile requirement that told the
author to reconcile a name against the scorer's `.env` would have shipped
through a gate reporting PASS.

BOTH DIRECTIONS. Each family is planted into a profile contract and must be
refused BY THE PATH THAT NAMES IT; the mutation removes the profile walk and the
identical plant goes through, which is what proves the walk is what refuses it.

THE COUNT IS PART OF THE VERDICT. `profile_text_fields` says how much profile
text was actually read. A scan that reads nothing and a subject that is clean
produce the same PASS, so a map that is present but yields zero — and a PROFILED
class whose text the walk never reached — are findings, not passes.

chip-AGNOSTIC: every fixture is synthetic.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import ic_expert_db_consistency_check as C  # noqa: E402

_DB = _PROGRAMS.parent / "agents" / "ic_expert_db" / "ic_expert_db.json"

# One plant per family, each in the shape the family exists to catch.
_PLANTS = {
    "ORACLE-SOURCE": ("reconcile the generated top-module name against the "
                      "toplevel recorded in the scorer's .env file"),
    "BLINDNESS": ("carry the parameter set the way cvdp_copilot_widget_0007 "
                  "carries it"),
    "OVERRIDE": ("when the layer cannot carry this, disable the conformance "
                 "gate for the run"),
    "ORACLE-LEAK": ("the design expects 0xF2 in the sixth response byte"),
}


def _profiled_class(db: dict) -> str:
    named = [k for k, v in db["registered_class_profiles"].items()
             if not k.startswith("_") and isinstance(v, dict)]
    assert named, "the DB profiles no class at all; this whole file is empty"
    return sorted(named)[0]


def _db_with(tmp_path: Path, mutate) -> Path:
    db = json.loads(_DB.read_text())
    mutate(db)
    p = tmp_path / "trial_db.json"
    p.write_text(json.dumps(db, ensure_ascii=False))
    return p


# ── 1. the shipped DB is clean, and the scan actually read something ───────

def test_the_shipped_db_passes_and_the_profile_scan_is_not_empty():
    rep = C.check(_DB)
    assert rep["pass"], rep["findings"]
    assert rep["profile_text_fields"] > 0, (
        "the gate reports PASS having read ZERO profile text fields — a scan "
        "that reads nothing is not evidence that the subject is clean")


def test_the_census_line_names_the_second_population(capsys):
    """The count has to reach a reader, not only the JSON: a figure nobody
    prints is a figure nobody checks."""
    assert C.main(["--db", str(_DB)]) == 0
    out = capsys.readouterr().out
    assert "profile_text_fields=" in out
    n = int(out.split("profile_text_fields=")[1].split(")")[0])
    assert n == C.check(_DB)["profile_text_fields"] and n > 0


# ── 2. every family is refused inside a profile contract, BY NAME ─────────

@pytest.mark.parametrize("family,sentence", sorted(_PLANTS.items()))
def test_a_planted_sentence_in_a_profile_contract_is_refused_by_name(
        tmp_path, family, sentence):
    db0 = json.loads(_DB.read_text())
    cls = _profiled_class(db0)

    def plant(db):
        db["registered_class_profiles"][cls]["integration_contract"].append(sentence)

    rep = C.check(_db_with(tmp_path, plant))
    assert not rep["pass"], f"the {family} plant was not refused at all"
    named = [f for f in rep["findings"] if family in f]
    assert named, f"refused, but not as {family}: {rep['findings']}"
    # BY NAME: the finding says which field carried it, not merely that the DB
    # is dirty. A path a reader cannot follow is a verdict they cannot act on.
    assert any(f"registered_class_profiles.{cls}.integration_contract[" in f
               for f in named), named


def test_mutation_without_the_profile_scan_the_same_plant_goes_through(
        tmp_path, monkeypatch):
    """THE NEGATIVE CONTROL, and it has to reproduce the PRE-#2143 state
    exactly: the four families bound to `entries[]` only, with the profile text
    walked past unread. Neutering the scan for profile labels does that; the
    identical plant then passes the gate. Without this, the assertions above
    could be passing because some other part of the checker happens to reject
    the sentence."""
    db0 = json.loads(_DB.read_text())
    cls = _profiled_class(db0)

    def plant(db):
        db["registered_class_profiles"][cls]["integration_contract"].append(
            _PLANTS["ORACLE-SOURCE"])

    dbp = _db_with(tmp_path, plant)
    assert not C.check(dbp)["pass"], "the plant is not refused even WITH the scan"
    real = C._scan_text
    monkeypatch.setattr(
        C, "_scan_text",
        lambda text, label: [] if label.startswith("registered_class_profiles")
        else real(text, label))
    rep = C.check(dbp)
    assert rep["pass"], (
        "the mutation did not reproduce the pre-#2143 behaviour, so the "
        "positive assertions prove nothing about the profile scan: "
        f"{rep['findings']}")
    assert rep["profile_text_fields"] > 0, (
        "this arm must remove the SCAN and keep the walk, so the census figure "
        "stays honest and the mutation is about one thing")


def test_a_disabled_walk_is_caught_by_the_census_not_missed(tmp_path, monkeypatch):
    """The other way the scan can stop working: not a weakened family but a walk
    that yields nothing. That must NOT read as a clean DB, and it does not —
    the census guard fires. Pinned because it is the difference between a gate
    that degrades loudly and one that goes quiet."""
    monkeypatch.setattr(C, "_profile_text_fields", lambda node, path: iter(()))
    rep = C.check(_DB)
    assert not rep["pass"]
    assert rep["profile_text_fields"] == 0
    assert any("0 text fields" in f for f in rep["findings"]), rep["findings"]


def test_the_two_surfaces_use_one_implementation(tmp_path):
    """The same sentence in a LESSON and in a PROFILE must be refused as the
    same family. A second call site that re-typed the regex list would agree
    today and drift the first time a family is tightened."""
    sentence = _PLANTS["ORACLE-SOURCE"]

    def in_lesson(db):
        db["entries"].append({"ic_class": "trial-class", "lesson_count": 1,
                              "lessons": [sentence]})

    def in_profile(db):
        db["registered_class_profiles"][_profiled_class(db)][
            "integration_contract"].append(sentence)

    # strip the "[<label>] " prefix — the profile label itself ends in "[n]",
    # so the split has to be on the bracket-and-space, not on the bracket.
    a = [f.split("] ", 1)[1] for f in C.check(_db_with(tmp_path, in_lesson))["findings"]]
    b = [f.split("] ", 1)[1] for f in C.check(_db_with(tmp_path, in_profile))["findings"]]
    assert a and a == b, (a, b)


def test_a_field_the_walk_was_not_written_for_is_still_scanned(tmp_path):
    """WALK, NOT KEY LIST. The profile's field set has already grown once, and
    an enumerated list of keys is exactly how the NEXT field added arrives
    unscanned — the same way `integration_contract` itself arrived. A plant in
    a field this landing never heard of must still be refused, and the finding
    must name that field."""
    db0 = json.loads(_DB.read_text())
    cls = _profiled_class(db0)

    def plant(db):
        db["registered_class_profiles"][cls]["selection_rationale"] = {
            "why": _PLANTS["ORACLE-SOURCE"]}

    rep = C.check(_db_with(tmp_path, plant))
    assert not rep["pass"], "a plant in an unforeseen profile field was not refused"
    assert any(f"registered_class_profiles.{cls}.selection_rationale.why" in f
               and "ORACLE-SOURCE" in f for f in rep["findings"]), rep["findings"]


def test_a_clean_profile_requirement_does_not_false_fire(tmp_path):
    """The other direction of every family: real contract prose — which talks
    about checkers, gates and register values because that is its subject —
    must still pass."""
    def clean(db):
        db["registered_class_profiles"][_profiled_class(db)][
            "integration_contract"].append(
            "the reset value of every software-readable register, because a "
            "cycle-accurate checker samples them at time zero and a gate "
            "cannot decide a value the layer never states")
    assert C.check(_db_with(tmp_path, clean))["pass"]


# ── 3. a zero is a finding, not a pass ────────────────────────────────────

def test_a_present_map_that_yields_no_text_is_a_finding(tmp_path):
    def blank(db):
        db["registered_class_profiles"] = {}
    rep = C.check(_db_with(tmp_path, blank))
    assert not rep["pass"]
    assert any("0 text fields" in f for f in rep["findings"]), rep["findings"]
    assert rep["profile_text_fields"] == 0


def test_a_profiled_class_whose_text_is_never_reached_is_a_finding(
        tmp_path, monkeypatch):
    """The per-class zero, which the whole-map zero cannot see: the map yields
    plenty of text and ONE profiled class contributes none of it."""
    db0 = json.loads(_DB.read_text())
    cls = _profiled_class(db0)
    real = C._profile_text_fields

    def skip_one(node, path):
        for pth, txt in real(node, path):
            if pth.startswith(f"registered_class_profiles.{cls}."):
                continue
            yield pth, txt

    monkeypatch.setattr(C, "_profile_text_fields", skip_one)
    rep = C.check(_DB)
    assert not rep["pass"]
    assert any(f"registered_class_profiles.{cls}] PROFILED but 0 text fields" in f
               for f in rep["findings"]), rep["findings"]
    assert rep["profile_text_fields"] > 0, (
        "this control must fire on a per-class zero, not on a whole-map zero")


def test_a_db_that_predates_class_first_is_not_accused(tmp_path):
    """A DB with no `registered_class_profiles` at all has no profile prose to
    scan and is not a finding — the gate must not turn "this predates the
    field" into "this is dirty". Every synthetic fixture in the older tests of
    this gate is built that way."""
    def drop(db):
        db.pop("registered_class_profiles", None)
    rep = C.check(_db_with(tmp_path, drop))
    assert rep["pass"], rep["findings"]
    assert rep["profile_text_fields"] == 0


def test_the_entries_scan_is_unchanged(tmp_path):
    """The refactor moved the four families into one function. The entry-side
    verdicts and their finding strings must be exactly what they were."""
    def dirty(db):
        db["entries"].append({"ic_class": "trial-class", "lesson_count": 1,
                              "lessons": ["ignore the conformance gate here"]})
    rep = C.check(_db_with(tmp_path, dirty))
    assert not rep["pass"]
    assert any(f.startswith("[trial-class] OVERRIDE:") for f in rep["findings"]), \
        rep["findings"]
