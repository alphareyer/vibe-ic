"""Regression controls for #3417: tool absence is not a design verdict."""
import importlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

H = importlib.import_module("rtl_hygiene_lint")
BC = importlib.import_module("_blocker_classification")
FCC = importlib.import_module("flow_compliance_check")


LEAF = "module leaf(input a, output y); assign y = a; endmodule\n"
TOP = """module top(input source, output result);
wire routed;
leaf u_leaf(.a(source), .y(routed));
assign result = routed;
endmodule
"""


def test_missing_host_yosys_is_blocking_not_measured(tmp_path, monkeypatch, capsys):
    leaf, top = tmp_path / "leaf.v", tmp_path / "top.v"
    leaf.write_text(LEAF)
    top.write_text(TOP)
    report = tmp_path / "rtl_hygiene.json"

    monkeypatch.delenv("VIBEIC_EDA_IMAGE", raising=False)
    monkeypatch.delenv("IIC_EDA_IMAGE", raising=False)
    monkeypatch.setattr(
        H._pr, "run",
        lambda *a, **k: (_ for _ in ()).throw(
            FileNotFoundError("COMMAND_NOT_FOUND: 'yosys'")))
    monkeypatch.setattr(sys, "argv", [
        str(PROGRAMS / "rtl_hygiene_lint.py"), str(leaf), str(top),
        "--top", "top", "--severity", "ERROR", "--json", str(report)])

    rc = H.main()
    stdout = capsys.readouterr().out
    findings = json.loads(report.read_text())
    context = json.loads(next(tmp_path.glob(
        "native-elaboration-*/context.json")).read_text())

    assert rc == 2
    assert "NOT_MEASURED [EXECUTION_ERROR]" in stdout
    assert "NATIVE_TOOL_ABSENT: Yosys" in stdout
    assert context["status"] == "NOT_MEASURED"
    assert context["reason_class"] == "tool_absent"
    assert context["capability"] == "yosys"
    assert {f["rule"] for f in findings} <= {
        "native-elaboration-unresolved",
        "instance-driver-analysis-unresolved",
    }


def test_named_missing_native_tool_classifies_as_capability_not_design_fact():
    step = {
        "id": 2,
        "name": "RTL validation",
        "stage": "stage1",
        "status": "NOT_MEASURED",
        "reason_class": "execution_error",
        "reasons": [
            "GATE_RAN rtl_hygiene_lint rc=2 INCOMPLETE",
            "NOT_MEASURED [EXECUTION_ERROR]: NATIVE_TOOL_ABSENT: Yosys",
        ],
        "evidence": [],
        "gate_output": "",
        "cascade_note": "",
        "self_skip_disclosed": False,
        "structure_only_disclosed": False,
        "gate_records": None,
    }

    blocker = BC.build_blockers([step])[0]
    assert blocker["classification"] == "MISSING_CAPABILITY"
    assert blocker["basis"] == "native-tool-absent"


def test_flow_consumer_keeps_missing_yosys_incomplete(tmp_path, monkeypatch):
    rtl = tmp_path / "phase2/stage1/rtl"
    report = tmp_path / "reports/phase2/lint/rtl_hygiene.json"
    rtl.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    (rtl / "leaf.v").write_text(LEAF)
    (rtl / "top.v").write_text(TOP)
    monkeypatch.delenv("VIBEIC_EDA_IMAGE", raising=False)
    monkeypatch.delenv("IIC_EDA_IMAGE", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")

    result = FCC._check_program_exit_zero(
        tmp_path,
        "rtl_hygiene_lint phase2/stage1/rtl/leaf.v "
        "phase2/stage1/rtl/top.v --top top --severity ERROR "
        "--json reports/phase2/lint/rtl_hygiene.json")

    assert result.exit_code == 2
    assert result.verdict == "INCOMPLETE"
    assert result.reason_class == "EXECUTION_ERROR"
    assert "NATIVE_TOOL_ABSENT: Yosys" in result[1]


def test_real_undriven_wire_still_returns_design_fail(tmp_path, monkeypatch):
    top = tmp_path / "top.v"
    top.write_text("module top(output result); wire missing; assign result=missing; endmodule\n")
    report = tmp_path / "rtl_hygiene.json"
    monkeypatch.setattr(
        H, "_native_driver_view",
        lambda *a, **k: (_ for _ in ()).throw(
            H.NativeElaborationCapabilityAbsent(
                "NATIVE_TOOL_ABSENT: Yosys test control")))
    monkeypatch.setattr(sys, "argv", [
        str(PROGRAMS / "rtl_hygiene_lint.py"), str(top),
        "--top", "top", "--severity", "ERROR", "--json", str(report)])

    assert H.main() == 1
    findings = json.loads(report.read_text())
    assert any(f["rule"] == "undriven-wire" for f in findings)
