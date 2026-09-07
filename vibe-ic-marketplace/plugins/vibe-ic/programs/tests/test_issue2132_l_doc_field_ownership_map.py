#!/usr/bin/env python3
"""vibe-ic#2132 — is there already a layer field for this fact, or is it a gap?

WHY THIS INSTRUMENT EXISTS
--------------------------
Thirty expert-track expectations were filed as "the layer contract has no
field for the fact". Answering that per row, by hand, is how the same
question gets a different answer each time — and one of those answers is
"write a second producer for a field that already exists", which this lane
began doing before the producer basis was measured.

THE TWO BASES, AND WHY BOTH ARE TESTED SEPARATELY
  * INSTANCE — a layer carries the name as a KEY in an emitted L-doc.
  * PRODUCER — a program writes a field of that name into a document.

A field nothing has yet had occasion to emit is INSTANCE-unowned and
PRODUCER-owned; a field a design happened to state but no program writes is
the reverse. Collapsing them to one boolean loses exactly the case that
matters, so the report keeps them apart and so do these tests.

NEGATIVE CONTROLS. Each behaviour test below has a paired assertion that
fails if the discrimination is removed:
  * a VALUE that equals the name is not a declaration (drop the key/value
    distinction and `test_a_value_is_not_a_declaration` goes red);
  * a name inside a docstring or a comment is not a producer (replace the
    `ast` walk with a grep and `test_a_name_in_prose_is_not_a_producer`
    goes red);
  * a dynamic subscript key is refused (admit it and
    `test_a_dynamic_key_is_not_a_producer` goes red);
  * `related_names` never counts as ownership (fold it in and
    `test_a_related_name_is_a_lead_and_never_ownership` goes red).

chip-AGNOSTIC: every fixture is synthesised neutral text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import l_doc_field_ownership_map as MAP        # noqa: E402


def _project(tmp_path: Path, docs: dict) -> Path:
    root = tmp_path / "proj"
    gen = root / "phase1" / "generated_docs"
    gen.mkdir(parents=True)
    for name, blob in docs.items():
        (gen / name).write_text(json.dumps(blob), encoding="utf-8")
    return root


# ─────────────────────────────────────────────────────────────────────
# INSTANCE basis
# ─────────────────────────────────────────────────────────────────────

def test_a_layer_that_carries_the_key_owns_the_name(tmp_path: Path) -> None:
    project = _project(tmp_path, {
        "L1_DATASHEET.json": {"fields": {"endianness": "little"}},
        "L9_INTEGRATION_SPEC.json": {"ports": []},
    })
    decl = MAP.layer_declarations(project)
    assert MAP.owners_of(decl, "endianness") == ["L1"]
    # ... and a name no layer carries is owned by none.
    assert MAP.owners_of(decl, "inter_module_signals") == []


def test_a_value_is_not_a_declaration(tmp_path: Path) -> None:
    """The design saying the word is not the layer declaring the field."""
    project = _project(tmp_path, {
        "L2_FRS.json": {"description": "the alerts are recoverable and fatal"},
    })
    assert MAP.owners_of(MAP.layer_declarations(project), "alerts") == []


def test_a_separator_difference_is_not_a_different_name(
        tmp_path: Path) -> None:
    project = _project(tmp_path, {
        "L9_INTEGRATION_SPEC.json": {"interModuleSignals": []}})
    decl = MAP.layer_declarations(project)
    assert MAP.owners_of(decl, "inter_module_signals") == ["L9"]
    # But a name that differs by a real character is a different name.
    assert MAP.owners_of(decl, "inter_module_signal") == []


def test_no_readable_l_doc_is_not_measured_never_an_empty_map(
        tmp_path: Path) -> None:
    """'Could not read it' is not 'read it and nothing is declared'."""
    empty = tmp_path / "bare"
    empty.mkdir()
    rep = MAP.build(empty)
    assert rep["status"] == "NOT_MEASURED", rep
    assert "resolved" not in rep


def test_an_unparsable_l_doc_is_named_not_silently_skipped(
        tmp_path: Path) -> None:
    project = _project(tmp_path, {"L1_DATASHEET.json": {"a": 1}})
    (project / "phase1" / "generated_docs" / "L2_FRS.json").write_text(
        "{not json", encoding="utf-8")
    rep = MAP.build(project)
    assert rep["status"] == "OK"
    assert any(r.startswith("L2_FRS.json") for r in rep["unreadable"]), rep


# ─────────────────────────────────────────────────────────────────────
# PRODUCER basis
# ─────────────────────────────────────────────────────────────────────

def _mini(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "progs"
    d.mkdir(exist_ok=True)
    (d / "emitter_x.py").write_text(body, encoding="utf-8")
    return d


def test_a_key_constant_and_a_literal_subscript_are_producers(
        tmp_path: Path) -> None:
    prod = MAP._producer_names(_mini(tmp_path, '''
_WIDGET_KEY = "widget_target"

def run(fields):
    fields["second_field"] = 1
    return fields
'''))
    assert prod.get("widget_target") == ["emitter_x.py"]
    assert prod.get("second_field") == ["emitter_x.py"]


def test_a_name_in_prose_is_not_a_producer(tmp_path: Path) -> None:
    """A grep would call this a producer; the AST walk does not."""
    prod = MAP._producer_names(_mini(tmp_path, '''
"""This module discusses widget_target at length.

    fields["widget_target"] = ...   # <- an example inside a docstring
"""
# widget_target is also mentioned in this comment
X = "widget_target is only a string value here"
'''))
    assert "widget_target" not in prod, prod


def test_a_dynamic_key_is_not_a_producer(tmp_path: Path) -> None:
    """An unresolvable key would have to be guessed, and a guess is a
    fabricated declaration."""
    prod = MAP._producer_names(_mini(tmp_path, '''
def run(fields, name):
    fields[name] = 1
    other = {}
    other["not_a_layer_field"] = 2
'''))
    assert prod == {}, prod


def test_the_two_bases_are_reported_separately(tmp_path: Path) -> None:
    project = _project(tmp_path, {
        "L1_DATASHEET.json": {"fields": {"instance_only": 1}}})
    producers = {"producer_only": ["emitter_x.py"]}
    decl = MAP.layer_declarations(project)

    inst = MAP.resolve_field_path(decl, "instance_only", producers)
    assert inst["leaf_layers_declaring"] == ["L1"]
    assert inst["leaf_written_by"] == []
    assert inst["segments_unowned_on_both_bases"] == []

    prod = MAP.resolve_field_path(decl, "producer_only", producers)
    assert prod["leaf_layers_declaring"] == []
    assert prod["leaf_written_by"] == ["emitter_x.py"]
    # Owned on EITHER basis is not a schema gap.
    assert prod["segments_unowned_on_both_bases"] == []

    gap = MAP.resolve_field_path(decl, "neither.side", producers)
    assert gap["segments_unowned_on_both_bases"] == ["neither", "side"]


def test_a_related_name_is_a_lead_and_never_ownership(
        tmp_path: Path) -> None:
    """`area` must NOT be owned by `synthesis_area_budget` — it must be led
    to it. This is the distinction that stopped a duplicate producer."""
    project = _project(tmp_path, {
        "L19_CONSTRAINTS_PDK.json": {"fields": {"die_area_budget_um": None}}})
    decl = MAP.layer_declarations(project)
    producers = {"synthesis_area_budget": ["_tapeout_declaration.py"]}
    res = MAP.resolve_field_path(decl, "area", producers)

    assert res["leaf_layers_declaring"] == []
    assert res["leaf_written_by"] == []
    assert res["segments_unowned_on_both_bases"] == ["area"]

    lead = res["segments"][0]["related_names_LEAD_ONLY"]
    assert "synthesis_area_budget" in lead["written_by_programs"], lead
    assert "L19.die_area_budget_um" in lead["in_layers"], lead


def test_a_related_name_needs_a_whole_word_match(tmp_path: Path) -> None:
    """`io` must not lead to `version`, or the lead is noise."""
    project = _project(tmp_path, {"L1_DATASHEET.json": {"version": 1}})
    res = MAP.resolve_field_path(MAP.layer_declarations(project), "io", {})
    assert res["segments"][0]["related_names_LEAD_ONLY"]["in_layers"] == []


# ─────────────────────────────────────────────────────────────────────
# The report as a whole
# ─────────────────────────────────────────────────────────────────────

def test_the_report_states_its_basis_so_it_cannot_be_read_as_a_schema(
        tmp_path: Path) -> None:
    project = _project(tmp_path, {"L1_DATASHEET.json": {"a": 1}})
    rep = MAP.build(project, ["a.b"])
    assert "INSTANCE" in rep["basis"] and "PRODUCER" in rep["basis"], rep
    assert rep["producer_field_count"] >= 1
    assert rep["resolved"][0]["field_path"] == "a.b"


def test_the_shipped_tree_names_the_field_this_lane_nearly_duplicated(
        ) -> None:
    """The measurement the commit rests on, re-run as a test."""
    prod = MAP._producer_names(_PROGRAMS)
    assert prod.get("synthesis_area_budget") == ["_tapeout_declaration.py"], (
        "the area field this lane nearly re-implemented is no longer "
        f"attributed to its producer: {prod.get('synthesis_area_budget')}")
