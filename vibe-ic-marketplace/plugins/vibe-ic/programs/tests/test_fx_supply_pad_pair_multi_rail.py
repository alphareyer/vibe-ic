"""A supply-pad pair for IO libraries whose supply cells carry SEVERAL rails.

MEASURED (sha256 x sky130A, front door, phase 3): `pad_ring_gen` refused
SUPPLY_PAD_PAIR_UNRESOLVED -- every sky130 `sky130_ef_io` supply cell carries
the whole ring (VCCD, VCCHIB, VDDA, VDDIO, VDDIO_Q, VSWITCH / VSSA, VSSD,
VSSIO, VSSIO_Q) plus its bond terminal, so "the one non-core POWER terminal"
cannot exist. The same rule refuses ihp-sg13g2 (every supply cell carries
vdd/vss/iovdd/iovss); gf180mcu resolves (dvdd omits the core VDD).

The fallback reads the PDK only:
  bond terminal   PAD_PLACE_IO_TERMINALS, else Liberty `is_pad`
  fed rail        the bond itself when it is a pg_pin; else the cell port the
                  PDK netlist joins to it through one series resistor
  core power      a fed rail characterised at the core voltage the active
                  standard-cell Liberty states
  core ground     the related_ground_pin the IO library pairs with it
and refuses, naming the candidates, whenever a polarity has zero or several.

The fixtures TRANSCRIBE each PDK's measured structure (pin USE, is_pad,
voltage_map, the netlist's series resistor, the declared terminals) and go
through the module's own LEF / Liberty / netlist parsers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _pad_ring as PR                  # noqa: E402
import io_pad_chip_top_gen as G         # noqa: E402

RING_P = ["VCCD", "VCCHIB", "VDDA", "VDDIO", "VDDIO_Q", "VSWITCH"]
RING_G = ["VSSA", "VSSD", "VSSIO", "VSSIO_Q"]
VOLTS = {"VCCD": 1.8, "VCCHIB": 1.8, "VDDA": 3.3, "VDDIO": 3.3,
         "VDDIO_Q": 3.3, "VSWITCH": 3.3, "VSSA": 0.0, "VSSD": 0.0,
         "VSSIO": 0.0, "VSSIO_Q": 0.0}


def _lef(macros: dict) -> str:
    out = []
    for name, pins in macros.items():
        out.append(f"MACRO {name}\n  CLASS PAD POWER ;\n  SIZE 75 BY 200 ;")
        for pin, use in pins.items():
            out.append(f"  PIN {pin}\n    DIRECTION INOUT ;\n    USE {use} ;\n"
                       f"    PORT\n      LAYER m5 ;\n        RECT 0 0 1 1 ;\n"
                       f"    END\n  END {pin}")
        out.append(f"END {name}")
    return "\n".join(out) + "\n"


def _liberty(volts: dict, cells: dict, nom=None) -> str:
    out = ["library (io) {"]
    if nom is not None:
        out.append(f"  nom_voltage : {nom};")
    out += [f'  voltage_map ("{n}", {v});' for n, v in volts.items()]
    for cell, spec in cells.items():
        out.append(f'  cell ("{cell}") {{')
        for pg, attrs in spec.get("pg_pins", {}).items():
            out.append(f'    pg_pin ("{pg}") {{')
            out += [f'      {k} : "{v}";' for k, v in attrs.items()]
            out.append("    }")
        for pin, attrs in spec.get("pins", {}).items():
            out.append(f'    pin ("{pin}") {{')
            out += [f'      {k} : "{v}";' for k, v in attrs.items()]
            out.append("    }")
        out.append("  }")
    out.append("}")
    return "\n".join(out) + "\n"


def _inputs(lef_text: str):
    classes = PR.parse_lef_macro_classes(lef_text)
    sizes = PR.parse_lef_macros(lef_text)
    roles = PR.parse_lef_pin_roles(lef_text)
    return classes, sizes, roles


def _resolve(lef_text, prefix, power, ground, terminals, libs, netlists,
             core):
    classes, sizes, roles = _inputs(lef_text)
    resolve = getattr(G, "resolve_supply_pad_pair", None)
    if resolve is None:
        # The tree before the fix: the same-domain rule is all it has, so it
        # answers this question (with a refusal) instead of being skipped.
        pair, plan = G._derive_supply_pad_pair(
            classes, sizes, roles, prefix, power, ground, {})
        return pair, plan, None
    return resolve(
        classes, sizes, roles, prefix, power, ground, {}, terminals,
        liberty_texts=libs, netlist_texts=netlists, core_liberty_text=core)


# ── sky130A-shaped library (measured structure, transcribed) ──────────────
def _sky_cell(bond: str, use: str) -> dict:
    pins = {bond: use}
    pins.update({p: "POWER" for p in RING_P})
    pins.update({g: "GROUND" for g in RING_G})
    return pins


SKY_MACROS = {
    "lib__vccd_lvc_clamped_pad": _sky_cell("VCCD_PAD", "POWER"),
    "lib__vssd_lvc_clamped_pad": _sky_cell("VSSD_PAD", "GROUND"),
    "lib__vddio_hvc_clamped_pad": _sky_cell("VDDIO_PAD", "POWER"),
    "lib__vssio_hvc_clamped_pad": _sky_cell("VSSIO_PAD", "GROUND"),
    # not declared by the PDK's terminal list: must not be a candidate
    "lib__vccd_hvc_pad": _sky_cell("VCCD_PAD", "POWER"),
}
SKY_FEEDS = {"lib__vccd_lvc_clamped_pad": ("VCCD_PAD", "VCCD", "P"),
             "lib__vssd_lvc_clamped_pad": ("VSSD_PAD", "VSSD", "G"),
             "lib__vddio_hvc_clamped_pad": ("VDDIO_PAD", "VDDIO", "P"),
             "lib__vssio_hvc_clamped_pad": ("VSSIO_PAD", "VSSIO", "G"),
             "lib__vccd_hvc_pad": ("VCCD_PAD", "VCCD", "P")}
SKY_TERMINALS = {m: SKY_FEEDS[m][0] for m in list(SKY_FEEDS)[:4]}


def _sky_netlist(feeds=SKY_FEEDS) -> str:
    """Each wrapper instantiates a base cell whose bond port reaches its core
    port through one series resistor -- the measured sky130 shape."""
    out = []
    for kind in ("P", "G"):
        out.append(f".SUBCKT base_{kind} {kind}_CORE {kind}_PAD ISO\n"
                   f"RI21 {kind}_PAD {kind}_CORE rmodel w=1 l=1\n"
                   f"RIL {kind}_CORE ISO rmodel w=1 l=1\n.ENDS")
    for cell, (bond, core, kind) in feeds.items():
        ports = " ".join([bond] + RING_P + RING_G)
        out.append(f".SUBCKT {cell} {ports}\nX0 {core} {bond} ISOL "
                   f"base_{kind}\n.ENDS")
    return "\n".join(out) + "\n"


def _sky_liberty(feeds=SKY_FEEDS, volts=VOLTS) -> str:
    cells = {}
    for cell, (bond, _core, _k) in feeds.items():
        cells[cell] = {
            "pg_pins": {r: {"pg_type": "primary_power" if r in RING_P
                            else "primary_ground", "voltage_name": r}
                        for r in RING_P + RING_G},
            "pins": {bond: {"direction": "inout", "is_pad": "true",
                            "related_power_pin": "VDDIO",
                            "related_ground_pin": "VSSD"}}}
    # a signal cell: the library pairs VCCD with VSSD (core controls) and
    # the high-voltage controls with VDDIO/VSSIO
    cells["lib__gpio_pad"] = {"pins": {
        "SLOW": {"direction": "input", "related_power_pin": "VCCD",
                 "related_ground_pin": "VSSD"},
        "HLD_H_N": {"direction": "input", "related_power_pin": "VDDIO",
                    "related_ground_pin": "VSSIO"},
        "PAD": {"direction": "inout", "is_pad": "true",
                "related_power_pin": "VDDIO", "related_ground_pin": "VSSIO"}}}
    return _liberty(volts, cells)


SKY_CORE = 'library (std) {\n  voltage_map ("VPWR", 1.8);\n}\n'


def test_sky130_shape_resolves_the_core_rail_bridge_pair():
    """RED on main (the same-domain rule is all there is): REFUSE."""
    pair, plan, core = _resolve(_lef(SKY_MACROS), "lib__", "VPWR", "VGND",
                                SKY_TERMINALS, [_sky_liberty()],
                                [_sky_netlist()], SKY_CORE)
    by = {e["kind"]: e for e in pair}
    assert by["power"]["master"] == "lib__vccd_lvc_clamped_pad"
    assert by["power"]["terminal"] == "VCCD_PAD"
    assert by["power"]["supply_connections"] == {
        "VCCD": "VPWR", "VCCD_PAD": "VPWR", "VSSD": "VGND"}
    assert by["ground"]["master"] == "lib__vssd_lvc_clamped_pad"
    assert by["ground"]["supply_connections"] == {
        "VCCD": "VPWR", "VSSD": "VGND", "VSSD_PAD": "VGND"}
    assert plan["core_rails_on_ring"] == {"power": "VCCD", "ground": "VSSD",
                                          "core_voltage": 1.8}
    # the high-voltage ring rails are NOT tied to the core rail
    assert "VDDIO" in plan["ring_rails_unbound"]["lib__vccd_lvc_clamped_pad"]
    assert plan["domain_topology"] == "single_domain"
    assert core["volts"] == 1.8 and core["basis"] == "voltage_map(VPWR)"


def test_unbonded_ring_rails_are_a_named_refusal_not_an_emitted_die():
    """The core pair alone is insufficient: each remaining PDK ring rail
    needs a bond-reachable pad or an explicit connector declaration."""
    _pair, plan, _core = _resolve(
        _lef(SKY_MACROS), "lib__", "VPWR", "VGND", SKY_TERMINALS,
        [_sky_liberty()], [_sky_netlist()], SKY_CORE)
    validate = getattr(G, "require_bonded_ring_rails", lambda _plan: None)
    with pytest.raises(G.Refusal) as exc:
        validate(plan)
    assert exc.value.rule == "RING_RAIL_BOND_UNDECLARED"
    assert "VCCHIB" in exc.value.message
    assert "VSWITCH" in exc.value.message


def test_ihp_shape_resolves_from_the_bonded_pg_pin():
    """RED on main: every cell carries vdd AND iovdd, so the same-domain rule
    finds two non-core POWER terminals."""
    pins = {"iovdd": "POWER", "iovss": "GROUND", "vdd": "POWER",
            "vss": "GROUND"}
    macros = {f"io_Pad{n}": dict(pins) for n in ("Vdd", "Vss", "IOVdd",
                                                  "IOVss")}
    bonded = {"io_PadVdd": "vdd", "io_PadVss": "vss", "io_PadIOVdd": "iovdd",
              "io_PadIOVss": "iovss"}
    cells = {m: {"pg_pins": {p: dict({"pg_type": "primary_power" if "dd" in p
                                      else "primary_ground",
                                      "voltage_name": p},
                                     **({"is_pad": "true"} if p == b else {}))
                             for p in pins}}
             for m, b in bonded.items()}
    cells["io_PadInOut"] = {"pins": {"c2p": {"direction": "input",
                                             "related_power_pin": "vdd",
                                             "related_ground_pin": "vss"}}}
    lib = _liberty({"vdd": 1.2, "vss": 0.0, "iovdd": 3.3, "iovss": 0.0},
                   cells)
    pair, plan, core = _resolve(_lef(macros), None, "VDD", "VSS", {}, [lib],
                                [], "library (std) {\n  nom_voltage : 1.2;\n}\n")
    by = {e["kind"]: e for e in pair}
    assert (by["power"]["master"], by["power"]["terminal"]) == ("io_PadVdd",
                                                                "vdd")
    assert (by["ground"]["master"], by["ground"]["terminal"]) == ("io_PadVss",
                                                                  "vss")
    assert by["power"]["supply_connections"] == {"vdd": "VDD", "vss": "VSS"}
    assert core["basis"] == "nom_voltage"


GF_MACROS = {"gf__dvdd": {"DVDD": "POWER", "DVSS": "GROUND", "VSS": "GROUND"},
             "gf__dvss": {"DVDD": "POWER", "DVSS": "GROUND", "VDD": "POWER"}}


def test_gf180_shape_is_unchanged():
    """Control, GREEN on main: dvdd omits the core VDD, so the same-domain
    rule decides exactly as before."""
    pair, plan, core = _resolve(_lef(GF_MACROS), "gf__", "VDD", "VSS", {},
                                [], [], None)
    by = {e["kind"]: e for e in pair}
    assert (by["power"]["master"], by["power"]["terminal"]) == ("gf__dvdd",
                                                                "DVDD")
    assert by["power"]["supply_connections"] == {
        "DVDD": "VDD", "DVSS": "VSS", "VSS": "VSS"}
    assert (by["ground"]["master"], by["ground"]["terminal"]) == ("gf__dvss",
                                                                  "DVSS")
    assert core is None and "core_rails_on_ring" not in plan


def test_the_multi_rail_inputs_are_not_loaded_when_the_same_domain_rule_decides():
    def never():
        raise AssertionError("the multi-rail inputs were loaded")

    classes, sizes, roles = _inputs(_lef(GF_MACROS))
    pair, _plan, _core = G.resolve_supply_pad_pair(
        classes, sizes, roles, "gf__", "VDD", "VSS", {}, {},
        liberty_texts=never, netlist_texts=never, core_liberty_text=never)
    assert {e["master"] for e in pair} == {"gf__dvdd", "gf__dvss"}


def test_two_candidates_at_the_core_voltage_refuse_by_name():
    """Declare a SECOND power cell whose bond also feeds VCCD: no pick."""
    feeds = dict(SKY_FEEDS)
    terms = dict(SKY_TERMINALS, lib__vccd_hvc_pad="VCCD_PAD")
    with pytest.raises(G.Refusal) as e:
        _resolve(_lef(SKY_MACROS), "lib__", "VPWR", "VGND", terms,
                 [_sky_liberty(feeds)], [_sky_netlist(feeds)], SKY_CORE)
    assert e.value.rule == "SUPPLY_PAD_PAIR_UNRESOLVED"
    assert "lib__vccd_hvc_pad" in e.value.message
    assert "lib__vccd_lvc_clamped_pad" in e.value.message
    assert "same-domain rule:" in e.value.message   # both reasons carried


def test_two_ground_rails_paired_with_the_core_rail_refuse():
    # the library now ALSO pairs VCCD with VSSIO (on one signal pin)
    lib = _sky_liberty().replace('related_power_pin : "VDDIO";\n      '
                      'related_ground_pin : "VSSIO";',
                      'related_power_pin : "VCCD";\n      '
                      'related_ground_pin : "VSSIO";', 1)
    with pytest.raises(G.Refusal) as e:
        _resolve(_lef(SKY_MACROS), "lib__", "VPWR", "VGND", SKY_TERMINALS,
                 [lib], [_sky_netlist()], SKY_CORE)
    assert "pairs power rail VCCD with" in e.value.message


def test_no_core_voltage_refuses_rather_than_guessing():
    with pytest.raises(G.Refusal) as e:
        _resolve(_lef(SKY_MACROS), "lib__", "VPWR", "VGND", SKY_TERMINALS,
                 [_sky_liberty()], [_sky_netlist()], None)
    assert "core rail's voltage is not stated" in e.value.message


def test_a_netlist_that_joins_the_bond_to_nothing_refuses():
    """No series element from the bond: the fed rail is not proven."""
    netlist = _sky_netlist().replace("RI21", "CI21")
    with pytest.raises(G.Refusal) as e:
        _resolve(_lef(SKY_MACROS), "lib__", "VPWR", "VGND", SKY_TERMINALS,
                 [_sky_liberty()], [netlist], SKY_CORE)
    assert "feeds no POWER rail" in e.value.message


def test_bond_fed_rails_reads_one_level_down_through_a_resistor():
    subs = PR.parse_spice_subckts(_sky_netlist())
    assert PR.bond_fed_rails(subs, "lib__vccd_lvc_clamped_pad", "VCCD_PAD")[0] \
        == ["VCCD"]
    assert PR.bond_fed_rails(subs, "lib__vssio_hvc_clamped_pad",
                             "VSSIO_PAD")[0] == ["VSSIO"]


def test_the_not_prose_claim_for_the_liberty_supply_view_is_falsifiable():
    """The `_NOT_PROSE` claim: only Liberty productions are read. A commented
    `is_pad` or `voltage_map` -- the one place prose can sit in a Liberty
    file -- must lend no value. Remove the comment blanking and this fails."""
    lib = ('library (io) {\n  voltage_map ("VA", 1.8);\n'
           '/*\n  voltage_map ("VB", 1.8);\n*/\n'
           '  cell ("c") {\n    pin ("P") {\n      direction : "inout";\n'
           '/* this pin is NOT the pad:\n      is_pad : "true";\n*/\n'
           '    }\n  }\n}\n')
    view = PR.parse_liberty_supply_view(lib)
    assert view["voltage_map"] == {"VA": 1.8}
    assert view["cells"]["c"]["pins"]["P"] == {"direction": "inout"}
