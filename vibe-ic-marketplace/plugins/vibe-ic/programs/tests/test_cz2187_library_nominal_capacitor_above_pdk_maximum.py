"""A capacitor above the PDK maximum is split BECAUSE it is above the maximum.

WHAT WAS BROKEN, MEASURED ON A REAL RUN (vibe-ic#2187)
=====================================================
`analog_a2_topology_emit.split_oversize_capacitors` looked each capacitor up in
`param_exprs` and `continue`d when it found nothing:

    expr = by_dev.get(str(d.get("name")))
    if d.get("role") != CAP_ROLE or expr is None ...:
        out_devs.append(d); continue

So the ceiling was never applied to a capacitor whose drawn length is a plain
number on its own device record — the length never reached the comparison at
all. The split was keyed on WHERE THE LENGTH CAME FROM instead of on the one
fact it is about: whether the PDK can draw it.

MEASURED at the function level on the base this lane forked from, with the
shipped registry's own `cap` ceiling (max_length_um 30.0) and the family's own
measured capacitance constants, on TWO capacitors of IDENTICAL geometry
(w=10.0u, l=60.0u):

    length via `device_param_exprs`  ->  split into 2 units of 29.973474801061u
    length as a literal on the device ->  UNSPLIT at l=60.0u, no record, no refusal

Downstream, on a real front-door run: the magic capacitor gencell asked for a
length above its own stated `lmax` does NOT refuse — it CLAMPS to the maximum
and reports success — so the drawn device was half the one the netlist asked
for, A5 reported
`A5_DEVICE_ABOVE_PDK_MAXIMUM` / `CLAMPED_GEOMETRY`, and the sign-off LVS
correctly reported a mismatch on a block whose DRC was 0 violations over 560
rules. A5 and A6 are two rows of this one cause.

THE PROPERTY EVERY TEST BELOW IS ABOUT
======================================
The predicate is "this device's drawn length is above the PDK's stated
maximum", and it is evaluated on the length the netlist will actually RENDER,
whichever of the two places that length comes from. Both directions are
asserted, because a split that fires on everything is not a repair: a device at
or below the maximum still emits no array and is returned untouched, so a
design that needs no split takes a path that changes nothing.

BLOCKING vs ADVISORY: this is the PRODUCER, not a gate. Its refusal path is
BLOCKING — `build_ir` raises `CapacitorNotRealisable` on a refusal and the
block is emitted nowhere — and that path is asserted here too, on the family
shape that reaches it (a stated maximum with no measured capacitance
constants), because a producer that gained a split and lost a refusal has not
gained anything.

chip-AGNOSTIC: generic role tokens (`cap`, `nmos`), the OPEN `ihp-sg13g2`
registry entry, and invented device and block names. No commercial foundry,
node, SKU or codename.
"""
from __future__ import annotations

import re

import pytest

import _plugin_tree  # noqa: F401  — puts programs/ on sys.path
import analog_a2_topology_emit as A2  # noqa: E402
import pdk_analog_layout_minima as M  # noqa: E402

from _analog_producer_fixture import (  # noqa: E402
    A1, A2 as A2_PROG, A3, bdir, block, make_project, read_json, run_prog)

#: The OPEN family whose registry record states a capacitor ceiling AND carries
#: the measured capacitance constants the array is solved against. Read from
#: the registry, never retyped, so a family whose numbers move moves these too.
FAMILY = "ihp-sg13g2"
BLK = "mod_cz2187"


def _family_records(name=FAMILY):
    _fam, ent = M.resolve_family(name)
    maxima = (ent.get("analog_device_layout_maxima") or {}).get("roles") or {}
    minima = (ent.get("analog_device_layout_minima") or {}).get("roles") or {}
    return ent, maxima, minima


def _measured(ent):
    """The family's measured capacitance constants, found wherever the record
    carries them — the registry nests the measured corner and this test is not
    the place to hold a second copy of that shape."""
    def find(o, key):
        if isinstance(o, dict):
            if key in o:
                return o[key]
            for v in o.values():
                r = find(v, key)
                if r is not None:
                    return r
        return None
    return {"cap_area_ff_per_um2": find(ent, "cap_area_ff_per_um2"),
            "cap_perim_ff_per_um": find(ent, "cap_perim_ff_per_um")}


def _split(devices, param_exprs, *, measured=None, family=FAMILY):
    ent, maxima, minima = _family_records(family)
    return A2.split_oversize_capacitors(
        devices, param_exprs, {}, maxima, minima,
        _measured(ent) if measured is None else measured)


def _lmax(family=FAMILY):
    _ent, maxima, _minima = _family_records(family)
    lmax = M.max_length_um(maxima, A2.CAP_ROLE)
    assert lmax is not None, (
        f"{family} states no capacitor ceiling any more; every assertion "
        f"below is about that ceiling and none of them would mean anything")
    return float(lmax)


def _nominal_cap(name="c_nom", w=10.0, l_um=None):
    """One capacitor whose drawn length is a LIBRARY NOMINAL — a number on the
    device record, with no `device_param_exprs` entry anywhere. This is the
    shape the reported run carried."""
    return {"name": name, "role": A2.CAP_ROLE, "nets": ["n1", "vss"],
            "w": w, "l": (2.0 * _lmax() if l_um is None else l_um)}


# ── the capability ────────────────────────────────────────────────────────
def test_a_library_nominal_capacitor_above_the_maximum_is_split():
    """T1. RED on the base: the device came back unsplit at twice the ceiling
    because it had no sizing expression to be looked up by."""
    lmax = _lmax()
    devs, _exprs, recs, refusals = _split([_nominal_cap()], [])
    assert refusals == [], refusals
    names = [d["name"] for d in devs]
    assert "c_nom" not in names, (
        f"the capacitor is still one device at l={devs[0].get('l')}u against a "
        f"stated maximum of {lmax}u — the gencell will CLAMP it and draw "
        f"something else")
    assert len(names) >= 2 and names == [f"c_nom_u{i}" for i in range(len(names))]
    for d in devs:
        assert isinstance(d.get("l"), (int, float)), (
            f"unit {d['name']} carries no drawn length of its own, so it "
            f"still renders the parent's")
        assert float(d["l"]) <= lmax, (d["name"], d["l"], lmax)
        assert float(d["w"]) == 10.0, (d["name"], d["w"])
        assert list(d["nets"]) == ["n1", "vss"], (
            f"{d['name']} is not on the parent's node pair, so the units are "
            f"not in parallel and the array is not the capacitor")


def test_the_library_nominal_split_preserves_the_capacitance():
    """T2. The array is only the same capacitor if it carries the same value
    on the PDK's own two-term model — the bound is the producer's own.

    THE SPLIT IS ASSERTED FIRST, and that is not decoration. Written without
    the count assertion this test PASSED on the unfixed base (measured, lane
    cz2187): the sum over the ONE unchanged device it got back is exactly the
    target, so "the split never fired" satisfied a bound about what the split
    preserves. A check that cannot fail in the direction it was written for is
    not a check, so the value bound is coupled to the split having happened.
    """
    ent, _maxima, _minima = _family_records()
    m = _measured(ent)
    carea, cperi = m["cap_area_ff_per_um2"], m["cap_perim_ff_per_um"]
    cap = _nominal_cap()
    devs, _e, _r, refusals = _split([dict(cap)], [])
    assert refusals == []
    assert len(devs) >= 2, (
        f"the capacitor came back as {len(devs)} device(s), so the value bound "
        f"below is being asserted over a capacitor nothing split")
    target = A2.capacitance_ff(cap["w"], cap["l"], carea, cperi)
    got = sum(A2.capacitance_ff(float(d["w"]), float(d["l"]), carea, cperi)
              for d in devs)
    assert abs(got - target) / target <= A2.CAP_SPLIT_TOLERANCE, (got, target)


def test_the_library_nominal_split_is_DECLARED_like_every_other_one():
    """T3. The record is what the deck's `capacitor_array` line is rendered
    from, so a split with no record is a deck that emits N units and declares
    none — the shape the 2026-09-07 declare-what-you-emit rule rejects."""
    lmax = _lmax()
    cap = _nominal_cap()
    devs, _e, recs, _ref = _split([dict(cap)], [])
    assert len(recs) == 1, recs
    r = recs[0]
    assert r["device"] == "c_nom"
    assert r["role"] == A2.CAP_ROLE
    assert r["units"] == len(devs)
    assert r["library_l_um"] == pytest.approx(cap["l"])
    assert r["pdk_max_l_um"] == pytest.approx(lmax)
    assert r["unit_l_um"] <= lmax
    assert r["relative_value_error"] <= A2.CAP_SPLIT_TOLERANCE


# ── the other direction ───────────────────────────────────────────────────
@pytest.mark.parametrize("l_um", ["min", 10.0, None])
def test_a_library_nominal_capacitor_the_pdk_CAN_draw_is_untouched(l_um):
    """T4. THE CONTROL. `None` is the ceiling itself, read from the registry —
    at the maximum is legal, and a split that fires there would be splitting
    devices the PDK draws perfectly well. `"min"` is the other end, also read
    from the registry: it used to be a literal 0.5u, which was "drawable" only
    while the family stated no capacitor minimum. It now states the Magic
    gencell's (q6-a2-cap-osr), and a 0.5u device is one the gencell clamps UP
    — re-solved or refused, see test_q6_a2_cap_split_and_swing_model.py —
    so the control is taken AT the minimum, where it is legal."""
    lmax = _lmax()
    if l_um == "min":
        _e, _mx, minima = _family_records()
        l_um = M.min_length_um(minima, A2.CAP_ROLE)
        assert l_um is not None
    cap = _nominal_cap(l_um=(lmax if l_um is None else l_um))
    devs, exprs, recs, refusals = _split([dict(cap)], [])
    assert [d["name"] for d in devs] == ["c_nom"]
    assert devs[0]["l"] == pytest.approx(cap["l"])
    assert recs == [] and refusals == [] and exprs == []


def test_a_non_capacitor_above_the_capacitor_ceiling_is_untouched():
    """T5. THE OTHER CONTROL. The ceiling is the CAPACITOR role's; a fet whose
    drawn length happens to exceed it is a different device class and this
    pass has nothing to say about it."""
    fet = {"name": "m_long", "role": "nmos", "nets": ["d", "g", "s", "b"],
           "w": 10.0, "l": 2.0 * _lmax()}
    devs, _e, recs, refusals = _split([dict(fet)], [])
    assert [d["name"] for d in devs] == ["m_long"]
    assert devs[0]["l"] == pytest.approx(fet["l"])
    assert recs == [] and refusals == []


# ── the refusal path, which must not have been lost ───────────────────────
def _no_constants():
    return {"cap_area_ff_per_um2": None, "cap_perim_ff_per_um": None}


def test_a_family_with_a_ceiling_and_no_constants_REFUSES_BY_NAME():
    """T6. The family states a maximum and carries nothing to solve the array
    with. Detecting the oversize needs only the ceiling and the drawn length,
    so the device is refused BY NAME rather than carried at its library length
    — and that was equally blind to a library-nominal length on the base."""
    devs, _e, recs, refusals = _split([_nominal_cap()], [],
                                      measured=_no_constants())
    assert recs == []
    assert len(refusals) == 1, refusals
    assert refusals[0].startswith("c_nom:")
    assert "cap_area_ff_per_um2" in refusals[0]
    assert [d["name"] for d in devs] == ["c_nom"], (
        "a refused device is never silently redrawn")


def test_a_family_with_a_ceiling_and_no_constants_refuses_only_the_oversize():
    """T7. THE CONTROL for T6. A refusal that fires on a family whose
    capacitors are all legal is a false accusation about the PDK."""
    devs, _e, recs, refusals = _split([_nominal_cap(l_um=_lmax())], [],
                                      measured=_no_constants())
    assert refusals == [] and recs == []
    assert [d["name"] for d in devs] == ["c_nom"]


# ── end to end, on the shipped entry the run reported ─────────────────────
_CAP_ARRAY_RE = re.compile(
    r"(?im)^\*\s*_provenance:\s*capacitor_array\s+base=(\S+)\s+units=(\d+)\s+"
    r"instances=\[([^\]]*)\]")


def _ds_specs():
    return [
        {"name": "Order", "target": 2.0, "unit": "—"},
        {"name": "OSR", "target": 256.0, "unit": "—"},
        {"name": "ENOB", "min": 14.0, "unit": "bit"},
        {"name": "Vref", "target": 1.0, "unit": "V"},
        {"name": "Vdd (core)", "target": 1.2, "unit": "V"},
        {"name": "fclk", "target": 1.0, "min": 0.1, "max": 1.0, "unit": "MHz"},
    ]


def _emit(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    make_project(root, [block(BLK, "delta_sigma", _ds_specs())])
    assert run_prog(A1, root).returncode == 0
    r2 = run_prog(A2_PROG, root, "--pdk", FAMILY)
    assert r2.returncode == 0, r2.stderr
    r3 = run_prog(A3, root, "--pdk", "sky130A")
    assert r3.returncode == 0, r3.stderr
    d = bdir(root, BLK)
    return d, read_json(d / "topology.json"), \
        (d / f"{BLK}.sp").read_text(encoding="utf-8")


def _deck_caps(text):
    """`{instance: (w, l)}` for every capacitor card in the deck."""
    out = {}
    for ln in text.splitlines():
        toks = ln.split()
        if not toks or not toks[0].startswith("x") or "cap" not in ln:
            continue
        w = l = None
        for t in toks:
            if t.startswith("l="):
                l = float(t[2:].rstrip("u"))
            elif t.startswith("w="):
                w = float(t[2:].rstrip("u"))
        if w is not None and l is not None:
            out[toks[0][1:]] = (w, l)
    return out


def test_no_emitted_capacitor_is_above_the_families_stated_maximum(tmp_path):
    """T8. THE REPORTED DEFECT, end to end on the shipped entry. On the base
    exactly one card survives above the ceiling — the entry's library-nominal
    delay capacitor — and it is the one the gencell clamps."""
    lmax = _lmax()
    _d, _ir, txt = _emit(tmp_path)
    caps = _deck_caps(txt)
    assert caps, "the deck carries no capacitor card at all"
    over = {n: g for n, g in caps.items() if g[1] > lmax + 1e-9}
    assert not over, (
        f"these capacitor cards ask the gencell for a drawn length above the "
        f"{lmax}u this family states, and it CLAMPS rather than refusing: "
        f"{sorted(over.items())}")


def test_every_array_the_deck_declares_is_the_array_it_emits(tmp_path):
    """T9. THE CONTROL that keeps the split honest end to end: the deck
    declares what it emits and emits what it declares. A split that started
    firing on a new device class and did not declare it would pass T8 and fail
    here."""
    _d, _ir, txt = _emit(tmp_path)
    cards = set(_deck_caps(txt))
    arrays = {}
    for m in _CAP_ARRAY_RE.finditer(txt):
        insts = [t.strip().strip("'\"") for t in m.group(3).split(",") if t.strip()]
        arrays[m.group(1)] = (int(m.group(2)), insts)
    assert arrays, "the deck declares no capacitor array at all"
    for base, (n, insts) in sorted(arrays.items()):
        assert n == len(insts) >= 2, (base, n, insts)
        for inst in insts:
            assert inst in cards, (
                f"the deck declares array unit `{inst}` for `{base}` and no "
                f"such capacitor card is in the netlist")
    declared = {i for _n, insts in arrays.values() for i in insts}
    stray = {c for c in cards if re.match(r"^.*_u\d+$", c)} - declared
    assert not stray, (
        f"these cards are spelled as array units and no declaration names "
        f"them: {sorted(stray)}")
