#!/usr/bin/env python3
"""_metric_vocabulary.py — one fact, one canonical name, and the spellings it
is actually emitted under.

THE DEFECT THIS CLOSES
======================
This tree carries TWO metric vocabularies for the SAME measurements:

    the AXES ask for      timing.setup.worst_slack_ns   (dotted, 20 keys, the
                                                         proof vocabulary in
                                                         `_ppa.feasibility`)
    the PRODUCERS emit    timing__setup__ws             (double-underscore, 34
                                                         keys across 10
                                                         programs, OpenROAD's
                                                         own convention)

Measured 2026-09-04: `every_required_metric_key_has_a_producer` examines 20
canonical keys and observes TWO of them in emitted records, then reports that
axes like `physical.drc` are "NOT PROVEN BY ANY RUN IN THIS CORPUS". The runs
measured them. They measured them under the other spelling.

That is a detector reporting a FALSE ABSENCE, which is this repo's worst shape:
"I do not recognise this name" published as "nobody measured this". The cost is
not cosmetic — an axis reported unproven is an axis nobody goes and fixes.

WHY A TABLE AND NOT A RULE
==========================
A mechanical rule (`s/__/./g`) would be wrong, and wrong in the direction that
launders a real absence:

    timing.setup.wns_ns          worst NEGATIVE slack
    timing.setup.worst_slack_ns  worst slack, which may be POSITIVE
    timing__setup__ws            worst slack

`ws` is the second, never the first. They differ on every clean design, and the
canonical vocabulary ALREADY distinguishes them — it carries both keys. A
transliteration would answer `wns_ns` from `ws` and report a met timing
constraint on a design that has no negative slack to report, which reads as
evidence where there is none.

So every entry is stated with the RELATION it holds, and only `SAME_FACT`
entries resolve. `NARROWER`/`RELATED` are recorded so the next reader does not
re-derive the comparison, and they never answer a canonical key.

chip/PDK/vendor-AGNOSTIC: tool names appear only as the emitting-tool prefix the
producers already use (`klayout__`, `magic__`), which is a tool, not a foundry.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The relation an emitted spelling holds to the canonical key.
SAME_FACT = "SAME_FACT"        # interchangeable; resolves the canonical key
NARROWER = "NARROWER"          # measures a subset; NEVER resolves
RELATED = "RELATED"            # about the same subject, different quantity

#: canonical dotted key -> ((emitted spelling, relation, why), ...)
#:
#: EVERY ENTRY CARRIES ITS REASON, because the cost of a wrong SAME_FACT is a
#: canonical key answered by a number that is not it — and an axis then reported
#: PROVEN on evidence that does not prove it. A synonym without a stated reason
#: is a guess with a table's authority.
SYNONYMS: Dict[str, Tuple[Tuple[str, str, str], ...]] = {
    "timing.setup.worst_slack_ns": (
        ("timing__setup__ws", SAME_FACT,
         "OpenROAD's `ws` IS worst slack, signed, positive when met — the same "
         "quantity this key names."),
        ("cts__timing__setup__ws", NARROWER,
         "the same quantity measured AFTER CTS and before route; a stage's "
         "intermediate value is not the sign-off one."),
    ),
    "timing.hold.worst_slack_ns": (
        ("timing__hold__ws", SAME_FACT,
         "OpenROAD's `ws` on the hold side is worst slack, signed, positive "
         "when met — the same quantity this key names."),
        ("20__timing__hold__ws", NARROWER,
         "step 20's own hold slack, before the post-route repair the sign-off "
         "number is taken after."),
    ),
    "timing.setup.wns_ns": (
        ("timing__setup__tns", RELATED,
         "TOTAL negative slack sums every violating path; WNS is the worst "
         "single one. A design with TNS 0 has no WNS to report, but TNS != WNS "
         "on any design that violates."),
    ),
    "timing.hold.wns_ns": (
        ("timing__hold__tns", RELATED,
         "TOTAL negative slack on the hold side sums every violating path; "
         "WNS is the worst single one, and they are equal only when exactly "
         "one path violates."),
    ),
    "physical.drc.violations": (
        ("klayout__drc_error__count", SAME_FACT,
         "a DRC violation count from the sign-off deck; the tool prefix names "
         "WHO measured it, not a different quantity."),
        ("magic__drc_error__count", SAME_FACT,
         "the same count from the other sign-off DRC engine this flow runs."),
        ("route__drc_errors", NARROWER,
         "the ROUTER's own in-loop DRC, which checks a subset of the sign-off "
         "deck and runs on the DEF rather than the streamed layout. Treating "
         "it as the sign-off number is how a clean router log reads as a clean "
         "die."),
        ("detailedroute__route__drc_errors", NARROWER,
         "the detailed router's own in-loop DRC — the same subset question as "
         "`route__drc_errors`, asked by the stage that placed the wires."),
    ),
    "physical.lvs.violations": (
        ("design__lvs_error__count", SAME_FACT,
         "the total LVS error count the sign-off comparison reports, across "
         "every class of mismatch rather than one of them."),
        ("design__lvs_unmatched_net__count", NARROWER,
         "one CLASS of LVS error; a design may match every net and still fail "
         "on devices or pins."),
        ("design__lvs_unmatched_device__count", NARROWER,
         "unmatched DEVICES only; a layout may match every device and still "
         "fail on nets or pins."),
        ("design__lvs_unmatched_pin__count", NARROWER,
         "unmatched PINS only; the same argument as devices and nets."),
    ),
    "physical.antenna.violations": (
        # Added 2026-09-15 (icspm2). The two NARROWER entries below are right
        # and stay; what was missing is a spelling that IS the total. The
        # producer emits `antenna__violation__count` ONLY when both populations
        # are zero — the one state in which the total is not in doubt, since a
        # net may violate at several pins and the two do not sum. A design WITH
        # violations still resolves nothing here, which is the direction that
        # cannot certify a dirty result.
        ("antenna__violation__count", SAME_FACT,
         "the antenna violation TOTAL, emitted only for a result whose net and "
         "pin populations are both zero; never a sum of the two below."),
        ("antenna__violating__nets", NARROWER,
         "antenna violations are counted per NET and per PIN and the two are "
         "not the same population; neither alone is the total."),
        ("antenna__violating__pins", NARROWER,
         "the per-PIN population; a net may violate at one pin and not "
         "another, so pins and nets do not sum to a single total."),
    ),
    "timing.drv.max_cap_violations": (
        ("design__max_cap_violation__count", SAME_FACT,
         "the max-capacitance violation count — one name for the count of "
         "nets whose load exceeds the library's stated maximum."),
    ),
    "timing.drv.max_tran_violations": (
        ("design__max_slew_violation__count", SAME_FACT,
         "max SLEW and max TRANSITION are the same design-rule check under two "
         "industry names; both mean the signal edge is too slow."),
    ),
    # The third leg of the `drv` proof group, added 2026-09-15 (icspm2). The
    # other two resolved and this one had NO emitted spelling at all, so the
    # group could never close and `every_required_metric_key_has_a_producer`
    # reported `axis 'drv' IS NOT PROVEN BY ANY RUN IN THIS CORPUS` on runs
    # whose own STA report carried `SIGNOFF_DRV_CENSUS max_fanout violators=0`.
    # A two-thirds-resolvable group is indistinguishable, in the verdict, from
    # one nobody measured.
    "timing.drv.max_fanout_violations": (
        ("design__max_fanout_violation__count", SAME_FACT,
         "the max-fanout violation count — one name for the count of nets "
         "driving more loads than the library's or the SDC's stated maximum. "
         "Emitted by `signoff_metrics_aggregate` from OpenSTA's own per-check "
         "counter (`SIGNOFF_DRV_CENSUS max_fanout violators=N`), which is a "
         "measured zero and not an absent table."),
    ),
    # Three more axes the flow MEASURED and never published under a canonical
    # name (2026-09-15, icspm2). Same shape as the drv leg above: the record
    # was on disk, in the checker's own report, under a spelling this table
    # did not carry.
    "power.ir.worst_drop_v": (
        ("power__ir__worst_drop_v", SAME_FACT,
         "the worst static IR drop, in volts. The producer converts the PSM "
         "record's microvolts and refuses when `supply_measured` is false, so "
         "it is a drop the tool established and never one derived from a zero "
         "supply it could not find."),
    ),
    "reliability.em.violations": (
        ("reliability__em__violation__count", SAME_FACT,
         "the count of routed segments whose current density exceeds the "
         "layer's Jmax — the EM violation population, from the Jmax screen's "
         "own `offender_count` over the segments it actually screened."),
    ),
    "reliability.em.worst_ratio": (
        ("reliability__em__worst_ratio", SAME_FACT,
         "worst current density AS A FRACTION of the layer's Jmax, which is "
         "what an EM 'worst ratio' is; the screen's `worst_utilization`."),
    ),
    "design_for_eco.spares.count": (
        ("design_for_eco__spares__count", SAME_FACT,
         "the number of spare cells that SURVIVED into the shipped artefacts. "
         "The design-for-ECO question is what a metal-only ECO can still "
         "reach, and an inserted spare a later pass removed reaches nothing, "
         "so `survived` is the count and `inserted` is not."),
    ),
    "equivalence.verdict": (
        ("equivalence__verdict", SAME_FACT,
         "the post-layout LEC's own verdict string — the logical-equivalence "
         "proof itself, as the checker recorded it (added 2026-09-15, icspm2)."),
        ("design__xor_difference__count", RELATED,
         "a layout-vs-layout XOR difference count is not a logical-equivalence "
         "verdict; a zero XOR says two layouts match, not that the netlist "
         "implements the RTL."),
    ),
}


def relations_for(canonical: str) -> Tuple[Tuple[str, str, str], ...]:
    return SYNONYMS.get(canonical, ())


def resolving_spellings(canonical: str) -> List[str]:
    """Only the spellings that may ANSWER this key."""
    return [s for s, rel, _ in relations_for(canonical) if rel == SAME_FACT]


def all_known_spellings() -> Dict[str, Tuple[str, str]]:
    """emitted spelling -> (canonical, relation), for every entry."""
    out: Dict[str, Tuple[str, str]] = {}
    for canon, entries in SYNONYMS.items():
        for spelling, rel, _ in entries:
            out[spelling] = (canon, rel)
    return out


def resolve(record: Dict[str, Any], canonical: str) -> Tuple[Any, Optional[str]]:
    """(value, the spelling it was read under) — or (None, None).

    The canonical key wins when present. Otherwise the first SAME_FACT spelling
    that is present answers, and the spelling is returned so a reader can see
    WHICH name carried the number. A NARROWER or RELATED spelling never answers,
    however tempting: that is the whole point of stating the relation.
    """
    if canonical in record and record[canonical] is not None:
        return record[canonical], canonical
    for spelling in resolving_spellings(canonical):
        if spelling in record and record[spelling] is not None:
            return record[spelling], spelling
    return None, None


def unmapped(emitted: Iterable[str]) -> List[str]:
    """Emitted keys this table says nothing about.

    A table that silently goes stale is the register-that-must-be-hand-fed
    defect: the vocabulary grows and the mapping does not, and the axis gate
    goes back to reporting false absences one key at a time. The checker built
    on this reports these by name.
    """
    known = set(all_known_spellings())
    return sorted(k for k in emitted if k not in known)
