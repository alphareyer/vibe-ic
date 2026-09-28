"""Value controls for the LEC residual verdict, including the flow vocabulary."""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from _plugin_tree import plugin_path

sys.path.insert(0, str(plugin_path() / "programs"))
import lec_equivalence_check as pre
import lec_post_layout_check as post
import verdict
import lec_counterexample_search as cex
import _docker_memory as _dmem
import flow_compliance_check as flow
import flow_dashboard_data as dashboard
import vibe_ic_one_shot_runner as frontdoor
import phase3_one_shot_runner as phase3
import phase23_completion_self_audit_check as completion
import lec_run


def _residual():
    return {
        "equivalent": False, "verdict": "NOT_PROVEN",
        "total_points": 3, "proven_points": 2, "unproven_points": 1,
        "non_equivalent_points": 0, "unproven_point_names": ["out"],
        "counterexample_search": {
            "result": "NONE_FOUND", "method": "Yosys equiv_miter SAT",
            "reason": "no counterexample within 20 cycles under the recorded initial-state policy",
            "bound_cycles": 20, "bound_source": "flow/lec_counterexample_search.json",
            "completeness": "BOUNDED", "tool": "yosys", "tool_version": "0.3.84-test",
            "run_identity": "fixture-run", "point_names": ["out"],
        },
    }


def test_post_layout_residual_is_not_proven():
    result = post.evaluate_report(_residual())
    assert result["result"] == "NOT_PROVEN"
    assert result["total_points"] == 3
    assert result["unproven_point_names"] == ["out"]
    assert result["counterexample_search"]["result"] == "NONE_FOUND"


def test_pre_layout_residual_is_not_proven(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(_residual()))
    result = pre.audit(tmp_path)
    assert result.verdict == "NOT_PROVEN"
    assert not result.passed
    assert result.summary["unproven_points"] == 1


def test_not_run_is_explicit_and_never_passes(tmp_path):
    doc = _residual()
    doc["counterexample_search"] = cex.not_run(
        "solver unavailable", ["out"], run_identity="fixture-run")
    result = post.evaluate_report(doc)
    assert result["result"] == "NOT_PROVEN"
    assert result["counterexample_search"]["reason"] == "solver unavailable"
    assert "counterexample search NOT RUN: solver unavailable" in result["findings"][0]
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict == "NOT_PROVEN"
    assert "counterexample search NOT RUN: solver unavailable" in pre_result.findings[-1].message


def test_decided_failure_survives_not_run_at_both_gates(tmp_path):
    doc = _residual()
    doc.update(verdict="NON_EQUIVALENT", non_equivalent_points=0)
    doc["counterexample_search"] = cex.not_run(
        "terminal IL unavailable", ["out"], run_identity="fixture-run")
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "NON_EQUIVALENT"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict != "NOT_PROVEN"
    assert not pre_result.passed


def test_combinational_not_run_is_execution_error_at_both_gates(tmp_path):
    doc = _residual()
    doc["miter_stateless"] = True
    doc["counterexample_search"] = cex.not_run(
        "unsupported SAT cell", ["out"], run_identity="fixture-run")
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "RUN_ERROR"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict != "NOT_PROVEN"
    assert any("SAT" in f.rule for f in pre_result.findings)


def test_bare_not_run_is_invalid_evidence_at_both_gates(tmp_path):
    doc = _residual()
    doc["counterexample_search"] = {"result": "NOT_RUN"}
    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "RUN_ERROR"
    assert any("NOT_RUN" in f for f in post_result["findings"])
    assert all("no counterexample found" not in f for f in post_result["findings"])
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict != "NOT_PROVEN"
    assert any("NOT_RUN" in f.message for f in pre_result.findings)
    assert all("no counterexample found" not in f.message for f in pre_result.findings)


@pytest.mark.parametrize("missing", [
    "reason", "method", "bound_cycles", "bound_source", "run_identity",
    "point_names", "tool", "tool_version", "completeness",
    "initial_state_policy",
])
def test_each_not_run_field_is_required_by_both_gates(tmp_path, missing):
    doc = _residual()
    search = cex.not_run("SAT executable unavailable", ["out"],
                         run_identity="fixture-run")
    del search[missing]
    doc["counterexample_search"] = search
    post_result = post.evaluate_report(doc)
    assert post_result["verdict"] == "RUN_ERROR"
    assert post_result["result"] == "FAIL"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict == "RUN_ERROR"
    assert any(f.rule == "LEC_NOT_RUN_EVIDENCE_INVALID" for f in pre_result.findings)


@pytest.mark.parametrize("bad_names", [42, "out", [""], ["out", 42]])
def test_malformed_not_run_point_names_are_named_run_errors(tmp_path, bad_names):
    doc = _residual()
    search = cex.not_run("SAT executable unavailable", ["out"],
                         run_identity="fixture-run")
    search["point_names"] = bad_names
    doc["counterexample_search"] = search

    post_result = post.evaluate_report(doc)
    assert post_result["result"] == "FAIL"
    assert post_result["verdict"] == "RUN_ERROR"
    assert any("LEC_POST_NOT_RUN_EVIDENCE_INVALID" in finding
               and "point_names" in finding
               for finding in post_result["findings"])

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    pre_result = pre.audit(tmp_path)
    assert pre_result.verdict == "RUN_ERROR"
    assert any(f.rule == "LEC_NOT_RUN_EVIDENCE_INVALID"
               and "point_names" in f.message for f in pre_result.findings)


def test_malformed_not_run_cli_writes_verdict_json(tmp_path, capsys):
    doc = _residual()
    search = cex.not_run("SAT executable unavailable", ["out"],
                         run_identity="fixture-run")
    search["point_names"] = 42
    doc["counterexample_search"] = search
    report = tmp_path / "reports" / "phase3" / "lec_post_layout.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(doc))
    output = tmp_path / "verdict.json"

    assert post.main([str(tmp_path), "--json", str(output)]) == 1
    verdict_doc = json.loads(output.read_text())
    assert verdict_doc["verdict"] == "RUN_ERROR"
    assert "point_names" in verdict_doc["findings"][0]
    assert json.loads(capsys.readouterr().out)["verdict"] == "RUN_ERROR"


def test_complete_sat_disposition_requires_the_named_search(tmp_path):
    doc = _residual()
    doc.update(equivalent=True, verdict="PROVEN_EQUIVALENT",
               proven_points=3, unproven_points=0, counterexample_search={})
    assert post.evaluate_report(doc)["result"] == "FAIL"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    assert pre.audit(tmp_path).passed is False
    doc["counterexample_search"] = _residual()["counterexample_search"] | {
        "completeness": "COMPLETE"}
    assert post.evaluate_report(doc)["result"] == "PASS"
    (reports / "lec.json").write_text(json.dumps(doc))
    assert pre.audit(tmp_path).passed is True
    del doc["counterexample_search"]["reason"]
    assert post.evaluate_report(doc)["result"] == "FAIL"
    (reports / "lec.json").write_text(json.dumps(doc))
    assert pre.audit(tmp_path).passed is False


def test_decided_fail_survives_a_complete_sat_claim(tmp_path):
    doc = _residual()
    doc["verdict"] = "FAIL"
    doc["counterexample_search"]["completeness"] = "COMPLETE"
    assert post.evaluate_report(doc)["result"] == "FAIL"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    assert pre.audit(tmp_path).verdict == "FAIL"


def test_counterexample_is_fail_with_trace():
    doc = _residual()
    doc["counterexample_search"].update({
        "result": "COUNTEREXAMPLE", "trace": [{"cycle": 1, "inputs": {"a": "0"}}],
    })
    result = post.evaluate_report(doc)
    assert result["result"] == "FAIL"
    assert result["counterexample_search"]["trace"][0]["inputs"]["a"] == "0"
    assert result["unproven_point_names"] == ["out"]


def test_run_rollup_preserves_not_proven_and_fail_dominates():
    np = verdict.StepVerdict.not_proven("13", "LEC", reason="1 of 3 unproven")
    assert verdict.run_verdict([verdict.StepVerdict.pass_("12"), np]) is verdict.Verdict.NOT_PROVEN
    assert verdict.run_verdict([np, verdict.StepVerdict.fail("14")]) is verdict.Verdict.FAIL
    assert not np.is_green


def test_frontdoor_and_dashboard_preserve_not_proven(tmp_path):
    assert frontdoor._aggregate(["PASS", "NOT_PROVEN"]) == "NOT_PROVEN"
    assert frontdoor._aggregate(["FAIL", "NOT_PROVEN"]) == "FAIL"
    assert frontdoor._aggregate(["PASS"], ["NOT_PROVEN"]) == "NOT_PROVEN"
    assert dashboard._map_compliance_status("NOT_PROVEN") == "not_proven"
    reports = tmp_path / "reports" / "orchestrator"
    reports.mkdir(parents=True)
    (reports / "phase2_one_shot.json").write_text(json.dumps({
        "steps": [{"name": "yosys_synth", "status": "NOT_PROVEN",
                   "detail": "1 of 3 points unproven"}]}))
    assert dashboard._runner_verdict_overrides(tmp_path)["9"]["status"] == "not_proven"


def test_bounded_phase3_audit_keeps_not_proven():
    assert phase3._bounded_window_verdict("PASS", ["NOT_PROVEN"]) == "NOT_PROVEN"
    assert phase3._bounded_window_verdict("NOT_PROVEN", ["NOT_MEASURED"]) == "NOT_PROVEN"
    assert phase3._bounded_window_verdict("NOT_PROVEN", ["FAIL"]) == "FAIL"
    assert frontdoor._bounded_window_verdict("NOT_PROVEN", {"later": "stale"}) == "NOT_PROVEN"
    assert frontdoor._bounded_window_verdict("FAIL", {"later": "stale"}) == "FAIL"


def test_completion_record_carries_open_point_census(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(_residual()))
    census = completion._lec_open_proofs(tmp_path)
    assert len(census) == 1
    assert census[0]["unproven_points"] == 1
    assert census[0]["unproven_point_names"] == ["out"]
    assert census[0]["counterexample_search"]["bound_cycles"] == 20


def test_actual_flow_gate_keeps_the_residual_word(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(_residual()))
    outcome = flow._check_program_exit_zero(
        tmp_path, "lec_equivalence_check . --json reports/lec_gate.json")
    assert outcome[0] is True
    assert outcome.verdict == "NOT_PROVEN"
    assert outcome.structured_verdict == "NOT_PROVEN"
    assert outcome.exit_code == 5
    passed, reasons = flow._evaluate_gate(
        tmp_path, {"program_exit_zero":
                   "lec_equivalence_check . --json reports/lec_gate.json"})
    assert passed
    assert any(r.startswith(flow._NOT_PROVEN_HINT_PREFIX) for r in reasons)
    step = flow.check_step(
        tmp_path,
        {"id": 13, "name": "LEC", "stage": "stage2",
         "gate": {"program_exit_zero":
                  "lec_equivalence_check . --json reports/lec_gate.json"}},
        waivers={})
    assert step.status == "NOT_PROVEN"


def _yosys(work, script_name):
    p = subprocess.run(
        ["docker", "run", *_dmem.docker_memory_flags(), "--rm", "-v", f"{work}:/work",
         "ghcr.io/vibeic/vibeic-eda:0.3.84", "--skip", "yosys",
         "-Q", "-T", "-s", f"/work/{script_name}"],
        capture_output=True, text=True, timeout=90)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout + p.stderr


def test_real_pinned_yosys_decides_mismatch_and_combinational_no_cex(tmp_path):
    for gate_expr, expected in (("~a", "COUNTEREXAMPLE"), ("a", "NONE_FOUND")):
        (tmp_path / "gold.v").write_text("module gold(input a, output y); assign y=a; endmodule\n")
        (tmp_path / "gate.v").write_text(
            f"module gate(input a, output y); assign y={gate_expr}; endmodule\n")
        (tmp_path / "proof.ys").write_text(
            "read_verilog /work/gold.v\nread_verilog /work/gate.v\n"
            "equiv_make gold gate equiv\nhierarchy -top equiv\n"
            "equiv_status\nwrite_rtlil /work/equiv.il\n")
        _yosys(tmp_path, "proof.ys")
        (tmp_path / "search.ys").write_text(
            cex.script("/work/equiv.il", "/work/flat.il", "/work/trace.json", 3, 30))
        log = _yosys(tmp_path, "search.ys")
        record = cex.interpret(
            log, tmp_path / "flat.il", tmp_path / "trace.json",
            bound=3, bound_source="test declaration", point_names=["y"],
            tool_version="pinned 0.3.84", run_identity="real-yosys")
        assert record["result"] == expected
        assert record["completeness"] == "COMPLETE"
        if expected == "COUNTEREXAMPLE":
            assert any(cycle["inputs"].get("a") in ("0", "1")
                       and "y" in cycle["mismatched_points"]
                       for cycle in record["trace"])


def test_real_pinned_yosys_sequential_zero_init_is_bounded(tmp_path):
    rtl = "module NAME(input clk,a, output reg y); always @(posedge clk) y<=a; endmodule\n"
    (tmp_path / "gold.v").write_text(rtl.replace("NAME", "gold"))
    (tmp_path / "gate.v").write_text(rtl.replace("NAME", "gate"))
    (tmp_path / "proof.ys").write_text(
        "read_verilog /work/gold.v\nread_verilog /work/gate.v\nproc\n"
        "equiv_make gold gate equiv\nhierarchy -top equiv\n"
        "equiv_status\nwrite_rtlil /work/equiv.il\n")
    _yosys(tmp_path, "proof.ys")
    (tmp_path / "search.ys").write_text(
        cex.script("/work/equiv.il", "/work/flat.il", "/work/trace.json", 3, 30))
    record = cex.interpret(
        _yosys(tmp_path, "search.ys"), tmp_path / "flat.il", tmp_path / "trace.json",
        bound=3, bound_source="test declaration", point_names=["y"],
        tool_version="pinned 0.3.84", run_identity="real-yosys")
    assert record["result"] == "NONE_FOUND"
    assert record["completeness"] == "BOUNDED"
    assert "zero" in record["initial_state_policy"]


# ---------------------------------------------------------------------------
# Controls on the pinned image: designs EQUIVALENT FROM RESET whose miter still
# holds a model from the unconstrained zero state. That model is a candidate
# and must never become NON_EQUIVALENT (review of 7af91b7bd).
# ---------------------------------------------------------------------------

_UP_COUNTER = """module gold(input clk, input rst, output y);
  reg [6:0] s;
  always @(posedge clk) if (rst) s <= 7'd0; else s <= s + 7'd1;
  assign y = (s == 7'd3);
endmodule
"""
_DOWN_COUNTER = """module gate(input clk, input rst, output y);
  reg [6:0] g;
  always @(posedge clk) if (rst) g <= 7'd127; else g <= g - 7'd1;
  assign y = (g == 7'd124);
endmodule
"""
# The same three-state machine, binary in gold and one-hot in gate, the state
# register paired by name with no encoding table.
_BINARY_FSM = """module gold(input clk, input rst, input go, output y);
  reg [2:0] state;
  always @(posedge clk)
    if (rst) state <= 3'd0;
    else if (go) state <= (state == 3'd2) ? 3'd0 : state + 3'd1;
  assign y = (state == 3'd2);
endmodule
"""
_ONEHOT_FSM = """module gate(input clk, input rst, input go, output y);
  reg [2:0] state;
  always @(posedge clk)
    if (rst) state <= 3'b001;
    else if (go) state <= {state[1:0], state[2]};
  assign y = state[2];
endmodule
"""


def _real_residual_search(work, gold, gate, proof_tail, bound):
    (work / "gold.v").write_text(gold)
    (work / "gate.v").write_text(gate)
    (work / "proof.ys").write_text(
        "read_verilog /work/gold.v\nread_verilog /work/gate.v\nproc\n"
        "opt_clean\nequiv_make gold gate equiv\nhierarchy -top equiv\n"
        + proof_tail + "equiv_status\nwrite_rtlil /work/equiv.il\n")
    proof_log = _yosys(work, "proof.ys")
    names = lec_run.unproven_names(proof_log)
    assert names == sorted(post.parse_unproven_points(proof_log))
    total = int(re.search(r"Found (\d+) \$equiv cells in equiv", proof_log).group(1))
    (work / "search.ys").write_text(cex.script(
        "/work/equiv.il", "/work/flat.il", "/work/trace.json", bound, 60))
    record = cex.interpret(
        _yosys(work, "search.ys"), work / "flat.il", work / "trace.json",
        bound=bound, bound_source="test declaration", point_names=names,
        tool_version="pinned 0.3.84", run_identity="real-yosys")
    return names, total, record


@pytest.mark.parametrize("gold,gate", [(_UP_COUNTER, _DOWN_COUNTER),
                                       (_BINARY_FSM, _ONEHOT_FSM)],
                         ids=["up_vs_down_counter", "binary_vs_onehot_fsm"])
def test_real_pinned_yosys_equivalent_from_reset_is_never_non_equivalent(
        tmp_path, gold, gate):
    names, total, record = _real_residual_search(
        tmp_path, gold, gate, "equiv_simple -seq 5\nequiv_induct\n", 8)
    assert names, "the proof must leave a residual for the search to examine"
    assert record["result"] != "COUNTEREXAMPLE"
    # yosys DOES find the model from the unreachable zero state; it is kept.
    assert record["result"] == cex.CANDIDATE
    assert record["completeness"] == "BOUNDED"
    assert any(step["mismatched_points"] for step in record["trace"])
    decision = cex.decide_residual("INCONCLUSIVE", record, names, len(names), False)
    assert decision["verdict"] == "NOT_PROVEN"
    doc = {"equivalent": False, "verdict": decision["verdict"],
           "total_points": total, "proven_points": total - len(names),
           "unproven_points": len(names), "non_equivalent_points": 0,
           "unproven_point_names": names, "miter_stateless": False,
           "counterexample_search": record}
    assert post.evaluate_report(doc)["result"] == "NOT_PROVEN"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "lec.json").write_text(json.dumps(doc))
    assert pre.audit(tmp_path).verdict == "NOT_PROVEN"


def test_real_pinned_yosys_finds_the_gold_zero_gate_one_mismatch(tmp_path):
    """`equiv_miter -undef` makes an undefined gold bit a don't-care; without
    SAT undef modelling the gold-0/gate-1 direction was masked (NONE_FOUND)."""
    names, _total, record = _real_residual_search(
        tmp_path, "module gold(input a, b, output y); assign y = a & b; endmodule\n",
        "module gate(input a, b, output y); assign y = a | b; endmodule\n", "", 1)
    assert names == ["y"]
    assert record["result"] == "COUNTEREXAMPLE"
    assert record["completeness"] == "COMPLETE"
    step = record["trace"][0]
    assert step["mismatched_points"] == ["y"]
    assert step["inputs"]["a"] != step["inputs"]["b"]


def test_real_pinned_yosys_trace_names_only_the_differing_output(tmp_path):
    names, _total, record = _real_residual_search(
        tmp_path,
        "module gold(input a, b, output y1, y2); assign y1 = a | b; "
        "assign y2 = a & b; endmodule\n",
        "module gate(input a, b, output y1, y2); assign y1 = a ^ b; "
        "assign y2 = a & b; endmodule\n", "", 3)
    assert names == ["y1", "y2"]
    assert record["result"] == "COUNTEREXAMPLE"
    flagged = [step for step in record["trace"] if step["mismatched_points"]]
    assert flagged
    for step in flagged:
        assert step["mismatched_points"] == ["y1"]
        assert step["inputs"] == {"a": "1", "b": "1"}
