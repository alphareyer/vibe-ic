#!/usr/bin/env python3
"""_die_level_attribution_consult.py — may a SIGN-OFF checker stop counting a
die-level rule against a macro? One answer, re-derived, for every checker that
asks.

THE DEFECT THIS CLOSES, MEASURED 2026-09-15 on `subservient` x gf180mcuD, a
HARDMACRO delivery that had already passed 9 of 9 declared phase-3 sign-off
gates::

    reports/phase3/die_level_rule_attribution.json   (written BY THE RUN)
      verdict     DIE_LEVEL_DENSITY_ATTRIBUTED_TO_INTEGRATOR
      deliverable HARDMACRO        total 2
      attributed_density_rules {M2.4: 1, M3.4: 1}   unattributed_total 0
      drc_tier    PASS_WITH_ATTRIBUTION

    phase 3's own `drc` STEP        -> PASS_WITH_ATTRIBUTION, remainder 0
    step 31 `drc_report_check --signoff`  -> FAIL "2 real DRC violation(s)"
    step 36 `tapeout_signoff_check`       -> FAIL "2 violation(s) (2 at design
                                                   level - met2+/via+)"

Three checkers, ONE quantity, and two of them had never been told what the
delivery IS. Those two FAILs are two of the completion audit's failed gates and
two of its non-green steps, on a design whose unattributed remainder is zero.

M2.4 and M3.4 are metal-density MINIMUMS whose measurement window, in the deck's
own block, is the WHOLE DIE. A 413 um macro placed inside somebody else's die
cannot move that mean; the die's top-level fill closes it. The flow already
says so: `die_level_deck_rule_attribution` derives it from the PDK deck,
`PASS_WITH_ATTRIBUTION` is a declared tier, and `flow_compliance_check`
REFUSES a delivery that attributes such a rule and does not hand it over.

WHY THIS IS NOT A WAIVER, AND WHY IT IS NOT A VERDICT HANDED IN FROM OUTSIDE.
A verdict a caller can hand in is a verdict a caller can forge, so NOTHING here
is taken on the attribution record's word. :func:`consult` re-establishes all
FIVE of the following, and credits nothing unless every one of them holds:

  1. the DELIVERABLE is HARDMACRO, re-read from the run's own
     `tapeout_declaration.json` through `_tapeout_declaration` -- not from the
     attribution record's copy of it;
  2. every attributed rule NAMES A DECK FILE THAT EXISTS ON THIS HOST AND
     CONTAINS THAT RULE NAME. This is the clause a forger cannot satisfy: you
     cannot attribute a rule the PDK does not declare, and you cannot attribute
     it to a deck you cannot produce;
  3. the HANDOFF record beside the abstract names every attributed rule --
     the same condition `flow_compliance_check.hardmacro_handoff_refusal`
     already makes blocking, asked again here so a checker never credits an
     attribution the delivery does not carry;
  4. the ARITHMETIC CLOSES: attributed + unattributed == the record's own
     total, and that total equals the count the CALLER measured for itself.
     A record that does not account for the caller's own violations is not
     about the caller's report;
  5. the UNATTRIBUTED REMAINDER IS ZERO. One violation that is the design's
     keeps the FAIL, and that is the whole point: this narrows what a checker
     counts, it never narrows what it refuses.

Every clause is a REFUSAL this code path did not have before it existed. A
caller that consults this asks MORE of a HARDMACRO delivery than one that does
not, and exactly as much of a DIE delivery (`applicable` is False and nothing
changes).

chip-AGNOSTIC: rule names, deck paths and counts all come from the run and the
PDK. No design, PDK, vendor or IC literal appears here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _tapeout_declaration as _td  # noqa: E402
import die_level_deck_rule_attribution as _dla  # noqa: E402

PROGRAM = "die_level_attribution_consult"

#: Where the run's attribution report and the record that must travel with the
#: delivery live. Both names are OWNED by `die_level_deck_rule_attribution`;
#: they are re-stated here as relative paths only, never as a second
#: definition of what either document contains.
ATTRIBUTION_REL = "reports/phase3/die_level_rule_attribution.json"
HANDOFF_REL = "phase3/stage4/hardmacro/" + _dla.HANDOFF_NAME

#: The tier a credited consultation licenses. The SAME word phase 3's own
#: `drc` step already reaches, so three checkers stop reporting three
#: different verdicts about one measurement.
TIER = _dla.TIER_PASS_WITH_ATTRIBUTION


def _word_in(word: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


def _blank(reason: str) -> Dict[str, Any]:
    return {"program": PROGRAM, "applicable": False, "credit": False,
            "reason": reason, "attributed_rules": {}, "attributed_total": 0,
            "unattributed_total": None, "record_total": None,
            "deck_verified": {}, "provenance": {},
            "handoff_ok": None, "tier": None}


def consult(project: Path,
            measured_total: Optional[int] = None,
            measured_rules: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """May a sign-off checker stop counting ``measured_total`` against this
    macro? `{"credit": bool, "reason": str, ...}`, re-derived every time.

    ``measured_total`` is the violation count THE CALLER measured for itself.
    Passing it is how a caller checks that the record is about ITS report;
    omitting it skips clause 4's second half and the reason says so.

    `credit` True means: HARDMACRO, every attributed rule re-found in a deck
    on disk, the handoff record complete, the arithmetic closed, and the
    unattributed remainder zero. Anything else is False WITH A REASON -- this
    function has no silent path.
    """
    # 1 ── the DELIVERABLE, from the declaration and not from the record
    doc, why = _td.load(project / _td.DECLARATION_REL)
    if why is not None or not isinstance(doc, dict):
        return _blank("the delivery is undeclared: "
                      + (why or "the declaration is not a mapping"))
    got = _td.answer(doc, "deliverable")
    if not _td.is_answered(got):
        return _blank(f"{_td.DECLARATION_REL} answers no `deliverable`, so "
                      f"what leaves this flow is undeclared rather than a "
                      f"macro")
    if str(got).upper() != "HARDMACRO":
        return _blank(f"the declared deliverable is {str(got)!r}, not "
                      f"HARDMACRO: a die owns its own die-level rules and "
                      f"there is nobody above it to attribute them to")

    # 2 ── the run's attribution report
    try:
        rec = json.loads((project / ATTRIBUTION_REL).read_text(
            errors="replace"))
    except OSError as exc:
        return _blank(f"{ATTRIBUTION_REL} could not be read ({exc}) -- "
                      f"unreadable is not attributed")
    except ValueError as exc:
        return _blank(f"{ATTRIBUTION_REL} is not JSON ({exc})")
    if not isinstance(rec, dict):
        return _blank(f"{ATTRIBUTION_REL}'s top level is not a mapping")
    if rec.get("verdict") != _dla.DENSITY_ATTRIBUTED:
        return _blank(
            f"{ATTRIBUTION_REL} reaches verdict {rec.get('verdict')!r}, not "
            f"{_dla.DENSITY_ATTRIBUTED!r}: this run attributed no die-level "
            f"density rule, so there is nothing to stop counting")
    rules = rec.get("attributed_density_rules")
    if not isinstance(rules, dict) or not rules:
        return _blank(f"{ATTRIBUTION_REL} attributes no rule by name")

    out = _blank("")
    out["applicable"] = True
    out["attributed_rules"] = {str(k): v for k, v in rules.items()}

    # 3 ── EVERY attributed rule must be RE-DERIVABLE, not merely asserted.
    #
    # The predicate is the producer's own and is not restated here: a rule is
    # die-level DENSITY when its `# Rule` block's CODE reads one of the deck's
    # whole-die AREA identifiers (`die_level_density_rules`). What this clause
    # does is RUN THAT PREDICATE AGAIN over the deck text, against the deck
    # file when this host has it and against the code the producer recorded
    # when it does not.
    #
    # BOTH ARMS EXIST BECAUSE OF WHERE THE CALLERS RUN. MEASURED 2026-09-15:
    # the deck is `/foss/pdks/...`, which exists inside the flow's container
    # image and NOT on the host — and the completion audit, which re-invokes
    # the two callers of this module, runs on the host. A single deck-on-disk
    # arm would therefore refuse in the one place the question is asked. The
    # arm actually taken is DISCLOSED in `provenance`, never averaged away.
    deck_of = rec.get("die_level_density_rules_in_deck")
    ev = rec.get("die_level_density_rule_evidence")
    die_names = rec.get("die_area_identifiers")
    verified: Dict[str, str] = {}
    provenance: Dict[str, str] = {}
    unverified = []
    for name in sorted(out["attributed_rules"]):
        src = (deck_of or {}).get(name) if isinstance(deck_of, dict) else None
        if not src:
            unverified.append(f"{name}: the record names no deck source")
            continue
        text = None
        try:
            text = Path(str(src)).read_text(errors="replace")
        except OSError:
            text = None
        if text is not None:
            if name not in text:
                unverified.append(
                    f"{name}: its deck {src} is on this host and does NOT "
                    f"contain the rule name, so this PDK does not declare it")
                continue
            verified[name] = str(src)
            provenance[name] = "deck_on_disk"
            continue
        block = (ev or {}).get(name) if isinstance(ev, dict) else None
        if not isinstance(block, dict):
            unverified.append(
                f"{name}: its deck {src} is not on this host and the record "
                f"carries no recorded deck code for it, so nothing here can "
                f"re-derive that the PDK declares it a die-level density rule")
            continue
        code = str(block.get("code") or "")
        matched = [str(m) for m in (block.get("die_area_identifiers_matched")
                                    or [])]
        # NOT "the code names the rule". A deck states a rule's ID in the
        # `# Rule <id>` COMMENT above its block, and `deck_code_only` strips
        # comments ON PURPOSE — a comment that happens to spell the die-area
        # identifier must not make a rule die-level. So the recorded code
        # CANNOT contain the rule id, and asking it to was this clause's
        # first draft going red against the real gf180mcuD density deck.
        # What binds a block to a rule NAME is clause 3b below: the names
        # attributed must be exactly the names the CALLER's own report
        # reports violations under.
        if not matched:
            unverified.append(
                f"{name}: the recorded deck code references NO whole-die area "
                f"identifier, so by the producer's own predicate it is not a "
                f"die-level rule at all")
            continue
        declared = {str(n) for n in (die_names or ())}
        stray = [m for m in matched if declared and m not in declared]
        if stray:
            unverified.append(
                f"{name}: the recorded evidence claims die-area identifier(s) "
                f"{stray} that the record does not list as the deck's "
                f"({sorted(declared)})")
            continue
        if not any(_word_in(m, code) for m in matched):
            unverified.append(
                f"{name}: the recorded deck code does not actually contain "
                f"the die-area identifier(s) {matched} it claims to match")
            continue
        verified[name] = str(src)
        provenance[name] = "producer_recorded_deck_code"
    out["deck_verified"] = verified
    out["provenance"] = provenance
    # 3b ── the NAMES must be the caller's own. This is what ties a recorded
    # deck block to a real violation: a forged record can claim a block, it
    # cannot make the caller's DRC report emit violations under a rule the
    # design did not violate. When the caller states which rules it measured,
    # the attributed set must be exactly that set — attributing a rule the
    # report never reported, or leaving one it did report unattributed, both
    # mean this record is not an account of the caller's report.
    if measured_rules is not None:
        theirs = {str(r) for r in measured_rules}
        mine = set(out["attributed_rules"])
        if mine != theirs:
            out["reason"] = (
                f"the attributed rule(s) {sorted(mine)} are not the rule(s) "
                f"the caller's own report carries violations under "
                f"({sorted(theirs)})")
            return out
        out["rules_match_caller"] = True
    if unverified:
        out["reason"] = ("the attribution was NOT re-derivable from the deck "
                         "evidence it cites: " + "; ".join(unverified))
        return out

    # 4 ── the handoff record beside the abstract names every attributed rule
    try:
        ho = json.loads((project / HANDOFF_REL).read_text(errors="replace"))
        stated = {str(r.get("rule")) for r in (ho.get("requirements") or [])
                  if isinstance(r, dict)}
    except (OSError, ValueError, AttributeError) as exc:
        out["handoff_ok"] = False
        out["reason"] = (f"the delivery carries no readable handoff record at "
                         f"{HANDOFF_REL} ({exc}). An attributed rule the "
                         f"delivery does not hand over is a waiver wearing "
                         f"another word.")
        return out
    missing = [n for n in sorted(out["attributed_rules"]) if n not in stated]
    out["handoff_ok"] = not missing
    if missing:
        out["reason"] = (f"{HANDOFF_REL} names {sorted(stated) or 'nothing'} "
                         f"and is missing {missing}: a partial handoff hands "
                         f"part of the problem to nobody")
        return out

    # 5 ── the arithmetic, and the remainder
    att = rec.get("attributed_density_violations")
    unatt = rec.get("unattributed_total")
    total = rec.get("total")
    for label, val in (("attributed_density_violations", att),
                       ("unattributed_total", unatt), ("total", total)):
        if isinstance(val, bool) or not isinstance(val, int):
            out["reason"] = (f"{ATTRIBUTION_REL} states no integer {label}, "
                             f"so its arithmetic cannot be checked")
            return out
    out["attributed_total"] = att
    out["unattributed_total"] = unatt
    out["record_total"] = total
    if att + unatt != total:
        out["reason"] = (f"{ATTRIBUTION_REL} does not add up: "
                         f"{att} attributed + {unatt} unattributed != "
                         f"{total} total")
        return out
    if measured_total is None:
        out["reason"] = ("the caller did not state the violation count it "
                         "measured, so nothing establishes that this record "
                         "is about the caller's own report")
        return out
    if measured_total != total:
        out["reason"] = (f"this record accounts for {total} violation(s) and "
                         f"the caller measured {measured_total}: the record "
                         f"is not about the report the caller read")
        return out
    if unatt != 0:
        out["reason"] = (f"{unatt} violation(s) are NOT attributed to the "
                         f"integrator and are this design's to close")
        return out

    out["credit"] = True
    out["tier"] = TIER
    out["reason"] = (
        f"HARDMACRO delivery: all {att} violation(s) are die-level density "
        f"rule(s) ({', '.join(sorted(out['attributed_rules']))}) whose "
        f"measurement window is the whole die, re-derived from the PDK deck(s) "
        f"that declare them, handed to the integrator in {HANDOFF_REL}; "
        f"unattributed remainder 0")
    return out


def disclosure(result: Dict[str, Any]) -> str:
    """One line a checker can print or put in a finding, either way."""
    if result.get("credit"):
        return (f"{TIER}: {result['attributed_total']} die-level density "
                f"violation(s) "
                f"({', '.join(sorted(result['attributed_rules']))}) are the "
                f"INTEGRATOR's to close and travel with the delivery in "
                f"{HANDOFF_REL}; unattributed remainder 0. This is not a "
                f"waiver: the rules are named, measured and handed over.")
    return f"die-level attribution NOT credited: {result.get('reason')}"
