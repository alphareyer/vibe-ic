"""PDK-evidenced ring source closure; no IC or PDK names in the planner."""
from __future__ import annotations

import sys
import inspect
import os
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import io_pad_chip_top_gen as G  # noqa: E402
import _pad_ring as PR  # noqa: E402


def test_connector_requires_pdk_verilog_lef_and_empty_cdl():
    classes = {"io__bridge": "PAD SPACER", "io__active": "PAD AREAIO"}
    roles = {m: {p: ("INOUT", "POWER") for p in ("RA", "RB", "RC", "RD")}
             for m in classes}
    netlists = {"io__bridge": (["RA", "RC", "RD"], []),
                "io__active": (["RA", "RB"], [["R1", "RA", "RB"]])}
    verilog = """module io__bridge(RA, RB, RC, RD);
      inout RA, RB, RC, RD;
      assign RB = RA;
      assign RD = RC;
    endmodule
    module io__active(RA, RB);
      inout RA, RB;
      assign RB = RA;
    endmodule"""
    assert G._pdk_connector_cells(classes, roles, netlists, [verilog]) == {
        "io__bridge": [("RB", "RA"), ("RD", "RC")]}
    roles["io__bridge"]["RB"] = ("INOUT", "GROUND")
    assert G._pdk_connector_cells(classes, roles, netlists, [verilog]) == {
        "io__bridge": [("RD", "RC")]}


def test_minimum_bond_pads_with_documented_connector_slices():
    pads, connectors, reached = G._minimum_ring_sources(
        {"CORE", "GROUND", "IO", "IOG", "QUIET", "QUIETG",
         "KEEP", "SW", "ANALOG", "ANALOGG"},
        {"CORE", "GROUND"},
        {"io_power": {"IO", "QUIET"},
         "io_ground": {"IOG", "QUIETG"},
         "analog_power": {"ANALOG"},
         "analog_ground": {"ANALOGG"}},
        {"keep_switch": [("KEEP", "CORE"), ("SW", "IO")],
         "analog_bridge": [("ANALOG", "IO"), ("ANALOGG", "IOG")]})
    assert pads == ["io_ground", "io_power"]
    assert connectors == ["analog_bridge", "keep_switch"]
    assert reached == {"CORE", "GROUND", "IO", "IOG", "QUIET",
                       "QUIETG", "KEEP", "SW", "ANALOG", "ANALOGG"}
    no_connector = G._minimum_ring_sources(
        {"CORE", "IO", "KEEP"}, {"CORE"},
        {"io_power": {"IO"}}, {})
    assert "KEEP" not in no_connector[2]


def test_wrapper_keeps_connector_rails_on_distinct_named_nets():
    chosen = {"u_connector": {
        "port": "io__bridge", "master": "io__bridge",
        "supply_connections": {"RA": "VPWR", "RB": "KEEP"},
        "is_ring_connector": True}}
    ordered = {s: ["u_connector"] if s == "S" else [] for s in G.SIDES}
    wrapper = G._emit_verilog("top", "core", ordered, chosen, [], ["VPWR"])
    assert "wire KEEP;" in wrapper
    assert ".RA(VPWR)" in wrapper and ".RB(KEEP)" in wrapper
    assert "inout KEEP" not in wrapper


def test_installed_io_pdk_has_complete_minimal_ring_sources():
    """The installed PDK is the authority; enable in an EDA image."""
    root = os.environ.get("VIBEIC_TEST_PDK_ROOT")
    if not root:
        pytest.skip("requires VIBEIC_TEST_PDK_ROOT in the EDA image")
    pdk = os.environ.get("VIBEIC_TEST_PDK", "sky130A")
    lefs = PR.discover_io_lefs(root, pdk)
    assert lefs
    classes, sizes, roles, sources = {}, {}, {}, {}
    for path in lefs:
        view = path.read_text(errors="replace")
        for master, cls in PR.parse_lef_macro_classes(view).items():
            classes[master] = cls
            sources.setdefault(master, []).append(path)
        sizes.update(PR.parse_lef_macros(view))
        roles.update(PR.parse_lef_pin_roles(view))
    prefix = os.environ.get("VIBEIC_TEST_IO_PREFIX", "sky130_ef_io__")
    terminals = PR.io_terminals(
        PR.discover_io_library_configs(root, pdk), prefix)
    resolver = getattr(G, "resolve_supply_pad_pair", None)
    if resolver is None:
        # The same-domain-only main arm gives its substantive refusal.
        G._derive_supply_pad_pair(classes, sizes, roles, prefix,
                                  "VPWR", "VGND", sources)
        pytest.fail("main arm unexpectedly resolved the multi-rail ring")
    core = (Path(root) / pdk / "libs.ref" / "sky130_fd_sc_hd" /
            "lib" / "sky130_fd_sc_hd__tt_025C_1v80.lib")
    kwargs = dict(
        liberty_texts=lambda: [p.read_text(errors="replace") for p in
                               PR.discover_io_liberty(root, pdk)],
        netlist_texts=lambda: [p.read_text(errors="replace") for p in
                               PR.discover_io_netlists(root, pdk)],
        core_liberty_text=core.read_text(errors="replace"))
    if "connector_verilog_texts" in inspect.signature(resolver).parameters:
        kwargs["connector_verilog_texts"] = lambda: [
            p.read_text(errors="replace") for p in
            PR.discover_io_verilog(root, pdk)]
    pair, plan, _ = resolver(classes, sizes, roles, prefix,
                             "VPWR", "VGND", sources, terminals, **kwargs)
    missing = plan["ring_rails_without_bond"]
    assert missing == [], f"observed unsourced PDK rails: {missing}"
    assert len(pair) == 2
    assert len(plan["supplemental_supply_pads"]) == 2
    assert len(plan["ring_connector_cells"]) == 2
    G.require_bonded_ring_rails(plan)
