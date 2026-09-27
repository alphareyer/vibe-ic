#!/usr/bin/env python3
"""FX_P2 review fix (6c, 6d) — two MINORs.

(6c) 065ef1c45 appended the read-enable tokens (ren/rden/rd_en/read_en) to the
     same bus-activity list the boot-latency and reset-glitch oracles pick the
     FIRST matching output from, in port order. A CPU top that exposes both a
     handshake and a data-port read-enable (`o_dmem_ren` listed before
     `o_ibus_cyc`) switched from observing its instruction fetch to observing a
     data read: a false red for boot latency, a missed fetch race for the
     glitch oracle. Handshake tokens now win; a read-enable is the fallback for
     a top with no handshake (the SRAM-only top the change was measured on).

(6d) The branch dated phase-3 records against this run's phase-2 netlist only
     to excuse a missing reading; `_requirements_backed` still credited an
     older phase-3 PASS as this run's measurement. Now a phase-3 record older
     than the netlist backs nothing (it reads as absent). A stale FAILING
     record stays failing -- dating never turns a red into an absence.

Driven through the real generators and the real gate (as a subprocess).
chip-AGNOSTIC: synthetic ports and project.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import cpu_boot_latency_oracle_tb_gen as CLG    # noqa: E402
import reset_invariant_oracle_tb_gen as RIV     # noqa: E402
import phase1_post_process as P                 # noqa: E402

TOP = "core_top"
INPUTS = [("i_clk", ""), ("i_rst", ""), ("i_ibus_rdt", "[31:0]")]
#: The data-port read-enable is listed FIRST.
BOTH = [("o_dmem_ren", ""), ("o_ibus_cyc", ""), ("o_ibus_adr", "[31:0]")]
SRAM_ONLY = [("o_sram_waddr", "[8:0]"), ("o_sram_wen", ""),
             ("o_sram_ren", ""), ("o_gpio", "")]
BOOT = {"name": "boot", "kind": "functional_vector",
        "stimulus": "Reset 解除後 N cycle 內取得第一條 instruction",
        "expected": "N ≤ 10 cycle"}
GLITCH = {"name": "glitch", "kind": "functional_vector",
          "stimulus": "i_rst glitch 不應導致 instruction fetch race",
          "expected": "holds: i_rst glitch 不應導致 instruction fetch race"}


def _observes(text: str, port: str) -> bool:
    """The oracle's activity comparison is on `port` (every DUT wire is
    declared, so a bare substring says nothing)."""
    return f"(({port}) === 1'b1" in text or f"if (({port}) === 1'b1" in text


# ---- (6c) --------------------------------------------------------------
def test_a_handshake_wins_over_a_read_enable_listed_first():
    assert CLG._pick_bus_activity_output(BOTH) == "o_ibus_cyc"


def test_the_boot_oracle_still_observes_the_fetch_handshake():
    text = CLG.emit_case_oracle_from_ports(BOOT, TOP, INPUTS, BOTH, [])
    assert text and _observes(text, "o_ibus_cyc"), text
    assert not _observes(text, "o_dmem_ren")


def test_the_glitch_oracle_still_observes_the_fetch_handshake():
    text = RIV.emit_case_oracle_from_ports(GLITCH, TOP, INPUTS, BOTH, [])
    assert text and _observes(text, "o_ibus_cyc"), text
    assert not _observes(text, "o_dmem_ren")


def test_a_top_with_only_a_read_enable_falls_back_to_it():
    """The measured SRAM-only top keeps what the branch fixed."""
    assert CLG._pick_bus_activity_output(SRAM_ONLY) == "o_sram_ren"
    text = RIV.emit_case_oracle_from_ports(GLITCH, TOP, INPUTS, SRAM_ONLY, [])
    assert text and _observes(text, "o_sram_ren")


# ---- (6d) --------------------------------------------------------------
GATE = PROGRAMS / "l24_signoff_evidence_backed_check.py"
T_OLD, T_NEW = 1_700_000_000, 1_700_100_000


def _project(tmp_path: Path) -> Path:
    d = tmp_path / "input" / "docs"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("Sign-off requires DRC clean.\n")
    doc = P.emit_l_doc_skeleton("L24", "unknown", project_dir=tmp_path)
    doc.pop("evidence", None)
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L24_SIGNOFF.json").write_text(json.dumps(doc))
    return tmp_path


def _at(path: Path, text: str, t: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (t, t))
    return path


def _drc(proj: Path, status: str, t: int) -> None:
    _at(proj / "reports" / "phase3" / "drc_verdict.json",
        json.dumps({"status": status}), t)


def _netlist(proj: Path, t: int) -> None:
    _at(proj / "phase2" / "stage2" / "synth" / "top_synth.v",
        "module top; endmodule\n", t)


def _gate(proj: Path):
    r = subprocess.run([sys.executable, str(GATE), str(proj)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def test_a_stale_phase3_pass_backs_nothing(tmp_path):
    """An earlier run's DRC PASS, older than this run's netlist: before phase 3
    of THIS netlist the requirement is not yet measurable -- not backed."""
    proj = _project(tmp_path)
    _drc(proj, "PASS", T_OLD)
    _netlist(proj, T_NEW)
    rc, out = _gate(proj)
    assert rc == 0, out
    assert "backed by" not in out, out
    assert "not yet measurable" in out


def test_a_stale_pass_does_not_back_this_runs_phase3(tmp_path):
    """This run's phase 3 ran (a record newer than the netlist) and wrote no
    DRC reading; the earlier run's DRC PASS does not stand in for it."""
    proj = _project(tmp_path)
    _drc(proj, "PASS", T_OLD)
    _netlist(proj, T_OLD + 10)
    _at(proj / "reports" / "orchestrator" / "phase3_one_shot.json",
        json.dumps({"verdict": "FAIL"}), T_NEW)
    rc, out = _gate(proj)
    assert rc == 1, out
    assert "the input REQUIRES DRC clean" in out


def test_a_fresh_phase3_pass_is_still_backed(tmp_path):
    """CONTROL: a DRC PASS written after this run's synthesis backs it."""
    proj = _project(tmp_path)
    _netlist(proj, T_OLD)
    _drc(proj, "PASS", T_NEW)
    rc, out = _gate(proj)
    assert rc == 0, out
    assert "backed by" in out


def test_a_stale_fail_is_never_turned_into_an_absence(tmp_path):
    proj = _project(tmp_path)
    _drc(proj, "FAIL", T_OLD)
    _netlist(proj, T_NEW)
    rc, out = _gate(proj)
    assert rc == 1, out
