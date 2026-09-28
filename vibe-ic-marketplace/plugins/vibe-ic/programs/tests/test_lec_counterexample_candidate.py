"""LEC residual counterexamples: what a SAT model may decide, and how it is read.

Review of `next/claude-fx-lec-not-proven-state` at 7af91b7bd, verified on the
pinned image:

  * A bounded SAT search starts a STATEFUL miter from the all-zero state
    (`sat -set-init-zero`). No declared reset constrains that state, so it
    need not be reachable: a gold 7-bit up-counter reset to 0 with
    `y = (s == 3)` and a gate 7-bit down-counter reset to 127 with
    `y = (g == 124)` are equivalent from reset, yet the all-zero pair
    diverges at step 4. The producers decided that model NON_EQUIVALENT and
    both gates FAILed an equivalent design. Only a model of a COMPLETE
    (stateless) miter may decide; a stateful model is a candidate, NOT_PROVEN.
  * `equiv_miter -cmp` drives `cmp_<point>` with the EQUALITY of gold and gate,
    WaveJSON writes `.` for "same as before", and a stateful dump carries an
    `init` column first. The decoder treated 1 as a mismatch, did not expand
    `.`, and shifted every stateful step by one.
  * `unproven_point_names` was iterated before it was validated, so a
    malformed value raised TypeError in both gates and the CLI wrote no verdict.
  * A bit of a multi-bit wire prints as `\\q_gold [0] \\q_gate [0]`; neither
    name parser read it, so the producer published NOT_PROVEN with no names.
  * The design runner mapped INCONCLUSIVE + unproven straight to NOT_PROVEN
    without the producer's word or receipt.

The SAT logs, WaveJSON dumps and flattened-miter lines below are verbatim
output of the pinned image's yosys on the two designs named in each fixture.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROGS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGS))

import design_one_shot_runner as dosr  # noqa: E402
import lec_counterexample_search as cex  # noqa: E402
import lec_equivalence_check as pre  # noqa: E402
import lec_post_layout_check as post  # noqa: E402
import lec_run  # noqa: E402


_BANNER = r"""   ______                   ___       ___       _ _            _ _
  (_____ \                 / __)     / __)     (_) |          | | |
   _____) )___ ___   ___ _| |__    _| |__ _____ _| | _____  __| | |
  |  ____/ ___) _ \ / _ (_   __)  (_   __|____ | | || ___ |/ _  |_|
  | |   | |  | |_| | |_| || |       | |  / ___ | | || ____( (_| |_
  |_|   |_|   \___/ \___/ |_|       |_|  \_____|_|\_)_____)\____|_|
"""

# gold: y1 = a | b, y2 = a & b.  gate: y1 = a ^ b, y2 = a & b.  No
# equiv_simple, so BOTH points reach the search: y1 differs, y2 never does.
TWO_LOG = ("Solving problem with 168 variables and 427 clauses..\n"
           "SAT proof finished - model found: FAIL!\n" + _BANNER + """\
  Time Signal Name             Dec       Hex           Bin
  ---- --------------- ----------- --------- -------------
     1 \\a                        1         1             1
     1 \\b                        1         1             1
     1 \\cmp_y1                   0         0             0
     1 \\cmp_y2                   1         1             1
     1 \\trigger                  1         1             1
     1 \\y1                       1         1             1
     1 \\y2                       1         1             1
  ---- --------------- ----------- --------- -------------
     2 \\a                        0         0             0
     2 \\b                        0         0             0
     2 \\cmp_y1                   1         1             1
     2 \\cmp_y2                   1         1             1
     2 \\trigger                  0         0             0
     2 \\y1                       0         0             0
     2 \\y2                       0         0             0
  ---- --------------- ----------- --------- -------------
     3 \\a                        0         0             0
     3 \\b                        0         0             0
     3 \\cmp_y1                   1         1             1
     3 \\cmp_y2                   1         1             1
     3 \\trigger                  0         0             0
     3 \\y1                       0         0             0
     3 \\y2                       0         0             0

Dumping SAT model to WaveJSON file '/work/two/trace.json'.
""")
TWO_JSON = {"signal": [
    {"name": "y2", "wave": "10."}, {"name": "y1", "wave": "10."},
    {"name": "trigger", "wave": "10."}, {"name": "cmp_y2", "wave": "1.."},
    {"name": "cmp_y1", "wave": "01."}, {"name": "b", "wave": "10."},
    {"name": "a", "wave": "10."}], "config": {"hscale": 0.25}}
TWO_FLAT = """\
  wire output 3 \\y2
  wire \\y2_gold
  wire output 4 \\y1
  wire \\y1_gold
  wire \\y1_gate
  wire input 1 \\a
  wire input 2 \\b
  wire \\y2_gate
  wire output 6 \\cmp_y2
  wire output 7 \\cmp_y1
  wire output 5 \\trigger
  cell $and $and$/work/two/gate.v:1$4_gate
  cell $xor $xor$/work/two/gate.v:1$3_gate
  cell $or $or$/work/two/gold.v:1$1_gold
  cell $equiv $auto$equiv_make.cc:258:find_same_wires$6
  cell $and $and$/work/two/gold.v:1$2_gold
  cell $equiv $auto$equiv_make.cc:258:find_same_wires$5
  cell $eqx $auto$equiv_miter.cc:223:make_stuff$7
  cell $eqx $auto$equiv_miter.cc:222:make_stuff$9
  cell $logic_or $auto$equiv_miter.cc:222:make_stuff$11
  cell $not $auto$equiv_miter.cc:241:make_stuff$13
  cell $eqx $auto$equiv_miter.cc:223:make_stuff$15
"""

# gold: 7-bit up-counter, sync reset to 0, y = (s == 3).
# gate: 7-bit down-counter, sync reset to 127, y = (g == 124).
# Equivalent from reset; `equiv_induct` leaves y unproven; SAT -seq 8 from the
# zero state (g = 0 is unreachable) finds y differing at steps 4 and 5.
COUNTER_LOG = ("Solving problem with 2590 variables and 7013 clauses..\n"
               "SAT proof finished - model found: FAIL!\n" + _BANNER + """\
  Time Signal Name             Dec       Hex           Bin
  ---- --------------- ----------- --------- -------------
  init \\g_gate                   0         0       0000000
  init \\s_gold                   0         0       0000000
  ---- --------------- ----------- --------- -------------
     1 \\clk                      0         0             0
     1 \\cmp_y                    1         1             1
     1 \\rst                      0         0             0
     1 \\trigger                  0         0             0
     1 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     2 \\clk                      0         0             0
     2 \\cmp_y                    1         1             1
     2 \\rst                      0         0             0
     2 \\trigger                  0         0             0
     2 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     3 \\clk                      0         0             0
     3 \\cmp_y                    1         1             1
     3 \\rst                      0         0             0
     3 \\trigger                  0         0             0
     3 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     4 \\clk                      0         0             0
     4 \\cmp_y                    0         0             0
     4 \\rst                      0         0             0
     4 \\trigger                  1         1             1
     4 \\y                        1         1             1
  ---- --------------- ----------- --------- -------------
     5 \\clk                      0         0             0
     5 \\cmp_y                    0         0             0
     5 \\rst                      0         0             0
     5 \\trigger                  1         1             1
     5 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     6 \\clk                      0         0             0
     6 \\cmp_y                    1         1             1
     6 \\rst                      1         1             1
     6 \\trigger                  0         0             0
     6 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     7 \\clk                      0         0             0
     7 \\cmp_y                    1         1             1
     7 \\rst                      0         0             0
     7 \\trigger                  0         0             0
     7 \\y                        0         0             0
  ---- --------------- ----------- --------- -------------
     8 \\clk                      0         0             0
     8 \\cmp_y                    1         1             1
     8 \\rst                      1         1             1
     8 \\trigger                  0         0             0
     8 \\y                        0         0             0

Dumping SAT model to WaveJSON file '/work/counter/trace.json'.
""")
COUNTER_JSON = {"signal": [
    {"name": "y", "wave": "40..10..."}, {"name": "trigger", "wave": "40..1.0.."},
    {"name": "rst", "wave": "40....101"}, {"name": "cmp_y", "wave": "41..0.1.."},
    {"name": "clk", "wave": "40......."},
    {"name": "s_gold", "wave": "=44444444",
     "data": ["0000000", "", "", "", "", "", "", "", ""]},
    {"name": "g_gate", "wave": "=44444444",
     "data": ["0000000", "", "", "", "", "", "", "", ""]}],
    "config": {"hscale": 1.75}}
COUNTER_FLAT = """\
  wire output 2 \\y
  wire \\y_gold
  wire width 7 \\s_gold
  wire input 3 \\rst
  wire input 1 \\clk
  wire width 7 \\g_gate
  wire \\y_gate
  wire output 5 \\cmp_y
  wire output 4 \\trigger
  cell $eq $eq$/work/counter/gate.v:4$6_gate
  cell $dff $procdff$13_gate
  cell $mux $procmux$8_gate
  cell $sub $sub$/work/counter/gate.v:3$5_gate
  cell $add $add$/work/counter/gold.v:3$2_gold
  cell $mux $procmux$11_gold
  cell $dff $procdff$14_gold
  cell $eq $eq$/work/counter/gold.v:4$3_gold
  cell $equiv $auto$equiv_make.cc:258:find_same_wires$15
  cell $eqx $auto$equiv_miter.cc:223:make_stuff$16
  cell $not $auto$equiv_miter.cc:241:make_stuff$22
"""


def _interpret(tmp_path, log, trace_doc, flat, bound, names):
    flat_il = tmp_path / "flat.il"
    trace_json = tmp_path / "trace.json"
    flat_il.write_text(flat)
    trace_json.write_text(json.dumps(trace_doc))
    return cex.interpret(log, flat_il, trace_json, bound=bound,
                         bound_source="flow/lec_counterexample_search.json",
                         point_names=names, tool_version="Yosys 0.69+",
                         run_identity="fixture-run")


def _write_lec(project: Path, doc: dict) -> None:
    reports = project / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "lec.json").write_text(json.dumps(doc))


# ---------------------------------------------------------------------------
# The trace decoder
# ---------------------------------------------------------------------------

def test_two_outputs_the_trigger_step_names_exactly_the_differing_point(tmp_path):
    record = _interpret(tmp_path, TWO_LOG, TWO_JSON, TWO_FLAT, 3, ["y1", "y2"])
    assert record["result"] == "COUNTEREXAMPLE"
    assert record["completeness"] == "COMPLETE"
    assert [step["mismatched_points"] for step in record["trace"]] == [
        ["y1"], [], []]
    assert record["trace"][0]["inputs"] == {"a": "1", "b": "1"}
    assert cex.decides_non_equivalence(record)


def test_stateful_steps_align_with_the_sat_table_not_the_init_column(tmp_path):
    record = _interpret(tmp_path, COUNTER_LOG, COUNTER_JSON, COUNTER_FLAT, 8, ["y"])
    assert [step["cycle"] for step in record["trace"]] == list(range(1, 9))
    assert [step["cycle"] for step in record["trace"]
            if step["mismatched_points"]] == [4, 5]
    assert record["trace"][5]["inputs"] == {"clk": "0", "rst": "1"}
    assert record["initial_state"] == {"g_gate": "0000000", "s_gold": "0000000"}


def test_a_model_whose_compares_are_all_equal_is_not_a_trace(tmp_path):
    log = TWO_LOG.replace("\\cmp_y1                   0         0             0",
                          "\\cmp_y1                   1         1             1")
    record = _interpret(tmp_path, log, TWO_JSON, TWO_FLAT, 3, ["y1", "y2"])
    assert record["result"] == "NOT_RUN"
    assert "no compare output is 0" in record["reason"]


def test_the_search_script_models_undef_with_the_undef_miter():
    text = cex.script("/w/e.il", "/w/f.il", "/w/t.json", 20, 300)
    assert "equiv_miter -trigger -cmp -undef" in text
    sat = next(line for line in text.splitlines() if line.startswith("sat "))
    assert "-enable_undef" in sat.split()
    assert "-set-def-inputs" in sat.split()


# ---------------------------------------------------------------------------
# What a model may decide
# ---------------------------------------------------------------------------

def test_the_up_down_counter_model_is_a_candidate_never_non_equivalent(tmp_path):
    record = _interpret(tmp_path, COUNTER_LOG, COUNTER_JSON, COUNTER_FLAT, 8, ["y"])
    assert record["result"] != "COUNTEREXAMPLE"
    assert record["result"] == cex.CANDIDATE
    assert record["completeness"] == "BOUNDED"
    assert not cex.decides_non_equivalence(record)
    decision = cex.decide_residual("INCONCLUSIVE", record, ["y"], 1, False)
    assert decision["verdict"] == "NOT_PROVEN"
    assert cex.CANDIDATE in decision["explanation"]
    assert "first mismatch at cycle 4" in decision["explanation"]


@pytest.mark.parametrize("state_cell", ["$mem_v2", "$sr", "$aldffe",
                                        "sky130_fd_sc_hd__dfrtp_1"])
def test_statelessness_needs_positive_evidence(tmp_path, state_cell):
    """A miter holding a cell outside yosys's combinational vocabulary is not
    COMPLETE, whatever the cell is called; its model cannot decide."""
    flat = TWO_FLAT + f"  cell {state_cell} $auto$state$1\n"
    record = _interpret(tmp_path, TWO_LOG, TWO_JSON, flat, 3, ["y1", "y2"])
    assert record["completeness"] == "BOUNDED"
    assert record["result"] == cex.CANDIDATE
    assert not cex.decides_non_equivalence(record)


def _candidate_doc(tmp_path, verdict="NOT_PROVEN"):
    search = _interpret(tmp_path, COUNTER_LOG, COUNTER_JSON, COUNTER_FLAT, 8, ["y"])
    return {"equivalent": False, "verdict": verdict, "total_points": 1,
            "proven_points": 0, "unproven_points": 1,
            "non_equivalent_points": 0, "unproven_point_names": ["y"],
            "miter_stateless": False, "counterexample_search": search}


def test_both_gates_keep_the_candidate_not_proven(tmp_path):
    doc = _candidate_doc(tmp_path)
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "NOT_PROVEN"
    assert post_result["verdict"] == "NOT_PROVEN"
    assert post_result["counterexample_search"]["trace"][3]["mismatched_points"] == ["y"]
    assert "first mismatch at cycle 4" in post_result["findings"][0]
    project = tmp_path / "project"
    _write_lec(project, doc)
    pre_result = pre.audit(project)
    assert pre_result.verdict == "NOT_PROVEN"
    assert cex.CANDIDATE in pre_result.findings[-1].message


def test_a_non_equivalent_record_resting_on_a_bounded_model_is_not_a_fail(tmp_path):
    """The record the reviewed producer wrote for the up/down counters: a
    COUNTEREXAMPLE word on a BOUNDED miter, verdict NON_EQUIVALENT, one
    non-equivalent point. Neither gate may FAIL the design on it."""
    doc = _candidate_doc(tmp_path, verdict="NON_EQUIVALENT")
    doc["non_equivalent_points"] = 1
    doc["counterexample_search"]["result"] = "COUNTEREXAMPLE"
    post_result = post.evaluate_report(doc)
    assert post_result["verdict"] != "NON_EQUIVALENT"
    assert post_result["verdict"] == "RUN_ERROR"
    assert "candidate" in post_result["findings"][0]
    project = tmp_path / "project"
    _write_lec(project, doc)
    pre_result = pre.audit(project)
    assert all(f.rule != "LEC_NOT_EQUIVALENT" for f in pre_result.findings)
    assert pre_result.verdict == "RUN_ERROR"
    assert any("candidate" in f.message for f in pre_result.findings)


def test_a_complete_counterexample_still_fails_both_gates(tmp_path):
    search = _interpret(tmp_path, TWO_LOG, TWO_JSON, TWO_FLAT, 3, ["y1", "y2"])
    doc = {"equivalent": False, "verdict": "NON_EQUIVALENT", "total_points": 2,
           "proven_points": 0, "unproven_points": 2, "non_equivalent_points": 1,
           "unproven_point_names": ["y1", "y2"], "miter_stateless": True,
           "counterexample_search": search}
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "NON_EQUIVALENT"
    project = tmp_path / "project"
    _write_lec(project, doc)
    assert any(f.rule == "LEC_NOT_EQUIVALENT" for f in pre.audit(project).findings)


def test_the_decision_names_the_complete_mismatch_and_its_count(tmp_path):
    search = _interpret(tmp_path, TWO_LOG, TWO_JSON, TWO_FLAT, 3, ["y1", "y2"])
    decision = cex.decide_residual("INCONCLUSIVE", search, ["y1", "y2"], 2, True)
    assert decision["verdict"] == "NON_EQUIVALENT"
    assert decision["non_equivalent_points"] == 1
    assert decision["explanation"].startswith("NON_EQUIVALENT")


def test_unbound_names_are_a_named_run_error_at_the_producer():
    search = cex.not_run("terminal equivalence IL was not written", [],
                         run_identity="fixture-run")
    decision = cex.decide_residual("UNPROVEN", search, [], 2, False)
    assert decision["verdict"] == "RUN_ERROR"
    assert "left 2 point(s) unproven but named 0" in decision["explanation"]
    decision = cex.decide_residual("UNPROVEN", search, ["q[0]"], 2, False)
    assert decision["verdict"] == "RUN_ERROR"
    assert "named 1" in decision["explanation"]


# ---------------------------------------------------------------------------
# unproven_point_names is validated before it is iterated
# ---------------------------------------------------------------------------

def _names_fixture(names, unproven=1):
    doc = {"equivalent": False, "verdict": "NOT_PROVEN", "total_points": 3,
           "proven_points": 3 - unproven, "unproven_points": unproven,
           "non_equivalent_points": 0, "unproven_point_names": names,
           "counterexample_search": cex.not_run(
               "SAT executable unavailable", ["out"], run_identity="fixture-run")}
    if unproven == 0:
        doc.update(verdict="PROVEN_EQUIVALENT", equivalent=True)
    return doc


@pytest.mark.parametrize("names,unproven", [
    (42, 1), (7, 0), ("out", 1), ([""], 1), (["out", 3], 1)])
def test_malformed_point_names_are_a_named_run_error_at_both_gates(
        tmp_path, names, unproven):
    doc = _names_fixture(names, unproven)
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "RUN_ERROR"
    assert post_result["findings"][0].startswith("LEC_POST_POINT_NAMES_INVALID")
    project = tmp_path / "project"
    _write_lec(project, doc)
    pre_result = pre.audit(project)
    assert pre_result.verdict == "RUN_ERROR"
    assert [f.rule for f in pre_result.findings] == ["LEC_POINT_NAMES_INVALID"]


@pytest.mark.parametrize("names,unproven", [(42, 1), (7, 0)])
def test_malformed_point_names_cli_writes_its_verdict(tmp_path, capsys,
                                                      names, unproven):
    report = tmp_path / "reports" / "phase3" / "lec_post_layout.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(_names_fixture(names, unproven)))
    output = tmp_path / "verdict.json"
    assert post.main([str(tmp_path), "--json", str(output)]) == 1
    assert json.loads(output.read_text())["verdict"] == "RUN_ERROR"
    assert json.loads(capsys.readouterr().out)["verdict"] == "RUN_ERROR"
    _write_lec(tmp_path, _names_fixture(names, unproven))
    gate_json = tmp_path / "gate.json"
    assert pre.main([str(tmp_path), "--json", str(gate_json)]) == 1
    assert json.loads(gate_json.read_text())["verdict"] == "RUN_ERROR"


# ---------------------------------------------------------------------------
# The bit-sliced unproven line, verbatim from the pinned image (a 3-bit FSM
# register paired by name, no splitnets).
# ---------------------------------------------------------------------------

_BIT_STATUS = """\
Found 4 $equiv cells in equiv:
  Of those cells 1 are proven and 3 are unproven.
  Unproven $equiv $auto$equiv_make.cc:258:find_same_wires$22: \\y_gold \\y_gate
  Unproven $equiv $auto$equiv_make.cc:295:find_same_wires$20: \\state_gold [1] \\state_gate [1]
  Unproven $equiv $auto$equiv_make.cc:295:find_same_wires$19: \\state_gold [0] \\state_gate [0]
  Unproven $equiv $auto$equiv_make.cc:295:find_same_wires$18: \\state_gold [2] \\other_gate [2]
  Unproven $equiv $auto$equiv_make.cc:295:find_same_wires$17: \\state_gold [2] \\state_gate [3]
Found a total of 3 unproven $equiv cells.
"""


def test_both_name_parsers_read_one_bit_of_a_multi_bit_wire():
    expected = {"y", "state[0]", "state[1]"}
    assert set(lec_run.unproven_names(_BIT_STATUS)) == expected
    assert set(post.parse_unproven_points(_BIT_STATUS)) == expected
    assert len(post.parse_unproven_points(_BIT_STATUS)) == 3


# ---------------------------------------------------------------------------
# The design runner's step status for the same records
# ---------------------------------------------------------------------------

def _status(tmp_path, doc):
    f = tmp_path / "lec.json"
    f.write_text(json.dumps(doc))
    return dosr.lec_step_status_from_report(f)[0]


def test_inconclusive_unproven_without_the_producer_word_is_not_not_proven(tmp_path):
    doc = {"verdict": "INCONCLUSIVE", "unproven_points": 5, "total_points": 10,
           "compared_points": 5, "step_budget_exhausted": True}
    assert _status(tmp_path, doc) != "NOT_PROVEN"
    assert _status(tmp_path, doc) == "NOT_MEASURED"
    assert dosr.lec_inconclusive_reason_class(doc) == "budget_exhausted"


def test_not_proven_with_an_invalid_receipt_is_not_not_proven(tmp_path):
    doc = _names_fixture(["out"])
    doc["counterexample_search"] = {"result": "NOT_RUN"}
    assert _status(tmp_path, doc) == "NOT_MEASURED"
    assert dosr.lec_not_proven_receipt_error(doc)
    project = tmp_path / "project"
    _write_lec(project, doc)
    assert pre.audit(project).verdict == "RUN_ERROR"


def test_not_proven_step_text_names_why_the_search_did_not_run(tmp_path):
    doc = _names_fixture(["out"])
    doc["counterexample_search"] = cex.not_run(
        "SAT executable unavailable", ["out"], run_identity="fixture-run")
    assert _status(tmp_path, doc) == "NOT_PROVEN"
    text = dosr.lec_not_proven_step_reason(doc)
    assert "NOT RUN: SAT executable unavailable" in text
    assert "K=" not in text
    doc["step_budget_exhausted"] = True
    assert "stopped before it finished" in dosr.lec_not_proven_step_reason(doc)


# ---------------------------------------------------------------------------
# lec_run, the pre-layout producer, end to end with its yosys runs faked
# ---------------------------------------------------------------------------

_E2E_RTL = "module m(input clk, input rst, output y); endmodule\n"
_E2E_GATE = ("module m(input clk, input rst, output y);\n"
             "  DFF u0 (.D(rst), .CLK(clk), .Q(y));\n"
             "endmodule\n")
# Measured with vibeic-eda:0.3.84 / Yosys 0.69+.  The two source designs
# reset to corresponding states and emit y on every fourth state.  A one-rung
# proof leaves y open; the bounded search starts h at illegal 0000 and finds
# a model at cycle 4.  The full proof can prove this small pair, but an open
# rung must never turn the unreachable model into NON_EQUIVALENT.
_BINARY_GOLD = """module top(input clk, input rst, output y);
  reg [1:0] s;
  always @(posedge clk) if (rst) s <= 2'b00; else s <= s + 2'b01;
  assign y = (s == 2'b11);
endmodule
"""
_ONEHOT_GATE = """module top(input clk, input rst, output y);
  reg [3:0] h;
  always @(posedge clk) if (rst) h <= 4'b0001;
  else h <= {h[2:0], h[3]};
  assign y = h[3];
endmodule
"""
_ONEHOT_FLAT = """wire input 1 \\clk
wire input 2 \\rst
wire output 3 \\cmp_y
cell $dff $gold_state
cell $dff $gate_state
"""
_ONEHOT_MODEL = """SAT proof finished - model found: FAIL!
  Time Signal Name             Dec       Hex           Bin
  ---- --------------- ----------- --------- -------------
  init \\h[0]_gate                0         0             0
  init \\h[1]_gate                0         0             0
  init \\h[2]_gate                0         0             0
  init \\h[3]_gate                0         0             0
  init \\s[0]_gold                0         0             0
  init \\s[1]_gold                0         0             0
  ---- --------------- ----------- --------- -------------
     1 \\clk                      0         0             0
     1 \\cmp_y                    1         1             1
     1 \\rst                      0         0             0
  ---- --------------- ----------- --------- -------------
     2 \\clk                      0         0             0
     2 \\cmp_y                    1         1             1
     2 \\rst                      0         0             0
  ---- --------------- ----------- --------- -------------
     3 \\clk                      0         0             0
     3 \\cmp_y                    1         1             1
     3 \\rst                      0         0             0
  ---- --------------- ----------- --------- -------------
     4 \\clk                      0         0             0
     4 \\cmp_y                    0         0             0
     4 \\rst                      1         1             1
  ---- --------------- ----------- --------- -------------
"""
_ONEHOT_WAVE = {"signal": [
    {"name": "cmp_y", "wave": "41..0"},
    {"name": "rst", "wave": "40..1"},
    {"name": "clk", "wave": "40..."},
    {"name": "h[0]_gate", "wave": "04444"},
    {"name": "s[0]_gold", "wave": "04444"},
]}
# A stateful miter whose induction made no progress on one point.
_STATEFUL_PROOF_LOG = """\
=== equiv ===
     1   $dff
     1   $equiv
     1   $eq
Executing EQUIV_INDUCT pass.
Found 1 unproven $equiv cells in module equiv:
  Proving existence of base case for step 1. (683 clauses over 261 variables)
  Proving induction step 1. (1503 clauses over 562 variables)
  Proof for induction step failed. Extending to next time step.
Proved 0 previously unproven $equiv cells.
Executing EQUIV_STATUS pass
Found 1 $equiv cells in equiv:
  Of those cells 0 are proven and 1 are unproven.
  Unproven $equiv $auto$equiv_make.cc:258:find_same_wires$15: \\y_gold \\y_gate
Found a total of 1 unproven $equiv cells.
"""


def _drive_lec_run(monkeypatch, tmp_path, proof_log, search_log, flat,
                   trace_doc, *, gold=_E2E_RTL, gate=_E2E_GATE, top="m",
                   capture_log=None):
    """Every file the real yosys runs would write is written: the terminal
    equivalence IL, the flattened search miter and the WaveJSON dump."""
    def fake_run(_container, script, *_a, **_k):
        text = Path(script).read_text()
        for path in re.findall(r"write_rtlil (\S+)", text):
            Path(path).write_text(flat if "lec_cex_flat" in path
                                  else "module \\equiv\nend\n")
        for path in re.findall(r"-dump_json (\S+)", text):
            Path(path).write_text(json.dumps(trace_doc))
        if "equiv_miter" in text:
            return True, search_log
        if "lec_equiv_capture." in Path(script).name and capture_log is not None:
            return True, capture_log
        return True, proof_log

    monkeypatch.setattr(lec_run, "run_yosys_equiv", fake_run)
    monkeypatch.setattr(lec_run, "_container_available", lambda _c: True)
    monkeypatch.setattr(lec_run, "_container_file_exists", lambda *_a: False)
    monkeypatch.setattr(lec_run, "_yosys_version", lambda _c: "Yosys 0.69")
    monkeypatch.setattr(lec_run, "_container_image_digest",
                        lambda _c: "sha256:image-id")
    project = tmp_path / "project"
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "phase2/stage2/synth").mkdir(parents=True)
    (project / "phase2/stage1/rtl/m.v").write_text(gold)
    (project / "phase2/stage2/synth/netlist.v").write_text(gate)
    lec_run.main([str(project), "--top", top, "--container", "fake",
                  "--liberty", "/missing"])
    return json.loads((project / "reports/lec.json").read_text())


def test_binary_counter_and_onehot_ring_never_become_non_equivalent(
        monkeypatch, tmp_path):
    # This is the real Yosys model from the declared-reset pair above.  The
    # initial all-zero one-hot register is unreachable after reset.  Drive
    # the actual producer and both gates, while faking only Yosys file writes.
    report = _drive_lec_run(
        monkeypatch, tmp_path, _STATEFUL_PROOF_LOG, _ONEHOT_MODEL,
        _ONEHOT_FLAT, _ONEHOT_WAVE, gold=_BINARY_GOLD, gate=_ONEHOT_GATE,
        top="top")
    assert report["verdict"] == "NOT_PROVEN"
    assert report["counterexample_search"]["result"] == cex.CANDIDATE
    assert report["counterexample_search"]["initial_state"]["h[0]_gate"] == "0"
    assert report["counterexample_search"]["trace"][3]["mismatched_points"] == ["y"]
    assert post.evaluate_report(report)["verdict"] == "NOT_PROVEN"
    project = tmp_path / "gate"
    _write_lec(project, report)
    assert pre.audit(project).verdict == "NOT_PROVEN"


def test_lec_run_keeps_a_stateful_model_not_proven_and_says_so(monkeypatch, tmp_path):
    report = _drive_lec_run(monkeypatch, tmp_path, _STATEFUL_PROOF_LOG,
                            COUNTER_LOG, COUNTER_FLAT, COUNTER_JSON)
    assert report["verdict"] != "NON_EQUIVALENT"
    assert report["verdict"] == "NOT_PROVEN"
    assert report["counterexample_search"]["result"] == "CANDIDATE_COUNTEREXAMPLE"
    assert report["non_equivalent_points"] == 0
    assert report["verdict_explanation"].startswith("NOT_PROVEN")
    assert "first mismatch at cycle 4" in report["verdict_explanation"]
    assert "INCONCLUSIVE" not in report["verdict_explanation"]


def test_sat_handoff_refuses_a_recaptured_different_residual(monkeypatch,
                                                             tmp_path):
    changed = _STATEFUL_PROOF_LOG.replace(
        "Of those cells 0 are proven and 1 are unproven.",
        "Of those cells 1 are proven and 0 are unproven.")
    report = _drive_lec_run(monkeypatch, tmp_path, _STATEFUL_PROOF_LOG,
                            COUNTER_LOG, COUNTER_FLAT, COUNTER_JSON,
                            capture_log=changed)
    assert report["counterexample_handoff_capture"]["status"] == "NOT_RUN"
    assert report["counterexample_search"]["result"] == "NOT_RUN"
    assert report["verdict"] == "NOT_PROVEN"


def test_lec_run_rewrites_the_explanation_when_a_complete_model_decides(
        monkeypatch, tmp_path):
    log = _STATEFUL_PROOF_LOG.replace("     1   $dff\n", "")
    report = _drive_lec_run(monkeypatch, tmp_path, log, TWO_LOG, TWO_FLAT,
                            TWO_JSON)
    assert report["counterexample_search"]["result"] == "COUNTEREXAMPLE"
    assert report["verdict"] == "NON_EQUIVALENT"
    assert report["non_equivalent_points"] == 1
    assert report["verdict_explanation"].startswith("NON_EQUIVALENT")
    assert "['y1']" in report["verdict_explanation"]
