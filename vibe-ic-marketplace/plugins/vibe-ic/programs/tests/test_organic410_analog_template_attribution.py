#!/usr/bin/env python3
"""ORGANIC #410 (analog half) — a context must not claim to describe a PDK
whose template it does not carry.

`known_family_context` falls back to sky130's template for an unknown
selector while `family` records the name that was ASKED FOR. The context then
claims to describe a PDK whose devices, corner sections and model lib it does
not have — #389's misattribution in the analog track.

MEASURED, AND NARROWER THAN IT LOOKS. No consumer SIMULATES with the
substituted values: `analog_real_corner_sweep` uses its own `PDK_LIB` on the
`source == "known_family"` fast path, gets None for an unknown selector, and
stops at "pdk lib not reachable"; `analog_mc_yield_run` only calls
`parse_sections`. This is a LATENT trap for the next consumer that reads
`ctx.device_map` at face value, not a wrong simulation happening now — and
these tests say so rather than implying a live defect.

RE-MEASURED when a consumer DID start reading the context on that path. The
gf180 native-template follow-on made the fast path take the family's own
device map, geometry units, corner grid and deck prelude off `ctx` — which is
exactly the change `test_the_consumers_measured_claim_still_holds` existed to
catch, and it caught it. The scope did not widen, because every one of those
reads is GUARDED by `known_family_key(pdk)`: that returns None for a selector
no known family matches, so an unknown selector still gets `devices = None`,
no corner grid, no prelude, and `PDK_LIB.get(pdk)` = None — the same honest
stop. The claim is therefore stronger than it was: it is no longer "no
consumer reads it", it is "the consumer reads it only when the family really
matched", and that guard is what the test below now pins.

CONTROL FLOW IS DELIBERATELY UNCHANGED, pinned below: marking the fallback
with a different `source` would push unknown selectors into the caller's
`else` branch where sky130's `model_lib` WOULD be used — strictly worse than
today's honest stop.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROGRAMS))
import analog_pdk_deck_context as A  # noqa: E402
import analog_real_corner_sweep as S  # noqa: E402


def test_a_known_family_attributes_to_itself():
    for sel in ("sky130", "gf180"):
        c = A.known_family_context(sel)
        assert c.family == sel and c.template_family == sel
        assert "NO authored template" not in c.disclosure


def test_an_unknown_selector_says_which_template_it_carries():
    c = A.known_family_context("ihp-sg13g2")
    assert c.family == "ihp-sg13g2", "the request is still recorded"
    assert c.template_family == "sky130", "and so is what it actually carries"
    assert "NO authored template family for 'ihp-sg13g2'" in c.disclosure
    assert "does NOT describe" in c.disclosure


def test_the_attribution_reaches_the_serialised_form():
    """A truth only present in a Python attribute does not reach the artefact
    a reviewer reads."""
    j = A.known_family_context("totally_made_up").as_json()
    assert j["family"] == "totally_made_up"
    assert j["template_family"] == "sky130"


def test_the_known_family_values_are_unchanged():
    """The paired half. The sky130/gf180 fast path is a documented
    bit-identical regression surface; a fix that moved those values would
    trade a latent misattribution for a live behaviour change."""
    sky = A.known_family_context("sky130")
    assert sky.device_map == dict(A._KNOWN_FAMILIES["sky130"]["device_map"])
    assert sky.model_lib == A._KNOWN_FAMILIES["sky130"]["model_lib"]
    gf = A.known_family_context("gf180")
    assert gf.model_lib == A._KNOWN_FAMILIES["gf180"]["model_lib"]
    assert gf.corner_sections == list(
        A._KNOWN_FAMILIES["gf180"]["corner_sections"])
    # read off the table, never typed: these two open PDKs do NOT share a
    # corner vocabulary, and typing one list for both is the assumption that
    # produced `section definition tt not found`.
    assert sky.corner_sections != gf.corner_sections


def test_the_source_still_reads_known_family_for_an_unknown_selector():
    """Control flow must NOT change. `analog_real_corner_sweep` branches on
    `source == "known_family"` BEFORE it looks at status; sending an unknown
    selector down the other branch would make it use sky130's model_lib
    instead of stopping at "pdk lib not reachable"."""
    assert A.known_family_context("ihp-sg13g2").source == "known_family"


def test_the_consumers_measured_claim_still_holds():
    """The premise of the narrow scope. AMENDED after it fired, which is what
    it was for: a consumer now DOES read the context on the fast path, so the
    premise is re-stated as the guard that keeps the scope narrow instead of
    as the absence of the read.

    BEHAVIOURAL, not textual. The first form matched source text inside a
    fixed 700-character window; both times it went red the thing that had
    moved was a COMMENT, so it was measuring formatting. The binding is now a
    function, and this drives it — a guard that is exercised cannot be
    satisfied by wording."""
    ctx = A.known_family_context("totally_made_up")
    assert ctx.source == "known_family", "the fallback still comes down here"
    assert ctx.device_map, "and it still CARRIES the fallback template's map"
    got = S.known_family_deck_inputs(ctx, "totally_made_up")
    # …none of which reaches the deck emitter for a selector nothing matched
    assert got["family_key"] is None
    assert got["pdk_lib"] is None, "so the caller stops at 'not reachable'"
    assert got["devices"] is None
    assert got["device_terminals"] is None
    assert got["device_geometry_units"] is None
    assert got["deck_prelude"] == []
    assert got["corner_sections"] is None
    assert got["companion_sections"] is None
    assert got["process_corners"] is None


def test_a_matched_family_does_get_its_own_template_through():
    """The other direction of the same guard: for a selector that DOES match a
    known family — in either published spelling — every value comes through,
    and it is that family's own. Without this the guard could be satisfied by
    never passing anything at all."""
    for sel, key in (("sky130", "sky130"), ("sky130A", "sky130"),
                     ("gf180", "gf180"), ("gf180mcuD", "gf180")):
        ctx = A.known_family_context(sel)
        got = S.known_family_deck_inputs(ctx, sel)
        entry = A._KNOWN_FAMILIES[key]
        assert got["family_key"] == key, sel
        assert got["pdk_lib"] == entry["model_lib"], sel
        assert got["devices"] == ctx.device_map, sel
        assert got["corner_sections"] == list(entry["corner_sections"]), sel
        assert got["typ_section"] == ctx.typ_section, sel
        assert got["deck_prelude"] == list(entry.get("deck_prelude") or []), sel


def test_an_unknown_selector_matches_no_known_family():
    """The guard's own predicate, in both directions — the property the text
    assertion above depends on. Every published spelling of a known family
    matches it; a selector that is not one matches nothing, so the fallback
    template's values can never be handed to a consumer as that PDK's."""
    for sel in ("sky130", "sky130A", "gf180", "gf180mcuD"):
        assert A.known_family_key(sel) in A._KNOWN_FAMILIES, sel
    for sel in ("ihp-sg13g2", "totally_made_up", ""):
        assert A.known_family_key(sel) is None, sel
