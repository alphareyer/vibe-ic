#!/usr/bin/env python3
"""vibe-ic#2139 — ONE PDK per deck, and ONE spelling per PDK.

WHAT WAS MEASURED, on c54016beddc3 / tree c5047fa97cf0, 8HD-4, in the pinned
canonical EDA image (`sha256:1463dac5…`, label 0.3.48):

A project that declares NO L19 asked for one open PDK. `resolve_pdk_context`
had no authored deck template for it, took ANOTHER open PDK's model library
from the fall-back table under the requested name, and then filled the roles
that library does not cover from the REQUESTED family's registry entry. The
emitted deck loaded one process and instantiated the other's devices:

    _provenance: model_lib=/foss/pdks/sky130A/…/sky130.lib.spice section=tt
    xr_ib   vdd nbias vss rhigh    w=0.5 l=31.7117      (4 instances)
    xc_…    …             cap_cmim w=10  l=28.5292      (90 instances)

`analog_a3_netlist_emit` said `1 netlist(s) emitted and verified`, rc 0.
ngspice said

    Error: unknown subckt: xdut.xr_ib vdd xdut.nbias 0 rhigh w=0.5 l=31.7117
        in line no. 34 from file ./blk_ds.sp
        Simulation interrupted due to error!

and simulated nothing.

SECOND HALF, SAME RESOLVER. Four readers of one registry each carried their
own matcher and the four disagreed, so the punctuation-free spelling
`analog_pdk_availability.resolve_pdk` ITSELF PRODUCES resolved in none of the
other three:

    selector      _registry_entry  resolve_family  device_map  canonical
    ihp-sg13g2    'ihp-sg13g2'     'ihp-sg13g2'    12 roles    ihp-sg13g2
    ihpsg13g2     None             None            0 roles     ihp-sg13g2
    sg13g2        None             'ihp-sg13g2'    0 roles     ihp-sg13g2

Measured end to end: `--pdk <hyphenated>` was REFUSED
(PDK_NOT_BOUND_BY_BLOCK) because the block bound the unhyphenated form, and
`--pdk <unhyphenated>` was then refused by A2 (ENTRY_REQUIREMENTS_NOT_MET) on
four measured process constants the registry entry states in full — because
the entry could not be found at all. A user who follows the first refusal's
hint lands on the second.

HOW THIS FILE IS BUILT, AND WHY. Every test below had to be able to RUN
against the unfixed program, so that its red says the program gave the WRONG
ANSWER rather than that a name it needs is missing. So the new module is
imported defensively, the new constants are read through `getattr` with the
value they will have, and nothing at module scope depends on the fix. Three
tests do exercise the new predicate directly and can only red by absence on
the base; they are named as such in their own docstrings, and they are not
the ones the finding rests on.

No chip, node, SKU or vendor codename appears here. The only PDK names are the
three OPEN ones already published in `programs/pdk_registry.json`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import _analog_producer_fixture as F
import _plugin_tree  # noqa: F401 — puts programs/ on sys.path

import analog_a3_netlist_emit as A3
import analog_pdk_availability as APA
import analog_pdk_deck_context as APDC
import pdk_analog_layout_minima as MINIMA
import pdk_device_map as PDM

try:                                       # the module this issue introduces
    import pdk_family_identity as IDENT
except ImportError:                        # the base arm of the control
    IDENT = None

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))

#: The three OPEN PDKs this repo publishes.
OPEN_SKY = "sky130A"
OPEN_GF = "gf180mcuD"
OPEN_IHP = "ihp-sg13g2"

#: The `_KNOWN_FAMILIES` keys those two published names denote. Read from the
#: table so this file cannot drift from what the tree ships.
KEY_SKY = "sky130"
KEY_GF = "gf180"

#: The status the binder takes when a context cannot name one PDK. Read
#: through `getattr` so the module imports on the arm that has no such status.
CROSS = getattr(A3, "PDK_CROSS_BINDING", "PDK_CROSS_BINDING")

#: The two spellings of one family that were refused against each other. The
#: second is what `analog_pdk_availability` reports for the first, spelled
#: here WITHOUT the new module so both arms agree on what is being compared.
IHP_SPELLINGS = (OPEN_IHP, re.sub(r"[^a-z0-9]", "", OPEN_IHP.lower()))

ROLES = ["cap", "nmos", "pmos", "res"]

#: The block the issue's repro builds. Its topology instantiates passives, so
#: it is a shape where the two halves of a context CAN disagree at all — a
#: MOS-only block binds every token from the same declaration.
_SPECS = [
    {"name": "Vout", "target": 1.8, "unit": "V"},
    {"name": "Vin", "target": 3.0, "unit": "V"},
    {"name": "Vref", "target": 0.9, "unit": "V"},
    {"name": "Reff", "target": 50000.0, "unit": "ohm"},
    {"name": "ENOB", "target": 14.0, "unit": "bit"},
    {"name": "fclk", "target": 1.0, "min": 0.1, "max": 1.2, "unit": "MHz"},
    {"name": "OSR", "target": 256.0, "unit": "-"},
    {"name": "Order", "target": 2.0, "unit": "-"},
    {"name": "Vdd (core)", "target": 1.2, "unit": "V"},
    {"name": "Vin (diff)", "target": 1.0, "unit": "V"},
]


def _project(root: Path, declared: str | None = None) -> Path:
    blocks = [F.block("blk_ds", "delta_sigma", specs=list(_SPECS))]
    p = F.make_project(root, blocks)
    if declared is not None:
        (p / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json").write_text(
            json.dumps({"fields": {"pdk_target": declared}}, indent=2),
            encoding="utf-8")
    return p


def _bind(selector: str, roles=None) -> dict:
    """The binder's answer for `selector`, with NO project and NO container —
    the exact shape the issue reproduces (nothing to resolve a target from)."""
    return A3.resolve_pdk_context(Path("/nonexistent-project"), selector, "",
                                  list(roles or ROLES))


def _cross(ctx: dict) -> list:
    return list(ctx.get("cross_pdk_bindings") or [])


# ── the repro, and the refusal that replaces it ───────────────────────────

def test_the_repro_project_is_the_shape_the_issue_describes(tmp_path):
    """FIXTURE GUARD — green on both arms. If the producers stop emitting a
    topology with passive roles for this block, the defect's shape is gone and
    every assertion below is testing nothing."""
    p = _project(tmp_path / "shape")
    assert F.run_prog(F.A1, p).returncode == 0
    assert F.run_prog(F.A2, p, "--pdk", OPEN_IHP).returncode == 0
    ir = json.loads((p / "phase3/analog/blk_ds/topology.json").read_text())
    roles = {d["role"] for d in ir.get("devices") or []}
    assert {"res", "cap"} <= roles, (
        "the repro block must instantiate passives, or the library and the "
        "tokens cannot come from two different declarations at all")


def test_o1proj_shape_refuses_and_emits_no_netlist(tmp_path):
    """THE ISSUE. No L19, an explicit open-PDK request the fall-back table has
    no template for. Before: rc 0 and a netlist that dies in ngspice. After:
    an honest gap and NOTHING on disk to simulate."""
    p = _project(tmp_path / "o1")
    assert F.run_prog(F.A1, p).returncode == 0
    assert F.run_prog(F.A2, p, "--pdk", OPEN_IHP).returncode == 0
    r3 = F.run_prog(F.A3, p, "--pdk", OPEN_IHP)
    bdir = p / "phase3/analog/blk_ds"
    assert not (bdir / "blk_ds.sp").exists(), \
        "a deck that cannot name one PDK must not reach the disk"
    assert not (bdir / "netlist_provenance.json").exists()
    assert (bdir / "netlist_gap.json").exists()
    assert r3.returncode == 2, (r3.returncode, r3.stderr)


def test_the_gap_carries_the_cross_binding_status_and_the_structured_record(
        tmp_path):
    """The gap must say WHICH finding this is. Flattening it into the older
    NEEDS_NATIVE_TEMPLATE would delete the answer the reader needs."""
    p = _project(tmp_path / "gap")
    F.run_prog(F.A1, p)
    F.run_prog(F.A2, p, "--pdk", OPEN_IHP)
    F.run_prog(F.A3, p, "--pdk", OPEN_IHP)
    gpath = p / "phase3/analog/blk_ds/netlist_gap.json"
    assert gpath.exists(), "no gap was written — the deck was emitted instead"
    gap = json.loads(gpath.read_text())
    assert gap["status"] == CROSS
    rec = gap.get("cross_pdk_bindings") or []
    assert {c["kind"] for c in rec} == {"model_library", "device_token"}, rec
    assert {c["role"] for c in rec if c["kind"] == "device_token"} == \
        {"res", "cap"}, rec


def test_the_refusal_names_the_token_the_library_and_both_pdk_ids():
    """A refusal that does not name what disagreed cannot be acted on."""
    ctx = _bind(OPEN_IHP)
    assert ctx["status"] == CROSS, ctx["status"]
    text = " ".join(ctx["work_items"])
    tokens = [c["element"] for c in _cross(ctx) if c["kind"] == "device_token"]
    assert tokens, "the mix that kills the deck must be named token by token"
    for tok in tokens:
        assert f"`{tok}`" in text
    lib = APDC._KNOWN_FAMILIES[KEY_SKY]["model_lib"]
    assert lib in text, "the library it would have loaded"
    assert f"`{OPEN_IHP}`" in text and f"`{OPEN_SKY}`" in text, \
        "both PDK ids, in their published spelling"


def test_no_default_library_survives_the_refusal():
    """`never falls through to a default library` is a property of what the
    binder RETURNS, not only of what it says. A caller that reads `model_lib`
    without reading `status` must not be handed another PDK's library."""
    ctx = _bind(OPEN_IHP)
    assert ctx["model_lib"] is None, ctx["model_lib"]
    assert ctx["deck_loads"] == []


def test_the_binder_refuses_before_any_simulator_is_reached(tmp_path):
    """The refusal is a binder decision, so it holds on a host with no PDK
    tree and no simulator — the deck never gets far enough to die."""
    p = _project(tmp_path / "nosim")
    F.run_prog(F.A1, p)
    F.run_prog(F.A2, p, "--pdk", OPEN_IHP)
    r3 = F.run_prog(F.A3, p, "--pdk", OPEN_IHP)
    assert "unknown subckt" not in (r3.stderr or "").lower()
    assert not list((p / "phase3/analog/blk_ds").glob("*.sp"))


def test_a_context_that_names_one_pdk_is_not_refused():
    """THE OTHER DIRECTION — green on both arms, deliberately. A check that
    refuses everything is not a check: a published open PDK that HAS an
    authored device template must still bind, under both of its spellings.

    AMENDED BY vibe-ic#2161. This loop used to run over BOTH published open
    PDKs. It could, because the other one's entry declared THIS one's MOS
    tokens as its own device map — so it "bound", and what it bound was the
    wrong process. #2161 measured that end to end (the deck it produced died
    in ngspice) and made that entry refuse by name. Its half of this
    assertion did not disappear: it moved to
    `test_the_family_with_no_authored_template_refuses_by_name` below, which
    asks for MORE than this one did. The "a check that only refuses is not a
    check" guarantee this test exists for is carried by the family that does
    have a template, which is the only family that could ever have carried
    it honestly."""
    for sel in (OPEN_SKY, KEY_SKY):
        ctx = _bind(sel)
        assert ctx["status"] == "OK", (sel, ctx["work_items"])
        assert _cross(ctx) == [], sel
        assert ctx["model_lib"], sel


def test_the_other_open_family_binds_its_own_devices_and_only_its_own():
    """vibe-ic#2161 — the OTHER published open PDK's half of the test above.

    AMENDED, IN THE OPEN, BY THE #2161 FOLLOW-ON, AND STRICTER FOR IT. #2161
    left this family NOT_AVAILABLE and this test pinned that refusal; the
    follow-on authored its native template against the device library its own
    published ngspice directory ships, so the family now RESOLVES. What the
    test asserts is therefore the same property one step further on: not
    "it refuses" but "every token it binds is its own". The invariant that
    actually mattered — not one field may carry the other open PDK's device
    tokens, by any route — is unchanged and still here, and it is now checked
    on a context that carries a FULL map rather than an empty one, which is
    the harder case. The refusal path itself did not go away and is proven
    against a synthetic family in
    `test_gf180_family_has_its_own_native_analog_device_template.py`."""
    for sel in (OPEN_GF, KEY_GF):
        ctx = _bind(sel)
        assert ctx["status"] == "OK", (sel, ctx.get("work_items"))
        assert _cross(ctx) == [], sel
        dctx = APDC.known_family_context(sel)
        assert dctx.device_map, (sel, dctx.device_map)
        blob = repr(dctx.as_json())
        for token in APDC.SKY130_DEVICES.values():
            assert token not in blob, (sel, token)
        # and the roles it reports are this family's own — never the other's.
        for role, model in (ctx.get("role_models") or {}).items():
            assert not model.startswith("sky130_fd_pr__"), (sel, role, model)
        # the library it binds is this family's, and its own root
        _root = A3._registry_entry(OPEN_GF)[1].get("container_path")
        assert _root and Path(ctx["model_lib"]).is_relative_to(Path(_root)), \
            (sel, ctx["model_lib"], _root)


# ── one canonical spelling per PDK ────────────────────────────────────────

def test_every_registry_reader_answers_the_same_family():
    """FOUR readers of ONE registry. They disagreed; a selector that resolves
    for one of them must not silently fail to resolve for another."""
    for spelling in IHP_SPELLINGS + ("sg13g2",):
        assert A3._registry_entry(spelling)[0] == OPEN_IHP, spelling
        assert MINIMA.resolve_family(spelling)[0] == OPEN_IHP, spelling
        assert PDM.device_map(spelling), spelling
    for spelling in (KEY_SKY, OPEN_SKY):
        assert A3._registry_entry(spelling)[0] == OPEN_SKY
        assert MINIMA.resolve_family(spelling)[0] == OPEN_SKY
    for spelling in (KEY_GF, OPEN_GF):
        assert A3._registry_entry(spelling)[0] == OPEN_GF
        assert MINIMA.resolve_family(spelling)[0] == OPEN_GF


def test_the_resolver_reports_the_published_spelling():
    """The spelling `resolve_pdk` REPORTS is the one its own readers resolve.
    This is the loop that closed on itself: it reported a name it could not
    then look up. Driven through the real rung-2 ladder with the directory
    listing INJECTED, so it needs no container and no PDK tree."""
    installed = {"/pdks": ["ihp-sg13g2"],
                 "/pdks/ihp-sg13g2/libs.tech": ["ngspice"],
                 "/pdks/ihp-sg13g2/libs.tech/ngspice": ["models.lib"]}
    for spelling in IHP_SPELLINGS:
        res = APA.resolve_pdk(spelling, pdks_root="/pdks", container="c",
                              lister=lambda p: installed.get(p, []))
        assert res["family"] == OPEN_IHP, (spelling, res["family"])
    unknown = "famzeta_node"
    res = APA.resolve_pdk(unknown, pdks_root="/pdks", container="c",
                          lister=lambda p: installed.get(p, []))
    assert res["family"] == re.sub(r"[^a-z0-9]", "", unknown), \
        "a family the registry does not publish keeps the spelling it had"


def test_both_ihp_spellings_reach_one_context():
    """MEMBERSHIP, not equality of one field: the whole binding must be the
    same object for either spelling."""
    keys = ("status", "registry_family", "model_lib", "role_models",
            "unresolved_roles")
    got = [dict({k: _bind(s)[k] for k in keys},
                cross=_cross(_bind(s))) for s in IHP_SPELLINGS]
    assert got[0] == got[1], IHP_SPELLINGS


def test_a2_admits_the_entry_under_either_spelling(tmp_path):
    """The SECOND refusal the issue describes: A2 refused the unhyphenated
    spelling for carrying no measured process constants, on a family whose
    registry entry states them in full."""
    for i, spelling in enumerate(IHP_SPELLINGS):
        p = _project(tmp_path / f"a2_{i}", declared=spelling)
        assert F.run_prog(F.A1, p).returncode == 0
        r2 = F.run_prog(F.A2, p, "--pdk", spelling)
        assert r2.returncode == 0, (spelling, r2.stderr)
        assert (p / "phase3/analog/blk_ds/topology.json").exists(), spelling


def test_the_published_open_pdk_spellings_reach_their_own_template():
    """`--pdk <published name>` must reach that family's OWN template.

    It did not. The table is keyed on the bare process token, so the spelling
    `pdk_registry.json` publishes for one open PDK missed it and fell through
    to the OTHER open PDK's model library — while `--pdk <bare token>` on the
    same run got the right one. One family, two spellings, two processes."""
    for published, key in ((OPEN_SKY, KEY_SKY), (OPEN_GF, KEY_GF)):
        want = APDC._KNOWN_FAMILIES[key]["model_lib"]
        assert APDC.known_family_context(published).model_lib == want, \
            published
        assert _bind(published)["model_lib"] == want, published


def test_the_two_published_open_pdks_are_both_in_the_known_table():
    """FIXTURE GUARD — green on both arms. Pins the premise of the test
    above: there are exactly two authored template families."""
    assert set(APDC._KNOWN_FAMILIES) == {KEY_SKY, KEY_GF}


def test_the_unknown_selector_fallback_of_organic_410_is_unchanged():
    """#410 pinned the fall-back in place deliberately and this issue did not
    move it — what moved is which selectors REACH it. Green on both arms."""
    c = APDC.known_family_context(OPEN_IHP)
    assert c.source == "known_family"
    assert c.family == OPEN_IHP
    assert c.template_family == KEY_SKY
    assert f"NO authored template family for '{OPEN_IHP}'" in c.disclosure


def test_the_published_families_agree_cases_are_unchanged():
    """FIXTURE GUARD — green on both arms. `families_agree` now delegates to
    the one authority; its published answers must not have moved."""
    for a, b, expect in (
            (OPEN_IHP, "sg13g2", True),
            (OPEN_SKY, KEY_SKY, True),
            (OPEN_GF, KEY_GF, True),
            (OPEN_SKY, "sg13g2", False),
            (OPEN_IHP, "ihp-sg13cmos5l", False),
            ("", "sg13g2", None),
            ("sg13g2", "", None),
            ("ab", "sg13g2", None)):
        assert APA.families_agree(a, b) is expect, (a, b)


# ── the paired half: the published roots must not move ────────────────────

def test_the_published_sky130A_and_gf180mcuD_analog_roots_are_unchanged():
    """MEMBERSHIP, green on both arms and REQUIRED to be. A fix that closed
    the cross-binding by moving what the two published open PDKs DECLARE would
    have traded one wrong answer for another, so every declared population is
    pinned here by name. The one thing that did move for those two — which
    library the PUBLISHED spelling resolves to — is a different assertion, in
    `test_the_published_open_pdk_spellings_reach_their_own_template`, and it
    is a fix, not a drift."""
    #: AMENDED BY vibe-ic#2161, and AGAIN by its follow-on — each time only
    #: where the fix DELIBERATELY moved something, and each time in the open.
    #: Everything else stays pinned by name.
    #:   #2161: the family with no authored device template stopped reporting
    #:     OK with the OTHER open PDK's tokens bound by the deck context, and
    #:     started refusing with its roles bound from its own registry entry.
    #:   the follow-on: that family now has a native template read off its own
    #:     published device library, so it reports OK again — but with ITS OWN
    #:     tokens, all four roles bound by the deck context, and its own corner
    #:     vocabulary. Measured on the branch, both spellings:
    #:     role_models {nfet_03v3, pfet_03v3, ppolyf_u, cap_mim_1f0ff};
    #:     bound_by all four = deck_context; corner_sections ss/typical/ff.
    #: The corner list is now PER FAMILY, because these two open PDKs do not
    #: spell their nominal corner the same way — that difference is the whole
    #: reason the shipped deck used to die at `section definition tt not
    #: found`, so pinning one list for both would re-assert the defect.
    _EXPECT = {
        OPEN_SKY: ("OK", ["ss", "tt", "ff"],
                   {"cap": A3.BOUND_BY_REGISTRY,
                    "nmos": A3.BOUND_BY_DECK_CONTEXT,
                    "pmos": A3.BOUND_BY_DECK_CONTEXT,
                    "res": A3.BOUND_BY_REGISTRY}),
        OPEN_GF: ("OK", ["ss", "typical", "ff"],
                  {"cap": A3.BOUND_BY_DECK_CONTEXT,
                   "nmos": A3.BOUND_BY_DECK_CONTEXT,
                   "pmos": A3.BOUND_BY_DECK_CONTEXT,
                   "res": A3.BOUND_BY_DECK_CONTEXT}),
    }
    for family, key in ((OPEN_SKY, KEY_SKY), (OPEN_GF, KEY_GF)):
        ctx = _bind(key)                     # the bare token: never moved
        want_status, want_corners, want_bound = _EXPECT[family]
        assert ctx["status"] == want_status, (family, ctx["status"])
        assert ctx["model_lib"] == APDC._KNOWN_FAMILIES[key]["model_lib"]
        assert ctx["corner_sections"] == want_corners, family
        assert sorted(ctx["role_models"]) == ROLES, family
        assert ctx["unresolved_roles"] == [], family
        assert ctx["role_model_election"]["bound_by"] == want_bound, family
        # the registry side of the root: the declared populations, by NAME.
        name, entry = A3._registry_entry(family)
        assert name == family
        assert entry.get("device_models"), family
        assert MINIMA.layout_minima(family)[0] == family
        assert MINIMA.layout_maxima(family)[0] == family


# ── the positive direction, hermetically ──────────────────────────────────

def _staged_family_lib(root: Path, family: str, roles=None) -> tuple:
    """A rung-1 project-staged family: one sectioned model lib on disk whose
    `.subckt` names carry this family's own prefix. No container, no PDK tree,
    no registry entry — so the binder's answer is a property of the tree
    alone."""
    tokens = {r: f"{family}_{r}_dev" for r in (roles or ROLES)}
    body = "\n".join(f".subckt {d} d g s b w=1 l=1\n.ends"
                     for d in tokens.values())
    text = "* synthetic model lib\n"
    for sec in ("ss", "tt", "ff"):
        text += f".lib {sec}\n{body}\n.endl\n"
    lib = root / "input" / "pdk" / "spice" / f"{family}_models.lib"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(text, encoding="utf-8")
    res = {"available": True, "source": "project_custom_pdk",
           "family": family, "target": family, "spice_libs": [str(lib)]}
    return res, str(lib), tokens


def test_a_declared_pdk_binds_its_own_library_and_its_own_tokens(tmp_path):
    """THE POSITIVE DIRECTION, hermetically — green on both arms, and that is
    the point: a check that only ever refuses is not a check. The family, its
    lib and its devices are all staged in the project, so the answer depends
    on nothing outside the tree.

    The container-installed positive — the issue's own project resolving to
    the installed PDK's libs and devices, and the deck then CONVERGING in
    ngspice where it used to die on `unknown subckt` — is measured in
    `evidence/ACCEPTANCE_real_simulator.txt` on the lane host."""
    res, lib, tokens = _staged_family_lib(tmp_path, "famcz")
    ctx = A3.resolve_pdk_context(tmp_path, "famcz", "", list(ROLES),
                                 resolution=res)
    assert ctx["status"] == "OK", ctx["work_items"]
    assert _cross(ctx) == []
    assert ctx["model_lib"] == lib
    assert ctx["role_models"] == tokens, ctx["role_models"]


# ── the predicate itself, both ways ───────────────────────────────────────
#
# These three call the function this issue introduces, so on the base arm of
# the control they red by ABSENCE, not by a wrong answer. They are here for
# what they pin going FORWARD — that the rule fires on exactly one element and
# stays silent on an unaskable comparison — and the finding does not rest on
# them.

def test_the_same_declaration_binds_the_library_and_the_tokens(tmp_path):
    """Same library, same tokens: nothing is refused. Move ONE token to
    another family and that token — and only that token — is named."""
    _res, lib, tokens = _staged_family_lib(tmp_path, "famcz")
    bound = {r: A3.BOUND_BY_DECK_CONTEXT for r in tokens}
    assert A3.cross_pdk_bindings("famcz", "famcz", lib, tokens, bound,
                                 "famcz") == []
    moved = dict(bound, res=A3.BOUND_BY_REGISTRY)
    got = A3.cross_pdk_bindings("famcz", "famcz", lib, tokens, moved, "famqq")
    assert [c["role"] for c in got] == ["res"], got
    assert got[0]["element"] == tokens["res"]
    assert got[0]["element_pdk"] == "famqq"
    assert got[0]["model_lib_pdk"] == "famcz"


def test_the_library_is_checked_against_the_name_the_context_carries(tmp_path):
    """The other disagreement: every token may agree with the library and the
    context still be wrong, because the library is not the family the context
    NAMES. That is the fall-through, and it is refused on its own."""
    _res, lib, tokens = _staged_family_lib(tmp_path, "famcz")
    bound = {r: A3.BOUND_BY_DECK_CONTEXT for r in tokens}
    got = A3.cross_pdk_bindings("famqq", "famcz", lib, tokens, bound, None)
    assert [c["kind"] for c in got] == ["model_library"], got
    assert got[0]["named_pdk"] == "famqq"
    assert got[0]["model_lib_pdk"] == "famcz"
    assert lib in got[0]["sentence"]


def test_an_unaskable_comparison_is_not_a_disagreement():
    """`same_family` answers None when the question cannot be asked, and None
    is not False. Refusing on it would charge a design for a comparison
    nobody made."""
    assert IDENT is not None, "the one family matcher is not in this tree"
    assert IDENT.same_family("", "sg13g2") is None
    assert IDENT.same_family("ab", "sg13g2") is None
    assert A3.cross_pdk_bindings("famq", "famq", "/x/y.lib",
                                 {"nmos": "dev_a"},
                                 {"nmos": A3.BOUND_BY_REGISTRY}, None) == []
    assert A3.cross_pdk_bindings("famq", "famq", None, {"nmos": "dev_a"},
                                 {"nmos": A3.BOUND_BY_REGISTRY},
                                 "famz") == [], \
        "no library bound means no library for anything to disagree with"
