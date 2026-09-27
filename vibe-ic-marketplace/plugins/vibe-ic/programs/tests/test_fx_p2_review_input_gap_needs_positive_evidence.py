#!/usr/bin/env python3
"""FX_P2 review fix (3, 4) — a declared case or coverage goal is booked as the
design INPUT's gap only on positive evidence; everything the flow could have
run stays blocking.

Owner rule: only a plain FAIL is red, and a FAIL must never be relabelled
NOT_MEASURED when the flow could have run the check. At 065ef1c45 two
classifiers moved runnable work out of FAIL:

(3) `testbench_gen.case_input_gap`'s no-image branch booked ANY case no family
    claimed as `input_absent`. The stated-vector family "claimed" a case only
    when its driver could EXTRACT a value, so a case whose input states both
    halves in a shape the driver cannot read (two expected outputs, an odd-
    length hex, a decimal) was called the input's gap. A family detector that
    raised, and the reset family's refusal of an ambiguous case (both of its
    detectors fire), also read as "no family"; and a case this run HAD a
    testbench for (sim/tb/<case>.v) was never asked about.

(4) `cpu_functional_oracle_waiver_check._coverage_goal_summary` booked a goal
    as the input's gap whenever its dimension bound and its number was absent
    -- which is also what a coverage instrument that never ran, timed out or
    crashed leaves behind.

Driven through the real gate (`_evaluate`) and the real classifier; only the
testbench EXECUTION record (the shape `testbench_gen.run_unit_tbs` writes) and
the instrument receipt (through the instrument's own `measure`/`write_receipt`)
are planted. chip-AGNOSTIC: synthetic cases; file names are formats.
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

import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import design_one_shot_runner as DOSR              # noqa: E402
import instruction_coverage_measure as ICM         # noqa: E402
import testbench_gen as TBG                        # noqa: E402
import _l10_execution as L10X                      # noqa: E402
import _path_layout as PL                          # noqa: E402

TOP = "core_top"

#: A case whose stimulus and expected halves are both stated, but whose shape
#: no driver of this flow reads -- none of them names an image.
PASSING = {"name": "boot", "kind": "functional_vector",
           "stimulus": "Reset 解除後 N cycle 內取得第一條 instruction",
           "expected": "N ≤ 10 cycle"}
TWO_OUTPUTS = {"name": "rw_pair", "kind": "functional_vector",
               "stimulus": "write 0xA5 to DATA, pulse START",
               "expected_outputs": {"dout": "0x5a", "valid": "0x01"}}
ODD_HEX = {"name": "odd_hex", "kind": "functional_vector",
           "stimulus": "load ACC then shift once", "expected": "0x5"}
DECIMAL = {"name": "add_regs", "kind": "functional_vector",
           "stimulus": "x1=5, x2=7 後執行 ADD x3,x1,x2", "expected": "x3 = 12"}
LIST_OUTPUTS = {"name": "list_out", "kind": "functional_vector",
                "stimulus": "strobe the port once",
                "expected_outputs": ["0xA5"]}
BOTH_RESET = {"name": "hold_and_glitch", "kind": "functional_vector",
              "stimulus": "reset 期間 SRAM 內容保持，reset glitch 不應造成 race",
              "expected": "SRAM 內容保持，reset glitch 不應造成 race"}
#: The measured input gap: no image, no delivered program, expected "PASS".
PROSE = {"name": "zifencei", "kind": "functional_vector",
         "stimulus": "Zifencei 指令", "expected": "PASS"}
HEX = {"name": "hello_hex", "kind": "functional_vector",
       "stimulus": "hello.hex", "expected": "UART 輸出 hello"}
GOAL = {"name": "rv32i_40", "kind": "functional_vector",
        "stimulus": "整套 RV32I 指令(40+ 條)單元測試",
        "expected": "100% PASS(可用 compliance suite 或內附 testbench)"}
LINE_GOAL = {"name": "line_cov", "kind": "coverage_goal",
             "stimulus": "line coverage over all RTL", "expected": ">= 90%"}


def _junit(tests: int) -> str:
    cases = "".join(f'<testcase classname="u" name="c{i}" time="1"/>'
                    for i in range(tests))
    return ("<?xml version='1.0' encoding='utf-8'?>\n"
            f'<testsuites><testsuite name="l10_unit_tb" errors="0" '
            f'failures="0" skipped="0" tests="{tests}" time="1">{cases}'
            "</testsuite></testsuites>\n")


def _project(tmp_path: Path, cases, verdicts, *, input_files=()) -> Path:
    """A completed step-4 tree: connectivity bridge + passing professional
    JUnit + the declared L10 cases + the per-case execution record."""
    proj = tmp_path / "proj"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    l10 = gd / "L10_TEST_CASES.json"
    l10.write_text(json.dumps({"fields": {"test_cases": list(cases)}},
                              ensure_ascii=False))
    (proj / "input" / "docs").mkdir(parents=True)
    (proj / "input" / "docs" / "L7.md").write_text("verification plan\n")
    for rel in input_files:
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(":00000001FF\n")
    run = proj / "phase2" / "stage1" / "sim_full_stack" / "run"
    run.mkdir(parents=True)
    transcript = run / "full_stack.log"
    transcript.write_text("FULL_STACK_TB_CHECKS pass=6 fail=0\n"
                          "FULL_STACK_TB_DONE\n")
    pro = proj / "phase2" / "stage1" / "sim_professional" / "l10_unit_tb"
    pro.mkdir(parents=True)
    (pro / "results.xml").write_text(_junit(len(cases)))
    assert DOSR._emit_connectivity_sim_bridge(proj, transcript, TOP,
                                              "generic_full_stack")
    rows = []
    for c in cases:
        v = verdicts.get(c["name"], "NOT_EXECUTED")
        rows.append({"id": c["name"], "verdict": v,
                     "sim_executed": v in ("PASS", "FAIL"),
                     "detail": "planted"})
    L10X.write_record(proj, l10, rows, producer="testbench_gen.run_unit_tbs")
    return proj


def _gap(proj: Path, case: dict):
    return TBG.case_input_gap(proj, case)


# ---------------------------------------------------------------------------
# (3) the input stated both halves: a driver gap, never the input's
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", [TWO_OUTPUTS, ODD_HEX, DECIMAL, LIST_OUTPUTS],
                         ids=lambda c: c["name"])
def test_a_case_stating_its_answer_is_not_the_inputs_gap(tmp_path, case):
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"})
    assert _gap(proj, case) is None
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert case["name"] in msg


@pytest.mark.parametrize("case", [TWO_OUTPUTS, ODD_HEX, DECIMAL, LIST_OUTPUTS],
                         ids=lambda c: c["name"])
def test_the_stated_vector_family_claims_what_it_cannot_extract(case):
    assert TBG.oracle_family_claiming(case) == "stated_vector"


def test_an_ambiguous_reset_case_is_claimed_not_orphaned(tmp_path):
    """Both reset detectors fire: the family's emitter refuses to guess, but
    the family OWNS the case -- its non-execution is this flow's."""
    import reset_invariant_oracle_tb_gen as RIV
    assert RIV.is_reset_hold_case(BOTH_RESET)
    assert RIV.is_reset_glitch_case(BOTH_RESET)
    assert RIV.case_family(BOTH_RESET) is None        # the emitter's refusal
    assert TBG.oracle_family_claiming(BOTH_RESET) == "reset_invariant"
    proj = _project(tmp_path, [PASSING, BOTH_RESET], {PASSING["name"]: "PASS"})
    assert _gap(proj, BOTH_RESET) is None


def test_a_detector_that_raises_keeps_the_case_blocking(tmp_path, monkeypatch):
    import cpu_boot_latency_oracle_tb_gen as CLG

    def _boom(_case):
        raise RuntimeError("detector broke")

    monkeypatch.setattr(CLG, "is_boot_latency_case", _boom)
    # `getattr`, so a tree without the sentinel ANSWERS (None) instead of
    # raising AttributeError -- a control that errors observes nothing.
    unknown = getattr(TBG, "FAMILY_UNKNOWN", "UNKNOWN_DETECTOR_RAISED")
    assert TBG.oracle_family_claiming(PROSE) == unknown
    proj = _project(tmp_path, [PASSING, PROSE], {PASSING["name"]: "PASS"})
    assert _gap(proj, PROSE) is None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_a_case_this_run_had_a_testbench_for_is_the_flows(tmp_path):
    """The flow's sim/tb/ holds an authored testbench for the case and it did
    not execute (it failed to compile): that is this flow's failure."""
    proj = _project(tmp_path, [PASSING, PROSE], {PASSING["name"]: "PASS"})
    tb = PL.sim_dir(proj) / "tb" / f"{PROSE['name']}.v"
    tb.parent.mkdir(parents=True, exist_ok=True)
    tb.write_text("module tb; initial begin $display(\"PASS\"); end\n")
    assert _gap(proj, PROSE) is None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_a_substance_floor_file_is_not_a_testbench(tmp_path):
    """The floor the emitter writes when it has no oracle is not one."""
    proj = _project(tmp_path, [PASSING, PROSE], {PASSING["name"]: "PASS"})
    tb = PL.sim_dir(proj) / "tb" / f"{PROSE['name']}.v"
    tb.parent.mkdir(parents=True, exist_ok=True)
    tb.write_text(f"// {TBG.ORACLE_NONE_MARKER}\nmodule tb; endmodule\n")
    assert _gap(proj, PROSE) is not None


def test_the_measured_input_gap_is_still_named(tmp_path):
    """CONTROL: the input states no value and delivers no program."""
    proj = _project(tmp_path, [PASSING, PROSE, HEX], {PASSING["name"]: "PASS"})
    gap = _gap(proj, PROSE)
    assert gap is not None, gap
    assert _gap(proj, HEX)["missing_from_input"] == ["hello.hex"]
    rc, msg = GATE._evaluate(proj)
    assert rc == 2 and msg.startswith(GATE.NOT_MEASURED_PREFIX), (rc, msg)


# ---------------------------------------------------------------------------
# (4) a coverage goal: the instrument's own receipt, or it stays a refusal
# ---------------------------------------------------------------------------
def _receipt(proj: Path) -> Path:
    receipt = ICM.measure(proj)
    return ICM.write_receipt(proj, receipt)


def test_a_line_goal_with_no_coverage_receipt_stays_fail(tmp_path):
    """The verilator arm published nothing: the flow could have measured."""
    proj = _project(tmp_path, [PASSING, PROSE, LINE_GOAL],
                    {PASSING["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "line_cov" in msg


def test_an_instruction_goal_whose_instrument_never_ran_stays_fail(tmp_path):
    proj = _project(tmp_path, [PASSING, PROSE, GOAL],
                    {PASSING["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "rv32i_40" in msg


def test_a_receipt_from_an_earlier_run_stays_fail(tmp_path):
    """The instrument timed out this run; its receipt is the previous run's."""
    proj = _project(tmp_path, [PASSING, PROSE, GOAL],
                    {PASSING["name"]: "PASS"})
    rec = _receipt(proj)
    old = L10X.resolve_record(proj).stat().st_mtime - 60
    os.utime(rec, (old, old))
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)


def test_a_delivered_program_means_the_number_was_the_flows(tmp_path):
    """hello.hex IS delivered and hello_hex ran and passed: a missing tally is
    this flow's (its testbench or its instrument), not the input's."""
    proj = _project(tmp_path, [PASSING, HEX, GOAL],
                    {PASSING["name"]: "PASS", HEX["name"]: "PASS"},
                    input_files=["input/sim/programs/hello.hex"])
    _receipt(proj)
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "rv32i_40" in msg


def test_a_tally_the_instrument_refused_is_not_an_absence(tmp_path):
    proj = _project(tmp_path, [PASSING, PROSE, GOAL],
                    {PASSING["name"]: "PASS"})
    log = proj / "phase2" / "stage1" / "sim" / "tb" / f"{PROSE['name']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction "
                   "covered=3 total=41\n")
    rec = json.loads(_receipt(proj).read_text())
    assert rec["refusals"], rec
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_a_fresh_empty_receipt_and_no_delivered_program_is_the_inputs(tmp_path):
    """CONTROL: the instrument ran here, found no tally anywhere, and the
    input delivers no program for any case."""
    proj = _project(tmp_path, [PASSING, PROSE, GOAL],
                    {PASSING["name"]: "PASS"})
    _receipt(proj)
    rc, msg = GATE._evaluate(proj)
    assert rc == 2, (rc, msg)
    assert "rv32i_40" in msg


def test_the_goal_gap_names_the_instrument_that_ran(tmp_path):
    proj = _project(tmp_path, [PASSING, PROSE, GOAL],
                    {PASSING["name"]: "PASS"})
    _receipt(proj)
    _rc, msg = GATE._evaluate(proj)
    assert "instrument ran in this run" in msg, msg
