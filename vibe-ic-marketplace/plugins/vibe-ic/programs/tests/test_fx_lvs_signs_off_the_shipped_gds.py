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
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eda_report_audit as audit  # noqa: E402
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
    # The canonical transcript the Step-31 gate reads is the shipped compare;
    # the routed-DEF transcript is kept beside it.
    rpt = project / "reports" / "phase3"
    assert "failed pin matching" in (rpt / "lvs.rpt").read_text()
    assert "match uniquely" in (rpt / "lvs_routed_def.rpt").read_text()
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
