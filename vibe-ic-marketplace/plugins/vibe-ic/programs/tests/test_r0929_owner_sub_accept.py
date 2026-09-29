#!/usr/bin/env python3
"""R-0929-OWNER-SUB-ACCEPT — the owner's two acceptance rules, in the audit.

(1) A CPU core-ISA conformance suite run on the SAME RTL with only a
    memory-size PARAMETER changed (no RTL edit) COUNTS as the CPU
    functional-verification evidence for the ISA rows, and is DISCLOSED in the
    verdict: parameter name, delivered value vs test value, suite, programs and
    instructions passed; the delivered-memory run is still reported, measured
    or not. Any RTL change, a different top, or a failing suite gives no
    credit.
(2) A firmware row whose input is absent (its stimulus names a program image
    the design input does not contain) is EXCLUDED from real PASS exactly like
    the FPGA board steps 6/39 (flow_compliance_check F10): NOT_MEASURED
    [input_absent], published under `not_measured_excluded` beside the
    verdict with its `excluded_from_verdict` sentence, never a pass, not
    blocking.

MEASURED, subservient x gf180mcuD (2026-09-29, copy of subic4_ic_tb_20260929):
the delivered 1024-byte SRAM cannot hold a single riscv-arch-test program, so
the delivered arm reads RV32I 0/39 and Zifencei 1/2 while the same RTL at
MEMSIZE=2 MiB passes 39/39 and 2/2; blinky.hex/hello.hex are not in the input.
The Step-4 functional gate therefore read FAIL (rv32i goal NOT_MEASURED) with
three input gaps behind it.

Driven through the real gates (`cpu_functional_oracle_waiver_check._evaluate`
and `main --json`, `l10_tb_conformance_check.main`); only the execution record
and the ISA-suite receipt are planted, each in the shape its producer writes.

chip-AGNOSTIC: a synthetic top, synthetic case names, synthetic file names.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import design_one_shot_runner as DOSR              # noqa: E402
import l10_tb_conformance_check as L10C            # noqa: E402
import _l10_execution as L10X                      # noqa: E402
import testbench_gen as TBG                        # noqa: E402

TOP = "core_top"
PARAM = "memsize"
DELIVERED = 1024
TEST = 4096
RECEIPT_REL = "reports/phase2/isa_suites/isa_suite_receipt.json"

BOOT = {"name": "reset_n_cycle_instruction", "kind": "functional_vector",
        "stimulus": "Reset 解除後 N cycle 內取得第一條 instruction",
        "expected": "N ≤ 策略決定的最大 boot latency(典型 < 10 cycle)"}
FIRMWARE = {"name": "firmware_toggle", "kind": "functional_vector",
            "stimulus": "toggle.hex", "expected": "GPIO 輸出規則性 toggle"}
FENCE = {"name": "fence_case", "kind": "functional_vector",
         "stimulus": "Zifencei 指令", "expected": "PASS"}
# The producer (`isa_suite_producer.bind_case`) binds the base unit only to a
# row whose own text names `RV<xlen>I`, and the reader now re-derives that
# binding (review wave 57, SUBACCEPT P4), so the goal row names it.
GOAL = {"name": "base_isa_goal", "kind": "functional_vector",
        "stimulus": "full RV32I instruction set unit tests",
        "expected": "100% PASS"}

RTL = {
    f"{TOP}.v": (f"module {TOP} #(\n    parameter integer {PARAM} = "
                 f"{DELIVERED},\n    parameter integer RESET_PC = 0\n) (\n"
                 "    input wire i_clk,\n    input wire i_rst,\n"
                 "    output wire o_sram_ren\n);\n"
                 "  core_alu u_alu (.clk(i_clk));\n"
                 "  assign o_sram_ren = 1'b0;\nendmodule\n"),
    "core_alu.v": "module core_alu (input wire clk);\nendmodule\n",
}
I_INSTRUCTIONS = ("add", "sub", "lui")


def _junit(tests: int) -> str:
    cases = "".join(f'<testcase classname="u" name="c{i}" time="1"/>'
                    for i in range(tests))
    return ("<?xml version='1.0' encoding='utf-8'?>\n"
            f'<testsuites><testsuite name="l10_unit_tb" errors="0" '
            f'failures="0" skipped="0" tests="{tests}" time="1">{cases}'
            "</testsuite></testsuites>\n")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _passing(key: str = "staged_full") -> dict:
    return {key: {"state": "PASS", "by_init": [
        {"init": "ff", "state": "PASS", "why": "signature equal"},
        {"init": "a5", "state": "PASS", "why": "signature equal"}]}}


def _receipt(bound: dict) -> dict:
    """The ISA-suite producer's receipt, in the shape it writes."""
    programs = {}
    for ins in I_INSTRUCTIONS:
        programs[f"act-I-{ins}-01"] = {
            "suite": "riscv-arch-test", "unit": "I", "instruction": ins,
            "role": "primary", "judge": "signature", "size": 20000,
            "arms": _passing(), "fits_declared_memsize": False}
    programs["rv32ui-p-add"] = {
        "suite": "riscv-tests", "unit": "I", "instruction": "add",
        "role": "supplementary", "judge": "tohost", "size": 600,
        "arms": {**_passing(), **_passing("staged_delivered")},
        "fits_declared_memsize": True}
    programs["act-Zifencei-01"] = {
        "suite": "riscv-arch-test", "unit": "Zifencei",
        "instruction": "fence.i", "role": "primary", "judge": "signature",
        "size": 8000, "arms": _passing(), "fits_declared_memsize": False}
    cov = {"covered": 3, "total": 3, "excluded": ["ebreak", "ecall"],
           "uncovered": []}
    cases = {}
    for case, units in bound.items():
        n = 3 if "I" in units else 1
        cases[case] = {
            "verdict": "NOT_MEASURED", "passed": 0, "primary_programs": n,
            "coverage": None,
            "full_parameter": {"verdict": "PASS", "passed": n,
                               "primary_programs": n, "memsize": TEST,
                               "coverage": cov if "I" in units else None},
            "delivered": {"verdict": "NOT_MEASURED", "passed": 0,
                          "primary_programs": n, "memsize": DELIVERED,
                          "coverage": None}}
    return {
        "schema": "vibeic.isa_suite_receipt.v1",
        "producer": "isa_suite_producer",
        "cases": cases, "rows": [], "refusal": None,
        "bound_cases": bound,
        "design_facts": {"top": TOP, "memsize_declared": DELIVERED,
                         "memsize_param": PARAM},
        "instruction_total": {"total": 3, "excluded": ["ecall", "ebreak"],
                              "why": "no trap target"},
        "acquisition": ["riscv-arch-test@0123abcd: 3 locked file(s) verified"],
        "arms": ["staged"],
        "arm_rtl_sha256": {"staged": {n: _sha(t) for n, t in RTL.items()}},
        "deviation_disclosures": [],
        "executor_rc": 0,
        "programs": programs,
        "memsize_full": TEST,
    }


def _project(tmp_path: Path, cases, verdicts, *, receipt=None,
             declaration=None, rtl=None, input_files=()) -> Path:
    proj = tmp_path / "proj"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    l10 = gd / "L10_TEST_CASES.json"
    l10.write_text(json.dumps({"test_cases": list(cases)},
                              ensure_ascii=False))
    (proj / "input" / "docs").mkdir(parents=True)
    (proj / "input" / "docs" / "L7.md").write_text("verification plan\n")
    for rel in input_files:
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(":00000001FF\n")
    rtl_dir = proj / "phase2" / "stage1" / "rtl"
    rtl_dir.mkdir(parents=True)
    for name, text in (RTL if rtl is None else rtl).items():
        (rtl_dir / name).write_text(text)
    po = proj / "plugin_output"
    po.mkdir()
    (po / "declaration.json").write_text(json.dumps(
        declaration if declaration is not None else
        {"top_module": TOP, "isa_extensions": ["I", "Zifencei"],
         "core_parameters": {PARAM: DELIVERED}}))
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
    tb = proj / "phase2" / "stage1" / "sim" / "tb"
    tb.mkdir(parents=True, exist_ok=True)
    for c in cases:
        # The case that ran carries its own oracle; every other one is the
        # substance-floor scaffold the producer writes when it has none, and
        # says so in the producer's own marker.
        floor = ("" if verdicts.get(c["name"]) else
                 f"// {TBG.ORACLE_NONE_MARKER}\n")
        (tb / f"{c['name']}.v").write_text(
            f"{floor}module tb_{c['name']};\n  {TOP} dut();\nendmodule\n")
    rows = []
    for c in cases:
        v = verdicts.get(c["name"], "NOT_EXECUTED")
        rows.append({"id": c["name"], "verdict": v,
                     "sim_executed": v in ("PASS", "FAIL"),
                     "detail": "planted"})
    rp = proj / RECEIPT_REL
    if receipt is not None:
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(receipt))
    # Setup only (review wave 58): the Step-4 execution record binds the
    # receipt its own run produced, exactly as `run_unit_tbs` writes it; the
    # reader credits nothing else. Assertions are unchanged.
    L10X.write_record(proj, l10, rows, producer="testbench_gen.run_unit_tbs",
                      isa_receipt=rp)
    return proj


BOUND = {FENCE["name"]: ["Zifencei"], GOAL["name"]: ["I"]}


def _gate_json(tmp_path: Path, proj: Path) -> "tuple[int, dict]":
    out = tmp_path / "gate.json"
    rc = GATE.main([str(proj), "--json", str(out)])
    return rc, json.loads(out.read_text())


# ---------------------------------------------------------------------------
# (1) + (2) together: the measured shape
# ---------------------------------------------------------------------------
def test_both_rules_release_the_step4_functional_gate(tmp_path):
    proj = _project(tmp_path, [BOOT, FIRMWARE, FENCE, GOAL],
                    {BOOT["name"]: "PASS"}, receipt=_receipt(BOUND))
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    assert msg.startswith("PASS:"), msg
    # (1) disclosed: parameter, delivered vs test, suite, programs,
    # instructions, and the delivered-memory run.
    assert f"test {PARAM}={TEST}" in msg and f"delivered {PARAM}={DELIVERED}" \
        in msg, msg
    assert "riscv-arch-test" in msg and "3/3 primary program(s)" in msg, msg
    assert "3/3 instruction(s)" in msg, msg
    assert f"delivered {PARAM}={DELIVERED} run: NOT_MEASURED 0/3" in msg, msg
    # (2) listed separately, never a pass.
    assert "NOT_MEASURED [input_absent] and EXCLUDED" in msg, msg
    assert "toggle.hex" in msg, msg


def test_the_record_publishes_both_rules_beside_the_verdict(tmp_path):
    proj = _project(tmp_path, [BOOT, FIRMWARE, FENCE, GOAL],
                    {BOOT["name"]: "PASS"}, receipt=_receipt(BOUND))
    rc, rep = _gate_json(tmp_path, proj)
    assert rc == 0 and rep["verdict"] == "PASS", rep["message"]
    excl = rep["not_measured_excluded"]
    assert [e["case"] for e in excl] == [FIRMWARE["name"]], excl
    assert excl[0]["status"] == "NOT_MEASURED"
    assert excl[0]["reason_class"] == "input_absent"
    assert excl[0]["missing_from_input"] == ["toggle.hex"]
    assert excl[0]["reason"].startswith(
        "NOT_MEASURED, excluded from the verdict: "), excl[0]
    credited = {c["case"]: c for c in rep["isa_conformance_credit"]["credited"]}
    assert set(credited) == {FENCE["name"], GOAL["name"]}, credited
    goal = credited[GOAL["name"]]
    assert goal["parameter"] == PARAM
    assert (goal["delivered_value"], goal["test_value"]) == (DELIVERED, TEST)
    assert goal["programs_passed"] == goal["programs_total"] == 3
    assert goal["instructions"]["covered"] == goal["instructions"]["total"] == 3
    assert goal["delivered_run"] == {"verdict": "NOT_MEASURED", "passed": 0,
                                     "primary_programs": 3,
                                     "memsize": DELIVERED}
    assert goal["rtl_identity"]["files"] == len(RTL)
    row = rep["coverage_goals"]["rows"][0]
    assert row["verdict"] == "PASS" and row["achieved_pct"] == 100.0, row
    assert row["instrument"] == L10X.ISA_CREDIT_KIND


# ---------------------------------------------------------------------------
# (2) alone
# ---------------------------------------------------------------------------
def test_a_firmware_row_whose_image_is_absent_does_not_block(tmp_path):
    """Before the ruling this was NOT_MEASURED [EXTERNAL] rc 2: the one
    firmware row kept an otherwise-passing Step 4 out of real PASS."""
    proj = _project(tmp_path, [BOOT, FIRMWARE], {BOOT["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    assert FIRMWARE["name"] in msg and "EXCLUDED" in msg, msg


def test_the_excluded_firmware_row_is_never_counted_as_passed(tmp_path):
    proj = _project(tmp_path, [BOOT, FIRMWARE], {BOOT["name"]: "PASS"})
    rc, rep = _gate_json(tmp_path, proj)
    assert rc == 0
    assert rep["not_measured_excluded"][0]["status"] == "NOT_MEASURED"
    ran = GATE._oracles_that_actually_ran(proj)
    assert FIRMWARE["name"] not in ran["executed"], ran
    assert ran["executed"] == [BOOT["name"]], ran


def test_a_bare_verdict_row_without_a_suite_still_blocks(tmp_path):
    """Not a firmware row: no image is named. Rule (2) does not reach it and,
    with no conformance receipt, rule (1) does not either -- NOT_MEASURED."""
    proj = _project(tmp_path, [BOOT, FIRMWARE, FENCE], {BOOT["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 2, (rc, msg)
    assert msg.startswith(GATE.NOT_MEASURED_PREFIX), msg
    assert FENCE["name"] in msg


def test_the_exclusion_reader_answers_only_for_a_named_absent_image(tmp_path):
    """Asked directly, not through a caller's own guard: a bare-verdict row is
    an input gap but not a firmware row, so it gets no exclusion record."""
    proj = _project(tmp_path, [BOOT, FIRMWARE, FENCE], {BOOT["name"]: "PASS"})
    assert TBG.case_input_gap(proj, FENCE) is not None
    assert TBG.input_absent_exclusion(proj, FENCE) is None
    rec = TBG.input_absent_exclusion(proj, FIRMWARE)
    assert rec is not None and rec["missing_from_input"] == ["toggle.hex"]
    assert rec["reason_class"] == "input_absent"
    assert rec["status"] == "NOT_MEASURED"
    assert rec["excluded_from_verdict"].startswith(
        TBG.EXCLUDED_FROM_VERDICT_PREFIX)


def test_a_firmware_image_the_input_does_contain_is_not_excluded(tmp_path):
    proj = _project(tmp_path, [BOOT, FIRMWARE], {BOOT["name"]: "PASS"},
                    input_files=["input/firmware/toggle.hex"])
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "EXCLUDED" not in msg, msg


def test_a_firmware_row_that_ran_and_failed_stays_fail(tmp_path):
    proj = _project(tmp_path, [BOOT, FIRMWARE],
                    {BOOT["name"]: "PASS", FIRMWARE["name"]: "FAIL"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert FIRMWARE["name"] in msg


# ---------------------------------------------------------------------------
# (1) alone
# ---------------------------------------------------------------------------
def test_the_isa_suite_credits_a_vector_row(tmp_path):
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=_receipt({FENCE["name"]: ["Zifencei"]}))
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    assert "CREDITED" in msg and "1/1 primary program(s) PASS" in msg, msg


def test_the_isa_suite_measures_the_instruction_goal(tmp_path):
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=_receipt({GOAL["name"]: ["I"]}))
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    assert "3/3 instruction(s)" in msg, msg


def _credit_refusal(tmp_path, **kw) -> "tuple[int, str, str]":
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"}, **kw)
    rc, msg = GATE._evaluate(proj)
    _credit, why = L10X.isa_conformance_credit(proj, FENCE["name"])
    assert _credit is None
    return rc, msg, why or ""


def test_an_rtl_change_gives_no_credit(tmp_path):
    rtl = dict(RTL)
    rtl["core_alu.v"] = rtl["core_alu.v"].replace("clk", "clk_edit")
    rc, msg, why = _credit_refusal(
        tmp_path, receipt=_receipt({FENCE["name"]: ["Zifencei"]}), rtl=rtl)
    assert rc == 2, (rc, msg)
    assert "an RTL change" in why and "core_alu.v" in why, why


def test_an_extra_rtl_file_gives_no_credit(tmp_path):
    rtl = dict(RTL, **{"core_extra.sv": "module core_extra; endmodule\n"})
    rc, msg, why = _credit_refusal(
        tmp_path, receipt=_receipt({FENCE["name"]: ["Zifencei"]}), rtl=rtl)
    assert rc == 2 and "an RTL change" in why, (rc, why)


def test_a_different_top_gives_no_credit(tmp_path):
    rc, msg, why = _credit_refusal(
        tmp_path, receipt=_receipt({FENCE["name"]: ["Zifencei"]}),
        declaration={"top_module": "core_wrapper",
                     "core_parameters": {PARAM: DELIVERED}})
    assert rc == 2 and "a different top" in why, (rc, why)


def test_a_failing_suite_gives_no_credit(tmp_path):
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["programs"]["act-Zifencei-01"]["arms"]["staged_full"]["state"] = "FAIL"
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert rc == 2 and "a failing suite" in why, (rc, why)


def test_a_failing_supplementary_program_gives_no_credit(tmp_path):
    rec = _receipt({GOAL["name"]: ["I"]})
    rec["programs"]["rv32ui-p-add"]["arms"]["staged_full"]["state"] = "FAIL"
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=rec)
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    _c, why = L10X.isa_conformance_credit(proj, GOAL["name"])
    assert _c is None and "a failing suite" in why, why


def test_one_init_pattern_short_of_pass_gives_no_credit(tmp_path):
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["programs"]["act-Zifencei-01"]["arms"]["staged_full"]["by_init"][1][
        "state"] = "NOT_MEASURED"
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert rc == 2 and "did not PASS" in why, (rc, why)


def test_a_memory_size_that_is_not_a_top_parameter_gives_no_credit(tmp_path):
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["design_facts"]["memsize_param"] = "DEPTH"
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert rc == 2 and "is not a parameter of" in why, (rc, why)


def test_a_delivered_value_the_rtl_does_not_carry_gives_no_credit(tmp_path):
    rtl = dict(RTL)
    rtl[f"{TOP}.v"] = rtl[f"{TOP}.v"].replace(f"= {DELIVERED}", "= 2048")
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["arm_rtl_sha256"]["staged"] = {n: _sha(t) for n, t in rtl.items()}
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec, rtl=rtl)
    assert rc == 2 and "the delivered value" in why, (rc, why)


def test_a_receipt_that_disagrees_with_its_rows_gives_no_credit(tmp_path):
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["cases"][FENCE["name"]]["full_parameter"]["passed"] = 2
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert rc == 2 and "inconsistent" in why, (rc, why)


def test_a_producer_refusal_gives_no_credit(tmp_path):
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["refusal"] = "the container job did not complete"
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert rc == 2 and "refused" in why, (rc, why)


def test_a_case_that_ran_and_failed_is_not_credited(tmp_path):
    proj = _project(tmp_path, [BOOT, FENCE],
                    {BOOT["name"]: "PASS", FENCE["name"]: "FAIL"},
                    receipt=_receipt({FENCE["name"]: ["Zifencei"]}))
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "CREDITED" not in msg, msg


def test_an_instruction_count_short_of_the_goal_is_a_fail(tmp_path):
    """The credit supplies a NUMBER; the goal is judged by it like any other."""
    rec = _receipt({GOAL["name"]: ["I"]})
    rec["instruction_total"]["total"] = 4
    rec["cases"][GOAL["name"]]["full_parameter"]["coverage"]["total"] = 4
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=rec)
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert "75%" in msg and "[FAIL]" in msg, msg


def test_a_receipt_that_binds_nothing_says_nothing(tmp_path):
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=_receipt({"some_other_case": ["I"]}))
    assert L10X.isa_conformance_credit(proj, FENCE["name"]) == (None, None)
    rc, _msg = GATE._evaluate(proj)
    assert rc == 2


# ---------------------------------------------------------------------------
# the L10 table reads the same two rules through the same readers
# ---------------------------------------------------------------------------
def _l10c(tmp_path, proj) -> "tuple[int, dict]":
    out = tmp_path / "l10c.json"
    rc = L10C.main([
        "--l10", str(proj / "phase1" / "generated_docs" / "L10_TEST_CASES.json"),
        "--tb-dir", str(proj / "phase2" / "stage1" / "sim" / "tb"),
        "--project", str(proj), "--out", str(out)])
    return rc, json.loads(out.read_text())


def test_the_l10_table_credits_and_excludes_instead_of_waiving(tmp_path):
    """Before the ruling all three rows were WAIVED-DEFERRED
    (cap:cpu_functional_oracle) and the table read PASS_WITH_WAIVERS rc 3."""
    proj = _project(tmp_path, [BOOT, FIRMWARE, FENCE, GOAL],
                    {BOOT["name"]: "PASS"}, receipt=_receipt(BOUND))
    rc, rep = _l10c(tmp_path, proj)
    by_id = {r["id"]: r for r in rep["results"]}
    assert rc == 0, rep
    assert rep["waived"] == 0 and rep["not_executed"] == 0, rep
    assert by_id[FENCE["name"]]["status"] == "pass"
    assert by_id[FENCE["name"]]["credited_by"] == L10X.ISA_CREDIT_KIND
    assert by_id[FENCE["name"]]["sim_executed"] is False
    assert by_id[GOAL["name"]]["status"] == "pass"
    fw = by_id[FIRMWARE["name"]]
    assert fw["status"] == "NOT_MEASURED" and fw["pass"] is False, fw
    assert fw["reason_class"] == "input_absent"
    assert [e["case"] for e in rep["not_measured_excluded"]] == \
        [FIRMWARE["name"]]
    assert {c["case"] for c in rep["isa_conformance_credited"]} == \
        {FENCE["name"], GOAL["name"]}


def test_the_l10_table_keeps_a_refused_credit_where_it_was(tmp_path):
    rec = _receipt(BOUND)
    rec["executor_rc"] = 1
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=rec)
    rc, rep = _l10c(tmp_path, proj)
    row = {r["id"]: r for r in rep["results"]}[FENCE["name"]]
    assert row["status"] == "waived", row
    assert "did not complete" in row["isa_conformance_refused"], row
    assert rc == 3


@pytest.mark.parametrize("mutate", ["schema", "arms"])
def test_a_malformed_receipt_never_credits(tmp_path, mutate):
    rec = copy.deepcopy(_receipt({FENCE["name"]: ["Zifencei"]}))
    if mutate == "schema":
        rec["schema"] = "vibeic.isa_suite_receipt.v0"
    else:
        rec["arms"] = []
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=rec)
    credit, why = L10X.isa_conformance_credit(proj, FENCE["name"])
    assert credit is None and why, why
    rc, _msg = GATE._evaluate(proj)
    assert rc == 2


# ---------------------------------------------------------------------------
# review wave 57 (SUBACCEPT) — the four false-PASS paths it found
# ---------------------------------------------------------------------------
def _short_goal_receipt() -> dict:
    """3 of 4 instructions covered: 75% against a stated 100%."""
    rec = _receipt({GOAL["name"]: ["I"]})
    rec["instruction_total"]["total"] = 4
    rec["cases"][GOAL["name"]]["full_parameter"]["coverage"]["total"] = 4
    return rec


def test_the_l10_table_fails_a_goal_the_suite_falls_short_of(tmp_path):
    """HIGH (P1): the L10 table credited the goal row as pass at 3/4 = 75%
    against a stated 100% while Step 4 FAILed the same row. The two
    consumers now apply ONE judgment (`_l10x.isa_goal_verdict`)."""
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=_short_goal_receipt())
    rc_gate, _msg = GATE._evaluate(proj)
    rc, rep = _l10c(tmp_path, proj)
    row = {r["id"]: r for r in rep["results"]}[GOAL["name"]]
    assert row["status"] == "fail" and row["pass"] is False, row
    assert row["achieved_pct"] == 75.0, row
    assert rc == 1 and rc_gate == 1, (rc, rc_gate)
    # `_v1_6_609_l10_conformance_ok` upgrades coverage on ok == total.
    assert rep["ok"] != rep["total"], rep
    assert GOAL["name"] not in {c["case"] for c in
                                rep["isa_conformance_credited"]}


def test_the_l10_table_still_credits_a_goal_the_suite_meets(tmp_path):
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=_receipt({GOAL["name"]: ["I"]}))
    rc, rep = _l10c(tmp_path, proj)
    row = {r["id"]: r for r in rep["results"]}[GOAL["name"]]
    assert row["status"] == "pass" and row["achieved_pct"] == 100.0, row
    assert rc == 0, rep


def test_a_fail_on_the_delivered_arm_refuses_the_credit(tmp_path):
    """MEDIUM (P2): a program that fits the delivered memory and FAILs there
    was published inside a PASS credit."""
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["programs"]["act-Zifencei-01"]["arms"]["staged_delivered"] = {
        "state": "FAIL", "by_init": [
            {"init": "ff", "state": "FAIL", "why": "signature differs"},
            {"init": "a5", "state": "FAIL", "why": "signature differs"}]}
    rec["cases"][FENCE["name"]]["delivered"].update(verdict="FAIL")
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert "a failing delivered configuration" in why, why
    assert rc != 0 and "CREDITED" not in msg, (rc, msg)


def test_a_fail_under_one_init_on_the_delivered_arm_refuses(tmp_path):
    rec = _receipt({GOAL["name"]: ["I"]})
    rec["programs"]["rv32ui-p-add"]["arms"]["staged_delivered"][
        "by_init"][0]["state"] = "FAIL"
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"},
                    receipt=rec)
    _c, why = L10X.isa_conformance_credit(proj, GOAL["name"])
    assert _c is None and "a failing delivered configuration" in why, why


def test_the_delivered_run_is_counted_from_the_rows(tmp_path):
    """MEDIUM (P2): delivered_run was copied from the receipt's summary
    words. Summary words that disagree with the rows are refused."""
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["cases"][FENCE["name"]]["delivered"].update(verdict="PASS", passed=1)
    rc, msg, why = _credit_refusal(tmp_path, receipt=rec)
    assert "inconsistent" in why and "delivered run" in why, why
    rec = _receipt({FENCE["name"]: ["Zifencei"]})
    rec["programs"]["act-Zifencei-01"]["arms"].update(
        _passing("staged_delivered"))
    rec["cases"][FENCE["name"]]["delivered"].update(passed=1)
    proj = _project(tmp_path / "b", [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=rec)
    credit, why = L10X.isa_conformance_credit(proj, FENCE["name"])
    assert why is None, why
    assert credit["delivered_run"] == {"verdict": "PASS", "passed": 1,
                                       "primary_programs": 1,
                                       "memsize": DELIVERED}


RESET_SRAM = {"name": "reset_during_sram_write", "kind": "functional_vector",
              "stimulus": "assert reset while an SRAM write is in flight",
              "expected": "PASS"}


def test_a_binding_the_case_text_does_not_name_credits_nothing(tmp_path):
    """MEDIUM (P4): the receipt's bound_cases was trusted verbatim; a
    reset/SRAM row bound to Zifencei was CREDITED and Step 4 read PASS."""
    proj = _project(tmp_path, [BOOT, RESET_SRAM], {BOOT["name"]: "PASS"},
                    receipt=_receipt({RESET_SRAM["name"]: ["Zifencei"]}))
    credit, why = L10X.isa_conformance_credit(proj, RESET_SRAM["name"])
    assert credit is None and "names no ISA unit" in why, why
    rc, msg = GATE._evaluate(proj)
    assert rc != 0 and "CREDITED" not in msg, (rc, msg)
    rc, rep = _l10c(tmp_path, proj)
    row = {r["id"]: r for r in rep["results"]}[RESET_SRAM["name"]]
    assert row["status"] != "pass", row


def test_a_binding_to_a_row_the_declaration_lacks_credits_nothing(tmp_path):
    proj = _project(tmp_path, [BOOT, FENCE], {BOOT["name"]: "PASS"},
                    receipt=_receipt({FENCE["name"]: ["Zifencei"],
                                      "gone_case": ["Zifencei"]}))
    credit, why = L10X.isa_conformance_credit(proj, "gone_case")
    assert credit is None and "not a row" in why, why


FW_RV32I = {"name": "firmware_rv32i", "kind": "functional_vector",
            "stimulus": "toggle.hex (RV32I firmware)",
            "expected": "GPIO 輸出規則性 toggle"}


def test_a_firmware_row_naming_rv32i_is_excluded_not_credited(tmp_path):
    """MEDIUM (P3): rule (1) was asked before rule (2), so a firmware row
    whose stimulus also names RV32I was CREDITED as a PASS instead of being
    NOT_MEASURED [input_absent]."""
    proj = _project(tmp_path, [BOOT, FW_RV32I], {BOOT["name"]: "PASS"},
                    receipt=_receipt({FW_RV32I["name"]: ["I"]}))
    credit, why = L10X.isa_conformance_credit(proj, FW_RV32I["name"])
    assert credit is None and "a firmware row" in why, why
    rc, rep = _gate_json(tmp_path, proj)
    assert [e["case"] for e in rep["not_measured_excluded"]] == \
        [FW_RV32I["name"]], rep
    assert rep["isa_conformance_credit"]["credited"] == [], rep
    rc, rep = _l10c(tmp_path, proj)
    row = {r["id"]: r for r in rep["results"]}[FW_RV32I["name"]]
    assert row["status"] == "NOT_MEASURED" and row["pass"] is False, row
    assert row.get("excluded_from_verdict"), row
