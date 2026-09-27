"""The step-31 SHIPPED-GDS arm's inputs and tool writes, for tests of step_lvs.

Since lane fxlvs, `step_lvs` signs off on the layout that ships: it needs the
shipped `<pnr>/<top>.gds`, a PDK that declares its cell SPICE models
(`CELL_SPICE_MODELS` in its own flow configuration), and three tool sessions
(OpenROAD writes the routed database's powered netlist, Magic extracts the GDS,
netgen compares). A test whose subject is something else in step 31 -- the
routed-DEF arm, the tech preflight, a stall -- and which expects a clean step
must now provide those, exactly as a real run does. Nothing here fakes runner
logic: the PDK is a real directory tree and only the tools' file writes are
doubled, answering from the bytes the tool was told to read.

    p = _proj(tmp_path)
    monkeypatch.setattr(runner, "_docker_exec", fake)
    pdk = shipped_gds_ready(monkeypatch, runner, p, "chip_top", _pdk(), tmp_path)
    r = runner.step_lvs(p, "chip_top", pdk, "x")
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

#: A layout whose GDS carries this marker extracts to a netlist netgen cannot
#: pin-match (the shape the spm x gf180mcuD shipped GDS measured).
UNLABELLED = b"unlabelled"

MATCH = "Final result: Circuits match uniquely.\n"
PIN_FAIL = ("Cell pin lists for {top} and {top} altered to match.\n"
            "Pin mismatch: in cell '{top}' ckt1 pins {{VDD VSS}} found no "
            "counterpart (ckt2 has {{proxyVDD proxyVSS}}).\n"
            "Final result: Top level cell failed pin matching.\n")
LIB = "acme_sc"


def netgen_report(top: str, final: str) -> str:
    """A report in the shape netgen's `lvs` writes: bottom-up cell blocks, the
    top's `Device classes` line last, then the terminal verdict."""
    out = ["Netgen 1.5 compare, setup file read.\n"]
    for cell in [f"{LIB}__inv_1", f"{LIB}__nand2_1", f"{LIB}__dff_1", top]:
        out.append(
            "\nSubcircuit summary:\n"
            f"Circuit 1: {cell:<34}|Circuit 2: {cell:<34}\n"
            + "-" * 43 + "|" + "-" * 43 + "\n"
            f"{'Number of devices: 4':<43}|Number of devices: 4\n"
            f"{'Number of nets: 8':<43}|Number of nets: 8\n"
            + "-" * 87 + "\nNetlists match uniquely.\n"
            f"Device classes {cell} and {cell} are equivalent.\n")
    out.append("\n" + final)
    return "".join(out)


#: How an open_pdks PDK declares its cell models (gf180mcuD, ihp-sg13g2 form).
CONFIG = ('set ::env(CELL_SPICE_MODELS) "$::env(PDK_ROOT)/$::env(PDK)/libs.ref/'
          '$::env(STD_CELL_LIBRARY)/spice/$::env(STD_CELL_LIBRARY).spice"\n')


def pdk_tree(tmp_path: Path, *, config: str | None = CONFIG) -> Path:
    """A PDK laid out as open_pdks lays it out; `config` is its flow
    configuration's text (None: the PDK ships no flow configuration)."""
    root = Path(tmp_path) / "shipped_gds_pdk" / "acme"
    (root / "libs.ref" / LIB / "lef").mkdir(parents=True, exist_ok=True)
    (root / "libs.ref" / LIB / "lef" / f"{LIB}.lef").write_text(
        f"MACRO {LIB}__inv_1\nEND {LIB}__inv_1\n")
    (root / "libs.ref" / LIB / "spice").mkdir(parents=True, exist_ok=True)
    (root / "libs.ref" / LIB / "spice" / f"{LIB}.spice").write_text(
        f".subckt {LIB}__inv_1 A Y VDD VSS\n.ends\n")
    if config is not None:
        (root / "libs.tech" / "librelane").mkdir(parents=True, exist_ok=True)
        (root / "libs.tech" / "librelane" / "config.tcl").write_text(config)
    return root


def answer_tools(inner, top: str):
    """Wrap a test's `_docker_exec` double: answer the shipped-GDS arm's three
    sessions, hand every other command to `inner` unchanged."""

    def fake(container, cmd, timeout=0, **kw):
        if "openroad" in cmd and "write_powered_netlist.tcl" in cmd:
            tcl = Path(re.search(r"(\S+write_powered_netlist\.tcl)",
                                 cmd).group(1)).read_text()
            out = re.search(r"write_verilog -include_pwr_gnd \{(\S+)\}", tcl)
            Path(out.group(1)).write_text(
                f"module {top}(VDD, VSS);\ninout VDD;\ninout VSS;\nendmodule\n")
            return (0, "", "")
        if "magic" in cmd and "GDS=" in cmd:
            env = dict(re.findall(r"(\w+)='?([^' ]+)'?", cmd))
            gds = Path(env["GDS"]).read_bytes()
            Path(env["SPICE_OUT"]).write_text(
                f".subckt {top} VDD VSS\n* {'unlabelled' if UNLABELLED in gds else 'labelled'}"
                f"\n.ends\n")
            Path(env["FEEDBACK_OUT"]).write_text("")
            (Path(env["EXT_DIR"]) / "ext2spice.log").write_text(
                "MAGIC_EXT2SPICE_DONE\n")
            return (0, "MAGIC_EXT2SPICE_DONE\n", "")
        if "netgen -batch source" in cmd:
            env = dict(re.findall(r"(\w+)='?([^' ]+)'?", cmd))
            layout = Path(env["LAYOUT_SPICE"]).read_text()
            final = (PIN_FAIL.format(top=top) if "* unlabelled" in layout
                     else MATCH)
            Path(env["LVS_RPT"]).write_text(netgen_report(top, final))
            return (0, "", "")
        return inner(container, cmd, timeout=timeout, **kw)
    return fake


def shipped_gds_ready(monkeypatch, runner, project: Path, top: str, pdk,
                      tmp_path: Path, *, gds: bytes = b"labelled"):
    """Plant the shipped GDS, answer the arm's tools, and return `pdk` placed
    in a PDK tree that declares its cell models (the only field moved is the
    cell LEF, which is how the runner finds the PDK and its library)."""
    pnr = Path(project) / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / f"{top}.gds").write_bytes(gds)
    monkeypatch.setattr(runner, "_docker_exec",
                        answer_tools(runner._docker_exec, top))
    root = pdk_tree(tmp_path)
    return dataclasses.replace(
        pdk, cell_lef=str(root / "libs.ref" / LIB / "lef" / f"{LIB}.lef"))
