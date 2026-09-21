#!/usr/bin/env python3
"""The cell exclusion must be in force BEFORE anything can insert a cell.

vibe-ic#551, established by controlled experiment on ibex (sky130A, 47 846
instances), in vibeic-eda:0.2.45:

    exclusion at ROUTE time, from post_cts.def (probe=61)
        -> [ERROR DRT-0085] Valid access pattern combination not found
    exclusion before CTS,    from placed.def  (probe=0)
        -> 0 probe cells after CTS, DRT-0085 = 0, DRT-0073 = 0

`set_dont_use` governs the optimizer's FUTURE cell pool. Against a DEF that
already contains those cells it is inert — and the run still prints
`DONT_USE_APPLIED: 52 cells`, so the guard reads as working while the route
dies. Measured on the real artefacts:

    floorplan.def  probe=0      placed.def  probe=0      post_cts.def  probe=61

The 61 unroutable instances enter between placement and CTS, which is precisely
the window the exclusion has to precede.

The emitted TCL already gets this right — the block sits after `link_design`
and before `set_wire_rc`, resizing, CTS and routing. NOTHING KEPT IT THERE.
These tests are what makes the ordering a property rather than a coincidence,
because the failure it prevents is silent: no error, no warning, just probe
cells in the DEF and a route that cannot finish.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as P                            # noqa: E402


#: The marker substituted for the exclusion block, so its position is found by
#: identity rather than by matching a command the emitted TCL also contains.
_MARKER = "SET_DONT_USE_MARKER"

#: A complete argument set for the builder. Every block-valued parameter is
#: empty: the ordering asserted here is a property of the TEMPLATE, and a
#: fixture that filled them would be asserting the fixture's own layout.
_KW = dict(
    tech_lef_c="/t.lef", cell_lef_c="/c.lef", macro_lefs_tcl="",
    liberty_c="/l.lib", macro_libs_tcl="", netlist_c="/n.v", top="foo",
    sdc_c="/s.sdc", dont_use_block=_MARKER + "\n", metal_prefix="met",
    die_w=100, die_h=100, core_pad=10, core_w=80, core_h=80, site="unit",
    out_dir_c="/out", tapcell_block="", pdn_block="", util=0.45,
    spare_protection_tcl="", spare_postfix_tcl="", clk_buf="", clk_buf_root="",
    routing_constraint_tcl="", pg_cleanup_block="", spef_repair_block="",
    antenna_repair_block="", filler_block="", spef_repair_estimate_block="",
)

#: The steps that can insert a cell. `set_dont_use` governs the optimizer's
#: future pool, so each must run AFTER the exclusion is in force.
_INSERTERS = ("clock_tree_synthesis", "repair_design", "repair_timing",
              "global_route", "detailed_route", "detailed_placement")


# ── A MENTION IS NOT AN INVOCATION ────────────────────────────────────────────
#
# MEASURED on live main a9b98b6bf (lane icslot11): this file asserted the
# ordering with a plain substring search, and it went red reporting
# "`global_route` appears BEFORE the cell exclusion" at offset 496. Nothing
# routes there. The template now opens with a ROUTE-GUIDE DISCIPLINE PROLOGUE
# that WRAPS those two commands:
#
#     if {[llength [info commands detailed_route]] == 0 || ... } { ... }
#     rename global_route _vibeic_real_global_route
#     proc   global_route {args} { ... }
#     rename detailed_route _vibeic_real_detailed_route
#     proc   detailed_route {args} { ... }
#
# Every pre-marker occurrence is introspection, a rename, a proc HEADER, a
# diagnostic string, or the renamed original `_vibeic_real_*`. A `proc` body is
# not executed where it is written, and installing a wrapper inserts no cell —
# so #551's invariant was never violated. What broke is the INSTRUMENT: it could
# not tell "this step runs here" from "this command's name appears here".
#
# THE RULE IS CONSERVATIVE ON PURPOSE, and this is the load-bearing choice. The
# obvious rule — "an invocation is the command in Tcl command position, i.e. the
# first word of the line" — is WRONG and I measured it before rejecting it:
# almost every real call in this template is `if {[catch {cmd ...}]}`, so that
# rule recognises 1 of 33 real invocations and would have turned a guard into a
# decoration. Errors here must fall on the STRICT side: a mention miscounted as
# an invocation only makes the test harsher, while a missed invocation silently
# removes the thing #551 exists to prevent. So this excludes ONLY the contexts
# that PROVABLY cannot call, and counts everything else.
_QUOTED = re.compile(r'"[^"\n]*"')


def _invokes(line: str, cmd: str) -> bool:
    """Does this template line CALL `cmd`? Conservative: see the note above."""
    text = _QUOTED.sub(" ", line)                    # diagnostics are not code
    text = text.replace(f"_vibeic_real_{cmd}", " ")  # the renamed original
    if "info commands" in text:                      # introspection
        return False
    head = text.strip().split(" ")[0] if text.strip() else ""
    if head in ("rename", "proc"):                   # renaming / defining
        return False
    return re.search(r'(?<![\w:])' + re.escape(cmd) + r'(?![\w:])',
                     text) is not None


def _invocation_lines(template: str, cmd: str):
    """`(before, after)` line indices that invoke `cmd`, split at the marker."""
    marker_at = template.index(_MARKER)
    before, after, offset = [], [], 0
    for index, line in enumerate(template.split("\n")):
        if _invokes(line, cmd):
            (before if offset < marker_at else after).append(index)
        offset += len(line) + 1
    return before, after


def _pnr_template() -> str:
    """The TCL the runner actually emits, comments stripped.

    COMMENTS STRIPPED. Every one of the six step names appears in the prose
    around this block — `# … governs global_route / detailed_route / the
    antenna repair loop / CTS …` and five more — so a naive scan reported all
    six as "before the exclusion" when not one of them was. That is the same
    mistake I made a day earlier in vibeic-eda, where `actions/runs` in a
    comment read as a reimplementation of the API call it was documenting.

    A check that cannot tell documentation from code has to be weakened the
    first time someone documents something, and then it means nothing.

    v1.9.0 — this used to read a 4000-character WINDOW of the source file
    around the `{dont_use_block}` placeholder. That proxy was wrong in BOTH
    directions and the two errors hid each other:

      false FAIL   the window reaches back above `return f\"\"\"` into the
                   Python that BUILDS the template, where a line such as
                   `_repair_design_margin_tcl("repair_design_pl")` is a
                   builder call, not an emitted step. The suite went red on
                   it with the emitted order entirely correct.
      false PASS   `repair_design` is not in the f-string at all — it is
                   injected through `{_rd_margin_placement}`. Anchoring the
                   window to the template instead would have made that name
                   absent, and `find(...) == -1` is what this test calls a
                   pass. The strictest-looking of the six assertions would
                   have been the emptiest.

    So the template is now BUILT, not scanned, and `_INSERTERS` is asserted
    present as well as late — an ordering test whose subject is missing
    proves nothing about ordering.
    """
    tcl = P._build_pnr_tcl_text(**_KW)
    kept = []
    for ln in tcl.splitlines():
        s = ln.lstrip()
        # TCL comment lines are not executed and must not be read as steps.
        kept.append("" if s.startswith("#") else ln)
    return "\n".join(kept)


def test_the_exclusion_precedes_every_step_that_can_insert_a_cell():
    """resize, CTS, repair and route must all come after it.

    Ordering asserted against the TEMPLATE the runner emits, not against a
    description of it — the defect this guards is an edit that moves the block,
    and a test reading prose would not notice.
    """
    t = _pnr_template()
    for later in _INSERTERS:
        before, after = _invocation_lines(t, later)
        assert before == [], (
            f"`{later}` is INVOKED before the cell exclusion, at template "
            f"line(s) {before}. set_dont_use only governs the optimizer's "
            f"future pool, so anything it inserts first is baked into the DEF "
            f"and the exclusion is inert against it (vibe-ic#551: 61 probe "
            f"cells, DRT-0085, route never finishes)")
        # THE DENOMINATOR, so the assertion above cannot pass by vacuity. A
        # template that stopped calling a step at all would satisfy "nothing
        # before the marker" while quietly removing the step this ordering is
        # about — which is the one way this guard could rot into a decoration.
        assert after, (
            f"`{later}` is never invoked anywhere in the template, so the "
            f"ordering assertion above held over an empty population")


def test_every_step_this_orders_is_actually_in_the_emitted_tcl():
    """The positive control for the assertion above.

    `find(step, 0, here) == -1` is satisfied by a step that never appears, so
    without this the ordering test grades an emitter that emits nothing. It is
    not hypothetical: `repair_design` reaches the TCL only through
    `{_rd_margin_placement}`, so it IS absent from the raw f-string, and the
    earlier source-scanning version of this file would have passed vacuously
    on it the moment its window was tightened.
    """
    t = _pnr_template()
    here = t.index(_MARKER)
    for step in _INSERTERS:
        assert t.find(step, here) != -1, (
            f"`{step}` is not in the emitted TCL at all, so ordering it "
            f"against the exclusion asserts nothing")


def test_the_exclusion_follows_link_design():
    """Before `link_design` there is no library for `set_dont_use` to act on."""
    t = _pnr_template()
    assert t.index("link_design") < t.index(_MARKER)


def test_the_exclusion_reaches_the_template_at_all():
    """A block computed and never interpolated is the wiring failure that
    version of this bug would take: `_dont_use_tcl` runs, the log says nothing,
    and no cell is excluded."""
    src = pathlib.Path(P.__file__).read_text()
    assert "dont_use_block = _dont_use_tcl(pdk)" in src
    assert "{dont_use_block}" in src, "computed and never emitted"
    assert "dont_use_block=dont_use_block" in src, "never passed to the emitter"


def test_the_emitted_exclusion_covers_the_families_that_broke_the_route():
    """The two families in sky130A's own pnr_excluded.cells, plus the fallback.

    Read from the PDK at run time; the fallback is what fires when the PDK
    ships no such file, which is the state that produced #551's route failure
    on an image whose PDK had renamed the directory (openlane -> librelane).
    """
    pdk = P.PdkConfig(name="sky130A", liberty="x", tech_lef="x", cell_lef="x",
                      cell_gds="x", site="unithd", drc_deck="x",
                      pnr_exclude_cell_file="/foss/pdks/sky130A/libs.tech/"
                                            "librelane/sky130_fd_sc_hd/"
                                            "pnr_excluded.cells")
    tcl = P._dont_use_tcl(pdk)
    for fam in ("probe", "lpflow"):
        assert fam in tcl, f"the {fam} family is not in the fallback"
    assert "librelane" in tcl and "openlane" in tcl, \
        "both PDK layouts must be globbed — the rename is what broke this once"
    assert "DONT_USE_SKIPPED" in tcl, \
        "a run that excluded nothing must say so rather than look applied"


# ── the INSTRUMENT, pinned both ways ──────────────────────────────────────────
#
# The ordering assertion above is only as good as `_invokes`, and `_invokes` is
# the part this lane had to replace. A rule that is too loose reports a defect
# that is not there (the red that opened this work); one that is too tight
# removes the guard silently. Both directions are therefore cases of their own,
# over the SHAPES the shipped template actually contains rather than invented
# ones — each string below is copied from the emitted Tcl.
_NOT_AN_INVOCATION = (
    'if {[llength [info commands detailed_route]] == 0 || '
    '[llength [info commands global_route]] == 0} {',
    'rename global_route _vibeic_real_global_route',
    'proc global_route {args} {',
    '    return [uplevel 1 _vibeic_real_global_route $args]',
    '      if {[catch {_vibeic_real_global_route} _rgd_e]} {',
    '        puts "ROUTE_GUIDES_UNAVAILABLE: global_route returned cleanly"',
)

_IS_AN_INVOCATION = (
    'global_route',
    '  global_route',
    'if {[catch {global_route} _e]} { puts "X" }',
)


@pytest.mark.parametrize("line", _NOT_AN_INVOCATION)
def test_a_mention_is_not_read_as_an_invocation(line):
    """Introspection, a rename, a proc HEADER, the renamed original, and a
    diagnostic string all NAME the command without calling it. Installing a
    wrapper inserts no cell, so none of these may trip the ordering guard."""
    assert _invokes(line, "global_route") is False, line


@pytest.mark.parametrize("line", _IS_AN_INVOCATION)
def test_a_real_call_is_read_as_an_invocation(line):
    """The direction that matters more. `if {[catch {cmd ...}]}` is how almost
    every step in this template is actually called — 32 of the 33 real
    invocations — so a rule keyed on Tcl command position would recognise
    nearly none of them and the guard would become a decoration."""
    assert _invokes(line, "global_route") is True, line


def test_the_denominator_can_actually_fail():
    """`assert after` is the anti-vacuity half, and it needs a proven detector:
    over a template that never calls the command, `_invocation_lines` must
    report an empty `after` rather than silently satisfying the ordering."""
    template = f"read_lef /t.lef\n{_MARKER}\ndetailed_placement\n"
    before, after = _invocation_lines(template, "global_route")
    assert (before, after) == ([], [])
    before, after = _invocation_lines(template, "detailed_placement")
    assert before == [] and after, (before, after)
