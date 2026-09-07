#!/usr/bin/env python3
"""phase1_expert_parse_track.py — the SECOND track of Phase 1.

ENFORCEMENT: advisory
(Advisory describes the FINDINGS. The track's EXECUTION is mandatory — see
"A track that cannot fail silently" below.)

The gap this closes
-------------------
The doctrine is program-first + AI-backup DUAL-TRACK CONVERGENCE: two tracks
solve the same problem INDEPENDENTLY, then their disagreement is root-caused.
Phase 1 shipped with one track.

  * `ai_deep_review_patches.json` is read by three gates
    (`l_doc_structured_field_count_check`,
    `phase1_doc_input_completeness_check`,
    `phase1_input_vs_generated_completeness_check`) and written by NOTHING.
  * No Phase-1 program invokes the IC Expert to parse anything. Of everything
    referencing `ic_expert_db` / `ic-expert-agent`, most is the DB's own
    tooling (query, capture, health audit, consistency gate, digest renderer);
    the rest is a scope guard, a benchmark dispatcher, a clock-form check, the
    PHASE-2 design runner, an enhancement emitter, a lint and a coverage check,
    and the backup-pack assembler. Not one is a Phase-1 parser.
  * `phase1_doc_one_shot_runner` has no AI-track call site.

So `_split_ai_vs_program()` in the completeness gate is not a track, it is a
COUNTER: it partitions L-doc content that already exists into "AI-patched" vs
"program-extracted" and totals it. With nothing writing the sidecar,
`ai_captured_tokens_count` can only ever be 0. A measured run:
`tokens_missing_everywhere: 52`, `ai_captured_tokens_count: 0`,
`alias_captured_tokens_count: 0` — which reads as the AI performing badly and
is actually the second track never having run at all (issue #312).

What this program is
--------------------
An INDEPENDENT reader of the same design input. It does not consult the
program track's L-docs to decide what SHOULD be there; it derives that from
expert knowledge and the design's own input, and only then compares.

  1. RETRIEVE — expert knowledge for this design, from the assets the IC
     Expert Agent owns: the design-class craft in
     `agents/ic_expert_db/ic_expert_db.json` (via `ic_expert_db_query`) and
     the distilled expert skills in `agents/ic-expert-agent.md` (via
     `_lesson_digest`). Same assets, same retrieval as the authoring path.
  2. EXPECT   — turn the applicable knowledge into CONCRETE, NAMED expectations:
     "this input should have produced <entry> in <layer>", each carrying the
     input evidence it rests on and the verbatim expert lesson it comes from.
  3. COMPARE  — check each expectation against what the program track actually
     emitted, ONE BY ONE.
  4. NAME     — every unmet expectation becomes its own finding, identified by
     rule AND subject. Not a count. `2 expectations unmet` tells nobody what to
     do; `EXPERT_TRACK_EXPECTATION_UNMET::nvm_program_supply::<macro>/<pin>`
     does.

Two sub-tracks, because knowledge splits into two kinds
------------------------------------------------------
  * DETERMINISTIC sub-track — expert knowledge that is machine-evaluable
    against the design's own input. Always runs, needs no LLM. This is where
    a convention with a decidable structural signature lives.
  * AI sub-track — the open-ended reading no rule captures. Handed to the
    `vibe-ic:ic-expert-agent` subagent through `ic_expert_backup_pack`, which
    is the assembler this doctrine already built for exactly this hand-off.

The AI sub-track's answer is CONVERGED, not filed (#312, second landing)
------------------------------------------------------------------------
The first landing of this track wired the AI half as far as reading the
subagent's answer back — and stopped there. Two measured defects made that
half inert, and between them they reproduced, one nesting level in, the exact
shape #312 was filed about:

  1. THE HAND-OFF WAS GATED ON A BACKEND IT DOES NOT USE. `ai_subtrack`
     returned early unless `llm_semantic_confirm.backend_available()` — the
     probe for an in-process `anthropic` SDK plus `ANTHROPIC_API_KEY`. Nothing
     downstream of that probe uses either: `ic_expert_backup_pack.assemble` is
     pure deterministic assembly (retrieve, render two digests, write a
     descriptor), and the answer is authored by the `vibe-ic:ic-expert-agent`
     SUBAGENT, which is a Claude Code agent and imports nothing. So on every
     host without that SDK the track (a) never wrote the pack, so no
     orchestrator could invoke the agent on it, and (b) returned BEFORE the
     `answer.is_file()` check, so an answer sitting on disk was never read.
     A self-sealing loop: the state could never be left, and the reason it
     could never be left was a probe for a backend the path does not touch.
     MEASURED: all 8 published fleet designs reported SKIPPED-CONDITION.

  2. A CONSUMED ANSWER WAS DEAD DATA. Even with the probe forced true and an
     answer present, `evaluate` built findings from `DETERMINISTIC_RULES`
     ALONE. `ai["expectations"]` was serialised into the report and read by
     nothing — `phase1_expert_track_evidence_check` reads only `.status`.
     MEASURED on a published design: an expectation naming a field no L-doc
     contains produced `verdict: PASS`, `findings: 0`, which the evidence
     check then reports as RAN_EMPTY — "the track ran and named NO findings —
     a real zero". The AI's disagreement was rendered as an agreement.

So the fix is the CONVERGE step this doctrine's name already promises:

  * the hand-off and the read-back are unconditional — an `inline_llm_backend`
    fact is RECORDED, and never again allowed to veto a path that does not
    use it;
  * every consumed expectation is checked against the program track's own
    L-docs by `converge_ai_expectation`, DETERMINISTICALLY;
  * every disagreement becomes a named finding of its own, in the same list,
    with the same shape and the same `about: "design"` classification as a
    deterministic one — so it reaches the printed run output and flips the
    evidence check from RAN_EMPTY to RAN.

The comparator is deterministic ON PURPOSE, and it IGNORES any `met` the
answer supplies. An AI half that scored itself would agree with itself, and
"a consumer that reads the file and always agrees" is not a second track — it
is the first track with a witness.

A track that cannot fail silently
---------------------------------
This repo has just measured that 62 of its 72 gates cannot block anything
(#306). A second track that quietly does nothing would be the same disease in
a new place, so the failure modes are pinned:

  * THE AI HALF HAS NOT READ — the sub-track reports `HANDOFF_EMITTED` (the
    pack is written, the subagent has not answered) and emits a NAMED FINDING
    saying so. It is in the findings list, printed, and in the report. It is
    never absent, and never mistaken for "nothing to report". The whole track
    reports `INCOMPLETE` and exits `AWAITING_EXIT_CODE` (4): creating work for
    an expert is not the same event as consuming the expert's answer
    (issue #1973), and it is not a failed run either (issue #2014 D1). The
    report carries an `awaiting` block naming the subagent, the pack and the
    action that ends the wait; `execution.disposition` is `AWAITING`.
    This state replaced `SKIPPED-CONDITION`, which named the wrong fact: the
    obstacle was never a missing LLM, it was that nobody had invoked the agent
    — and the old wording pointed a reader at a host capability instead of at
    the one action that advances the track.
  * THE TRACK ITSELF FAILS — exit 1. The report is a MANDATORY output: the
    runner treats a missing or unparseable report as a Phase-1 failure. So the
    findings are advisory, but running is not optional.
  * THE AI HALF ANSWERED IN A SHAPE THIS CONSUMER CANNOT READ — verdict
    INCOMPLETE, exit 1, a named finding, and the printed `INCOMPLETE:` sentinel.
    NOT `CONSUMED`, and under no combination of the other half's results a
    `PASS`. This state was previously invisible: `data.get("expectations")`
    followed by `exps if isinstance(exps, list) else []` mapped every
    unexpected shape onto the empty list, and the record then read `CONSUMED —
    read 0 expectation(s)`, which is verbatim what an agent with nothing to
    say produces. MEASURED on a real run: an expert review that named two
    cross-layer defects in its own schema was flattened to `[]` and published
    as `verdict PASS, blocking False, 0 findings`. The answer existed and was
    discarded — worse than an ordinary silent pass, because the work was done.
    The refusal NAMES the top-level keys that did arrive, so a reader sees a
    schema mismatch and not an empty answer.
  * NOTHING WAS DECIDED — verdict INCOMPLETE, exit 1, with the reason stated
    per rule. Decided on the DENOMINATOR (`examined_expectations` —
    deterministic expectations from applicable rules plus every converged AI
    one), not on "did a rule apply": a rule can apply and produce no
    expectation, and the AI half can answer with an empty list. Both are zero
    denominators, and a zero denominator cannot complete the expert track —
    this repo's own `gate_zero_denominator_refuses_check`.

EXECUTION CREDIT (issue #1973) AND THE THIRD STATE (issue #2014 D1)
-------------------------------------------------------------------
The earlier runner contract accepted rc 2 as execution and reduced the whole
question to "did a JSON report get written?". That credited
`HANDOFF_EMITTED`, a schema-refused answer, and a genuinely empty answer as a
completed expert track. None of them is credited now: all three are
`INCOMPLETE`, `execution.complete` is false, and the printed `INCOMPLETE:`
sentinel says so. Design findings remain advisory after a real answer is
consumed; the existence and non-zero consumption of that answer are mandatory
execution evidence.

CREDIT AND FAILURE ARE DIFFERENT AXES, and #1973's repair moved both at once.
Withholding credit is right. Making the wait a FAILED RUN was not: a program
cannot spawn the subagent, so `HANDOFF_EMITTED` is what EVERY program-only
invocation produces, and D1's gate clause runs this program directly. MEASURED
on live main v1.16.87, clean Path-A project, program only: rc 1 -> Step D1
`FAIL` ("program failed: phase1_expert_parse_track ."), classified DESIGN_FACT
/ gate-reached-verdict, with step 1 recorded `derived-from-upstream (D1)`. D1 is
the flow's unconditional first step, so that is every design failing the front
door on a state none of them can avoid — a fact about the protocol reported as
a fact about the design.

So the two axes are separated, and the exit code carries three states, not two:
`AWAITING_EXIT_CODE` (4) for the hand-off awaiting its agent, 1 for a record
that cannot be read as either credit or a stated wait, 0 for a real reading.
See `AI_AWAITING_STATES`. The pending set is an ALLOW-LIST: a new failure state
inherits 1.

THE EXPECTATION'S OWN SCOPE IS CHECKED (#2127)
----------------------------------------------
An expectation carries a `layer` and a `field_path`. The comparator read the
first and printed the second. MEASURED on a published Phase-1 run: all 46
consumed expectations named a `field_path` under a top-level key
(`integration.` on L9, `constraints.` on L19) that the addressed layer document
does not declare at any depth — so every finding said

    expected L9.integration.register_map.offsets to carry: <requirement>.
    The program track produced: L9 does not carry [...]

about a field nothing had looked at. The whole layer was searched; the path was
decoration. Two consequences, both closed here:

  * THE PATH IS CHECKED. `field_path_status` compares it against the paths the
    layer document declares, and an expectation naming an undeclared one is
    REFUSED BY NAME (`RULE_AI_FIELD_PATH_UNDECLARED`), `about: "track"`. The
    refusal is ADDITIVE — `met` is still decided over the whole layer — because
    a guard that also moved verdicts could not be told apart from a comparator
    change. MEASURED: 46 of 46 refused on that run; every one of them is an
    authoring defect that had no name.

  * A MISS IS NOT AUTOMATICALLY AN EXTRACTION GAP. When the addressed layer
    misses and ANOTHER layer the program track wrote carries every expected
    token, the fact WAS extracted and the expectation asked the wrong layer.
    That is `RULE_AI_MISSCOPED`, `about: "track"`, naming the owning layer —
    never `about: "design"`. MEASURED on the same run: 11 of 43 disagreements,
    with owners L1_DATASHEET, L2_FRS and L4_REGMAP. Reporting those as missing
    extractions said the tree lacked facts it demonstrably had, and pointed a
    reader at an extractor for a defect that lives in the expectation.

The remedy for a mis-scoped expectation is its `field_path`, or a stated
layer-contract defect — never an extractor. A program cannot decide which of
the two, so the finding names both and decides neither.

On NOT writing the AI-patch sidecar
-----------------------------------
This track deliberately does NOT write
`phase1/ai_deep_review_patches.json`, even though that file's missing writer is
what exposed the missing track. The three gates that read it MERGE its contents
into the very haystack they then measure for completeness. A track that writes
its own expectations there would be scoring itself: tokens it supplied would
come back as "captured", and coverage would rise by exactly the amount the
track invented. That is the failure mode #309 named `rail_undeclared` — a
design manufacturing 100% coverage against rails that exist only inside its own
mapping.

The report records `ai_patch_sidecar_present` so the missing writer stays a
VISIBLE, separate fact rather than an inference. Closing it is a real task; it
needs a source of patches that is independent of the gate measuring them.

chip-AGNOSTIC. §4.05: reads design INPUT and the design's own generated L-docs.
Never an oracle, a golden artefact or a harness.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _path_layout as _pl  # noqa: E402
# Module level for `SUBAGENT_TYPE` alone — the name of the agent the
# `awaiting` block has to hand a reader. `ai_subtrack` keeps its own
# guarded import for `assemble`, whose failure is a reportable state.
import ic_expert_backup_pack as _pack  # noqa: E402
import nvm_program_supply_intent as _nps  # noqa: E402

PROGRAM = "phase1_expert_parse_track"
VERSION = "1.0.0"

_EXPERT_DB = (_HERE.parent / "agents" / "ic_expert_db" / "ic_expert_db.json")

RULE_UNMET = "EXPERT_TRACK_EXPECTATION_UNMET"
RULE_AI_SKIPPED = "EXPERT_TRACK_AI_SUBTRACK_SKIPPED"
# A disagreement the AI half named and the comparator confirmed. Deliberately a
# SEPARATE rule id from the deterministic `RULE_UNMET`: a reader must be able to
# tell which track raised a finding, or the two tracks stop being two.
RULE_AI_UNMET = "EXPERT_TRACK_AI_EXPECTATION_UNMET"
# An answer entry the comparator cannot decide either way. It is a finding
# about the TRACK, never silence: an undecidable expectation that vanished
# would make the AI half look like it agreed.
RULE_AI_UNUSABLE = "EXPERT_TRACK_AI_EXPECTATION_UNUSABLE"
# An answer file that PARSES but is not the shape this consumer reads. Its own
# rule id, because the remedy is different from every other one here: nothing
# about the DESIGN is in question, an answer exists and this consumer could not
# read it. Naming it `SKIPPED` would say the agent never answered, which is the
# opposite of what happened.
RULE_AI_ANSWER_SCHEMA_MISMATCH = "EXPERT_TRACK_AI_ANSWER_SCHEMA_MISMATCH"
# The agent answered, and its `expectations` list was genuinely empty. A real
# reading, and a real zero — reported so it can never be read as coverage.
RULE_AI_ANSWER_EMPTY = "EXPERT_TRACK_AI_ANSWER_EMPTY"
# The PACK ITSELF was not assembled (#2094). Its own rule id and `about:
# "track"`, because nothing about the DESIGN is in question: the hand-off went
# out carrying no target module and no interface contract for a class the
# expert DB profiles, i.e. an empty context that READS as an assembled pack.
# Every expectation the agent then writes is authored from the design input
# alone; a record that does not say so credits the pack for coverage it did not
# supply.
RULE_PACK_NOT_ASSEMBLED = "EXPERT_TRACK_PACK_NOT_ASSEMBLED"
# An expectation whose `field_path` names a path the layer it addresses does
# NOT declare (#2127). Its own rule id and `about: "track"`, because nothing
# about the DESIGN is in question: the expectation asked for a field that does
# not exist, and until #2127 the path was carried straight into the finding
# text as though it had been checked. MEASURED on a published Phase-1 run: all
# 46 consumed expectations named a `field_path` under a top-level key
# (`integration.` / `constraints.`) that neither addressed layer declares, and
# the findings read "expected L9.integration.register_map.offsets to carry ..."
# — an authoritative sentence about a field nothing ever looked at.
RULE_AI_FIELD_PATH_UNDECLARED = "EXPERT_TRACK_AI_EXPECTATION_FIELD_PATH_UNDECLARED"
# An expectation the addressed layer does not satisfy, where ANOTHER layer the
# program track wrote carries every one of its expected tokens (#2127). Its own
# rule id and `about: "track"`, because the fact IS extracted: reporting it as
# a design finding says the tree is missing something it demonstrably has.
# MEASURED on the same run: 11 of 43 disagreements — including 5 of the 10 the
# issue classifies as asking the wrong layer — have such a layer.
RULE_AI_MISSCOPED = "EXPERT_TRACK_AI_EXPECTATION_MISSCOPED"
# An expectation naming a layer THE L-DOC TAXONOMY DOES NOT DECLARE (#2150).
# `about: "track"`, its own rule id, and deliberately NARROW.
#
# The wider reading was tried first and is wrong. "The root has no such
# document" is NOT by itself a track defect: `l_doc_taxonomy` declares 28
# layers, and a layer it declares that the root does not carry is the PROGRAM
# TRACK failing to emit a document its own taxonomy says applies — a real
# disagreement, and issue #312 pinned it as one on the grounds that silence
# there would be the original defect exactly. That test is right and this
# landing leaves its verdict alone.
#
# What the taxonomy CANNOT account for is a layer that does not exist in the
# contract at all. Then nothing was pointed at: the expectation addresses a
# document this plugin never emits for any class, so the miss is a fact about
# the EXPECTATION (or about the artefact it was computed from), and filing it
# as a design gap says the tree lacks a fact whose home the tree has never
# had. That is #2150 F11's shape — a decision table computed over a different
# Phase-1 root, whose OWNER cells name leaves the judged artefact lacks.
#
# MEASURED on the surviving opentitan_aes root (24 files / 23 codes; the
# taxonomy declares 28): `L24_SIGNOFF` IS declared by the taxonomy, so it
# stays a disagreement and only gains the taxonomy fact and an owning-layer
# answer; a name the taxonomy does not declare is refused here by name.
RULE_AI_LAYER_ABSENT = "EXPERT_TRACK_AI_EXPECTATION_LAYER_ABSENT"
# A SPLIT expectation whose grammar is self-contradictory or empty (#2150).
# Reuses `RULE_AI_UNUSABLE` rather than inventing a token: an expectation the
# comparator cannot decide is one situation, and it already has a name. What
# is new is that a split can be undecidable in ways a single expectation
# cannot — no branches, a branch with no layer, a branch that itself splits,
# or a parent that states BOTH a split and its own `expected_tokens`.
SPLIT_KEY = "sub_expectations"
#: `field_path_status` / `layer_present` on a SPLIT parent. The parent
#: addresses no single field and no single layer, so answering DECLARED or
#: True for it would be a reading of one branch reported as a reading of the
#: expectation. The branches carry their own, per branch.
SPLIT_MARKER = "SPLIT"
# An expectation the author has WITHDRAWN, with the reason it was withdrawn
# (#2127, second addendum; the disposition #2132 reaches for 12 of its 30 rows).
# A withdrawn expectation is a DECISION, and a decision that leaves no trace is
# indistinguishable from an expectation the agent never wrote — so it is
# recorded, named and counted, and it is `about: "track"` because nothing about
# the design is in question.
RULE_AI_WITHDRAWN = "EXPERT_TRACK_AI_EXPECTATION_WITHDRAWN"
# A withdrawal carrying NO reason. REFUSED: the expectation is converged as if
# it had never been withdrawn, AND the refusal is named. Withdrawal is the one
# operation that can remove a row from what this track reports, so it is the
# one that must not be silent — "never silently delete" is not satisfied by a
# marker that says only that something was deleted.
RULE_AI_WITHDRAWN_NO_REASON = "EXPERT_TRACK_AI_EXPECTATION_WITHDRAWN_WITHOUT_REASON"

#: #2164. The retrieval query was built from a design input this reader could
#: not open. About the TRACK, never about the design: a pack assembled from
#: nothing says nothing about the chip, and the number derived from it is not a
#: measurement of the expert DB's coverage.
RULE_INPUT_NOT_READABLE = "EXPERT_TRACK_DESIGN_INPUT_NOT_READABLE"

# ── the AI sub-track's status vocabulary ────────────────────────────────────
#
# FIVE tokens, not three, because the three collapsed two pairs of genuinely
# different outcomes into one word each — and the report's ONLY downstream
# consumer (`phase1_expert_track_evidence_check`) reads the token and nothing
# else, so a collapsed token is a fact that no longer exists anywhere.
#
#   CONSUMED         an answer was read AND it carried expectations
#   CONSUMED_EMPTY   an answer was read and its `expectations` list was EMPTY.
#                    A real answer and a real zero. Split from CONSUMED because
#                    "the expert had nothing to add" is a legitimate result
#                    that a reader must be able to see AS a zero — under one
#                    token it is indistinguishable from a full reading, and
#                    that is the whole disease this track was built to cure.
#   SCHEMA_MISMATCH  the answer parses as JSON but is not `{"expectations":
#                    [...]}`. NOT a reading, NOT consumed, and never a PASS.
#                    The refusal NAMES the top-level keys that did arrive, so
#                    the reader sees a schema mismatch rather than an empty
#                    answer. MEASURED: one real expert review (verdict "gaps",
#                    complete false, two named cross-layer defects) used its own
#                    schema with no `expectations` key, was coerced to `[]` by
#                    `exps if isinstance(exps, list) else []`, recorded as
#                    "CONSUMED — read 0 expectation(s)", and published as
#                    verdict PASS / 0 findings. The answer existed and was
#                    discarded; a zero denominator passed instead of refusing.
AI_HANDOFF_EMITTED = "HANDOFF_EMITTED"
AI_CONSUMED = "CONSUMED"
AI_CONSUMED_EMPTY = "CONSUMED_EMPTY"
AI_SCHEMA_MISMATCH = "ANSWER_SCHEMA_MISMATCH"
AI_ERROR = "ERROR"
#: The states in which the AI half actually DELIVERED A READING. Everything
#: else — a refused answer, an error, a pack nobody answered — is not one.
AI_READ_STATES = frozenset({AI_CONSUMED, AI_CONSUMED_EMPTY})

# ── the THIRD state: waiting is not failing (#2014 D1) ──────────────────────
#
# `ai_subtrack` is a TWO-PASS protocol and says so in its own hand-off message:
# pass one writes the pack and reports HANDOFF_EMITTED — "invoke subagent
# vibe-ic:ic-expert-agent … and re-run to consume its answer". A PROGRAM cannot
# spawn that subagent, so pass one is what every non-agent invocation produces.
#
# MEASURED on live main v1.16.87, a clean Path-A project, program-only:
#
#     phase1_expert_parse_track .        -> rc 1
#     flow_compliance_check .            -> Step D1 FAIL
#                                           "program failed:
#                                            phase1_expert_parse_track ."
#                                           classified DESIGN_FACT /
#                                           gate-reached-verdict
#     step 1 (Spec-to-RTL)               -> "derived-from-upstream … (D1)"
#
# D1 is the flow's first step and has no `condition:`, so a rc that no
# single-pass input can avoid makes the front door unpassable for every design
# at once. That is not a fact about any design.
#
# THREE states, not two. `phase1_one_shot_runner._expert_track_disposition`
# already draws this line for the RUNNER's exit code (#2014, 1aa24ef268):
# CREDITED / PENDING / DEFECT. It could not draw it for the FLOW, because D1's
# gate clause runs THIS program directly and reads THIS exit code, where
# "nobody has answered yet" and "the answer is unreadable" were one number.
#
#   * AWAITING — a state the protocol defines and the next pass leaves.
#     Exit `AWAITING_EXIT_CODE`. Never credited (`execution.complete` stays
#     False, the verdict word stays INCOMPLETE, the `INCOMPLETE:` sentinel is
#     printed), and never a bare PASS in the roll-up: `flow_compliance_check`
#     maps rc 4 + that sentinel onto the #599 INCOMPLETE tier, which is the
#     word this repo already coined for "not audited, and someone must return".
#   * DEFECT   — ERROR, ANSWER_SCHEMA_MISMATCH, CONSUMED_EMPTY, or a status
#     this reader does not know. Still exit 1.
#
# THE SET IS AN ALLOW-LIST, deliberately: a producer that grows a new failure
# state inherits `1`, never the wait.
#
# CONSUMED_EMPTY IS NOT IN IT, and that is not an oversight. HANDOFF_EMITTED is
# an ORDERING state — the second pass has not happened. CONSUMED_EMPTY is a
# MEASUREMENT state — the pass happened and decided nothing, which is the zero
# denominator `gate_zero_denominator_refuses_check` exists to refuse. Only the
# first is left by re-running.
AI_AWAITING_STATES = frozenset({AI_HANDOFF_EMITTED})

#: Exit code for AWAITING. Not 0 (a naive `rc == 0` consumer must not credit an
#: unanswered hand-off), not 1 (that is a failed run), not 2 (`VACUOUS_PASS`
#: means "nothing applied", and #599 measured that this state is the opposite:
#: the input WAS applicable), not 3 (`_WAIVER_EXIT_CODE`).
AWAITING_EXIT_CODE = 4

#: The three dispositions, recorded in the report so no consumer has to infer
#: one from an exit code. Same words as the runner's, on purpose.
DISPOSITION_CREDITED = "CREDITED"
DISPOSITION_AWAITING = "AWAITING"
DISPOSITION_DEFECT = "DEFECT"

_JSON_TYPE_NAMES = {
    dict: "object", list: "array", str: "string",
    bool: "boolean", int: "number", float: "number", type(None): "null",
}


def json_type_name(value: Any) -> str:
    """What a reader of the answer file would call this value's type."""
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


def answer_schema_mismatch(data: Any) -> Optional[Dict[str, Any]]:
    """`None` if the answer is the shape this consumer reads; otherwise a
    STATED description of what arrived instead.

    The description names the TOP-LEVEL KEYS PRESENT, never only the key that
    was wanted. "no expectations key" and "no expectations key, but here are
    the ten keys it does carry, including `cross_layer_inconsistencies`" send a
    reader to two completely different places, and only the second one is where
    the answer actually is.
    """
    if not isinstance(data, dict):
        return {
            "json_type": json_type_name(data),
            "top_level_keys": [],
            "why": (f"the answer's top level is a JSON "
                    f"{json_type_name(data)}, not an object"),
        }
    keys = sorted(str(k) for k in data)
    if "expectations" not in data:
        return {"json_type": "object", "top_level_keys": keys,
                "why": "the answer object carries no `expectations` key"}
    if not isinstance(data["expectations"], list):
        return {"json_type": "object", "top_level_keys": keys,
                "why": (f"`expectations` is a "
                        f"{json_type_name(data['expectations'])}, not a list")}
    return None

# Text extensions that count as design INPUT for expert retrieval.
_INPUT_TEXT_EXTS = (".md", ".txt", ".rst", ".adoc", ".yaml", ".yml", ".json")
_INPUT_TEXT_CAP = 200_000     # keep retrieval bounded on a large spec


# ── expert assets ───────────────────────────────────────────────────────────

def expert_db_lesson(ic_class: str,
                     db_path: Path = _EXPERT_DB) -> Optional[str]:
    """The verbatim first lesson of one expert-DB class.

    Looked up BY CLASS rather than through the lexical top-k. A rule that
    embodies a specific piece of craft must quote THAT craft; letting ranking
    decide would make a finding's justification depend on how the design's
    prose happened to score."""
    try:
        db = json.loads(db_path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    for e in db.get("entries") or []:
        if isinstance(e, dict) and e.get("ic_class") == ic_class:
            lessons = e.get("lessons") or []
            if lessons:
                return lessons[0]
    return None


#: `input_text_report` status codes (#2164). FIVE states, and the reason there
#: are five is that four of them used to be spelled as the same empty string.
#: "I could not read it" and "I read it and it was empty" are different facts,
#: and a retrieval query built from the first is not a measurement of anything.
INPUT_READ = "READ"                       #: at least one character was read
INPUT_NO_TREE = "NO_INPUT_TREE"           #: <project>/input does not exist
INPUT_NO_FILES = "NO_FILES_AT_ALL"        #: the tree exists and holds no files
INPUT_UNREADABLE_FORMAT = "UNREADABLE_FORMAT"   #: files, none in a read format
INPUT_READ_AND_EMPTY = "READ_AND_EMPTY"   #: readable files, all of them empty

#: why a file under `input/` contributed nothing.
SKIP_UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
SKIP_ORACLE_PATH = "ORACLE_PATH"          #: deliberate §4.05 exclusion
SKIP_UNREADABLE = "UNREADABLE"            #: the file exists and would not open


def input_text_report(project: Path) -> Dict[str, Any]:
    """The design-input prose AND an account of what was left out.

    THE DEFECT THIS CLOSES, MEASURED (#2164). `input_text` returned a string
    and nothing else, so a project whose entire design input is a PDF returned
    `""` — indistinguishable from a project with no input at all, and from one
    whose files are genuinely empty. That empty string was then handed to
    `ic_expert_db_query.query` as a retrieval QUERY and to the AI sub-track as
    a PROMPT, and every consumer read the result as a measurement. Measured on
    the published corpus: TWELVE projects across five registered classes are in
    that state today, and one of them is a design of a class whose expert-pack
    profile was landed on a population that counted it.

    Returns the text (identical to what `input_text` returns) plus `status`,
    the files that were READ, the files that were SKIPPED with the reason and
    the size of each, and the unsupported suffixes with their counts. The
    account is what makes a zero legible: a caller can say WHICH formats it
    could not read instead of reporting an empty query as an empty design.
    INPUT only (§4.05) — the oracle exclusion is itself recorded, as a
    deliberate skip rather than as a failure."""
    parts: List[str] = []
    read: List[str] = []
    skipped: List[Dict[str, Any]] = []
    total = 0
    truncated = False
    root = project / "input"
    if not root.is_dir():
        return {"text": "", "chars": 0, "status": INPUT_NO_TREE,
                "files_read": [], "files_skipped": [],
                "unsupported_suffixes": {}, "truncated": False,
                "input_root": str(root)}
    n_files = 0
    for q in sorted(root.rglob("*")):
        if not q.is_file():
            continue
        n_files += 1
        try:
            rel = q.relative_to(root)
        except ValueError:
            continue
        try:
            size = q.stat().st_size
        except OSError:
            size = None
        if _nps._is_oracle_parts(rel.parts):
            skipped.append({"path": str(rel), "suffix": q.suffix.lower(),
                            "bytes": size, "reason": SKIP_ORACLE_PATH})
            continue
        if q.suffix.lower() not in _INPUT_TEXT_EXTS:
            skipped.append({"path": str(rel), "suffix": q.suffix.lower(),
                            "bytes": size, "reason": SKIP_UNSUPPORTED_FORMAT})
            continue
        try:
            t = q.read_text(errors="replace")
        except OSError:
            skipped.append({"path": str(rel), "suffix": q.suffix.lower(),
                            "bytes": size, "reason": SKIP_UNREADABLE})
            continue
        read.append(str(rel))
        parts.append(t)
        total += len(t)
        if total >= _INPUT_TEXT_CAP:
            truncated = True
            break
    text = "\n".join(parts)[:_INPUT_TEXT_CAP]
    unsupported: Dict[str, int] = {}
    for sk in skipped:
        if sk["reason"] == SKIP_UNSUPPORTED_FORMAT:
            unsupported[sk["suffix"] or "<none>"] = \
                unsupported.get(sk["suffix"] or "<none>", 0) + 1
    if text:
        status = INPUT_READ
    elif n_files == 0:
        status = INPUT_NO_FILES
    elif read:
        # Files WERE opened and read; they simply had no characters. This is
        # the only empty that is a fact about the design rather than about the
        # reader, and it is the one that must not be lumped in with the others.
        status = INPUT_READ_AND_EMPTY
    else:
        status = INPUT_UNREADABLE_FORMAT
    return {"text": text, "chars": len(text), "status": status,
            "files_read": read, "files_skipped": skipped,
            "unsupported_suffixes": unsupported, "truncated": truncated,
            "input_root": str(root)}


def input_text(project: Path) -> str:
    """Concatenated design-input prose — the retrieval query and the AI
    sub-track's prompt. INPUT only (§4.05).

    Kept as-is for every caller that only wants the text; it is
    `input_text_report(project)["text"]` and nothing else. A caller that has to
    tell an unreadable input from an empty one — which is every caller that
    reports a number derived from it — uses the report."""
    return input_text_report(project)["text"]


def input_readability_disposition(report: Dict[str, Any]) -> Dict[str, Any]:
    """What a CONSUMER must record about the query it was given.

    Three answers, and they are not interchangeable. `MEASURED` — there was
    text and the retrieval ran on it. `NOT_MEASURED` — there was no text, and
    the reason names the formats that were skipped, so a reader learns "the
    spec is a PDF this reader cannot open" rather than "the expert DB offered
    nothing". `MEASURED_EMPTY` — files were read and were genuinely empty,
    which IS a fact about the design and is the only zero that may be reported
    as one."""
    st = report.get("status")
    if st == INPUT_READ:
        return {"retrieval_input": "MEASURED", "chars": report.get("chars", 0)}
    if st == INPUT_READ_AND_EMPTY:
        return {"retrieval_input": "MEASURED_EMPTY", "chars": 0,
                "reason": ("every readable design-input file was opened and "
                           "contained no characters — an empty design input, "
                           "not an unreadable one")}
    unsup = report.get("unsupported_suffixes") or {}
    if st == INPUT_UNREADABLE_FORMAT:
        listed = ", ".join(f"{k} x{v}" for k, v in sorted(unsup.items())) or "none"
        why = (f"the design input holds {len(report.get('files_skipped') or [])} "
               f"file(s), NONE of them in a format this reader opens "
               f"({listed}); readable formats are "
               f"{', '.join(_INPUT_TEXT_EXTS)}")
    elif st == INPUT_NO_TREE:
        why = (f"there is no design-input tree at "
               f"{report.get('input_root')} to read")
    else:
        why = (f"the design-input tree at {report.get('input_root')} holds no "
               f"files at all")
    return {"retrieval_input": "NOT_MEASURED", "chars": 0, "reason": why,
            "unsupported_suffixes": unsup,
            "readable_formats": list(_INPUT_TEXT_EXTS)}


#: `registered_ic_class_disposition` reason codes. See its docstring: these
#: are THREE different zeros and the record must not spell them the same way.
CLASS_CLASSIFIED = "CLASSIFIED"
CLASS_NOT_YET_CLASSIFIABLE = "NOT_YET_CLASSIFIABLE"
CLASS_UNCLASSIFIABLE = "UNCLASSIFIABLE"
CLASS_NOT_MEASURED = "NOT_MEASURED"

#: The L-doc prefixes `ic_class_profile._detect_ic_class_infer` requires before
#: any classification branch can be reached: with L1, L2 and L3 all absent it
#: returns at its own `no_l1_l2_l3_docs` branch without consulting a single
#: feature. Named here so this module asks the question STRUCTURALLY (do those
#: documents exist?) instead of matching the classifier's `decisive_evidence`
#: prose — prose cannot distinguish "the classifier had nothing to read" from
#: "the classifier read and was unconvinced", which is the very distinction
#: this disposition exists to make.
_CLASSIFIER_REQUIRED_L_DOCS = ("L1_", "L2_", "L3_")


def _classifier_has_documents(project: Path) -> bool:
    """True when this tree carries something the classifier can classify FROM.

    Either a class already persisted at `reports/ic_class.json` (#435 — then the
    answer is knowable by definition), or at least one of the three L documents
    the inference needs before it can reach any branch.
    """
    if (project / "reports" / "ic_class.json").is_file():
        return True
    try:
        docs = _pl.generated_docs_dir(project)
    except Exception:  # noqa: BLE001 — an unreadable layout is not a class fact
        return False
    if not docs.is_dir():
        return False
    return any(next(docs.glob(f"{pfx}*.json"), None) is not None
               for pfx in _CLASSIFIER_REQUIRED_L_DOCS)


def registered_ic_class_disposition(project: Path) -> Dict[str, Any]:
    """The design's REGISTERED ic_class AND, when there is none, WHY.

    `registered_ic_class` returns `None` for three different reasons and the
    consumer's record spelled all three the same way — "no registered ic_class
    was supplied, or the name is not one the registry carries". Only one of the
    three is a statement about the DESIGN:

      CLASSIFIED            a registered class was detected; class-first fires.
      NOT_YET_CLASSIFIABLE  this tree carries no persisted class and none of the
                            L documents the classifier reads, so the class is
                            not KNOWABLE at this point in the run. That is an
                            ORDERING fact about when the track was asked — the
                            L docs are step D1's own `required_outputs` and the
                            track is D1's own gate clause, so a run that reaches
                            the track through the flow has them. Reported under
                            its own name because a reader who sees the flat
                            "no registered ic_class" cannot tell this apart from
                            a design the classifier genuinely could not place,
                            and the two need opposite responses: this one is
                            fixed by asking later, that one never is.
      UNCLASSIFIABLE        the classifier DID read L documents and still
                            answered `unknown` — its fail-closed verdict, and a
                            real reading. `unknown` is not a class: handing it
                            to the expert DB would look up a profile for a name
                            that means "we do not know".
      NOT_MEASURED          the classifier could not be consulted at all. Never
                            defaulted to one of the other three: "could not read
                            it" is not "read it and there was nothing".

    #2144. §4.05: reads the classifier's answer about the project, never an
    oracle or golden output.
    """
    try:
        import ic_class_profile as _icp
        profile = _icp.detect_ic_class(project) or {}
    except Exception as exc:  # noqa: BLE001 — a missing class is context, never a fail
        return {"ic_class": None, "reason_class": CLASS_NOT_MEASURED,
                "reason": (f"the classifier could not be consulted ({exc}); "
                           f"this is an unread classifier, not an unclassified "
                           f"design")}
    cls = profile.get("ic_class")
    if cls and cls != "unknown":
        return {"ic_class": cls, "reason_class": CLASS_CLASSIFIED,
                "reason": "the run's own detected class"}
    if not _classifier_has_documents(project):
        return {
            "ic_class": None,
            "reason_class": CLASS_NOT_YET_CLASSIFIABLE,
            "reason": (
                "this tree carries no persisted class and none of the "
                f"{'/'.join(p.rstrip('_') for p in _CLASSIFIER_REQUIRED_L_DOCS)} "
                "documents the classifier reads, so the class is not knowable "
                "HERE — the expert track was asked before step D1 produced the "
                "L documents it classifies from. The class-first pack selection "
                "cannot fire on this tree, and that is a fact about WHEN the "
                "track ran, not about the design"),
        }
    return {
        "ic_class": None,
        "reason_class": CLASS_UNCLASSIFIABLE,
        "reason": ("the classifier read the L documents and answered "
                   "`unknown` — its fail-closed verdict, and a real reading"),
    }


def registered_ic_class(project: Path) -> Optional[str]:
    """The design's REGISTERED ic_class, or None when it has not been detected.

    Read through `ic_class_profile.detect_ic_class`, which returns the profile
    PERSISTED at `reports/ic_class.json` when one exists (#435's persist-once
    contract) — this track must see the class the run itself detected, not a
    second inference taken at a different point in the run. `"unknown"` is the
    classifier's fail-closed answer and is NOT a class: returning it would make
    the expert DB look up a profile for a name that means "we do not know".

    WHY None was returned is `registered_ic_class_disposition` (#2144). This
    function keeps its single-value contract — every caller of it wants a class
    or nothing — and the reason goes in the RECORD, where a reader looks.
    """
    return registered_ic_class_disposition(project).get("ic_class")


def retrieved_classes(prompt: str, k: int = 5, ic_class=None) -> List[Dict[str, Any]]:
    """What the expert DB surfaces for THIS design — recorded so a reviewer can
    see which knowledge the track had in hand, including when it had none.

    CLASS-FIRST (#2094): when `ic_class` names a class the DB profiles, this is
    the CONFINED retrieval, so the record shows the same classes the pack
    carried. Recording an unconfined list beside a confined pack would make the
    record disagree with the artefact it describes."""
    if not prompt.strip():
        return []
    try:
        import ic_expert_db_query as _db
        return [{"ic_class": h.get("ic_class"),
                 "score": round(float(h.get("score", 0) or 0), 2)}
                for h in (_db.query(prompt, k=k, ic_class=ic_class) or [])
                if isinstance(h, dict)]
    except Exception:  # noqa: BLE001 — retrieval is context, never a hard fail
        return []


# ── the deterministic expectation rules ─────────────────────────────────────
#
# Each rule reads the design INPUT, decides what the L-docs SHOULD contain, and
# reports whether the program track produced it. A rule returns a list of
# expectation dicts:
#
#   id            stable identity  <rule>::<subject>
#   layer         which L doc should carry it
#   field_path    where in that layer
#   requirement   what should be there, in words
#   evidence      the INPUT facts the expectation rests on
#   expert_source the DB class + verbatim lesson it comes from
#   met           did the program track produce it?
#   observed      what the program track actually has

_NVM_DB_CLASS = "nvm-fuse-array"


def rule_nvm_program_supply(project: Path) -> Dict[str, Any]:
    """A programmable non-volatile memory that the design means to BURN needs
    its programming supply to arrive from outside, so that supply has to exist
    as a terminal in the pin/pad inventory AND as a rail in the power-intent
    layer, distinct from the core rail.

    The deterministic half of this rule is `nvm_program_supply_intent`, kept as
    its own module rather than inlined here: one convention must have exactly
    one evaluation, so that if this is ever promoted to a blocking gate the two
    cannot drift. A drifting supply rule is how the pin gets lost.

    NOTHING STOPS ON THIS. See `deferred_enforcement` in the report.
    """
    lesson = expert_db_lesson(_NVM_DB_CLASS)
    assessment = _nps.assess(project)
    out: Dict[str, Any] = {
        "rule": "nvm_program_supply",
        "expert_db_class": _NVM_DB_CLASS,
        "expert_lesson": lesson,
        "applicable": bool(assessment.get("applicable")),
        "reason": assessment.get("reason", ""),
        "expectations": [],
    }
    if not assessment.get("applicable"):
        return out

    l21 = _nps.load_l21(project)
    rails = _nps.declared_rails(l21)
    boundary = assessment.get("boundary", {}).get("names", [])
    intent = assessment.get("program_intent", {})

    for pin in assessment.get("pins", []):
        subject = f"{pin['master']}/{pin['pin']}"
        evidence = [
            f"input/pdk_local: macro {pin['master']} declares this pin "
            f"USE POWER in its own LEF, alongside "
            f"{len(assessment['multi_supply_macros'].get(pin['master'], []))} "
            f"distinct supply pins in total",
            f"input RTL: instantiates {pin['master']}",
            f"input RTL: programming-control logic present "
            f"({', '.join(intent.get('role_categories', []))})",
        ]

        # Expectation A — the chip boundary carries a terminal for it.
        hit_pin = _nps._rail_token_match(pin["pin"], boundary)
        out["expectations"].append({
            "id": f"nvm_program_supply::boundary_terminal::{subject}",
            "layer": "L1_DATASHEET",
            "field_path": "fields.pinout / fields.pin_table "
                          "(or L5.pads / L9.top_level_ports)",
            "requirement": (
                f"a boundary terminal for {subject} — the programming supply "
                f"is sourced outside the die and sits above the core rail, so "
                f"it can only enter through a package pin or a probe pad"),
            "evidence": evidence,
            "expert_source": {"db_class": _NVM_DB_CLASS, "lesson": lesson},
            "met": bool(hit_pin) or pin["status"] == "declared_gap",
            "observed": (f"terminal {hit_pin!r}" if hit_pin
                         else "declared integration gap"
                         if pin["status"] == "declared_gap"
                         else "no corresponding terminal in the design's "
                              "boundary inventory"),
        })

        # Expectation B — the power-intent layer declares it as its own rail.
        hit_rail = _nps._rail_token_match(pin["pin"], rails)
        out["expectations"].append({
            "id": f"nvm_program_supply::power_intent_rail::{subject}",
            "layer": "L21_POWER_INTENT",
            "field_path": "fields.power_rails[] / fields.power_domains[]",
            "requirement": (
                f"a rail entry for {subject}, distinct from the core rail — "
                f"the back end builds the supply network from this layer, and "
                f"a supply it never sees is a supply it never builds"),
            "evidence": evidence,
            "expert_source": {"db_class": _NVM_DB_CLASS, "lesson": lesson},
            "met": bool(hit_rail),
            "observed": (f"rail {hit_rail!r}" if hit_rail
                         else f"power-intent layer declares "
                              f"{rails if rails else 'no rails at all'}"),
        })
    return out


DETERMINISTIC_RULES = (rule_nvm_program_supply,)


# ── converge: the AI half's expectations vs the program track's L-docs ──────
#
# This is the half of "dual-track convergence" that the doctrine's name
# promises and the first landing did not have. It is DELIBERATELY deterministic
# and it deliberately recomputes `met` rather than trusting the answer, so the
# AI half cannot mark its own homework.

# The same relative-path family `hardmacro_supply_intent.load_l21` walks, kept
# as one list so a project layout that one of them can read is readable by
# both. A layer the program track wrote where only one of the two looks is a
# disagreement manufactured by path drift, not by the design.
_L_DOC_DIRS = ("generated_docs", "phase1/generated_docs",
               "input/generated_docs")


def _norm(text: Any) -> str:
    """Lower-case, with every run of non-alphanumerics collapsed to one space.

    Not `hardmacro_supply_intent._rail_token_match`: that answers "does this
    ONE pin name equal one of these rail strings", and this answers "does this
    possibly-multi-word phrase occur in this whole document". Reusing a matcher
    across two different questions is how a matcher ends up wrong for both.
    """
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def phrase_present(phrase: str, haystack: str) -> bool:
    """Is a phrase present in the text, ANCHORED AT TOKEN BOUNDARIES?

    Both sides are reduced to alphanumeric tokens; the phrase matches iff some
    CONTIGUOUS run of haystack tokens concatenates to the phrase's tokens
    concatenated. Two properties follow, and both are load-bearing:

      * a phrase can never match INSIDE a token — `VDD` does not match
        `AVDD_REF`, and `REF` does not match `REFHI`. That is the #309
        mis-binding bug, and a comparator that made it would report agreement
        on the strength of a substring, which is a false CLEAN;
      * a separator difference is not a disagreement — `1.8 V` matches
        `1.8V`, `TRIM_SEL` matches `trim sel`. Spec prose and JSON field names
        spell the same fact both ways constantly, and scoring that as a gap
        would fill an advisory channel with noise until nobody read it, which
        is its own way of becoming a report nobody opens.

    The run length is bounded by the phrase's own length (every token
    contributes at least one character), so this stays linear in the document.
    """
    want = _norm(phrase).split()
    if not want:
        return False
    target = "".join(want)
    toks = _norm(haystack).split()
    for i in range(len(toks)):
        acc = ""
        for j in range(i, len(toks)):
            acc += toks[j]
            if len(acc) > len(target):
                break
            if acc == target:
                return True
    return False


def _layer_haystack(blob: Any) -> str:
    """Every key and every scalar of an L-doc, flattened to searchable text.

    Keys count: a layer can carry a fact as a field NAME as legitimately as it
    can carry it as a value, and a comparator blind to one half of the document
    would report a gap the design does not have."""
    out: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None:
            out.append(str(node))

    walk(blob)
    return " ".join(out)


def _haystack_units(blob: Any) -> List[str]:
    """The layer's searchable text as INDEPENDENT UNITS — one per key, one per
    scalar — rather than as one joined document (#2127 addendum, #2128).

    `_layer_haystack` joins every key and every scalar with spaces, and
    `phrase_present` then matches any CONTIGUOUS run of that joined stream. The
    join is not a boundary, so a phrase can be assembled out of parts that
    belong to different fields. MEASURED on neutral fixtures:

        {"x": ["power", "domain"]}   phrase "power domain"  -> True
        {"mode": "fast"}             phrase "mode fast"     -> True
        {"busy": {}, "mode": {}}     phrase "busy mode"     -> True

    None of those three documents states the phrase. A run's expected token
    list is then satisfied by an accident of ADJACENCY, and the accident is
    invisible because the report prints the phrase, not where it matched.

    Matching per UNIT keeps every property `phrase_present` was built for — a
    phrase still cannot match inside a token (`REF` does not match `REFHI`),
    and a separator difference is still not a disagreement (`1.8 V` matches
    `1.8V` within one unit) — and removes only the cross-field assembly."""
    out: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None:
            out.append(str(node))

    walk(blob)
    return out


def present_in_units(phrase: str, units: List[str]) -> bool:
    """Is the phrase present WITHIN ONE unit? Never assembled across two."""
    return any(phrase_present(phrase, u) for u in units)


def subtree_at(blob: Any, field_path: Any, layer: Any = None) -> Optional[list]:
    """The node(s) the expectation's `field_path` addresses, or None.

    A list on the way down contributes each of its ELEMENTS, so
    `registers.offset` on a list of register records resolves to every
    record's `offset` — the reading the expectation means. Returns None (not
    []) when the path does not resolve, because "the path is not there" and
    "the path is there and empty" are different answers and this repo has
    measured what happens when a program supplies one for the other.
    """
    key = _normalise_field_path(field_path, layer)
    if not key:
        return None
    # The emitter nests the payload under `fields`; some protocol synthesizers
    # write it flat (`l_doc_consumer_contract.l_doc_fields` accepts both). An
    # expectation may spell either, and a spelling difference between a schema
    # and an expectation is not a defect — this repo has measured what scoring
    # one as a defect does to an advisory channel. Both spellings are tried,
    # HERE and only here, so the resolver and the status can never disagree.
    for candidate in _field_path_spellings(key):
        nodes = _walk_field_path(blob, candidate)
        if nodes is not None:
            return nodes
    return None


def _field_path_spellings(key: str) -> List[str]:
    """The one path, in both the nested and the flat spelling."""
    if key.startswith("fields."):
        return [key, key[len("fields."):]]
    return [key, "fields." + key]


def _walk_field_path(blob: Any, key: str) -> Optional[list]:
    """One literal walk of one spelling. `None` when a segment is absent."""
    nodes: List[Any] = [blob]
    for seg in key.split("."):
        nxt: List[Any] = []
        for node in nodes:
            stack = [node]
            while stack:
                cur = stack.pop()
                if isinstance(cur, list):
                    stack.extend(cur)
                elif isinstance(cur, dict):
                    for k, v in cur.items():
                        if str(k).lower() == seg:
                            nxt.append(v)
        if not nxt:
            return None
        nodes = nxt
    return nodes


def resolve_layer_file(project: Path, layer: str) -> Optional[Path]:
    """The L-doc the AI half named, tolerating how it spelled the layer.

    An expert naming `L21` and one naming `L21_POWER_INTENT` mean the same
    layer. Matching on the leading `L<number>` token rather than the full file
    stem keeps a spelling difference from being scored as a design gap — while
    still refusing a layer that does not exist at all."""
    want = (layer or "").strip()
    if not want:
        return None
    m = re.match(r"^\s*(l\s*\d+)", want.lower())
    key = re.sub(r"\s+", "", m.group(1)) if m else None
    for rel in _L_DOC_DIRS:
        d = project / rel
        if not d.is_dir():
            continue
        exact = d / f"{want}.json"
        if exact.is_file():
            return exact
        if not key:
            continue
        for p in sorted(d.glob("*.json")):
            stem = p.stem.lower()
            if stem == key or stem.startswith(key + "_"):
                return p
    return None



# ── the field_path guard (#2127) ────────────────────────────────────────────
#
# `field_path` was carried into the finding text and read by nothing. That is
# not a cosmetic gap: the sentence a reader gets is
#
#     expected L9.integration.register_map.offsets to carry: <requirement>.
#     The program track produced: L9 does not carry [...]
#
# which states that a NAMED field of a NAMED layer is empty. MEASURED on a
# published Phase-1 run: `L9_INTEGRATION_SPEC.json` has no `integration` key at
# any depth, so no such field was ever looked at — the whole layer was searched
# and the path was decoration. An expectation can therefore name any path at
# all and the report will repeat it as fact.
#
# So the path is CHECKED against what the layer document declares, and an
# expectation naming an undeclared one is refused BY NAME. The refusal is
# ADDITIVE — `met` is decided exactly as before, over the whole layer — because
# a guard that also moved verdicts could not be told apart from a comparator
# change, and this one has to be readable on its own.


def _normalise_field_path(field_path: Any, layer: Any = None) -> str:
    """The path as a comparable key: no indices, no layer prefix, lower case.

    A layer-qualified path (`L4_REGMAP.registers[].offset`) means the same
    field as the bare one, and refusing the qualified spelling would refuse
    exactly the repair this guard exists to ask for — an expectation re-scoped
    onto the layer that owns the fact naturally writes that layer into the
    path.
    """
    text = re.sub(r"\[[^\]]*\]", "", str(field_path or "")).strip().strip(".")
    if not text:
        return ""
    parts = [seg for seg in text.split(".") if seg.strip()]
    if (len(parts) > 1
            and re.match(r"^l\d+[a-z]?(_[a-z0-9_]+)?$", parts[0].lower())):
        parts = parts[1:]          # a leading layer token is addressing, not a field
    return ".".join(parts).lower()


def field_path_status(blob: Any, field_path: Any, layer: Any = None) -> str:
    """`DECLARED`, `UNDECLARED`, or `ABSENT` when the expectation named none.

    DERIVED FROM `subtree_at`, and deliberately not from a second walk that
    collects "the paths this document declares". One convention, one
    evaluation: an earlier revision of this landing had exactly that second
    walk, and the two answers disagreed within a day — the collector accepted
    a `fields.`-elided path that the resolver could not reach, so a path read
    DECLARED and then fell back to a whole-layer search anyway. Two answers
    about one path is the same disease as a field_path nothing reads.

    Nothing here looks at the path's VALUE: an empty declared field is a design
    question, an undeclared one is an authoring question, and conflating them
    is how the second became invisible.
    """
    if not _normalise_field_path(field_path, layer):
        return "ABSENT"
    return "DECLARED" if subtree_at(blob, field_path, layer) is not None \
        else "UNDECLARED"


def emitted_layer_files(project: Path) -> List[Path]:
    """Every L-doc the program track wrote for this project, in one list.

    The FIRST readable `_L_DOC_DIRS` entry wins outright. Two directories are a
    staging difference, not two layer sets, and merging them would let a stale
    copy of a layer answer for the live one.
    """
    for rel in _L_DOC_DIRS:
        d = project / rel
        if d.is_dir():
            files = sorted(p for p in d.glob("*.json") if p.is_file())
            if files:
                return files
    return []


#: How deep an authoring schema enumerates. Two segments is what an
#: expectation writes in practice (`fields.notes`, `records.offset`) and it
#: keeps the pack bounded; the DEPTH IS STATED in the schema, so a reader
#: knows a path is absent from the list because it is deeper, not because the
#: layer does not declare it.
AUTHORING_PATH_DEPTH = 2
#: Per-layer cap, also stated. A truncated list that did not say so would be
#: read as "these are all the paths", which is the exact defect this schema
#: exists to end, one level over.
AUTHORING_PATHS_PER_LAYER = 200


def declared_field_paths(blob: Any, layer: Any = None,
                         max_depth: int = AUTHORING_PATH_DEPTH) -> List[str]:
    """The dotted paths this layer document declares, to `max_depth`.

    SELF-VERIFIED AGAINST `subtree_at`, which is what `field_path_status` —
    the guard — resolves with. Every candidate this walk produces is resolved
    before it is emitted, and one that does not resolve is dropped: the schema
    an author is handed must be exactly the set the guard accepts, and this
    module has already measured what two answers about one path cost. An
    earlier revision of the #2127 landing collected "the paths this document
    declares" in a second walk, the two disagreed within a day, and a path
    read DECLARED and then fell back to a whole-layer search anyway.

    So this is not a second authority. It is a candidate generator whose
    output is filtered by the one authority there is.
    """
    out: List[str] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, list):
            for v in node:
                walk(v, prefix, depth)
            return
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            out.append(path)
            walk(v, path, depth + 1)

    walk(blob, "", 1)
    seen: set = set()
    kept: List[str] = []
    for path in out:
        if path in seen:
            continue
        seen.add(path)
        if subtree_at(blob, path, layer) is not None:
            kept.append(path)
    return sorted(kept)


def authoring_schema(project: Path) -> Dict[str, Any]:
    """THE DECLARATION AN EXPECTATION AUTHOR IS HANDED (#2150 F5).

    THE DEFECT THIS CLOSES, measured on the pack this track itself emits.
    `ic_expert_backup_pack` wrote a HARD-CODED two-entry contract —
    `{"L9": "integration specification", "L19": "constraints and
    implementation context"}` — for a project whose Phase-1 root carries 24
    layer documents, and an `answer_contract` documenting `field_path` as
    `"optional field path"` and nothing else. An author handed that pack can
    address only two of the twenty-four layers, and can only INVENT a path out
    of the two prose words it was given. That is where `integration.*` and
    `constraints.*` came from, and why the #2127 guard then refused 46 of 46
    field_paths as undeclared: the pack manufactured the very authoring defect
    the guard was built to catch, and the guard reported the author.

    A guard that refuses at review time and a schema that refuses at authoring
    time are the same declaration read at two moments. This is that
    declaration, derived from the project's own emitted L-docs and filtered
    through `subtree_at` — so a path this schema lists is a path the guard
    accepts, by construction rather than by agreement.

    NOT_MEASURED when no L-doc is readable: an empty schema would be an author
    told that no layer declares anything, which is a false statement about the
    root rather than an absent one.
    """
    files = emitted_layer_files(project)
    if not files:
        return {"status": "NOT_MEASURED",
                "reason": ("no L-doc was found under "
                           f"{list(_L_DOC_DIRS)}, so no authoring schema can "
                           "be derived — this is 'could not read the root', "
                           "never 'read it and no layer declares anything'"),
                "layers": {}}

    import l_doc_taxonomy as _tax
    purpose = {}
    for spec in _tax.L_DOCS_V2:
        purpose[spec.full_name] = f"{spec.title} — {spec.description}"

    layers: Dict[str, Any] = {}
    unreadable: List[str] = []
    for f in files:
        try:
            blob = json.loads(f.read_text(errors="replace"))
        except (OSError, ValueError) as exc:
            # Named, never silently absent: a layer nobody could read is not a
            # layer that declares nothing.
            unreadable.append(f"{f.stem}: {exc.__class__.__name__}")
            continue
        paths = declared_field_paths(blob, f.stem)
        layers[f.stem] = {
            "purpose": purpose.get(f.stem, "no taxonomy entry declares this "
                                           "layer name"),
            "declared_field_path_count": len(paths),
            "declared_field_paths": paths[:AUTHORING_PATHS_PER_LAYER],
            "truncated": len(paths) > AUTHORING_PATHS_PER_LAYER,
        }
    return {
        "status": "OK",
        "schema": "vibeic.phase1-expert-expectation-authoring.v1",
        "phase1_root": phase1_root_identity(project),
        "unreadable": unreadable,
        "enumeration": {
            "max_depth": AUTHORING_PATH_DEPTH,
            "per_layer_cap": AUTHORING_PATHS_PER_LAYER,
            "basis": ("every path here was resolved with the SAME resolver "
                      "the review-time guard uses, so a path this schema "
                      "lists is a path the guard accepts. A path absent from "
                      "a layer's list is either deeper than max_depth or not "
                      "declared — the counts say which"),
        },
        "rule": ("an expectation's `field_path` MUST be one this schema lists "
                 "for the layer it addresses, or be omitted entirely. A path "
                 "no layer declares is refused BY NAME at review time and the "
                 "reading silently falls back to the whole layer, so the "
                 "finding then describes a field nothing looked at"),
        "layers": layers,
    }


def refuse_undeclared_field_path(schema: Dict[str, Any], layer: Any,
                                 field_path: Any) -> Optional[str]:
    """The AUTHORING-time half of the guard: why this path may not be written.

    `None` when the path is acceptable. A REASON when it is not — never a
    bare boolean, because the author has to be told which layer was consulted
    and what it does declare, or the refusal costs more than it saves.

    An ABSENT field_path is acceptable: the expectation then addresses the
    whole layer and says so. That is a weaker claim, not an authoring error,
    and refusing it here would make this schema stricter than the guard it is
    supposed to be the other reading of.
    """
    if schema.get("status") != "OK":
        return None          # nothing was read; refusing on that is a guess
    if not str(field_path or "").strip():
        return None
    layers = schema.get("layers") or {}
    stem = None
    want = str(layer or "").strip().lower()
    if want:
        # TWO PASSES, EXACT FIRST — mirroring `resolve_layer_file`, which
        # tries the exact filename before falling back to the `L<number>`
        # token. One pass is not the same function: two layer documents share
        # a numeric prefix (`L8_RTL_CONSTANTS` and `L8_TIMING_WAVEFORM`), so a
        # single loop that accepts either match returns whichever sorts first
        # and answers about the wrong document. MEASURED on a 504-path sweep
        # of a real root: 502 agreed with the guard and the 2 that did not
        # were both L8, both this collision. Compare identities, not names.
        exact = [n for n in sorted(layers) if n.lower() == want]
        stem = exact[0] if exact else None
        if stem is None:
            m = re.match(r"^\s*(l\s*\d+)", want)
            key = re.sub(r"\s+", "", m.group(1)) if m else None
            if key:
                pref = [n for n in sorted(layers)
                        if n.lower() == key or n.lower().startswith(key + "_")]
                stem = pref[0] if pref else None
    if stem is None:
        return (f"no layer named {layer!r} is in this Phase-1 root; it "
                f"carries {sorted(layers)}")
    declared = layers[stem]["declared_field_paths"]
    norm = _normalise_field_path(field_path, layer)
    if any(_normalise_field_path(d) == norm for d in declared):
        return None
    if layers[stem]["truncated"]:
        # The list is not the whole set, so a miss is not a proof. Say so
        # rather than refuse on an enumeration that admits it is partial.
        return None
    return (f"{stem} declares no field_path {field_path!r}. It declares "
            f"{declared[:20]}"
            + (" (first 20 of "
               f"{layers[stem]['declared_field_path_count']})"
               if len(declared) > 20 else "")
            + ". Address a path this layer declares, or omit `field_path` and "
              "state that the expectation is about the whole layer.")


def taxonomy_layer_stems() -> List[str]:
    """Every L-doc stem `l_doc_taxonomy` declares — the ONE roster.

    Read from the taxonomy module rather than re-listed here: a second list of
    the layers that exist is a second answer to a question this repo already
    answers in one place, and the two drift on the first extension.
    `NOT_MEASURED` is impossible — the roster is a module constant — but an
    import failure must not be spelled as an empty roster, so it raises.
    """
    import l_doc_taxonomy as _tax
    return list(_tax.all_l_doc_full_names())


def layer_declared_by_taxonomy(layer: Any) -> bool:
    """Does the taxonomy declare a layer of this name?

    Matched the way `resolve_layer_file` matches a document — on the leading
    `L<number>` token, plus the exact stem — so a spelling the resolver
    accepts is a spelling this answers for. Two matchers for one question is
    how a layer comes to be present for one reader and absent for the other.
    """
    want = (layer or "").strip()
    if not want:
        return False
    stems = [s.lower() for s in taxonomy_layer_stems()]
    if want.lower() in stems:
        return True
    m = re.match(r"^\s*(l\s*\d+)", want.lower())
    if not m:
        return False
    key = re.sub(r"\s+", "", m.group(1))
    return any(s == key or s.startswith(key + "_") for s in stems)


def phase1_root_identity(project: Path) -> Dict[str, Any]:
    """WHICH Phase-1 root this run judged, as a value a later reader can check.

    Every artefact in the #2127/#2132/#2150 chain — a classification, a
    decision table, a repaired corpus — is an answer ABOUT one Phase-1 root,
    and none of them said which. MEASURED (#2150 F11, re-measured here): the
    table computed for #2132 names `L24_SIGNOFF` as an owning layer and the
    published opentitan_aes root has 23 layer codes in 24 files and no `L24`
    at all, so two of its OWNER cells address a document that does not exist.
    Reading such a table against this root does not disagree with it — it
    silently answers a different question.

    So the root is IDENTIFIED, by MEMBERSHIP (the layer stems) and by CONTENT
    (a digest over each layer's bytes). Membership is the half a substitution
    cannot disturb, and content is the half a re-run can. Both are recorded;
    neither is reduced to the other.

    `NOT_MEASURED` when no L-doc is readable — never an empty root presented
    as a root that was read.
    """
    files = emitted_layer_files(project)
    if not files:
        return {"status": "NOT_MEASURED",
                "reason": ("no L-doc was found under "
                           f"{list(_L_DOC_DIRS)} — this is 'could not read "
                           "the Phase-1 root', never 'read it and it was "
                           "empty'"),
                "layers": [], "digest": None}
    import hashlib
    per = []
    for p in files:
        try:
            raw = p.read_bytes()
        except OSError:
            per.append((p.stem, "UNREADABLE"))
            continue
        per.append((p.stem, hashlib.sha256(raw).hexdigest()))
    joined = "\n".join(f"{stem} {dig}" for stem, dig in sorted(per))
    present = sorted(stem for stem, _ in per)
    declared = taxonomy_layer_stems()
    return {
        "status": "OK",
        "layers": present,
        "layer_count": len(per),
        # WHICH declared layers this root does NOT carry. The half that makes
        # a cross-root artefact readable: an expectation, a classification or
        # a decision table naming one of these was computed against a root
        # that had it, and this one does not — which is a different question
        # from a disagreement, and reads identically without this list.
        "taxonomy_layer_count": len(declared),
        "taxonomy_layers_absent": sorted(set(declared) - set(present)),
        "digest": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        "digest_basis": ("sha256 over the sorted `<layer stem> <sha256 of the "
                         "layer file>` lines — membership and content, kept "
                         "separable so a reader can see which of the two "
                         "moved"),
        "root_dir": str(files[0].parent),
    }


def owning_layers(project: Path, tokens: List[str],
                  exclude: Optional[Path] = None) -> List[str]:
    """The layers that carry EVERY one of these tokens — the scope answer.

    An expectation the addressed layer does not satisfy is an extraction gap
    ONLY if no layer satisfies it. When one does, the fact was extracted and
    the expectation asked the wrong layer, and saying "the design is missing
    it" is false about the design. Returns the layer file STEMS, sorted, so a
    reader is told where to point the `field_path`.

    EVERY token, never some: a layer carrying part of the fact does not own it,
    and scoring a partial match as ownership would launder a real gap into a
    scope finding the moment one token happened to appear anywhere.

    DELIBERATELY ASYMMETRIC. The addressed layer is read at the field the
    expectation names; the other layers are read whole. The two questions are
    different: the first is "does the layer say it WHERE this expectation says
    it should", the second is "does the program track have this fact AT ALL".
    Scoping the second would need a field_path for a layer the expectation
    never addressed, which nothing supplies — and asking it strictly would turn
    "the fact is here, one layer over" back into "the design is missing it",
    which is the defect this function exists to end.
    """
    if not tokens:
        return []
    out: List[str] = []
    for p in emitted_layer_files(project):
        if exclude is not None and p == exclude:
            continue
        try:
            blob = json.loads(p.read_text(errors="replace"))
        except (OSError, ValueError):
            continue          # unreadable content is not content
        units = _haystack_units(blob)
        if all(present_in_units(t, units) for t in tokens):
            out.append(p.stem)
    return sorted(out)


def _split_refusal(base: Dict[str, Any], why: str) -> Dict[str, Any]:
    """A split this comparator cannot decide. `usable` stays False."""
    base["split"] = True
    base["observed"] = why
    return base


def _converge_split(project: Path, base: Dict[str, Any], exp: Any,
                    raw: Any) -> Dict[str, Any]:
    """Decide a SPLIT expectation: N scoped branches, ALL of which must agree.

    Every refusal here is a refusal to DECIDE, never a verdict: an ill-formed
    split is `usable: False` and is reported under `RULE_AI_UNUSABLE`, the
    same way an ill-formed single expectation is. Silently treating a broken
    split as "not met" would put a design finding on the record for an
    authoring mistake, which is the family of defect this whole issue is.
    """
    base["split"] = True
    base["field_path_status"] = SPLIT_MARKER
    base["layer_present"] = SPLIT_MARKER
    base["scope"] = SPLIT_MARKER

    if not base["id"]:
        return _split_refusal(base, "the split expectation names no id, so "
                                    "there is nothing to compare it against")
    # A parent that states BOTH grammars is ambiguous by construction: each
    # would produce a verdict and the report could not say which one decided.
    if exp.get("expected_tokens") or exp.get("layer") or exp.get("field_path"):
        return _split_refusal(
            base,
            f"the expectation states BOTH a {SPLIT_KEY!r} list and its own "
            f"layer/field_path/expected_tokens. Those are two grammars, each "
            f"of which would decide the row, and a report that carried one "
            f"verdict could not say which of them produced it. State the "
            f"split alone: every address belongs to a branch")
    if not isinstance(raw, list):
        return _split_refusal(
            base, f"{SPLIT_KEY!r} is a {json_type_name(raw)}, not a list of "
                  f"branches, so no branch can be read from it")
    if not raw:
        return _split_refusal(
            base, f"{SPLIT_KEY!r} is an empty list. A split with no branches "
                  f"decides nothing, and a denominator of zero cannot be an "
                  f"agreement")

    branches: List[Dict[str, Any]] = []
    for i, sub in enumerate(raw):
        if not isinstance(sub, dict):
            return _split_refusal(
                base, f"branch {i} is a {json_type_name(sub)}, not an object")
        if sub.get(SPLIT_KEY) is not None:
            return _split_refusal(
                base, f"branch {i} carries its own {SPLIT_KEY!r}. A split is "
                      f"exactly one level deep: a nested one would make the "
                      f"row's denominator depend on a shape no reader of the "
                      f"report can see")
        # The branch inherits what it did not state. `requirement` and
        # `evidence` belong to the FACT, which is one fact however many layers
        # carry it; `layer`, `field_path` and `expected_tokens` are the
        # branch's own address and are never inherited.
        child = {
            "id": f"{base['id']}#{i}",
            "layer": sub.get("layer"),
            "field_path": sub.get("field_path"),
            "requirement": sub.get("requirement") or base["requirement"],
            "evidence": sub.get("evidence") or base["evidence"],
            "expert_source": sub.get("expert_source") or base.get(
                "expert_source"),
            "expected_tokens": sub.get("expected_tokens"),
        }
        res = converge_ai_expectation(project, child)
        if not res["usable"]:
            return _split_refusal(
                base, f"branch {i} ({child['id']}) cannot be converged: "
                      f"{res['observed']}. A split is decided only when every "
                      f"branch is")
        branches.append(res)

    base["usable"] = True
    base["sub_results"] = branches
    base["sub_layers"] = [b["layer"] for b in branches]

    # A branch addressing a layer this Phase-1 root does not contain (#2150,
    # first commit) makes the whole conjunction undecidable about the DESIGN:
    # one conjunct was never opened. Propagated to the parent so the row is
    # refused ONCE, under the layer-absent rule, naming the branch — rather
    # than decided `met: False` and published as a design gap.
    absent = [b for b in branches if b["layer_present"] is False]
    if absent:
        base["layer_present"] = False
        base["layers_in_root"] = absent[0].get("layers_in_root") or []
        # A conjunction containing a term NO root can ever satisfy is refused
        # as a track finding; one whose absent terms are all declared layers
        # is the program track not emitting documents its contract applies,
        # which is #312's disagreement and stays one. ANY undeclared term is
        # enough: a single unanswerable branch makes the whole conjunction
        # unanswerable, however well-formed the rest of it is.
        base["layer_in_taxonomy"] = all(b["layer_in_taxonomy"]
                                        for b in absent)
        # The parent states no `layer` of its own, so the finding would name
        # none. Name the branches that are actually missing.
        base["layer"] = ", ".join(b["layer"] for b in absent)
        base["owning_layers"] = (
            sorted({L for b in absent for L in b["owning_layers"]})
            if all(b["owning_layers"] for b in absent) else [])
        base["observed"] = (
            f"{len(absent)} of {len(branches)} branch(es) of this split "
            f"address a layer this Phase-1 root does not contain "
            f"({[b['layer'] for b in absent]}), so the conjunction was never "
            f"evaluated: " + "; ".join(f"[{b['id']}] {b['observed']}"
                                       for b in absent))
        return base

    # THE CONJUNCTION. Every branch, never any.
    base["met"] = all(b["met"] for b in branches)
    failed = [b for b in branches if not b["met"]]
    base["missing_tokens"] = sorted({t for b in failed
                                     for t in (b.get("missing_tokens") or [])})
    # The parent's owning-layer answer is the answer for the branches that
    # MISSED, and only when every one of them has one: a split whose failing
    # halves are each carried elsewhere is a re-scope, and one whose failing
    # half is carried nowhere is a disagreement about the design. Mixing the
    # two would let one re-scopable branch launder a real gap.
    base["owning_layers"] = (
        sorted({L for b in failed for L in (b["owning_layers"] or [])})
        if failed and all(b["owning_layers"] for b in failed) else [])
    if base["met"]:
        base["observed"] = (
            f"all {len(branches)} branch(es) of this split agree: "
            + "; ".join(f"{b['layer']}.{b['field_path']} carries "
                        f"{b['expected_tokens']}" for b in branches))
    else:
        base["observed"] = (
            f"{len(failed)} of {len(branches)} branch(es) of this split "
            f"disagree — a split is met only when EVERY branch is: "
            + "; ".join(f"[{b['id']}] {b['observed']}" for b in failed))
    return base


# ── WHERE the fact actually lives (#2150, the layer-contract ruling) ────────
#
# `owning_layers` answers "does any layer carry every token", and that answer
# is a layer NAME. It cannot tell the two cases a layer contract has to keep
# apart, because they produce the same layer name:
#
#   * the fact sits in a field NAMED for it — `L8.clock_domains[].name`,
#     `L4.registers[].reset_value`, `L22.checklist_milestones[].id`. A consumer
#     that wants the fact reads that path. The contract question is only WHICH
#     layer owns it.
#   * the fact sits in a GENERIC bucket — a comparison-table row, a discovered
#     identifier, an evidence literal, a prose section. It is findable only by
#     someone who already knows it is there, which is the same shape as an
#     undeclared fact.
#
# THE RULING (#2150, owner, 2026-09-07): a generic bucket does NOT satisfy a
# layer obligation unless a consumer actually reads it by that path. And the
# repair is NOT to promote every such fact to a field — #2132's rule stands,
# nothing is promoted without a MEASURED consumer. So a fact present in the
# root but declared by no layer is RECORDED as a disagreement with its reason,
# and never rounded up to an agreement.
#
# This block is that recording, and nothing more. It is ADVISORY and strictly
# ADDITIVE: `met` is decided exactly where it was decided before, so a moved
# verdict can never be attributed to it. Measured on a freshly generated
# 28-layer Phase-1 root, lane cz2150b.
#
# The vocabulary below names the plugin's OWN generated-L-doc containers — it
# is a statement about this repo's schema, not about any design, PDK or
# vendor. A container qualifies when its elements are undifferentiated: a row,
# a literal, a discovered name, a prose section. A named record's own field
# (`registers.fields.encoding.description`) is NOT generic — the path says
# what the value is.
GENERIC_CARRIAGE_SEGMENTS = frozenset({
    "auto_cited_sections",
    "auto_discovered_identifiers",
    "auto_discovered_literals",
    "comparison_tables",
    "corroborating_evidence",
    "evidence",
    "extraction_evidence",
    "frs_sections",
    "notes",
    "raw",
    # a raw table ROW, undifferentiated exactly as `comparison_tables.rows` is
    "rows",
    # a source SENTENCE lifted whole (`parameter_couplings.sentence`)
    "sentence",
    # the container that says in its own name that it is not structured
    "unstructured",
    "vendor_short_literals",
})

#: the three row-level answers, and the one that means the reading did not run.
CARRIAGE_NOWHERE = "NOWHERE"
CARRIAGE_GENERIC_ONLY = "GENERIC_ONLY"
CARRIAGE_NAMED_FIELD = "NAMED_FIELD_ELSEWHERE"

CARRIAGE_RULE = (
    "a generic bucket does not satisfy a layer obligation unless a consumer "
    "reads it by that path; a fact present in the root but declared by no "
    "layer is recorded as a disagreement with its reason, never rounded up "
    "to an agreement (#2150)"
)


def is_generic_carriage_path(path: str) -> bool:
    """Does this dotted path pass through a generic container?

    ANY segment, not just the first: `fields.tables.rows` is as generic at
    depth three as `comparison_tables.rows` is at depth two, and reading only
    the head would call the deeper one a named field.
    """
    return any(seg.strip().lower() in GENERIC_CARRIAGE_SEGMENTS
               for seg in str(path or "").split("."))


def token_paths(blob: Any, token: str) -> List[str]:
    """Every dotted path in this document whose SCALAR carries the token.

    List indices are elided — `registers[7].reset_value` and
    `registers[8].reset_value` are one path, because the question is which
    FIELD carries the fact and not how many records do. Uses the same
    `phrase_present` the comparator uses, so the two can never disagree about
    whether a token is present.
    """
    hits: List[str] = []

    def walk(node: Any, path: List[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [str(k)])
        elif isinstance(node, list):
            for v in node:
                walk(v, path)
        elif node is not None and phrase_present(token, str(node)):
            hits.append(".".join(path))

    walk(blob, [])
    return sorted(set(hits))


def token_carriage(project: Path, tokens: List[str],
                   exclude: Optional[Path] = None) -> Dict[str, Any]:
    """WHERE each token lives across the layers, split named vs generic.

    `exclude` is the layer the expectation already addressed: it has been read
    and found wanting, and counting it as somewhere-else would answer the
    question with the layer that raised it.
    """
    per_token: Dict[str, Dict[str, Any]] = {}
    for tok in tokens:
        per_token[tok] = {"named": {}, "generic": {}}
    if not tokens:
        return {"verdict": CARRIAGE_NOWHERE, "per_token": per_token,
                "rule": CARRIAGE_RULE}

    for p in emitted_layer_files(project):
        if exclude is not None and p == exclude:
            continue
        try:
            blob = json.loads(p.read_text(errors="replace"))
        except (OSError, ValueError):
            continue          # unreadable content is not content
        for tok in tokens:
            paths = token_paths(blob, tok)
            named = [q for q in paths if not is_generic_carriage_path(q)]
            generic = [q for q in paths if is_generic_carriage_path(q)]
            if named:
                per_token[tok]["named"][p.stem] = named
            if generic:
                per_token[tok]["generic"][p.stem] = generic

    if any(v["named"] for v in per_token.values()):
        verdict = CARRIAGE_NAMED_FIELD
    elif any(v["generic"] for v in per_token.values()):
        verdict = CARRIAGE_GENERIC_ONLY
    else:
        verdict = CARRIAGE_NOWHERE
    # WHICH tokens the verdict is made of. The verdict above is an OR over
    # tokens — one token in a named field makes the row NAMED_FIELD_ELSEWHERE
    # even where the rest are nowhere — and a reader who cannot see the split
    # would read it as a statement about the whole fact. State it instead of
    # implying it.
    carried = sorted(t for t, v in per_token.items() if v["named"] or v["generic"])
    return {"verdict": verdict, "per_token": per_token, "rule": CARRIAGE_RULE,
            "tokens_carried_elsewhere": carried,
            "tokens_in_no_layer": sorted(set(per_token) - set(carried))}


def carriage_sentence(carriage: Dict[str, Any]) -> str:
    """The one sentence a finding adds, in the words of the ruling."""
    v = carriage.get("verdict")
    if v == CARRIAGE_NOWHERE:
        return ("Carriage: the missing token(s) are in NO other layer either, "
                "so this is a content gap and not a scoping one.")
    per = carriage.get("per_token") or {}
    if v == CARRIAGE_GENERIC_ONLY:
        where = sorted({f"{stem}.{q}"
                        for t in per.values()
                        for stem, qs in t["generic"].items() for q in qs})
        return ("Carriage: the fact is in the root ONLY under a generic "
                f"bucket ({', '.join(where[:4])}) and under no field named "
                "for it. Recorded as a DISAGREEMENT, not promoted: "
                + CARRIAGE_RULE + ".")
    where = sorted({f"{stem}.{q}"
                    for t in per.values()
                    for stem, qs in t["named"].items() for q in qs})
    return ("Carriage: a field NAMED for the fact carries it in another "
            f"layer ({', '.join(where[:4])}), so this is a layer-contract "
            "question — which layer owns it — and not a content gap.")


def converge_ai_expectation(project: Path, exp: Any) -> Dict[str, Any]:
    """Decide ONE AI expectation against what the program track actually wrote.

    Returns the expectation enriched with `met` / `observed` / `usable`. Any
    `met` the answer carried is DISCARDED before the decision — see the module
    docstring on why the AI half must not score itself.

    TWO GRAMMARS (#2150). An expectation is either SINGLE — one `layer`, one
    optional `field_path`, one `expected_tokens` list — or SPLIT: a
    `sub_expectations` list of branches, each with its own layer, field_path
    and tokens, ALL of which must agree. A split is decided branch by branch
    through exactly the same single-expectation machinery, so scoping, the
    field_path guard and the owning-layer answer apply per branch, unchanged.

    WHY A SPLIT HAD TO EXIST. MEASURED on the surviving opentitan_aes Phase-1
    root: an expectation whose two halves the program track extracted into two
    DIFFERENT layers (the FSM encoding in L1, the power budget in L19) is
    `met: False` in either layer, and `owning_layers` is empty because no
    single layer carries every token. It is therefore published as
    `about: "design"` — "the program track is missing this" — about a fact the
    program track has, in full, twice over. A conjunction that cannot be
    written down is not a fact the corpus lacks; it is a sentence the grammar
    cannot say, and the report blames the design for the difference.

    THE SPLIT IS A CONJUNCTION, NOT A DISJUNCTION. Every branch must agree.
    An "any branch" form would make a two-branch expectation EASIER to satisfy
    than either of its halves, which is a comparator that gets weaker as an
    author writes more — the opposite of what this track is for.
    """
    base: Dict[str, Any] = {
        "id": None, "layer": None, "field_path": None, "requirement": None,
        "evidence": [], "expected_tokens": [],
        "usable": False, "met": False, "observed": "",
        # #2127. `field_path_status` is NOT_MEASURED until a layer document is
        # in hand: an expectation refused before that was never checked against
        # a schema, and recording it as UNDECLARED would report a reading that
        # did not happen. `owning_layers` is [] only where it was computed.
        "field_path_status": "NOT_MEASURED", "owning_layers": [],
        # WHERE the token search happened: `field_path` when the named path
        # resolved, `whole_layer` when it did not. NOT_MEASURED until a layer
        # document is in hand.
        "scope": "NOT_MEASURED",
        # None = the expectation was not withdrawn. "" = it was withdrawn with
        # no usable reason, which is refused. A non-empty string is the reason.
        "withdrawn_reason": None,
        # #2150. Whether the Phase-1 root CONTAINS the layer this expectation
        # addresses. NOT_MEASURED until the lookup is done, for the same
        # reason `field_path_status` is: an expectation refused before the
        # root was consulted was never checked against one.
        "layer_present": "NOT_MEASURED",
        # Whether the L-doc TAXONOMY declares a layer of this name. Only
        # consulted when the root does not carry the document, so it stays
        # NOT_MEASURED whenever the question did not arise.
        "layer_in_taxonomy": "NOT_MEASURED",
    }
    if not isinstance(exp, dict):
        base["observed"] = ("the answer contained an entry that is not an "
                            "object, so nothing about it can be checked")
        return base

    base.update({
        "id": exp.get("id") or exp.get("expectation_id"),
        "layer": exp.get("layer"),
        "field_path": exp.get("field_path"),
        "requirement": exp.get("requirement"),
        "evidence": exp.get("evidence") or [],
        "expert_source": exp.get("expert_source"),
        "expected_tokens": [t for t in (exp.get("expected_tokens") or [])
                            if isinstance(t, str) and t.strip()],
    })

    # #2127 second addendum — a WITHDRAWAL is read before anything is decided.
    # `withdrawn` may be the reason string itself or an object carrying one;
    # both spellings are accepted, and a reason that is absent, blank or not a
    # string leaves `withdrawn_reason` as "", which the caller REFUSES.
    wd = exp.get("withdrawn")
    if wd is not None and wd is not False:
        reason = wd.get("reason", "") if isinstance(wd, dict) else wd
        # ONLY A STRING IS A REASON. `withdrawn: true` is the bare marker this
        # refusal exists to catch, and an earlier revision of this block ran it
        # through `str()` and recorded the reason as "True" — a withdrawal that
        # refused nothing, spelled exactly like one that stated something. Its
        # own test caught it.
        base["withdrawn_reason"] = reason.strip() if isinstance(reason, str) \
            else ""
        if base["withdrawn_reason"]:
            base["observed"] = (f"WITHDRAWN by the author: "
                                f"{base['withdrawn_reason']}")
            return base
        # fall through: a reasonless withdrawal is not honoured, so the
        # expectation is decided exactly as if it had never carried one.

    # ── SPLIT or SINGLE (#2150) ────────────────────────────────────────────
    # Dispatched HERE: after the withdrawal (a split can be withdrawn like any
    # other row) and before the single-expectation checks, whose `layer` and
    # `expected_tokens` requirements a split parent deliberately does not meet.
    raw_split = exp.get(SPLIT_KEY)
    if raw_split is not None:
        return _converge_split(project, base, exp, raw_split)

    if not base["id"] or not base["layer"]:
        base["observed"] = ("the expectation names no id and/or no layer, so "
                            "there is nothing to compare it against")
        return base
    if not base["expected_tokens"]:
        base["observed"] = (
            "the expectation carries no `expected_tokens`, so it states a "
            "requirement in prose only. A prose-only expectation cannot be "
            "decided by a program and must not be counted as agreed")
        return base

    base["usable"] = True
    path = resolve_layer_file(project, base["layer"])
    if path is None:
        # #2150 — the LAYER is absent from the Phase-1 root. Recorded as its
        # own fact rather than left to fall through: the whole-layer reading
        # never happened, so `met: False` here is not a statement about the
        # design and the caller must not file it as one.
        base["layer_present"] = False
        base["layers_in_root"] = [q.stem for q in emitted_layer_files(project)]
        # Is this a layer the CONTRACT declares? The two cases need different
        # readers: a declared layer the root does not carry is the program
        # track not emitting a document its own taxonomy applies (#312's
        # disagreement, kept), and an undeclared one is an expectation
        # addressing a document this plugin never emits (#2150, refused).
        base["layer_in_taxonomy"] = layer_declared_by_taxonomy(base["layer"])
        # Asked even though the addressed layer is missing: a fact that lives
        # wholly in a layer the root DOES carry is a re-scope with a named
        # destination. `exclude=None` — there is no addressed file to exclude.
        # Additive: it cannot make a row agree, only say where the fact is.
        base["owning_layers"] = owning_layers(project, base["expected_tokens"])
        # The FIRST sentence is unchanged from before this landing. Downstream
        # readers (and #312's own test) quote it, and a landing that rewords a
        # sentence it did not need to change makes itself unreviewable.
        base["observed"] = (
            f"the program track produced no {base['layer']} layer at all"
            + (" — and the L-doc taxonomy declares no layer of that name, so "
               "no document of this kind is emitted for any ic_class"
               if not base["layer_in_taxonomy"] else
               f" (the taxonomy DOES declare it; this Phase-1 root carries "
               f"{len(base['layers_in_root'])} layer document(s) and not "
               f"that one)")
            + (f"; {base['owning_layers']} carries every one of the "
               f"{len(base['expected_tokens'])} expected token(s)"
               if base["owning_layers"] else ""))
        return base
    base["layer_present"] = True
    try:
        blob = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        base["observed"] = (f"{base['layer']} exists but does not parse "
                            f"({exc.__class__.__name__}); unreadable content "
                            f"is not content")
        return base

    # #2127 — the guard. Read BEFORE the token search and kept independent of
    # it: `met` is decided over the whole layer exactly as it was, so a moved
    # verdict can never be attributed to this check.
    base["field_path_status"] = field_path_status(
        blob, base["field_path"], base["layer"])
    if base["field_path_status"] == "UNDECLARED":
        base["layer_declares"] = sorted(
            blob.keys() if isinstance(blob, dict) else [])

    # #2127 addendum (#2128) — WHERE the tokens are looked for.
    #
    # The reading is SCOPED to the field the expectation names whenever that
    # path resolves. Unscoped, any field of the layer can satisfy any token:
    # MEASURED on a real run, adding an unrelated boolean key to L19 made the
    # token "false" score and moved a row from 2-missing to 1-missing, on a
    # coincidence. A coincidence can score as agreement in both directions.
    #
    # When the path does NOT resolve there is nowhere to scope to, and the
    # whole-layer reading is kept — moving those verdicts is a much larger
    # decision than this one and would need its own landing — but the scope is
    # RECORDED, so a `met` obtained without scoping is visibly the weaker
    # claim it is rather than passing for a reading at the named field.
    subtree = subtree_at(blob, base["field_path"], base["layer"])
    if subtree is None:
        base["scope"] = "whole_layer"
        units = _haystack_units(blob)
    else:
        base["scope"] = "field_path"
        units = [u for node in subtree for u in _haystack_units(node)]

    missing = [t for t in base["expected_tokens"]
               if not present_in_units(t, units)]
    base["met"] = not missing
    base["missing_tokens"] = missing
    where = (f"{base['layer']}.{base['field_path']}"
             if base["scope"] == "field_path" else
             f"{base['layer']} (WHOLE LAYER — the expectation's field_path "
             f"{base['field_path']!r} does not resolve, so the reading is "
             f"UNSCOPED and any field of the layer could satisfy a token)")
    base["observed"] = (
        f"{where} carries all of {base['expected_tokens']}"
        if not missing else
        f"{where} does not carry {missing} "
        f"(checked {len(base['expected_tokens'])} expected token(s))")
    if missing:
        # Where else in the program track's own output does this fact live?
        # Asked ONLY on a miss: on a hit there is nothing to re-scope, and
        # walking every layer for an expectation already satisfied would be
        # work whose answer nobody reads.
        base["owning_layers"] = owning_layers(
            project, base["expected_tokens"], exclude=path)
        # #2150. WHERE the missing tokens live, and under what KIND of path.
        # Asked over the MISSING tokens alone: a token the addressed layer
        # already carries has no carriage question, and folding it in would
        # let a satisfied token supply a named field for an unsatisfied fact.
        base["carriage"] = token_carriage(project, missing, exclude=path)
    return base


# ── the AI sub-track ────────────────────────────────────────────────────────

def ai_subtrack(project: Path, prompt: str, out_dir: Path,
                ic_class=None, class_availability=None) -> Dict[str, Any]:
    """Hand the open-ended reading to the IC Expert Agent.

    Uses `ic_expert_backup_pack` — the assembler this doctrine already built
    for this hand-off (expert-skills digest as one author, expert-DB digest as
    an independent second, then converge). Reused rather than reimplemented;
    it is tested and it is the doctrine's own mechanism.

    A program cannot spawn a subagent, so this emits the pack + the hand-off
    descriptor that names exactly which subagent to invoke, and consumes the
    agent's answer if a previous invocation left one. Every outcome is a
    STATED status:

        HANDOFF_EMITTED         pack ready, agent has not answered yet
        CONSUMED                an answer was read back, carrying expectations
        CONSUMED_EMPTY          an answer was read back and its `expectations`
                                list was empty — a reading, and a real zero
        ANSWER_SCHEMA_MISMATCH  an answer exists and PARSES, but is not this
                                consumer's schema. REFUSED, and the refusal
                                names the top-level keys that did arrive
        ERROR                   assembly failed, or the answer does not parse

    NOTHING HERE IS CONDITIONAL ON AN LLM BACKEND. Assembly is deterministic
    and the author is a SUBAGENT, so the in-process `anthropic` SDK is
    irrelevant to both. It is still probed and RECORDED as
    `inline_llm_backend`, because losing a fact is its own defect — but it can
    no longer veto a path that never used it. That veto is what kept this half
    at SKIPPED-CONDITION on all 8 published designs while making the pack no
    agent could answer and ignoring an answer already on disk (#312).
    """
    status: Dict[str, Any] = {"status": "ERROR", "reason": "",
                              "expectations": [], "pack_dir": str(out_dir)}

    try:
        import llm_semantic_confirm as _llm
        status["inline_llm_backend"] = bool(_llm.backend_available())
    except Exception:  # noqa: BLE001
        status["inline_llm_backend"] = False

    try:
        import ic_expert_backup_pack as _pack
        # CLASS-FIRST (#2094). The design's REGISTERED class goes in, so the
        # pack's db_classes are selected by the class and only ranked by the
        # phrase — and so the pack can say whether it is assembled at all.
        # `iface=None, target=None` stays: this hand-off asks for L-doc
        # expectations, not an RTL body, so there is no recovered port list to
        # hand over. Whether that leaves the pack empty is now the pack's own
        # stated verdict rather than something a reader has to notice.
        # #2150 F5 — the pack carries the AUTHORING schema, derived from this
        # project's own emitted L-docs and filtered through the resolver the
        # review-time guard uses. Passed rather than hard-coded in the pack:
        # the pack cannot see the project, and a contract that names two
        # layers for a root that has two dozen is what made every expectation
        # address one of those two.
        schema = authoring_schema(project)
        status["authoring_schema_status"] = schema["status"]
        status["authoring_schema_layer_count"] = len(schema.get("layers") or {})
        handoff = _pack.assemble(
            prompt=prompt, iface=None, target=None,
            expert_skills=[], verify_gates=[PROGRAM],
            out_dir=out_dir, k=5,
            output_target="l_doc_expectations.json",
            ic_class=ic_class, authoring_schema=schema)
        status["handoff"] = handoff
        # The disposition is recorded for EVERY design, profiled or not: the
        # pack file only gains a `class_first` block when there was a profile
        # to confine it with, so without this the unprofiled case would leave
        # no trace anywhere and "we did not confine" would be indistinguishable
        # from "there was nothing to confine".
        status["class_first"] = _pack.class_first_disposition(
            ic_class, availability=class_availability)
        status["pack_assembly_status"] = (
            (handoff.get("class_first") or {}).get("assembly_status")
            or "NOT_EVALUATED")
    except Exception as exc:  # noqa: BLE001
        status.update(status="ERROR", reason=f"pack assembly failed: {exc}")
        return status

    answer = out_dir / "l_doc_expectations.json"
    if answer.is_file():
        try:
            data = json.loads(answer.read_text(errors="replace"))
        except (OSError, ValueError) as exc:
            status.update(status=AI_ERROR,
                          reason=f"agent answer does not parse: {exc}")
            return status
        # PARSING IS NOT UNDERSTANDING. An answer that is valid JSON and not
        # this consumer's schema is REFUSED, never consumed: `data.get(...)`
        # followed by `if isinstance(x, list) else []` turns every unexpected
        # shape into the same empty list, and an empty list is exactly what an
        # agent with nothing to say produces. The two must not share a record.
        mismatch = answer_schema_mismatch(data)
        if mismatch is not None:
            keys = mismatch["top_level_keys"]
            status.update(
                status=AI_SCHEMA_MISMATCH,
                reason=(
                    f"the agent answered, and the answer is not the shape this "
                    f"consumer reads: {mismatch['why']}. Top-level key(s) "
                    f"present: {keys if keys else '(none)'}. The answer was "
                    f"NOT read and NOT counted — an answer this consumer "
                    f"cannot read is a refusal, never a reading of zero"),
                answer_path=str(answer),
                answer_json_type=mismatch["json_type"],
                answer_schema_why=mismatch["why"],
                answer_top_level_keys=keys)
            return status
        exps = data["expectations"]
        status.update(
            status=AI_CONSUMED if exps else AI_CONSUMED_EMPTY,
            reason=(f"read {len(exps)} expectation(s) from a prior agent "
                    f"invocation" if exps else
                    "the agent answered and its `expectations` list was empty "
                    "— a real reading of zero, not a missing answer"),
            answer_path=str(answer),
            expectations=exps)
        return status

    status.update(
        status=AI_HANDOFF_EMITTED,
        reason=(f"pack written to {out_dir}; invoke subagent "
                f"{_pack.SUBAGENT_TYPE} on "
                f"{out_dir / 'ic_expert_agent_handoff.json'} and re-run to "
                f"consume its answer"))
    return status


# ── evaluate ────────────────────────────────────────────────────────────────

def evaluate(project: Path) -> Dict[str, Any]:
    # THE REPORT, not just the string (#2164): every number below that is
    # derived from the design input has to be able to say whether the input was
    # readable, and a bare `""` cannot.
    input_report = input_text_report(project)
    prompt = input_report["text"]
    readability = input_readability_disposition(input_report)
    out_dir = _pl.report_path(project, "phase1/expert_parse_track").parent \
        / "expert_parse_track_pack"

    availability = registered_ic_class_disposition(project)
    ic_class = availability.get("ic_class")
    rules = [fn(project) for fn in DETERMINISTIC_RULES]
    ai = ai_subtrack(project, prompt, out_dir, ic_class=ic_class,
                     class_availability=availability)

    findings: List[Dict[str, Any]] = []
    # #2164, FIRST, because everything after it is derived from the query this
    # names. Reported rather than raised: the deterministic half of the track
    # reads the L documents and is unaffected, so the run is not void — but the
    # AI half's pack was assembled from nothing and no reader may credit it.
    if readability["retrieval_input"] == "NOT_MEASURED":
        findings.append({
            "severity": "REVIEW",
            "about": "track",
            "rule": RULE_INPUT_NOT_READABLE,
            "message": (
                f"The expert retrieval query is 0 characters because "
                f"{readability['reason']}. Everything downstream of it — the "
                f"retrieved expert classes, the phrase ranking inside the "
                f"class-first selection, and the prompt the AI sub-track was "
                f"handed — is NOT_MEASURED, not measured-as-empty. This is "
                f"about the READER, not about the design: the design input is "
                f"there and this track cannot open it."),
        })
    for r in rules:
        for e in r["expectations"]:
            if e["met"]:
                continue
            findings.append({
                "severity": "REVIEW",
                # `about` separates a finding about the DESIGN from one about
                # the TRACK ITSELF. A reader counting "what did the expert
                # track find in this design" must not be handed a run whose
                # only entry says the track's own AI half was unavailable —
                # that is the same conflation of two different zeros this
                # track exists to stop.
                "about": "design",
                "rule": f"{RULE_UNMET}::{e['id']}",
                "layer": e["layer"],
                "field_path": e["field_path"],
                "message": (
                    f"The expert track, reading the same design input "
                    f"independently, expected {e['layer']}."
                    f"{e['field_path']} to carry: {e['requirement']}. The "
                    f"program track produced: {e['observed']}. Grounds: "
                    f"{'; '.join(e['evidence'])}."),
                "expert_source": e["expert_source"],
            })

    # ── CONVERGE: the AI half's answer becomes findings, not a file ─────────
    converged = [converge_ai_expectation(project, e)
                 for e in (ai.get("expectations") or [])]
    ai["converged"] = converged
    for c in converged:
        if c["withdrawn_reason"] == "":
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_WITHDRAWN_NO_REASON}::{c['id'] or '<unnamed>'}",
                "layer": c["layer"],
                "message": (
                    "The answer marks this expectation WITHDRAWN and states no "
                    "reason. The withdrawal is REFUSED and the expectation is "
                    "converged below exactly as if it had never carried one: "
                    "withdrawal is the one operation that can remove a row "
                    "from what this track reports, so it is the one that must "
                    "not be silent, and a marker saying only THAT something "
                    "was withdrawn is not a record of the decision."),
            })
            # deliberately NOT `continue` — the row is still decided.
        elif c["withdrawn_reason"]:
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_WITHDRAWN}::{c['id']}",
                "layer": c["layer"],
                "field_path": c["field_path"],
                "message": (
                    f"The expectation is WITHDRAWN by its author, and the "
                    f"reason is recorded here rather than the row being "
                    f"deleted: {c['withdrawn_reason']} It is NOT converged "
                    f"against the design and is not a design finding; it "
                    f"remains in the denominator so a reader can see that a "
                    f"decision was taken, not that an expectation was never "
                    f"written."),
            })
            continue

        if not c["usable"]:
            findings.append({
                "severity": "REVIEW",
                # About the TRACK: an entry the comparator could not decide
                # says nothing about the design, and counting it as a design
                # finding would inflate what the AI half found.
                "about": "track",
                "rule": f"{RULE_AI_UNUSABLE}::{c['id'] or '<unnamed>'}",
                "layer": c["layer"],
                "message": (
                    f"The AI sub-track returned an expectation this track "
                    f"cannot converge: {c['observed']}. It is reported rather "
                    f"than dropped — an undecidable expectation that vanished "
                    f"would make the AI half look like it agreed."),
            })
            continue

        # ── the field_path guard, PER BRANCH, for a SPLIT (#2150) ─────────
        # The guard is ADDITIVE and independent of the verdict, so it is
        # emitted per branch and named `<parent id>#<i>`: a split whose third
        # branch names an undeclared path has ONE repairable branch, and a
        # refusal naming only the parent would send the author to re-read all
        # of them. The parent's own `field_path_status` is SPLIT — it
        # addresses no single field, and answering DECLARED for it would
        # report a reading of one branch as a reading of the expectation.
        for b in (c.get("sub_results") or []):
            if b["field_path_status"] != "UNDECLARED":
                continue
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_FIELD_PATH_UNDECLARED}::{b['id']}",
                "layer": b["layer"],
                "field_path": b["field_path"],
                "message": (
                    f"Branch {b['id']} of this split expectation named "
                    f"field_path {b['field_path']!r} in {b['layer']}, and "
                    f"that layer declares no such path. It declares "
                    f"{b.get('layer_declares') or '(no top-level keys)'}. "
                    f"The branch is REFUSED by name rather than left to "
                    f"appear as a field that was checked: that branch's "
                    f"reading was decided over the WHOLE layer. Repair the "
                    f"branch's field_path to a path its layer declares."),
            })

        # ── the layer is not in the CONTRACT at all (#2150) ────────────────
        # Refused BY NAME and `about: "track"`, and only for a layer the
        # L-doc taxonomy does not declare. A DECLARED layer this root does not
        # carry stays the disagreement #312 made it: the program track did not
        # emit a document its own taxonomy applies, and silence there was the
        # original defect. What is refused here is an expectation addressing a
        # document this plugin never emits for any ic_class — the shape a
        # decision table computed over a DIFFERENT Phase-1 root produces, and
        # a shape no extractor can ever repair.
        if c["layer_present"] is False and c["layer_in_taxonomy"] is False:
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_LAYER_ABSENT}::{c['id']}",
                "layer": c["layer"],
                "field_path": c["field_path"],
                "owning_layers": c["owning_layers"],
                "layers_in_root": c.get("layers_in_root") or [],
                "message": (
                    f"The AI sub-track addressed layer {c['layer']!r}, and "
                    f"the L-doc taxonomy declares no layer of that name — so "
                    f"no Phase-1 root of any design carries it and no "
                    f"extractor can produce it. This root carries "
                    f"{c.get('layers_in_root') or '(no layer document)'}. "
                    f"The miss is a fact about the EXPECTATION, not about the "
                    f"design"
                    + (f"; {c['owning_layers']} carries every one of the "
                       f"{len(c['expected_tokens'])} expected token(s), so "
                       f"the repair is to re-scope the expectation there"
                       if c["owning_layers"] else
                       ". Either re-scope it to a layer the taxonomy "
                       "declares, or state that the expectation was computed "
                       "over a DIFFERENT artefact than the one being judged "
                       "— an expectation naming a layer that does not exist "
                       "answers a different question rather than "
                       "disagreeing with this one") + "."),
                "expert_source": c.get("expert_source"),
            })
            continue

        # ── the field_path guard (#2127) ───────────────────────────────
        # ADDITIVE and independent of the verdict: the expectation named a
        # path the layer does not declare, and until this refusal that path
        # was repeated verbatim in the finding text as though it had been
        # read. Refused BY NAME so the author can repair the ONE expectation,
        # and `about: "track"` so it never counts as something found in the
        # design.
        if c["field_path_status"] == "UNDECLARED":
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_FIELD_PATH_UNDECLARED}::{c['id']}",
                "layer": c["layer"],
                "field_path": c["field_path"],
                "message": (
                    f"The AI sub-track named field_path "
                    f"{c['field_path']!r} in {c['layer']}, and that layer "
                    f"declares no such path. It declares "
                    f"{c.get('layer_declares') or '(no top-level keys)'}. "
                    f"The expectation is REFUSED by name rather than left to "
                    f"appear in a finding as a field that was checked: the "
                    f"verdict below was decided over the WHOLE layer, so a "
                    f"reader told that {c['layer']}.{c['field_path']} is "
                    f"empty is being told about a field nothing looked at. "
                    f"Repair the expectation's field_path to a path the "
                    f"owning layer declares."),
            })

        if c["met"]:
            continue

        # ── the fact IS extracted, one layer over (#2127) ──────────────────
        # A miss the addressed layer does not explain: another layer the
        # program track wrote carries EVERY expected token. Reporting that as
        # a design finding says the tree is missing something it demonstrably
        # has — which is how ten expectations asking L9 for a register map
        # were recorded as extraction gaps while the register layer carried
        # the registers. `about: "track"`, because the repair is to the
        # expectation's scope or to the layer contract, never to an extractor.
        if c["owning_layers"]:
            findings.append({
                "severity": "REVIEW",
                "about": "track",
                "rule": f"{RULE_AI_MISSCOPED}::{c['id']}",
                "layer": c["layer"],
                "field_path": c["field_path"],
                "owning_layers": c["owning_layers"],
                "message": (
                    f"The AI sub-track asked {c['layer']}"
                    f"{'.' + c['field_path'] if c['field_path'] else ''} for: "
                    f"{c['requirement']}. That layer does not carry it, and "
                    f"{c['owning_layers']} DOES — every one of the "
                    f"{len(c['expected_tokens'])} expected token(s). So the "
                    f"fact was extracted and the expectation named the wrong "
                    f"layer; this is NOT a missing extraction and must not be "
                    f"repaired in an extractor. Either re-scope the "
                    f"expectation to the layer that owns the fact, or — if "
                    f"the layer contract really does put it in {c['layer']} "
                    f"— record that as a layer-contract defect in its own "
                    f"right."),
                "expert_source": c.get("expert_source"),
            })
            continue

        findings.append({
            "severity": "REVIEW",
            "about": "design",
            "rule": f"{RULE_AI_UNMET}::{c['id']}",
            "layer": c["layer"],
            "field_path": c["field_path"],
            "message": (
                f"The AI sub-track, reading the same design input "
                f"independently, expected {c['layer']}"
                f"{'.' + c['field_path'] if c['field_path'] else ''} to carry: "
                f"{c['requirement']}. The program track produced: "
                f"{c['observed']}."
                + (f" Grounds: {'; '.join(str(e) for e in c['evidence'])}."
                   if c["evidence"] else "")
                # #2150 — APPENDED, never woven in: the sentences above are
                # what every existing reader parses, and the ruling is a
                # separate statement that must be readable on its own.
                + (" " + carriage_sentence(c["carriage"])
                   if c.get("carriage") else "")),
            "expert_source": c.get("expert_source"),
            # The machine-readable half of the same sentence, so a consumer
            # never has to parse prose to learn the carriage verdict.
            "carriage": c.get("carriage"),
        })

    if ai.get("pack_assembly_status") == "NOT_ASSEMBLED":
        cf = (ai.get("handoff") or {}).get("class_first") or {}
        findings.append({
            "severity": "REVIEW",
            # About the TRACK. The design is not what is in question: the
            # hand-off this track emitted carried no class-specific context,
            # and everything the agent writes from it is authored from the
            # design input alone.
            "about": "track",
            "rule": RULE_PACK_NOT_ASSEMBLED,
            "message": (
                f"The expert pack for registered ic_class "
                f"{cf.get('ic_class')!r} is NOT_ASSEMBLED: "
                f"{cf.get('not_assembled_reason')} Anything the AI half "
                f"returns for this run was authored from the design input "
                f"alone; the pack contributed no class knowledge to it. "
                f"Reported rather than left implicit — a null pack that reads "
                f"as an assembled one is credited as context that was never "
                f"there. Pack: {out_dir / 'ic_expert_agent_handoff.json'}."),
        })

    if ai["status"] == AI_SCHEMA_MISMATCH:
        keys = ai.get("answer_top_level_keys") or []
        findings.append({
            "severity": "REVIEW",
            # About the TRACK: the design is not what is in question. An answer
            # arrived and this consumer could not read it.
            "about": "track",
            "rule": RULE_AI_ANSWER_SCHEMA_MISMATCH,
            "message": (
                f"The AI sub-track's answer file EXISTS and parses, and is not "
                f"the shape this consumer reads: "
                f"{ai.get('answer_schema_why')}. It carries "
                f"{len(keys)} top-level key(s): {keys if keys else '(none)'}. "
                f"It is REFUSED rather than coerced to an empty list — an "
                f"answer flattened to zero expectations is recorded in the "
                f"identical words as an agent that had nothing to say, and one "
                f"of those two is a discarded answer. Answer file: "
                f"{ai.get('answer_path')}. Findings below come from the "
                f"deterministic sub-track ALONE — read them as a floor, not as "
                f"coverage."),
        })
    elif ai["status"] == AI_CONSUMED_EMPTY:
        findings.append({
            "severity": "REVIEW",
            # About the TRACK: a reading that named nothing is not a statement
            # about the design, it is a statement about how much was covered.
            "about": "track",
            "rule": RULE_AI_ANSWER_EMPTY,
            "message": (
                f"The AI sub-track answered and named NO expectations "
                f"({ai['status']}): {ai['reason']} This is a real zero and it "
                f"is reported rather than left silent, because an empty "
                f"reading and a full one produce the same zero findings and "
                f"only one of them is coverage."),
        })
    elif ai["status"] not in AI_READ_STATES:
        findings.append({
            "severity": "REVIEW",
            "about": "track",
            "rule": RULE_AI_SKIPPED,
            "message": (
                f"The AI sub-track of the Phase-1 expert track did not deliver "
                f"a reading ({ai['status']}): {ai['reason']} Findings below "
                f"come from the deterministic sub-track ALONE — read them as a "
                f"floor, not as coverage."),
        })

    applicable = [r for r in rules if r["applicable"]]
    # THE DENOMINATOR — how many expectations this run actually DECIDED, from
    # both halves. The verdict is taken from it rather than from "did any rule
    # apply", because those are different questions: a rule can apply and
    # yield no expectation, and the AI half can answer and yield none either.
    # A run that decided nothing has measured nothing, and this repo's own
    # `gate_zero_denominator_refuses_check` exists to stop that from exiting 0.
    det_examined = sum(len(r["expectations"]) for r in applicable)
    examined = det_examined + len(converged)
    # EXECUTION outranks findings. Creating a handoff, refusing an answer,
    # reading an empty answer, or failing to parse one are four different
    # facts, but none is an expert reading with a non-zero denominator. Before
    # #1973 the runner credited all of them because it accepted rc 2 and only
    # checked that this report existed.
    execution_complete = ai["status"] == AI_CONSUMED and len(converged) > 0
    if not execution_complete:
        # AWAITING vs DEFECT — see AI_AWAITING_STATES. The verdict WORD is
        # INCOMPLETE either way, because neither is coverage; what differs is
        # whether re-running is the action that closes it.
        verdict = "INCOMPLETE"
        awaiting = ai["status"] in AI_AWAITING_STATES
        rc = AWAITING_EXIT_CODE if awaiting else 1
    elif not examined:
        # Defensive: AI_CONSUMED currently implies at least one converged
        # expectation. Keep the zero-denominator refusal local so a future
        # status change cannot manufacture a pass.
        verdict, rc = "INCOMPLETE", 1
        awaiting = False
    elif findings:
        verdict, rc = "FINDINGS", 0
        awaiting = False
    else:
        verdict, rc = "PASS", 0
        awaiting = False

    if execution_complete:
        disposition = DISPOSITION_CREDITED
    elif awaiting:
        disposition = DISPOSITION_AWAITING
    else:
        disposition = DISPOSITION_DEFECT

    sidecar = _pl.phase1_ai_deep_review_patches_file(project)
    return {
        "verdict": verdict,
        "rc": rc,
        "blocking": False,
        "enforcement": "advisory (findings) / mandatory (execution)",
        "execution": {
            "complete": execution_complete,
            # WHICH of the three, stated. A reader who has only `complete:
            # false` cannot tell "nobody has answered yet" from "the answer is
            # unreadable", and those two need different people.
            "disposition": disposition,
            "required_ai_status": AI_CONSUMED,
            "required_ai_consumed_min": 1,
            "observed_ai_status": ai["status"],
            "observed_ai_consumed": len(converged),
        },
        # WHAT IT IS WAITING FOR, in the words of the action that ends the
        # wait — never a bare state name. Null when nothing is being waited
        # for, so "waiting" and "not waiting" are two readable states rather
        # than a field a reader has to interpret.
        "awaiting": ({
            "what": "the Phase-1 IC-Expert answer (second pass of the "
                    "hand-off protocol)",
            "ai_subtrack_status": ai["status"],
            "action": (f"invoke subagent {_pack.SUBAGENT_TYPE} on "
                       f"{out_dir / 'ic_expert_agent_handoff.json'}, then "
                       f"re-run this track to consume its answer"),
            "credited": False,
            "exit_code": AWAITING_EXIT_CODE,
        } if awaiting else None),
        # WHICH Phase-1 root this run judged (#2150 F11). Every artefact the
        # expert track produces is an answer ABOUT one root, and until now
        # none of them said which — so a classification, a decision table and
        # a repaired corpus computed over three different roots read as three
        # answers to one question. Membership AND content, so a later reader
        # can tell a root that GREW from a root that CHANGED.
        "phase1_root": phase1_root_identity(project),
        # WHETHER the author was handed the declaration (#2150 F5). A run
        # whose pack could not carry one produced expectations authored
        # against two prose words, and a reader has to be able to see that
        # rather than infer it from the field_paths that came back.
        "authoring_schema_status": ai.get("authoring_schema_status",
                                          "NOT_EVALUATED"),
        "retrieved_expert_classes": retrieved_classes(prompt, ic_class=ic_class),
        # #2164. WHETHER that list is a measurement. An empty list from an
        # empty query and an empty list from a query that found nothing are the
        # same JSON and opposite facts; the status is what separates them, and
        # the reason names the formats this reader could not open.
        "retrieval_input": {
            **readability,
            "status": input_report["status"],
            "files_read": len(input_report["files_read"]),
            "files_skipped": input_report["files_skipped"],
            "truncated": input_report["truncated"],
        },
        # WHICH classes the expert DB was allowed to offer, and why. Without
        # this a reader sees a list of db_classes with no way to tell whether
        # the design's own class chose them or a phrase collided.
        "class_first": ai.get("class_first"),
        "pack_assembly_status": ai.get("pack_assembly_status", "NOT_EVALUATED"),
        # A PASS must say how much it looked at. This is that number, split by
        # half so a reader can see WHICH half contributed it — a total of 4
        # means something different when the AI half supplied 0 of it.
        "examined_expectations": examined,
        "denominator": {
            "deterministic": det_examined,
            "ai": len(converged),
            "total": examined,
            "ai_status": ai["status"],
        },
        "deterministic_subtrack": rules,
        "ai_subtrack": ai,
        # The convergence LEDGER. Agreements are counted, not just
        # disagreements, because "the second track always agrees" and "the
        # second track works" are different states that a disagreement-only
        # report renders identical — and the first one is worth knowing about.
        "ai_convergence": {
            "consumed": len(converged),
            "agreed": len([c for c in converged if c["usable"] and c["met"]]),
            "disagreed": len([c for c in converged
                              if c["usable"] and not c["met"]]),
            # UNDECIDABLE excludes a WITHDRAWN row. Both are "not decided", and
            # they are not the same event: one is an answer this comparator
            # could not read, the other is a decision its author took and
            # stated. Under one column a reader cannot tell them apart, which
            # is the two-zeros conflation this whole track exists to prevent.
            "undecidable": len([c for c in converged
                                if not c["usable"] and not c["withdrawn_reason"]]),
            # #2127 — two SUB-populations of `disagreed`, recorded so they can
            # be counted rather than read out of finding prose. Neither moves
            # `agreed`/`disagreed`: `met` is decided exactly as before.
            #   misscoped              the addressed layer misses it and
            #                          another layer the program track wrote
            #                          carries every expected token
            #   field_path_undeclared  the expectation named a path its layer
            #                          does not declare (counted over every
            #                          expectation whose layer was readable,
            #                          met or not)
            "misscoped": len([c for c in converged
                              if c["usable"] and not c["met"]
                              and c["owning_layers"]]),
            "field_path_undeclared": len(
                [c for c in converged
                 if c["field_path_status"] == "UNDECLARED"]),
            # How many readings could NOT be scoped to the field the
            # expectation named. Every one of them is a weaker claim than the
            # report's wording suggests, and a count is the only way a reader
            # sees that without opening each finding.
            "unscoped_readings": len(
                [c for c in converged if c["scope"] == "whole_layer"]),
            # Withdrawn WITH a reason. A reasonless withdrawal is refused and
            # is therefore NOT counted here — it is still decided, and counting
            # it would make a refused withdrawal look like an honoured one.
            "withdrawn": len([c for c in converged if c["withdrawn_reason"]]),
        },
        "findings": findings,
        # #312's own rule turned on this landing's scope decision. The
        # deterministic rule below has a real payload and REPORTS every
        # divergence it finds, but nothing in Phase 1 STOPS on it. A deferral
        # nobody can see is the same defect this track exists to name, so the
        # deferral is a recorded fact rather than an absence a reader has to
        # infer from a passing run.
        "deferred_enforcement": {
            "subject": "nvm_program_supply — the programmable-NVM programming "
                       "supply convention",
            "blocking_gate_landed": False,
            "reason": (
                "the findings are advisory. Promoting this convention to a "
                "blocking Phase-1 gate is a separate enforcement decision: its "
                "blocking behaviour has never been observed on a real design "
                "(every design in the fleet corpus assesses as "
                "not-applicable), and a stop that has only ever been exercised "
                "against synthesised fixtures is an unmeasured stop. The "
                "assessment library it would call is here and tested; what is "
                "deferred is the automatic stop, not the knowledge."),
            "issue": "vibe-ic#312",
        },
        "track_health": {
            # A visible fact, not an inference: nothing in the repo writes this
            # file, so the three gates that read it measure a haystack no
            # producer ever fills. See the module docstring on why THIS track
            # must not be that producer.
            "ai_patch_sidecar_path": str(sidecar),
            "ai_patch_sidecar_present": sidecar.is_file(),
            "input_text_chars": len(prompt),
            # A zero here means one of five things and the status says which.
            "input_text_status": input_report["status"],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project_dir", type=Path)
    ap.add_argument("--json", default=None)
    ap.add_argument("--input-readability", action="store_true",
                    help="print ONLY whether this project's design input can "
                         "be read, and what was skipped — the census a "
                         "corpus-wide loop needs (#2164). Exit 0 either way: "
                         "an unreadable input is a fact to report, not a run "
                         "that failed.")
    args = ap.parse_args(argv)
    if not args.project_dir.is_dir():
        print(f"ERROR: not a directory: {args.project_dir}", file=sys.stderr)
        return 1

    project = args.project_dir.resolve()
    if args.input_readability:
        rep = input_text_report(project)
        disp = input_readability_disposition(rep)
        print(f"{disp['retrieval_input']}\t{rep['status']}\t"
              f"chars={rep['chars']}\tread={len(rep['files_read'])}\t"
              f"skipped={len(rep['files_skipped'])}\t"
              f"unsupported={json.dumps(rep['unsupported_suffixes'], sort_keys=True)}"
              f"\t{project}")
        if disp["retrieval_input"] == "NOT_MEASURED":
            print(f"  reason: {disp['reason']}")
        return 0
    try:
        rep = evaluate(project)
    except Exception as exc:  # noqa: BLE001
        # The track failing is itself reportable. Exit 1: a second track that
        # cannot run must say so loudly, never look like a clean run.
        print(f"{PROGRAM}: ERROR — the expert track did not complete: {exc}",
              file=sys.stderr)
        return 1

    rc = rep.pop("rc")
    rep = {"program": PROGRAM, "version": VERSION, **rep}
    out = json.dumps(rep, indent=2, ensure_ascii=False)

    target = Path(args.json) if args.json else \
        _pl.report_path(project, "phase1/expert_parse_track.json")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out)
    except OSError as exc:
        print(f"{PROGRAM}: ERROR — report not written to {target}: {exc}",
              file=sys.stderr)
        return 1

    if rep["verdict"] == "INCOMPLETE":
        # The `INCOMPLETE:` sentinel is the house mechanism (#599) for "not
        # audited, and someone must come back". Name the exact state: a handoff
        # is actionable, a schema mismatch is a refused answer, and an empty
        # answer is a measured zero; none is completed execution.
        ai = rep["ai_subtrack"]
        print(f"INCOMPLETE: {PROGRAM} — the AI sub-track did not deliver a "
              f"non-empty schema-readable review ({ai['status']}): "
              f"{ai['reason']} The expert answer was NOT credited as consumed; "
              f"the deterministic findings are a floor, not coverage")
        # The state a reader can ACT on is printed as its own line. "Awaiting"
        # with no named action is the same silence as a bare INCOMPLETE.
        aw = rep.get("awaiting")
        if aw:
            print(f"  awaiting: {aw['what']} — {aw['action']} "
                  f"(exit {aw['exit_code']}: a stated wait, not a failed run; "
                  f"not credited)")
    den = rep["denominator"]
    print(f"{PROGRAM}: {rep['verdict']} — examined "
          f"{den['total']} expectation(s) "
          f"({den['deterministic']} deterministic + {den['ai']} from the AI "
          f"sub-track); {len(rep['findings'])} finding(s); AI sub-track "
          f"{rep['ai_subtrack']['status']}")
    for f in rep["findings"]:
        print(f"  [{f['severity']}] {f['rule']}: {f['message']}")
    print(f"  report: {target}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
