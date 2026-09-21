#!/usr/bin/env python3
"""The switched-capacitor phases are NON-OVERLAPPING, and they are emitted
from the entry's own declaration rather than collapsed onto `clk`/`nclkb`.

WHY (MEASURED, lane icadc2 on 8hd-3, image 0.3.67, probe1/probe2, 60 clocks):
with both phases taken from one inverter, the switch holding a sampling
capacitor's plate at the SUMMING NODE (`mn_cstv{i}`/`mn_cftv{i}`) opened on the
very edge the switch returning it to `vcm` (`mn_cstc{i}`/`mn_cftc{i}`) closed.
Bucketing the charge on the integrating capacitor by sub-interval put
-0.06408 Cs*V per clock (sd 0.01150) in the +/-5 ns around the rising edge,
while charge was conserved to -0.00197 across the settled sampling phase --
58% of the input signal charge, every clock, at that one edge. An otherwise
identical deck with a BEHAVIOURAL quantiser read -0.06420 at the same edge, so
the comparator is not the cause.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "a2", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analog_a2_topology_emit.py"))
a2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a2)

ENTRY = a2.LIBRARY["delta_sigma"]
# READ THROUGH getattr SO THE PRE-FIX TREE CAN RUN THIS FILE. Naming the new
# constant directly made the module raise AttributeError on main sources, and a
# collection error proves only that a symbol is new -- every assertion below
# went unexecuted, so none of them showed it pins anything. With the fallback
# the old tree runs every test and answers WRONGLY, which is the evidence.
CLOCK_PHASE_GENERATOR_KEY = getattr(
    a2, "CLOCK_PHASE_GENERATOR_KEY", "clock_phase_generator")
DECL = ENTRY.get(CLOCK_PHASE_GENERATOR_KEY)


def _gen_devices(decl):
    """The generator's devices, or [] on a tree that has no generator at all."""
    fn = getattr(a2, "non_overlap_phase_devices", None)
    return [] if fn is None else fn(decl)


def _gen_nets(decl):
    fn = getattr(a2, "non_overlap_phase_nets", None)
    return [] if fn is None else fn(decl)
SPEC = {"osr": 256.0, "order": 2.0, "vdd": 1.2, "vref": 1.0,
        "fclk": 1e6, "enob": 14.0, "vindiff": 1.0}


def _expanded():
    devices, nets, _, _ = a2.expand_stages(ENTRY, SPEC)
    return devices, nets


# ── direction 1: an entry that declares the pair GETS non-overlapping phases ──

def test_the_entry_declares_a_two_phase_generator():
    assert isinstance(DECL, dict), "the entry declares no phase generator"
    assert set(DECL["phases"]) == {"nph1", "nph2"}


def test_each_phase_is_held_off_by_the_other():
    """THE dead time, and it is structural. Each phase's NOR reads the OTHER
    phase, so neither can rise until the other has fallen. Read off the
    emitted devices, not off the declaration that asked for them."""
    devices, _ = _expanded()
    by_out = {}
    for d in devices:
        n = d.get("nets") or []
        if len(n) == 4:
            by_out.setdefault(str(n[0]), []).append(d)
    for phase, other in (("nph1", "nph2"), ("nph2", "nph1")):
        # walk back from the phase through its two inverters to the NOR node
        node = phase
        for _ in range(2):
            drivers = by_out.get(node) or []
            assert drivers, "nothing drives %s" % node
            node = str(drivers[0]["nets"][1])
        gates = {str(d["nets"][1]) for d in by_out.get(node) or []}
        assert other in gates, (
            "%s does not read %s, so nothing holds it off" % (phase, other))


def test_the_summing_node_switch_and_the_reference_switch_are_on_opposite_phases():
    """The defect, stated as a property: the switch that reaches the virtual
    ground and the switch that returns the same plate to `vcm` must not be
    driven by two nets that change on one edge."""
    devices, _ = _expanded()
    gate = {d["name"]: str(d["nets"][1]) for d in devices
            if len(d.get("nets") or []) == 4}
    for i in (1, 2):
        for v, c in (("mn_cstv%d", "mn_cstc%d"), ("mn_cftv%d", "mn_cftc%d")):
            gv, gc = gate[v % i], gate[c % i]
            assert gv != gc
            assert {gv, gc} == {"nph2", "nph1"}, (v % i, gv, gc)


def test_every_sc_switch_is_driven_from_a_declared_phase_not_from_clk():
    devices, _ = _expanded()
    raw = [d["name"] for d in devices
           if len(d.get("nets") or []) == 4
           and d["name"].startswith(("mn_smp", "mp_smp", "mn_cst", "mp_cst",
                                     "mn_cft", "mp_cft"))
           and str(d["nets"][1]) in ("clk", "nclkb")]
    assert raw == [], raw


def test_the_generator_nets_are_declared_internal():
    devices, nets = _expanded()
    for n in _gen_nets(DECL):
        assert n in nets, n


# ── direction 2: an entry that declares NO pair is emitted exactly as before ──

def test_an_entry_that_declares_no_generator_gets_no_devices():
    assert _gen_devices(None) == []
    assert _gen_devices({}) == []


def test_a_single_phase_declaration_keeps_its_single_phase():
    """A topology that declares ONE phase is not given a second one. The
    generator is a consequence of the declaration, never of this module's
    opinion about switched-capacitor design."""
    assert _gen_devices(
        {"source": "clk", "complement": "nclkb",
         "phases": {"only": {"complement_net": "onlyb"}}}) == []


def test_a_phase_without_a_complement_net_is_refused_not_guessed():
    assert _gen_devices(
        {"source": "clk", "complement": "nclkb",
         "phases": {"a": {}, "b": {"complement_net": "bb"}}}) == []


def test_no_other_library_entry_is_given_a_generator():
    """Both directions across the whole library: only entries that declare the
    key get the devices."""
    for name, lib in a2.LIBRARY.items():
        if lib.get(CLOCK_PHASE_GENERATOR_KEY) is None:
            assert _gen_devices(
                lib.get(CLOCK_PHASE_GENERATOR_KEY)) == [], name


# ── the invariant the change must not move ───────────────────────────────

def test_the_derived_feedback_delay_is_unchanged():
    """MEASURED as 1 on the pre-change tree. The phases are ALIASED back to the
    two the derivation always read, so the decode stamp this block ships
    (`feedback_delay_clocks=1`) is the same circuit's, not a new one's."""
    assert a2.derived_feedback_delay(ENTRY) == 1


def test_each_declared_phase_resolves_to_the_edge_it_replaces():
    al = ENTRY[a2.CLOCK_PHASE_ALIASES_KEY]
    assert a2._resolve_phase("nph1", al) == "clk"
    assert a2._resolve_phase("nph2", al) == "nclkb"
    assert a2._resolve_phase("nph1b", al) == "nclkb"
    assert a2._resolve_phase("nph2b", al) == "clk"


def test_the_forward_and_feedback_branches_keep_their_polarities():
    """`sc_branch_polarities` decides sign by comparing RESOLVED phases. If the
    aliases were wrong the two branches would swap sign silently and every
    decoded value would change with no test going red."""
    st = a2._stage_groups(ENTRY)[0]
    probe = {"i": 1, "i1": 2, "in": "IN", "out": "OUT", "coeff": "1.0",
             "alt": "ALT", "in2": "IN2", "out2": "OUT2"}
    devs = []
    for d in st.get("devices") or []:
        nd = dict(d)
        nd["nets"] = [str(n).format(**probe) for n in d.get("nets") or []]
        nd["name"] = str(d["name"]).format(**probe)
        devs.append(nd)
    pol = a2.sc_branch_polarities(devs, ENTRY[a2.CLOCK_PHASE_ALIASES_KEY])
    assert pol, "no branch resolved a polarity"
    assert all(b["polarity"] == 1 for b in pol.values()), pol


# ── the property my own first attempt violated ───────────────────────────
#
# MEASURED (lane icadc2, 8hd-3, 60 clocks). Moving a branch capacitor's
# SUMMING-NODE switches onto the declared phases while leaving that same
# capacitor's BOTTOM-PLATE switches on the raw clock put the two plates of ONE
# capacitor in two clock domains 1.5 ns apart. The charge that branch delivered
# across the rising edge went from +0.02541 sd 0.49949 -- varying with the
# decision, because it IS the feedback -- to +0.54421 sd 0.00644, a CONSTANT
# step dumped into the summing node. The integrator ran away to the rail and
# the bitstream stuck at 96% ones. Every test above still passed.

def _phase_domain(devices, roots):
    """Nets driven, transitively, by a gate already in the domain. A net whose
    driver is gated from a declared phase carries that phase's timing even
    though its name is not a phase name -- the gated DAC clock is exactly
    that, and it is why this is a reachability question and not a name test."""
    dom = set(roots)
    changed = True
    while changed:
        changed = False
        for d in devices:
            n = d.get("nets") or []
            if len(n) != 4:
                continue
            if str(n[1]) in dom and str(n[0]) not in dom:
                dom.add(str(n[0]))
                changed = True
    return dom


def test_both_plates_of_a_branch_capacitor_are_in_one_clock_domain():
    devices, _ = _expanded()
    if not DECL:
        return
    roots = set(DECL["phases"]) | {
        str(v["complement_net"]) for v in DECL["phases"].values()}
    dom = _phase_domain(devices, roots)
    caps = {d["name"]: [str(x) for x in d["nets"]]
            for d in devices if len(d.get("nets") or []) == 2}
    offenders = []
    for i in (1, 2):
        for cap in ("cs%d" % i, "cf%d" % i):
            plates = set(caps[cap])
            for d in devices:
                n = d.get("nets") or []
                if len(n) == 4 and str(n[0]) in plates:
                    if str(n[1]) not in dom:
                        offenders.append((cap, d["name"], str(n[1])))
    assert offenders == [], (
        "these switches drive a branch capacitor's plate from OUTSIDE the "
        "declared phase domain, so the two plates of one capacitor move at "
        "different times: %r" % (offenders,))


def test_the_gated_dac_clock_is_gated_from_the_declared_phase():
    """Named directly as well as by reachability, because this is the one the
    measurement caught and a reader should be able to find it."""
    devices, _ = _expanded()
    gates = {d["name"]: str(d["nets"][1]) for d in devices
             if len(d.get("nets") or []) == 4}
    for dev in ("mp_ndac1", "mn_ndac1"):
        assert gates[dev] == "nph1", (dev, gates[dev])
