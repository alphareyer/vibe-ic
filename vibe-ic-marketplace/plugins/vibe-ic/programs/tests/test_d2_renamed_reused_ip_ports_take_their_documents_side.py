#!/usr/bin/env python3
"""D2: a reused IP's RENAMED ports take the side the document gave the family,
and the document's illustrative names get no pad.

MEASURED on subservient x gf180mcuD (8HD-4, v1.25.64 @76a277544): step 15.5ic
refused PORT_WITHOUT_A_SIDE for 36 nets, the synthesised core's REAL SRAM
ports (`o_sram_waddr`, `o_sram_wdata`, `o_sram_wen`, ...). The pad population
came from L9 top_ports, which is the document's ports UNION the staged top's.
The L3 illustrative names (`o_sram_data`, `o_sram_addr`, `o_sram_we`, ...)
carry `declared_by_staged_top: false`, are absent from the netlist, and were
the ones the group rows "SRAM data bus" / "SRAM addr + control(we / cyc)"
resolved to. The real ports have atoms (wdata, waddr, wen) no row contains.

Two halves, both load-bearing:
  (A) an L9 entry labelled `declared_by_staged_top: false` and absent from the
      selected netlist is a document port the core does not have: no pad,
      recorded with its evidence;
  (B) a hand-authored SOURCE_MANIFEST `renamed_interfaces` pair carries its
      L9 names' group side to its RTL names, through the SAME exact-atom rule.
Remove (A) and the illustrative names get phantom pads; remove (B) and the
groups resolve to nothing (PAD_GROUP_UNRESOLVED).

The refusals that stay are pinned too: a pair spanning two groups is
PORT_ON_TWO_SIDES, no pair is PAD_GROUP_UNRESOLVED, a port nothing names is
PORT_WITHOUT_A_SIDE, and step 2's budget reads the same renames and refuses
the same contradictions first. The LibreLane chip path (the chip default)
runs the core-connection backstop before pad_assignment_gen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(TESTS))

import _l_doc_pad_placement as LPP                      # noqa: E402
import test_io_pad_chip_top_gen as IO                    # noqa: E402

GEN = PROGRAMS / "io_pad_chip_top_gen.py"
CHIP_TOP_V = "phase3/stage3/pnr/chip_top_io.v"

DOC = """---
layer: L3
---

# L3 — External Interface

The I/O cell library is delegated to the PDK i/o pad defaults.

## Physical Pad Placement

| Pad side | signals |
|---|---|
| **North (N)** | memory data bus |
| **South (S)** | memory addr + control(we) |
| **East (E)** | `clk` / `rst` |
| **West (W)** | status pin(s) |
"""


def _p(name, direction, staged, width=1, **extra):
    port = {"name": name, "direction": direction, "width": width,
            "declared_by_staged_top": staged,
            "evidence": ("input/vendor_rtl/core.v" if staged
                         else "input/docs/L3_external_interface.md")}
    if width > 1:
        port.update(msb=width - 1, lsb=0)
    port.update(extra)
    return port


#: L9's reconciled list: the staged top's real ports, plus the document's
#: illustrative ones it labels `declared_by_staged_top: false`.
SPEC = {
    "top_module": "core",
    "top_ports": [
        _p("clk", "input", True), _p("rst", "input", True),
        _p("o_status", "output", True),
        _p("o_memory_wdata", "output", True, 2),
        _p("o_memory_waddr", "output", True),
        _p("o_memory_wen", "output", True),
        _p("o_memory_data", "output", False, 2),
        _p("o_memory_addr", "output", False),
        _p("o_memory_we", "output", False),
    ],
}

NETLIST = """module core(clk, rst, o_memory_wdata, o_memory_waddr, o_memory_wen, o_status);
  input clk;
  input rst;
  output [1:0] o_memory_wdata;
  output o_memory_waddr;
  output o_memory_wen;
  output o_status;
endmodule
"""

PAIRS = [{"l9": ["o_memory_data"], "rtl": ["o_memory_wdata"]},
         {"l9": ["o_memory_addr", "o_memory_we"],
          "rtl": ["o_memory_waddr", "o_memory_wen"]}]

DOC_ONLY = {"o_memory_data", "o_memory_addr", "o_memory_we"}


def _project(tmp_path, *, pairs=PAIRS, reused_ip=True, doc=DOC, spec=SPEC,
             netlist=NETLIST):
    proj = IO._project(tmp_path, doc=doc, spec=spec)
    synth = proj / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    (synth / "core_synth.v").write_text(netlist)
    if pairs is not None:
        rtl = proj / "phase2/stage1/rtl"
        rtl.mkdir(parents=True)
        (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(
            {"reused_ip": reused_ip, "renamed_interfaces": pairs}))
    return proj


def _gen(tmp_path, **kw):
    proj = _project(tmp_path, **kw)
    res = IO._run(GEN, proj, IO._pdk(tmp_path / "pdk"))
    return proj, res, res.stdout + res.stderr


def _refused(proj, res, out, rule):
    assert res.returncode == 1, out
    assert rule in out, out
    assert not (proj / CHIP_TOP_V).exists()


# --------------------------------------------------------------------------- #
# the measured shape: RED on main (PORT_WITHOUT_A_SIDE on the real ports)
# --------------------------------------------------------------------------- #
def test_renamed_ports_take_their_groups_side_and_doc_names_get_no_pad(tmp_path):
    proj, res, out = _gen(tmp_path)
    assert res.returncode == 0, out
    rec = IO._record(proj)
    sides = {k: set(v) for k, v in
             rec["derived_answers"]["pad_order_by_side"].items() if v}
    assert sides == {
        "north": {"u_pad_o_memory_wdata_1", "u_pad_o_memory_wdata_0"},
        "south": {"u_pad_o_memory_waddr", "u_pad_o_memory_wen"},
        "east": {"u_pad_clk", "u_pad_rst"},
        "west": {"u_pad_o_status"},
    }
    ports = {r["port"] for r in rec["pad_instances"].values()}
    assert not {p for p in ports if p.split("[")[0] in DOC_ONLY}
    wrapper = (proj / CHIP_TOP_V).read_text()
    for name in DOC_ONLY:
        assert f".{name}(" not in wrapper
    dropped = rec["doc_ports_not_implemented"]
    assert {d["name"] for d in dropped} == DOC_ONLY
    assert all(d["declared_by_staged_top"] is False and d["evidence"]
               and d["netlist"].endswith("core_synth.v") for d in dropped)
    via = {v["port"] for r in rec["pad_group_resolution"]
           for v in r["matched_via_rename"]}
    assert via == {"o_memory_wdata", "o_memory_waddr", "o_memory_wen"}


def test_the_wrapper_passes_the_runners_core_connection_backstop(tmp_path):
    import phase3_one_shot_runner as R
    proj, res, out = _gen(tmp_path)
    assert res.returncode == 0, out
    R._validate_padring_core_connections(
        proj / "phase2/stage2/synth/core_synth.v", proj / CHIP_TOP_V,
        "core", IO._record(proj).get("chip_top_module") or "chip_top")


# --------------------------------------------------------------------------- #
# the refusals that stay
# --------------------------------------------------------------------------- #
def test_a_pair_spanning_two_groups_is_on_two_sides(tmp_path):
    pairs = [{"l9": ["o_memory_data", "o_memory_addr"],
              "rtl": ["o_memory_wdata", "o_memory_waddr"]},
             {"l9": ["o_memory_we"], "rtl": ["o_memory_wen"]}]
    _refused(*_gen(tmp_path, pairs=pairs), "PORT_ON_TWO_SIDES")


@pytest.mark.parametrize("kw", [{"pairs": []}, {"pairs": None},
                                {"reused_ip": False}],
                         ids=["no-pair", "no-manifest", "not-reused-ip"])
def test_without_a_declared_rename_the_groups_are_unresolved(tmp_path, kw):
    _refused(*_gen(tmp_path, **kw), "PAD_GROUP_UNRESOLVED")


def test_an_implemented_port_nothing_names_has_no_side(tmp_path):
    spec = dict(SPEC, top_ports=SPEC["top_ports"] + [
        _p("o_memory_ren", "output", True)])
    netlist = NETLIST.replace("o_status);", "o_status, o_memory_ren);") \
        .replace("endmodule", "  output o_memory_ren;\nendmodule")
    _refused(*_gen(tmp_path, spec=spec, netlist=netlist),
             "PORT_WITHOUT_A_SIDE")


def test_an_exact_row_naming_a_dropped_doc_port_is_refused(tmp_path):
    """Renames carry sides through GROUP rows only (a deliberate choice: a
    per-bit mapping between an illustrative bus and its renamed parts is not
    stated anywhere). An exact row that names a doc-only port the core lacks
    is refused, never silently re-pointed."""
    doc = DOC.replace("| **North (N)** | memory data bus |",
                      "| **North (N)** | `o_memory_data[1:0]` |")
    _refused(*_gen(tmp_path, doc=doc), "PORT_WITHOUT_A_SIDE")


def test_an_unlabelled_or_staged_absent_port_is_never_dropped(tmp_path):
    """(A) reads L9's label, not absence alone: only `declared_by_staged_top
    is False` (or `optional is True`) AND absent is dropped."""
    import phase3_one_shot_runner as R
    import io_pad_chip_top_gen as G
    proj = _project(tmp_path)
    ports = [{"name": "clk"},
             {"name": "i_gone_unlabelled"},
             {"name": "i_gone_staged", "declared_by_staged_top": True},
             {"name": "i_gone_truthy", "declared_by_staged_top": 0},
             {"name": "i_gone_doc", "declared_by_staged_top": False}]
    orig = R.pnr_input_netlist
    try:
        R.pnr_input_netlist = lambda p, c: (
            proj / "phase2/stage2/synth/core_synth.v", "t", False)
        kept, dropped = G._drop_unimplemented_optional_ports(proj, ports)
    finally:
        R.pnr_input_netlist = orig
    assert [p["name"] for p in kept] == ["clk", "i_gone_unlabelled",
                                         "i_gone_staged", "i_gone_truthy"]
    assert [d["name"] for d in dropped] == ["i_gone_doc"]


def test_an_all_carrier_word_l9_name_carries_no_side():
    """`o_bus` has no semantic atom. The empty set is a subset of every
    statement, so without the non-empty guard its RTL names would land on
    every group side."""
    placement = LPP.parse_pad_placement(DOC, "L3.md")
    ports = [{"name": "o_memory_wdata", "width": 2}]
    grouped, records = LPP.resolve_declared_pad_groups(
        placement, ports, renames=[({"o_bus"}, {"o_memory_wdata"})])
    assert grouped == {}
    assert all(r["matched_via_rename"] == [] for r in records)


# --------------------------------------------------------------------------- #
# step 2 reads the same renames and refuses the same contradictions first
# --------------------------------------------------------------------------- #
def test_step2_and_the_ring_read_one_rename_list(tmp_path):
    proj = _project(tmp_path)
    ring = LPP.derive_own_ring(proj, [
        {"name": "clk"}, {"name": "rst"}, {"name": "o_status"},
        {"name": "o_memory_wdata", "width": 2},
        {"name": "o_memory_waddr"}, {"name": "o_memory_wen"}])
    assert ring["measurable"] and ring["groups_unresolved"] == []
    assert ring["by_side"]["N"] == ["o_memory_wdata[1]", "o_memory_wdata[0]"]
    assert ring["by_side"]["S"] == ["o_memory_waddr", "o_memory_wen"]
    assert ring["nets_on_two_sides"] == []
    assert ring["renamed_interfaces"] == [
        {"l9": sorted(p["l9"]), "rtl": sorted(p["rtl"])} for p in PAIRS]


_STEP2_L3 = """# L3 external interface

The I/O cell library is delegated to the PDK.

## Physical Pad Placement

| side | signals |
|---|---|
| E | `i_clk`, `i_rst` |
| N | sensor data bus |
| S | sensor addr + control (we) |
| W | gpio pin |
"""

_STEP2_RTL = ("module widget (input wire i_clk, input wire i_rst,\n"
              "  output wire [1:0] o_sensor_waddr, output wire [1:0] o_sensor_wdata,\n"
              "  output wire o_sensor_wen, output wire o_gpio);\n"
              "endmodule\n")

_STEP2_PAIRS = [{"l9": ["o_sensor_data"], "rtl": ["o_sensor_wdata"]},
                {"l9": ["o_sensor_addr", "o_sensor_we"],
                 "rtl": ["o_sensor_waddr", "o_sensor_wen"]}]


def _step2(pairs):
    import test_r0915101_a_die_budgets_against_its_own_pad_ring as R0915
    proj = R0915._project(rtl=_STEP2_RTL, l3=_STEP2_L3)
    if pairs is not None:
        (proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json").write_text(
            json.dumps({"reused_ip": True, "renamed_interfaces": pairs}))
    return R0915._run(proj)


def test_step2_fits_through_a_declared_rename():
    rc, rep = _step2(_STEP2_PAIRS)
    assert (rc, rep["verdict"]) == (0, "FITS"), rep.get("reason")
    assert rep["derived_ring"]["groups_unresolved"] == []


def test_step2_names_the_unresolved_group_and_the_manifest_remedy():
    rc, rep = _step2(None)
    assert (rc, rep["verdict"]) == (1, "DOES_NOT_FIT")
    assert set(rep["derived_ring"]["groups_unresolved"]) == {"N", "S"}
    assert "renamed_interfaces" in rep["reason"]


def test_step2_refuses_a_rename_that_spans_two_groups():
    pairs = [{"l9": ["o_sensor_data", "o_sensor_addr"],
              "rtl": ["o_sensor_wdata", "o_sensor_waddr"]},
             {"l9": ["o_sensor_we"], "rtl": ["o_sensor_wen"]}]
    rc, rep = _step2(pairs)
    assert (rc, rep["verdict"]) == (1, "DOES_NOT_FIT")
    assert "o_sensor_wdata[1]" in rep["derived_ring"]["nets_on_two_sides"]
    assert "PORT_ON_TWO_SIDES" in rep["reason"]


# --------------------------------------------------------------------------- #
# the LibreLane chip path (the chip default) runs the core-connection
# backstop before its PAD_* assignment reaches the tool
# --------------------------------------------------------------------------- #
_CORE = "module core(a);\n  input a;\nendmodule\n"


@pytest.mark.parametrize("wrapper_text,finding,unknown", [
    (_CORE + "module chip_top(a, o_phantom);\n  input a;\n  output o_phantom;\n"
     "  core u_core (.a(a), .o_phantom(o_phantom));\nendmodule\n",
     "PADRING_CORE_PORT_CONNECTION_MISMATCH", "unknown=['o_phantom']"),
    (_CORE + "module chip_top(a);\n  input a;\n  core u_core (.a(a));\n"
     "endmodule\n", None, None),
], ids=["phantom-pad", "matching-wrapper"])
def test_the_librelane_chip_path_refuses_a_phantom_pad_before_assignment(
        tmp_path, monkeypatch, wrapper_text, finding, unknown):
    import phase3_one_shot_runner as R
    import librelane_contract as contract
    from _stated_eda_image import state_the_image
    from types import SimpleNamespace
    monkeypatch.delenv("VIBEIC_LIBRELANE_IMAGE", raising=False)
    monkeypatch.delenv("VIBEIC_LIBRELANE_PDK_ROOT", raising=False)
    state_the_image(monkeypatch)
    project = tmp_path / "project"
    out_dir = project / "phase3/stage3/pnr"
    out_dir.mkdir(parents=True)
    wrapper = out_dir / "chip_top_io.v"
    wrapper.write_text(wrapper_text)
    (project / "phase3/librelane_switch.json").write_text(
        json.dumps({"pdk_root_host": str(tmp_path)}))
    monkeypatch.setattr(R, "_padring_chip_top_record", lambda p: {
        "core_module": "core", "chip_top_module": "chip_top",
        "chip_top_verilog": str(wrapper.relative_to(project))})
    monkeypatch.setattr(R, "pnr_input_netlist",
                        lambda p, core: (wrapper, "n", False))
    monkeypatch.setattr(R, "_padring_pdk_root_and_tree",
                        lambda pdk, c: ("/pdk", "probe_pdk"))
    execs = []

    def _exec(container, cmd, **k):
        execs.append(cmd)
        return 1, "", "stop here: the backstop has already been passed"
    monkeypatch.setattr(R, "_docker_exec", _exec)
    monkeypatch.setattr(contract, "run_chain", lambda *a, **k: pytest.fail(
        "the tool chain must not run"))
    pdk = SimpleNamespace(name="probe_pdk")
    result, consumer = R._prepare_librelane_floorplan_for_route(
        project, pdk, "c", out_dir, "", {"15": "direct", "15.5ic": "librelane"},
        io_view_discover=lambda *a: ([], []))
    assert result.status == "FAIL" and consumer is None
    if finding is None:
        # the backstop passed: the next step (pad_assignment_gen) was reached
        assert result.extras["finding"] == "LL_PAD_ASSIGNMENT_FAILED"
        assert len(execs) == 1 and "pad_assignment_gen.py" in execs[0]
    else:
        assert result.extras["finding"] == finding, result.detail
        assert result.detail.startswith(finding) and unknown in result.detail
        assert execs == []
