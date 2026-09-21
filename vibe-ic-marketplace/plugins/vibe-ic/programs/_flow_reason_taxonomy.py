#!/usr/bin/env python3
"""Shared reason taxonomy for flow outcomes that did not decide the question.

The verdict word and the reason are deliberately separate.  ``SKIP`` says what
the flow does with an outcome; ``reason_class`` says why the check did not
produce a substantive PASS/FAIL verdict.  Only the three classes in
``SKIP_ELIGIBLE`` may remain in a skip/not-applicable tier.  The other three
leave the executed-PASS population and require follow-up.

This module is chip-, process-, tool-vendor-, and gate-agnostic.  Producers
should publish an explicit ``reason_class`` whenever possible.  The inference
helper exists for legacy programs and is deliberately fail-closed: an
unclassified non-verdict is an execution error, never a benign skip.

This is a shared helper, not an independently dispatched gate.  Its consuming
compliance rule blocks false PASS/N/A certification by moving unsafe reasons
to BLOCKED or INCOMPLETE, but it never fabricates a design FAIL from a process-
provenance gap.  Existing flow policy decides whether that disclosed tier
stops a caller.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates and much of the suite load a program — it does not. This module
# imported no sibling until R-0915-119 gave it one, which is why it carried
# no preamble; `test_issue2104_programs_load_by_path` caught the omission.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import _structural_absence as _sa  # R-0915-119

import re
from typing import Any, Mapping, Optional


import _structural_absence as _sa  # R-0915-119  # noqa: E402

DESIGN_DECLARED_NA = "DESIGN_DECLARED_NA"
CAPABILITY_ABSENT = "CAPABILITY_ABSENT"
EXTERNAL = "EXTERNAL"
BLOCKED_BY_UPSTREAM = "BLOCKED_BY_UPSTREAM"
EXECUTION_ERROR = "EXECUTION_ERROR"
ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
#: R-0915-46/47 — the gate ran, found its subject absent, and the thing that
#: PRODUCES that subject has not run yet in this flow. The gate is not broken
#: and the design has not declared anything; the question was simply asked too
#: early, and the answer comes from the completed tree.
#:
#: MEASURED twice on spm x gf180mcuD:
#:   klayout_deck_mode_check       in-flight "[skipped] no KLayout DRC
#:     artefacts found" -> on the completed tree, "[PASS] 14 DRC artefact(s);
#:     real rule deck attested by 3 of them". The P0 umbrella asks before
#:     phase-3 DRC has produced them.
#:   gate_evidence_completeness_check  "examined nothing (reason: no
#:     FINAL_REPORT.md or flow-compliance JSON in this run)" -- it books that
#:     for the absence of the completion audit's OWN record, which the audit
#:     it is a component of writes only after it passes. Circular: on a first
#:     run it can never be satisfied.
#:
#: It is SKIP-ELIGIBLE, and narrowly so: `flow_compliance_check` grants it only
#: when the tree shows the producer has NOT run. Once the producer HAS run, an
#: absent subject keeps whatever non-green verdict the gate gave it.
ASKED_BEFORE_PRODUCER = "ASKED_BEFORE_PRODUCER"

#: R-0915-119 — the checker ENUMERATED its subject population and it is
#: EMPTY. A design with no arbiter has been ANSWERED about arbiters: the gate
#: is not broken, nothing is unmeasured, and the question is closed. It is a
#: THIRD STATE, published under its own name, never a PASS.
#:
#: It is granted ONLY on the checker's own enumeration, never on its sentence.
#: `_SUBJECT_ABSENT_RE` below already recognised these messages and the
#: docstring beside it says why acting on them was wrong — "the sentence was a
#: better clue than the old default, and a clue is not a declaration". That
#: stands. What changes is that a checker can now STATE the class and carry
#: the enumeration that establishes it (`_structural_absence.absence`), and
#: `infer_nonverdict_reason` refuses the token when the enumeration is absent
#: or when the subject was FOUND and merely not examined.
NOT_APPLICABLE_BY_STRUCTURE = _sa.NOT_APPLICABLE_BY_STRUCTURE

REASON_CLASSES = (
    DESIGN_DECLARED_NA,
    NOT_APPLICABLE_BY_STRUCTURE,
    CAPABILITY_ABSENT,
    EXTERNAL,
    ASKED_BEFORE_PRODUCER,
    BLOCKED_BY_UPSTREAM,
    EXECUTION_ERROR,
    ZERO_DENOMINATOR,
)
REASON_CLASS_SET = frozenset(REASON_CLASSES)

# Only these classes satisfy the interrogation doctrine's N/A/skip bar.
SKIP_ELIGIBLE = frozenset({
    DESIGN_DECLARED_NA,
    # R-0915-119: DECIDED. The same standing R-0915-102(1) gives a
    # design-declared N/A — the question is answered, so it does not hold the
    # step at INCOMPLETE. It keeps its OWN name in every published row.
    NOT_APPLICABLE_BY_STRUCTURE,
    CAPABILITY_ABSENT,
    EXTERNAL,
    ASKED_BEFORE_PRODUCER,
})
INCOMPLETE = frozenset({
    BLOCKED_BY_UPSTREAM,
    EXECUTION_ERROR,
    ZERO_DENOMINATOR,
})


def normalise(value: Any) -> Optional[str]:
    """Return a canonical reason class, or ``None`` for an invalid value."""
    if not isinstance(value, str):
        return None
    token = value.strip().upper().replace("-", "_").replace(" ", "_")
    return token if token in REASON_CLASS_SET else None


def report_reason_class(report: Any) -> Optional[str]:
    """Read an explicit class from a gate report without inventing one."""
    if not isinstance(report, Mapping):
        return None
    for key in ("reason_class", "not_measured_class", "skip_reason_class"):
        cls = normalise(report.get(key))
        if cls:
            return cls
    summary = report.get("summary")
    if isinstance(summary, Mapping):
        for key in ("reason_class", "not_measured_class", "skip_reason_class"):
            cls = normalise(summary.get(key))
            if cls:
                return cls
    return None


# The counted-zero shapes below were added from three MEASURED gate messages,
# not from imagination. `read 0 <noun>` and `0 of 0 <noun> screened` are
# `em_peak_current_authority_check` on a tree with no EM segments; `nothing to
# re-derive` is `si_mcf_sta_check` on a SPEF it opened and parsed to zero
# coupling pairs. All three were booked EXECUTION_ERROR, which says the program
# errored — and none of them did. NOTHING GREENS ON THIS: ZERO_DENOMINATOR is
# not skip-eligible either, so both classes render the same INCOMPLETE step
# tier; what changes is only what the published row tells the person who has
# to act on it.
#
# `0 of 0` and not `0 of N`: the second is a resolvable population that failed
# to resolve, which is a fault and not an empty denominator —
# `sdc_validator_check`'s bad positional says "0 of 2 declared search root(s)
# could be resolved and 0 .sdc file(s) were read", and it must stay an
# EXECUTION_ERROR. `read 0` and not `were read`, for the same reason and
# against the same sentence.
#: The ONE shape `_structural_absence.sentence()` prints. Anchored on the
#: class token AND the counts, so a sentence that merely sounds like an
#: absence cannot reach the decided state.
_STRUCTURAL_LINE_RE = re.compile(
    r"\b" + _sa.NOT_APPLICABLE_BY_STRUCTURE + r"\b[^\n]{0,240}?"
    r"\benumerated\s+(?P<scanned>\d+)\b[^\n]{0,120}?\bfound\s+0\b",
    re.I)

_ZERO_RE = re.compile(
    r"(?:\bzero[ -]denominator\b|\bexamined\s*[=:]?\s*0\b|"
    r"\bchecked\s*[=:]?\s*0\b|\b0\s*/\s*\d+\s+(?:examined|checked)\b|"
    r"\b0\s+of\s+0\b|\bread\s+0\b|"
    r"\bnothing (?:to|left to) (?:re[\s-]?derive|examine|check|compare|"
    r"screen|measure|verify|audit)\b|"
    r"\ball\s+0\b|\bno (?:reports?|entries|documents?)\b|"
    r"\bno\b[^\n]{0,50}\bdocuments?\b|"
    r"\bnone\s+(?:could|were)\s+(?:be\s+)?(?:aged|examined|checked)\b|"
    r"\bdocs?\s+loaded\s*[=:]?\s*none\b)", re.I)
_BLOCKED_RE = re.compile(
    r"(?:\bblocked\b|\bupstream\b|\bhas not run\b|\bnot yet run\b|"
    r"\bno deliverable\b|\bno result\.md\b|\bno orchestrator report\b|"
    r"\bno\b[^\n]{0,80}\b(?:doc(?:ument)?|report|file)\b[^\n]{0,40}\bfound\b|"
    r"\bno\b[^\n]{0,80}\b(?:results?\.json|generated docs|input docs|"
    r"canonical artefacts?|canonical artifacts?)\b|"
    # `no L<n>` means "that layer document never arrived" — an upstream
    # cascade. It does NOT mean "L<n> arrived and declares none of X", which is
    # the design speaking. The two were one pattern until
    # `l9_floorplan_contract_check`'s own sentence — "the design mandates no
    # floorplan (... and no L19 die-area contract)" — matched `no l19` and was
    # booked BLOCKED_BY_UPSTREAM, costing step D1 its tier on a cascade that
    # does not exist. The exclusion is written from that sentence: a
    # declaration noun after the layer name says the layer was READ.
    r"\bno (?:spef|analog dir)\b|"
    r"\bno l\d+\b(?![^\n]{0,24}\b(?:die[- ]area|contract|mandate|declar)\w*)|"
    r"\bpre output project\b|"
    r"\bphase 1\b[^\n]{0,40}\bnot attempted\b|"
    r"\brequired output\b.*\b(?:absent|missing)\b|"
    r"\bmissing\b.*\b(?:producer|output|artefact|artifact)\b)", re.I)
_CAPABILITY_RE = re.compile(
    r"(?:\bcapability\b.*\b(?:absent|missing|unavailable)\b|"
    r"\b(?:executable|simulator|toolchain|instrument)\b.*\b(?:absent|missing|unavailable)\b|"
    r"\bno (?:supported )?(?:simulator|tool|instrument)\b)", re.I)
_EXTERNAL_RE = re.compile(
    r"(?:\bexternal\b|\bhardware bench\b|\bboard[- ]level\b|"
    r"\bfpga board\b|\bfoundry handoff\b)", re.I)
_DECLARED_NA_RE = re.compile(
    r"(?:\bdeclared no\b|\bno\b[^\n]{0,80}\bdeclared\b|"
    r"\bdo not declare\b|\bapplicable\s*(?:is|=|:)\s*false\b|"
    r"\bno waivers\.json\b|"
    r"\bwaivers\.json has no entries\b|\bno command protocol\b|"
    r"\bnon protocol design\b|"
    r"\bno analog (?:content|blocks?)\b|\bno inout\b|\bno otp\b|"
    r"\bno fpga target\b|\basic target\b)", re.I)


# R-0915-15 (lane icspm3, 2026-09-15) — A GATE THAT RAN AND FOUND ITS SUBJECT
# ABSENT FROM THE DESIGN IS NOT AN EXECUTION ERROR.
#
# MEASURED on `spm` x gf180mcuD (a serial-parallel multiplier) at main
# f64df0596. P0's umbrella carried SIXTEEN sub-gates that executed, read the
# design, found their subject is not in it, said so, and were booked
# EXECUTION_ERROR:
#
#   crc_oracle_vector_check         [skipped] no CRC module found in RTL
#   arbiter_starvation_check        [skipped] no arbitration patterns found
#   bram_pdob_combinational_check   [skipped] no inferred BRAM / megafunction …
#   break_framing_vs_l3_check       L3 does not indicate break-delimited framing
#   break_handler_safety_check      No break signals detected — not a break-based protocol
#   tx_abort_during_transmission…   No TX modules detected
#   l4_regmap_phase2_emitter…       L4 declares no registers[] … nothing to build
#   l20_dft_scan_topology…          … and L20 asserts none
#   l23_security_requirements…      design declares no security-relevant asset and L23 asserts none
#   l24_signoff_evidence_backed…    1/1 L24_SIGNOFF layer(s) assert no sign-off verdict (inert / N/A)
#   l25_reliability_envelope…       1/1 L25 layer(s) inert or N/A — no consumer exists
#
# EXECUTION_ERROR is not skip-eligible, so P0 went INCOMPLETE, which made
# stage1 non-green, which made `stage_on_pass_review` decline, which made steps
# 2, 7, 15 and 37 INCOMPLETE, which failed stage2/3/4_compliance. A multiplier
# was held INCOMPLETE for having no CRC, no arbiter, no BRAM, no register map,
# no DFT and no security assets.
#
# WHY THIS IS A SECOND PATTERN AND NOT A WIDER `_DECLARED_NA_RE`. The docstring
# below forbids laundering "a zero denominator or failed producer … into a
# design N/A merely because its sentence also contains the word `no`", and that
# refusal is right. So this pattern is checked LAST — after ZERO, BLOCKED,
# CAPABILITY, EXTERNAL and the existing declared-N/A recogniser — and it is
# written from the measured sentences rather than from the idea of them. Every
# branch below names a DESIGN ELEMENT the gate looked for inside something it
# READ; none of them matches a sentence about the gate's own source being
# absent or unreadable.
#
# THE SEPARATION IS THE POINT, AND IT IS TESTED IN BOTH DIRECTIONS over
# populations DERIVED from the tree (the 40 shipped `skipped_reason` literals
# plus the 16 measured P0 messages), never invented ones. These must NOT move:
#   `no RTL files found` · `no top-level modules found` · `no modules parseable`
#   `<l7>.txt is empty or unparseable` · `could not detect clock frequency`
#   `no L7 document in project` · `no KLayout DRC artefacts found`
#   `no module found in netlist` · `no FPGA top module found`
# A gate whose own input is absent or unreadable has not established that the
# design lacks anything, and the conservative direction keeps it INCOMPLETE.
_SUBJECT_ABSENT_RE = re.compile(
    # "no CRC module found in RTL", "no $readmemh / $readmemb in RTL" — the
    # gate READ the RTL; what is absent is the subject inside it. `in RTL`
    # (not `RTL files`) is what separates this from a missing source.
    r"(?:\bno\b[^\n]{0,60}\bin rtl\b|"
    # "no arbitration patterns found", "no toggle-divider patterns found"
    r"\bno\b[^\n]{0,40}\bpatterns found\b|"
    # "no rx_* modules found" — a NAMED subject, never the bare
    # "no top-level modules found" / "no modules parseable" of a broken input.
    r"\bno\b[^\n]{0,30}[*_][^\n]{0,20}\bmodules found\b|"
    # "No break signals detected", "No TX modules detected", "no cross-module
    # 1-cycle pulse races detected", "no inferred BRAM … detected"
    r"\bno\b[^\n]{0,80}\bdetected\b|"
    # "L3 does not indicate break-delimited framing"
    r"\bdoes not indicate\b|"
    # "not a break-based protocol"
    r"\bnot a\b[^\n]{0,30}\b(?:protocol|design|project)\b|"
    # "L4 declares no registers[]", "design declares no security-relevant asset"
    r"\bdeclares no\b|"
    # "L20 asserts none", "L24_SIGNOFF layer(s) assert no sign-off verdict"
    r"\basserts? (?:none|no)\b|"
    # "no staged HDL input declares an address-valued typedef enum"
    r"\bno\b[^\n]{0,40}\bdeclares\b|"
    # "no testable constraints in L3", "no DFT requirement derivable from the
    # design's own inputs"
    r"\bno testable\b|\bderivable from the design\b|"
    # "inert / N/A", "inert or N/A", "no consumer exists"
    r"\binert\s*(?:/|or)\s*n/?a\b|\bno consumer exists\b)", re.I)



#: The `skip_kind` values that ARE a declared absence, as opposed to a gate
#: reporting what its own scan did not find.
DECLARED_ABSENCE_SKIP_KINDS = frozenset({
    "class-not-applicable",
    "declaration-not-present",
    "declared-by-gate",
})


def _declared_basis(evidence: Mapping[str, Any]) -> bool:
    """Does this record carry a fact that says the DESIGN has no such thing?

    `input-missing` is not one: it is the P0 umbrella's ASSUMPTION about any
    rc 2, and a gate that scanned the RTL and found no CRC module has reported
    what it did not find, not what the design declared.
    """
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("declared_absence_basis"):
        return True
    return (str(evidence.get("skip_kind") or "").lower()
            in DECLARED_ABSENCE_SKIP_KINDS)

def structural_absence_line(text: Any) -> Optional[str]:
    """The line on which a gate STATED a structural absence, if it did.

    R-0915-119(2). The one shape `_structural_absence.sentence()` writes — the
    class token AND a scanned count AND `found 0` — found ANYWHERE in the
    gate's output, not only on its first line. The P0 umbrella records one
    line per skipping gate, and a gate that prints its old banner first and
    its class second had the class silently dropped; this is what lets the
    caller pick the line that actually states the decision.

    Returns None when the gate stated nothing, so a caller that finds nothing
    keeps exactly the line it would have kept before."""
    for raw in str(text or "").splitlines():
        if _STRUCTURAL_LINE_RE.search(raw):
            return raw.strip()
    return None


def _guard_structural(cls: str, evidence: Any) -> str:
    """R-0915-119 guards (i) and (ii), applied wherever the token arrives.

    A checker may STATE `NOT_APPLICABLE_BY_STRUCTURE`, and the token alone is
    not the claim: the ENUMERATION is. Without a valid one the record falls
    back to the fail-closed default, exactly as it did before the class
    existed — so a checker that crashes, cannot read its input, or found its
    subject and examined none of it cannot reach the decided state by writing
    a word into its report."""
    if cls != NOT_APPLICABLE_BY_STRUCTURE:
        return cls
    return (cls if _sa.is_valid((evidence or {}).get(_sa.EVIDENCE_KEY))
            else EXECUTION_ERROR)


def infer_nonverdict_reason(*, verdict: str = "", message: str = "",
                            evidence: Optional[Mapping[str, Any]] = None,
                            explicit: Any = None) -> str:
    """Classify a legacy non-verdict, defaulting loudly to execution error.

    Branch-owned evidence outranks prose.  The prose recognisers are narrow and
    ordered: a zero denominator or failed producer must not be laundered into a
    design N/A merely because its sentence also contains the word ``no``.
    """
    cls = normalise(explicit)
    if cls:
        return _guard_structural(cls, evidence)
    ev = dict(evidence or {})
    cls = normalise(ev.get("reason_class"))
    if cls:
        return _guard_structural(cls, ev)
    skip_kind = str(ev.get("skip_kind") or "").lower()
    if skip_kind == "class-not-applicable":
        return DESIGN_DECLARED_NA
    if skip_kind in {"external", "analog-track-deferred"}:
        return EXTERNAL
    if skip_kind in {"capability-absent", "verified-capability-absent"}:
        return CAPABILITY_ABSENT
    if skip_kind in {"blocked-by-upstream", "missing-upstream-output"}:
        return BLOCKED_BY_UPSTREAM
    if skip_kind in {"zero-denominator", "empty-denominator"}:
        return ZERO_DENOMINATOR
    if skip_kind in {"no-backing-program", "invocation-error"}:
        return EXECUTION_ERROR

    # Many gate-owned reason tokens use snake/kebab case.  Normalise only for
    # classification; the original message remains the published evidence.
    text = re.sub(r"[_-]+", " ", str(message or ""))
    if str(verdict).upper() in {"NOT_INVOCABLE", "NOT_FOUND", "CRASHED",
                                "STALLED", "INVOCATION_ERROR"}:
        return EXECUTION_ERROR
    # R-0915-119 — THE SECOND CHANNEL, and it is not a prose clue.
    #
    # A checker with no `--json` report can still STATE its class, and this
    # recogniser accepts it ONLY when the same line carries the enumeration
    # that establishes it: the class token, a scanned COUNT of at least one,
    # and `found 0`. That is the checker making the claim with its evidence
    # attached, which is what guard (i) asks for — the prose is the
    # transport, never the basis. `_sa.sentence()` is the one writer of this
    # shape, so the claim and its reader cannot drift.
    m = _STRUCTURAL_LINE_RE.search(str(message or ""))
    if m:
        try:
            if int(m.group("scanned")) >= _sa.MIN_SCANNED:
                return NOT_APPLICABLE_BY_STRUCTURE
        except (TypeError, ValueError):      # pragma: no cover
            pass
    if _ZERO_RE.search(text):
        return ZERO_DENOMINATOR
    if _BLOCKED_RE.search(text):
        return BLOCKED_BY_UPSTREAM
    if _CAPABILITY_RE.search(text):
        return CAPABILITY_ABSENT
    if _EXTERNAL_RE.search(text):
        return EXTERNAL
    if _DECLARED_NA_RE.search(text):
        return DESIGN_DECLARED_NA
    # R-0915-15, LAST, AND ONLY ON A DECLARED BASIS.
    #
    # THE CORRECTION (2026-09-15, lane icspm3). As first landed this branch
    # granted DESIGN_DECLARED_NA from the SENTENCE ALONE, and that broke the
    # #1978 contract two shipped tests pin: a BANNER-ONLY rc-2 — a gate that
    # merely scanned the RTL and found nothing, `skip_kind: input-missing`,
    # e.g. break_handler_safety_check's "No break signals detected — not a
    # break-based protocol" — is INCOMPLETE / EXECUTION_ERROR, never an
    # UNEARNED N/A. I had made the prose its own basis, which is the very
    # laundering the docstring above refuses; the sentence was a better clue
    # than the old default, and a clue is not a declaration.
    #
    # A DESIGN_DECLARED_NA must carry the fact that says the design has no
    # such thing, and the tree has exactly two channels for it, both of which
    # are read BEFORE this point and neither of which needs the prose:
    #   * the gate STATES its class (`evidence.reason_class`, or
    #     `report_reason_class` off its own `--json`) — #2275 / #2276;
    #   * the P0 roster answers from the design's declarations and the gate is
    #     never invoked (`skip_kind: declaration-not-present`) — R-0915-19.
    # A caller that HAS a basis and wants the sentence read passes it here;
    # everything else keeps the fail-closed default, which is what the two
    # pinned tests assert.
    if _SUBJECT_ABSENT_RE.search(text) and _declared_basis(ev):
        return DESIGN_DECLARED_NA
    return EXECUTION_ERROR


def record_verdict(reason_class: str) -> str:
    """The P0 record verdict permitted for this reason class."""
    cls = normalise(reason_class)
    if cls in SKIP_ELIGIBLE:
        return "SKIP"
    if cls == BLOCKED_BY_UPSTREAM:
        return "BLOCKED"
    return "INCOMPLETE"


def p0_tier_for_reason_classes(reason_classes: list[str]) -> str:
    """Return PASS only when every non-verdict class is skip-eligible."""
    return ("INCOMPLETE" if any(normalise(c) not in SKIP_ELIGIBLE
                                for c in reason_classes) else "PASS")
