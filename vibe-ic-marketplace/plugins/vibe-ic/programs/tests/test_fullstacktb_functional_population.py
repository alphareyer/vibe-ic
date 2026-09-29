#!/usr/bin/env python3
"""FULLSTACKTB (2026-09-29) — Step 5's full-stack population must be FUNCTIONAL.

MEASURED on both DIE benchmark runs (spm, subservient): the full-stack
testbench the flow generates was the connectivity skeleton (no stimulus, zero
golden compares), and `bit_level_full_stack_tb_check` answered every IC without
a command protocol with VACUOUS_PASS (`DESIGN_DECLARED_NA`), so Step 5 carried
it as a partially-vacuous PASS clause. The functional oracle that did exist
drove the bare core, never the pad-ring chip top the die is.

Pinned here, each with its known-negative:
  * the gate REFUSES a connectivity-only population (NOT_MEASURED + INCOMPLETE,
    never a pass) and refuses a DIE whose chip top is not built;
  * a population is credited only through the REQUIRED top (the pad-ring chip
    top on a DIE route), with testbench, transcript, sources and design input
    re-verified by the gate — a record cannot vouch for itself;
  * the producer drives the design's own L10 cases, each with the oracle its
    input states, through the chip top, honours the population, the corners and
    the reset-mid-computation scenario the case's own text states, and never
    counts a coverage FIGURE as a functional case.

The simulator is a stand-in here (the host carries none); the real simulation
of both benchmark ICs through their pad-ring tops is the replay in the lane
report. chip-AGNOSTIC: every fixture is a synthetic design.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import bit_level_full_stack_tb_check as gate  # noqa: E402
import arith_oracle_tb_gen as aog  # noqa: E402


def _fsf():
    return importlib.import_module("full_stack_functional_tb")


# ---------------------------------------------------------------------------
# a synthetic serial-parallel arithmetic core (8-bit), optionally on a DIE
# ---------------------------------------------------------------------------
CORE = "dut_core"
CORE_V = f"""module {CORE} (input clk, input rst, input [7:0] x, input y,
                  output p);
  reg [15:0] acc; assign p = acc[0];
  always @(posedge clk) if (rst) acc <= 0; else acc <= acc + (y ? x : 0);
endmodule
"""
CHIP_TOP_V = f"""// generated pad-ring top (synthetic)
module chip_top (
    input clk,
    input rst,
    input [7:0] x,
    input y,
    output p,
    inout VDD,
    inout VSS
);
    wire clk__core, rst__core, y__core, p__core, t0;
    wire [7:0] x__core;
    TIELO u_tie0 (.ZN(t0));
    PADIN u_pad_clk (.PAD(clk), .Y(clk__core));
    PADIN u_pad_rst (.PAD(rst), .Y(rst__core));
    PADIN u_pad_y (.PAD(y), .Y(y__core));
    PADIN u_pad_x_0 (.PAD(x[0]), .Y(x__core[0]));
    PADOUT u_pad_p (.A(p__core), .PAD(p));
    {CORE} u_core (.clk(clk__core), .rst(rst__core), .x(x__core),
                   .y(y__core), .p(p__core));
endmodule
"""
MODELS = {
    "/pdk/libs.ref/io/verilog/io.v":
        "module PADIN(input PAD, output Y); assign Y = PAD; endmodule\n"
        "module PADOUT(input A, output PAD); assign PAD = A; endmodule\n",
    "/pdk/libs.ref/sc/verilog/sc.v":
        "module TIELO(output ZN); assign ZN = 1'b0; endmodule\n",
}

CASES = [
    {"name": "random_equivalence", "kind": "coverage_goal",
     "stimulus": "隨機 ≥ 100 組 (x, y),全部對 golden model 一致",
     "expected": "100% PASS (binary)"},
    {"name": "corner_operand", "kind": "coverage_goal",
     "stimulus": "x = 0、y_stream = 0、x = MAX_POS、x = MIN_NEG、y = MAX_POS、"
                 "y = MIN_NEG、x = -1、y = -1",
     "expected": "100% PASS"},
    {"name": "reset", "kind": "coverage_goal",
     "stimulus": "reset 期間 / reset 解除瞬間 / reset 在計算進行中 assert 三種情況",
     "expected": "100% PASS"},
    {"name": "toggle_branch_coverage", "kind": "functional_vector",
     "stimulus": "同 random run;非 sign-off gate", "expected": "≥ 95%"},
]


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _mk_project(tmp_path: Path, *, die: bool, chip_top: bool = True,
                cases=None) -> Path:
    proj = tmp_path / "proj"
    gd = proj / "phase1" / "generated_docs"
    _write(gd / "L3_CMD_PROTOCOL.json", json.dumps(
        {"opcodes": [], "no_opcodes_in_input": True}))
    _write(gd / "L4_REGMAP.json", json.dumps({"registers": []}))
    _write(gd / "L2_FRS.json", json.dumps(
        {"function": "p = x * y mod 2^N (serial-parallel multiplier)"}))
    _write(gd / "L9_INTEGRATION_SPEC.json", json.dumps({
        "top_module": CORE,
        "top_ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst", "direction": "input", "width": 1},
            {"name": "x", "direction": "input", "width": 8},
            {"name": "y", "direction": "input", "width": 1},
            {"name": "p", "direction": "output", "width": 1}]}))
    _write(gd / "L10_TEST_CASES.json", json.dumps(
        {"test_cases": CASES if cases is None else cases}, ensure_ascii=False))
    _write(proj / "phase2" / "stage1" / "rtl" / f"{CORE}.v", CORE_V)
    sfs = proj / "phase2" / "stage1" / "sim_full_stack"
    # the connectivity skeleton + its results, exactly the refused shape
    _write(sfs / f"tb_{CORE}_full.v",
           f"module tb_{CORE}_full; {CORE} u_dut(); endmodule\n")
    _write(sfs / "results.json", json.dumps(
        {"pass": False, "functional_verified": False,
         "functional_coverage": {"scored_with_golden": 0},
         "command_oracle_applicable": False, "per_vector": []}))
    if die:
        _write(proj / "input" / "submission_template" / "SELF_TAPEOUT.txt",
               "self tape-out\n")
        if chip_top:
            _write(proj / "phase3" / "stage3" / "pnr" / "chip_top_io.v",
                   CHIP_TOP_V)
            _write(proj / "reports" / "phase3" / "io_pad_chip_top.json",
                   json.dumps({"verdict": "WROTE",
                               "chip_top_module": "chip_top",
                               "core_module": CORE,
                               "chip_top_verilog":
                                   "phase3/stage3/pnr/chip_top_io.v"}))
    return proj


def _resolver(project, used, container):
    return dict(MODELS), "fixture models"


class _FakeSim:
    """A simulator stand-in: builds succeed; a run reports what the TB's own
    population is, scored as `outcome(case, nv)` says. It reads the testbench
    it is asked to run, so a TB that is not there cannot be 'run'."""

    def __init__(self, outcome=None):
        self.calls = []
        self.tb_for = {}
        self.outcome = outcome or (lambda case, nv: (nv, nv))

    def __call__(self, argv, run_dir, container, tool, timeout):
        self.calls.append(list(argv))
        if argv[0] == "iverilog":
            self.tb_for[str(run_dir)] = argv[-1]
            return 0, ""
        tb = Path(self.tb_for[str(run_dir)])
        text = tb.read_text()
        case = tb.stem
        m = re.search(r"localparam integer NV = (\d+);", text)
        if m:
            nv = int(m.group(1))
            a, b = self.outcome(case, nv)
            return 0, f"ORACLE_TB_DONE pass={a}/{b}\n"
        a, b = self.outcome(case, 1)
        word = "PASS" if a == b else "FAIL"
        return (0 if word == "PASS" else 1), f"[TB {case}] {word} — ok\n"


@pytest.fixture
def arith_class(monkeypatch):
    import testbench_gen as tbg
    monkeypatch.setattr(tbg, "_detect_ic_class",
                        lambda project: "digital_arithmetic_primitive")


def _run_gate(proj: Path, tmp_path: Path, capsys=None):
    out = tmp_path / "gate.json"
    old = sys.argv
    sys.argv = ["bit_level_full_stack_tb_check.py", str(proj), "--json",
                str(out)]
    try:
        rc = gate.main()
    finally:
        sys.argv = old
    stdout = capsys.readouterr().out if capsys is not None else ""
    return rc, json.loads(out.read_text()), stdout


# ===========================================================================
# THE GATE — a connectivity-only population is refused
# ===========================================================================
def test_connectivity_only_population_is_refused_not_vacuous(tmp_path, capsys):
    """KNOWN-POSITIVE of the refusal: skeleton + 0 golden vectors, no record."""
    proj = _mk_project(tmp_path, die=False)
    rc, res, out = _run_gate(proj, tmp_path, capsys)
    assert rc == 2, res
    assert res["verdict"] == "NOT_MEASURED", res
    assert res["reason_class"] == "ZERO_DENOMINATOR"
    assert res["pass"] is False and res["vacuous_pass"] is False
    assert res["rule"] == "functional_full_stack_population_absent"
    # the sentinel that makes flow_compliance raise INCOMPLETE, last, column 0
    assert out.rstrip().splitlines()[-1].startswith("INCOMPLETE:")


def test_die_route_without_chip_top_is_blocked(tmp_path, capsys):
    """A DIE whose pad-ring chip top is not built yet measures nothing."""
    proj = _mk_project(tmp_path, die=True, chip_top=False)
    rc, res, out = _run_gate(proj, tmp_path, capsys)
    assert rc == 2
    assert res["verdict"] == "NOT_MEASURED"
    assert res["reason_class"] == "BLOCKED_BY_UPSTREAM"
    assert res["required_top"]["pad_ring"] is True
    assert "INCOMPLETE:" in out


# ===========================================================================
# THE PRODUCER — functional cases through the pad-ring chip top
# ===========================================================================
def test_producer_drives_every_case_through_the_pad_ring_top(tmp_path,
                                                            arith_class):
    proj = _mk_project(tmp_path, die=True)
    sim = _FakeSim()
    rec = _fsf().generate(proj, "ctr", dispatch=sim,
                          model_resolver=_resolver)
    assert rec["verdict"] == "PASS", rec["reason"]
    by = {c["name"]: c for c in rec["cases"]}
    assert rec["counts"]["executed"] == 3 and rec["counts"]["passed"] == 3
    assert by["toggle_branch_coverage"]["state"] == "excluded"
    assert not (proj / "phase2/stage1/sim_full_stack/functional"
                / "toggle_branch_coverage.v").exists()
    for name in ("random_equivalence", "corner_operand", "reset"):
        c = by[name]
        assert c["state"] == "passed", c
        text = (proj / c["tb"]).read_text()
        assert _fsf().instance_count(text, "chip_top") == 1
        assert _fsf().instance_count(text, CORE) == 0
        assert c["family"] == "declared_function_golden"
    # compiled with the staged PDK models, the chip top and the core RTL
    build = next(a for a in sim.calls if a[0] == "iverilog")
    joined = " ".join(build)
    assert "phase3/stage3/pnr/chip_top_io.v" in joined
    assert f"rtl/{CORE}.v" in joined
    assert sum("/functional/models/" in a for a in build) == 2


def test_the_case_text_decides_population_corners_and_reset_scenario(
        tmp_path, arith_class):
    proj = _mk_project(tmp_path, die=True)
    rec = _fsf().generate(proj, "ctr", dispatch=_FakeSim(),
                          model_resolver=_resolver)
    by = {c["name"]: c for c in rec["cases"]}
    rnd = (proj / by["random_equivalence"]["tb"]).read_text()
    nv = int(re.search(r"localparam integer NV = (\d+);", rnd).group(1))
    assert nv >= 100 and by["random_equivalence"]["declared_population"] == 100
    assert "framing calibrated on the first 28" in rnd
    corner = (proj / by["corner_operand"]["tb"]).read_text()
    # MIN_NEG for an 8-bit operand is the pattern 128, crossed with itself
    assert "_av[0] = 8'd0; _bv[0] = 8'd0;" in corner
    assert re.search(r"= 8'd128; _bv\[\d+\] = 8'd128;", corner)
    reset = (proj / by["reset"]["tb"]).read_text()
    assert "for (_k = 0; _k < N/2; _k = _k + 1)" in reset
    assert "for (_k = 0; _k < N/2" not in corner


def test_a_case_short_of_its_stated_population_is_not_a_pass(tmp_path,
                                                            arith_class):
    proj = _mk_project(tmp_path, die=True)
    # the simulator reports FEWER vectors than the case states
    sim = _FakeSim(lambda case, nv: (min(nv, 50), min(nv, 50))
                   if case == "random_equivalence" else (nv, nv))
    rec = _fsf().generate(proj, "ctr", dispatch=sim, model_resolver=_resolver)
    by = {c["name"]: c for c in rec["cases"]}
    assert by["random_equivalence"]["state"] == "short_population"
    assert rec["verdict"] == "NOT_MEASURED"


def test_producer_refuses_a_chip_top_it_has_no_models_for(tmp_path,
                                                         arith_class):
    proj = _mk_project(tmp_path, die=True)
    rec = _fsf().generate(
        proj, "ctr", dispatch=_FakeSim(),
        model_resolver=lambda p, u, c: ({"/pdk/io.v": MODELS[
            "/pdk/libs.ref/io/verilog/io.v"]}, "io only"))
    assert rec["verdict"] == "NOT_MEASURED"
    assert rec["reason_class"] == "EXECUTION_ERROR"
    assert "TIELO" in rec["reason"]
    assert not list((proj / "phase2/stage1/sim_full_stack/functional")
                    .glob("*.v"))


def test_producer_before_the_chip_top_exists_runs_nothing(tmp_path,
                                                         arith_class):
    proj = _mk_project(tmp_path, die=True, chip_top=False)
    sim = _FakeSim()
    rec = _fsf().generate(proj, "ctr", dispatch=sim, model_resolver=_resolver)
    assert rec["verdict"] == "NOT_MEASURED"
    assert rec["reason_class"] == "BLOCKED_BY_UPSTREAM"
    assert sim.calls == []


def test_off_the_pad_ring_route_the_core_is_the_full_stack_top(tmp_path,
                                                              arith_class):
    proj = _mk_project(tmp_path, die=False)
    rec = _fsf().generate(proj, None, dispatch=_FakeSim(),
                          model_resolver=_resolver)
    assert rec["full_stack_top"]["pad_ring"] is False
    assert rec["full_stack_top"]["module"] == CORE
    assert rec["verdict"] == "PASS"
    assert rec["models"] == []


# ===========================================================================
# THE GATE — crediting a population, and refusing one that cannot vouch
# ===========================================================================
def _passing_die(tmp_path, arith_class=None):
    proj = _mk_project(tmp_path, die=True)
    rec = _fsf().generate(proj, "ctr", dispatch=_FakeSim(),
                          model_resolver=_resolver)
    assert rec["verdict"] == "PASS"
    return proj, rec


def test_gate_credits_a_population_measured_through_the_chip_top(
        tmp_path, arith_class, capsys):
    proj, _rec = _passing_die(tmp_path)
    rc, res, out = _run_gate(proj, tmp_path, capsys)
    assert rc == 0, res
    assert res["verdict"] == "PASS" and res["functional_verified"] is True
    assert res["vacuous_pass"] is False
    assert res["counts"] == {"executed": 3, "passed": 3, "failed": 0,
                             "short": 0}
    assert res["required_top"]["module"] == "chip_top"
    assert "INCOMPLETE" not in out


def test_gate_fails_a_case_whose_oracle_disagreed(tmp_path, arith_class,
                                                  capsys):
    proj = _mk_project(tmp_path, die=True)
    sim = _FakeSim(lambda case, nv: (nv - 1, nv) if case == "corner_operand"
                   else (nv, nv))
    rec = _fsf().generate(proj, "ctr", dispatch=sim, model_resolver=_resolver)
    assert rec["verdict"] == "FAIL"
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 1 and res["verdict"] == "FAIL"
    assert res["counts"]["failed"] == 1


def test_gate_refuses_a_transcript_that_is_not_the_recorded_one(
        tmp_path, arith_class, capsys):
    proj, rec = _passing_die(tmp_path)
    log = proj / next(c for c in rec["cases"]
                      if c["name"] == "reset")["run_log"]
    log.write_text(log.read_text() + "\n")
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["verdict"] == "NOT_MEASURED"
    assert res["rule"] == "functional_record_inconsistent"


def test_gate_rescores_the_transcript_rather_than_trusting_the_record(
        tmp_path, arith_class, capsys):
    """A record that says `passed` over a transcript with no verdict marker."""
    proj, rec = _passing_die(tmp_path)
    fsf = _fsf()
    c = next(c for c in rec["cases"] if c["name"] == "corner_operand")
    log = proj / c["run_log"]
    log.write_text("simulation finished\n")
    c["run_log_sha256"] = fsf.sha256_file(log)      # the record is re-sealed
    fsf.record_path(proj).write_text(json.dumps(rec))
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["rule"] == "functional_record_inconsistent"
    assert "scores errored" in res["rationale"]


def test_gate_refuses_a_testbench_that_bypasses_the_pad_ring(
        tmp_path, arith_class, capsys):
    proj, rec = _passing_die(tmp_path)
    fsf = _fsf()
    c = next(c for c in rec["cases"] if c["name"] == "reset")
    tb = proj / c["tb"]
    tb.write_text(tb.read_text().replace("chip_top dut", f"{CORE} dut"))
    c["tb_sha256"] = fsf.sha256_file(tb)            # the record is re-sealed
    fsf.record_path(proj).write_text(json.dumps(rec))
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["rule"] == "functional_record_inconsistent"
    assert "does not instantiate the required top" in res["rationale"]


def test_gate_refuses_a_record_measured_through_the_core_on_a_die(
        tmp_path, arith_class, capsys):
    proj = _mk_project(tmp_path, die=True)
    # a population measured OFF the pad-ring route, then the die is declared
    rec = _fsf().generate(proj, None, route="HARDMACRO", dispatch=_FakeSim(),
                          model_resolver=_resolver)
    assert rec["verdict"] == "PASS"
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["rule"] == "functional_record_wrong_top"


def test_gate_refuses_a_record_of_bytes_that_have_changed(
        tmp_path, arith_class, capsys):
    proj, _rec = _passing_die(tmp_path)
    rtl = proj / "phase2" / "stage1" / "rtl" / f"{CORE}.v"
    rtl.write_text(rtl.read_text() + "// edited\n")
    rc, res, _ = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["rule"] == "functional_record_stale"


def test_gate_refuses_a_population_that_executed_nothing(tmp_path, capsys,
                                                         monkeypatch):
    """No oracle family grounds any case -> the record is empty -> refused."""
    import testbench_gen as tbg
    monkeypatch.setattr(tbg, "_detect_ic_class", lambda project: None)
    proj = _mk_project(tmp_path, die=True, cases=[
        {"name": "blinky", "kind": "functional_vector",
         "stimulus": "blinky.hex", "expected": "GPIO toggles"}])
    rec = _fsf().generate(proj, "ctr", dispatch=_FakeSim(),
                          model_resolver=_resolver)
    assert rec["counts"]["executed"] == 0
    assert rec["cases"][0]["state"] == "no_oracle"
    rc, res, out = _run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["verdict"] == "NOT_MEASURED"
    assert out.rstrip().splitlines()[-1].startswith("INCOMPLETE:")


# ===========================================================================
# TRANSCRIPT SCORING — each side of every pair
# ===========================================================================
@pytest.mark.parametrize("rc,text,state", [
    (0, "ORACLE_TB_DONE pass=28/28\n", "passed"),
    (0, "ORACLE_TB_DONE pass=27/28\nORACLE_MISMATCH: no single\n", "failed"),
    (0, "ORACLE_TB_DONE pass=0/0\n", "errored"),
    (0, "[TB c1] PASS — holds\n", "passed"),
    (1, "[TB c1] FAIL: x asserted\n", "failed"),
    (0, "[TB other] PASS — holds\n", "errored"),
    (0, "simulation finished\n", "errored"),
    (1, "", "errored"),
])
def test_score_transcript_pairs(rc, text, state):
    assert _fsf().score_transcript("c1", rc, text)["state"] == state


def test_coverage_figure_is_not_a_functional_acceptance():
    fsf = _fsf()
    assert fsf.acceptance_is_coverage_figure({"expected": "≥ 95%"})
    assert not fsf.acceptance_is_coverage_figure(
        {"expected": "100% PASS (binary)"})
    assert not fsf.acceptance_is_coverage_figure({"expected": "PASS"})


# ===========================================================================
# THE CASE-TEXT READERS (arith_oracle_tb_gen)
# ===========================================================================
def test_declared_population_reads_the_stimulus_not_the_acceptance():
    assert aog.declared_vector_population(
        {"stimulus": "隨機 ≥ 10000 組 (x, y)", "expected": "100% PASS"}) == 10000
    assert aog.declared_vector_population(
        {"stimulus": "at least 2,048 random pairs"}) == 2048
    assert aog.declared_vector_population(
        {"stimulus": "同 random run", "expected": "≥ 95%"}) is None
    assert aog.declared_vector_population({"stimulus": "≥ 95% toggle"}) is None


def test_named_corners_are_width_bit_patterns():
    case = {"stimulus": "x = 0、x = MAX_POS、x = MIN_NEG、y = -1"}
    assert aog.named_corner_values(case, 8) == [0, 127, 128, 255]
    assert aog.named_corner_values({"stimulus": "random pairs"}, 8) == []


def test_reset_mid_computation_grammar():
    assert aog.reset_mid_computation_case(
        {"stimulus": "reset 在計算進行中 assert"})
    assert aog.reset_mid_computation_case(
        {"stimulus": "assert reset during a computation"})
    assert not aog.reset_mid_computation_case(
        {"stimulus": "reset 解除後 10 cycle 內取得第一條 instruction"})


def test_the_historical_sample_is_unchanged_without_case_text():
    spec = {"width": 8, "operator": "*", "signed": False}
    base = aog.select_operand_pairs(spec, "mixed")
    assert len(base) == 28
    assert aog.select_operand_pairs(spec, "mixed", min_vectors=None,
                                    extra_corners=()) == base
    grown = aog.select_operand_pairs(spec, "random", min_vectors=500)
    assert len(grown) == 500 and len(set(grown)) == 500
    named = aog.select_operand_pairs(spec, "corners", extra_corners=[0, 128])
    assert named[:4] == [(0, 0), (0, 128), (128, 0), (128, 128)]


def test_small_populations_keep_the_full_framing_search():
    spec = {"top": "t", "operator": "*", "width": 8, "signed": False,
            "parallel": "x", "serial_in": "y", "serial_out": "p",
            "clk": "clk", "rst": "rst", "reset_active_low": False,
            "parallel_is_lhs": True, "other_inputs": [],
            "ports": [{"name": n, "dir": d} for n, d in
                      (("clk", "input"), ("rst", "input"), ("x", "input"),
                       ("y", "input"), ("p", "output"))]}
    small = aog.emit_serial_oracle_module("m", spec, [(1, 2)] * 28)
    large = aog.emit_serial_oracle_module(
        "m", spec, [(i, i + 1) for i in range(40)])
    assert "calibrated" not in small
    assert "for (_vi = 0; _vi < NV; _vi = _vi + 1) begin" in small
    assert "framing calibrated on the first 28" in large
    with pytest.raises(ValueError):
        aog.emit_serial_oracle_module("m", dict(spec, rst=None), [(1, 2)],
                                      abort_before_each=True)


# ===========================================================================
# THE WIRING — Phase 2 step and the phase-3 re-run after the pad ring
# ===========================================================================
def test_phase2_step_publishes_the_records_word(monkeypatch, tmp_path):
    import design_one_shot_runner as dsor
    fsf = _fsf()
    for verdict, cls, want, want_cls in (
            ("PASS", None, "PASS", ""),
            ("FAIL", None, "FAIL", ""),
            ("NOT_MEASURED", "ZERO_DENOMINATOR", "NOT_MEASURED",
             "no_population"),
            ("NOT_MEASURED", "BLOCKED_BY_UPSTREAM", "NOT_MEASURED",
             "input_absent")):
        monkeypatch.setattr(fsf, "generate", lambda p, c, _v=verdict, _c=cls: {
            "verdict": _v, "reason_class": _c, "reason": "r",
            "counts": {"executed": 1}, "full_stack_top": {"module": "t"}})
        res = dsor.step_full_stack_functional_tb(tmp_path, "ctr")
        assert res.name == "full_stack_functional_tb"
        assert res.status == want and res.reason_class == want_cls


def test_phase3_reruns_the_producer_only_after_the_pad_ring_is_written(
        monkeypatch, tmp_path):
    import phase3_one_shot_runner as p3
    fsf = _fsf()
    seen = []
    monkeypatch.setattr(fsf, "generate", lambda p, c: seen.append(p) or {
        "verdict": "PASS", "counts": {"executed": 2}, "reason": "ok"})
    monkeypatch.setattr(p3, "_chip_path_requests_pad_ring", lambda p: True)
    monkeypatch.setattr(p3, "step_io_pad_chip_top_gen",
                        lambda p, c, pdk, **kw: p3.StepResult(
                            "io_pad_chip_top_gen", "PASS", 0.0, "rc=0"))
    res = p3._padring_producer_dispatch(tmp_path, "ctr", None)
    assert res.status == "PASS" and seen == [tmp_path]
    assert res.extras["full_stack_functional"]["verdict"] == "PASS"
    seen.clear()
    monkeypatch.setattr(p3, "step_io_pad_chip_top_gen",
                        lambda p, c, pdk, **kw: p3.StepResult(
                            "io_pad_chip_top_gen", "FAIL", 0.0, "rc=1"))
    res = p3._padring_producer_dispatch(tmp_path, "ctr", None)
    assert res.status == "FAIL" and seen == []
