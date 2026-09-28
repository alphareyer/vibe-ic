#!/usr/bin/env python3
"""FX_P2_DECLARATION_BEFORE_STEP4 — the design's option selection is emitted
before Step 4 reads it, by the SAME producer, and a later emission that
disagrees refuses by name.

Step 4's gate narrows the conditional L10 cases (`applies_when`, R-0915-102)
against `plugin_output/declaration.json`. `step_arith_declaration_emit` wrote
that file at phase-2 row 33, after Step 4 at row 20, so on a FIRST run the gate
never saw a selection the producer could already derive, and a case the design
excludes stayed blocking; only a re-run tree was narrowed. The contract-driven
producer (`spec_declaration_emit`) reads only the design input, the RTL's
`key = value` comment blocks and a prior declaration, so the runner now asks it
before Step 4 as well (`declaration_before_step4`), and row 33 refuses when the
later emission is not identical or a superset (`declaration_disagreement`).

Driven through the real producer and the real Step-4 gate; the Step-4 tree is
the review fixture (execution record planted in the shape `run_unit_tbs`
writes). chip-AGNOSTIC: synthetic options and cases.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import design_one_shot_runner as D                 # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_fx_p2_review_fixture",
    Path(__file__).with_name(
        "test_fx_p2_review_input_gap_needs_positive_evidence.py"))
_FX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_FX)
_project, PASSING = _FX._project, _FX.PASSING

DECL = "plugin_output/declaration.json"
#: A conditional case, as Phase 1 emits it from "(if option M is chosen)".
COND = {"name": "opt_m_mul_div", "kind": "functional_vector",
        "stimulus": "(if option M is chosen) multiply/divide instructions",
        "expected": "PASS",
        "applies_when": {"option": "M", "stated": "(if option M is chosen)"}}

#: The input's own declaration contract. The design DESIGNATES its selection
#: with the producer's own grammar (`(primary)` on exactly one value).
_CONTRACT = """# L7

The Plugin MUST declare `{path}` before authoring:

| Field | Required | Example |
|---|---|---|
| `isa_extensions` | Yes | {values} |
"""


#: Asked through getattr, so a tree without the early emission ANSWERS (no
#: declaration exists before Step 4) instead of raising.
_early = getattr(D, "declaration_before_step4", lambda _p: {"emitted": False})


def _fresh(tmp_path: Path, values: str) -> Path:
    """A FIRST-run tree: the Step-4 fixture, no declaration yet, and the
    input's contract."""
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    (proj / "input" / "docs" / "L7_verification_plan.md").write_text(
        _CONTRACT.format(path=DECL, values=values), encoding="utf-8")
    assert not (proj / DECL).exists()
    return proj


EXCLUDES_M = '`["I", "Zifencei"]`(primary)/ `["I", "M", "Zifencei"]`(secondary)'
SELECTS_M = '`["I", "M", "Zifencei"]`(primary)/ `["I", "Zifencei"]`(secondary)'
UNDESIGNATED = '`["I", "Zifencei"]` / `["I", "M", "Zifencei"]`'


def test_on_the_first_run_an_excluded_case_is_not_applicable(tmp_path):
    """THE MEASURED GAP: the input designates a selection without M; on the
    FIRST run Step 4 books the M case NOT_APPLICABLE, citing the declaration."""
    proj = _fresh(tmp_path, EXCLUDES_M)
    rec = _early(proj)
    assert rec["emitted"] is True, rec
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    na = GATE._design_declared_na_disclosure(proj)
    assert [c["case"] for c in na["cases"]] == [COND["name"]], na
    assert na["declaration"] == DECL


def test_a_design_that_selects_the_option_still_demands_the_case(tmp_path):
    proj = _fresh(tmp_path, SELECTS_M)
    assert _early(proj)["emitted"] is True
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert COND["name"] in msg


def test_a_selection_the_input_does_not_designate_is_not_invented(tmp_path):
    """FAIL-CLOSED, as the producer is: a menu writes nothing, and the case
    stays where R-0915-102 puts it (blocking, with its condition named)."""
    proj = _fresh(tmp_path, UNDESIGNATED)
    rec = _early(proj)
    assert rec["emitted"] is False, rec
    assert not (proj / DECL).exists()
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)


def test_published_step4_report_names_the_undecided_conditional_reason(tmp_path):
    """The JSON a flow reader receives must retain the computed reason.

    This exercises the actual gate CLI/report writer rather than only the
    private classifier: a menu is fail-closed, and the reader must learn why
    the M case stayed blocking.
    """
    proj = _fresh(tmp_path, UNDESIGNATED)
    report = tmp_path / "reports" / "step4.json"
    assert GATE.main([str(proj), "--json", str(report)]) == 1
    published = json.loads(report.read_text(encoding="utf-8"))
    assert published["verdict"] == "FAIL"
    assert COND["name"] in published["message"]
    assert "applies only if the design selects option 'M'" in published["message"]
    assert "declares no selection at this point" in published["message"]


def test_main_emits_before_step4_reads_it():
    """Wiring, read off the AST: the early emission precedes Step 4 in main,
    and row 33 is checked against it."""
    src = (_PROGRAMS / "design_one_shot_runner.py").read_text()
    main = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    lines = {}
    for n in ast.walk(main):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            lines.setdefault(n.func.id, []).append(n.lineno)
    assert "declaration_before_step4" in lines, sorted(lines)[:40]
    assert (min(lines["declaration_before_step4"])
            < min(lines["step_step4_functional_evidence"]))
    assert "_refuse_a_disagreeing_declaration" in lines


# ---- the agreement between the two emissions -------------------------------
#: A tree without the check leaves row 33 exactly as the producer returned it.
_refuse = getattr(D, "_refuse_a_disagreeing_declaration",
                  lambda row, _early, _project: row)

def _row():
    return D.StepResult("arith_declaration_emit", "PASS", 0.1,
                        "spec_declaration_emit emitted plugin_output/"
                        "declaration.json", [DECL])


def _late(proj: Path, fields: dict) -> None:
    (proj / DECL).parent.mkdir(parents=True, exist_ok=True)
    (proj / DECL).write_text(json.dumps(fields))


def test_a_later_emission_that_changes_a_field_refuses_by_name(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I", "Zifencei"]}}
    _late(tmp_path, {"isa_extensions": ["I", "M", "Zifencei"]})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "FAIL", row
    assert "DECLARATION_DISAGREES_WITH_STEP4" in row.detail
    assert "isa_extensions" in row.detail


def test_a_later_emission_that_drops_a_field_refuses(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I"],
                                         "top_module": "t"}}
    _late(tmp_path, {"isa_extensions": ["I"]})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "FAIL" and "top_module: dropped" in row.detail


def test_an_identical_or_superset_later_emission_stands(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I"]}}
    _late(tmp_path, {"isa_extensions": ["I"], "top_module": "t"})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "PASS" and "superset" in row.detail


def test_no_early_emission_leaves_row_33_as_it_was(tmp_path):
    row = _refuse(_row(), {"emitted": False},
                                              tmp_path)
    assert row.status == "PASS" and "superset" not in row.detail
