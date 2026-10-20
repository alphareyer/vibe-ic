"""Unit tests for `phase2_verify_aggregate.py`."""
import importlib
import json
import subprocess
import sys
from pathlib import Path
import pytest

from _shipped_version import shipped_plugin_version  # noqa: E402  (#800)
mod = importlib.import_module("phase2_verify_aggregate")


class TestArtifactScan:
    def test_missing_returns_false(self, tmp_path):
        out = mod.scan_artifacts(tmp_path)
        assert all(not a.present for a in out)

    def test_present_returns_true(self, tmp_path):
        d = tmp_path / "phase2" / "stage1" / "rtl"
        d.mkdir(parents=True)
        (d / "core.v").write_text("module core; endmodule\n")
        out = mod.scan_artifacts(tmp_path)
        rtl = [a for a in out if a.name == "rtl"][0]
        assert rtl.present


class TestAggregator:
    def _c(self, name, ec):
        return mod.CheckResult(name=name, exit_code=ec, stdout_tail="")

    def _a(self, name, present):
        return mod.ArtifactPresence(name=name, path=f"/x/{name}",
                                      present=present)

    def test_clean_run(self):
        artifacts = [self._a("rtl", True), self._a("sof", True), self._a("tb", True)]
        rep = mod.aggregate(mod.Path("/x"), artifacts, [self._c("a", 0)])
        assert rep.verdict == "PASS"

    def test_missing_artifact_fails(self):
        artifacts = [self._a("rtl", False), self._a("sof", True), self._a("tb", True)]
        rep = mod.aggregate(mod.Path("/x"), artifacts, [self._c("a", 0)])
        assert rep.verdict == "FAIL"

    def test_failed_check_fails(self):
        artifacts = [self._a("rtl", True), self._a("sof", True), self._a("tb", True)]
        rep = mod.aggregate(mod.Path("/x"), artifacts, [self._c("a", 1)])
        assert rep.verdict == "FAIL"

    def test_attribution(self):
        rep = mod.aggregate(mod.Path("/x"), [], [])
        d = rep.as_dict()
        assert d["emitted_by"] == \
            f"phase2_verify_aggregate v{shipped_plugin_version()}"


def _native_project(tmp_path):
    from programs.tests._route_fixture import stage_owner_route
    project = tmp_path / "project"
    stage_owner_route(project, "ic")
    for rel in ("phase2/stage1/rtl", "phase2/stage2/synth",
                "phase2/stage1/sim_full_stack", "phase1/generated_docs"):
        (project / rel).mkdir(parents=True, exist_ok=True)
    rtl = "module core(input wire clk, output wire gpio); assign gpio=clk; endmodule\n"
    (project / "phase2/stage1/rtl/core.v").write_text(rtl)
    (project / "phase2/stage2/synth/core_synth.v").write_text(rtl)
    (project / "phase2/stage1/sim_full_stack/tb_core.v").write_text(
        "module tb_core; reg clk; wire gpio; core dut(clk,gpio); endmodule\n")
    (project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": "core", "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "gpio", "direction": "output", "width": 1}]}))
    return project


def _native_aggregate(project, *extra):
    output = project / "aggregate.json"
    result = subprocess.run([sys.executable, mod.__file__, str(project),
                             "--out-json", str(output), "--strict", *extra],
                            capture_output=True, text=True, timeout=180)
    assert output.is_file(), result.stdout + result.stderr
    return result, json.loads(output.read_text())


def test_public_ic_aggregate_uses_real_child_clis_and_current_artifacts(tmp_path):
    result, doc = _native_aggregate(_native_project(tmp_path))
    assert result.returncode == 0 and doc["verdict"] == "PASS", doc
    assert doc["verification_target"] == "ic"
    assert all(row["exit_code"] == 0 for row in doc["checks"]), doc
    for row in doc["checks"]:
        assert row["argv"] and "stderr_tail" in row
    assert not any("sof" in row["path"] for row in doc["artifacts"])


@pytest.mark.parametrize("kind", ["netlist", "tb", "empty_rtl", "spec_mismatch", "lint"])
def test_native_aggregate_preserves_real_missing_and_checker_failures(tmp_path, kind):
    project = _native_project(tmp_path)
    if kind == "netlist":
        (project / "phase2/stage2/synth/core_synth.v").unlink()
    elif kind == "tb":
        (project / "phase2/stage1/sim_full_stack/tb_core.v").unlink()
    elif kind == "empty_rtl":
        (project / "phase2/stage1/rtl/core.v").unlink()
    elif kind == "spec_mismatch":
        path = project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
        doc = json.loads(path.read_text())
        doc["ports"].append({"name": "required", "direction": "input", "width": 1})
        path.write_text(json.dumps(doc))
    else:
        path = project / "phase2/stage1/rtl/core.v"
        path.write_text('module core(input wire clk, output wire gpio);\n'
                        'wire undriven;\nassign gpio=undriven;\nendmodule\n')
    result, doc = _native_aggregate(project)
    assert result.returncode == 1 and doc["verdict"] == "FAIL", doc
    if kind in ("spec_mismatch", "lint"):
        check = "spec_conformance" if kind == "spec_mismatch" else "rtl_hygiene_lint"
        row = next(row for row in doc["checks"] if row["name"] == check)
        assert row["exit_code"] == 1 and row["reason_class"] == "CHECK_FAILED", row
    else:
        assert any(not row["present"] for row in doc["artifacts"]), doc


def test_fpga_artifact_is_required_only_for_explicit_fpga_review(tmp_path):
    project = _native_project(tmp_path)
    result, doc = _native_aggregate(project, "--target", "fpga")
    assert result.returncode == 1 and any(not a["present"] and a["name"] == "sof"
                                        for a in doc["artifacts"]), doc
    bitstream = project / "phase2/stage1/fpga/output_files/core.sof"
    bitstream.parent.mkdir(parents=True)
    bitstream.write_bytes(b"fixture-bitstream-artifact")
    result, doc = _native_aggregate(project, "--target", "fpga")
    assert result.returncode == 0, doc
    assert doc["verification_target"] == "fpga"


def test_child_usage_failure_retains_actual_argv_and_stderr(tmp_path, monkeypatch):
    project = _native_project(tmp_path)
    observed = []
    def usage(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 2, "", "usage: required --spec")
    monkeypatch.setattr(mod.subprocess, "run", usage)
    row = mod._run_check("spec_conformance", "spec_conformance_check.py", [], project)
    assert row.exit_code == 2 and row.argv == observed[0]
    assert "required --spec" in row.stderr_tail
    assert row.reason_class == "INVOCATION_ERROR"


def _package_project(tmp_path, case="clean"):
    """Source-only neutral input; never fabricate aggregate netlist/TB proof."""
    project = tmp_path / "package_project"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "00_byte_ops.sv").write_text(
        "`define BYTE_PROVIDER\npackage byte_ops;\n"
        "function automatic logic [7:0] fold(input logic [7:0] value);\n"
        "fold=value ^ 8'h5a;\nendfunction\nendpackage\n")
    leaf = "module byte_leaf(input logic [7:0] data_i, output logic [7:0] data_o);\n"
    leaf += ("assign data_o=;\n" if case == "bad_provider" else
             "assign data_o=byte_ops::fold(data_i);\n")
    if case == "driver_conflict":
        leaf += "always_comb data_o=~data_i;\n"
    if case != "missing_provider":
        (rtl / "byte_leaf.sv").write_text(leaf + "endmodule\n")
    ports = [{"name": "data_i", "direction": "input", "width": 8}]
    if case == "warnings":
        ports += [{"name": "clk", "direction": "input", "width": 1}]
        ports += [{"name": f"q{i}", "direction": "output", "width": 8}
                  for i in range(13)]
        body = "module neutral_top(input wire clk, input wire [7:0] data_i,\n"
        body += ",\n".join(f"output reg [7:0] q{i}" for i in range(13)) + ");\n"
        body += "always @(posedge clk) begin\n"
        body += "\n".join(f"q{i} <= byte_ops::fold(data_i);" for i in range(13))
        body += "\nend\nendmodule\n"
    else:
        ports += [{"name": "data_o", "direction": "output", "width": 8}]
        body = ("module neutral_top(input logic [7:0] data_i, output logic [7:0] data_o);\n"
                "`ifdef BYTE_PROVIDER\nbyte_leaf u(.data_i(data_i),.data_o(data_o));\n"
                "`else\nmissing_define_provider u();\n`endif\n"
                "`ifdef SYNTHESIS\nimplicit_synthesis_provider forbidden();\n`endif\n"
                "endmodule\n")
    (rtl / "neutral_top.sv").write_text(body)
    spec = project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    spec.parent.mkdir(parents=True)
    spec.write_text(json.dumps({"top_module": "neutral_top", "ports": ports}))
    return project


@pytest.mark.parametrize("case", ["clean", "warnings"])
def test_aggregate_hygiene_preserves_project_native_context_and_default_policy(tmp_path, case):
    project = _package_project(tmp_path, case)
    report = mod.verify(project, target="ic")
    row = next(c for c in report.checks if c.name == "rtl_hygiene_lint")
    assert row.exit_code == (0 if case == "clean" else 1), row.as_dict()
    assert row.argv[2:4] == ["--project", str(project)]
    assert "--severity" not in row.argv
    findings_path = Path(row.argv[row.argv.index("--json") + 1])
    findings = json.loads(findings_path.read_text())
    contexts = list(findings_path.parent.glob("native-elaboration-*/context.json"))
    context, = [json.loads(p.read_text()) for p in contexts]
    assert context["status"] == "MEASURED", context
    assert context["native_returncode"] == context["transport_returncode"] == 0
    assert context["top"] == "neutral_top"
    assert {Path(p).name for p in context["files"]} == {
        "00_byte_ops.sv", "byte_leaf.sv", "neutral_top.sv"}
    assert all(p in context["input_sha256"] for p in context["files"])
    script = contexts[0].with_name("elaborate.ys").read_text()
    assert "read_slang --single-unit --no-synthesis-define --top neutral_top" in script
    if case == "clean":
        assert findings == []
    else:
        warnings = [f for f in findings if f["severity"] == "WARN"]
        assert len(warnings) == 13, findings
        assert {f["rule"] for f in warnings} == {"uninit-registered-output"}
        assert row.reason_class == "CHECK_FAILED"
        assert "[WARN]" in row.stdout_tail
    # This source-only review has no synthesized netlist or reference TB.
    assert report.verdict == "FAIL"
    assert {a.name for a in report.artifacts if not a.present} == {"netlist", "tb"}


@pytest.mark.parametrize("case", ["driver_conflict", "bad_provider", "missing_provider"])
def test_aggregate_hygiene_rejects_true_selected_native_failures(tmp_path, case):
    project = _package_project(tmp_path, case)
    row = mod._run_check("rtl_hygiene_lint", "rtl_hygiene_lint.py", [], project)
    assert row.exit_code == 1 and row.reason_class == "CHECK_FAILED", row.as_dict()
    assert "hierarchy-port-resolution" not in row.stderr_tail
    findings = json.loads(Path(row.argv[row.argv.index("--json") + 1]).read_text())
    assert any(f["severity"] == "ERROR" and f["rule"] == "native-elaboration-unresolved"
               for f in findings), findings
    native, = Path(row.argv[row.argv.index("--json") + 1]).parent.glob(
        "native-elaboration-*/context.json")
    context = json.loads(native.read_text())
    assert context["status"] == "NOT_MEASURED"
    assert context["native_returncode"] != 0
