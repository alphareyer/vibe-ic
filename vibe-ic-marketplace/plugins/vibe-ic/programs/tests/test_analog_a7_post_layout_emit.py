"""A7 post-layout producer: LibreLane Magic.RCX + A4's ngspice resim (T94).

A7 had no deterministic producer (the runner WAIVED it to a skill). The
producer is `analog_a7_post_layout_emit`; MEASURED on vibeic-eda 0.3.77 /
u_hawaii_adc/ldo it extracts five ngspice styles at depth RC (269..1158 R,
85..123 C) and the A7 gate certifies the comparison it writes.

The end-to-end tests drive the shipped `run()` — the real
`librelane_contract.resolve_step_config` / `run_chain`, the real
`magic_extract_spice_emit` audit and A4's real `_run_ngspice` — against a stub
`docker` that only plays the TOOLS: it writes what `librelane.steps run
--id Magic.RCX` writes (state_out.json + the SPICE_RCX file) and prints the
measurement line ngspice prints. Nothing inside vibe-ic is monkeypatched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _eda_pin as PIN
import analog_a7_post_layout_emit as A7

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))
IMAGE = "ghcr.io/vibeic/vibeic-eda@" + PIN.IMAGE_DIGEST

RCX_RC = """* NGSPICE file created from blk.ext - technology: t
.subckt blk a.n1 b vss a x.t1
X0 a.n1 x.t1 vss vss nfet w=1u l=1u
R0 a a.n1 12.5
R1 x.t1 b 3.0
C0 a.n1 vss 1.2f
C1 b vss 0.4f
.ends
"""
RCX_NONE = """* NGSPICE file created from blk.ext - technology: t
.subckt blk a b vss
X0 a b vss vss nfet w=1u l=1u
.ends
"""

TECH = """tech
  format 35
  t
end
include t-extract
"""
TECH_EXTRACT = """extract
 style ngspice variants (),(lvs),(hrhc)
 cscale 1
end
"""


# ── pure helpers ───────────────────────────────────────────────────────────
def test_styles_come_from_the_technology_and_skip_the_device_only_variant(
        tmp_path):
    (tmp_path / "t.tech").write_text(TECH)
    (tmp_path / "t-extract.tech").write_text(TECH_EXTRACT)
    assert A7.extraction_styles(tmp_path / "t.tech") == [
        "ngspice()", "ngspice(hrhc)"]
    assert A7.extraction_styles(tmp_path / "absent.tech") == []


def test_the_wrapper_keeps_the_declared_port_order_and_splits_no_node():
    text, mapping = A7.post_layout_netlist(
        "blk", ["a", "b", "vss"], RCX_RC, [".lib ../m.lib tt"])
    assert ".subckt blk__rcx a.n1 b vss a x.t1" in text
    assert ".subckt blk a b vss" in text
    # a split node Magic exported is private, never shorted onto its net
    assert mapping["a.n1"] != "a" and mapping["x.t1"].startswith("rcx_int_")
    assert "xrcx rcx_int_0 b vss a rcx_int_4 blk__rcx" in text
    assert text.index(".lib ../m.lib tt") < text.index(".subckt blk__rcx")


def test_a_declared_port_the_extraction_does_not_expose_is_refused():
    with pytest.raises(ValueError, match="A7_RCX_PORT_MISSING"):
        A7.post_layout_netlist("blk", ["a", "b", "vdd"], RCX_RC, [])


def test_the_testbench_must_include_the_block_netlist_exactly_once(tmp_path):
    tb = ".include blk.sp\n.lib ../../m.lib tt\nX1 a b 0 blk\n.end\n"
    out = A7.post_layout_testbench(tb, "blk", "blk_post.sp", tmp_path,
                                   tmp_path / "post_layout")
    assert ".include blk_post.sp" in out
    assert ".lib ../../../m.lib tt" in out
    for bad in ("X1 a b 0 blk\n", ".include blk.sp\n.include blk.sp\n"):
        with pytest.raises(ValueError, match="A7_TB_INCLUDE_AMBIGUOUS"):
            A7.post_layout_testbench(bad, "blk", "p.sp", tmp_path, tmp_path)


# ── the producer, end to end, against a stub docker ───────────────────────
STUB = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
a = sys.argv[1:]
j = " ".join(a)
if a[0] == "inspect":
    if "Mounts" in j:
        print(json.dumps([{"Type": "bind", "Source": os.environ["STUB_ROOT"],
                           "Destination": os.environ["STUB_ROOT"]}]))
    else:
        print("sha256:aaaa\trepo@" + os.environ["STUB_DIGEST"])
    sys.exit(0)
if a[0] == "run":
    if "--help" in j:                                   # image capability
        sys.exit(0)
    if "-c" in a and "Config.load" in a[-1]:            # resolve_step_config
        src = re.search(r"p='([^']+)'", a[-1]).group(1)
        out = re.search(r"out='([^']+)'", a[-1]).group(1)
        cfg = json.load(open(src))
        cfg["MAGIC_TECH"] = os.environ["STUB_TECH"]
        open(out, "w").write(json.dumps(cfg))
        sys.exit(0)
    if "librelane.steps" in a:                           # the Magic.RCX step
        folder = a[a.index("-o") + 1]
        state_in = json.load(open(a[a.index("-i") + 1]))
        os.makedirs(folder, exist_ok=True)
        rcx = os.path.join(folder, "blk.rcx.spice")
        open(rcx, "w").write(open(os.environ["STUB_RCX"]).read())
        state_in["spice_rcx"] = rcx
        open(os.path.join(folder, "state_out.json"), "w").write(
            json.dumps(state_in))
        sys.exit(0)
    sys.exit(1)
if a[0] == "exec":
    cmd = a[-1]
    if "--json-measure" in cmd:
        print("unrecognized option"); sys.exit(0)
    if "command -v ngspice" in cmd:
        print("/foss/tools/bin/ngspice"); sys.exit(0)
    if " -b " in cmd:
        deck = cmd.split()[-2].strip("'")
        print("MEAS vout= " + ("1.19" if "_post_" in deck else "1.20"))
        sys.exit(0)
    sys.exit(subprocess.run(["bash", "-c", cmd]).returncode)
sys.exit(0)
'''


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    b = project / "phase3" / "analog" / "blk"
    b.mkdir(parents=True)
    tech_dir = tmp_path / "pdkroot" / "tpdk" / "libs.tech" / "magic"
    tech_dir.mkdir(parents=True)
    (tech_dir / "t.tech").write_text(TECH)
    (tech_dir / "t-extract.tech").write_text(TECH_EXTRACT)
    (b / "blk.gds").write_bytes(b"\x00\x06\x00\x02\x02\x58")
    (b / "layout_provenance.json").write_text(json.dumps(
        {"pdk_sources": {"magic_tech": str(tech_dir / "t.tech")}}))
    (b / "blk.sp").write_text(
        ".lib ../../../models/m.lib tt\n.subckt blk a b vss\n"
        "X0 a b vss vss nfet w=1u l=1u\n.ends blk\n")
    (b / "tb_blk.sp").write_text(
        ".include blk.sp\nV1 a 0 1.8\nX1 a b 0 blk\n.op\n.end\n")
    (b / "corner_results.json").write_text(json.dumps(
        {"design_content": "structure_and_geometry",
         "corners": [{"simulator_run": True}]}))
    (project / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    return project


@pytest.fixture
def stub(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "docker").write_text(STUB)
    (d / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_ROOT", str(tmp_path))
    monkeypatch.setenv("STUB_DIGEST", PIN.IMAGE_DIGEST)
    monkeypatch.setenv("STUB_TECH",
                       str(tmp_path / "pdkroot/tpdk/libs.tech/magic/t.tech"))
    monkeypatch.setenv("VIBEIC_DESIGNS_HOST_ROOT", str(tmp_path))
    import analog_real_corner_sweep as ARS
    ARS._NGSPICE_CACHE.clear()
    ARS._JSON_MEASURE_SUPPORT.clear()
    ARS._CONTAINER_PATH_CACHE.clear()
    return tmp_path


def _rcx(tmp_path, text):
    p = tmp_path / "rcx.spice"
    p.write_text(text)
    os.environ["STUB_RCX"] = str(p)


def test_the_producer_writes_a_comparison_the_a7_gate_certifies(stub):
    project = _project(stub)
    _rcx(stub, RCX_RC)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    bdir = project / "phase3/analog/blk"
    doc = json.loads((bdir / "pre_vs_post.json").read_text())
    assert [s["name"] for s in doc["specs"]] == ["vout@ngspice()",
                                                 "vout@ngspice(hrhc)"]
    assert all((s["pre_value"], s["post_value"]) == (1.20, 1.19)
               for s in doc["specs"])
    assert (project / doc["_provenance"]["extracted_netlist"]).is_file()
    rec = json.loads((bdir / "a7_post_layout.json").read_text())
    assert [c["depth"] for c in rec["corners"]] == ["RC", "RC"]
    # every extraction ran through the contract, one step dir per style
    assert len(list((project / "phase3/librelane/analog/blk").glob(
        "a7_*/01-magic-rcx/vibeic_receipt.json"))) == 2
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS / "analog_a7_post_layout_resim_check.py"),
                         str(project), "--block", "blk"],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_a_parasitic_free_extraction_is_refused_and_writes_no_comparison(
        stub):
    project = _project(stub)
    _rcx(stub, RCX_NONE)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 1
    bdir = project / "phase3/analog/blk"
    assert not (bdir / "pre_vs_post.json").exists()
    rec = json.loads((bdir / "a7_post_layout.json").read_text())
    assert rec["rule"] == "A7_RCX_PARASITIC_FREE"


def test_an_absent_layout_is_an_honest_gap(stub):
    project = _project(stub)
    (project / "phase3/analog/blk/blk.gds").unlink()
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 2


def test_the_runner_invokes_the_producer_only_when_the_switch_selects_it(
        tmp_path, monkeypatch):
    import analog_one_shot_runner as R
    project = _project(tmp_path)
    calls = []
    real_run = R._pr.run

    class _CP:
        returncode, stdout, stderr = 2, "", "HONEST_GAP: x"

    def fake_run(cmd, *a, **k):
        if any(str(x).endswith("analog_a7_post_layout_emit.py") for x in cmd):
            calls.append(cmd)
            return _CP()
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(R._pr, "run", fake_run)
    monkeypatch.setattr(R._pin, "container_image_digest",
                        lambda c: (PIN.IMAGE_DIGEST, ""))
    R.step_for_block(project, {"name": "blk", "type": "ldo"},
                     "A7_post_layout_resim", None)
    assert calls == []
    (project / "phase3/librelane_switch.json").write_text(
        json.dumps({"steps": {"A7": "librelane"}}))
    R.step_for_block(project, {"name": "blk", "type": "ldo"},
                     "A7_post_layout_resim", None)
    assert len(calls) == 1
    assert calls[0][calls[0].index("--image") + 1].endswith(PIN.IMAGE_DIGEST)
    assert "@" in calls[0][calls[0].index("--image") + 1]
