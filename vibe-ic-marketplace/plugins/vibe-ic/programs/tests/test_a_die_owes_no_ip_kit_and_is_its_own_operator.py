"""Two rulings of 2026-09-20, both derived from an owner-attested `deliverable`.

R-0915 (1): **a DIE owes NO IP delivery set.** Step 37.5ip's producers build the
LEF/lib/Verilog/GDS kit and the six IP documents. Measured on spm x gf180mcuD,
DIE route (v1.22.10, run6): that kit cost 7278.5 s — the run's longest step by a
factor of two and a half — and `ip_release_docs_gen` then refused it anyway. The
step now stands down BY DECLARATION, with the declaration cited: the flow row via
37.5ip's own `delivery_declares` condition, and the runner channel (which
dispatches the two producers directly) via a typed N/A record.

R-0915 (2): **on a DIE with no operator template the die is its own operator
(37.5self).** There is no external slot to measure against — the operator
catalogue on disk is information, not a purchase — so the budget is the pad ring
THIS RUN BUILT against the pins the design declares. Measured on run7:
FITS, 34 of 34 declared signal bits bonded by a 38-pad ring.

A HARDMACRO still owes the IP set, and still budgets against an operator slot.
Both directions are tested here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
import slot_pad_budget_check as S  # noqa: E402
import flow_compliance_check as FCC  # noqa: E402
import _tapeout_declaration as TD  # noqa: E402

RTL = """module spm #(parameter size = 32) (
  input clk, input rst, input [size-1:0] x, input y, output p);
endmodule
"""


def _declare(project: Path, deliverable: str, *, attested: bool = True,
             operator: dict | None = None) -> None:
    """Stage the design's answers + the merged declaration, as a run does."""
    prov = {"deliverable": {"answered_by": "owner" if attested else "agent",
                            "citation": "R-0915-95 — fixture"}}
    answers = {"answers": {"deliverable": deliverable, "top_cell": "spm"},
               "operator_template": operator or {"path": None, "slot": None},
               "answer_provenance": prov}
    p = project / "input" / "step_0_5ic_answers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(answers))
    doc, _ = TD.merge_answers(TD.blank_declaration(),
                              {"deliverable": deliverable, "top_cell": "spm"})
    doc[TD.PROVENANCE_KEY] = prov
    d = project / TD.DECLARATION_REL
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(json.dumps(doc, indent=2))


def _ring(project: Path, signals) -> None:
    rec = project / "reports" / "phase3" / "padring.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"producer": {"pads": [
        {"instance": f"u_pad_{i}", "master": "io_cell", "signal": s}
        for i, s in enumerate(signals)]}}))


def _project(tmp_path: Path, deliverable="DIE", *, ring=True, rtl=RTL,
             declaration: dict | None = None, **kw) -> Path:
    project = tmp_path
    _declare(project, deliverable, **kw)
    rtl_dir = project / Path(*S._RTL_DIR_REL)
    rtl_dir.mkdir(parents=True, exist_ok=True)
    (rtl_dir / "spm.v").write_text(rtl)
    chip = project / "reports" / "phase3" / "io_pad_chip_top.json"
    chip.parent.mkdir(parents=True, exist_ok=True)
    chip.write_text(json.dumps({"verdict": "WROTE", "chip_top_module": "chip_top",
                                "core_module": "spm"}))
    if declaration is not None:
        d = project / "plugin_output" / "declaration.json"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(json.dumps(declaration))
    if ring:
        _ring(project, ["VDD", "VSS", "clk", "rst", "y", "p"]
              + [f"x[{i}]" for i in range(32)])
    return project


def _run_budget(project: Path, *extra) -> tuple[int, dict]:
    out = project / "budget.json"
    rc = subprocess.run(
        [sys.executable, str(PROGRAMS / "slot_pad_budget_check.py"),
         str(project), "--json", str(out), *extra],
        capture_output=True, text=True).returncode
    return rc, json.loads(out.read_text())


# ---------------------------------------------------------------- ruling (1)

def test_an_owner_attested_die_stands_the_ip_producers_down_with_the_citation(tmp_path):
    project = _project(tmp_path)
    na, cited, evidence = R._ip_delivery_declared_na(project)
    assert na is True
    assert "answers.deliverable='DIE'" in cited and "owner" in cited
    assert evidence["declaration_path"] == TD.DECLARATION_REL
    row = R.step_digital_hardmacro_gen(project)
    assert row.status == "SKIP"
    assert row.detail.startswith("DESIGN-DECLARED-N/A")
    assert TD.DECLARATION_REL in row.detail
    rec = json.loads((project / "reports/phase3/digital_hardmacro_gen.json").read_text())
    assert rec["verdict"] == "NOT_APPLICABLE"
    assert rec["reason_class"] == "DESIGN_DECLARED_NA"
    assert rec["produced"] == []


def test_a_hardmacro_still_owes_the_ip_kit(tmp_path):
    """The other direction: the kit is the hardmacro's whole deliverable."""
    project = _project(tmp_path, "HARDMACRO")
    assert R._ip_delivery_declared_na(project)[0] is False


@pytest.mark.parametrize("case,kw", [
    ("an agent's reading is not the owner's word", {"attested": False}),
    ("no declaration at all", {"deliverable": "NOT_DETERMINED"}),
])
def test_only_the_owners_word_stands_the_ip_kit_down(tmp_path, case, kw):
    deliverable = kw.pop("deliverable", "DIE")
    project = _project(tmp_path, deliverable, **kw)
    assert R._ip_delivery_declared_na(project)[0] is False, case


def test_the_flow_row_stands_down_by_the_same_declaration(tmp_path):
    project = _project(tmp_path)
    spec = {"declaration": TD.DECLARATION_REL, "field": "answers.deliverable",
            "absent_when": ["DIE"], "corroborated_by": "owner_attestation"}
    got = FCC._delivery_declares_absence(project, spec)
    assert got is not None and got[0] == TD.DECLARATION_REL
    assert "owner-attested" in got[1]
    assert FCC._check_condition(project, {"delivery_declares": spec}) is False
    # un-attested, a different word, and an unknown corroboration all RUN it
    assert FCC._delivery_declares_absence(
        _project(tmp_path / "b", attested=False), spec) is None
    assert FCC._delivery_declares_absence(
        _project(tmp_path / "c", "HARDMACRO"), spec) is None
    assert FCC._delivery_declares_absence(
        project, {**spec, "corroborated_by": "something_else"}) is None


def test_the_flow_yaml_carries_that_condition_on_37_5ip():
    import yaml
    flow = yaml.safe_load(
        (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s.get("id")) == "37.5ip")
    dd = step["condition"]["delivery_declares"]
    assert dd["absent_when"] == ["DIE"]
    assert dd["corroborated_by"] == "owner_attestation"
    # and 37.5ic keeps its own, opposite, corroboration
    ic = next(s for s in flow["steps"] if str(s.get("id")) == "37.5ic")
    assert ic["condition"]["delivery_declares"]["absent_when"] == ["HARDMACRO"]
    assert "corroborated_by" not in ic["condition"]["delivery_declares"]


# ---------------------------------------------------------------- ruling (2)

def test_a_die_is_budgeted_against_the_ring_this_run_built(tmp_path):
    rc, rep = _run_budget(_project(tmp_path))
    assert rc == 0 and rep["verdict"] == "FITS", rep
    assert rep["budget_basis"] == "37.5self:own_pad_ring"
    assert rep["own_ring"]["declared_signal_bits"] == 34
    assert rep["own_ring"]["bonded_declared_bits"] == 34
    assert rep["own_ring"]["ring_pads_total"] == 38
    assert rep["top"] == "spm" and "core_module" in rep["top_source"]


def test_a_pin_the_ring_never_bonded_is_a_refusal(tmp_path):
    """The check can fail: drop one bit from the ring and the die does not fit."""
    project = _project(tmp_path)
    _ring(project, ["VDD", "VSS", "clk", "rst", "y", "p"]
          + [f"x[{i}]" for i in range(31)])          # x[31] has no pad
    rc, rep = _run_budget(project)
    assert rc == 1 and rep["verdict"] == "DOES_NOT_FIT", rep
    assert rep["own_ring"]["unbonded_declared_bits"] == ["x[31]"]
    assert "x[31]" in rep["reason"]


def test_before_the_ring_exists_the_step_names_its_producer(tmp_path):
    rc, rep = _run_budget(_project(tmp_path, ring=False))
    assert rc == 2 and rep["verdict"] == "UNDECIDED"
    assert rep["reason_class"] == "BLOCKED_BY_UPSTREAM"
    assert "pad_ring_gen" in rep["reason"] and "15.5ic" in rep["reason"]


def test_a_hardmacro_is_still_budgeted_against_an_operator_slot(tmp_path):
    """The other direction: a macro with no slot is still a design-declared N/A,
    and nothing here turns it into a die."""
    rc, rep = _run_budget(_project(tmp_path, "HARDMACRO"))
    assert rep["verdict"] == "NOT_APPLICABLE" and rc == 2
    assert rep["reason_class"] == "DESIGN_DECLARED_NA"
    assert rep.get("budget_basis") is None


def test_a_die_that_bound_an_operator_slot_is_not_its_own_operator(tmp_path):
    project = _project(tmp_path, "DIE",
                       operator={"path": "shuttle/x.yaml", "slot": "1x1"})
    rc, rep = _run_budget(project)
    assert rep.get("budget_basis") is None, rep


def test_the_design_declaration_supplies_a_width_the_clause_does_not(tmp_path):
    """MEASURED: the auditor's clause passes no --param, the RTL default was
    the only source, and a design whose own declaration answered was refused."""
    rtl = RTL.replace("parameter size = 32", "parameter size")
    project = _project(tmp_path, rtl=rtl, declaration={"size_param": 32})
    rc, rep = _run_budget(project)
    assert rc == 0 and rep["verdict"] == "FITS", rep
    # and with neither source the refusal stands
    bare = _project(tmp_path / "bare", rtl=rtl)
    rc2, rep2 = _run_budget(bare)
    assert rc2 == 2 and rep2["reason_class"] == "ZERO_DENOMINATOR"
    assert "parameterised" in rep2["reason"]


def test_two_answers_about_one_width_are_refused(tmp_path):
    project = _project(tmp_path, declaration={"size_param": 8})   # RTL says 32
    rc, rep = _run_budget(project)
    assert rc == 2 and rep["verdict"] == "UNDECIDED", rep
    assert rep["parameter_conflicts"]["size"] == {"rtl_default": 32,
                                                  "declared": 8}
