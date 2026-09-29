"""Exercise PDK lookup through the unmodified MCP server and a fake Docker CLI.

Only the Docker subprocess is substituted. No server functions are extracted,
patched or reimplemented. The fake backend captures argv without running EDA;
its output deliberately contains no PPA or equivalence measurement. Each test
leaves the full MCP exchange and subprocess trace in runtime_evidence.json.
"""
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MCP_ROOT = Path(__file__).resolve().parents[1]
INDEX = MCP_ROOT / "src" / "index.js"
LIB = ("/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/"
       "gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib")
DIAG = ("Error response from daemon: Container pdk_runtime_fixture is paused, "
        "unpause the container before exec")
BACKEND = "FAKE_DOCKER_BACKEND_ARGV_CAPTURE_ONLY"

FAKE_DOCKER = r'''
import json, os, sys
from pathlib import Path
state_path = Path(os.environ["PDK_RUNTIME_STATE"])
state = json.loads(state_path.read_text())
argv = sys.argv[1:]
status, stdout, stderr, kind = 0, "", "", "unexpected"
if argv[0] == "ps":
    kind, stdout = "probe", "pdk_runtime_fixture\n"
elif argv[0] == "inspect":
    kind, stdout = "image_id", "sha256:" + "0" * 64 + "\n"
elif argv[:2] == ["image", "inspect"]:
    kind, stdout = "image_digest", "[]\n"
elif argv[0] == "exec":
    cmd = argv[-1]
    if cmd == "command -v timeout":
        kind, stdout = "timeout_probe", "/usr/bin/timeout\n"
    elif cmd == "command -v quartus_pgm":
        kind, status = "device_probe", 1
    elif cmd.startswith("for f in "):
        kind = "input_visibility"
    elif cmd.startswith("ls -1d "):
        kind = "asset_lookup"
        n = state["lookup_count"]
        state["lookup_count"] += 1
        state_path.write_text(json.dumps(state))
        if n < len(state["responses"]):
            response = state["responses"][n]
            status = response["status"]
            stdout, stderr = response["stdout"], response["stderr"]
        else:
            status, stderr = 98, "unexpected extra asset lookup\n"
    elif cmd.startswith("cat ") and cmd.endswith(" 2>/dev/null"):
        kind, stdout = "liberty_read", "library (fixture) { nom_voltage : 5; }\n"
    elif cmd.startswith("cat > /tmp/lvs_equiv_") and "__YS_EOF__" in cmd:
        kind = "backend_script"
    elif "yosys -p " in cmd or "yosys -s " in cmd:
        kind, stdout = "backend", "FAKE_DOCKER_BACKEND_ARGV_CAPTURE_ONLY\n"
    elif "yosys -V " in cmd:
        kind, stdout = "version", "fake-docker-argv-fixture\n"
if kind == "unexpected":
    status, stderr = 99, "unexpected fake Docker argv: " + repr(argv) + "\n"
with Path(os.environ["PDK_RUNTIME_TRACE"]).open("a") as stream:
    stream.write(json.dumps({"kind": kind, "argv": argv, "status": status,
                             "stdout": stdout, "stderr": stderr}) + "\n")
sys.stdout.write(stdout)
sys.stderr.write(stderr)
sys.exit(status)
'''


def _response(stdout="", stderr="", status=0):
    return {"stdout": stdout, "stderr": stderr, "status": status}


class Runtime:
    def __init__(self, directory, responses):
        self.directory = directory
        self.state = directory / "docker_state.json"
        self.trace = directory / "docker_trace.jsonl"
        self.state.write_text(json.dumps({"responses": responses, "lookup_count": 0}))
        bin_dir = directory / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER)
        docker.chmod(0o755)
        node = shutil.which("node")
        assert node, "INCONCLUSIVE_HARNESS: node is required"
        assert (MCP_ROOT / "node_modules" / "@modelcontextprotocol" / "sdk").is_dir(), (
            "INCONCLUSIVE_HARNESS: run npm ci in mcp-eda first")
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                   EDA_CONTAINER="pdk_runtime_fixture", EDA_PROJECT_DIR=str(directory),
                   MCP_EDA_GROUP_FIXED="1", PDK_RUNTIME_STATE=str(self.state),
                   PDK_RUNTIME_TRACE=str(self.trace))
        self.stderr = (directory / "server_stderr.txt").open("w")
        self.proc = subprocess.Popen([node, str(INDEX)], cwd=MCP_ROOT, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.stderr, text=True, bufsize=1)
        self.messages = []
        self.next_id = 1

    def __enter__(self):
        try:
            reply = self.request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "pdk-runtime-fixture", "version": "1"}})
            assert reply["result"]["serverInfo"]["name"] == "mcp-eda"
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self.proc.stdout.close()
        self.stderr.close()
        evidence = {"server_pid": self.proc.pid,
                    "server_source_sha256": hashlib.sha256(INDEX.read_bytes()).hexdigest(),
                    "messages": self.messages, "docker_calls": self.calls(),
                    "docker_state": json.loads(self.state.read_text()),
                    "server_stderr": (self.directory / "server_stderr.txt").read_text(),
                    "scope": "fake Docker subprocess; no real EDA or measurements"}
        (self.directory / "runtime_evidence.json").write_text(json.dumps(evidence, indent=2))

    def send(self, message):
        self.messages.append({"direction": "request", "message": message})
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params})
        while True:
            ready, _, _ = select.select([self.proc.stdout], [], [], 20)
            assert ready, "INCONCLUSIVE_HARNESS: fake-subprocess MCP response stalled"
            line = self.proc.stdout.readline()
            assert line, "INCONCLUSIVE_HARNESS: server exited: " + (
                self.directory / "server_stderr.txt").read_text()
            reply = json.loads(line)
            self.messages.append({"direction": "response", "message": reply})
            if reply.get("id") == request_id:
                assert "error" not in reply, reply
                return reply["result"] if method == "tools/call" else reply

    def call(self, tool):
        args = {"pdk": "gf180", "top_module": "fixture"}
        if tool == "eda_synth":
            args.update(verilog_files=["/fixture/input.v"], output_netlist="/fixture/output.v")
        else:
            args.update(mode="yosys_equiv", schematic_netlist="/fixture/input.v",
                        layout_netlist="/fixture/output.v")
        return self.request("tools/call", {"name": tool, "arguments": args})

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def count(self):
        return json.loads(self.state.read_text())["lookup_count"]


def _text(result):
    return "\n".join(item.get("text", "") for item in result.get("content", []))


def _assert_backend_received_valid_lib(result, calls, tool):
    assert not result.get("isError", False), result
    assert BACKEND in _text(result), result
    backend = [call["argv"][-1] for call in calls if call["kind"] == "backend"]
    assert len(backend) == 1, calls
    scripts = [call["argv"][-1] for call in calls if call["kind"] == "backend_script"]
    consumed = backend if tool == "eda_synth" else scripts
    assert len(consumed) == 1, calls
    expected = f"dfflibmap -liberty {LIB}" if tool == "eda_synth" else f"read_liberty -lib {LIB}"
    assert expected in consumed[0], consumed
    assert DIAG not in consumed[0], consumed
    assert all(call["kind"] != "unexpected" for call in calls), calls


@pytest.mark.parametrize("tool", ["eda_synth", "eda_lvs"])
@pytest.mark.parametrize("shape", ["unique", "zero", "multiple"])
def test_successful_lookup_requires_one_path_and_only_caches_it(tmp_path, tool, shape):
    first = {"unique": LIB + "\n", "zero": "",
             "multiple": LIB + "\n" + LIB.replace("gf180mcuD", "gf180mcuC") + "\n"}[shape]
    responses = [_response(first)]
    if shape != "unique":
        responses.append(_response(LIB + "\n"))
    with Runtime(tmp_path, responses) as runtime:
        first_result = runtime.call(tool)
        first_calls = runtime.calls()
        offset = len(first_calls)
        recovered = runtime.call(tool)
        second_calls = runtime.calls()[offset:]
        offset += len(second_calls)
        cached = runtime.call(tool)
        third_calls = runtime.calls()[offset:]
        if shape == "unique":
            _assert_backend_received_valid_lib(first_result, first_calls, tool)
        else:
            assert first_result.get("isError") is True, first_result
            assert "PDK_ASSET_GLOB_NOT_UNIQUE" in _text(first_result)
            assert f"matched {0 if shape == 'zero' else 2} file(s)" in _text(first_result)
            assert not any(call["kind"] in ("backend", "backend_script", "liberty_read")
                           for call in first_calls)
        _assert_backend_received_valid_lib(recovered, second_calls, tool)
        _assert_backend_received_valid_lib(cached, third_calls, tool)
        assert runtime.count() == (1 if shape == "unique" else 2)


@pytest.mark.parametrize("tool", ["eda_synth", "eda_lvs"])
@pytest.mark.parametrize("failure", ["one_line_stderr", "valid_stdout_with_nonzero_exit"])
def test_failed_lookup_preserves_diagnosis_and_recovers_in_same_server(tmp_path, tool, failure):
    stdout = "" if failure == "one_line_stderr" else LIB + "\n"
    with Runtime(tmp_path, [_response(stdout, DIAG + "\n", 1),
                            _response(LIB + "\n")]) as runtime:
        failed = runtime.call(tool)
        first_calls = runtime.calls()
        offset = len(first_calls)
        recovered = runtime.call(tool)
        second_calls = runtime.calls()[offset:]
        offset += len(second_calls)
        cached = runtime.call(tool)
        third_calls = runtime.calls()[offset:]
        # Execute all requests before asserting, so even the red arm retains
        # the poisoned-cache signature and its same-process recovery attempt.
        assert failed.get("isError") is True, failed
        assert DIAG in _text(failed), failed
        assert "PDK_ASSET_GLOB_NOT_UNIQUE" not in _text(failed), failed
        assert not any(call["kind"] in ("backend", "backend_script", "liberty_read")
                       for call in first_calls)
        _assert_backend_received_valid_lib(recovered, second_calls, tool)
        _assert_backend_received_valid_lib(cached, third_calls, tool)
        assert runtime.count() == 2, runtime.calls()
