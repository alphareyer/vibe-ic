"""Step 31's LVS must judge the layout that SHIPS (lane fxlvs).

THE DEFECT. `step_lvs` extracted only the ROUTED DEF over LEF abstracts (every
standard cell a black box whose pins come from the LEF) and never opened the
shipped GDS. MEASURED, spm x gf180mcuD, same-RTL run (image 0.3.83): the flow
recorded `LVS_MATCH_POWER_VERIFIED` on a shipped `spm.gds` whose 31 standard
cells carried 0 pin labels; extracting THAT file with the PDK's own Magic tech
and comparing with the PDK's own netgen setup gives 29 pin mismatches and
"Netlists do not match". Nothing the stream or a post-stream pass did to the
GDS could reach the flow's verdict.

THE RULE NOW. Step 31 has two arms and both must be clean:
  * SHIPPED GDS (the sign-off): `<pnr>/<top>.gds` extracted by Magic with the
    PDK's magicrc (`gds read`, cells at transistor level, LibreLane's recipe),
    compared by netgen with the PDK's setup file against the powered netlist
    OpenROAD writes from the routed DEF (`write_verilog -include_pwr_gnd`) plus
    the PDK's declared `CELL_SPICE_MODELS`;
  * ROUTED DEF (kept, a different question): the P&R database vs the gate
    netlist -- the shipped-GDS arm's schematic is written FROM the DEF, so it
    cannot see a DEF that disagrees with the netlist.
A missing GDS / cell-SPICE declaration / tool is NOT_MEASURED with the reason,
never a PASS; the Step-31 gate re-derives both transcripts.

Driven through the REAL `step_lvs`; only the EDA tools' file writes are faked.
chip/PDK-AGNOSTIC: a synthetic PDK tree and a synthetic design.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eda_report_audit as audit  # noqa: E402
import signoff_audit  # noqa: E402
import lvs_tapeout_signoff_check as tapeout_lvs  # noqa: E402
import phase3_one_shot_runner as runner  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shipped_gds_lvs_double import (  # noqa: E402
    CONFIG, LIB, MATCH, answer_tools, netgen_report, pdk_tree)

TOP = "widget"


def _pdk(root: Path):
    return runner.PdkConfig(
        name="acme", liberty=str(root / "libs.ref" / LIB / "lib" / "x.lib"),
        tech_lef=str(root / "libs.ref" / LIB / "techlef" / "t.tlef"),
        cell_lef=str(root / "libs.ref" / LIB / "lef" / f"{LIB}.lef"),
        cell_gds=None, site="s", drc_deck=None)


def _project(tmp_path: Path, *, gds: bytes | None) -> Path:
    project = tmp_path / "proj"
    pnr = project / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / f"{TOP}.def").write_text(
        f"VERSION 5.8 ;\nDESIGN {TOP} ;\nEND DESIGN\n")
    (pnr / f"{TOP}_pnr.v").write_text(
        f"module {TOP}(a, y);\ninput a;\noutput y;\nendmodule\n")
    if gds is not None:
        (pnr / f"{TOP}.gds").write_bytes(gds)
    return project


class RoutedDefTools:
    """The routed-DEF arm's tools, faked only where they write files;
    `def_verdict` is what netgen says about that extraction. The shipped-GDS
    arm is answered by `answer_tools` from the bytes of the GDS magic reads,
    so its verdict follows the LAYOUT, not the test's wish."""

    def __init__(self, def_verdict: str, *, openroad: bool = True):
        self.def_verdict = def_verdict
        self.openroad = openroad

    def __call__(self, container, cmd, timeout=0, **_kw):
        if cmd.startswith("command -v"):
            return (0 if (self.openroad or "openroad" not in cmd) else 1,
                    "", "")
        if cmd.startswith("test -f"):
            return (0, "", "")
        if "magic" in cmd and "SPICE_OUT=" in cmd:
            env = dict(re.findall(r"(\w+)=(\S+)", cmd))
            out = Path(env["SPICE_OUT"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(f".subckt {TOP} a y\n.ends\n")
            Path(env["FEEDBACK_OUT"]).write_text("")
            (out.parent / "ext2spice.log").write_text("MAGIC_EXT2SPICE_DONE\n")
            return (0, "MAGIC_EXT2SPICE_DONE\n", "")
        if "netgen" in cmd:
            m = re.search(r"(\S+/lvs(?:_power_aware)?\.rpt)", cmd)
            if m:
                Path(m.group(1)).write_text(
                    netgen_report(TOP, self.def_verdict))
            return (0, self.def_verdict, "")
        return (0, "", "")


class Recorder:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, container, cmd, timeout=0, **kw):
        self.calls.append(cmd)
        return self.fn(container, cmd, timeout=timeout, **kw)


def _run(tmp_path, monkeypatch, *, gds, def_verdict=MATCH, config=CONFIG,
         openroad=True):
    root = pdk_tree(tmp_path, config=config)
    project = _project(tmp_path, gds=gds)
    tools = Recorder(answer_tools(RoutedDefTools(def_verdict,
                                                 openroad=openroad), TOP))
    monkeypatch.setattr(runner, "_docker_exec", tools)
    monkeypatch.setattr(runner, "_to_container_path", lambda s, c: s)
    row = runner.step_lvs(project, TOP, _pdk(root), "x")
    vpath = project / "reports" / "phase3" / "lvs_verdict.json"
    verdict = json.loads(vpath.read_text()) if vpath.is_file() else {}
    return project, row, verdict, tools


def _magic_read_the_shipped_gds(project: Path, tools: Recorder) -> bool:
    gds = str(project / "phase3" / "stage3" / "pnr" / f"{TOP}.gds")
    for cmd in tools.calls:
        m = re.search(r"-rcfile \S+ (\S+\.tcl)", cmd)
        if "magic" not in cmd or not m or gds not in cmd:
            continue
        if "gds read $env(GDS)" in Path(m.group(1)).read_text():
            return True
    return False


# ── THE DEFECT: the flow's LVS never opened the layout that ships ─────────
def test_the_shipped_gds_is_extracted_by_magic(tmp_path, monkeypatch):
    project, row, _v, tools = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert _magic_read_the_shipped_gds(project, tools), (
        "no magic session read the shipped GDS: the step-31 LVS judged only "
        "the routed DEF", [c[:160] for c in tools.calls])


def test_a_shipped_gds_that_fails_pin_matching_fails_the_step(
        tmp_path, monkeypatch):
    """The routed DEF matches; the SHIPPED layout does not. That is a FAIL."""
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=b"unlabelled")
    assert row.status == "FAIL", (row.status, row.detail)
    assert row.extras["finding"] == "LVS_SHIPPED_GDS_MISMATCH"
    assert row.extras["routed_def_lvs"] == "PASS"
    assert v["status"] == "FAIL" and v["finding"] == "LVS_SHIPPED_GDS_MISMATCH"
    assert v["routed_def_arm"]["status"] == "PASS"
    # lvs.rpt remains the routed-DEF producer's output; the named shipped
    # report is independently consumed by the sign-off gate.
    rpt = project / "reports" / "phase3"
    assert "match uniquely" in (rpt / "lvs.rpt").read_text()
    assert "failed pin matching" in (rpt / "lvs_shipped_gds.rpt").read_text()
    gate = audit._check_lvs(project)
    assert gate.passed is False
    assert gate.summary.get("terminal_verdict") == "MISMATCH", gate.summary


def test_both_arms_clean_is_a_pass_and_names_the_shipped_compare(
        tmp_path, monkeypatch):
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert row.status == "PASS", (row.status, row.detail)
    assert row.extras["shipped_gds_lvs"] == "PASS"
    assert v["status"] == "PASS"
    assert v["signoff_layout"] == "shipped_gds"
    shipped = v["shipped_gds"]
    assert shipped["finding"] == "LVS_SHIPPED_GDS_MATCH"
    assert shipped["layout"] == f"phase3/stage3/pnr/{TOP}.gds"
    assert shipped["layout_sha256"]
    assert shipped["report"] == "reports/phase3/lvs_shipped_gds.rpt"
    gate = audit._check_lvs(project)
    assert gate.passed is True, [f.message for f in gate.findings]


def test_the_gate_rederives_the_shipped_transcript(tmp_path, monkeypatch):
    """Both arms clean, then the shipped transcript is replaced by a mismatch:
    the gate reads it by the name the verdict gives, so it must refuse."""
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert row.status == "PASS"
    (project / v["shipped_gds"]["report"]).write_text(netgen_report(
        TOP, "Final result: Top level cell failed pin matching.\n"))
    gate = audit._check_lvs(project)
    assert gate.passed is False
    assert gate.summary.get("terminal_verdict") == "MISMATCH"


def test_the_gate_requires_a_terminal_verdict_in_each_arm(tmp_path, monkeypatch):
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert row.status == "PASS"
    report = project / v["shipped_gds"]["report"]
    report.write_text(netgen_report(TOP, "Netlists match uniquely.\n"))
    gate = audit._check_lvs(project)
    assert not gate.passed and gate.summary["terminal_verdict"] == "INCOMPLETE"


def test_missing_named_shipped_report_refuses_gate(tmp_path, monkeypatch):
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert row.status == "PASS"
    (project / v["shipped_gds"]["report"]).unlink()
    gate = audit._check_lvs(project)
    assert not gate.passed
    assert any(f.rule == "LVS_SHIPPED_GDS_REPORT_MISSING" for f in gate.findings)


def test_shipped_extraction_refusal_does_not_pass_on_def_match(tmp_path, monkeypatch):
    root = pdk_tree(tmp_path)
    project = _project(tmp_path, gds=b"labelled")
    tools = Recorder(answer_tools(RoutedDefTools(MATCH), TOP))
    monkeypatch.setattr(runner, "_docker_exec", tools)
    monkeypatch.setattr(runner, "_to_container_path", lambda s, c: s)
    session = runner._declared_session_exec
    def magic_writes_no_netlist(*args, **kwargs):
        if "magic -dnull" in str(args[1]) and " GDS=" in str(args[1]):
            log = project / "phase3/stage3/extracted/shipped_gds/ext2spice.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("MAGIC_EXT2SPICE_DONE\n")
            return 0, "", ""
        return session(*args, **kwargs)
    monkeypatch.setattr(runner, "_declared_session_exec", magic_writes_no_netlist)
    row = runner.step_lvs(project, TOP, _pdk(root), "x")
    verdict = json.loads((project / "reports/phase3/lvs_verdict.json").read_text())
    assert row.status == "FAIL"
    assert row.extras["finding"] == "LVS_SHIPPED_GDS_EXTRACTION_NO_NETLIST"
    assert verdict["compare_performed"] is False
    gate = audit._check_lvs(project)
    assert not gate.passed


def test_tapeout_lvs_readers_honour_shipped_blocked_verdict(tmp_path):
    project = tmp_path / "p"
    rpt = project / "reports/phase3/lvs.rpt"
    rpt.parent.mkdir(parents=True)
    rpt.write_text(netgen_report(TOP, MATCH))
    (rpt.parent / "lvs_verdict.json").write_text(json.dumps(
        {"status": "BLOCKED", "finding": "LVS_SHIPPED_GDS_ABSENT"}))
    _, result = signoff_audit._evaluate_lvs(project)
    assert result["passed"] is False and result["tapeout_verdict"] == "BLOCKED"
    result = tapeout_lvs.check(project)
    assert result["passed"] is False and result["tapeout_verdict"] == "BLOCKED"


def test_sdr_shadow_admission_calls_routed_def_only_lvs(tmp_path, monkeypatch):
    project = tmp_path / "shadow"
    project.mkdir()
    pdk = _pdk(pdk_tree(tmp_path))
    pdk.drc_deck = "synthetic-deck"
    def gds(*args, **kwargs):
        layout = project / "phase3/stage3/pnr" / f"{TOP}.gds"
        layout.parent.mkdir(parents=True, exist_ok=True)
        layout.write_bytes(b"candidate")
        future_ns = time.time_ns() + 2_000_000_000
        os.utime(layout, ns=(future_ns, future_ns))
        return runner.StepResult("gds", "PASS", 0, "ok")
    def drc(*args, **kwargs):
        report = project / "phase3/reports/drc.rpt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('<report-database><items/></report-database>')
        future_ns = time.time_ns() + 2_000_000_000
        os.utime(report, ns=(future_ns, future_ns))
        return runner.StepResult("drc", "PASS", 0, "ok")
    called = {}
    def lvs(*args, **kwargs):
        called.update(kwargs)
        v = project / "reports/phase3/lvs_verdict.json"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_text('{"status":"PASS"}')
        future_ns = time.time_ns() + 2_000_000_000
        os.utime(v, ns=(future_ns, future_ns))
        return runner.StepResult("lvs", "PASS", 0, "ok")
    monkeypatch.setattr(runner, "step_gds", gds)
    monkeypatch.setattr(runner, "step_drc", drc)
    monkeypatch.setattr(runner, "step_lvs", lvs)
    admitted, reason = runner._sdr_candidate_signoff_clean_in_shadow(
        project, TOP, pdk, "x")
    assert admitted, reason
    assert called.get("_skip_shipped_gds") is True


def test_second_run_netgen_crash_cannot_reuse_prior_report(tmp_path, monkeypatch):
    project, row, _v, tools = _run(tmp_path, monkeypatch, gds=b"labelled")
    assert row.status == "PASS"
    (project / "phase3/stage3/pnr/widget.gds").write_bytes(b"unlabelled")
    original = runner._docker_exec
    def crash(container, cmd, timeout=0, **kw):
        if "netgen -batch source" in cmd:
            return 137, "", "killed"
        return original(container, cmd, timeout=timeout, **kw)
    monkeypatch.setattr(runner, "_docker_exec", crash)
    # call the public step again in the same project; prior products are stale.
    root = pdk_tree(tmp_path)
    tools2 = Recorder(crash)
    monkeypatch.setattr(runner, "_to_container_path", lambda s, c: s)
    row2 = runner.step_lvs(project, TOP, _pdk(root), "x")
    assert row2.status == "NOT_MEASURED", (row2.status, row2.detail)
    assert row2.extras["finding"] == "LVS_SHIPPED_GDS_NETGEN_ERROR"


def test_no_shipped_gds_is_not_verified_never_a_pass(tmp_path, monkeypatch):
    project, row, v, _t = _run(tmp_path, monkeypatch, gds=None)
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert row.reason_class == "input_absent"
    assert row.extras["finding"] == "LVS_SHIPPED_GDS_ABSENT"
    assert row.extras["routed_def_lvs"] == "PASS"
    assert v["status"] == "BLOCKED" and v["finding"] == "LVS_SHIPPED_GDS_ABSENT"
    # The routed-DEF MATCH transcript is on disk at the canonical path; the
    # gate must still refuse to certify the shipped layout from it.
    assert "match uniquely" in (
        project / "reports" / "phase3" / "lvs.rpt").read_text()
    gate = audit._check_lvs(project)
    assert gate.passed is False
    assert gate.summary.get("terminal_verdict") == "BLOCKED", gate.summary


def test_a_pdk_that_declares_no_cell_spice_is_not_verified(
        tmp_path, monkeypatch):
    _p, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled", config=None)
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert row.extras["finding"] == "LVS_SHIPPED_GDS_NO_CELL_SPICE"
    assert "CELL_SPICE_MODELS" in row.detail
    assert v["status"] == "BLOCKED"


def test_no_openroad_is_not_verified(tmp_path, monkeypatch):
    _p, row, _v, _t = _run(tmp_path, monkeypatch, gds=b"labelled",
                           openroad=False)
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert row.extras["finding"] == "LVS_SHIPPED_GDS_TOOL_ABSENT"
    assert row.reason_class == "tool_absent"


def test_the_routed_def_question_is_kept(tmp_path, monkeypatch):
    """A clean shipped GDS does not excuse a routed DEF that disagrees with the
    gate netlist: the step is the worse arm, and that arm's record stands."""
    _p, row, v, _t = _run(tmp_path, monkeypatch, gds=b"labelled",
                          def_verdict="Final result: Netlists do not match.\n")
    assert row.status == "FAIL", (row.status, row.detail)
    assert row.extras["shipped_gds_lvs"] == "PASS"
    assert v["finding"] == "LVS_MISMATCH"
    assert v["shipped_gds_arm"]["status"] == "PASS"


def test_the_schematic_is_the_routed_databases_powered_netlist(
        tmp_path, monkeypatch):
    """Not derived from the layout's port list: OpenROAD writes it from the
    routed DEF, and netgen reads it with the PDK's own setup and cell models."""
    project, _row, _v, tools = _run(tmp_path, monkeypatch, gds=b"labelled")
    work = project / "phase3" / "stage3" / "extracted" / "shipped_gds"
    tcl = (work / "write_powered_netlist.tcl").read_text()
    assert f"read_def {{{project / 'phase3/stage3/pnr' / (TOP + '.def')}}}" in tcl
    assert "write_verilog -include_pwr_gnd" in tcl
    netgen = [c for c in tools.calls if "netgen -batch source" in c]
    assert len(netgen) == 1
    assert "NETGEN_SETUP=/foss/pdks/acme/libs.tech/netgen/acme_setup.tcl" \
        in netgen[0], netgen[0]
    assert f"{LIB}/spice/{LIB}.spice" in netgen[0]
    assert "MAGIC_EXT_USE_GDS=1" in netgen[0]


# ── the PDK's own declaration, both grammars ──────────────────────────────
@pytest.mark.parametrize("config", [
    CONFIG,
    ('set ::env(CELL_SPICE_MODELS) [glob "$::env(PDK_ROOT)/$::env(PDK)/'
     'libs.ref/$::env(STD_CELL_LIBRARY)/spice/*.spice"]\n'),
])
def test_cell_spice_models_come_from_the_pdk_config(tmp_path, config):
    root = pdk_tree(tmp_path, config=config)
    files, src = runner._pdk_cell_spice_models(_pdk(root), None)
    assert files == [str(root / "libs.ref" / LIB / "spice" / f"{LIB}.spice")]
    assert src.endswith("libs.tech/librelane/config.tcl:CELL_SPICE_MODELS")


def test_a_declared_model_that_does_not_exist_is_refused(tmp_path):
    root = pdk_tree(tmp_path, config=CONFIG.replace('.spice"', '.spi"'))
    files, why = runner._pdk_cell_spice_models(_pdk(root), None)
    assert files == [] and "does not exist" in why


def test_unreadable_pdk_config_is_not_reported_as_undeclared(tmp_path, monkeypatch):
    root = pdk_tree(tmp_path, config=None)
    monkeypatch.setattr(runner, "_docker_exec",
                        lambda *a, **k: (137, "", "exec failed"))
    files, why = runner._pdk_cell_spice_models(_pdk(root), "x")
    assert not files and "could not be read" in why


def test_failed_pdk_model_probe_is_not_reported_as_missing_file(
        tmp_path, monkeypatch):
    config = ('set ::env(CELL_SPICE_MODELS) [glob '
              '"$::env(PDK_ROOT)/$::env(PDK)/libs.ref/'
              '$::env(STD_CELL_LIBRARY)/spice/*.spice"]\n')
    root = pdk_tree(tmp_path, config=config)
    for f in (root / "libs.ref" / LIB / "spice").glob("*.spice"):
        f.unlink()
    monkeypatch.setattr(runner, "_docker_exec",
                        lambda *a, **k: (137, "", "exec failed"))
    files, why = runner._pdk_cell_spice_models(_pdk(root), "x")
    assert not files and "probe failed" in why and "rc=137" in why


def test_placed_hard_macros_are_read_and_abstracted_by_name(tmp_path,
                                                            monkeypatch):
    """A hard macro the routed DEF places: OpenROAD must read its LEF to write
    the powered netlist, and the extraction compares it by its pins (its own
    LVS verified its devices). A macro the DEF does not place is neither."""
    root = pdk_tree(tmp_path)
    macro_lef = tmp_path / "macros.lef"
    macro_lef.write_text("MACRO acme_macro\nEND acme_macro\n"
                         "MACRO acme_unused\nEND acme_unused\n")
    project = _project(tmp_path, gds=b"labelled")
    (project / "phase3" / "stage3" / "pnr" / f"{TOP}.def").write_text(
        f"VERSION 5.8 ;\nDESIGN {TOP} ;\nCOMPONENTS 2 ;\n"
        f"- u_mac acme_macro + PLACED ( 0 0 ) N ;\n"
        f"- u_inv {LIB}__inv_1 + PLACED ( 0 0 ) N ;\n"
        f"END COMPONENTS\nEND DESIGN\n")
    tools = Recorder(answer_tools(RoutedDefTools(MATCH), TOP))
    monkeypatch.setattr(runner, "_docker_exec", tools)
    monkeypatch.setattr(runner, "_to_container_path", lambda s, c: s)
    import dataclasses
    pdk = dataclasses.replace(_pdk(root), macro_lefs=[str(macro_lef)])
    row = runner.step_lvs(project, TOP, pdk, "x")
    assert row.extras["shipped_gds_lvs"] == "PASS", row.detail
    work = project / "phase3" / "stage3" / "extracted" / "shipped_gds"
    assert f"read_lef {{{macro_lef}}}" in (
        work / "write_powered_netlist.tcl").read_text()
    magic = [c for c in tools.calls if "magic" in c and "GDS=" in c]
    assert len(magic) == 1 and "ABSTRACT_CELLS=acme_macro " in magic[0], magic
    rec = json.loads((project / row.extras["shipped_gds_record"]).read_text())
    assert rec["abstracted_cells"] == ["acme_macro"]
