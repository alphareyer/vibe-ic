#!/usr/bin/env python3
"""Step 23: the sign-off deck of a DIE top carries a resolved off-chip input drive.

ENFORCEMENT: blocking. Declared as a Step-23 `program_exit_zero` clause in
flow/phase1_phase2_phase3.yaml (R-0929-IO-INPUT-TRANSITION-2), so
flow_compliance_check can never grade Step 23 PASS on ideal bond-pad edges.

R-0929-PAD-INPUT-DRIVE: on a pad-ring (DIE) top every input is a bond pad. Its
off-chip drive is the design's declared driver/transition, else the PDK IO
tier (the linked IO Liberty's characterised pad-edge bracket); never the core
synthesis driving cell, never an ideal edge. `sdc_environment` resolves it and
writes `reports/phase3/pad_input_drive.json` with the exact command lines the
deck must carry (`sdc_lines`). This gate reads that record and the deck PnR
and sign-off loaded (`phase3/stage3/pnr/constraint.sdc`):

* no owner-attested deliverable                   -> NOT_MEASURED,   rc 2
* owner-attested HARDMACRO (not a DIE top)        -> NOT_APPLICABLE, rc 0
* owner-attested DIE                              -> judged, below:
* record absent / unreadable / NOT_MEASURED       -> NOT_MEASURED,   rc 2
* record resolved against an older pad-ring record -> NOT_MEASURED,  rc 2
* sign-off deck absent                            -> NOT_MEASURED,   rc 2
* deck lacks a recorded drive line, or applies the
  refused core driving cell                       -> FAIL,           rc 1
* otherwise                                       -> PASS,           rc 0

Chip- and PDK-agnostic: every value comes from the record and the deck.
"""
from __future__ import annotations

import os as _os
import sys as _sys
if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from _atomic_artefact import write_json

PROGRAM = "pad_input_drive_check"
SIGNOFF_DECK = "phase3/stage3/pnr/constraint.sdc"
_RC = {"PASS": 0, "NOT_APPLICABLE": 0, "FAIL": 1, "NOT_MEASURED": 2,
       "REFUSED": 1}


def _route(project: Path):
    """(DIE | HARDMACRO | None, basis) from the OWNER-ATTESTED deliverable
    (`_delivery_route.owner_route`). R-0929-ROUTE-NOT-FROM-SILENCE: the route
    is never decided from absent files -- no SELF_TAPEOUT.txt and no slots/
    is silence, not a HARDMACRO answer. ef1b46bc65 decided "not a DIE" from
    exactly that silence, so an empty project read NOT_APPLICABLE and Step 23
    PASSed on a question nobody had answered."""
    try:
        import _delivery_route as _dr
        return _dr.owner_route(project)
    except Exception as exc:                                 # noqa: BLE001
        return None, f"the owner-attested route could not be read: {exc}"


def _not_a_die(out: Dict[str, Any], basis: str) -> Dict[str, Any]:
    """NOT_APPLICABLE_BY_STRUCTURE on the owner's HARDMACRO answer: a hard
    macro's inputs are driven on chip, so there is no bond pad to drive. It is
    never a PASS (R-0915-119)."""
    import _structural_absence as _sa
    import _delivery_route as _dr
    ev = _sa.absence("owner-attested deliverables requesting a pad ring", 1, 0,
                     names=[_dr.owner_route_rel(Path(out["project"]))],
                     detail=f"{basis}: inputs are driven on chip")
    doc = dict(out, verdict="NOT_APPLICABLE",
               reason=_sa.sentence(ev, "bond-pad input drive"))
    return _sa.attach(doc, ev)


def judge(project: Path, deck_rel: str = SIGNOFF_DECK) -> Dict[str, Any]:
    import sdc_environment as _se
    project = Path(project)
    out: Dict[str, Any] = {"program": PROGRAM, "project": str(project),
                           "record": _se.PAD_INPUT_DRIVE_REPORT, "deck": deck_rel}
    # F15 -- STEP 23 ON THE TOOL. When step 23 runs `librelane` or `dual`, the
    # deck that was signed off is the SDC the tool TIMED (its bound
    # `state_out.json` view), not the runner's own copy; a tool arm that cannot
    # be bound REFUSES before anything is judged, exactly as the other step-23
    # gates do (`librelane_signoff.step23_tool_arm`).
    import librelane_signoff as _ls
    from librelane_contract import Refusal
    try:
        arm = _ls.step23_tool_arm(project)
    except Refusal as exc:
        return dict(out, verdict="REFUSED", refusal=exc.code,
                    reason=f"step 23 runs on the tool and its sign-off cannot "
                           f"be read: {exc}")
    if arm is not None:
        out["tool_arm"] = _ls.tool_arm_basis(arm)
        try:
            state = json.loads((Path(arm["folder"]) / "state_out.json").read_text())
            deck_rel = str(state["sdc"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return dict(out, verdict="REFUSED", refusal="LL_STA_TOOL_ARM_UNREADABLE",
                        reason=f"the SDC the tool timed cannot be named: {exc}")
        out["deck"] = deck_rel
    delivery, basis = _route(project)
    out["route_basis"] = basis
    if delivery is None:
        return dict(out, verdict="NOT_MEASURED",
                    reason=f"{basis} -- the route (DIE or not) is the owner's "
                           f"answer at step 0.5ic and is not read from what "
                           f"is absent on disk")
    import _tapeout_declaration as _td
    if delivery != _td.DELIVERABLE_DIE:
        return _not_a_die(out, basis)
    try:
        rec = json.loads((project / _se.PAD_INPUT_DRIVE_REPORT).read_text())
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        return dict(out, verdict="NOT_MEASURED",
                    reason="no readable pad-input-drive record for this DIE top: "
                           "the off-chip input drive was never resolved")
    out["drive"] = {k: rec.get(k) for k in ("verdict", "model", "source")}
    if rec.get("verdict") not in ("DECLARED", "PDK_IO_TIER"):
        return dict(out, verdict="NOT_MEASURED",
                    reason=f"off-chip input drive {rec.get('verdict')}: "
                           f"{rec.get('reason') or 'unresolved'}")
    was = (rec.get("pdk_io_tier") or {}).get("record_sha256")
    if rec.get("verdict") == "PDK_IO_TIER":
        try:
            now = hashlib.sha256((project / _se.IO_PAD_RECORD).read_bytes()).hexdigest()
        except OSError:
            now = None
        if not was or was != now:
            return dict(out, verdict="NOT_MEASURED",
                        reason=f"the drive was resolved from a different "
                               f"{_se.IO_PAD_RECORD} than the one on disk")
    lines: List[str] = [str(x) for x in rec.get("sdc_lines") or [] if str(x).strip()]
    if not lines:
        return dict(out, verdict="NOT_MEASURED",
                    reason="the record names no SDC line for the resolved drive")
    try:
        deck = (project / deck_rel).read_text(errors="replace").splitlines()
    except OSError:
        return dict(out, verdict="NOT_MEASURED",
                    reason=f"sign-off deck {deck_rel} absent")
    deck_set = {ln.strip() for ln in deck}
    missing = [ln for ln in lines if ln.strip() not in deck_set]
    refused = (rec.get("refused_core_driving_cell") or {}).get("value")
    core = []
    if refused and "/" in str(refused):
        cell = str(refused).split("/", 1)[0]
        core = [ln for ln in deck if re.match(r"\s*set_driving_cell\b", ln)
                and re.search(r"-lib_cell\s+\{?%s\b" % re.escape(cell), ln)]
    out.update(lines_required=len(lines), lines_missing=missing,
               core_driving_cell_lines=core)
    if missing or core:
        return dict(out, verdict="FAIL",
                    reason=(f"the sign-off deck does not carry the resolved "
                            f"drive ({len(missing)} of {len(lines)} line(s) "
                            f"missing)" if missing else
                            f"the sign-off deck drives bond pads with the "
                            f"refused core cell {refused}"))
    return dict(out, verdict="PASS",
                reason=f"{rec.get('verdict')} drive ({rec.get('model')}) carried "
                       f"by the sign-off deck: {len(lines)} line(s)")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--json", default="", help="project-relative receipt path")
    ap.add_argument("--deck", default=SIGNOFF_DECK,
                    help="project-relative sign-off SDC (default: %(default)s)")
    args = ap.parse_args(argv)
    project = Path(args.project_dir).resolve()
    doc = judge(project, args.deck)
    # Typed non-verdict classes for flow_compliance_check: an unresolved drive
    # is an upstream gap (never a skip); a non-DIE top has no bond pad.
    doc.setdefault("reason_class", {"NOT_MEASURED": "BLOCKED_BY_UPSTREAM",
                                    "NOT_APPLICABLE": "NOT_APPLICABLE_BY_STRUCTURE"
                                    }.get(doc["verdict"]))
    if args.json:
        write_json(project / args.json, doc)
    print(f"[{doc['verdict']}] {PROGRAM}: {doc.get('reason')}")
    return _RC[doc["verdict"]]


if __name__ == "__main__":
    _sys.exit(main())
