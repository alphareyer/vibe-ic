"""FX_AES_STEP1_VERILATOR: step 1's rtl content check refused reused IP for a
read-order defect of its own, and said nothing about why.

MEASURED on opentitan_aes x sky130A (branch next/claude-fx-aes-l4-regmap, the
phase-2 final audit): `flow_step_output_content_check . --mode rtl` printed
only `FAIL: Verilator lint failed`. The front end
(`p0_tool_frontend_check.check`) handed Verilator the 126 staged sources in
ALPHABETICAL order, so every file that names a package type came before the
package (`aes_pkg.sv` was 22nd): 14x "Reference to '<type>' before declaration
(IEEE 1800-2023 6.18)", then `--error-limit`. Verilator elaborates in one pass;
Yosys `read_slang --single-unit` is order-tolerant and passed the same set.
With packages in dependency order (the shared `topological_package_first`,
#682), Verilator reports 0 errors on that set.

And the refusal disclosed nothing: the tool's diagnostics lived only in the
in-memory record and were printed only on PASS.
"""
from __future__ import annotations

import contextlib
import io
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

# A package whose name sorts AFTER the module that uses it: the shape that a
# single-pass elaborator refuses when the read order is alphabetical.
USER = ("module a_user\n  import z_pkg::*;\n"
        "(input logic clk, output mode_e m);\n"
        "  assign m = MODE_ON;\nendmodule\n")
PKG = ("package z_pkg;\n  typedef enum logic [0:0] {MODE_OFF, MODE_ON} mode_e;\n"
       "endpackage\n")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / RTL).mkdir(parents=True)
    (p / RTL / "a_user.sv").write_text(USER)
    (p / RTL / "z_pkg.sv").write_text(PKG)
    return p


class _Single:
    """A single-pass elaborator's ORDER rule, and nothing else: a file that
    names `pkg::` before `package pkg` has been read errors the way Verilator
    does. Stands in for the tool's output only (a real-tool test is below)."""

    def __init__(self):
        self.argv = {}

    def __call__(self, tool, args, project, image):
        self.argv[tool] = list(args)
        if tool != "verilator":
            return subprocess.CompletedProcess(args, 0, "", "")
        seen, out = set(), []
        for a in args:
            if not a.endswith((".sv", ".v")):
                continue
            text = Path(a).read_text()
            for line in text.splitlines():
                if line.startswith("package "):
                    seen.add(line.split()[1].rstrip(";"))
            for pkg in ("z_pkg",):
                if f"{pkg}::" in text and pkg not in seen:
                    out.append(f"%Error: {a}:1:40: Reference to 'mode_e' before "
                               f"declaration (IEEE 1800-2023 6.18)")
        rc = 1 if out else 0
        return subprocess.CompletedProcess(args, rc, "", "\n".join(out) + "\n")


@pytest.fixture
def fake_tools(monkeypatch):
    fake = _Single()
    monkeypatch.setattr(P0, "_invoke", fake)
    monkeypatch.setattr(P0, "_route_image", lambda tool, image: None)
    return fake


def _main(project: Path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", str(project), "--mode", "rtl"])
    rc = C.main()
    return rc, capsys.readouterr().out


# ── the read order ────────────────────────────────────────────────────────

def test_packages_are_read_before_the_rtl_that_uses_them(tmp_path, fake_tools):
    p = _project(tmp_path)
    verdict = P0.check(p)
    files = [a for a in fake_tools.argv["verilator"] if a.endswith(".sv")]
    assert [Path(f).name for f in files] == ["z_pkg.sv", "a_user.sv"]
    assert verdict["findings"] == [], verdict["findings"]


def test_the_real_single_pass_elaborator_accepts_the_ordered_set(tmp_path):
    """The real Verilator (on PATH, else the pinned image through docker)."""
    if not (shutil.which("verilator") or shutil.which("docker")):
        pytest.skip("NOT_VERIFIED: no verilator and no docker on this host")
    p = _project(tmp_path)
    verdict = P0.check(p)
    lint = verdict["tools"]["Verilator.Lint"]
    assert lint["exit_code"] == 0, lint.get("output")
    assert "Verilator lint failed" not in verdict["findings"]


# ── the disclosure ────────────────────────────────────────────────────────

def test_a_refused_rtl_check_prints_the_tools_own_words(tmp_path, fake_tools,
                                                        capsys, monkeypatch):
    p = _project(tmp_path)
    # the failing (alphabetical) order, through the real reader and printer
    import rtl_transitive_cone as T
    monkeypatch.setattr(T, "topological_package_first", lambda f: sorted(f))
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 1, out
    assert "FAIL: Verilator lint failed" in out
    assert "Reference to 'mode_e' before declaration" in out
    line = next(l for l in out.splitlines() if "Verilator.Lint: exit 1" in l)
    rel = line.split("transcript ")[1].split()[0]
    sha = line.split()[-1]
    import hashlib
    assert "sha256:" + hashlib.sha256((p / rel).read_bytes()).hexdigest() == sha
    record = json.loads((p / "reports/audit/phase2/lint/rtl_content_check.json")
                        .read_text())
    assert record["tools"]["Verilator.Lint"]["errors"]


def test_a_tool_past_its_deadline_is_not_measured(tmp_path, capsys,
                                                  monkeypatch):
    p = _project(tmp_path)
    monkeypatch.setattr(P0.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def slow(cmd, **kw):
        assert kw.get("timeout") == P0.TOOL_DEADLINE_S
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(P0.subprocess, "run", slow)
    rc, out = _main(p, capsys, monkeypatch)
    assert rc == 2, out
    assert out.startswith("NOT_MEASURED:") and "deadline" in out
    assert "FAIL" not in out
