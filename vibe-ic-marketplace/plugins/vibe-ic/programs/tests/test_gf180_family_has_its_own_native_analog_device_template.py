#!/usr/bin/env python3
"""The gf180 family gets its OWN native analog device template — the #2161
follow-on, so the family stops reading NOT_AVAILABLE and starts simulating.

WHAT #2161 LEFT, AND WHY. #2161 removed a borrowed device map: the entry had
typed the OTHER open PDK's MOS tokens as its own, so `--pdk gf180` bound this
family's model library and instantiated foreign devices against it. The entry
was left NOT_AVAILABLE because the file it named as the family's model library
defines nothing — 0 `.subckt`, 0 `.model`, 0 corner sections. That measurement
was right about the FILE and wrong about the FAMILY: the device library is a
sibling file in the SAME published ngspice directory, and the entry now names
it. Nothing was moved, re-pointed or copied into this repo.

MEASURED, host 8HD-3, pinned image `vibeic-eda@sha256:89a8fd72…` (0.3.49),
real ngspice, on the family's own published root:

  * the device library defines 71 device subckts in its closure and the
    top-level sections `typical`, `ff`, `ss`, `fs`, `sf`. It defines NO section
    called `tt` — which IS #2161's `section definition tt not found`, in the
    corner vocabulary rather than in the device map. The nominal corner of this
    family is spelled `typical`.
  * its MOS subckts declare METRIC geometry (`w=1e-5 l=2.8e-7`).
  * its MOS subckt bodies reference a statistical switch the device library
    does not define; the family's global switch deck does. Without that file
    ngspice stops at `Undefined parameter [sw_stat_mismatch]`, exit(1), before
    any analysis:

        A3/A4 deck, device library only    Undefined parameter [...]  rc=1
        the same deck, switch deck first   MEAS vout= 1.79224         rc=0

  * it splits its corners BY DEVICE CLASS inside ONE file: the process corner
    carries the MOS models, while the resistor and the MIM capacitor have their
    own `res_<corner>` / `mimcap_<corner>` sections. A deck that loads only the
    process corner elaborates its transistors and stops at
    `unknown subckt: … ppolyf_u`.
  * its passives name their own geometry (`r_width`/`r_length`,
    `c_width`/`c_length`) and DEFAULT those formals to bare `w`/`l`, which no
    deck defines. Emitting `w=` there passes a formal the subckt does not
    declare and leaves the real geometry at an undefined global.

END TO END with the real simulator, A1→A4 on a gf180 project: 9 of 9 PVT
corners really simulated (`full_pvt_sweep_executed: true`, 0 derived, 0 not
completed), vout 1.19240…1.19595 V against the L5 target 1.2 V.

THE SILENT ONE. `.option scale=1u` + a bare `w=8` DOES reach this family's
primitive geometry correctly, so the operating point is the same either way —
but the subckt's OWN mismatch arithmetic consumes the RAW `w`, so with the
statistical switch on, the mis-scaled deck reports ZERO device mismatch.
Measured, 8 seeds each:

    metric geometry     vout 1.78311 … 1.80377  (real spread)
    the scaled idiom    vout 1.79224 on all 8   (mismatch silently zero)

That is the failure mode that still simulates, so it gets its own test below.

The only PDK names here are the OPEN ones `programs/pdk_registry.json` already
publishes. No chip, node, SKU or vendor codename appears.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROGRAMS))
import analog_pdk_deck_context as A            # noqa: E402
import analog_real_corner_sweep as S           # noqa: E402

KEY = "gf180"
OTHER = "sky130"
ENTRY = A._KNOWN_FAMILIES[KEY]

#: The family's published root exists only where the PDK is installed, which is
#: inside the image. A host without it measures NOTHING — never "it is empty".
_HAVE_PDK = Path(ENTRY["model_lib"]).is_file()
_HAVE_NGSPICE = shutil.which("ngspice") is not None
_needs_pdk = pytest.mark.skipif(
    not _HAVE_PDK, reason=f"this family's published root is not installed here "
                          f"({ENTRY['model_lib']} absent) — NOT MEASURED")
_needs_sim = pytest.mark.skipif(
    not (_HAVE_PDK and _HAVE_NGSPICE),
    reason="the PDK root and/or ngspice are not present here — NOT MEASURED")


# ── direction 1: the entry names ONLY what its own library defines ───────────

@_needs_pdk
def test_every_token_and_section_the_entry_declares_is_defined_by_its_library():
    """The acceptance #2161 built. If any of these lists is non-empty the
    template is wrong — that is the grounding doing its job, not an obstacle."""
    A.grounding_cache_clear()
    gr = A.family_library_grounding(KEY)
    assert gr["measured"] is True
    assert gr["undefined_tokens"] == [], gr["undefined_tokens"]
    assert gr["undefined_sections"] == [], gr["undefined_sections"]
    assert gr["undefined_companion_sections"] == [], \
        gr["undefined_companion_sections"]
    assert gr["prelude_unreadable"] == [], gr["prelude_unreadable"]
    assert gr["undefined_params"] == [], gr["undefined_params"]
    assert gr["n_subckts"] > 0


@_needs_pdk
def test_the_context_is_emittable_and_carries_the_family_s_own_values():
    c = A.known_family_context(KEY)
    assert c.status == "OK", c.work_items
    assert c.work_items == []
    assert c.device_map == dict(ENTRY["device_map"])
    assert c.typ_section == "typical"
    assert c.model_lib == ENTRY["model_lib"]
    assert c.deck_prelude == list(ENTRY["deck_prelude"])
    assert set(c.device_geometry_units.values()) >= {"metric"}
    assert c.device_geometry_params["res"] == {"w": "r_width",
                                               "l": "r_length"}
    assert c.device_geometry_params["cap"] == {"w": "c_width",
                                               "l": "c_length"}
    assert c.device_geometry_params["nmos"] == {"w": "w", "l": "l"}


@_needs_pdk
def test_this_family_does_not_spell_its_nominal_corner_the_other_family_s_way():
    """#2161's simulator message, at the level it actually lives: the section
    `tt` does not exist in this library, and the entry no longer claims it."""
    txt = A._default_reader(ENTRY["model_lib"])
    sections = {s.lower() for s in A.parse_sections(txt)}
    assert "typical" in sections
    assert "tt" not in sections, "the corner this family does NOT define"
    assert "tt" not in [s.lower() for s in ENTRY["corner_sections"]]
    typ, grid = A.map_corner_sections(list(ENTRY["corner_sections"]))
    assert typ == "typical"
    assert [g[0] for g in grid] == ["ss", "typical", "ff"]


# ── direction 2: the same claim, made falsely, is refused BY NAME ────────────
# Every mutation below is supplied through the reader seam, so it states its
# own input and runs on a host with no PDK installed at all.

_LIB_OK = (
    ".lib typical\n.lib 'own.lib' fets\n.endl\n"
    ".lib ss\n.lib 'own.lib' fets\n.endl\n"
    ".lib ff\n.lib 'own.lib' fets\n.endl\n"
    ".lib res_typical\n.endl\n.lib res_ss\n.endl\n.lib res_ff\n.endl\n"
    ".lib fets\n"
    ".subckt fam_nfet d g s b w=1e-5 l=2.8e-7\n"
    ".param mis=1\n"
    "m0 d g s b fam_nfet_m delvto='mis*sw_stat_mismatch' w=w l=l\n"
    ".ends fam_nfet\n"
    ".subckt fam_pfet d g s b w=1e-5 l=2.8e-7\n"
    "m0 d g s b fam_pfet_m w=w l=l\n"
    ".ends fam_pfet\n"
    ".subckt fam_res 1 2 3 r_length=l r_width=w\n"
    "r0 1 2 r='r_length/r_width'\n"
    ".ends fam_res\n"
    ".endl\n")
_SWITCH_DECK = ".param sw_stat_mismatch=0\n"


def _fams(**over):
    entry = {"device_map": {"nmos": "fam_nfet", "pmos": "fam_pfet",
                            "res": "fam_res"},
             "corner_sections": ["ss", "typical", "ff"],
             "companion_corner_groups": ["res"],
             "companion_sections": [],
             "model_lib": "/nowhere/fam.lib",
             "deck_prelude": ["/nowhere/switches.deck"]}
    entry.update(over)
    return {"fam": entry}


def _reader(lib=_LIB_OK, switches=_SWITCH_DECK):
    def _rd(path):
        if path == "/nowhere/fam.lib":
            return lib
        if path == "/nowhere/switches.deck":
            return switches
        return None
    return _rd


def _ground(fams, rd):
    A.grounding_cache_clear()
    try:
        return A.family_library_grounding("fam", rd, families=fams)
    finally:
        A.grounding_cache_clear()


def test_the_control_arm_of_every_mutation_below_is_clean():
    """A refusal test proves nothing unless the unmutated input passes."""
    gr = _ground(_fams(), _reader())
    assert gr["measured"] is True
    assert gr["undefined_tokens"] == []
    assert gr["undefined_sections"] == []
    assert gr["undefined_companion_sections"] == []
    assert gr["undefined_params"] == []
    assert gr["prelude_unreadable"] == []


def test_mutation_a_corner_the_library_does_not_define_is_refused_by_name():
    gr = _ground(_fams(corner_sections=["ss", "tt", "ff"]), _reader())
    assert gr["undefined_sections"] == ["tt"], gr["undefined_sections"]


def test_mutation_a_companion_section_missing_at_one_corner_is_named():
    """The companion is as load-bearing as the corner: a grid that loads the
    MOS corner and leaves a device class behind stops at `unknown subckt`."""
    lib = _LIB_OK.replace(".lib res_ss\n.endl\n", "")
    gr = _ground(_fams(), _reader(lib=lib))
    assert gr["undefined_companion_sections"] == [("ss", "res_ss")], \
        gr["undefined_companion_sections"]


def test_mutation_removing_the_deck_prelude_names_the_undefined_parameter():
    """Bidirectional, and it is the simulator's own sentence: with the switch
    deck declared the family is clean; with it gone the refusal names the
    parameter and the device that reads it."""
    clean = _ground(_fams(), _reader())
    assert clean["undefined_params"] == []
    gone = _ground(_fams(deck_prelude=[]), _reader())
    assert gone["undefined_params"] == [("fam_nfet", "sw_stat_mismatch")], \
        gone["undefined_params"]


def test_mutation_an_unreadable_declared_prelude_is_not_silently_dropped():
    gr = _ground(_fams(), _reader(switches=None))
    assert gr["prelude_unreadable"] == ["/nowhere/switches.deck"]
    assert gr["undefined_params"] == [("fam_nfet", "sw_stat_mismatch")]


def test_a_passive_default_of_a_formal_the_deck_supplies_is_not_a_requirement():
    """`r_length=l` is only evaluated when the caller passes nothing. The deck
    always passes it, so `l` is not an undefined parameter — while the SAME
    name inside the body would be. Both directions, one library."""
    gr = _ground(_fams(), _reader())
    assert ("fam_res", "l") not in gr["undefined_params"]
    assert gr["geometry_param_names"]["fam_res"] == {"w": "r_width",
                                                     "l": "r_length"}
    body = A.subckt_body(_LIB_OK, "fam_res")
    assert A.subckt_free_params(body, ("r_width", "r_length")) == []
    assert A.subckt_free_params(body, ()) == ["l", "w"]


def test_an_unreadable_library_still_measures_nothing_here():
    gr = _ground(_fams(), lambda p: None)
    assert gr["measured"] is False
    assert gr["undefined_params"] == [] and gr["undefined_sections"] == []
    assert gr["undefined_companion_sections"] == []


# ── direction 3: the geometry-unit read, and the failure that still runs ─────

_OLD_GEOM_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([wl])\s*=\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
    r"\s*([a-zA-Z]*)")


def test_a_metric_default_followed_by_another_parameter_reads_as_metric():
    """The suffix is ATTACHED to the number. Read across whitespace, the NEXT
    parameter's name became the suffix (`('w','1e-5','l')`), a plainly metric
    default was classified UNITLESS, and the `l=` it swallowed was never looked
    at. The mutation restores the old pattern and shows the wrong verdict."""
    decl = ".subckt d1 a b c d w=1e-5 l=2.8e-7 nf=1\n.ends\n"
    assert A.parse_subckt_geometry_units(decl)["d1"] == "metric"
    assert A._GEOM_DEFAULT_RE.findall(" w=1e-5 l=2.8e-7") == [
        ("w", "1e-5", ""), ("l", "2.8e-7", "")]
    assert _OLD_GEOM_RE.findall(" w=1e-5 l=2.8e-7") == [("w", "1e-5", "l")]
    saved = A._GEOM_DEFAULT_RE
    A._GEOM_DEFAULT_RE = _OLD_GEOM_RE
    try:
        assert A.parse_subckt_geometry_units(decl)["d1"] == "unitless", \
            "the mutation must reproduce the wrong verdict, or it proves nothing"
    finally:
        A._GEOM_DEFAULT_RE = saved
    # the other family's idiom is untouched by the fix
    scaled = ".subckt d2 a b c d w=1 l=0.15\n.ends\n"
    assert A.parse_subckt_geometry_units(scaled)["d2"] == "unitless"


def test_a_metric_family_s_deck_carries_metres_and_no_scale_card():
    """The consumer half, on the built-in corner template. Both directions:
    with the units read as metric the geometry is emitted in metres and the
    scale card is gone; with them read as scaled the deck keeps the card and
    the bare number — the arm that still simulates and reports no mismatch."""
    devices = {"nmos": "fam_nfet", "pmos": "fam_pfet"}
    metric, _ = S.render_deck("ldo", "u_ldo", "fam", "/nowhere/fam.lib",
                              "typical", "m_pass", 8, devices=devices,
                              device_geometry_units={"nmos": "metric",
                                                     "pmos": "metric"})
    scaled, _ = S.render_deck("ldo", "u_ldo", "fam", "/nowhere/fam.lib",
                              "typical", "m_pass", 8, devices=devices,
                              device_geometry_units={"nmos": "unitless",
                                                     "pmos": "unitless"})
    assert ".option scale=1u" not in metric
    assert ".option scale=1u" in scaled
    assert "fam_nfet w=8u l=1u" in metric
    assert "fam_nfet w=8 l=1" in scaled
    assert "m='m_pass'" in metric, "a device MULTIPLIER is not a length"


# ── direction 4: the deck loads every section it binds a device from ─────────

@_needs_pdk
def test_the_context_lists_a_companion_section_for_every_corner():
    c = A.known_family_context(KEY)
    assert set(c.companion_sections) == set(ENTRY["corner_sections"])
    for corner, secs in c.companion_sections.items():
        assert secs, corner
        assert f"res_{corner}" in secs and f"mimcap_{corner}" in secs
        for fixed in ENTRY["companion_sections"]:
            assert fixed in secs
    loads = {sec for _lib, sec in c.deck_loads}
    assert "typical" in loads and "res_typical" in loads


def test_the_other_family_declares_no_companion_and_keeps_one_lib_line():
    """The regression surface. A single-section family must keep the empty
    `deck_loads` its consumers branch on, so its emitted deck is unchanged."""
    c = A.known_family_context(OTHER)
    assert A.family_companion_sections(A._KNOWN_FAMILIES[OTHER], "tt") == []
    assert c.deck_loads == []
    assert c.deck_prelude == []


def _design_project(tmp_path, cards):
    proj = tmp_path / "proj"
    d = proj / "phase3" / "analog" / "blk"
    d.mkdir(parents=True)
    (d / "blk.sp").write_text(
        "* blk\n" + "\n".join(cards) + "\n"
        ".subckt blk vdd vss\nr0 vdd vss 1k\n.ends blk\n")
    (d / "tb_blk.sp").write_text(
        "* tb\n.include blk.sp\nxdut vdd 0 blk\nv1 vdd 0 1\n"
        ".control\nop\n.endc\n.end\n")
    return proj


def test_a_same_file_companion_card_moves_to_the_new_corner(tmp_path):
    """A4 owns exactly ONE process-corner card, and a companion in the SAME
    file is moved to that corner's section of its own device class rather than
    left behind at the nominal one."""
    lib = "/nowhere/fam.lib"
    proj = _design_project(tmp_path, [
        f".lib {lib} typical", f".lib {lib} res_typical", f".lib {lib} cap_mim"])
    comp = {"typical": ["res_typical", "cap_mim"],
            "ss": ["res_ss", "cap_mim"], "ff": ["res_ff", "cap_mim"]}
    deck, info = S.build_design_deck(proj, "blk", lib, "ss",
                                     corner_sections=["ss", "typical", "ff"],
                                     companion_sections=comp)
    assert deck is not None, info
    assert f".lib {lib} ss" in deck
    assert f".lib {lib} res_ss" in deck
    assert f".lib {lib} cap_mim" in deck
    assert "res_typical" not in deck, "the companion must not stay behind"
    assert info["lib_cards_restamped"] == 1
    assert info["lib_cards_moved_with_corner"] == 2


def test_two_process_corner_cards_are_still_refused(tmp_path):
    """The shipped invariant this must not loosen: A4 will not guess which of
    two process cards governs."""
    lib = "/nowhere/fam.lib"
    proj = _design_project(tmp_path, [f".lib {lib} typical", f".lib {lib} ss"])
    deck, info = S.build_design_deck(proj, "blk", lib, "ff",
                                     corner_sections=["ss", "typical", "ff"],
                                     companion_sections={})
    assert deck is None
    assert "exactly one is required" in info["reason"]


def test_a_family_that_declares_no_sections_partitions_by_file_as_before(tmp_path):
    """The historical caller passes neither list; the same deck must behave
    exactly as it did — one own card, one companion kept verbatim."""
    lib = "/nowhere/fam.lib"
    proj = _design_project(tmp_path, [f".lib {lib} tt",
                                      ".lib /nowhere/other.lib res_typ"])
    deck, info = S.build_design_deck(proj, "blk", lib, "ss")
    assert deck is not None, info
    assert f".lib {lib} ss" in deck
    assert ".lib /nowhere/other.lib res_typ" in deck
    assert info["lib_cards_restamped"] == 1
    assert info["lib_cards_kept"] == 1


# ── direction 5: the OTHER open PDK is untouched ─────────────────────────────

def test_the_other_open_pdk_context_is_field_for_field_what_it_was():
    c = A.known_family_context(OTHER)
    assert c.status == "OK", c.work_items
    assert c.device_map == dict(A.SKY130_DEVICES)
    assert c.corner_sections == ["ss", "tt", "ff"]
    assert c.typ_section == "tt"
    assert c.process_corners == [("ss", -0.03), ("tt", 0.0), ("ff", 0.03)]
    assert c.device_terminals == {"nmos": 4, "pmos": 4}
    assert c.model_lib == A._KNOWN_FAMILIES[OTHER]["model_lib"]
    assert c.work_items == []


def test_the_other_open_pdk_deck_is_byte_identical_with_or_without_the_context():
    """The deck emitter is now handed that family's own resolved values on the
    known-family path. For a family with no companions, no prelude and no
    metric device that has to be a NO-OP, byte for byte."""
    c = A.known_family_context(OTHER)
    before, _ = S.render_deck("ldo", "u_ldo", OTHER, S.PDK_LIB[OTHER],
                              "tt", "m_pass", 8)
    after, _ = S.render_deck("ldo", "u_ldo", OTHER, S.PDK_LIB[OTHER],
                             "tt", "m_pass", 8, devices=c.device_map,
                             device_terminals=c.device_terminals,
                             device_geometry_units=c.device_geometry_units,
                             deck_prelude=c.deck_prelude)
    assert after == before


# ── direction 6: the real simulator, where the PDK actually is ───────────────

def _emit(tmp_path, corner, prelude, loads, devices, name):
    body = [f"* {name}"]
    body += [f".include {p}" for p in prelude]
    body += [f".lib {lib} {sec}" for lib, sec in loads]
    body += [
        f"v_vdd vdd 0 3.3", "v_vref vref 0 0.9",
        f"xmn_b nbias nbias 0 0 {devices['nmos']} w=2u l=2u",
        "r_ibias vdd nbias 600k",
        f"xmp_pass vout vg vdd vdd {devices['pmos']} w=5u l=0.5u m=8",
        f"xmn_tail ntail nbias 0 0 {devices['nmos']} w=4u l=2u",
        f"xmn1 nd1 vfb  ntail 0 {devices['nmos']} w=8u l=1u",
        f"xmn2 vg  vref ntail 0 {devices['nmos']} w=8u l=1u",
        f"xmp1 nd1 nd1 vdd vdd {devices['pmos']} w=4u l=1u",
        f"xmp2 vg  nd1 vdd vdd {devices['pmos']} w=4u l=1u",
        "cc vg vout 5p", "r1 vout vfb 8k", "r2 vfb 0 8k", "r_load vout 0 1k",
        ".control", "op", 'echo "MEAS vout=" $&v(vout)', ".endc", ".end"]
    p = tmp_path / f"{name}.spice"
    p.write_text("\n".join(body) + "\n")
    return p


def _ngspice(deck: Path):
    r = subprocess.run(["ngspice", "-b", deck.name], cwd=str(deck.parent),
                       capture_output=True, text=True)
    m = re.search(r"MEAS vout=\s*([0-9.eE+-]+)", r.stdout + r.stderr)
    return r.returncode, (float(m.group(1)) if m else None), r.stdout + r.stderr


@_needs_sim
def test_the_family_s_own_corners_all_solve_in_the_real_simulator(tmp_path):
    """The whole point: a deck built from THIS context, at every corner of the
    grid it declares, that ngspice actually solves."""
    c = A.known_family_context(KEY)
    assert c.status == "OK", c.work_items
    seen = {}
    for corner, _off in c.process_corners:
        loads = [(c.model_lib, corner)] + [(c.model_lib, s)
                                           for s in c.companion_sections[corner]]
        deck = _emit(tmp_path, corner, c.deck_prelude, loads, c.device_map,
                     f"solve_{corner}")
        rc, vout, log = _ngspice(deck)
        assert rc == 0, log[-1500:]
        assert vout is not None and 1.5 < vout < 2.1, (corner, vout)
        seen[corner] = vout
    assert set(seen) == {"ss", "typical", "ff"}, seen


@_needs_sim
def test_the_corner_this_family_does_not_define_dies_in_the_simulator(tmp_path):
    """The negative control for the corner map, in the simulator's own words —
    the exact sentence #2161 reported."""
    c = A.known_family_context(KEY)
    deck = _emit(tmp_path, "tt", c.deck_prelude, [(c.model_lib, "tt")],
                 c.device_map, "dead_tt")
    rc, vout, log = _ngspice(deck)
    assert rc != 0 and vout is None
    assert "section definition tt not found" in log


@_needs_sim
def test_without_the_declared_prelude_the_simulator_stops_on_the_parameter(tmp_path):
    """The negative control for the prelude, likewise measured rather than
    asserted."""
    c = A.known_family_context(KEY)
    loads = [(c.model_lib, "typical")] + [(c.model_lib, s)
                                          for s in c.companion_sections["typical"]]
    deck = _emit(tmp_path, "typical", [], loads, c.device_map, "no_prelude")
    rc, vout, log = _ngspice(deck)
    assert rc != 0 and vout is None
    assert "Undefined parameter" in log
    assert "sw_stat_mismatch" in log


# ── the `_NOT_PROSE` claim, RE-MEASURED rather than quoted ───────────────────
#
# `prose_polarity_consulted_check` flags `subckt_geometry_param_names`: it
# matches with a regex and writes the matched text into a record, which is the
# #706/#711 shape exactly. The claim registered against it is that what it reads
# is a `.subckt` card's FORMAL PARAMETER LIST — a grammar in which "not" cannot
# be spelled — and that no sentence reaches the regex, because every input is
# put through `spice_code_only` first.
#
# THAT CLAIM WAS FALSE UNTIL THE STRIP LANDED, and these tests are what measured
# it: without the strip, an English sentence in an inline comment or a quoted
# default moves the published answer in 4 of the 7 positions a sentence can
# physically occupy in this production. So the sweep is re-run here in BOTH
# arms on every test run — the register entry quotes no number this file does
# not re-derive.

import analog_pdk_deck_context as _A          # noqa: E402  (already imported)
import _prose_polarity as _P                  # noqa: E402

#: The vocabulary's own tokens, both tiers. Asserted against `NEGATION_RE`
#: below, so this list cannot silently drift away from the module it claims to
#: be quoting.
_DENIAL_TOKENS = [
    "not", "no", "none", "without", "excluding", "excluded", "never", "non",
    "非", "无", "無", "不", "否",
    "removed", "obsolete", "superseded", "n/a", "inapplicable", "deprecated",
    "no longer", "does not apply",
]

#: The production whose answer the SUFFIX SEARCH decides — a passive that names
#: its geometry after its device class, which is the real gf180 shape. A subckt
#: that declares `w`/`l` directly short-circuits and CANNOT move, so measuring
#: on one would be a zero from an inert fixture.
_CARD = (".subckt fam_res 1 2 3 r_length=l r_width=w dtemp=0\n"
         "r0 1 2 r='r_length/r_width'\n"
         ".ends fam_res\n")
_WANT = {"w": "r_width", "l": "r_length"}

#: Every position a sentence can physically occupy in this production.
_POSITIONS = {
    "*-comment above the card":   lambda b, s: "* " + s + "\n" + b,
    ";-comment on the card":      lambda b, s: b.replace("dtemp=0\n", "dtemp=0 ; " + s + "\n"),
    "$-comment on the card":      lambda b, s: b.replace("dtemp=0\n", "dtemp=0 $ " + s + "\n"),
    "+-continuation comment":     lambda b, s: b.replace("dtemp=0\n", "dtemp=0\n+ $ " + s + "\n"),
    "*-comment inside the body":  lambda b, s: b.replace("r0 ", "* " + s + "\nr0 "),
    "quoted default on the card": lambda b, s: b.replace("dtemp=0\n", "dtemp=0 par='1 ; " + s + "'\n"),
    ";-comment on .ends":         lambda b, s: b.replace(".ends fam_res\n", ".ends fam_res ; " + s + "\n"),
}


def _payload(token):
    """DECLARATION-SHAPED and denial-carrying: it mints two rival geometry
    names, so a reader that sees it either publishes one of them or is left
    unable to choose. Either is a move."""
    return (f"the c_width = 3 is {token} the width, and c_length = 4 is "
            f"{token} the length")


def _sweep(stripped):
    saved = _A.spice_code_only
    if not stripped:
        _A.spice_code_only = lambda t, blank_quoted=False: t
    try:
        moved, trials = [], 0
        for tok in _DENIAL_TOKENS:
            for pos, place in _POSITIONS.items():
                trials += 1
                got = _A.subckt_geometry_param_names(
                    place(_CARD, _payload(tok)), "fam_res")
                if got != _WANT:
                    moved.append((pos, tok))
        return trials, moved
    finally:
        _A.spice_code_only = saved


def test_the_vocabulary_this_sweep_quotes_is_the_module_s_own():
    """A sweep that invents its own denial words proves nothing about the
    vocabulary the gate is defending."""
    for tok in _DENIAL_TOKENS:
        assert _P.NEGATION_RE.search(tok), tok
    assert _A.subckt_geometry_param_names(_CARD, "fam_res") == _WANT


def test_the_not_prose_claim_for_the_subckt_formal_reader_is_falsifiable():
    """BOTH ARMS, re-measured. With the strip no sentence moves the answer;
    with the strip deleted the SAME sentences do — so the zero is a statement
    about what reaches the regex, not about a fixture that could not move."""
    trials, moved = _sweep(stripped=True)
    assert trials == len(_DENIAL_TOKENS) * len(_POSITIONS)
    assert moved == [], moved

    _trials, blind = _sweep(stripped=False)
    assert _trials == trials
    live = sorted({p for p, _ in blind})
    assert blind, "the mutation must reproduce the defect, or it proves nothing"
    assert len(live) >= 4, live
    for expect in (";-comment on the card", "$-comment on the card",
                   "quoted default on the card", "+-continuation comment"):
        assert expect in live, (expect, live)


def test_the_same_names_as_CODE_still_move_the_answer():
    """The negative control the zero above depends on. The strip must remove
    the SENTENCE, not the reader's ability to answer: spliced in as real
    formals, the identical names change what is published."""
    code = _CARD.replace("dtemp=0\n", "dtemp=0 c_width=3 c_length=4\n")
    assert _A.subckt_geometry_param_names(code, "fam_res") != _WANT
    # and a genuine formal on a `+` continuation is still read as code
    cont = _CARD.replace("dtemp=0\n", "dtemp=0\n+ c_width=3\n")
    got = _A.subckt_geometry_param_names(cont, "fam_res")
    assert got.get("l") == "r_length"
    assert "w" not in got, "two names match the suffix — it must refuse, not guess"


def test_the_strip_keeps_offsets_and_lines_so_no_caller_shifts():
    """`spice_code_only` blanks; it must never delete. A caller that walks `+`
    continuations or searches for `.ends` by line would silently mis-slice."""
    for src in (_CARD, "* a\n.subckt x a b ; note\n+ w=1 $ note\n.ends x\n",
                "r0 1 2 r='a;b'\n", ""):
        out = _A.spice_code_only(src)
        assert len(out) == len(src), src
        assert out.count("\n") == src.count("\n"), src
    # a `;` INSIDE a quoted expression is not a comment
    kept = _A.spice_code_only("r0 1 2 r='a;b' w=3\n")
    assert "w=3" in kept, kept
    # an apostrophe inside a COMMENT cannot open a quote that eats live code
    safe = _A.spice_code_only("* it's a note\n.subckt y a b w=1\n.ends y\n")
    assert ".subckt y a b w=1" in safe, safe


@_needs_pdk
def test_the_strip_moves_no_published_answer_on_the_real_open_pdks():
    """The other direction: it removes sentences and NOTHING else. Measured on
    both installed open PDKs — every section, every device, every unit verdict
    and the whole resolved context, with the strip and with it deleted."""
    saved = _A.spice_code_only
    try:
        for fam in (KEY, OTHER):
            lib = _A._KNOWN_FAMILIES[fam]["model_lib"]
            txt = _A._default_reader(lib)
            assert txt is not None
            snaps = {}
            for arm, on in (("stripped", True), ("raw", False)):
                _A.spice_code_only = (saved if on else
                                      (lambda t, blank_quoted=False: t))
                _A.grounding_cache_clear()
                snaps[arm] = (
                    _A.parse_sections(txt),
                    _A.transitive_subckts(lib, txt, _A._default_reader),
                    _A.transitive_geometry_units(lib, txt, _A._default_reader),
                    _A.known_family_context(fam).as_json(),
                )
                _A.grounding_cache_clear()
            assert snaps["stripped"] == snaps["raw"], fam
    finally:
        _A.spice_code_only = saved
        _A.grounding_cache_clear()
