"""R-0929-UNSELECTED-FEATURES — a case for an option the design declares it
did NOT select is design-declared NOT_APPLICABLE, in every consumer.

MEASURED on subservient x gf180mcuD, IC path (DIE), front door v5
(8HD-3, subic3_ic_v5_20260929): L10 states three cases conditionally --
"(若 Plugin 選 M) Mul/Div 指令" and its Zicsr and C siblings, each carrying
`applies_when` -- and the design's own `plugin_output/declaration.json`
records `isa_extensions: ["I", "Zifencei"]` (L2: "是否包含可選 SERV
extensions(C / M / Zicsr) — Plugin 在 declaration.json 聲明"). The three
consumers disagreed about those rows:

  * Step 4 (`cpu_functional_oracle_waiver_check`) narrowed them away as
    design-declared N/A;
  * the L10 table (`l10_tb_conformance_check`) booked them
    WAIVED-DEFERRED `cap:conditional_feature_undeclared` ("declaration.json
    does not confirm as selected"), so Step 4 read PASS_WITH_WAIVERS forever;
  * Step 5 (`full_stack_functional_tb`) booked them `no_oracle`, owed with
    no owner -- since closed by the landed Step-5 bar
    (`unexecuted_disposition` -> `design_declared_na`, non-blocking), which
    these tests treat as the contract the L10 table must agree with.

The ruling: design-declared NOT_APPLICABLE -- not waived, not owed, not a
pass -- and a declaration that SELECTS the option keeps the row owed. The
three consumers must agree. chip-AGNOSTIC: the option token is whatever the
case's own `applies_when` names; the selection is whatever the declaration
lists.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
_TESTS = Path(__file__).resolve().parent
for _p in (PROGRAMS, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import l10_tb_conformance_check as l10gate  # noqa: E402
import cpu_functional_oracle_waiver_check as step4gate  # noqa: E402
import _l10_execution as execution  # noqa: E402

#: Literals, not the programs' constants, so a tree without the fix answers
#: these questions wrongly instead of raising AttributeError.
NA_ROW = "not_applicable"          # l10_tb_conformance_check row status
_SRC = "input/docs/L7_verification_plan.md (verification-plan table)"


def _conditional(name, option, text):
    stated = f"(若 Plugin 選 {option})"
    return {"name": name, "kind": "functional_vector",
            "stimulus": f"{stated} {text}", "expected": "PASS",
            "evidence": _SRC,
            "applies_when": {"option": option, "stated": stated,
                             "source": _SRC}}


#: The three conditional rows exactly as the v5 L10 carries them.
CONDITIONAL = [
    _conditional("plugin_m_mul_div", "M", "Mul/Div 指令"),
    _conditional("plugin_zicsr_csr_access_timer_irq", "Zicsr",
                 "CSR access + timer IRQ"),
    _conditional("plugin_c_16_bit_compressed", "C", "16-bit compressed 指令"),
]
ALWAYS = {"name": "zifencei", "kind": "functional_vector",
          "stimulus": "FENCE.I 指令", "expected": "PASS"}
V5_DECLARATION = {"top_module": "subservient",
                  "isa_extensions": ["I", "Zifencei"]}


def _l10_project(tmp_path, declaration, cases=None):
    cases = [ALWAYS, *CONDITIONAL] if cases is None else cases
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    l10 = gd / "L10_TEST_CASES.json"
    l10.write_text(json.dumps({"test_cases": cases}, ensure_ascii=False))
    tb = tmp_path / "phase2" / "stage1" / "sim" / "tb"
    tb.mkdir(parents=True)
    (tb / "tb_dummy.v").write_text("module tb_dummy;\nendmodule\n")
    work = tmp_path / "phase2" / "stage1" / "sim" / "work"
    work.mkdir(parents=True)
    (work / "summary.txt").write_text("")
    if declaration is not None:
        po = tmp_path / "plugin_output"
        po.mkdir(parents=True)
        (po / "declaration.json").write_text(json.dumps(declaration))
    execution.write_record(tmp_path, l10, [
        {"id": "zifencei", "verdict": "PASS", "sim_executed": True}],
        producer="test")
    return l10, tb, work / "summary.txt"


def _run_l10(tmp_path, declaration, cases=None):
    l10, tb, summary = _l10_project(tmp_path, declaration, cases)
    out = tmp_path / "l10.json"
    rc = l10gate.main(["--l10", str(l10), "--tb-dir", str(tb),
                       "--summary", str(summary), "--out", str(out),
                       "--project", str(tmp_path)])
    data = json.loads(out.read_text())
    return rc, data, {r["id"]: r for r in data["results"]}


# ---------------------------------------------------------------------------
# the L10 table
# ---------------------------------------------------------------------------
def test_l10_table_books_unselected_options_design_declared_na(tmp_path):
    rc, data, rows = _run_l10(tmp_path, V5_DECLARATION)
    for case in CONDITIONAL:
        row = rows[case["name"]]
        assert row["status"] == NA_ROW, row
        assert row["waived"] is False and row["pass"] is False
        assert row["capability_gap"] is None
        assert row["reason_class"] == "DESIGN_DECLARED_NA"
    assert data["waived"] == 0, data["results"]
    assert data["not_executed"] == 0
    assert {r["case"]: r["option"] for r in data.get("design_declared_na", [])} == {
        "plugin_m_mul_div": "M",
        "plugin_zicsr_csr_access_timer_irq": "Zicsr",
        "plugin_c_16_bit_compressed": "C"}
    assert all(r["design_selected"] == ["i", "zifencei"]
               for r in data.get("design_declared_na", []))
    # the only owed case executed and passed: nothing waived, nothing owed
    assert rc == 0


def test_a_declaration_that_selects_the_option_keeps_the_row_owed(tmp_path):
    rc, data, rows = _run_l10(
        tmp_path, {"isa_extensions": ["I", "Zifencei", "M"]})
    assert rows["plugin_m_mul_div"]["status"] != \
        NA_ROW
    assert rows["plugin_m_mul_div"]["status"] == execution.NOT_EXECUTED
    assert rc == 1, "a selected option is owed: not executed blocks Step 4"
    # its unselected siblings stay design-declared N/A
    assert rows["plugin_c_16_bit_compressed"]["status"] == \
        NA_ROW


def test_no_selection_statement_decides_nothing(tmp_path):
    """No declaration (or one without a selection field) is not an empty
    selection: the rows keep their earlier disposition."""
    rc, data, rows = _run_l10(tmp_path, {"top_module": "subservient"})
    assert data.get("design_declared_na", []) == []
    for case in CONDITIONAL:
        assert rows[case["name"]]["status"] == "waived"
        assert rows[case["name"]]["capability_gap"] == \
            l10gate.CAP_CONDITIONAL_FEATURE_UNDECLARED
    assert rc == 3


def test_a_case_with_no_applies_when_is_never_narrowed(tmp_path):
    bare = [{k: v for k, v in c.items() if k != "applies_when"}
            for c in CONDITIONAL]
    _rc, data, rows = _run_l10(tmp_path, V5_DECLARATION, [ALWAYS, *bare])
    assert data.get("design_declared_na", []) == []
    assert all(rows[c["name"]]["status"] != NA_ROW
               for c in bare)


# ---------------------------------------------------------------------------
# Step 5 -- the landed Step-5 bar (R-0929-STEP5-BAR, next/claude-step5-datapath)
# owns the Step-5 disposition: `full_stack_functional_tb.unexecuted_disposition`
# books an unselected option `design_declared_na`, non-blocking. It is the
# contract here; these tests pin that the L10 table now agrees with it.
# ---------------------------------------------------------------------------
import full_stack_functional_tb as fsf  # noqa: E402


def _step5(tmp_path, declaration, case):
    proj = tmp_path / "s5"
    proj.mkdir(parents=True, exist_ok=True)
    if declaration is not None:
        (proj / "plugin_output").mkdir(exist_ok=True)
        (proj / "plugin_output" / "declaration.json").write_text(
            json.dumps(declaration))
    return fsf.unexecuted_disposition(proj, case, "processor_cpu", False, "top")


@pytest.mark.parametrize("declaration", [
    V5_DECLARATION,
    {"isa_extensions": ["I", "Zifencei", "M"]},
    {"isa_extensions": ["I", "Zifencei", "M", "Zicsr", "C"]},
    {"top_module": "subservient"},
])
def test_step4_the_l10_table_and_step5_agree(tmp_path, declaration):
    _rc, data, _rows = _run_l10(tmp_path / "l10", declaration)
    l10_na = {r["case"] for r in data.get("design_declared_na", [])}
    disclosure = step4gate._design_declared_na_disclosure(tmp_path / "l10")
    step4_na = {c["case"] for c in disclosure.get("cases") or []}
    step5_na = {c["name"] for c in CONDITIONAL
                if _step5(tmp_path / c["name"], declaration, c)["disposition"]
                == fsf.DISP_NOT_APPLICABLE}
    assert l10_na == step4_na == step5_na


def test_step5_design_declared_na_is_non_blocking_and_cites_the_basis(tmp_path):
    d = _step5(tmp_path, V5_DECLARATION, CONDITIONAL[0])
    assert d["disposition"] == fsf.DISP_NOT_APPLICABLE
    assert d["blocking"] is False
    assert d["basis"]["option"] == "M"
    assert d["basis"]["design_selected"] == ["i", "zifencei"]
