"""A case the design declared it does not have is not demanded. R-0915-102(1).

MEASURED on subservient x gf180mcuD as a DIE, FRONT DOOR, run r48 (lane
icsub2, host 8HD-4, tree 41d3b39b8, image 0.3.67): the Step-4 gate blocked
with "only 1 of 10 declared L10 case(s) EXECUTED their own oracle". THREE of
those ten are stated CONDITIONALLY by the input's own verification-plan table

    (若 Plugin 選 M) Mul/Div 指令
    (若 Plugin 選 Zicsr) CSR access + timer IRQ
    (若 Plugin 選 C) 16-bit compressed 指令

and the design's own `plugin_output/declaration.json` records
`isa_extensions: ["I", "Zifencei"]` -- none of the three. The run was being
asked to execute three cases the design had declared it does not have.

THE BASIS IS DECLARED, NEVER SCANNED (R-0915-15). Phase 1 attaches
`applies_when` where the prose already becomes a row; the gate compares two
DECLARED documents and reads no prose at all. Measured on r48's own data:
7 applicable, 3 design-declared N/A (M, Zicsr, C).

Both directions: no declaration, no selection field, an unselected-option case
whose option the design DID pick, and a case with no condition at all each
leave the denominator exactly as it was.
"""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpu_functional_oracle_waiver_check as G  # noqa: E402
import phase1_doc_one_shot_runner as P  # noqa: E402


# ── Phase 1: the condition becomes DATA ───────────────────────────────────
def test_an_option_row_carries_its_own_condition():
    assert P._applies_when("(若 Plugin 選 M) Mul/Div 指令") == {
        "option": "M", "stated": "(若 Plugin 選 M)"}
    assert P._applies_when("(若 Plugin 選 Zicsr) CSR access")["option"] == "Zicsr"
    assert P._applies_when("(if the plugin selects M) mul/div")["option"] == "M"


def test_a_row_with_no_condition_carries_none():
    for t in ("整套 RV32I 指令(40+ 條)單元測試",   # a parenthesis, not a condition
              "Zifencei 指令",
              "blinky.hex",
              "Reset assert 中 SRAM 內容保留",
              ""):
        assert P._applies_when(t) is None


# ── the gate: two DECLARED documents, no prose ────────────────────────────
def _rows():
    return [{"name": "zifencei", "kind": "functional_vector",
             "applies_when": {"option": "Zifencei", "stated": "(若 選 Zifencei)"}},
            {"name": "plugin_m_mul_div", "kind": "functional_vector",
             "applies_when": {"option": "M", "stated": "(若 Plugin 選 M)"}},
            {"name": "blinky_hex", "kind": "functional_vector"}]


def test_an_unselected_option_is_not_demanded():
    app, na = G.split_design_declared_na(_rows(), frozenset({"i", "zifencei"}))
    assert [r["name"] for r in app] == ["zifencei", "blinky_hex"]
    assert [r["name"] for r in na] == ["plugin_m_mul_div"]


def test_an_option_the_design_did_pick_is_still_demanded():
    app, na = G.split_design_declared_na(
        _rows(), frozenset({"i", "zifencei", "m"}))
    assert na == []
    assert len(app) == 3


def test_no_declaration_decides_nothing(tmp_path):
    """Not an empty selection — no statement. Nothing may be narrowed."""
    assert G.design_selected_options(tmp_path) is None
    app, na = G.split_design_declared_na(_rows(), None)
    assert na == [] and len(app) == 3


def test_a_declaration_without_a_selection_field_decides_nothing(tmp_path):
    d = tmp_path / "plugin_output"
    d.mkdir()
    (d / "declaration.json").write_text(json.dumps({"top_module": "x"}))
    assert G.design_selected_options(tmp_path) is None


def test_an_unreadable_declaration_decides_nothing(tmp_path):
    d = tmp_path / "plugin_output"
    d.mkdir()
    (d / "declaration.json").write_text("{ not json")
    assert G.design_selected_options(tmp_path) is None


def test_the_selection_is_read_from_the_design_declaration(tmp_path):
    d = tmp_path / "plugin_output"
    d.mkdir()
    (d / "declaration.json").write_text(
        json.dumps({"isa_extensions": ["I", "Zifencei"]}))
    assert G.design_selected_options(tmp_path) == frozenset({"i", "zifencei"})


def test_the_narrowing_is_disclosed_case_by_case(tmp_path):
    (tmp_path / "plugin_output").mkdir()
    (tmp_path / "plugin_output" / "declaration.json").write_text(
        json.dumps({"isa_extensions": ["I", "Zifencei"]}))
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(
        json.dumps({"test_cases": _rows()}, ensure_ascii=False))
    out = G._design_declared_na_disclosure(tmp_path)
    assert out["decided"] is True
    assert out["design_selected"] == ["i", "zifencei"]
    assert [c["case"] for c in out["cases"]] == ["plugin_m_mul_div"]
    assert out["cases"][0]["option"] == "M"
    assert out["cases"][0]["stated"] == "(若 Plugin 選 M)"
    assert "not a waiver" in out["note"] and "not a pass" in out["note"]


def test_an_undecided_narrowing_says_so(tmp_path):
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(json.dumps({"test_cases": _rows()}))
    out = G._design_declared_na_disclosure(tmp_path)
    assert out["decided"] is False
    assert "no selection" in out["why"]


def test_the_denominator_drops_only_the_declared_na(tmp_path):
    (tmp_path / "plugin_output").mkdir()
    (tmp_path / "plugin_output" / "declaration.json").write_text(
        json.dumps({"isa_extensions": ["I", "Zifencei"]}))
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(
        json.dumps({"test_cases": _rows()}, ensure_ascii=False))
    assert G._declared_l10_case_ids(tmp_path) == ["zifencei", "blinky_hex"]


def test_without_a_declaration_the_denominator_is_unchanged(tmp_path):
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(json.dumps({"test_cases": _rows()}))
    assert G._declared_l10_case_ids(tmp_path) == [
        "zifencei", "plugin_m_mul_div", "blinky_hex"]
