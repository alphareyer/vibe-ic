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

WHOSE DIE THE SHORTFALL WAS MEASURED ON (vibe-ic#2160)
======================================================
A setup shortfall is a statement about a design AND about the die it was placed
on.  MEASURED on subservient x gf180mcuD and recorded in
`phase3_one_shot_runner._pdn_em_width_floor`'s own docstring: an EM strap width
derived from the I_total CONSERVATION BOUND demanded Metal4 20.77 um where the
per-segment MEASUREMENT needed 5.62 um (3.70x).  Seating those straps grew the
die 227x227 -> 416x416 um, which dropped core utilisation to 17 % against an
L9-DECLARED 50 %, which in turn "inflated CTS insertion delay to 6.47 ns, itself
40 % of the register-to-output-port setup budget".  That runner's own words for
it are "Two sign-off failures, one over-sized number" -- the setup shortfall
(#2160) and the die-level density rules (#2148) are ONE cause, not two.

The owner ruling of 2026-09-02 already fixed the sizing: when the run has
measured the distribution, the measurement supersedes the bound.  But the
measurement does not exist on a first pass -- `_pdn_em_width_floor` returns None
and the bound is used -- so a first-pass run is placed on the inflated die, and
NOTHING in its sign-off says so.  A reader of that run sees a setup shortfall and
a density failure and concludes the design cannot meet its DECLARED period.  The
honest statement is narrower: it did not meet the period ON A DIE SIZED FROM AN
UNMEASURED BOUND.

So when this gate reads a violating sign-off path AND the run's own
`pdn_em_sizing.json` says its strap widths came from the bound rather than from a
measurement, it NAMES that in the reason.  It does NOT change the verdict, the
route or the exit code: which remedy the shortfall needs is still decided by the
residual arithmetic alone.  This is disclosure, not a new refusal -- the shortfall
is still a shortfall, nothing is relaxed, no period is re-declared, and a run
whose die WAS sized from a measurement reads exactly as it did before.

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
try:
    import _path_layout as _pl  # noqa: E402
except Exception:  # pragma: no cover - _path_layout is always present in-tree
    _pl = None

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
    # Section banners delimit the preceding path too. Otherwise the next
    # corner's banner sits at the END of that path's chunk and relabels it
    # before classify_path runs (the last SS setup path becomes FF hold).
    for chunk in re.split(r"(?=^Startpoint: |^===\s)", text, flags=re.M):
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


#: The sizing bases `phase3_one_shot_runner._pdn_em_width_floor` records. Only
#: the bound is disclosed here: a measured basis is the preferred one and needs
#: no caveat. Spelled out rather than pattern-matched so a renamed basis reads as
#: "not the bound" and this disclosure goes quiet, which is the safe direction --
#: it can then under-disclose, never mis-disclose.
_EM_BOUND_BASIS = "i_total_conservation_bound"
_EM_MEASURED_BASIS = "measured_max_segment"

#: Where the runner writes the record. One path, read-only.
_EM_SIZING_REL = "reports/phase3/pdn_em_sizing.json"


#: THE FOURTH FIELD OF THE RE-AUTHORING REQUEST (vibe-ic#2081).
#:
#: The owner's ruling of 2026-09-09 names what a Phase-3 sign-off violation owes
#: a Phase-2 author: "a Phase-3 corner + slack + failing-path endpoints + THE
#: LATENCY CONTRACT THE SPEC ALLOWS". MEASURED on this gate before this change,
#: on #2081's own numbers (SS setup -2.53 ns, 39 arcs, PRUB 1.62): the request
#: carried the first three and nothing at all about the fourth —
#: `cycles_per_block` absent, `latency` absent, and the only "66" in the record
#: was the digits inside the endpoint name `_17661_`.
#:
#: WHY THE ABSENT FIELD IS THE DANGEROUS ONE. The request says "a combinational
#: depth the ARCHITECTURE implies", and the cheapest way for an author to cut
#: depth is to add a pipeline stage. On #2081's design that breaks the observable
#: 66-cycle READY contract — a design that closes timing and fails its spec.
#: Every remedy the owner measured on this very design was chosen to keep the
#: count: "Neither rewrite moves the cycle count."
#:
#: WHAT IT DOES AND DOES NOT SAY. It reports the cycle-count DECLARATIONS it can
#: read, with the file each came from. It does NOT rule on whether the spec
#: permits moving the count: on this design the L-docs contradict themselves
#: about exactly that (#2168), and a gate that picked a side would be asserting a
#: state nobody measured. So the sentence hands the author the declaration and
#: says the permission is not established here.
#:
#: The key set is SPELLED OUT, never pattern-matched, for the same reason
#: `_EM_BOUND_BASIS` is: a spelling this version does not know reads as "nothing
#: declared" and the disclosure goes quiet. It can under-disclose, never
#: mis-disclose.
_LATENCY_KEYS = (
    "latency_cycles_per_block",   # crypto_arch_extractor's own emitted field
    "total_cycles_per_block",     # agents/qbank/crypto-engine_L8R.yaml
    "throughput_cycles_per_block",  # agents/qbank/crypto-engine_L5.yaml
    "latency_cycles",
)


def _latency_rows(doc: object, source: str) -> List[Dict[str, object]]:
    """Every declared cycle count in ONE L-document, with where it was found."""
    rows: List[Dict[str, object]] = []
    if not isinstance(doc, dict):
        return rows
    scopes = [doc]
    fields = doc.get("fields")
    if isinstance(fields, dict):
        scopes.append(fields)
    for scope in scopes:
        for key in _LATENCY_KEYS:
            if key not in scope:
                continue
            value = scope[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                # A count that is not a number is not a count. Reading prose
                # here is how a gate starts asserting about a field it cannot
                # parse; it stays unread instead.
                continue
            row: Dict[str, object] = {"key": key, "cycles": value,
                                      "source": source}
            ev = scope.get(f"{key}_evidence")
            if isinstance(ev, dict):
                # crypto_arch_extractor records source/line/matched_token beside
                # the value; carry it so the author can check the declaration.
                row["evidence"] = ev
            rows.append(row)
    return rows


def latency_contract(project: Path) -> Optional[Dict[str, object]]:
    """The design's DECLARED cycles-per-block, or None when none can be read.

    None is returned for every uncertainty — no `_path_layout`, no
    `phase1/generated_docs`, a root this process cannot list, an unreadable or
    non-object document, and a document declaring no key this version knows.
    None means "no sentence is added", which leaves the request exactly as it
    was; it never means "the spec places no constraint".
    """
    if _pl is None:
        return None
    root = _pl.generated_docs_dir(project)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        # Absent, or present and unlistable. Both are "could not read", and a
        # failed read is never a declaration — least of all the declaration
        # that a count is free to move.
        return None
    rows: List[Dict[str, object]] = []
    for name in names:
        if not (name.startswith("L") and name.endswith(".json")):
            continue
        try:
            doc = json.loads((root / name).read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        rows.extend(_latency_rows(doc, name))
    if not rows:
        return None
    counts = sorted({row["cycles"] for row in rows})
    return {
        "declarations": rows,
        "declared_cycles": counts,
        "agrees": len(counts) == 1,
        "root": str(root),
    }


def _latency_sentence(info: Dict[str, object]) -> str:
    """The fourth field, stated without ruling on what it permits."""
    rows = info["declarations"]
    where = ", ".join(
        f"{r['source']}:{r['key']}={r['cycles']}" for r in rows)
    if info["agrees"]:
        head = (f" THE DESIGN DECLARES {info['declared_cycles'][0]} CYCLE(S) "
                f"PER BLOCK ({where}).")
    else:
        head = (f" THE DESIGN'S OWN DOCUMENTS DECLARE MORE THAN ONE CYCLE "
                f"COUNT — {where} — so the latency contract is NOT_MEASURED "
                f"here and must be settled before any re-authoring that "
                f"depends on it.")
    return head + (
        " A re-authoring that changes this count changes the design's "
        "observable contract, and the cheapest way to cut combinational depth "
        "— adding a pipeline stage — does exactly that. This gate does NOT "
        "establish whether the spec permits it: that is a Phase-1 question "
        "about the L-documents named above, and it is NOT_MEASURED here. "
        "Establish it there before moving the count; a rewrite that keeps the "
        "count needs no such permission.")


def die_sizing_basis(project: Path) -> Optional[Dict[str, object]]:
    """This run's EM strap-sizing basis, or None when it cannot be read.

    None is returned for every uncertainty -- absent file, unreadable JSON, no
    `sizing_basis` key (a record written before the field existed), or a basis
    this version does not recognise. None means "no caveat is added", which
    leaves the verdict exactly as it was; it never means "the die was measured".
    """
    path = project / _EM_SIZING_REL
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    basis = doc.get("sizing_basis")
    if basis not in (_EM_BOUND_BASIS, _EM_MEASURED_BASIS):
        return None
    return {
        "sizing_basis": basis,
        "from_unmeasured_bound": basis == _EM_BOUND_BASIS,
        "bound_over_measured_x": doc.get("bound_over_measured_x"),
        "i_total_A": doc.get("i_total_A"),
        "max_segment_current_A": doc.get("max_segment_current_A"),
        "record": _EM_SIZING_REL,
    }


def _die_sizing_sentence(info: Dict[str, object]) -> str:
    """The caveat, naming the record so a reader can check it."""
    return (
        " THE DIE THIS WAS MEASURED ON WAS SIZED FROM AN UNMEASURED EM BOUND: "
        f"{info['record']} says sizing_basis={info['sizing_basis']!r}, the "
        "I_total conservation fallback used when no per-segment measurement "
        "exists. That bound is generous by construction, the straps it demands "
        "inflate the die, and a lower core utilisation lengthens the clock tree "
        "the insertion delay on this path comes from. So this shortfall is NOT "
        "established as the design's floor at its DECLARED period — re-running "
        "the project so the EM measurement supersedes the bound (owner ruling "
        "2026-09-02) changes the die this path was placed on. Nothing here is "
        "relaxed or re-declared: the shortfall stands, and its BASIS is now "
        "stated with it.")


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
        "latency_contract": None,
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
    # DISCLOSURE, NOT A VERDICT. Attached to whichever verdict follows, and only
    # when this run actually read a violating path -- a clean design has no
    # shortfall to qualify, so it must read exactly as it did before.
    sizing = die_sizing_basis(project) if rep["paths"] else None
    rep["die_sizing_basis"] = sizing
    # #2081's fourth field. Read on the same condition as the sizing basis — a
    # clean design has no re-authoring request to qualify — and attached BELOW
    # to the FAIL branch only, because that is the branch that IS the request.
    latency = latency_contract(project) if rep["paths"] else None
    rep["latency_contract"] = latency
    caveat = (_die_sizing_sentence(sizing)
              if sizing and sizing["from_unmeasured_bound"] else "")
    if not arch:
        n_viol = len(rep["paths"])
        rep["verdict"] = "PASS"
        rep["reason"] = (
            f"no post-route sign-off path is PROVEN architectural "
            f"({n_viol} violating path(s) read). This is one-sided: it does "
            f"not certify that any remaining violation is physical."
            + caveat)
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
        f"re-declaring the period, dropping the corner or moving the target."
        + caveat + (_latency_sentence(latency) if latency else ""))
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
