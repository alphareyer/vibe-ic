#!/usr/bin/env python3
"""FX_P2 (1) — a declared case the design INPUT cannot run is NOT_MEASURED,
not FAIL; a case that ran and failed stays FAIL; a case THIS FLOW could have
run and did not stays blocking.

MEASURED on subservient (reused serv 1.4.0, 8HD-4, 2026-09-28, D3 stacked):
step4_functional_evidence FAILed "only 1 of 6 declared L10 case(s) EXECUTED
their own oracle", and the run halted in phase 2. The five split two ways:

  THIS FLOW'S GAP   reset_n_cycle_instruction, i_rst_glitch_instruction_fetch_race
                    Their oracle families (boot latency, reset glitch) claim
                    them, but could not observe the top: its outputs are an
                    SRAM port, and the bus-activity vocabulary had no
                    read-enable. With `*_ren` recognised both run in Verilator
                    and PASS (first read at cycle 1 of the declared 10; no
                    read inside a one-cycle reset glitch).
  THE INPUT'S GAP   blinky_hex / hello_hex name `blinky.hex` / `hello.hex`,
                    which are nowhere in the design input; zifencei states its
                    stimulus as "Zifencei 指令" and its expected half as
                    "PASS", and no program is delivered. The rv32i_40 coverage
                    goal binds the instruction dimension, whose instrument can
                    only read a program that ran — and none is delivered.
                    Nothing in this flow may author those programs (§4.05).

Owner rule (2026-09-28): only a plain FAIL is red. The input's gap is
NOT_MEASURED with the case and the missing input named.

Driven through the real gate (`_evaluate`, `main --json`) and the real runner
step; only the testbench EXECUTION record is planted, in the shape
`testbench_gen.run_unit_tbs` writes it.

chip-AGNOSTIC: synthetic ports and case texts; the file names are formats.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import cpu_boot_latency_oracle_tb_gen as CLG      # noqa: E402
import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import design_one_shot_runner as DOSR              # noqa: E402
import reset_invariant_oracle_tb_gen as RIV        # noqa: E402
import testbench_gen as TBG                        # noqa: E402
import _l10_execution as L10X                      # noqa: E402

TOP = "core_top"

#: Asked through `getattr` so a tree without the classifier ANSWERS (None:
#: "not the input's gap") rather than raising — the teeth below must run on
#: both trees.
_case_input_gap = getattr(TBG, "case_input_gap", lambda *_a, **_k: None)

#: The case texts are subservient's own, as its L10 carries them.
BOOT = {"name": "reset_n_cycle_instruction", "kind": "functional_vector",
        "stimulus": "Reset 解除後 N cycle 內取得第一條 instruction",
        "expected": "N ≤ 策略決定的最大 boot latency(典型 < 10 cycle)"}
HEX = {"name": "blinky_hex", "kind": "functional_vector",
       "stimulus": "blinky.hex", "expected": "GPIO 輸出規則性 toggle"}
PROSE = {"name": "zifencei", "kind": "functional_vector",
         "stimulus": "Zifencei 指令", "expected": "PASS"}
GOAL = {"name": "rv32i_40", "kind": "functional_vector",
        "stimulus": "整套 RV32I 指令(40+ 條)單元測試",
        "expected": "100% PASS(可用 compliance suite 或內附 testbench)"}

#: An SRAM-port top: the surface the bus-activity vocabulary could not see.
SRAM_INPUTS = [("i_clk", ""), ("i_rst", ""), ("i_sram_rdata", "[7:0]")]
SRAM_OUTPUTS = [("o_sram_waddr", "[8:0]"), ("o_sram_wdata", "[7:0]"),
                ("o_sram_wen", ""), ("o_sram_raddr", "[8:0]"),
                ("o_sram_ren", ""), ("o_gpio", "")]


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


# ---------------------------------------------------------------------------
# the measured case, through the gate
# ---------------------------------------------------------------------------
def test_cases_the_input_cannot_run_make_the_gate_not_measured(tmp_path):
    proj = _project(tmp_path, [BOOT, HEX, PROSE],
                    {BOOT["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 2, (rc, msg)
    assert msg.startswith(GATE.NOT_MEASURED_PREFIX), msg
    assert "blinky.hex" in msg and "zifencei" in msg
    assert "does not contain" in msg


def test_the_report_names_each_case_and_what_is_missing(tmp_path):
    proj = _project(tmp_path, [BOOT, HEX, PROSE], {BOOT["name"]: "PASS"})
    out = tmp_path / "gate.json"
    assert GATE.main([str(proj), "--json", str(out)]) == 2
    rep = json.loads(out.read_text())
    assert rep["verdict"] == "NOT_MEASURED", rep["verdict"]
    assert rep["reason_class"] == "EXTERNAL"
    by_case = {g["case"]: g for g in rep["input_not_supplied"]}
    assert set(by_case) == {"blinky_hex", "zifencei"}, by_case
    assert by_case["blinky_hex"]["missing_from_input"] == ["blinky.hex"]


def _instrument_ran_and_found_nothing(proj: Path) -> None:
    """The instruction instrument's OWN receipt, as `instruction_coverage_
    measure.measure` writes it when it applied and no transcript of any case
    carried a tally -- written after the run's L10 execution record."""
    import instruction_coverage_measure as ICM
    receipt = ICM.measure(proj)
    assert receipt["applicable"] is True and not receipt["totals"], receipt
    ICM.write_receipt(proj, receipt)


def test_an_unrunnable_coverage_goal_is_the_inputs_gap_too(tmp_path):
    """Review fix (4): the goal is the input's gap only on the instrument's
    own receipt -- it ran in this run and found no tally, and the input
    delivers no program for any case. (At 065ef1c45 this test planted no
    receipt at all, and certified exactly the conflation the review named;
    that shape is now `test_fx_p2_review_input_gap_needs_positive_evidence`.)"""
    proj = _project(tmp_path, [BOOT, GOAL], {BOOT["name"]: "PASS"})
    _instrument_ran_and_found_nothing(proj)
    rc, msg = GATE._evaluate(proj)
    assert rc == 2, (rc, msg)
    assert "rv32i_40" in msg


def test_the_runner_step_books_it_not_measured_input_absent(tmp_path):
    proj = _project(tmp_path, [BOOT, HEX, PROSE], {BOOT["name"]: "PASS"})
    sr = DOSR.step_step4_functional_evidence(proj)
    assert sr.status == "NOT_MEASURED", (sr.status, sr.detail)
    assert str(getattr(sr.reason_class, "value", sr.reason_class)) \
        == "input_absent", sr.reason_class
    assert "blinky.hex" in sr.detail


# ---------------------------------------------------------------------------
# TEETH — what must stay red
# ---------------------------------------------------------------------------
def test_a_case_that_ran_and_failed_stays_fail(tmp_path):
    proj = _project(tmp_path, [BOOT, HEX, PROSE],
                    {BOOT["name"]: "FAIL"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert BOOT["name"] in msg


def test_an_input_shaped_case_that_ran_and_failed_stays_fail(tmp_path):
    """The input-gap classifier is asked ONLY about a case that did not run.
    A case whose text looks exactly like an input gap, but which the record
    says EXECUTED and FAILED, is a FAIL -- nothing may move it to
    NOT_MEASURED."""
    proj = _project(tmp_path, [BOOT, HEX, PROSE],
                    {BOOT["name"]: "PASS", HEX["name"]: "FAIL",
                     PROSE["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert HEX["name"] in msg


def test_a_case_this_flow_could_run_stays_blocking(tmp_path):
    """A family claims the boot case; its non-execution is this flow's."""
    proj = _project(tmp_path, [BOOT, HEX], {})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert BOOT["name"] in msg


def test_a_named_image_the_input_does_contain_stays_blocking(tmp_path):
    """The input supplied blinky.hex; running it is this flow's job."""
    proj = _project(tmp_path, [BOOT, HEX], {BOOT["name"]: "PASS"},
                    input_files=["input/firmware/blinky.hex"])
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)


def test_a_delivered_testbench_is_not_the_inputs_gap(tmp_path):
    proj = _project(tmp_path, [BOOT, PROSE], {BOOT["name"]: "PASS"},
                    input_files=["input/sim/tb/zifencei.v"])
    assert _case_input_gap(proj, PROSE) is None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_a_case_that_states_no_stimulus_stays_blocking(tmp_path):
    """No stimulus stated is no evidence about the input either way."""
    bare = {"name": "bare_case", "kind": "functional_vector"}
    proj = _project(tmp_path, [BOOT, bare], {BOOT["name"]: "PASS"})
    assert _case_input_gap(proj, bare) is None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_every_case_ran_and_passed_is_still_pass(tmp_path):
    proj = _project(tmp_path, [BOOT, HEX, PROSE],
                    {c["name"]: "PASS" for c in (BOOT, HEX, PROSE)})
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)


def test_a_denied_image_name_is_not_a_named_stimulus():
    assert TBG.named_stimulus_images("load blinky.hex") == ["blinky.hex"]
    assert TBG.named_stimulus_images("no blinky.hex is used") == []


# ---------------------------------------------------------------------------
# THIS FLOW'S GAP — the read-enable is bus activity
# ---------------------------------------------------------------------------
def test_boot_latency_oracle_observes_an_sram_read_enable():
    text = CLG.emit_case_oracle_from_ports(BOOT, TOP, SRAM_INPUTS,
                                           SRAM_OUTPUTS, [])
    assert text and "o_sram_ren" in text


def test_reset_glitch_oracle_observes_an_sram_read_enable():
    glitch = {"name": "i_rst_glitch_instruction_fetch_race",
              "kind": "functional_vector",
              "stimulus": "i_rst glitch 不應導致 instruction fetch race",
              "expected": "holds: i_rst glitch 不應導致 instruction fetch race"}
    text = RIV.emit_case_oracle_from_ports(glitch, TOP, SRAM_INPUTS,
                                           SRAM_OUTPUTS, [])
    assert text and "o_sram_ren" in text


def test_a_write_enable_or_a_lookalike_is_not_a_read():
    """Only a read strobe is the first fetch; `wen` and a name that merely ends
    in the letters (`o_green`) are not."""
    outs = [("o_sram_wen", ""), ("o_green", "")]
    assert CLG._pick_bus_activity_output(outs) is None
