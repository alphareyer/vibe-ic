#!/usr/bin/env python3
"""vibe-ic#2161 — a PDK family's device tokens come from ITS OWN library, or the
entry says NOT_AVAILABLE and the run refuses by name.

WHAT WAS MEASURED, pristine main 0e44abf5e, host 8HD-4, pinned image
`vibeic-eda@sha256:1463dac5…` (0.3.48), real ngspice:

    `--pdk gf180` -> `_KNOWN_FAMILIES["gf180"]` declared the OTHER open PDK's
    MOS tokens as its own device map, so the emitted deck bound gf180mcuD's
    model library and instantiated sky130A's nfet/pfet against it.

      arm A  the deck exactly as shipped
             ERROR, library file …/gf180mcuD/libs.tech/ngspice/design.ngspice,
             section definition tt not found                            rc=1
      arm B  the same tokens against that family's OWN device library
             Error: unknown subckt: … sky130_fd_pr__nfet_01v8            rc=1
      arm C  the sky130 entry, own library and own tokens
             MEAS vout= 1.79269                                          rc=0

vibe-ic#2139's cross-binding guard cannot fire on this BY RULE and that is
correct: it refuses when the library and the tokens come from DIFFERENT
declarations. Here they come from ONE — the declaration itself was wrong. A
guard cannot save a table that lies about its own contents, so the table is
what changes.

THE TWO HALVES OF THE FIX, AND WHY THERE ARE TWO.
  * LOAD TIME, no PDK required — a declared token carrying ANOTHER registered
    family's `device_model_prefix` (published in the plugin's own
    `pdk_registry.json`) is that family's device wherever it is typed, and is
    refused BY NAME at import. This is the half that makes the wrong
    declaration unshippable on a host that has no PDK installed at all.
  * AT RESOLVE TIME, where the PDK IS installed — the entry's claim is measured
    against the family's own model library: an authored token the library does
    not define is refused by name, and an entry with NO authored map DERIVES one
    from that library. An unreadable library measures NOTHING and changes
    nothing; it is never read as "the library defines nothing".

WHY gf180 ENDED AT NOT_AVAILABLE RATHER THAN AT A NATIVE MAP. Measured in the
pinned image, the library that entry declared defines 0 `.subckt`, 0 `.model`,
0 `.include`/`.lib` targets and 0 corner sections — it is that family's global
switch/parameter deck. There was nothing in it to derive from, so the honest
entry stated NOT_AVAILABLE with that reason. Authoring a native template for
the family (a different file under the same published root) would name a
different file under a published open PDK's root, which is a separate,
declared decision — not something a fix for a wrong declaration may do
silently.

THAT DECISION WAS TAKEN, AND THIS FILE WAS AMENDED IN THE OPEN FOR IT. The
follow-on names the sibling device library the same published ngspice
directory ships, and the family now resolves natively: `typical`/`ss`/`ff`
sections, its own `nfet_03v3`/`pfet_03v3`/`ppolyf_u`/`cap_mim_1f0ff`, metric
geometry, and 9 of 9 PVT corners really simulated. Every test below that
pinned "this family is NOT_AVAILABLE" now pins the mechanism on a SYNTHETIC
family supplied through the reader seam — which is strictly stronger, because
the mechanism stops depending on one PDK happening to lack a template — and
the gf180-specific assertions became the stricter positive ones in
`test_gf180_family_has_its_own_native_analog_device_template.py`. Nothing was
deleted: this file's test population GREW. The NOT_AVAILABLE machinery itself
(`NOT_AVAILABLE`, `family_device_map`, `family_not_available_reason`, the
derivation path) is untouched and is still exercised here, by name.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROGRAMS))
import analog_pdk_deck_context as A            # noqa: E402
import analog_real_corner_sweep as S           # noqa: E402


# ── direction 1: a family whose tokens its own library defines LOADS ─────────

def test_the_shipped_table_declares_no_foreign_device_token():
    """The load-time half, on the table as shipped. `[]` is measured-and-clean;
    `None` would mean the registry could not be read, which is NOT the same
    thing and must not pass as clean."""
    foreign = A.foreign_device_tokens()
    assert foreign is not None, (
        "the registry could not be read, so nothing was measured — this is "
        "NOT_MEASURED, not a clean table")
    assert foreign == [], foreign
    assert A.KNOWN_FAMILY_DECLARATION_STATE == "clean"


def test_the_family_that_authors_a_map_still_carries_exactly_it():
    """sky130 authors a map and its own library defines it. Byte-identical to
    before the fix — the regression surface this must not move."""
    c = A.known_family_context("sky130")
    assert c.status == "OK", c.work_items
    assert c.device_map == dict(A.SKY130_DEVICES)
    assert c.model_lib == A._KNOWN_FAMILIES["sky130"]["model_lib"]
    assert c.corner_sections == ["ss", "tt", "ff"]
    assert c.work_items == []


def test_a_family_whose_library_defines_its_tokens_loads():
    """The positive direction of the resolve-time half, with a library that
    really does define the declared tokens (supplied through the reader seam,
    so the test states its own input rather than depending on an install)."""
    fams = {"fam": {"device_map": {"nmos": "own_nfet", "pmos": "own_pfet"},
                    "corner_sections": ["tt"],
                    "model_lib": "/nowhere/fam.lib"}}
    lib = (".lib tt\n.subckt own_nfet d g s b\n.ends\n"
           ".subckt own_pfet d g s b\n.ends\n.endl\n")
    A.grounding_cache_clear()
    gr = A.family_library_grounding("fam", lambda p: lib, families=fams)
    assert gr["measured"] is True
    assert gr["undefined_tokens"] == []
    assert gr["undefined_sections"] == []


# ── direction 2: a planted FOREIGN token is refused BY NAME ──────────────────

def test_a_planted_foreign_token_is_refused_by_name_at_load_time():
    """Exactly the declaration that stood on main, planted back. The refusal
    must name the family, the role, the token, the family the token belongs to
    and the library the entry binds — a refusal that names none of these leaves
    the reader to re-derive the defect."""
    planted = {"gf180": {
        "device_map": dict(A.SKY130_DEVICES),
        "corner_sections": ["ss", "tt", "ff"],
        "model_lib": "/foss/pdks/gf180mcuD/libs.tech/ngspice/design.ngspice"}}
    foreign = A.foreign_device_tokens(planted)
    assert foreign and {f["role"] for f in foreign} == {"nmos", "pmos"}
    with pytest.raises(A.PdkFamilyDeclarationError) as e:
        A.assert_families_declare_their_own_devices(planted)
    msg = str(e.value)
    assert "gf180" in msg
    assert A.SKY130_DEVICES["nmos"] in msg
    assert "sky130A" in msg, "the family the token belongs to is named"
    assert "sky130_fd_pr__" in msg, "the prefix that attributes it is named"
    assert "gf180mcuD/libs.tech/ngspice/design.ngspice" in msg, \
        "the library the entry binds is named"


def test_an_authored_token_its_own_library_does_not_define_is_refused():
    """The resolve-time half of the same refusal: the token is not another
    REGISTERED family's, so the load-time prefix rule cannot see it — only the
    library can."""
    fams = {"fam": {"device_map": {"nmos": "not_in_this_lib",
                                   "pmos": "own_pfet"},
                    "corner_sections": ["tt"],
                    "model_lib": "/nowhere/fam.lib"}}
    lib = ".lib tt\n.subckt own_pfet d g s b\n.ends\n.endl\n"
    A.grounding_cache_clear()
    gr = A.family_library_grounding("fam", lambda p: lib, families=fams)
    assert gr["measured"] is True
    assert gr["undefined_tokens"] == [("nmos", "not_in_this_lib")]


def test_an_unreadable_library_measures_nothing_and_refuses_nothing():
    """"Could not read it" is not "read it and it defines nothing". An
    unreadable library must leave the authored map exactly as it was."""
    fams = {"fam": {"device_map": {"nmos": "own_nfet"},
                    "corner_sections": ["tt"],
                    "model_lib": "/nowhere/fam.lib"}}
    A.grounding_cache_clear()
    gr = A.family_library_grounding("fam", lambda p: None, families=fams)
    assert gr["measured"] is False
    assert gr["undefined_tokens"] == [] and gr["undefined_sections"] == []
    assert gr["derived_device_map"] == {}


# ── direction 3: `--pdk gf180` refuses with the missing-template reason ──────

_NO_TEMPLATE = {"famnt": {
    "device_map": A.NOT_AVAILABLE,
    "device_map_not_available": (
        "no native device template was authored for this family, and its "
        "declared model library defines nothing to derive one from"),
    "corner_sections": ["ss", "tt", "ff"],
    "model_lib": "/nowhere/switches-only.deck"}}
#: A file that is a switch/parameter deck, not a device library — the shape
#: this family's entry used to name. Supplied through the reader seam so the
#: mechanism no longer depends on any PDK lacking a template.
_SWITCHES_ONLY = "* switches\n.param sw_stat_global=0\n.param mc_skew=3\n"


def test_a_family_with_no_native_template_says_NOT_AVAILABLE_by_name():
    """AMENDED: the family this ran against acquired a native template, so the
    mechanism moved onto a synthetic entry whose declared library really is a
    switch deck. Same assertions, one family less dependent."""
    c = _no_template_context()
    assert c.status == "NEEDS_NATIVE_TEMPLATE", c.as_json()
    assert c.device_map == {}, "it must carry NO device map, not a borrowed one"
    assert sorted(c.unresolved_roles) == ["nmos", "pmos"]
    joined = " | ".join(c.work_items)
    assert "NOT_AVAILABLE" in joined
    assert "famnt" in joined
    assert "switches-only.deck" in joined, "the library is named"
    assert "vibe-ic#2161" in joined
    assert "NOT_AVAILABLE" in c.disclosure


def _no_template_context():
    """`known_family_context` reads the SHIPPED table by key, so the synthetic
    entry is installed for the call and removed after it."""
    A._KNOWN_FAMILIES.update(_NO_TEMPLATE)
    try:
        A.grounding_cache_clear()
        return A.known_family_context("famnt",
                                      reader=lambda p: _SWITCHES_ONLY)
    finally:
        A._KNOWN_FAMILIES.pop("famnt", None)
        A.grounding_cache_clear()


def test_the_family_that_acquired_a_native_template_carries_only_its_own():
    """The gf180-specific half of the old test, at the ruling that replaced
    it: the entry resolves, and NOT ONE field carries the other open PDK's
    tokens. That second clause is the one that mattered and it is unchanged —
    now checked against a context carrying a FULL map, which is the harder
    case. The template's own grounding lives in
    `test_gf180_family_has_its_own_native_analog_device_template.py`."""
    c = A.known_family_context("gf180")
    assert c.status == "OK", c.work_items
    assert c.device_map, "it carries its own map now"
    for token in A.SKY130_DEVICES.values():
        assert token not in repr(c.as_json()), token


def test_no_borrowed_token_survives_anywhere_any_family_is_asked():
    """The whole point, widened: not one field of ANY known family's context
    may carry ANOTHER known family's device tokens, by any route. Was written
    against the one family that had none of its own; it is now checked for
    every entry in the table, which is what it always meant."""
    for key, entry in A._KNOWN_FAMILIES.items():
        j = A.known_family_context(key).as_json()
        blob = repr(j)
        for other, oentry in A._KNOWN_FAMILIES.items():
            if other == key:
                continue
            for token in (A.family_device_map(oentry) or {}).values():
                assert token not in blob, (key, other, token)


def test_the_NOT_AVAILABLE_machinery_still_answers_for_an_entry_that_uses_it():
    """AMENDED: the entry these three readers were checked on now authors a
    map, so they are checked on a synthetic entry that does not. The machinery
    is not deleted, and this is the test that says so by name."""
    entry = _NO_TEMPLATE["famnt"]
    assert entry["device_map"] == A.NOT_AVAILABLE
    assert A.family_device_map(entry) is None
    reason = A.family_not_available_reason(entry)
    assert reason and "no native device template" in reason
    # and an entry that DOES author a map is not mistaken for one that does not
    gf = A._KNOWN_FAMILIES["gf180"]
    assert A.family_device_map(gf) == dict(gf["device_map"])
    assert A.family_not_available_reason(gf) is None


def test_the_dispatcher_carries_the_verdict_for_the_same_family():
    """AMENDED: the dispatcher must report whatever the known-family path
    decided — which for this family is now OK with its own map. Both
    directions are asserted, so a dispatcher that hard-coded either answer
    would still be caught."""
    c = A.resolve_deck_context("gf180")
    assert c.source == "known_family"
    assert c.status == "OK", c.work_items
    assert c.device_map == dict(A._KNOWN_FAMILIES["gf180"]["device_map"])
    A._KNOWN_FAMILIES.update(_NO_TEMPLATE)
    try:
        A.grounding_cache_clear()
        n = A.resolve_deck_context("famnt")
    finally:
        A._KNOWN_FAMILIES.pop("famnt", None)
        A.grounding_cache_clear()
    assert n.source == "known_family"
    assert n.status == "NEEDS_NATIVE_TEMPLATE", n.as_json()
    assert n.device_map == {}


def test_the_sweep_refuses_a_not_ok_known_family_before_it_takes_a_lib():
    """The consumer half. `analog_real_corner_sweep` branches on
    `source == "known_family"` and then takes its OWN `PDK_LIB` — so the
    refusal has to happen INSIDE that branch, before the lib is read, or the
    context's verdict is simply not consulted on this path."""
    sweep = (_PROGRAMS / "analog_real_corner_sweep.py").read_text()
    i = sweep.index('ctx.source == "known_family"')
    # WIDENED, not loosened, TWICE, and each time because the window was
    # measuring the wrong thing. It was 700 characters — the distance between
    # the two statements on the day it was written — so a comment between them
    # broke it. It now ends at the branch's own `else:`, which is what "inside
    # that branch" means. And the lib pick moved into
    # `known_family_deck_inputs`, so the second anchor is that CALL: the point
    # after which a library has been chosen.
    _end = sweep.index("\n    else:", i)
    branch = sweep[i:_end]
    guard = branch.index('ctx.status != "OK"')
    lib = branch.index("known_family_deck_inputs(")
    assert guard < lib, "the refusal must precede the lib pick"
    assert "_write_native_template_gap" in branch[guard:lib]
    assert "return 2" in branch[guard:lib]
    # and the call it precedes really is the one that yields a library
    assert "pdk_lib" in S.known_family_deck_inputs(None, "sky130")


def test_the_two_open_pdk_libs_are_declared_in_exactly_one_place():
    """`analog_real_corner_sweep.PDK_LIB` is a SECOND declaration of the same
    per-family model library. Two declarations of one fact is how the first one
    got to be wrong without the second noticing; they must not drift."""
    assert set(S.PDK_LIB) == set(A._KNOWN_FAMILIES)
    for fam, lib in S.PDK_LIB.items():
        assert lib == A._KNOWN_FAMILIES[fam]["model_lib"], fam


# ── the mutation: restore the typed tokens and the death comes back ──────────
#
# MEASURED WHILE WRITING THIS FILE, and it changed what the mutation asserts.
# The first version planted the tokens and expected `status == "OK"` so it could
# render the cross-bound deck. That passed on a host with no PDK and FAILED
# inside the pinned image — because there the family's own library IS readable
# and the resolve-time half refused the planted map by name. The mutation was
# wrong, not the fix. So there are two mutations, one per half of the fix, and
# each says which half it is exercising.

_MUTATION_NO_GROUNDING = '''
import sys
sys.path.insert(0, {programs!r})
import analog_pdk_deck_context as A
import analog_real_corner_sweep as S
# main's state of knowledge, restored exactly: the typed tokens back in the
# table AND nothing looking at the family's own library.
A._KNOWN_FAMILIES["gf180"]["device_map"] = dict(A.SKY130_DEVICES)
# main's state of knowledge is "the library was never read", so the stub says
# measured=False and answers every key the caller reads with the empty value
# that state implies. It is a NOT-MEASURED grounding, not a clean one.
A.family_library_grounding = lambda *a, **k: {{
    "measured": False, "library": A._KNOWN_FAMILIES["gf180"]["model_lib"],
    "declared_tokens": {{}}, "declared_sections": [], "n_subckts": 0,
    "undefined_tokens": [], "undefined_sections": [], "derived_device_map": {{}},
    "geometry_units": {{}}, "geometry_param_names": {{}}, "terminals": {{}},
    "companion_sections": {{}}, "undefined_companion_sections": [],
    "prelude": [], "prelude_unreadable": [], "undefined_params": []}}
ctx = A.resolve_deck_context("gf180")
assert ctx.status == "OK", ctx.status
deck, _ = S.render_deck("ldo", "u_ldo", "gf180",
                        "/foss/pdks/gf180mcuD/libs.tech/ngspice/design.ngspice",
                        ctx.typ_section or "tt", "m_pass", 8, devices=None)
open({deck!r}, "w").write(deck)
'''


def test_mutation_the_typed_tokens_without_the_grounding_cross_bind_again(tmp_path):
    """MUTATION 1 — both halves of the fix removed (the table's declaration and
    the library grounding), which is main. The deck once again loads one open
    PDK's library and instantiates the other's devices against it: the exact
    text arm A and arm B died on with the real simulator.

    The library the mutant loads is spelled out here rather than taken from
    `PDK_LIB`, because the follow-on moved that entry onto this family's real
    device library. The mutation is a reconstruction of a past state, so it
    names the file that state pointed at."""
    deck_path = tmp_path / "mutated.spice"
    src = _MUTATION_NO_GROUNDING.format(programs=str(_PROGRAMS),
                                        deck=str(deck_path))
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    deck = deck_path.read_text()
    assert "gf180mcuD" in deck, "the deck loads that family's library"
    assert A.SKY130_DEVICES["nmos"] in deck, \
        "and instantiates the OTHER open PDK's device against it — the defect"
    assert ".lib /foss/pdks/gf180mcuD" in deck

    # AMENDED: on the SHIPPED table the mutant's defect cannot be produced at
    # all. It used to be un-producible because the context refused; it is now
    # un-producible because the context resolves to THIS family's own library
    # and THIS family's own devices. Both are asserted, so neither "it refuses"
    # nor "it resolves" alone would satisfy this.
    shipped = A.resolve_deck_context("gf180")
    for token in A.SKY130_DEVICES.values():
        assert token not in repr(shipped.as_json()), token
    if shipped.status == "OK":
        own = A._KNOWN_FAMILIES["gf180"]
        assert shipped.model_lib == own["model_lib"]
        assert shipped.device_map == dict(own["device_map"])
    else:
        assert shipped.device_map == {}


def test_mutation_the_typed_tokens_with_the_library_in_view_are_refused(monkeypatch):
    """MUTATION 2 — only the table's declaration is put back; the grounding
    stays. The library is supplied through the reader seam (no PDK content in
    this repo, and no dependence on an install), and the refusal must name the
    token and the library rather than merely returning an empty map."""
    A.grounding_cache_clear()
    monkeypatch.setitem(A._KNOWN_FAMILIES["gf180"], "device_map",
                        dict(A.SKY130_DEVICES))
    lib = ".lib ss\n.lib tt\n.subckt a_native_device d g s b\n.ends\n.endl\n"
    try:
        c = A.known_family_context("gf180", reader=lambda path: lib)
    finally:
        A.grounding_cache_clear()
    assert c.status == "NEEDS_NATIVE_TEMPLATE", c.as_json()
    assert c.device_map == {}
    joined = " | ".join(c.work_items)
    assert A.SKY130_DEVICES["nmos"] in joined, "the token is named"
    assert A.SKY130_DEVICES["pmos"] in joined
    # the library named is whatever the entry binds NOW — read off the entry
    # rather than typed, so this assertion cannot go stale the way the literal
    # it replaces did when the follow-on named the family's device library.
    assert Path(A._KNOWN_FAMILIES["gf180"]["model_lib"]).name in joined, \
        "the library is named"
    assert "gf180" in joined, "the family is named"


def test_the_grounding_can_derive_a_map_when_the_library_defines_one():
    """The other direction of the derivation: an entry that authors NO map and
    whose library DOES define devices derives its map from that library. This
    is what makes the gf180 entry NOT_AVAILABLE a measurement rather than a
    permanent verdict — point it at a library that defines devices and the map
    appears, without anyone typing a token."""
    fams = {"fam": {"device_map": A.NOT_AVAILABLE,
                    "device_map_not_available": "none authored",
                    "corner_sections": ["tt"],
                    "model_lib": "/nowhere/fam.lib"}}
    lib = (".lib tt\n.subckt fam_nfet_core d g s b\n.ends\n"
           ".subckt fam_pfet_core d g s b\n.ends\n.endl\n")
    A.grounding_cache_clear()
    gr = A.family_library_grounding("fam", lambda p: lib, families=fams)
    A.grounding_cache_clear()
    assert gr["derived_device_map"] == {"nmos": "fam_nfet_core",
                                        "pmos": "fam_pfet_core"}
