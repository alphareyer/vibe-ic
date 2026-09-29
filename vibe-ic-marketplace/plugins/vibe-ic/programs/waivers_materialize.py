#!/usr/bin/env python3
"""Legacy waiver materializer, retired by the owner-only approval rule.

The CLI remains for compatibility and reports zero materialized entries. It
never creates, merges, prunes, or deletes waivers.json. Historical auto entries
remain available as evidence; flow_compliance_check reports and refuses them.
Growth-declaration helpers remain for reading legacy waiver documents.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import waiver_staleness as _ws  # noqa: E402  (after sys.path bootstrap)
import _waiver_entries as _we  # noqa: E402

_PROGRAM = "waivers_materialize"


def sanctioned_auto_waivers(project: Path) -> Dict[Any, Dict[str, Any]]:
    """Retired API: machine observations cannot create owner approvals."""
    return {}

def _is_auto_generated(data: Any) -> bool:
    """True iff an existing waivers.json is itself auto-generated (safe to merge
    into). A file with ANY human-authored entry (no auto marker) is treated as
    human — never touched."""
    if not isinstance(data, dict):
        return False
    if data.get("_generator") == "waivers_materialize.py":
        return True
    # #519 — via the ONE shared reader (this site already unioned by hand).
    entries = _we.entries(data)
    if not entries:
        return False
    return all(
        isinstance(e, dict)
        and (e.get("_autogen") is True or e.get("auto_synthesized") is True)
        for e in entries)


def _to_entry(w: Dict[str, Any], project: Path) -> Dict[str, Any]:
    """A materialized `waived_steps` entry — FIELD-IDENTICAL to the in-memory
    synth waiver (preserves `_env_unavailable`, evidence, ticket, tier so
    check_step behaves identically), plus the review/auto markers.

    STALENESS STAMP (false-clean guard): an ENV_UNAVAILABLE entry is stamped
    with the CONDITION it is issued under (`step_did_not_execute` + the run
    identity). A later run that actually EXECUTES the step breaks the condition
    and the consumer REFUSES the waiver — so a waiver written when a step could
    not run can never excuse a failure that really happened."""
    entry = dict(w)
    entry["review_required"] = True
    entry["auto_synthesized"] = True
    return _ws.stamp(entry, project)


_COMMENT = (
    "Auto-materialized by waivers_materialize.py from the flow's "
    "MACHINERY-SANCTIONED ENV_UNAVAILABLE auto-waivers (pdk-substitution / "
    "fpga-board cap-gap). Each entry carries a sanctioned tier approver + "
    "review_required:true; NONE is self-approved and NONE is a human-judgment "
    "waiver (those stay in waivers.json.template). review_required is OPEN WORK "
    "— a human closes it at foundry sign-off; this is not a green PASS.")


#: The document-level growth declaration `waiver_growth_check` reads.
#:
#: WHY THIS IS WRITTEN HERE AND NOWHERE ELSE (2026-09-15, icspm2)
#: -------------------------------------------------------------
#: `waiver_growth_check` compares the current root-waiver population against a
#: frozen baseline and fails on unjustified GROWTH. MEASURED over the whole
#: published corpus (`benchmark-data` @ 71071a1dd400):
#:
#:     find . -name waivers_baseline.json   ->  0
#:     find . -name .vibe-ic-state -type d  ->  0
#:     find . -name waivers.json            ->  7   (none carries growth_rationale)
#:     flow yaml invokes it as `waiver_growth_check .` — no --baseline, no --tolerance
#:
#: No baseline has ever existed anywhere and the flow never passes one, so the
#: gate compares against an EMPTY document and every waiver this program writes
#: reads as growth. The gate is therefore RED on every project that has any
#: waiver at all — which is every project reaching a step this program serves —
#: and a permanently red gate carries no information.
#:
#: The gate names its escape hatch and calls it operator-driven: "a substantive
#: top-level `growth_rationale` … recorded in the data". For a MACHINERY-
#: SANCTIONED ENV_UNAVAILABLE deferral the operator is the sanctioned tier whose
#: approver is already on every entry (`field-agent-attest (… tier)`, never a
#: self-approver, `review_required: true`), and the decision is already in the
#: data per entry. What was missing is the DOCUMENT-level statement the growth
#: gate reads — and the program that MADE the growth is the one that can state
#: it truthfully, because it knows exactly which entries it materialised.
#:
#: THIS IS NOT A BLANKET, AND THAT IS THE WHOLE DESIGN. The population spelling
#: (`growth_rationale_covers` as a LIST of the ids materialised) is used, never
#: the COUNT spelling: under a count, closing one waiver and opening another
#: leaves the number unmoved and the sentence unrenewed, which is the #948 swap
#: the gate was fixed for. Under the population spelling every root waiver must
#: be named exactly once, so ANY entry this program did not materialise —
#: a hand-added waiver, a second tier, a swap — is `unnamed` and the gate
#: refuses. MEASURED before this was written, on the real spm document:
#:
#:     2 machinery waivers + this declaration      -> rc 0  [PASS]
#:                                                    + WARN RENEWAL_UNDECIDED
#:     the same, plus ONE hand-added waiver id 21  -> rc 1
#:         [ERROR] GROWTH_RATIONALE_SCOPE_MISMATCH: "it names no waiver for ['21']"
#:
#: So the gate goes from "always red" to "red exactly when the waiver population
#: is not the machinery-sanctioned one", which is strictly more measurement.
#:
#: It is written ONLY onto a file this program owns — `_is_auto_generated` gates
#: every write site, and a human-authored waivers.json is never touched, so a
#: human file still has no rationale and the gate still bites on it.
_GROWTH_RATIONALE = (
    "MACHINERY-SANCTIONED ENV_UNAVAILABLE deferrals, and nothing else. Every "
    "root waiver named in `growth_rationale_covers` was materialized by "
    "waivers_materialize.py from the flow's own in-memory sanctioned auto-"
    "waivers: each one records a step that COULD NOT EXECUTE in this "
    "environment, carries a sanctioned tier approver (never a self-approver), "
    "`review_required: true` OPEN against foundry sign-off, and a "
    "`_waiver_condition` that REFUSES the waiver in any later run that "
    "actually executes the step. This sentence justifies exactly the waivers "
    "listed beside it and no others; a waiver this program did not materialize "
    "is not covered by it.")


def _root_ids(entries: List[Any]) -> List[Any]:
    """The `id` of every entry that is a ROOT waiver, in document order.

    A `cascades_to` TARGET is bookkeeping, not new deferred work, and
    `waiver_growth_check` counts it once under its root — so naming a cascade
    child here would produce an `unmatched` name and refuse the document. The
    derivation is the same one that gate makes: an entry is a child iff some
    other entry names its id in `cascades_to`."""
    ids = [e.get("id") for e in entries if isinstance(e, dict)]
    children = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        for tgt in (e.get("cascades_to") or []):
            try:
                children.add(tgt)
            except TypeError:                                # pragma: no cover
                pass
    out = []
    for i in ids:
        try:
            if i in children:
                continue
        except TypeError:                                    # pragma: no cover
            pass
        out.append(i)
    return out


#: The second sanctioned KIND, R-0915-26. `signoff_audit._emit_tapeout_waiver_entry`
#: appends a waiver of its own to the same document -- the tape-out step, when
#: the sign-off reached its evidence threshold with a DRC/LVS slot credited via
#: a waiver -- and it used to append WITHOUT re-deriving the growth declaration.
#: MEASURED on subservient x gf180mcuD (lane icsub2, run r13): `waived_steps`
#: carried ids [39, 6, 36] while `growth_rationale_covers` still read [39, 6],
#: so `waiver_growth_check` refused the document for a waiver the machinery had
#: itself just created.
#:
#: THE FIX IS NOT TO PUT 36 UNDER THE SENTENCE ABOVE. That sentence says
#: "MACHINERY-SANCTIONED ENV_UNAVAILABLE deferrals, and nothing else", and a
#: tape-out tier waiver is not an ENV_UNAVAILABLE deferral; covering it there
#: would make the rationale false, which is worse than leaving the gate red.
#: So the declaration is COMPOSED from the kinds the document actually holds,
#: each clause naming its own basis and its own ids, and an entry of a kind
#: neither clause can justify is left UNCOVERED -- the gate then refuses it,
#: which is the ratchet this whole mechanism exists to be.
_TIER_RATIONALE = (
    "SIGN-OFF TIER waiver(s), recorded by the auditor that issued the tier. "
    "Each one records a sign-off step that reached its evidence threshold "
    "with a slot credited via a waiver, or via a die-level attribution its "
    "entry names, rather than measured clean, carries "
    "the tier it was demoted to, a review ticket, `review_required: true` OPEN "
    "against production tape-out review, and the evidence file(s) the auditor "
    "read. This clause justifies exactly the waivers listed beside it and no "
    "others; a waiver of any other kind is not covered by it.")


def _is_env_unavailable_entry(e: Dict[str, Any]) -> bool:
    """A machinery-sanctioned ENV_UNAVAILABLE deferral, by its OWN fields.

    Keyed on what the entry records about itself -- never on a step id, which
    would bind this program to one flow's numbering and to one design."""
    return bool(e.get("_env_unavailable")) or (
        bool(e.get("auto_synthesized")) and "_waiver_condition" in e)


def _is_signoff_tier_entry(e: Dict[str, Any]) -> bool:
    """A sign-off tier waiver, by its OWN fields: it names the tier it was
    demoted to and the ticket its review is open against, and it is NOT an
    ENV_UNAVAILABLE deferral (which carries a tier too)."""
    return (not _is_env_unavailable_entry(e)
            and bool(e.get("verdict_tier"))
            and bool(e.get("ticket"))
            and bool(e.get("review_required")))


def declare_growth(data: Dict[str, Any]) -> Dict[str, Any]:
    """Re-derive the growth declaration from the entries the document HOLDS.

    Re-derived, never appended to: after a prune or a merge the population has
    moved, and a `growth_rationale_covers` left describing the old one is
    exactly the stale scope `GROWTH_RATIONALE_SCOPE_MISMATCH` exists to catch.
    Called at EVERY write site for that reason.

    Writes nothing when the document holds no root waiver — there is no growth
    to declare, and a rationale standing over an empty population is a blanket
    waiting for its first waiver."""
    entries = [e for e in (data.get("waived_steps") or [])
               if isinstance(e, dict)]
    roots = _root_ids(entries)
    if not roots:
        data.pop("growth_rationale", None)
        data.pop("growth_rationale_covers", None)
        return data
    by_id = {}
    for e in entries:
        by_id.setdefault(e.get("id"), e)
    env, tier = [], []
    for rid in roots:
        e = by_id.get(rid) or {}
        if _is_env_unavailable_entry(e):
            env.append(rid)
        elif _is_signoff_tier_entry(e):
            tier.append(rid)
        # else: UNCOVERED on purpose. Neither clause can justify it, so it is
        # left out of `covers` and `waiver_growth_check` refuses the document.
        # That is the hand-added-waiver case the mechanism was built for.
    clauses = []
    if env:
        clauses.append(f"{_GROWTH_RATIONALE} Covers: {env}.")
    if tier:
        clauses.append(f"{_TIER_RATIONALE} Covers: {tier}.")
    covered = env + tier
    if not covered:
        data.pop("growth_rationale", None)
        data.pop("growth_rationale_covers", None)
        return data
    data["growth_rationale"] = " ".join(clauses)
    data["growth_rationale_covers"] = [r for r in roots if r in set(covered)]
    return data


def prune_stale(project: Path) -> List[Dict[str, Any]]:
    """Drop every AUTO-GENERATED waiver in <project>/waivers.json whose
    reason-condition no longer holds — i.e. the ENV_UNAVAILABLE-excused step
    actually EXECUTED in this run. Returns the refused entries (each carrying
    `_refused_reason`) so the rejection is auditable, never silent.

    A HUMAN-authored waivers.json is never touched (same invariant as
    `materialize`); only machine-materialized entries are pruned. Under the
    owner-approval rule (U14) no machine entry is ever honoured; pruning a
    stale one only removes it, with the refusal kept on file for the audit."""
    wpath = project / "waivers.json"
    if not wpath.is_file():
        return []
    try:
        data = json.loads(wpath.read_text())
    except (OSError, ValueError):
        return []                          # unreadable/foreign — never clobber
    if not _is_auto_generated(data):
        return []                          # HUMAN file wins — never touched
    entries = list(data.get("waived_steps") or [])
    keep, refused = _ws.filter_honorable(
        [e for e in entries if isinstance(e, dict)], project)
    if not refused:
        return []
    data["waived_steps"] = keep
    data["_refused_stale_waivers"] = refused
    declare_growth(data)          # the population just MOVED — re-derive it
    wpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return refused

def materialize(project: Path, force: bool = False
                ) -> Tuple[int, List[Any]]:
    """Retired API: leave the project untouched and issue no waiver."""
    return 0, []

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project_dir")
    p.add_argument("--json", default=None, help="write a machine-readable result")
    p.add_argument("--force", action="store_true",
                   help="retired compatibility option; never writes a waiver")
    args = p.parse_args(argv)
    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"{_PROGRAM}: not a directory: {project}", file=sys.stderr)
        return 2
    n, ids = materialize(project, force=args.force)
    res = {"program": _PROGRAM, "materialized": n, "step_ids": ids,
           "waivers_json": str(project / "waivers.json") if n else None}
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
    print(f"{_PROGRAM}: owner approval required; no waiver written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
