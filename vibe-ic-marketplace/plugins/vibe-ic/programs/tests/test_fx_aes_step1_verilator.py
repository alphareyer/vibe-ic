"""FX_AES_STEP1_VERILATOR (+ review wave 8): step 1's rtl content check refused
reused IP for a read-order defect of its own, and said nothing about why.

MEASURED on opentitan_aes x sky130A: `flow_step_output_content_check . --mode
rtl` printed only `FAIL: Verilator lint failed`. The P0 front end handed
Verilator the staged sources ALPHABETICALLY, so every file that names a package
type came before the package: 14x "Reference to '<type>' before declaration
(IEEE 1800-2023 6.18)". Verilator elaborates in one pass; Yosys `read_slang
--single-unit` is order-tolerant and passed. With packages in dependency order
Verilator reports 0 errors on that set.

Review wave 8 (AESV): a define-only file must still precede the packages that
use its macros; each tool's own outcome is kept (a measured Yosys FAIL is never
relabelled NOT_MEASURED by a Verilator stall); tools are stopped only on a
progress STALL, never by a clock (#2051); and the disclosure covers the
blocking-code warnings and slang's own error lines.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
for _p in (str(PROGRAMS), str(PROGRAMS.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_step_output_content_check as C  # noqa: E402
import p0_tool_frontend_check as P0  # noqa: E402

RTL = "phase2/stage1/rtl"
RECORD = "reports/phase2/lint/rtl_content_check.json"

# A package whose name sorts AFTER the module that uses it: the shape that a
# single-pass elaborator refuses when the read order is alphabetical.
USER = ("module a_user\n  import z_pkg::*;\n"
        "(input logic clk, output mode_e m);\n"
        "  assign m = MODE_ON;\nendmodule\n")
PKG = ("package z_pkg;\n  typedef enum logic [0:0] {MODE_OFF, MODE_ON} mode_e;\n"
       "endpackage\n")

# The review's shape: a macro-only source file a package depends on.
DEFINES = "`define WORD_W 8\n"
MACRO_PKG = ("package b_pkg;\n  typedef logic [`WORD_W-1:0] word_t;\n"
             "endpackage\n")
MACRO_TOP = ("module c_top\n  import b_pkg::*;\n"
             "(input word_t d, output word_t q);\n  assign q = d;\nendmodule\n")


def _project(tmp_path: Path, files=None) -> Path:
    p = tmp_path / "proj"
    (p / RTL).mkdir(parents=True)
    for name, text in (files or {"a_user.sv": USER, "z_pkg.sv": PKG}).items():
        (p / RTL / name).write_text(text)
    return p


class _Tools:
    """Stands in for the tools' OUTPUT only. Verilator: a single-pass reader's
    ORDER rule (a file naming `pkg::` before `package pkg` was read errors the
    way Verilator does). Yosys: whatever the test scripts."""

    def __init__(self, yosys=(0, ""), verilator=None):
        self.argv = {}
        self.yosys = yosys
        self.verilator = verilator

    def __call__(self, tool, args, project, image):
        self.argv[tool] = list(args)
        if tool == "yosys":
            rc, out = self.yosys
            if isinstance(rc, Exception):
                raise rc
            return subprocess.CompletedProcess(args, rc, out, "")
        if self.verilator is not None:
            rc, out = self.verilator
            if isinstance(rc, Exception):
                raise rc
            return subprocess.CompletedProcess(args, rc, out, "")
        seen, out = set(), []
        for a in args:
            if not a.endswith((".sv", ".v")):
                continue
            text = Path(a).read_text()
            for line in text.splitlines():
                if line.startswith("package "):
                    seen.add(line.split()[1].rstrip(";"))
            if "z_pkg::" in text and "z_pkg" not in seen:
                out.append(f"%Error: {a}:1:40: Reference to 'mode_e' before "
                           f"declaration (IEEE 1800-2023 6.18)")
        return subprocess.CompletedProcess(args, 1 if out else 0, "",
                                           "\n".join(out) + "\n")


@pytest.fixture
def tools(monkeypatch):
    def install(**kw):
        fake = _Tools(**kw)
        monkeypatch.setattr(P0, "_invoke", fake)
        monkeypatch.setattr(P0, "_route_image", lambda tool, image: None)
        return fake
    return install


def _main(project: Path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", str(project), "--mode", "rtl"])
    rc = C.main()
    return rc, capsys.readouterr().out


def _real_tool_or_skip():
    """NOT_VERIFIED unless a tool can actually run: verilator on PATH, or the
    pinned image present LOCALLY (never pulled mid-suite)."""
    if shutil.which("verilator") and shutil.which("yosys"):
        return
    if not shutil.which("docker"):
        pytest.skip("NOT_VERIFIED: no verilator/yosys and no docker on this host")
    import _eda_pin
    try:
        refs, why = _eda_pin.local_references_for_digest(
            _eda_pin.resolved_image_digest())
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"NOT_VERIFIED: the pinned EDA image does not resolve: {exc}")
    if not refs:
        pytest.skip(f"NOT_VERIFIED: the pinned EDA image is not local ({why})")


def _ran_or_skip(verdict: dict) -> None:
    if not verdict["tools"]:
        pytest.fail(f"no tool ran: {verdict['findings']} "
                    f"{verdict.get('not_measured')}")
    for name, row in verdict["tools"].items():
        if row["exit_code"] >= 125 and row["execution"] != "host":
            pytest.skip(f"NOT_VERIFIED: docker could not launch {name} "
                        f"(exit {row['exit_code']})")


# ── the read order ────────────────────────────────────────────────────────

def test_packages_are_read_before_the_rtl_that_uses_them(tmp_path, tools):
    fake = tools()
    p = _project(tmp_path)
    verdict = P0.check(p)
    files = [a for a in fake.argv["verilator"] if a.endswith(".sv")]
    assert [Path(f).name for f in files] == ["z_pkg.sv", "a_user.sv"]
    assert verdict["findings"] == [], verdict["findings"]


def test_a_define_only_file_precedes_the_packages_that_use_it(tmp_path, tools):
    fake = tools()
    p = _project(tmp_path, {"a_defines.sv": DEFINES, "b_pkg.sv": MACRO_PKG,
                            "c_top.sv": MACRO_TOP})
    P0.check(p)
    files = [Path(a).name for a in fake.argv["verilator"] if a.endswith(".sv")]
    assert files == ["a_defines.sv", "b_pkg.sv", "c_top.sv"]


@pytest.mark.parametrize("files", [
    {"a_user.sv": USER, "z_pkg.sv": PKG},
    {"a_defines.sv": DEFINES, "b_pkg.sv": MACRO_PKG, "c_top.sv": MACRO_TOP},
])
def test_the_real_tools_accept_the_ordered_set(tmp_path, files):
    """The real Verilator and Yosys (on PATH, else the LOCAL pinned image)."""
    _real_tool_or_skip()
    p = _project(tmp_path, files)
    verdict = P0.check(p)
    _ran_or_skip(verdict)
    for name, row in verdict["tools"].items():
        assert row["exit_code"] == 0, (name, row.get("output"))
    assert verdict["findings"] == []


# ── each tool's own outcome ───────────────────────────────────────────────

def test_a_measured_yosys_fail_survives_a_verilator_stall(tmp_path, tools,
                                                          capsys, monkeypatch):
    tools(yosys=(1, "/x/top.sv:3:5: error: unknown module 'foo'\n"
                    "ERROR: Compilation failed\n"),
          verilator=(P0.ToolStalled("verilator stalled: no progress"), ""))
    p = _project(tmp_path)
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 1, out
    assert "FAIL: Yosys elaboration failed" in out
    assert "/x/top.sv:3:5: error: unknown module 'foo'" in out
    assert "NOT_MEASURED Verilator.Lint: verilator stalled" in out


def test_a_stall_with_nothing_measured_failing_is_not_measured(tmp_path, tools,
                                                               capsys,
                                                               monkeypatch):
    tools(verilator=(P0.ToolStalled("verilator stalled: no progress"), ""))
    p = _project(tmp_path)
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 2, out
    assert out.startswith("NOT_MEASURED: Verilator.Lint: verilator stalled")
    assert "FAIL" not in out
    assert C.check(p, "rtl")                    # never an empty (passing) list


def test_a_host_tool_is_stopped_only_on_a_stall(tmp_path, capsys, monkeypatch):
    """No clock (#2051): the host path goes through `_progress_run.run` with no
    `timeout=`, and its `Stalled` is NOT_MEASURED."""
    import _progress_run
    src = ast.parse((PROGRAMS / "p0_tool_frontend_check.py").read_text())
    for node in ast.walk(src):
        if isinstance(node, ast.Call):
            assert "timeout" not in {k.arg for k in node.keywords}, \
                ast.unparse(node)[:80]
    p = _project(tmp_path)
    monkeypatch.setattr(P0.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def stalls(cmd, **kw):
        assert "timeout" not in kw
        raise _progress_run.Stalled(cmd, 3, 1.0, 9.0, {"cpu": True})
    monkeypatch.setattr(_progress_run, "run", stalls)
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 2, out
    assert "stalled" in out and "FAIL" not in out


def test_a_container_is_named_and_reaped_by_that_name(tmp_path, monkeypatch):
    """Docker path: the container gets this invocation's own name, and a
    stall reaps it BY that name (killing the client alone orphans the tool)."""
    import _docker_watchdog as D
    import _watchdog as W
    seen = {}
    monkeypatch.setattr(P0.shutil, "which",
                        lambda tool: "/usr/bin/docker" if tool == "docker" else None)

    def supervised(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return W.SupervisedResult(rc=W.RC_STALLED, out="", err="",
                                  outcome="stalled")
    monkeypatch.setattr(W, "run_host_supervised", supervised)
    reaped = []
    monkeypatch.setattr(D, "ephemeral_container_reap",
                        lambda name, **kw: reaped.append(name) or (lambda p, r: None))
    with pytest.raises(P0.ToolStalled):
        P0._invoke("verilator", ["--lint-only"], tmp_path, "img@sha256:" + "0" * 64)
    name = seen["cmd"][seen["cmd"].index("--name") + 1]
    assert name.startswith("vibeic_p0_") and reaped == [name]


# ── the disclosure ────────────────────────────────────────────────────────

def test_a_refused_rtl_check_prints_the_tools_own_words(tmp_path, tools,
                                                        capsys, monkeypatch):
    import rtl_transitive_cone as T
    tools()
    p = _project(tmp_path)
    # the failing (alphabetical) order, through the real reader and printer
    monkeypatch.setattr(T, "topological_package_first", lambda f: sorted(f))
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 1, out
    assert "FAIL: Verilator lint failed" in out
    assert "Reference to 'mode_e' before declaration" in out
    line = next(l for l in out.splitlines() if "Verilator.Lint: exit 1" in l)
    rel = line.split("transcript ")[1].split()[0]
    sha = line.split()[-1]
    assert "sha256:" + hashlib.sha256((p / rel).read_bytes()).hexdigest() == sha
    record = json.loads((p / RECORD).read_text())
    assert record["tools"]["Verilator.Lint"]["errors"]


def test_a_blocking_warning_is_disclosed_with_its_transcript(tmp_path, tools,
                                                             capsys,
                                                             monkeypatch):
    """SELRANGE is a %Warning under -Wno-fatal, exit 0, and still a refusal:
    its row, its line and its transcript are printed."""
    tools(verilator=(0, "%Warning-SELRANGE: /x/top.sv:3:10: Selection index "
                        "out of range\n"))
    p = _project(tmp_path)
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 1, out
    assert "FAIL: Verilator Warning-SELRANGE" in out
    assert "Verilator.Lint: exit 0; transcript reports/phase2/lint/" in out
    assert "%Warning-SELRANGE: /x/top.sv:3:10" in out
