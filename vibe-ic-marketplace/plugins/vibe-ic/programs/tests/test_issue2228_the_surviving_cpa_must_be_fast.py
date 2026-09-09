#!/usr/bin/env python3
"""vibe-ic#2228 — the slow-corner section taught the arm #2081's ruling REJECTED.

WHAT WAS MEASURED, AND BY WHOM
==============================
#2081 measured two rewrites of one full-width datapath and the owner RULED for
the LARGER one, in those words: "The front-door re-run takes the +8.828 ns arm,
not the +4.395 ns one — the smaller arm leaves untouched the [shared final sum]
group that is six of the top ten failing paths". The smaller arm is carry-save
plus a pre-add; the ruled arm is that PLUS a carry-select carry-propagate add
and a shared final sum computed once.

The shipped section taught the smaller arm. Its remedy 1 ended "until two
vectors remain, then do ONE carry-propagate add" and stopped: the words
`carry-select` and `parallel-prefix` did not appear in ANY skill or agent
document in this repository — only inside `arith_ss_corner_risk_check`'s finding
message, which an author reads after the fact if at all.

#2228 then measured the consequence end to end through the front door: an
author who applied the section faithfully — 3:2 compressors, the pre-add, cycle
count held, the rewrite proved two-sidedly — landed on a netlist with DRC 0 and
LVS matching whose sign-off SS corner was VIOLATED, and whose worst path was
twelve chained majority gates: the ONE carry-propagate add the section told the
author to leave, as a full-width ripple.

WHAT THESE TESTS PIN
====================
That the section now carries the third remedy in BOTH places it ships — the
skill an author is handed and the rendered lessons digest every author receives
— and that it stays chip-AGNOSTIC, which is the property that makes it transfer
rather than becoming a hint sheet for one design (vibe-ic#2178).

They read the SHIPPED documents, never a fixture: if either file moves or the
section is renamed, the premise test below fails loudly rather than letting the
guards pass over an empty string.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent  # plugins/vibe-ic/
SKILL = PLUGIN_ROOT / "skills" / "spec-to-rtl" / "SKILL.md"
AGENT = PLUGIN_ROOT / "agents" / "ic-expert-agent.md"

#: The two shipped sites, and the heading each is found under. Both, because
#: the skill's own closing paragraph says the craft "is now a `### Skill:`
#: section of `agents/ic-expert-agent.md`, so it reaches every author through
#: the mandatory rendered lessons digest and not only the reader of this file".
#: A remedy added to one and not the other reaches half the authors.
SITES = {
    "skills/spec-to-rtl/SKILL.md": (
        SKILL, "## The sign-off corner is the SLOW corner"),
    "agents/ic-expert-agent.md": (
        AGENT, "### Skill: multi-operand sums and the slow corner"),
}

#: Design identifiers that must never appear in this section. The generalised
#: variant was measured to author the same micro-architecture as the
#: design-specific one, so naming a design costs a reader and buys nothing.
FORBIDDEN_DESIGN_TOKENS = (
    "sha256", "spm", "subservient", "ibex", "caravel", "hawaii",
    "sky130", "gf180", "sg13g2", "tsmc",
)


def _section(path: Path, heading: str) -> str:
    """The heading's body, up to the next heading of the same or higher level."""
    text = path.read_text(encoding="utf-8")
    start = text.find(heading)
    assert start >= 0, (
        f"{path.name} no longer carries {heading!r}. This guard reads the "
        f"SHIPPED document; a renamed section makes every assertion below "
        f"vacuous, so it is a failure here rather than a silent pass.")
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[start + len(heading):]
    nxt = re.search(r"^#{1,%d} " % level, rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def test_premise_both_sites_still_carry_the_two_original_rewrites():
    """Without this, a deleted section would make the guards pass for the
    wrong reason. The third remedy is an ADDITION; the first two must survive."""
    for name, (path, heading) in SITES.items():
        body = _section(path, heading)
        assert "CARRY-SAVE the multi-operand sum" in body, name
        assert "PRE-ADD what is already known a cycle early" in body, name
        assert "3:2 compressors" in body, name


def test_the_surviving_carry_propagate_add_is_prescribed_fast():
    """The defect itself: the section stopped at "then do ONE carry-propagate
    add" and never said that add must not be a ripple."""
    for name, (path, heading) in SITES.items():
        body = _section(path, heading)
        assert "then do ONE\n   carry-propagate add" in body or (
            "then do ONE carry-propagate add" in body), name
        low = body.lower()
        assert "carry-select" in low, (
            f"{name}: the section leaves exactly one carry-propagate add on "
            f"the path and never names a fast one. #2081 RULED for the arm "
            f"that made it carry-select; teaching only the arm without it is "
            f"teaching the rejected arm.")
        assert "parallel-prefix" in low, name
        assert "ripple" in low, (
            f"{name}: a prescription the author cannot check against the "
            f"report is not actionable — the word the worst path shows is "
            f"'ripple' and the section must name it.")


def test_the_author_is_told_how_to_RECOGNISE_the_ripple():
    """A remedy with no detection rule is advice. The shape is in the report
    the author already has: a run of majority gates as long as the word."""
    for name, (path, heading) in SITES.items():
        low = _section(path, heading).lower()
        assert "majority" in low, name
        assert "word width" in low, name


def test_the_shared_sum_half_of_the_ruled_arm_is_taught_too():
    """#2081's ruled arm is TWO changes, not one: the fast add AND the sum
    computed once per word instead of once per consumer. Six of its top ten
    failing paths were in the group the smaller arm left alone."""
    for name, (path, heading) in SITES.items():
        low = _section(path, heading).lower()
        assert "once" in low and "consumer" in low, name


def test_a_synthesis_knob_is_not_offered_as_the_remedy():
    """This repo MEASURED the knob: `tools/vibeic-eda/FIX_STATUS.md` records an
    A/B in which a declared prefix-adder map restructured the routed ripple and
    the sign-off corner still did not close, while TNS and slew DRV got worse.
    A section that named the architecture without that boundary would send the
    next author to the knob."""
    for name, (path, heading) in SITES.items():
        body = _section(path, heading)
        assert "FIX_STATUS.md" in body, name
        assert re.search(r"knob is not this remedy|KNOB IS NOT THIS REMEDY",
                         body, re.IGNORECASE), name


def test_the_section_stays_chip_agnostic():
    """vibe-ic#2178 removed the design-specific worked numbers from here after
    a blind author quoted one back. The addition must not put them back."""
    for name, (path, heading) in SITES.items():
        low = _section(path, heading).lower()
        for token in FORBIDDEN_DESIGN_TOKENS:
            assert token not in low, (
                f"{name}: the slow-corner section names {token!r}. The "
                f"generalised text was measured to author the same "
                f"micro-architecture, so a design name costs a reader and "
                f"buys nothing (vibe-ic#2178).")
