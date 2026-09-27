#!/usr/bin/env python3
"""FX_P2 review fix (5, 6a, 6b) — the P0 declaration-order relaxation is
per FILE, every tool run has a deadline, and the flow's own record carries the
disclosure.

At 065ef1c45 `p0_tool_frontend_check` retried slang with
`--allow-use-before-declare` when the PROJECT was reused IP and every error was
use-before-declare. Three things the review found:

(6b) The condition was project-wide. A reused-IP project still carries RTL the
     plugin authored (the wrapper, chip_top), which repair can edit; its
     use-before-declare was relaxed too and never reached repair. Now only a
     diagnostic in a file that IS the supplied IP, byte-identical to what the
     input delivered (SOURCE_MANIFEST `staged_from_input`, or the sha256 an
     `ip_catalog_pull` recorded), may be relaxed.
(6a) `_invoke` ran `subprocess.run` with no timeout, for the strict call and
     the new relaxed one. Now both are bounded; on the docker path the named
     container is killed; a timeout is an execution error, never retried.
(5)  The flow's only caller, `flow_step_output_content_check --mode rtl`, kept
     only findings/sources/exit codes, so the relaxation was silent in the run:
     the Yosys row showed the RELAXED exit 0 and the strict refusal was gone.

Only the EDA tools' answers are faked (`_invoke` / `subprocess.run`), replaying
the REAL read_slang transcripts in `calibration/`. chip-AGNOSTIC: synthetic RTL.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import p0_tool_frontend_check as F            # noqa: E402
import flow_step_output_content_check as C    # noqa: E402

FLAG = "--allow-use-before-declare"
LATE = (PROGRAMS / "calibration"
        / "p0_slang_use_before_declare_positive.log").read_text()
IP_SRC = ("module ip_core(input a, output y);\n  assign y = late;\n"
          "  wire late = a;\nendmodule\n")
WRAP_SRC = ("module chip_top(input a, output y);\n  assign y = w;\n"
            "  wire w;\n  ip_core u(.a(a), .y(w));\nendmodule\n")


def _rtl(root: Path) -> Path:
    return root / "phase2" / "stage1" / "rtl"


def _project(tmp_path: Path, *, staged=True, edited=False,
             catalog=False) -> Path:
    """A reused-IP project: ip_core.v is the supplied IP, chip_top.v is the
    plugin's own wrapper."""
    rtl = _rtl(tmp_path)
    rtl.mkdir(parents=True)
    (rtl / "ip_core.v").write_text(IP_SRC + ("// edited\n" if edited else ""))
    (rtl / "chip_top.v").write_text(WRAP_SRC)
    mf = {"reused_ip": True}
    if staged:
        vendor = tmp_path / "input" / "vendor_rtl"
        vendor.mkdir(parents=True)
        (vendor / "ip_core.v").write_text(IP_SRC)
        mf["staged_from_input"] = ["input/vendor_rtl/ip_core.v"]
    if catalog:
        digest = hashlib.sha256(IP_SRC.encode()).hexdigest()
        (tmp_path / "provenance.jsonl").write_text(json.dumps({
            "event": "ip_catalog_pull", "ip": "ip_core",
            "outputs": {"phase2/stage1/rtl/ip_core.v": f"sha256:{digest}"},
        }) + "\n")
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(mf))
    return tmp_path


def _in(root: Path, leaf: str) -> str:
    """The real transcript, naming the absolute path slang was handed."""
    return LATE.replace("rtl/late.v", str((_rtl(root) / leaf).resolve()))


def _fake(monkeypatch, strict_log: str, *, relaxed_ok=True):
    calls = []

    def _invoke(tool, args, root, image):
        calls.append((tool, list(args)))
        if tool == "yosys":
            if FLAG in args[-1]:
                return subprocess.CompletedProcess(
                    [tool], 0 if relaxed_ok else 1, "", "")
            return subprocess.CompletedProcess([tool], 1, "", strict_log)
        return subprocess.CompletedProcess([tool], 0, "", "")

    monkeypatch.setattr(F, "_invoke", _invoke)
    return calls


def _yosys(calls):
    return [a for t, a in calls if t == "yosys"]


# ---- (6b) per file -------------------------------------------------------
def test_supplied_ip_staged_from_input_is_relaxed(monkeypatch, tmp_path):
    root = _project(tmp_path)
    calls = _fake(monkeypatch, _in(root, "ip_core.v"))
    r = F.check(root)
    assert r["passed"] is True, r["findings"]
    assert len(_yosys(calls)) == 2
    assert "ip_core.v" in r["disclosures"][0]


def test_supplied_ip_from_a_catalog_pull_is_relaxed(monkeypatch, tmp_path):
    root = _project(tmp_path, staged=False, catalog=True)
    _fake(monkeypatch, _in(root, "ip_core.v"))
    assert F.check(root)["passed"] is True


def test_the_plugins_own_wrapper_is_never_relaxed(monkeypatch, tmp_path):
    """Reused-IP project, but the diagnostic is in chip_top.v, which the plugin
    authored and repair can edit."""
    root = _project(tmp_path)
    calls = _fake(monkeypatch, _in(root, "chip_top.v"))
    r = F.check(root)
    assert r["passed"] is False
    assert "Yosys elaboration failed" in r["findings"]
    assert len(_yosys(calls)) == 1 and not r.get("disclosures")


def test_a_supplied_file_the_plugin_edited_is_not_relaxed(monkeypatch,
                                                          tmp_path):
    root = _project(tmp_path, edited=True)
    calls = _fake(monkeypatch, _in(root, "ip_core.v"))
    assert F.check(root)["passed"] is False
    assert len(_yosys(calls)) == 1


def test_a_project_flag_alone_attributes_no_file(monkeypatch, tmp_path):
    """reused_ip:true with no staged_from_input and no catalog provenance --
    nothing says which file is the IP, so nothing is relaxed."""
    root = _project(tmp_path, staged=False)
    calls = _fake(monkeypatch, _in(root, "ip_core.v"))
    assert F.check(root)["passed"] is False
    assert len(_yosys(calls)) == 1


def test_one_diagnostic_outside_the_ip_keeps_it_a_fail(monkeypatch, tmp_path):
    root = _project(tmp_path)
    log = _in(root, "ip_core.v")
    wrap = str((_rtl(root) / "chip_top.v").resolve())
    log = log.replace(
        "\nBuild failed: 1 error",
        f"\n{wrap}:2:14: error: identifier 'w' used before its declaration"
        "\nBuild failed: 2 errors")
    assert F.only_use_before_declare(log)
    calls = _fake(monkeypatch, log)
    assert F.check(root)["passed"] is False
    assert len(_yosys(calls)) == 1


# ---- (6a) deadlines -------------------------------------------------------
def test_every_invocation_carries_a_deadline_and_kills_its_container(
        monkeypatch, tmp_path):
    seen = []

    def _run(cmd, **kw):
        seen.append((list(cmd), kw.get("timeout")))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(F.shutil, "which",
                        lambda t: "/usr/bin/docker" if t == "docker" else None)
    monkeypatch.setattr(F.subprocess, "run", _run)
    monkeypatch.setenv(F.DEADLINE_ENV, "7")
    try:
        F._invoke("yosys", ["-p", "x"], tmp_path, "img@sha256:" + "0" * 64)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("a run past its deadline must not return")
    run, kill = seen
    assert run[1] == 7
    name = run[0][run[0].index("--name") + 1]
    assert kill[0] == ["docker", "kill", name] and kill[1]


def test_a_strict_timeout_is_an_execution_error_not_a_retry(monkeypatch,
                                                            tmp_path):
    root = _project(tmp_path)
    calls = []

    def _invoke(tool, args, project, image):
        calls.append(tool)
        raise subprocess.TimeoutExpired(["docker", "run", "--entrypoint",
                                         tool, "img"], 7)

    monkeypatch.setattr(F, "_invoke", _invoke)
    r = F.check(root)
    assert r["passed"] is False
    assert any("timed out" in f and "EXECUTION_ERROR" in f and "yosys" in f
               for f in r["findings"]), r["findings"]
    assert calls == ["yosys"]


def test_a_relaxed_retry_timeout_counts_for_nothing(monkeypatch, tmp_path):
    root = _project(tmp_path)
    strict = _in(root, "ip_core.v")

    def _invoke(tool, args, project, image):
        if tool == "yosys" and FLAG in args[-1]:
            raise subprocess.TimeoutExpired(["yosys"], 7)
        return subprocess.CompletedProcess([tool], 1 if tool == "yosys"
                                           else 0, "", strict)

    monkeypatch.setattr(F, "_invoke", _invoke)
    r = F.check(root)
    assert r["passed"] is False and not r.get("disclosures")


# ---- (5) the flow's record carries the relaxation -------------------------
def test_the_flow_caller_prints_the_disclosure_and_the_strict_exit(
        monkeypatch, tmp_path, capsys):
    root = _project(tmp_path)
    _fake(monkeypatch, _in(root, "ip_core.v"))
    monkeypatch.setattr(sys, "argv", ["flow_step_output_content_check.py",
                                      str(root), "--mode", "rtl"])
    assert C.main() == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "PASS: rtl output content"
    assert lines[1].startswith("DISCLOSURE: DECLARATION_ORDER_RELAXED")
    evidence = json.loads(next(l for l in lines
                               if l.startswith("TOOL_EVIDENCE:"))
                          .split(":", 1)[1])
    row = evidence["tools"]["Yosys.JsonHeader"]
    assert row["exit_code"] == 0 and row["strict_exit_code"] == 1
    assert row["relaxed_flags"] == [FLAG]
    assert any("DECLARATION_ORDER_RELAXED" in d
               for d in evidence["disclosures"])
    assert "used before its declaration" in evidence["strict_refusal"]


def test_an_unrelaxed_pass_prints_no_disclosure(monkeypatch, tmp_path,
                                                capsys):
    root = _project(tmp_path)

    def _invoke(tool, args, project, image):
        return subprocess.CompletedProcess([tool], 0, "", "")

    monkeypatch.setattr(F, "_invoke", _invoke)
    monkeypatch.setattr(sys, "argv", ["flow_step_output_content_check.py",
                                      str(root), "--mode", "rtl"])
    assert C.main() == 0
    out = capsys.readouterr().out
    assert "DISCLOSURE" not in out
    evidence = json.loads(out.split("TOOL_EVIDENCE:", 1)[1])
    assert "disclosures" not in evidence
    assert "strict_exit_code" not in evidence["tools"]["Yosys.JsonHeader"]
