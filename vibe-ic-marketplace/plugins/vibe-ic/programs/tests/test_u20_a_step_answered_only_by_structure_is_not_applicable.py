#!/usr/bin/env python3
"""U20 (IC_BLOCKER_AUDIT §2) — FS1 with only NOT_APPLICABLE_BY_STRUCTURE gates
reads NOT_APPLICABLE, not PASS.

MEASURED on spm v5 (IC path, deliverable DIE; 8HD-4 `spmic5/run_v5`, read-only
copy), the whole-flow audit:

    ✓ [PASS             ] Step FS1: ISO-26262 FMEDA diagnostic-coverage ...
       └─ NOT_APPLICABLE_BY_STRUCTURE (gate executed; it ENUMERATED its subject
          population and found none ...): fmeda_fault_injection_coverage ...
       └─ NOT_APPLICABLE_BY_STRUCTURE (...): fmeda_coverage_check ...

and FS1 was counted in spm's PASS tally. Both of FS1's clauses dispatched, both
ENUMERATED the RTL and found no safety mechanism, and neither measured a
diagnostic coverage — there is no coverage number anywhere on the row. R-0915-119
fixed the opposite error (NOT_MEASURED / no_population) by publishing the decided
class; the tier chain then had no word for a step whose EVERY clause is that
class, and fell through to the final `else`, a bare PASS.

THE RULE: a step whose every dispatched gate clause is a structural absence, that
declares no output of its own and carries no other reason, is NOT_APPLICABLE —
answered, and outside the executed-PASS numerator.

WHAT DOES NOT MOVE (pinned below):
  * a design that HAS a safety mechanism is still examined (not N/A);
  * a structural-absence clause beside a clause that substantively ran keeps the
    step a PASS — a clause's N/A is that clause's, not the step's;
  * DESIGN_DECLARED_NA executed-N/A steps keep their landed PASS
    (test_design_declared_na_execution_contract) — a typed declaration was
    examined there, which is a different fact.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _structural_absence as SA         # noqa: E402
import flow_compliance_check as F        # noqa: E402

NABS = SA.NOT_APPLICABLE_BY_STRUCTURE
FLOW = PROG.parent / "flow" / "phase1_phase2_phase3.yaml"
NA = F._T.Verdict.NOT_APPLICABLE.value
PASS = F._T.Verdict.PASS.value

#: spm's shape: a plain datapath, no ECC / parity / lockstep anywhere.
PLAIN_RTL = """\
module spm (input clk, input rst, input y, output p);
  reg [31:0] acc;
  always @(posedge clk) acc <= rst ? 32'b0 : {acc[30:0], y};
  assign p = acc[31];
endmodule
"""
#: A real SEC-DED-shaped pair: the design HAS the subject.
ECC_RTL = """\
module ham_enc(input [3:0] data_in, output [6:0] code_out);
  assign code_out = 7'b0;
endmodule
module ham_dec(input [6:0] code_in, output [3:0] data_out,
               output syndrome_err);
  assign data_out = code_in[3:0];
  assign syndrome_err = 1'b0;
endmodule
"""


def _project(tmp_path: Path, rtl: str) -> Path:
    p = tmp_path / "proj"
    (p / "phase2/stage1/rtl").mkdir(parents=True)
    (p / "phase2/stage1/rtl/dut.v").write_text(rtl)
    (p / "phase1/generated_docs").mkdir(parents=True)
    return p


def _fs1_step() -> dict:
    yaml = pytest.importorskip("yaml")
    for s in yaml.safe_load(FLOW.read_text())["steps"]:
        if str(s.get("id")) == "FS1":
            return s
    raise AssertionError("FS1 is not in the flow")


def test_fs1_answered_only_by_structure_reads_not_applicable(tmp_path):
    res = F.check_step(_project(tmp_path, PLAIN_RTL), _fs1_step(), waivers={})
    assert res.status == NA, (res.status, res.reason_class, res.reasons)
    # the two clauses are still named, with the class they stated
    assert sorted(str(e).split(" ", 1)[0]
                  for e in res.executed_declared_not_applicable) == [
        "fmeda_coverage_check", "fmeda_fault_injection_coverage"]
    assert sum(NABS in r for r in res.reasons) >= 2, res.reasons


def test_a_design_with_a_safety_mechanism_is_not_not_applicable(tmp_path):
    res = F.check_step(_project(tmp_path, ECC_RTL), _fs1_step(), waivers={})
    assert res.status != NA, (res.status, res.reasons)
    assert not res.executed_declared_not_applicable, res.reasons


def test_a_structural_absence_beside_a_substantive_clause_stays_pass(tmp_path):
    """One clause's structural absence does not speak for a step another
    clause substantively examined."""
    fs1 = _fs1_step()
    # a clause that examined something and passed, named by absolute path
    # (which `_resolve_program_cmd` honours)
    probe = tmp_path / "gates" / "examines_and_passes.py"
    probe.parent.mkdir()
    probe.write_text("print('[PASS] examined 1 file, 0 findings')\n")
    step = {"id": "T-u20", "name": "mixed", "stage": "stage2",
            "gate": {"all_of": list(fs1["gate"]["all_of"]) + [
                {"program_exit_zero": f"{probe} ."}]}}
    res = F.check_step(_project(tmp_path, PLAIN_RTL), step, waivers={})
    assert res.status == PASS, (res.status, res.reasons)
    assert len(res.executed_declared_not_applicable) == 2, res.reasons


def test_a_step_that_declares_an_output_is_not_moved(tmp_path):
    """A step that owes an artefact of its own is judged by it; the N/A of its
    gate clauses is not the step's."""
    step = dict(_fs1_step())
    step["id"] = "T-u20-out"
    step["required_outputs"] = ["phase2/stage1/rtl/dut.v"]
    res = F.check_step(_project(tmp_path, PLAIN_RTL), step, waivers={})
    assert res.status != NA, (res.status, res.reasons)
