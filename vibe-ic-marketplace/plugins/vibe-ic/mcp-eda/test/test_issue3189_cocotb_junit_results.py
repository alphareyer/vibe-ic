"""Actual eda_cocotb callback, shell XML parser and manifest decision; no EDA.

XML fixtures follow cocotb 2.0's upstream XUnitReporter structure, including
testcase populations without aggregate suite counters:
https://github.com/cocotb/cocotb/blob/v2.0.0/src/cocotb/_xunit_reporter.py
They are explicit structural fixtures, not a claimed native simulator capture.
Only the native `make` executable is replaced. The product's staging shell,
JUnit reader and registered MCP consumer execute unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


MCP_ROOT = Path(os.environ.get("VIBEIC_COCOTB_REVIEW_MCP_ROOT",
                               str(Path(__file__).resolve().parents[1]))).resolve()
NODE = shutil.which("node")


def _xml(outcomes):
    root = ET.Element("testsuites", name="results")
    suite = ET.SubElement(root, "testsuite", name="all", package="all")
    for index, outcome in enumerate(outcomes):
        case = ET.SubElement(suite, "testcase", name=f"test_{index}",
                             classname="tb_scoreboard", file="tb_scoreboard.py",
                             lineno=str(20 + index), time="0.001",
                             sim_time_ns="100.0", ratio_time="100000.0")
        if outcome != "passed":
            ET.SubElement(case, outcome,
                          message="independent instruction-scoreboard mismatch"
                          if outcome == "failure" else outcome)
    return ET.tostring(root, encoding="unicode")


def _invoke(tmp_path, *, outcomes, native_rc=0, mode="current", stdout=None,
            work_dir=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = tmp_path / "native-fixture.json"
    if stdout is None:
        failed = sum(value in ("failure", "error") for value in outcomes)
        skipped = outcomes.count("skipped")
        stdout = (f"TESTS={len(outcomes)} PASS={len(outcomes)-failed-skipped} "
                  f"FAIL={failed} SKIP={skipped}\n")
    xml = "<testsuites><testsuite><testcase></testsuites>" if mode == "malformed" else _xml(outcomes)
    fixture.write_text(json.dumps({"xml": xml, "mode": mode,
                                  "native_rc": native_rc, "stdout": stdout}))
    fake_bin = tmp_path / "fake-native-bin"
    fake_bin.mkdir()
    fake_make = fake_bin / "make"
    fake_make.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys
from pathlib import Path
fixture = json.loads(Path(os.environ['COCOTB_NATIVE_FIXTURE']).read_text())
target = next((arg.split('=', 1)[1] for arg in sys.argv[1:]
               if arg.startswith('COCOTB_RESULTS_FILE=')),
              os.environ.get('COCOTB_RESULTS_FILE', 'results.xml'))
if fixture['mode'] in ('current', 'malformed'):
    Path(target).write_text(fixture['xml'])
elif fixture['mode'] == 'stale':
    # Leave the pre-existing legacy result untouched; no fresh run result.
    assert Path('results.xml').exists()
elif fixture['mode'] != 'missing':
    raise AssertionError(fixture['mode'])
print(fixture['stdout'], end='')
sys.exit(fixture['native_rc'])
''')
    fake_make.chmod(0o755)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "top.v").write_text("module top; endmodule\n")
    (inputs / "tb_scoreboard.py").write_text("# Native simulator boundary fixture only.\n")
    work = work_dir if work_dir is not None else tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    stale = work / "results.xml"
    stale_bytes = _xml(["passed"] * 3).encode()
    if mode == "stale":
        stale.write_bytes(stale_bytes)
        os.utime(stale, (1, 1))

    src = (MCP_ROOT / "src/index.js").read_text()
    marker = src.index('"eda_cocotb"')
    start = src.rfind("server.tool(", 0, marker)
    end = src.find("server.tool(", marker)
    block = src[start:end]
    script = r'''
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {pathToFileURL} from 'node:url';
let cocotbJunitScript, cocotbJunitResult;
// Baseline's original callback does not call or import the new helper. Its
// real count failure must remain measurable, not become a missing-module error.
if (fs.existsSync(HELPER_PATH)) {
  ({cocotbJunitScript, cocotbJunitResult} = await import(pathToFileURL(HELPER_PATH)));
}
const manifests = [], calls = [];
let handler;
const chain = new Proxy(() => chain, {get: () => chain});
const z = chain, server = {tool: (...args) => {handler = args.at(-1);}};
const TOOLS = '/__no_native_eda_tools__';
const assertSafePaths = () => {}, assertSafePath = () => {};
const assertSafeIdent = () => {}, optPath = () => {};
const guardError = error => ({isError: true, error: String(error)});
const _edaThreadsToken = () => '1';
function writeManifest(directory, value) { manifests.push({directory, value}); }
function dockerExec(command) {
  calls.push(command);
  const result = spawnSync('/bin/bash', ['-c', command], {
    encoding: 'utf8', timeout: 15000,
    env: {...process.env, PATH: FAKE_BIN + ':' + process.env.PATH,
          COCOTB_NATIVE_FIXTURE: FIXTURE_PATH},
  });
  if (result.error) throw result.error;
  return {success: result.status === 0, output: result.stdout + result.stderr,
          exitCode: result.status};
}
TOOL_BLOCK
const response = await handler(PARAMS);
console.log(JSON.stringify({response, manifests, calls}));
'''.replace("HELPER_PATH", json.dumps(str(MCP_ROOT / "src/lib/cocotb_results.mjs")))
    script = script.replace("FAKE_BIN", json.dumps(str(fake_bin)))
    script = script.replace("FIXTURE_PATH", json.dumps(str(fixture)))
    script = script.replace("TOOL_BLOCK", block)
    script = script.replace("PARAMS", json.dumps({
        "verilog_files": [str(inputs / "top.v")], "top_module": "top",
        "testbench_py": str(inputs / "tb_scoreboard.py"),
        "simulator": "icarus", "work_dir": str(work)}))
    (tmp_path / "actual_handler.mjs").write_text(script)
    result = subprocess.run([NODE, str(tmp_path / "actual_handler.mjs")],
                            capture_output=True, text=True, timeout=30)
    (tmp_path / "node.stdout").write_text(result.stdout)
    (tmp_path / "node.stderr").write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    raw = json.loads(result.stdout)
    (tmp_path / "actual_result.json").write_text(json.dumps(raw, indent=2))
    assert len(raw["calls"]) == 1, "One invocation must read its own XML in the same shell"
    if mode == "stale":
        assert stale.read_bytes() == stale_bytes, "Historical native evidence must be retained"
    return json.loads(raw["response"]["content"][0]["text"]), raw["manifests"]


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("case,outcomes,native_rc,mode,expected_success", [
    ("all_pass", ["passed"] * 3, 0, "current", True),
    ("mixed_failure", ["passed", "failure", "passed"], 7, "current", False),
    ("mixed_error", ["passed", "error", "passed"], 1, "current", False),
    ("mixed_skipped", ["passed", "skipped", "passed"], 0, "current", True),
    ("all_skipped", ["skipped"] * 3, 0, "current", False),
    ("zero", [], 0, "current", False),
    ("stale_only_legacy", [], 0, "stale", False),
    ("missing_xml", [], 0, "missing", False),
    ("malformed_xml", [], 0, "malformed", False),
], ids=["all_pass", "mixed_failure", "mixed_error", "mixed_skipped",
        "all_skipped", "zero", "stale_only_legacy", "missing_xml", "malformed_xml"])
def test_native_junit_controls_actual_mcp_counts_and_manifest(
        tmp_path, case, outcomes, native_rc, mode, expected_success):
    # Contradictory human summaries must never override actual failing XML or
    # invent a measurement when there is no current XML. Preserve BFM failure.
    stdout = None
    if case in ("mixed_failure", "mixed_error"):
        stdout = ("INDEPENDENT_BFM: instruction-scoreboard mismatch\n"
                  "TESTS=3 PASS=3 FAIL=0 SKIP=0\n99 passed\n")
    elif case in ("all_skipped", "zero", "stale_only_legacy", "missing_xml", "malformed_xml"):
        stdout = "TESTS=3 PASS=3 FAIL=0 SKIP=0\n3 passed\n"
    shared_work = None
    if mode == "stale":
        shared_work = tmp_path / "same-work"
        previous, old_manifests = _invoke(tmp_path / "good", outcomes=["passed"] * 3,
                                          work_dir=shared_work)
        assert previous["success"] is True, previous
        assert old_manifests[-1]["value"]["status"] == "PASS"
        old_xml = {path.name: path.read_bytes() for path in shared_work.glob("results*.xml")}
        assert old_xml
    result, manifests = _invoke(tmp_path / "current", outcomes=outcomes, native_rc=native_rc,
                                mode=mode, stdout=stdout, work_dir=shared_work)
    if mode == "current":
        failures = sum(value in ("failure", "error") for value in outcomes)
        skipped = outcomes.count("skipped")
        expected = {"tests_total": len(outcomes),
                    "tests_passed": len(outcomes) - failures - skipped,
                    "tests_failed": failures, "tests_errors": outcomes.count("error"),
                    "tests_skipped": skipped}
        for key, value in expected.items():
            assert result.get(key) == value, (key, result)
    else:
        assert result.get("junit_valid") is False, result
        assert result.get("error"), result
        assert result.get("tests_total") is None, result
        assert result.get("tests_passed") is None, result
        assert result.get("tests_failed") is None, result
    assert result["success"] is expected_success, result
    assert result["native_exit_code"] == native_rc, result
    assert result["simulator"] == "icarus"
    if case in ("mixed_failure", "mixed_error"):
        assert "INDEPENDENT_BFM: instruction-scoreboard mismatch" in result["output"]
    if expected_success:
        assert len(manifests) == 1, manifests
        assert manifests[0]["value"]["status"] == "PASS"
        for key, value in expected.items():
            assert manifests[0]["value"].get(key) == value, manifests
    else:
        assert manifests, "The current failed/incomplete run must supersede a prior PASS"
        assert manifests[-1]["value"]["status"] in ("FAIL", "INCONCLUSIVE"), manifests
    assert manifests[-1]["value"]["results_xml"] == result["results_xml"], (result, manifests)
    assert manifests[-1]["value"]["native_exit_code"] == native_rc, manifests
    if mode == "stale":
        assert result["results_xml"] != previous["results_xml"], (previous, result)
        for name, content in old_xml.items():
            assert (shared_work / name).read_bytes() == content
