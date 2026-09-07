#!/usr/bin/env python3
"""sta_architectural_residual_check.py — a sign-off setup violation that NO
placement, routing, resizing or clock-tree work can close is a re-authoring
request, and the flow must say so and name where it goes.

THE DEFECT (measured, vibe-ic#2081, sha256 x sky130A, lane rbsha2)
==================================================================
The run reached the end of Phase 3 — route converged, sign-off DRC 0, LVS
MATCH, post-layout LEC PROVEN_EQUIVALENT — and the sign-off STA violated the
SS process corner at setup -2.530 ns / TNS -66.25.  The RTL is a one-round-
per-cycle SHA-256 compressor whose round is a 32-bit ripple carry.  Placement
and routing cannot remove a combinational depth the architecture implies; only
re-authoring the RTL can.

The flow had nowhere to put that discovery.  MEASURED on the flow itself:

  * `flow/phase1_phase2_phase3.yaml` step 23 (post-route STA sign-off) declares
    `closed_loop.fallback_to: 32` — post-route timing REPAIR, a physical remedy
    — and step 10 (pre-layout STA) declares `fallback_to: 7`, constraint setup.
    Neither is step 1, Spec-to-RTL.  No step in the file routes a Phase-3 STA
    verdict to authoring.
  * `benchmark/CAPTURE_ROUTING.json` `phase3.sta` routes to
    `programs/phase3_one_shot_runner.py` + `skills/sta-review/SKILL.md`.  Also
    not authoring.
  * `programs/sta_triage_classify.py` already OWNS the category
    `logic_depth_limited` -> "pipeline path; restructure RTL".  It is named by
    no flow step and no gate; it takes a pre-parsed `--endpoints-json` that
    nothing in the flow emits; and it returns 0 unconditionally.  The flow
    could name the case and could not refuse on it.

WHAT THIS GATE ASSERTS
======================
Read the run's OWN post-route sign-off timing reports.  For each VIOLATED
setup path, bound from the report alone how much slack any PHYSICAL remedy
could still recover, and REFUSE when the violation exceeds that bound.

  T_buf   -- the delay on arcs through buffer/inverter cells.  Re-placement,
             rerouting and resizing act on these; charge every one of them to
             the physical side, in full, as recoverable.
  skew_adverse -- max(0, launch insertion - capture insertion).  A perfectly
             balanced clock tree recovers exactly this and no more.
  PRUB    -- physical-recovery upper BOUND = T_buf + skew_adverse.
  residual = |slack| - PRUB.

`residual > 0` means: delete every buffer on the path AND balance the clock
perfectly, and the path is still `residual` ns over its budget.  What remains
is delay on LOGIC arcs, which placement cannot remove and re-authoring can.

ONE-SIDED, DELIBERATELY
=======================
Firing proves the violation is architectural.  NOT firing proves NOTHING —
it means this bound did not settle the question, not that the violation is
physical.  The bound is generous to the physical side on purpose (buffers are
charged as fully removable although removing one restores the net delay it was
inserted to hide), so the refusal is conservative in the direction that matters.

THE DRIVE-LIMITED GUARD, and the false positive that forced it
==============================================================
MEASURED on this same run's `per_corner/sta_SS.rpt`: 13 arcs, no buffers, slack
-125.54 ns -- the bare formula calls it architectural.  It is not.  Two arcs
carry 142 of its 151 ns: an unbuffered high-fanout net that placement and
buffering DID fix (-125.54 -> -2.53 on the routed netlist).  So a path on which
one arc carries more than `MAX_ARC_FRACTION` of the data delay is classified
`drive_limited` and never counted architectural: a single dominant arc is a
drive/fanout problem, not a depth problem.  Two further controls in the same
directory (TT -103.93 at 66%, FF -41.43 at 57%) are caught by the same guard.

The basis guard says the same thing structurally: only a report stamped
POST_ROUTE is judged, because only then has the tool already had its chance to
place, buffer and repair.  A pre-layout estimate is the tool BEFORE it tried.

WHAT IT DOES NOT DO
===================
It never relaxes a period, drops a corner, widens a constraint, moves a target,
or turns a FAIL into a PASS.  It has no baseline and nothing to re-date.  It
adds no remedy: it names a residual and routes it.  A run with no violated
sign-off path passes it unconditionally.

EXIT CODES
==========
  0  PASS        — no post-route sign-off path is proven architectural.
  1  FAIL        — at least one is; the reason names the corner, the endpoints,
                   the residual, and the flow step that can act on it.
  2  NOT CHECKED — no readable post-route sign-off timing report.  Absence of
                   an input is not a pass (vibe-ic#1140).

Chip / PDK / vendor-AGNOSTIC: no chip, node, library or vendor literal here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _atomic_artefact import write_text as atomic_write_text  # noqa: E402
from _prose_polarity import is_denied  # noqa: E402
from _sta_basis import declared_basis  # noqa: E402
# The report locations come from the module that already owns them, never
# re-typed here: a checker that spells its own subject drifts into asserting
# about a file nobody writes and passes forever.
from sta_corner_record_completeness_check import (  # noqa: E402
    _MCORNER_OCV_CANDIDATES, _MULTICORNER_CANDIDATES, _NOMINAL_SPEF_CANDIDATES)

#: The step this gate routes its finding TO — the one step that can remove a
#: combinational depth. Read from `flow/phase1_phase2_phase3.yaml` step 1.
ROUTE_STEP = "1"
ROUTE_STEP_NAME = "Spec-to-RTL"
ROUTE_SKILL = "spec-to-rtl"

#: A single arc carrying more than this share of the data path is a drive /
#: fanout problem, not a depth problem. See the docstring's measured control.
MAX_ARC_FRACTION = 0.25

#: Cell-name fragments that name a buffering / inverting cell. Structural
#: vocabulary, not a library list: every open PDK spells these the same way.
_BUFFER_CELL_RE = re.compile(
    r"(?:^|_)(?:buf|bufinv|inv|clkbuf|clkinv|clkdlybuf|dlygate|dlymetal|"
    r"lpflow_clkbuf|lpflow_inv)", re.I)

#: One arc row of an OpenSTA `report_checks` path. The leading Cap/Slew pair is
#: present in the SPEF-based reports and absent in the estimate ones.
_ARC_RE = re.compile(
    r"^\s+(?:[\d.]+\s+[\d.]+\s+)?([\d.]+)\s+[\d.]+\s+[v^]\s+(\S+)\s+\((\S+)\)",
    re.M)
_INSERTION_RE = re.compile(r"^\s+([\d.]+)\s+[\d.]+\s+clock network delay", re.M)
_SLACK_RE = re.compile(r"(-?\d+\.\d+)\s+slack \(VIOLATED\)")
_START_RE = re.compile(r"^Startpoint:\s*(\S+)", re.M)
_END_RE = re.compile(r"^Endpoint:\s*(\S+)", re.M)
#: A corner-section banner. The label is the `process=<name>` token when the
#: emitter writes one, else the whole banner text: the point is to name the
#: corner the reader will find in the corner record, not to re-spell it.
_CORNER_RE = re.compile(r"^===\s*(.+?)\s*===", re.M)
_PROCESS_RE = re.compile(r"process\s*=\s*(\S+)")
#: Setup paths only. A hold violation is a different physics with a different
#: remedy (min-path padding), and this bound says nothing about it.
_SETUP_PATH_RE = re.compile(r"^Path Type:\s*max\s*$", re.M)


def _is_buffer(cell: str) -> bool:
    return bool(_BUFFER_CELL_RE.search(cell.split("__")[-1] or cell))


def classify_path(chunk: str) -> Optional[Dict[str, object]]:
    """One `Startpoint:`-headed path chunk -> its classification, or None when
    the path is not a violating one."""
    if not _SETUP_PATH_RE.search(chunk):
        return None
    m = _SLACK_RE.search(chunk)
    if not m:
        return None
    slack = float(m.group(1))
    arcs = [(float(d), pin, cell) for d, pin, cell in _ARC_RE.findall(chunk)]
    if not arcs:
        return None
    total = sum(d for d, _, _ in arcs)
    if total <= 0:
        return None
    t_buf = sum(d for d, _, cell in arcs if _is_buffer(cell))
    ins = [float(v) for v in _INSERTION_RE.findall(chunk)]
    launch = ins[0] if ins else 0.0
    capture = ins[1] if len(ins) > 1 else launch
    skew_adverse = max(0.0, launch - capture)
    prub = t_buf + skew_adverse
    max_arc = max(d for d, _, _ in arcs)
    max_arc_fraction = max_arc / total
    residual = abs(slack) - prub

    if max_arc_fraction > MAX_ARC_FRACTION:
        category = "drive_limited"
    elif residual > 0:
        category = "architectural"
    else:
        category = "physical_reachable"

    start = _START_RE.search(chunk)
    end = _END_RE.search(chunk)
    return {
        "startpoint": start.group(1) if start else None,
        "endpoint": end.group(1) if end else None,
        "slack_ns": round(slack, 3),
        "depth_arcs": len(arcs),
        "buffer_arcs": sum(1 for _, _, c in arcs if _is_buffer(c)),
        "data_delay_ns": round(total, 3),
        "buffer_delay_ns": round(t_buf, 3),
        "skew_adverse_ns": round(skew_adverse, 3),
        "physical_recovery_upper_bound_ns": round(prub, 3),
        "residual_ns": round(residual, 3),
        "max_arc_fraction": round(max_arc_fraction, 4),
        "category": category,
    }


def analyse_report(text: str) -> Tuple[Optional[str], List[Dict[str, object]]]:
    """(declared basis, per-violating-path classifications) for one report.

    THE CORNER LABEL IS PROSE, AND ITS POLARITY IS ASKED (vibe-ic#712).
    A section banner is not a formal grammar: `_PROCESS_RE` matches when the
    emitter writes a `process=` token and the fallback publishes the banner's
    own WORDS, so an emitter that writes

        === Corner SS: this corner is NOT a sign-off corner, reference only ===

    would otherwise have that sentence written into a declared `corner` field
    and quoted back in the refusal as the corner this gate judged — #706's shape
    exactly, one field over. The banner IS the sentence, so it is the polarity
    span; when it denies, the label is NOT published as a declaration. The
    finding itself is untouched: the path is still classified and still refused,
    and the raw banner and the denial word are carried as evidence so a reader
    can see what was withheld and why. Nothing is waived by asking.
    """
    basis = declared_basis(text)
    paths: List[Dict[str, object]] = []
    corner = None
    corner_banner = None
    corner_denial = None
    for chunk in re.split(r"(?=^Startpoint: )", text, flags=re.M):
        for cm in _CORNER_RE.finditer(chunk):
            banner = cm.group(1)
            pm = _PROCESS_RE.search(banner)
            label = pm.group(1) if pm else banner.split(",")[0].strip()
            denial = is_denied(banner)
            corner_banner = banner
            corner_denial = denial
            corner = None if denial else label
        if not chunk.startswith("Startpoint:"):
            continue
        rec = classify_path(chunk)
        if rec is not None:
            rec["corner"] = corner
            rec["corner_banner"] = corner_banner
            rec["corner_label_denied"] = corner_denial
            paths.append(rec)
    return basis, paths


def corner_name(rec: Dict[str, object]) -> str:
    """How a refusal NAMES the corner of a path whose banner denied its label."""
    if rec.get("corner"):
        return str(rec["corner"])
    if rec.get("corner_label_denied"):
        return (f"<corner unnamed: its section banner denies its own label "
                f"({rec['corner_label_denied']!r} in "
                f"{str(rec.get('corner_banner'))!r})>")
    return "<corner unnamed: no section banner in the report>"


def _candidates(project: Path) -> List[Path]:
    rels: List[str] = []
    for group in (_MCORNER_OCV_CANDIDATES, _MULTICORNER_CANDIDATES,
                  _NOMINAL_SPEF_CANDIDATES):
        rels.extend(group)
    # The SAME report is written to two or three locations by the emitters
    # (`phase3/stage3/sta/x.rpt` and `reports/phase3/x.rpt`). Counting both
    # doubles every finding, so identical content is read ONCE. MEASURED on
    # vibe-ic#2081's own run: 3 violating paths became 6, and the one
    # architectural path was reported as two.
    seen: List[Path] = []
    digests: set = set()
    for rel in rels:
        p = project / rel
        if not p.is_file():
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in digests:
            continue
        digests.add(digest)
        seen.append(p)
    return seen


def check(project: Path) -> Dict[str, object]:
    rep: Dict[str, object] = {
        "program": "sta_architectural_residual_check",
        "project": str(project),
        "verdict": "NOT_CHECKED",
        "route_step": ROUTE_STEP,
        "route_step_name": ROUTE_STEP_NAME,
        "route_skill": ROUTE_SKILL,
        "reports_read": [],
        "reports_skipped": [],
        "paths": [],
        "architectural_paths": [],
        "reason": "",
        "reasons": [],
    }
    present = _candidates(project)
    if not present:
        rep["reason"] = ("NOT CHECKED — no post-route sign-off timing report "
                         "on disk; this gate has no input, which is not a pass")
        rep["reasons"] = [rep["reason"]]
        return rep

    judged = False
    for path in present:
        text = path.read_text(errors="replace")
        basis, paths = analyse_report(text)
        rel = str(path.relative_to(project)) if path.is_relative_to(project) \
            else str(path)
        if basis != "POST_ROUTE":
            rep["reports_skipped"].append(
                {"report": rel,
                 "declared_basis": basis,
                 "why": "not a post-route sign-off basis — before place, "
                        "buffer and repair the tool has not yet had its "
                        "chance, so no residual here is architectural"})
            continue
        judged = True
        rep["reports_read"].append(rel)
        for rec in paths:
            rec["report"] = rel
            rep["paths"].append(rec)

    if not judged:
        rep["reason"] = ("NOT CHECKED — every sign-off timing report on disk "
                         "declares a basis other than POST_ROUTE; nothing here "
                         "measures the design after the tool's physical "
                         "remedies ran")
        rep["reasons"] = [rep["reason"]]
        return rep

    arch = [p for p in rep["paths"] if p["category"] == "architectural"]
    rep["architectural_paths"] = arch
    if not arch:
        n_viol = len(rep["paths"])
        rep["verdict"] = "PASS"
        rep["reason"] = (
            f"no post-route sign-off path is PROVEN architectural "
            f"({n_viol} violating path(s) read). This is one-sided: it does "
            f"not certify that any remaining violation is physical.")
        rep["reasons"] = [rep["reason"]]
        return rep

    worst = max(arch, key=lambda p: p["residual_ns"])
    rep["verdict"] = "FAIL"
    rep["reason"] = (
        f"{len(arch)} sign-off setup path(s) violate by MORE than any physical "
        f"remedy can recover: worst is corner '{corner_name(worst)}' "
        f"{worst['startpoint']} -> {worst['endpoint']} at slack "
        f"{worst['slack_ns']} ns, of which only "
        f"{worst['physical_recovery_upper_bound_ns']} ns is reachable by "
        f"re-buffering ({worst['buffer_delay_ns']} ns of buffer arcs) plus a "
        f"perfectly balanced clock ({worst['skew_adverse_ns']} ns of adverse "
        f"skew) — leaving {worst['residual_ns']} ns of LOGIC-arc delay over "
        f"budget across {worst['depth_arcs']} arcs. Placement and routing "
        f"cannot remove a combinational depth the ARCHITECTURE implies. "
        f"ROUTED TO step {ROUTE_STEP} ({ROUTE_STEP_NAME}, skill "
        f"'{ROUTE_SKILL}'): this is a re-authoring request, not a closure "
        f"problem. The residual is NAMED, never waived — do not answer it by "
        f"re-declaring the period, dropping the corner or moving the target.")
    rep["reasons"] = [rep["reason"]] + [
        (f"corner '{corner_name(p)}' {p['startpoint']} -> {p['endpoint']}: "
         f"slack {p['slack_ns']} ns, PRUB "
         f"{p['physical_recovery_upper_bound_ns']} ns, residual "
         f"{p['residual_ns']} ns ({p['report']})")
        for p in arch]
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="REFUSE when a post-route sign-off setup violation "
                    "exceeds every physical remedy's upper bound, and route "
                    "it to the step that can act on it.")
    ap.add_argument("project")
    ap.add_argument("--json", help="write the structured report here")
    args = ap.parse_args(argv)
    rep = check(Path(args.project))
    if args.json:
        atomic_write_text(Path(args.json), json.dumps(rep, indent=2) + "\n")
    print(f"[{rep['verdict']}] sta_architectural_residual_check — "
          f"{rep['reason']}")
    print(json.dumps(rep, indent=2))
    return {"PASS": 0, "FAIL": 1}.get(str(rep["verdict"]), 2)


if __name__ == "__main__":
    sys.exit(main())
