#!/usr/bin/env python3
"""verdict.py — the ONE step-verdict vocabulary of the flow, and its ONE cascade rule.

DESIGN
======

The owner's sentence, which is the whole specification (2026-09-16, R-0915-85)::

    do the right thing at once. 不要設計的疊床架屋，這樣以後會很難維護，
    而且疊床架屋，以後都會忘記為什麼要這樣設計。

So this module is NOT a translation table laid on top of the words that were
here before. The producers changed; the old words are gone; and the reason is
written here, where the design lives, because a reason kept somewhere else is a
reason the next person will not find.

WHY — the two runs that made the case, both measured, both on this fleet
---------------------------------------------------------------------

**subservient r26** (2026-09-16, `_lane_icsub2/c2_proj`). Every gate that looked
at the design PASSED. The run was published FAIL. The chain, copied out of the
run's own `reports/audit/phase23_completion_audit.json`::

    stage_on_pass_review --stage stage3   rc=2  verdict=NOT_CHECKED
                                          reason_class=BLOCKED_BY_UPSTREAM
                                          enforcement=DISCLOSED_INCOMPLETE
      -> step 37  "GDSII output"                    INCOMPLETE
      -> step 37.4  "Sign-off metrics aggregation"  PASS_VOIDED_BY_DEPENDENCY
      -> step 37.5ip "Digital Hardmacro Generation" PASS_VOIDED_BY_DEPENDENCY
      -> step 38    "Foundry Handoff"               PASS_VOIDED_BY_DEPENDENCY
      -> stage4_compliance rc 1  ->  run FAIL

Step 37's own artefact, `phase3/stage4/gds/subservient.gds`, was on disk and its
other five gates were green. NOTHING about the design failed. One review gate
declining to look was converted, by four different words in four different
programs, into a verdict about a chip.

The defect is not any one of those words. It is that a word meaning *nobody
measured this* was allowed to VOID work that was measured. `INCOMPLETE` did not
say what it did to its dependents; `PASS_VOIDED_BY_DEPENDENCY` did not say
whether the dependency had FAILED or merely gone unexamined; and no reader
could tell the two apart, because the vocabulary had no place to put the
difference.

**sha256 run16, pass 2** (2026-09-16, `_lane_icsha4/run16/sha256`). The LEC step
returned the SAME record twice::

    pass 1   lec_equivalence  INCONCLUSIVE, 481 unproven of 846, 8306 s  -> phase 2 FAIL
    pass 2   lec_equivalence  INCONCLUSIVE, the kept record,       2 s   -> booked SKIP
                                                                         -> phase 2 PASS_WITH_WAIVERS
                                                                         -> phase 3 launched on an
                                                                            UNPROVEN netlist

Identical evidence, opposite verdicts, because `SKIP` and `INCONCLUSIVE` were
two words in one bag and the second pass picked the cheaper one. Twenty-three
words is not richness; it is twenty-three places to launder a result.

Neither run was caught by the flow matrix suite or by the 3,700 fixture tests,
because neither defect exists in a fixture. They exist only when a real run
reaches a later step CARRYING an earlier step's word. That is why the rule
below is a function with tests in both directions, and not a convention.

THE FIVE VERDICTS, and nothing else
-----------------------------------

    PASS                the step ran and what it examined was good
    PASS_WITH_WAIVERS   the same, with named rows somebody must close
    FAIL                the step ran and what it examined was not good;
                        or a required artefact does not exist
    NOT_MEASURED        nobody measured it — `reason_class` says why
    NOT_APPLICABLE      the input says there is nothing here to measure —
                        `declared_by` says which line of the input says so

Every other distinction the old vocabulary carried is a FIELD on the record,
not a word in the verdict: see `StepVerdict` below, where each field's
docstring names the word it replaces and why that word had to go.

THE RULE FOR THE NEXT PERSON
----------------------------

**A new status word is a schema change, not a string.** If you are about to
write a sixth word, you are about to do what the 2026-09-16 runs above did.
What you actually have is one of:

  * a new REASON why something was not measured  -> add a `ReasonClass`
  * a new thing worth DISCLOSING about a pass    -> add a `Disclosure`
  * a new kind of outcome                        -> then it is a schema change:
    change `Verdict`, bump every report's `schema_version`, migrate every
    producer, and write here WHY, as this text does.

There is deliberately no alias map, no `_LEGACY_STATUS_MAP`, no tolerant
reader. `parse` REFUSES an unknown word by raising `UnknownVerdictWord`. A
reader that meets `SKIP` in a report is reading a report written by a producer
that has not been migrated, and that must be loud where it happens rather than
quietly meaning something.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: Bumped by every report schema that carries a step status. A report written
#: with `step_status_schema_version < 2` uses the deleted vocabulary and is
#: refused by `parse`, not translated.
SCHEMA_VERSION = 2


class UnknownVerdictWord(ValueError):
    """A status word outside the five. Raised, never absorbed.

    This is the schema refusal the DESIGN note promises. It exists so that the
    r26 chain cannot happen again by accident: a producer that still writes
    `INCOMPLETE` does not silently mean something to a reader, it stops the
    reader.
    """


class Verdict(str, enum.Enum):
    """The only five words a step may wear.

    `str`-valued so a record serialises to JSON as the bare word and so
    `record["status"] == Verdict.PASS` is true from either side — but the
    *set* is closed, and `parse` is the only door in.
    """

    #: The step ran; everything it examined was good. Replaces `PASS`, and also
    #: `VACUOUS_PASS` / `PARTIALLY-VACUOUS` / `STRUCTURE-ONLY` / `ADVISORY`,
    #: every one of which was a PASS carrying a fact about HOW MUCH was
    #: examined. That fact is now a `Disclosure` (see `Disclosure.VACUITY`),
    #: which is where a reader that cares can find it and where a reader that
    #: does not care cannot mistake it for a different outcome.
    PASS = "PASS"

    #: The step ran and passed, and named rows remain open that somebody must
    #: close. Replaces `PASS_WITH_WAIVERS`, `WAIVED`, `WAIVED-DEFERRED`,
    #: `DEFERRED` and `PASS_WITH_ATTRIBUTION`. The rows themselves live in
    #: `waiver_rows`; an integrator-attributed item lives in `attribution`.
    #: The old words differed only in WHICH list the row belonged to, which is
    #: a field, not an outcome.
    PASS_WITH_WAIVERS = "PASS_WITH_WAIVERS"

    #: The step ran and what it examined was NOT good, or a required artefact
    #: does not exist. Replaces `FAIL`, `MISSING`, `FAIL_RTL_REPAIR_INERT` and
    #: `STALE_BOARD_DETECTED`. `MISSING` is here and not under NOT_MEASURED
    #: deliberately: a declared output that was never produced is a defect of
    #: the run, not an absence of measurement — `reason_class` is
    #: `MISSING_ARTEFACT` and the rule is stated in `cascade_to_dependent`.
    FAIL = "FAIL"

    #: Nobody measured it. `reason_class` says why; `reason` says it in
    #: sentences. Replaces `SKIP`, `SKIPPED`, `SKIPPED-SETUP-REQUIRED`,
    #: `INCOMPLETE`, `INCONCLUSIVE`, `NOT_CHECKED`, `NOT_EXECUTED`,
    #: `NOT-MEASURED`, `NO_TOOL`, `ENV_UNAVAILABLE`, `BLOCKED`,
    #: `BLOCKED_BY_UPSTREAM`, `DEFERRED-BY-UPSTREAM`,
    #: `PASS_VOIDED_BY_DEPENDENCY`, `REFUSED`, `ERROR`, `STALLED` and
    #: `LEC_BUDGET_EXHAUSTED` — seventeen words that all said the same thing
    #: and were each read differently downstream. It blocks the run-level PASS
    #: (see `run_verdict`) and it VOIDS NOTHING (see `cascade_to_dependent`):
    #: that split is the r26 fix.
    NOT_MEASURED = "NOT_MEASURED"

    #: The design INPUT says there is nothing here to measure.
    #: `declared_by` names the line that says so. Replaces
    #: `SKIPPED-CONDITION`, `SKIPPED-BY-ENTRY`, `SKIPPED-BY-EXIT`,
    #: `OUT-OF-SCOPE-BY-ENTRY` and `NOT_APPLICABLE` / `N/A`.
    #:
    #: THE LINE BETWEEN THIS AND `NOT_MEASURED` IS THE WHOLE POINT, and it is
    #: not a matter of taste: N/A requires a DECLARATION, and `declared_by`
    #: must name it. "The tool was missing", "the file was not there", "the
    #: previous step refused" are NOT declarations — they are reasons nobody
    #: measured, and they are `NOT_MEASURED`. run16 laundered an INCONCLUSIVE
    #: proof into a skip precisely by blurring this line.
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReasonClass(str, enum.Enum):
    """WHY a `NOT_MEASURED` step was not measured. Required on every one.

    A `NOT_MEASURED` with no reason is the bag the old vocabulary was: it is
    refused by `StepVerdict.__post_init__`.
    """

    #: The tool is not in this environment. Replaces `NO_TOOL` and
    #: `ENV_UNAVAILABLE`. Env-fixable; says nothing about the design.
    TOOL_ABSENT = "tool_absent"

    #: The step had a declared time or resource budget and spent it without
    #: reaching a verdict. Replaces `LEC_BUDGET_EXHAUSTED` and the
    #: budget half of R-0915-5. A budget that is spent is not a proof.
    BUDGET_EXHAUSTED = "budget_exhausted"

    #: The tool ran to completion and could not decide. Replaces
    #: `INCONCLUSIVE`. THIS IS THE run16 REASON: an inconclusive equivalence
    #: proof is not a skip and is not a pass, in pass 1 or in pass 42.
    INCONCLUSIVE = "inconclusive"

    #: An upstream step refused to produce what this one reads, or declined to
    #: look. Replaces `BLOCKED_BY_UPSTREAM` and `DEFERRED-BY-UPSTREAM`.
    #: Assigned by `cascade_to_dependent` when the upstream is itself
    #: NOT_MEASURED *and* this step genuinely cannot run without it — never as
    #: a blanket void (see that function).
    UPSTREAM_REFUSED = "upstream_refused"

    #: An upstream step FAILED, so this step's own result certifies nothing.
    #: Replaces `PASS_VOIDED_BY_DEPENDENCY`, which never said whether the
    #: dependency had failed or merely gone unexamined — r26 was voided by the
    #: second while the word implied the first.
    UPSTREAM_FAILED = "upstream_failed"

    #: The step started, made no progress, and was abandoned. Replaces
    #: `STALLED`.
    STALLED = "stalled"

    #: An input the step reads is not on disk, so it never ran. Replaces
    #: `BLOCKED` (the `step_preflight` refusal), `SKIPPED-SETUP-REQUIRED` and
    #: `REFUSED`. Distinct from `MISSING_ARTEFACT`, which is about an OUTPUT
    #: this step owed and did not produce.
    INPUT_ABSENT = "input_absent"

    #: The step dispatched and the dispatch itself broke — an exception, a
    #: crash, a non-zero exit with no verdict. Replaces `ERROR` and the
    #: exception-handler half of `SKIP`.
    EXECUTION_ERROR = "execution_error"

    #: The step was never dispatched, and no other reason above fits.
    #: Replaces the bare `SKIP` / `SKIPPED` / `NOT_EXECUTED` that said only
    #: that something did not happen.
    NOT_EXECUTED = "not_executed"

    #: The step ran and examined PART of the population it is named for.
    #: Replaces `INCOMPLETE`. r26's step 37 wore this.
    PARTIAL_POPULATION = "partial_population"

    #: The step ran and NOT ONE member of its population returned a verdict —
    #: 0 of N, which a reader cannot tell from 245-of-246 when both wear one
    #: word. Replaces the `NOT-MEASURED` tier (#2063) as distinct from
    #: `INCOMPLETE`.
    NO_POPULATION = "no_population"

    #: A required OUTPUT of this step does not exist. Carried with
    #: `Verdict.FAIL`, not NOT_MEASURED — see `Verdict.FAIL`. Named in this
    #: enum because `reason_class` is the one place a reader looks for the
    #: shape of a non-PASS, whichever verdict carries it.
    MISSING_ARTEFACT = "missing_artefact"


class Disclosure(str, enum.Enum):
    """Informational facts about a result that are NOT outcomes.

    Every member here used to be a WORD in the status field, which is how a
    disclosure — the thing a run makes by being honest — came to cost more
    than silence (`_flow_verdict_tiers` says this at length about
    `STRUCTURE-ONLY`). A disclosure now sits beside the verdict and changes
    no arithmetic; a consumer that cares reads `disclosures`.
    """

    #: The step ran and examined nothing about THIS design — the artefact it
    #: audits was absent, or its clauses were all predicates. Disclosed BESIDE
    #: `NOT_MEASURED(no_population)`, which is the verdict such a step wears:
    #: the old `VACUOUS_PASS` spelled both halves into one word, and the half
    #: that mattered (nothing was measured) kept being read as the half that
    #: did not (it passed). Both halves are here, each in its own field.
    VACUITY = "vacuity"

    #: Some clauses examined the design and some examined nothing. Replaces
    #: `PARTIALLY-VACUOUS`. Disclosed beside `PASS`, because part of the
    #: population WAS examined and returned a verdict — which is precisely the
    #: distinction #2063 split out of `INCOMPLETE` and could not then express.
    PARTIAL_VACUITY = "partial_vacuity"

    #: The content examined came from a library default / template rather than
    #: from the design. Replaces `STRUCTURE-ONLY` and `PASS_STRUCTURE_ONLY`.
    #: Disclosed beside `PASS_WITH_WAIVERS`, because every number measured on a
    #: library default is a number about the default and somebody must come
    #: back and replace it — which is what a waiver row IS. Note the direction:
    #: `_flow_verdict_tiers` recorded that a tree DISCLOSING a library default
    #: scored BELOW one that silently said PASS. It cannot now, because the
    #: disclosure is not the verdict.
    STRUCTURE_ONLY = "structure_only"

    #: The step reports for information and cannot move any verdict. Replaces
    #: `ADVISORY`.
    ADVISORY = "advisory"

    #: The result was not computed in this run — a kept record from an earlier
    #: pass was reused. THE run16 DISCLOSURE: pass 2 reused pass 1's
    #: inconclusive proof and nothing in the report said so. A reused record
    #: keeps the verdict it had; it does not become cheaper for being fast.
    REUSED_RECORD = "reused_record"

    #: The step is a progress marker inside an iteration, not an outcome.
    #: Replaces `RTL_REPAIR_RETRY`.
    PROGRESS_MARKER = "progress_marker"


#: The reason classes that describe an environment or a run, not the design.
#: A reader deciding "is this the plugin's problem or the chip's" starts here.
ENVIRONMENTAL_REASONS = frozenset({
    ReasonClass.TOOL_ABSENT,
    ReasonClass.BUDGET_EXHAUSTED,
    ReasonClass.EXECUTION_ERROR,
    ReasonClass.STALLED,
})


@dataclass
class WaiverRow:
    """One named thing somebody must close before tapeout.

    `id` is what a reviewer greps for; `reason` is the sentence; `owner` is who
    closes it. The old vocabulary carried a waiver as the WORD `WAIVED` with
    the row, if any, somewhere else — which is how a waived step could reach a
    published verdict with no row to close.
    """

    id: str
    reason: str = ""
    owner: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "reason": self.reason, "owner": self.owner}


@dataclass
class StepVerdict:
    """One step's result: the verdict, plus every distinction the old words carried.

    Construct through the five classmethods below rather than by hand — they
    are what make the required field required.
    """

    #: One of the five. Never a string outside `Verdict`.
    verdict: Verdict

    #: The step this is about, for the report and for `cascade_to_dependent`'s
    #: messages.
    step_id: str = ""
    name: str = ""

    #: REQUIRED on `NOT_MEASURED`. Optional-but-conventional on `FAIL`
    #: (`MISSING_ARTEFACT`). Forbidden on the other three.
    reason_class: Optional[ReasonClass] = None

    #: The reason in sentences, for a human. Never parsed.
    reason: str = ""

    #: REQUIRED on `NOT_APPLICABLE`: the INPUT line / declaration that makes
    #: this step inapplicable, e.g. `"L1_DATASHEET.md:44 — no digital
    #: datapath declared"` or `"step_0_5ic_answers.json: deliverable=HARDMACRO"`.
    #: Replaces the `SKIPPED-CONDITION` word, which said a condition was unmet
    #: without ever saying which one — so nobody could check it.
    declared_by: str = ""

    #: REQUIRED non-empty on `PASS_WITH_WAIVERS`. A waived step with no row is
    #: the defect the word used to permit.
    waiver_rows: List[WaiverRow] = field(default_factory=list)

    #: An item this run attributes to the integrator rather than measuring
    #: itself (e.g. a DRC deck the foundry owns). Replaces
    #: `PASS_WITH_ATTRIBUTION`. Carried WITH `PASS_WITH_WAIVERS`: an attributed
    #: item is a row somebody owns, which is what a waiver row is.
    attribution: str = ""

    #: Informational, arithmetic-free. See `Disclosure`.
    disclosures: List[Disclosure] = field(default_factory=list)

    #: Free-form per-step payload the report already carried (durations,
    #: counts, gate records). Untouched by this module.
    extras: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.verdict = parse(self.verdict)
        if self.reason_class is not None:
            self.reason_class = ReasonClass(self.reason_class)
        self.disclosures = [Disclosure(d) for d in self.disclosures]
        if self.verdict is Verdict.NOT_MEASURED and self.reason_class is None:
            raise ValueError(
                f"NOT_MEASURED without a reason_class on step "
                f"{self.step_id or self.name!r} — an unreasoned NOT_MEASURED "
                f"is the undifferentiated bag this module deleted. Pick a "
                f"ReasonClass, or say NOT_APPLICABLE and name declared_by.")
        if self.verdict is Verdict.NOT_APPLICABLE and not self.declared_by:
            raise ValueError(
                f"NOT_APPLICABLE without declared_by on step "
                f"{self.step_id or self.name!r} — N/A is a claim about the "
                f"INPUT and must name the line that makes it. Without one it "
                f"is NOT_MEASURED(reason_class=not_executed), which is what "
                f"sha256 run16 pass 2 should have said.")
        if self.verdict is Verdict.PASS_WITH_WAIVERS and not (
                self.waiver_rows or self.attribution):
            raise ValueError(
                f"PASS_WITH_WAIVERS with neither waiver_rows nor attribution "
                f"on step {self.step_id or self.name!r} — a waived step with "
                f"no row to close reaches no must-close list, which is the "
                f"defect the bare word permitted.")

    # ── the five constructors ────────────────────────────────────────────
    @classmethod
    def pass_(cls, step_id: str = "", name: str = "", *,
              disclosures: Sequence[Disclosure] = (), **kw) -> "StepVerdict":
        return cls(Verdict.PASS, step_id, name,
                   disclosures=list(disclosures), **kw)

    @classmethod
    def pass_with_waivers(cls, step_id: str = "", name: str = "", *,
                          waiver_rows: Sequence[WaiverRow] = (),
                          attribution: str = "", **kw) -> "StepVerdict":
        return cls(Verdict.PASS_WITH_WAIVERS, step_id, name,
                   waiver_rows=list(waiver_rows), attribution=attribution, **kw)

    @classmethod
    def fail(cls, step_id: str = "", name: str = "", *, reason: str = "",
             reason_class: Optional[ReasonClass] = None, **kw) -> "StepVerdict":
        return cls(Verdict.FAIL, step_id, name, reason_class=reason_class,
                   reason=reason, **kw)

    @classmethod
    def missing_artefact(cls, step_id: str = "", name: str = "", *,
                         reason: str = "", **kw) -> "StepVerdict":
        """A required output does not exist. FAIL, by the rule in `Verdict.FAIL`."""
        return cls(Verdict.FAIL, step_id, name,
                   reason_class=ReasonClass.MISSING_ARTEFACT,
                   reason=reason, **kw)

    @classmethod
    def not_measured(cls, step_id: str = "", name: str = "", *,
                     reason_class: ReasonClass, reason: str = "",
                     **kw) -> "StepVerdict":
        return cls(Verdict.NOT_MEASURED, step_id, name,
                   reason_class=reason_class, reason=reason, **kw)

    @classmethod
    def not_applicable(cls, step_id: str = "", name: str = "", *,
                       declared_by: str, reason: str = "",
                       **kw) -> "StepVerdict":
        return cls(Verdict.NOT_APPLICABLE, step_id, name,
                   declared_by=declared_by, reason=reason, **kw)

    # ── predicates, so no consumer keeps its own list ────────────────────
    @property
    def is_green(self) -> bool:
        """The step delivered a result about the design that it stands behind."""
        return self.verdict in (Verdict.PASS, Verdict.PASS_WITH_WAIVERS)

    @property
    def blocks_run_pass(self) -> bool:
        """Does this step stop the RUN from being called a pass?

        FAIL and NOT_MEASURED do; NOT_APPLICABLE does not, and neither does a
        disclosure. See `run_verdict`.
        """
        return self.verdict in (Verdict.FAIL, Verdict.NOT_MEASURED)

    @property
    def cascades(self) -> bool:
        """Does this step's outcome change its DEPENDENTS' verdicts?

        ONLY `FAIL`. This one line is the r26 fix: a step nobody measured
        stops the run being called a pass and takes nothing else down with it.
        """
        return self.verdict is Verdict.FAIL

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "status": self.verdict.value,
            "step_id": self.step_id,
            "name": self.name,
        }
        if self.reason_class is not None:
            d["reason_class"] = self.reason_class.value
        if self.reason:
            d["reason"] = self.reason
        if self.declared_by:
            d["declared_by"] = self.declared_by
        if self.waiver_rows:
            d["waiver_rows"] = [w.to_dict() for w in self.waiver_rows]
        if self.attribution:
            d["attribution"] = self.attribution
        if self.disclosures:
            d["disclosures"] = [x.value for x in self.disclosures]
        if self.extras:
            d["extras"] = self.extras
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StepVerdict":
        """Read a step record. REFUSES an old word — see `parse`."""
        rc = d.get("reason_class")
        return cls(
            verdict=parse(d.get("status")),
            step_id=str(d.get("step_id") or d.get("id") or ""),
            name=str(d.get("name") or ""),
            reason_class=ReasonClass(rc) if rc else None,
            reason=str(d.get("reason") or ""),
            declared_by=str(d.get("declared_by") or ""),
            waiver_rows=[WaiverRow(**w) for w in (d.get("waiver_rows") or [])],
            attribution=str(d.get("attribution") or ""),
            disclosures=[Disclosure(x) for x in (d.get("disclosures") or [])],
            extras=dict(d.get("extras") or {}),
        )


def parse(word: Any) -> Verdict:
    """The ONE door into the vocabulary. An unknown word is a schema refusal.

    NO NORMALISATION, on purpose. The module this replaces had a `normalize`
    that upper-cased and swapped `_` for `-` because the producer wrote
    `VACUOUS_PASS` and the reports said `VACUOUS-PASS` — two spellings of one
    word, and a classifier that saw two answered differently about one step.
    With five words and one producer vocabulary there is nothing to
    normalise, and tolerating a second spelling is how a third arrives.
    """
    if isinstance(word, Verdict):
        return word
    try:
        return Verdict(word)
    except (ValueError, KeyError):
        raise UnknownVerdictWord(
            f"{word!r} is not one of the five step verdicts "
            f"{[v.value for v in Verdict]}. It is either a word from the "
            f"vocabulary R-0915-85 deleted — in which case the producer that "
            f"wrote it has not been migrated, and translating it here is the "
            f"'疊床架屋' the ruling forbids — or a new outcome, in which case "
            f"read the rule at the top of programs/verdict.py: a new status "
            f"word is a schema change, not a string."
        ) from None



def validate_step_row(row: Any) -> None:
    """Enforce the five-word contract on a RUNNER's own `StepResult` row.

    The runners keep their own lightweight dataclass (it carries durations,
    output files and per-step extras this module has no business knowing
    about), so they call this from `__post_init__` rather than re-deriving the
    rules. Same rules, one implementation.

    THREE REFUSALS, each naming the defect it prevents, and ONE derivation:

      * a word outside the five             -> `UnknownVerdictWord`
      * NOT_MEASURED with no `reason_class` -> the undifferentiated bag
      * NOT_APPLICABLE with no `declared_by`-> the run16 laundering

    and PASS_WITH_WAIVERS with no rows DERIVES one from the step's own name and
    detail rather than refusing. This is deliberate and is a STRENGTHENING: the
    old `WAIVED` word required no row at all, so a waived step could reach a
    published verdict with nothing on any must-close list. Every waived step
    now produces exactly one row a reviewer can grep for, and a site that knows
    better still passes its own rows.
    """
    status = parse(getattr(row, "status", None))
    rc = getattr(row, "reason_class", "") or ""
    if rc:
        ReasonClass(rc)          # refuses a reason nobody declared
    for d in (getattr(row, "disclosures", None) or ()):
        Disclosure(d)
    name = getattr(row, "name", "") or "<unnamed step>"
    if status is Verdict.NOT_MEASURED and not rc:
        raise ValueError(
            f"{name}: NOT_MEASURED without a reason_class. Every NOT_MEASURED "
            f"says WHY — pick one of {[r.value for r in ReasonClass]}.")
    if status is Verdict.NOT_APPLICABLE and not (
            getattr(row, "declared_by", "") or ""):
        raise ValueError(
            f"{name}: NOT_APPLICABLE without declared_by. N/A is a claim about "
            f"the INPUT and must name the line that makes it; without one the "
            f"honest word is NOT_MEASURED with a reason_class.")
    if status is Verdict.PASS_WITH_WAIVERS and not (
            getattr(row, "waiver_rows", None)
            or getattr(row, "attribution", "")):
        row.waiver_rows = [WaiverRow(
            id=name,
            reason=(getattr(row, "detail", "") or "")[:400] or
            "waived with no reason recorded at the site").to_dict()]

# ── THE CASCADE RULE ─────────────────────────────────────────────────────

def cascade_to_dependent(upstream: StepVerdict,
                         dependent_id: str = "",
                         dependent_name: str = "") -> Optional[StepVerdict]:
    """What an upstream step does to a step that DEPENDS on it.

    Returns the verdict the dependent must take, or `None` when the dependent
    is untouched and must run and report its own.

    THE RULE, in full, and it is short on purpose::

        upstream FAIL             -> dependent NOT_MEASURED(upstream_failed)
        upstream NOT_MEASURED     -> None  (the dependent runs and reports itself)
        upstream NOT_APPLICABLE   -> None
        upstream PASS / PWW       -> None

    Why a dependent of a FAIL is NOT_MEASURED and not FAIL: the dependent did
    not fail. Nothing examined it. Calling it FAIL would say the design failed
    twice for one defect, and `run_verdict` already refuses the run on the
    upstream FAIL. What the dependent must never be is a PASS: a step standing
    on a failed input certifies nothing, which is exactly what
    `PASS_VOIDED_BY_DEPENDENCY` was invented to say — and why that word is
    gone rather than renamed, because it was also applied when the upstream
    had merely not been measured.

    Why NOT_MEASURED voids nothing: subservient r26. Three steps that had
    produced and verified their own artefacts were voided because a review gate
    upstream declined to look. A step nobody measured is a hole in the report,
    not a verdict about everything downstream of it — `run_verdict` keeps the
    run off PASS, and the work that WAS done keeps the word it earned.
    """
    if upstream.verdict is Verdict.FAIL:
        return StepVerdict.not_measured(
            dependent_id, dependent_name,
            reason_class=ReasonClass.UPSTREAM_FAILED,
            reason=(f"dependency [{upstream.step_id or upstream.name}] "
                    f"{upstream.name or ''} = FAIL, so this step's own result "
                    f"certifies nothing about the design").strip())
    return None


def required_artefact_absent(step_id: str = "", name: str = "", *,
                             artefact: str = "") -> StepVerdict:
    """A declared output that does not exist. FAIL, with the reason named.

    Stated as its own function because it is the one place the rule "absent
    output is a FAIL, absent measurement is not" is decided, and because the
    old vocabulary had `MISSING` sitting in the same bag as `SKIP`.
    """
    return StepVerdict.missing_artefact(
        step_id, name,
        reason=f"required artefact does not exist: {artefact}" if artefact
        else "required artefact does not exist")


def review_gate_verdict(step_id: str, name: str, *, inputs_present: bool,
                        declined_reason: str = "") -> Optional[StepVerdict]:
    """A review gate runs whenever its inputs exist.

    Returns `None` when the gate must RUN (inputs are there), and
    `NOT_MEASURED(input_absent)` when they are not. There is no third answer,
    and in particular there is no "an earlier stage's review declined, so this
    one declines too" — that is the r26 chain, and it is not expressible here.
    """
    if inputs_present:
        return None
    return StepVerdict.not_measured(
        step_id, name, reason_class=ReasonClass.INPUT_ABSENT,
        reason=declined_reason or "the inputs this review reads are not on disk")


# ── the run-level roll-up ────────────────────────────────────────────────

#: Worst-first. `run_verdict` returns the first of these any scoped step wears.
#: NOT_MEASURED sits ABOVE both passes: a run holding an unmeasured sign-off
#: step is not a pass, whatever the measured steps say. It sits BELOW FAIL:
#: a measured defect outranks a hole.
RUN_PRECEDENCE: Sequence[Verdict] = (
    Verdict.FAIL,
    Verdict.NOT_MEASURED,
    Verdict.PASS_WITH_WAIVERS,
    Verdict.PASS,
)


def run_verdict(steps: Iterable[StepVerdict]) -> Verdict:
    """The RUN's word, from its steps'. Total, with no catch-all.

    `NOT_APPLICABLE` contributes nothing — it is the input saying there was
    never anything here, and a run of one applicable step and forty N/A ones
    is exactly as good as its one step.

    A run with NO contributing step at all is `NOT_MEASURED(no_population)`
    rather than `PASS`: an empty numerator is not a pass, and the catch-all
    `return "PASS"` at the bottom of the two runners' aggregators is the
    single most-cited hazard in their own comments.
    """
    worn = {s.verdict for s in steps if s.verdict is not Verdict.NOT_APPLICABLE}
    for v in RUN_PRECEDENCE:
        if v in worn:
            return v
    return Verdict.NOT_MEASURED


def run_verdict_record(steps: Sequence[StepVerdict]) -> Dict[str, Any]:
    """`run_verdict`, plus the causes — so a red run always names its cause.

    `audit_reconciliation` equation E1 already refuses a red run that names
    none; this makes the naming a property of the roll-up rather than of
    whoever remembers to build the list.
    """
    steps = list(steps)
    v = run_verdict(steps)
    return {
        "step_status_schema_version": SCHEMA_VERSION,
        "verdict": v.value,
        "causes": [s.to_dict() for s in steps if s.blocks_run_pass],
        "counts": {w.value: sum(1 for s in steps if s.verdict is w)
                   for w in Verdict},
        "disclosures": sorted({d.value for s in steps for d in s.disclosures}),
        "waiver_rows": [w.to_dict() for s in steps for w in s.waiver_rows],
    }



# ── the predicates consumers used to keep as their own lists ─────────────
#
# `_flow_verdict_tiers` (vibe-ic#634) existed because the PRODUCER and the
# CONSUMER of a step status each kept a list of which words meant "done", and
# the lists drifted: a step wearing `STRUCTURE-ONLY` was counted as done by the
# producer's arithmetic and was invisible to the ordering guard. Its answer was
# to DERIVE done-ness by subtraction from two negative sets, so a tier invented
# tomorrow would be adjudicated without anyone remembering to register it.
#
# That was the right answer to a vocabulary that could grow. This one cannot:
# `parse` refuses a sixth word. So the derivation goes too, and each predicate
# below is a one-line statement over the five. The MODULE is gone, not moved —
# a second classifier beside this one is the 疊床架屋 the ruling forbids.

#: The whole vocabulary, for a consumer that wants to assert against it.
#: Replaces `_flow_verdict_tiers.PRODUCER_STATUSES`, which was a hand-pinned
#: list of the producer's words kept in sync by a test.
PRODUCER_STATUSES = frozenset(v.value for v in Verdict)

#: The step is not claimed as done and is not held against the run. Replaces
#: `EXCUSED`, which held seven spellings of "skipped" plus two of "deferred".
EXCUSED = frozenset({Verdict.NOT_APPLICABLE.value})

#: The step is a defect or a hole — what keeps a run from being green.
#: Replaces `NON_GREEN`.
NON_GREEN = frozenset({Verdict.FAIL.value, Verdict.NOT_MEASURED.value})

#: The one word that satisfies a predecessor outright.
FULL_PASS = Verdict.PASS.value


def is_excused(status: Any) -> bool:
    """Out of the verdict's scope because the INPUT said so."""
    return parse(status) is Verdict.NOT_APPLICABLE


def is_non_green(status: Any) -> bool:
    """Keeps the run off a pass: a measured defect, or a hole."""
    return parse(status) in (Verdict.FAIL, Verdict.NOT_MEASURED)


def is_full_pass(status: Any) -> bool:
    return parse(status) is Verdict.PASS


def is_done_claim(status: Any) -> bool:
    """The step claims it delivered a result about the design.

    Derived by subtraction in `_flow_verdict_tiers` ("neither excused nor
    non-green"); stated directly here, and the two agree on every word that
    still exists. What changed is that `INCOMPLETE`, `NOT-MEASURED`,
    `VACUOUS-PASS` and `STRUCTURE-ONLY` USED to answer True to this — they were
    in neither negative set — so a step that had measured nothing was
    adjudicated as claiming to be done. Two of those are now
    `NOT_MEASURED` and answer False, which is the r26 correction reaching the
    ordering guard as well as the roll-up.
    """
    return parse(status) in (Verdict.PASS, Verdict.PASS_WITH_WAIVERS)


def is_qualified_done(status: Any) -> bool:
    """A done-claim that is not a full pass: rows remain open."""
    return parse(status) is Verdict.PASS_WITH_WAIVERS


def says_nothing_was_measured(status: Any) -> bool:
    """Replaces `NO_VERDICT_IN_SCOPE = {INCOMPLETE, NOT-MEASURED}`."""
    return parse(status) is Verdict.NOT_MEASURED


def done_claims_in(statuses: Iterable[Any]) -> set:
    return {parse(s).value for s in statuses if is_done_claim(s)}


# ── which TRACK a step is on, and whether it reaches the verdict ─────────
#
# Moved here verbatim in intent from `_flow_verdict_tiers`, which this module
# REPLACES rather than sits beside (the ruling: "do not create a second thing
# beside an existing one"). The two predicates answer the verdict SCOPE's
# questions — which track a step is on, and whether it counts — and they live
# with the vocabulary for the reason everything else does: a scope kept as a
# list in a reader drifts from the thing it is a list of.

#: The flow yaml's own stage word for the analog track. Read from the STEP,
#: never from a step-id allow-list: ids get renumbered and tracks grow steps,
#: and an allow-list goes quiet on exactly the step it did not know about.
ANALOG_STAGE = "stage_analog"


def _field(step: Any, name: str) -> str:
    """One field off a step record in either shape — object or dict.

    `flow_compliance_check` holds its steps as `StepResult` objects and writes
    them to the JSON report as dicts, and a predicate that saw only one of
    those would answer correctly in the producer and silently `False` in every
    reader.
    """
    raw = (step.get(name) if isinstance(step, dict)
           else getattr(step, name, ""))
    return str(raw or "")


def in_analog_track(step: Any) -> bool:
    """Is this canonical step part of the ANALOG track?

    Chip-AGNOSTIC and renumber-proof: the answer comes from the flow yaml's
    own `stage` vocabulary. `stage_mixed_signal` (M1-M4) is a DIFFERENT track
    and is deliberately not included.
    """
    return _field(step, "stage").strip().lower() == ANALOG_STAGE


def scoped_into_verdict(step: Any) -> bool:
    """Does this step reach the run verdict?

    ABSENT IS NOT FAILED. Only `NOT_APPLICABLE` is out of scope, and only
    because the INPUT said so and `declared_by` names where. Everything else
    is in — including a step wearing a word this module refuses, which reaches
    `parse` and stops the reader rather than passing quietly.
    """
    return parse(_field(step, "status")) is not Verdict.NOT_APPLICABLE


if __name__ == "__main__":  # pragma: no cover — the module is a library
    import json as _json
    import sys as _sys
    print(_json.dumps({
        "step_status_schema_version": SCHEMA_VERSION,
        "verdicts": [v.value for v in Verdict],
        "reason_classes": [r.value for r in ReasonClass],
        "disclosures": [d.value for d in Disclosure],
        "cascade": {
            "FAIL": "dependent -> NOT_MEASURED(upstream_failed)",
            "NOT_MEASURED": "dependent unaffected; run cannot be PASS",
            "NOT_APPLICABLE": "dependent unaffected; contributes nothing",
        },
    }, indent=2))
    _sys.exit(0)
