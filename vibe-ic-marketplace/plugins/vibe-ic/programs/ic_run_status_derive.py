#!/usr/bin/env python3
"""ic_run_status_derive.py — where does each IC actually stand, DERIVED from the
run's own published artefacts.

WHY THIS EXISTS
===============
"Which of our ICs pass, and where does each one stop?" was answered by reading
lane directories across five hosts, because the answer lived only in run
artefacts scattered wherever a lane happened to run. The one register that
claimed to hold it -- a hand-fed marker directory an acceptance script counted
-- had gone stale, which is what a hand-maintained register guarding something
that changes automatically always does. The sibling program
``benchmark_evidence_index`` records the same lesson for the published corpus:
the one index this repo had was already the file nobody could trust.

So this program REMEMBERS NOTHING. Every field on every row is re-read from the
run root named on the command line, on every invocation. There is no marker
file, no cached status, no hand-maintained list of which IC is where, and no
default for a value that could not be read.

WHAT IT PRINTS
==============
One line per IC: the run's OWN verdict, ``halted_at``, the per-phase verdicts,
and -- for a run that reached sign-off -- DRC count and rule-name set, LVS,
STA per corner, LEC, antenna and IR-drop, each with the artefact it came from.

THE FOUR RULES THAT MAKE IT WORTH HAVING
========================================
1. IT REPORTS WHAT THE RUN PUBLISHED, NOT WHAT A LANE WROTE IN PROSE.
   A run's machine verdict lives in ``reports/orchestrator/vibe_ic_one_shot.json``.
   A run also usually carries prose -- ``RESULT.md``, ``AGENT_REPORT.md``,
   ``reports/final_summary.md`` -- which states a verdict in words. When the two
   disagree THE RUN WINS and the disagreement is PRINTED, never silently
   resolved: five published cells once asserted "PRODUCTION-READY" in the very
   document the publish contract designates while their own audit read FAIL.

2. EVERY FIGURE CARRIES THE IMAGE THE RUN USED.
   Numbers from two toolchains are not comparable, and forty patch releases have
   separated two pins inside one campaign. So each row carries the image digest
   the run recorded and its relation to the CURRENT pin -- read here from
   ``_eda_pin`` and never restated as a literal. A run that recorded no image is
   ``UNRECORDED``: not "on pin".
   A recorded LOCAL IMAGE ID is ``NOT_COMPARABLE``, not a mismatch --
   ``_eda_pin`` is explicit that an Id is content-addressed by storage driver
   and is an argument, never an identity, so it can neither confirm nor deny the
   pin. Saying "off pin" from an Id would be inventing a measurement.

3. IT NEVER SAYS PASS ON ITS OWN AUTHORITY.
   The ``verdict`` column is copied verbatim from the run's artefact. This
   program has no rule that turns evidence into a pass; the one verdict it
   issues of its own is ``NO_VERDICT`` -- "this run states none".

4. IT CAN SAY "NO RUN EXISTS FOR THIS IC ON THIS TREE".
   ``--ic NAME`` declares the population. A declared IC with no run under the
   search roots is ``NO_RUN``, printed as a row of its own -- the state one IC
   sat in for a day before anyone noticed, because absence had no row.

AND THE FIFTH, WHICH IS THE SAME RULE POINTED AT MYSELF
=======================================================
"I could not read it" is never "I read it and it was fine". A run root that
cannot be read is ``NOT_MEASURED`` with the reason named. No field is ever
defaulted, and no absence is ever a pass.

IC IDENTITY IS DERIVED TOO
==========================
An IC's name is read from the run's own ``phase1/generated_docs/L1_DATASHEET.json``
(``ic_name``). A directory basename is NOT an identity -- runs are staged, copied
and renamed -- so when no artefact names the IC the row says ``ic_name`` is
NOT_MEASURED and carries the path only as a ``label``, marked ``path``.

EXIT CODES
==========
  0  every declared IC RESOLVED (a run was found and its own verdict read).
     A run whose verdict is FAIL is a SUCCESSFUL measurement and exits 0 --
     this program reports status, it does not grade it.
  1  a declared IC has NO_RUN, or a run's machine verdict DISAGREES with its own
     prose. Both are states of the campaign, measured.
  2  UNDETERMINED: a run root or search root could not be read. Nothing is
     claimed about it. Precedence: 2 beats 1 beats 0.

chip-AGNOSTIC: no IC, PDK, foundry, vendor or SKU literal appears in this file.
The population is whatever ``--ic`` and ``--root`` name at the call site.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import _eda_pin
import verdict as _T
import _path_layout as _pl

NOT_MEASURED = "NOT_MEASURED"
NO_RUN = "NO_RUN"
NO_VERDICT = "NO_VERDICT"

#: The one artefact that states a whole-run verdict and where the run stopped.
ORCHESTRATOR = "reports/orchestrator/vibe_ic_one_shot.json"
#: Fallbacks, in order. Each is a RUN artefact; none is a lane's prose.
ORCHESTRATOR_FALLBACKS = (
    "reports/orchestrator/phase3_one_shot.json",
    "reports/orchestrator/phase2_one_shot.json",
)
#: THE SCOPE OF A VERDICT IS PART OF THE VERDICT. A phase-2 runner's
#: PASS_WITH_WAIVERS is a statement about phase 2, and printing it in the same
#: column as a whole-run verdict is how "spm PASS" gets read off a run that
#: never entered phase 3. MEASURED on this fleet: the newest dated run of one IC
#: is exactly that shape. So the scope is carried beside every verdict.
VERDICT_SCOPE = {
    ORCHESTRATOR: "whole-run",
    ORCHESTRATOR_FALLBACKS[0]: "phase3-only",
    ORCHESTRATOR_FALLBACKS[1]: "phase2-only",
}
AUDIT = "reports/audit/phase23_completion_audit.json"

#: Prose a lane or a publish contract writes. Read ONLY to be contradicted.
PROSE = ("RESULT.md", "AGENT_REPORT.md", "reports/final_summary.md")

#: Sign-off artefacts: (column, path, extractor-key). Order is the print order.
SIGNOFF_JSON = {
    "drc": "reports/phase3/drc_signoff.json",
    "lvs": "reports/phase3/lvs_verdict.json",
    "sta": "reports/phase3/sta/post_route_signoff_corner.json",
    "lec": "reports/phase3/lec_post_layout.json",
    "antenna": "reports/phase3/antenna.json",
    "ir_drop": "reports/phase3/ir_drop.json",
}

#: THE DECLARED FLOW, read at run time. Never a list in this file: an order
#: spelled here would go stale the moment the flow moved, which is the exact
#: failure this program exists to remove.
FLOW_YAML = "flow/phase1_phase2_phase3.yaml"

#: A run is a run when it published one of these. Nothing else makes a directory
#: a run root -- a name never does.
RUN_MARKERS = (ORCHESTRATOR,) + ORCHESTRATOR_FALLBACKS + (AUDIT,)

_VERDICT_WORD = re.compile(
    r"\b(PASS_WITH_WAIVERS|PRODUCTION-READY|PASS|FAIL|BLOCKED|INCOMPLETE)\b")
#: A DOCUMENT'S OVERALL CLAIM, and nothing else.
#:
#: The first verdict word in a document is NOT its claim. Measured over 3053 run
#: roots on one host: reading the first word charged 429 documents with a
#: disagreement, and the first three inspected were per-phase table cells
#: ("| Phase 1 | PASS | 14/14 L-docs |") in documents making no overall claim at
#: all. A gate that reports a defect it did not measure is the same false
#: certificate as one that reports a pass it did not measure, pointed the other
#: way. So only a line that DECLARES the document's verdict is read as a claim,
#: and a document that declares none is NOT_STATED -- never a disagreement.
#:
#: The declaring word must OPEN the line (markdown markup allowed in front of
#: it). Anchoring it only "somewhere on the line" charged another 100+ documents
#: on the strength of a quoted record -- ``image_match: true, verdict: PASS`` --
#: which is a sentence ABOUT an artefact, not a claim about the run.
_OVERALL_LINE = re.compile(
    r"^[\s#>*_`|~-]{0,8}(overall|verdict|final\s+status)\b", re.I)
#: KLayout RDB: one <category> per reported item, the rule that fired.
_RDB_ITEM_CATEGORY = re.compile(
    r"<item>.*?<category>\s*'?([^'<]*?)'?\s*</category>", re.S)


# ---------------------------------------------------------------------------
# THE DECLARED ORDER (ruling of 2026-09-08)
#
# `latest` keeps meaning NEWEST. `furthest` means "the run whose `halted_at` is
# furthest along the DECLARED flow order" -- and ordering by a declared sequence
# is READING, not ranking, which is the whole reason this is allowed to exist.
#
# WHERE THE ORDER COMES FROM, AND THE GAP MEASURED WHILE WIRING IT
# ================================================================
# The canonical flow file declares an ORDERED `stages:` list. It does NOT
# declare the words `halted_at` actually carries. Measured over 3053 run roots
# on one host, `halted_at` is one of `phase1` (15), `phase2` (800), `phase3`
# (312), or absent (1926); the flow's stage ids are `stage_phase1`, `stage1`,
# `stage2`, `stage_analog`, `stage3`, `stage_mixed_signal`, `stage4`,
# `stage5_manufacturing`. Only `phase1` resolves against a stage. Reading
# `phase3` as the stage `stage3` would be an invention -- those are stage
# NUMBERS, not phase names, and `stage3` is followed by two further stages.
#
# So the order is resolved by a LADDER of declared sources, each read at run
# time, and the row says WHICH ONE answered:
#
#   1. the flow file's `stages:` order, for a token a stage declares by id or
#      by name;
#   2. the runs' OWN ordered `phases[]` arrays -- a declaration the runner
#      publishes in every run record -- CROSS-CHECKED across the whole
#      population. Two runs declaring contradictory orders make the token
#      NOT_COMPARABLE; they do not vote.
#
# A token neither source declares is NOT_COMPARABLE and SAYS SO. It never
# sorts last quietly, because "I could not place this run" and "this run got
# nowhere" are the two things this program exists to keep apart.


def flow_stage_order(plugin_root: Path) -> Tuple[List[str], str]:
    """The declared stage sequence, or ([], reason)."""
    path = plugin_root / FLOW_YAML
    try:
        import yaml  # noqa: PLC0415 -- optional; absence is a REASON, not a default
    except ImportError:
        return [], "PyYAML is not importable, so the flow file could not be read"
    text, reason = _read_text(path)
    if text is None:
        return [], f"{FLOW_YAML}: {reason}"
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:                                # noqa: BLE001
        return [], f"{FLOW_YAML}: unparsable YAML: {exc.__class__.__name__}"
    stages = (doc or {}).get("stages")
    if not isinstance(stages, list):
        return [], f"{FLOW_YAML} declares no stages list"
    out = []
    for st in stages:
        if isinstance(st, dict) and st.get("id"):
            out.append((str(st["id"]), str(st.get("name") or "")))
    return out, ""


def _token_matches_stage(token: str, stage_id: str, stage_name: str) -> bool:
    """Does the flow DECLARE a stage for this `halted_at` token?

    Deliberately strict: the token must appear as a whole word in the stage's
    declared id or name once both are reduced to alphanumerics. `phase1` finds
    `stage_phase1`; `phase3` finds nothing, and that is the correct answer --
    matching it to `stage3` would be reading a stage NUMBER as a phase name.
    """
    t = re.sub(r"[^a-z0-9]", "", token.lower())
    if not t:
        return False
    sid = re.sub(r"[^a-z0-9]", "", stage_id.lower())
    sname = re.sub(r"[^a-z0-9]", "", stage_name.lower())
    return t in (sid, sname) or sid.endswith(t) or sname.startswith(t)


def declared_phase_order(rows: List[Dict[str, Any]], plugin_root: Path) -> Dict[str, Any]:
    """{token: rank} plus the source that answered and any contradiction found."""
    stages, stage_reason = flow_stage_order(plugin_root)

    # (2) every run's own declared phases[] sequence, cross-checked.
    seqs = set()
    for r in rows:
        if r.get("status") != "RESOLVED":
            continue
        names = tuple(str(p.get("name")) for p in (r.get("phases") or []) if p.get("name"))
        if names:
            seqs.add(names)
    merged: List[str] = []
    contradiction = None
    for seq in sorted(seqs, key=len, reverse=True):
        for i, name in enumerate(seq):
            if name in merged:
                # A shorter sequence must be a SUBSEQUENCE of what we already
                # have, in the same relative order. Anything else is two runs
                # declaring different flows, and neither gets to win.
                if merged.index(name) < (merged.index(seq[i - 1]) if i else -1):
                    contradiction = f"runs declare contradictory phase orders: {seq} vs {tuple(merged)}"
                continue
            merged.append(name)

    rank: Dict[str, Dict[str, Any]] = {}
    for token in sorted({str(r.get("halted_at")) for r in rows
                         if r.get("status") == "RESOLVED" and r.get("halted_at")}):
        placed = None
        for i, (sid, sname) in enumerate(stages):
            if _token_matches_stage(token, sid, sname):
                placed = {"rank": i, "by": f"{FLOW_YAML}:stages[{i}]={sid}"}
                break
        if placed is None and not contradiction and token in merged:
            placed = {"rank": len(stages) + merged.index(token),
                      "by": "the runs' own declared phases[] order"}
        rank[token] = placed or {
            "rank": None,
            "by": None,
            "reason": (contradiction or
                       f"neither {FLOW_YAML}'s stages nor any run's declared "
                       f"phases[] places {token!r}"),
        }
    return {"rank": rank, "stage_reason": stage_reason, "contradiction": contradiction,
            "stages_declared": [sid for sid, _ in stages]}


# ---------------------------------------------------------------------------
# reading, and refusing to guess

def _read_json(path: Path) -> Tuple[Optional[Any], str]:
    """(document, reason). `reason` is "" on success and names the failure
    otherwise. An unreadable file NEVER returns an empty document."""
    try:
        raw = path.read_text(errors="replace")
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"unreadable: {exc.__class__.__name__}"
    try:
        return json.loads(raw), ""
    except (ValueError, TypeError) as exc:
        return None, f"unparsable JSON: {exc.__class__.__name__}"


def _read_text(path: Path) -> Tuple[Optional[str], str]:
    try:
        return path.read_text(errors="replace"), ""
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"unreadable: {exc.__class__.__name__}"


def _nm(reason: str) -> Dict[str, Any]:
    return {"status": NOT_MEASURED, "reason": reason}


# ---------------------------------------------------------------------------
# identity

def ic_name_of(root: Path) -> Dict[str, Any]:
    """The IC's name AS THE RUN STATES IT, or NOT_MEASURED with the reason.

    The directory basename is offered as `label` with source `path`, and never
    as `name`: a staged, copied or renamed run directory would otherwise change
    an IC's identity without a single artefact changing.
    """
    ds = _pl.generated_docs_dir(root) / "L1_DATASHEET.json"
    doc, reason = _read_json(ds)
    if doc is None:
        return {"name": None, "source": None, "label": root.name,
                "label_source": "path",
                "reason": f"{ds.relative_to(root) if ds.is_relative_to(root) else ds}: {reason}"}
    name = doc.get("ic_name") if isinstance(doc, dict) else None
    if not isinstance(name, str) or not name.strip():
        return {"name": None, "source": None, "label": root.name,
                "label_source": "path", "reason": "L1_DATASHEET.json states no ic_name"}
    return {"name": name.strip(), "source": "phase1/generated_docs/L1_DATASHEET.json:ic_name",
            "label": name.strip(), "label_source": "artefact", "reason": None}


# ---------------------------------------------------------------------------
# the image the run used

def image_of(root: Path, pin: Optional[str]) -> Dict[str, Any]:
    """What image this run recorded, and how it stands to the CURRENT pin.

    `pin` is passed in (never re-spelled here) so that the pin has exactly one
    home, `_eda_pin.IMAGE_DIGEST`.
    """
    doc, reason = _read_json(root / "reports/container_image.json")
    if doc is None or not isinstance(doc, dict):
        return {"digest": None, "kind": None, "pin_state": "UNRECORDED",
                "reason": f"reports/container_image.json: {reason or 'not an object'}"}
    ref = str(doc.get("image_ref") or "")
    digest = _eda_pin.reference_digest(ref)
    if digest and pin is None:
        # THE CURRENT PIN COULD NOT BE READ HERE (no docker, or nothing held):
        # the run's own record is still reported, and its relation to a pin
        # nobody could name is NOT_COMPARABLE -- never "on pin", never "off".
        return {"digest": digest, "kind": "repo_digest", "ref": ref,
                "pin_state": "NOT_COMPARABLE",
                "reason": "the current pin could not be resolved on this host"}
    if digest:
        return {"digest": digest, "kind": "repo_digest", "ref": ref,
                "pin_state": "ON_PIN" if digest == pin else "OFF_PIN", "reason": None}
    iid = str(doc.get("image_id") or "")
    if _eda_pin.is_bare_image_id(iid):
        # An Id names bytes as THIS host's storage driver stored them. It cannot
        # be compared with a repository digest in either direction.
        return {"digest": iid, "kind": "image_id", "ref": ref or None,
                "pin_state": "NOT_COMPARABLE",
                "reason": "run recorded a local image Id, not a repository digest"}
    return {"digest": None, "kind": None, "ref": ref or None, "pin_state": "UNRECORDED",
            "reason": "reports/container_image.json names neither a repository digest nor an Id"}


# ---------------------------------------------------------------------------
# the run's own verdict

def run_verdict_of(root: Path) -> Dict[str, Any]:
    tried: List[str] = []
    for rel in (ORCHESTRATOR,) + ORCHESTRATOR_FALLBACKS:
        doc, reason = _read_json(root / rel)
        tried.append(f"{rel}: {reason or 'read'}")
        if not isinstance(doc, dict):
            continue
        verdict = doc.get("verdict")
        phases = []
        for st in (doc.get("phases") or []):
            if isinstance(st, dict):
                phases.append({"name": st.get("name"), "verdict": st.get("verdict"),
                               "rc": st.get("rc")})
        return {
            "verdict": verdict if isinstance(verdict, str) and verdict else NO_VERDICT,
            "halted_at": doc.get("halted_at", None),
            "phases": phases,
            "source": rel,
            "scope": VERDICT_SCOPE.get(rel, NOT_MEASURED),
            "tried": tried,
        }
    # No orchestrator record. The completion audit states a verdict too, and it
    # is a different question (step artefacts, not phase execution) -- so it is
    # reported AS the audit, never relabelled as the run's verdict.
    doc, reason = _read_json(root / AUDIT)
    tried.append(f"{AUDIT}: {reason or 'read'}")
    if isinstance(doc, dict) and isinstance(doc.get("verdict"), str):
        return {"verdict": NO_VERDICT, "halted_at": None, "phases": [],
                "source": None, "tried": tried,
                "audit_verdict": doc.get("verdict"),
                "note": "no orchestrator record; the completion audit's verdict is "
                        "reported separately and is not the run's verdict"}
    return {"verdict": NO_VERDICT, "halted_at": None, "phases": [], "source": None,
            "tried": tried}


def audit_of(root: Path) -> Dict[str, Any]:
    doc, reason = _read_json(root / AUDIT)
    if not isinstance(doc, dict):
        return _nm(f"{AUDIT}: {reason or 'not an object'}")
    return {"status": "READ", "verdict": doc.get("verdict"),
            "step_counts": doc.get("step_counts"), "run_at": doc.get("run_at"),
            "source": AUDIT}


# ---------------------------------------------------------------------------
# prose, read only to be contradicted

#: A markdown table ROW. Ruling of 2026-09-08: a table cell is a RENDERING of a
#: verdict, never the verdict. The verdict lives in the artefact the renderer
#: read, so a cell is recorded and then RESOLVED to that artefact -- and when the
#: artefact is not there the answer is NOT_MEASURED, never PASS.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
#: A project-relative artefact the document itself says it consumed. Derived from
#: the document's own text: no map from document to source lives in this file,
#: because such a map is the hand-maintained register all over again.
_CITED_ARTEFACT = re.compile(r"(reports/[A-Za-z0-9_./-]+\.json)")


def _resolve_backing(root: Path, text: str) -> Dict[str, Any]:
    """The artefacts THIS DOCUMENT SAYS it was generated from, read back.

    A generated summary names its source in its own bytes -- measured on a real
    run: "...as `reports/audit/phase23_completion_audit.json[step_counts]`. This
    table consumes that JSON object directly". That citation is derived
    evidence; a document-to-source table inside this program would not be.
    """
    cited = []
    for m in dict.fromkeys(_CITED_ARTEFACT.findall(text)):
        doc, reason = _read_json(root / m)
        if isinstance(doc, dict):
            cited.append({"path": m, "status": "READ", "verdict": doc.get("verdict")})
        else:
            cited.append({"path": m, "status": NOT_MEASURED,
                          "reason": f"{m}: {reason or 'not an object'}"})
    if not cited:
        return {"status": NOT_MEASURED,
                "reason": "the document cites no artefact it was generated from"}
    read = [c for c in cited if c["status"] == "READ"]
    return {"status": "READ" if read else NOT_MEASURED, "artefacts": cited,
            "reason": None if read else "every artefact this document cites is unreadable"}


def prose_claims(root: Path) -> List[Dict[str, Any]]:
    out = []
    for rel in PROSE:
        text, reason = _read_text(root / rel)
        if text is None:
            continue
        seen = sorted(set(_VERDICT_WORD.findall(text)))
        claim, line = None, None
        for raw in text.splitlines():
            if not _OVERALL_LINE.match(raw):
                continue
            words = _VERDICT_WORD.findall(raw)
            if words:
                claim, line = words[0], raw.strip()[:200]
                break
        # Table cells are recorded and never credited. Both are printed, so a
        # reader can see the rendering AND what stands behind it.
        cells = sorted({w for raw in text.splitlines() if _TABLE_ROW.match(raw)
                        for w in _VERDICT_WORD.findall(raw)})
        rec = {"file": rel, "claim": claim, "claim_line": line,
               "claims_seen": seen, "table_cells": cells,
               "claim_reason": None if claim else
               "no line in this document declares an overall verdict"}
        if cells:
            rec["table_cells_kind"] = "rendering, not a claim (ruling 2026-09-08)"
            rec["backing"] = _resolve_backing(root, text)
        out.append(rec)
    return out


def disagreements(machine: str, claims: List[Dict[str, Any]]) -> List[str]:
    """Where prose says a done-claim the machine verdict does not support.

    Comparison is by CLASS, not by string: `PASS_WITH_WAIVERS` in prose against
    `PASS_WITH_WAIVERS` in the artefact is agreement; a prose done-claim against
    a machine non-green is the disagreement this rule exists for.
    """
    out = []
    # R-0915-85: the machine word is one of the five and goes through `parse`;
    # the PROSE claim is a sentence somebody wrote and goes through
    # `as_verdict_or_none`, which answers "is that text one of the five" without
    # refusing when it is not. The old `normalize` blurred the two.
    _m = _T.as_verdict_or_none(machine)
    machine_is_done = bool(_m) and _T.is_done_claim(_m) and machine != NO_VERDICT
    for c in claims:
        claim = c.get("claim")
        if not claim:
            continue
        _c = _T.as_verdict_or_none(claim)
        prose_is_done = (claim in ("PRODUCTION-READY",)
                         or (bool(_c) and _T.is_done_claim(_c)))
        if prose_is_done and not machine_is_done:
            out.append(f"{c['file']} states {claim!r} while the run states {machine!r}")
    return out


# ---------------------------------------------------------------------------
# sign-off

def _drc(root: Path) -> Dict[str, Any]:
    rel = SIGNOFF_JSON["drc"]
    doc, reason = _read_json(root / rel)
    if not isinstance(doc, dict):
        return _nm(f"{rel}: {reason or 'not an object'}")
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    count = summary.get("real_violation_total")
    rec: Dict[str, Any] = {"status": "READ", "source": rel,
                           "count": count if isinstance(count, int) else None}
    if rec["count"] is None:
        rec["count_reason"] = f"{rel} states no summary.real_violation_total"
    # Rule NAMES come from the report the audit itself names -- never guessed
    # from a filename. Only the KLayout RDB dialect states them per item.
    scoped = [p for p in (summary.get("scoped_under") or []) if isinstance(p, str)]
    rules: List[str] = []
    unread: List[str] = []
    for p in scoped:
        text, r = _read_text(root / p)
        if text is None:
            unread.append(f"{p}: {r}")
            continue
        if "<report-database" not in text:
            unread.append(f"{p}: not a KLayout report-database; rule names not stated")
            continue
        rules.extend(m.strip() for m in _RDB_ITEM_CATEGORY.findall(text) if m.strip())
    if scoped:
        rec["rules"] = sorted(set(rules))
        rec["rule_source"] = scoped
        if unread:
            rec["rules_not_measured"] = unread
        if not rules and not unread:
            rec["rules"] = []
    else:
        rec["rules"] = None
        rec["rules_not_measured"] = [f"{rel} names no report under summary.scoped_under"]
    return rec


def _simple(root: Path, rel: str, fields: Tuple[str, ...]) -> Dict[str, Any]:
    doc, reason = _read_json(root / rel)
    if not isinstance(doc, dict):
        return _nm(f"{rel}: {reason or 'not an object'}")
    rec: Dict[str, Any] = {"status": "READ", "source": rel}
    for f in fields:
        rec[f] = doc.get(f, None)
    # THE VERDICT LADDER. These artefacts are written by different programs and
    # spell the same fact `verdict`, `status` or `result`. The ladder is fixed
    # and the key that answered is RECORDED, so a reader can see which word the
    # artefact actually used -- and an artefact that uses none says NO_VERDICT
    # rather than inheriting a neighbour's.
    for key in ("verdict", "status", "result"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            rec["verdict"] = val.strip()
            rec["verdict_key"] = key
            break
    else:
        rec["verdict"] = NO_VERDICT
        rec["verdict_key"] = None
    return rec


def signoff_of(root: Path) -> Dict[str, Any]:
    return {
        "drc": _drc(root),
        "lvs": _simple(root, SIGNOFF_JSON["lvs"], ("verdict", "result", "finding")),
        "sta": _simple(root, SIGNOFF_JSON["sta"],
                       ("verdict", "corners_available", "setup_worst_slack_ns",
                        "hold_worst_slack_ns", "governing_worst_slack_ns")),
        "lec": _simple(root, SIGNOFF_JSON["lec"],
                       ("verdict", "equivalent", "proven_points", "unproven_points")),
        "antenna": _simple(root, SIGNOFF_JSON["antenna"],
                           ("verdict", "net_violations", "pin_violations", "clean")),
        "ir_drop": _simple(root, SIGNOFF_JSON["ir_drop"],
                           ("verdict", "worst_ir_pct_vdd", "budget_pct_vdd",
                            "unmeasured_reason")),
    }


# ---------------------------------------------------------------------------
# a row

def derive(root: Path, pin: Optional[str], host: str = "") -> Dict[str, Any]:
    root = Path(root)
    if not root.exists():
        return {"status": NOT_MEASURED, "run_root": str(root), "host": host,
                "reason": "run root does not exist"}
    if not root.is_dir():
        return {"status": NOT_MEASURED, "run_root": str(root), "host": host,
                "reason": "run root is not a directory"}
    try:
        next(root.iterdir(), None)
    except OSError as exc:
        return {"status": NOT_MEASURED, "run_root": str(root), "host": host,
                "reason": f"run root unreadable: {exc.__class__.__name__}"}
    if not any((root / m).exists() for m in RUN_MARKERS):
        return {"status": NOT_MEASURED, "run_root": str(root), "host": host,
                "reason": "no run artefact under this root ("
                          + ", ".join(RUN_MARKERS) + ")"}

    ident = ic_name_of(root)
    rv = run_verdict_of(root)
    claims = prose_claims(root)
    row = {
        "status": "RESOLVED",
        "run_root": str(root),
        "host": host,
        "ic": ident,
        "verdict": rv["verdict"],
        "verdict_source": rv.get("source"),
        "verdict_scope": rv.get("scope"),
        "halted_at": rv.get("halted_at"),
        "phases": rv.get("phases"),
        "verdict_tried": rv.get("tried"),
        "audit": audit_of(root),
        "image": image_of(root, pin),
        "pin": pin,
        "prose": claims,
        "disagreements": disagreements(rv["verdict"], claims),
        "signoff": signoff_of(root),
    }
    # WHEN the run says it ran. The completion audit is the only artefact in
    # this set that states a time, and a filesystem mtime is not a substitute:
    # copying a run tree rewrites every mtime and rewrites no artefact. A run
    # that states no time is UNDATED, and stays UNDATED.
    row["run_at"] = row["audit"].get("run_at") if row["audit"].get("status") == "READ" else None
    for k in ("audit_verdict", "note"):
        if k in rv:
            row[k] = rv[k]
    return row


def discover(search_roots: List[Path], max_depth: int) -> Tuple[List[Path], List[str]]:
    """Every run root at or under `search_roots`. A directory is a run root when
    it PUBLISHED a run artefact; the walk does not descend into one."""
    found: List[Path] = []
    unreadable: List[str] = []

    def walk(d: Path, depth: int) -> None:
        if any((d / m).exists() for m in RUN_MARKERS):
            found.append(d)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError as exc:
            unreadable.append(f"{d}: {exc.__class__.__name__}")
            return
        for p in entries:
            if p.name in (".git", "__pycache__"):
                continue
            walk(p, depth + 1)

    for r in search_roots:
        if not r.exists():
            unreadable.append(f"{r}: does not exist")
            continue
        walk(r, 0)
    return found, unreadable


# ---------------------------------------------------------------------------
# printing

def _sig_cell(name: str, rec: Dict[str, Any]) -> str:
    if rec.get("status") == NOT_MEASURED:
        return f"{name}={NOT_MEASURED}"
    if name == "drc":
        n = rec.get("count")
        rules = rec.get("rules")
        rr = ("rules=" + (",".join(rules) if rules else "none")) if rules is not None \
            else "rules=" + NOT_MEASURED
        return f"drc={n if n is not None else NOT_MEASURED} {rr}"
    if name == "sta":
        return (f"sta={rec.get('verdict')} corners={rec.get('corners_available')} "
                f"setup={rec.get('setup_worst_slack_ns')} hold={rec.get('hold_worst_slack_ns')}")
    if name == "antenna":
        return (f"antenna={rec.get('verdict')} "
                f"nets={rec.get('net_violations')} pins={rec.get('pin_violations')}")
    if name == "ir_drop":
        return f"ir_drop={rec.get('verdict')} pct_vdd={rec.get('worst_ir_pct_vdd')}"
    return f"{name}={rec.get('verdict')}"


def render(rows: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for row in rows:
        if row["status"] == NO_RUN:
            out.append(f"{row['declared_ic']:<24} {NO_RUN:<20} "
                       f"reason={row['reason']}")
            continue
        if row["status"] == NOT_MEASURED:
            label = row.get("declared_ic") or Path(row["run_root"]).name
            out.append(f"{label:<24} {NOT_MEASURED:<20} "
                       f"root={row['run_root']} reason={row['reason']}")
            continue
        ic = row["ic"]
        label = ic["label"] + ("" if ic["label_source"] == "artefact" else "(path)")
        phases = " ".join(f"{p['name']}={p['verdict']}" for p in (row["phases"] or [])) \
            or "phases=" + NOT_MEASURED
        img = row["image"]
        d = img.get("digest")
        img_s = f"image={img['pin_state']}" + (f"[{(d or '')[:19]}]" if d else "")
        sig = " ".join(_sig_cell(k, row["signoff"][k]) for k in
                       ("drc", "lvs", "sta", "lec", "antenna", "ir_drop"))
        out.append(
            f"{label:<24} "
            f"{row['verdict'] + '/' + str(row.get('verdict_scope')):<32} "
            f"halted_at={row['halted_at']}  "
            f"{phases}  {img_s}  {sig}  root={row['run_root']}")
        for d_ in row["disagreements"]:
            out.append(f"{'':<24} DISAGREEMENT: {d_}  (the run wins)")
        if row["audit"].get("status") == "READ":
            out.append(f"{'':<24} audit={row['audit'].get('verdict')} "
                       f"steps={row['audit'].get('step_counts')}")
        else:
            out.append(f"{'':<24} audit={NOT_MEASURED} ({row['audit'].get('reason')})")
    return "\n".join(out)


def _select_rows(rows: List[Dict[str, Any]], order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """TWO labelled rows per stated ic_name (ruling of 2026-09-08).

        latest    the newest run, by the `run_at` THE RUN STATES
        furthest  the run whose `halted_at` is furthest along the DECLARED
                  flow order, ties broken by newest AND SAID

    When one run is both, ONE row is emitted carrying `latest+furthest` -- "the
    newest run is also the furthest" is information a reader wants, and two
    identical rows would hide it.

    Refusals, unchanged in spirit from the version that only knew `latest`:
      * a run stating no ic_name is never folded into another IC;
      * an UNDATED run never displaces a dated one, and an all-undated group
        keeps every row with none called the latest;
      * a run whose `halted_at` the declared order does not place is
        NOT_COMPARABLE for `furthest` -- it never sorts last quietly.
    """
    rank = order.get("rank", {})
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        name = r["ic"].get("name") if r.get("status") == "RESOLVED" else None
        if not name:
            out.append(r)
            continue
        by_name.setdefault(name, []).append(r)

    for name, group in by_name.items():
        dated = [g for g in group if isinstance(g.get("run_at"), str) and g["run_at"]]
        note = {"runs_seen": len(group), "undated": len(group) - len(dated)}

        latest: List[Dict[str, Any]] = []
        if dated:
            latest = [max(dated, key=lambda g: g["run_at"])]
        else:
            latest = list(group)
            note["latest_by"] = ("NOT_MEASURED: no run in this group states a "
                                 "run_at, so none is the latest")
        if dated:
            note["latest_by"] = "audit run_at"

        placeable, unplaceable = [], []
        for g in group:
            tok = str(g.get("halted_at")) if g.get("halted_at") else None
            entry = rank.get(tok or "", {})
            if tok and isinstance(entry.get("rank"), int):
                placeable.append((entry["rank"], g, entry["by"]))
            else:
                unplaceable.append((g, (entry.get("reason") if tok else
                                        "this run states no halted_at")))
        furthest, furthest_note = [], None
        if placeable:
            top = max(r_ for r_, _, _ in placeable)
            tied = [(g, by) for r_, g, by in placeable if r_ == top]
            if len(tied) > 1:
                dated_tied = [(g, by) for g, by in tied
                              if isinstance(g.get("run_at"), str) and g["run_at"]]
                pick = (max(dated_tied, key=lambda gb: gb[0]["run_at"])
                        if dated_tied else tied[0])
                furthest_note = (f"{len(tied)} runs tie at the furthest declared "
                                 f"position; TIE BROKEN BY " +
                                 ("newest run_at" if dated_tied else
                                  "first seen -- none of the tied runs states a run_at"))
            else:
                pick = tied[0]
            furthest = [pick[0]]
            furthest_note = (furthest_note or "") + f" (placed by {pick[1]})"
        else:
            note["furthest"] = "NOT_COMPARABLE: " + (
                unplaceable[0][1] if unplaceable else "no run could be placed")

        emitted: List[Dict[str, Any]] = []
        for role, chosen in (("latest", latest), ("furthest", furthest)):
            for g in chosen:
                same = next((e for e in emitted if e["run_root"] == g["run_root"]), None)
                if same is not None:
                    # The same run is both. ONE row -- and it must carry BOTH
                    # explanations, or the reason it is the furthest vanishes
                    # into the collapse.
                    same["role"] = "latest+furthest"
                    if role == "furthest" and furthest_note:
                        same["selected_from"]["furthest_by"] = furthest_note.strip()
                    continue
                row = dict(g)
                row["role"] = role
                row["selected_from"] = dict(note)
                if role == "furthest" and furthest_note:
                    row["selected_from"]["furthest_by"] = furthest_note.strip()
                emitted.append(row)
        out.extend(emitted)
    return out


# ---------------------------------------------------------------------------
# printing


def _sig_cell(name: str, rec: Dict[str, Any]) -> str:
    if rec.get("status") == NOT_MEASURED:
        return f"{name}={NOT_MEASURED}"
    if name == "drc":
        n = rec.get("count")
        rules = rec.get("rules")
        rr = ("rules=" + (",".join(rules) if rules else "none")) if rules is not None \
            else "rules=" + NOT_MEASURED
        return f"drc={n if n is not None else NOT_MEASURED} {rr}"
    if name == "sta":
        return (f"sta={rec.get('verdict')} corners={rec.get('corners_available')} "
                f"setup={rec.get('setup_worst_slack_ns')} hold={rec.get('hold_worst_slack_ns')}")
    if name == "antenna":
        return (f"antenna={rec.get('verdict')} "
                f"nets={rec.get('net_violations')} pins={rec.get('pin_violations')}")
    if name == "ir_drop":
        return f"ir_drop={rec.get('verdict')} pct_vdd={rec.get('worst_ir_pct_vdd')}"
    return f"{name}={rec.get('verdict')}"


def _image_cell(img: Dict[str, Any], has_signoff: bool) -> str:
    """The image goes on the FACE of every status line (ruling of 2026-09-08).

    Measured over 3053 run roots on one host: SIX were produced on the pinned
    image. A sign-off table from an off-pin run is evidence about a different
    toolchain, and a reader must not have to go looking for that -- so when such
    a row also quotes sign-off figures, the line says so in words.
    """
    d = img.get("digest") or ""
    cell = f"image={img['pin_state']}" + (f"[{d[:19]}]" if d else "")
    if has_signoff and img["pin_state"] != "ON_PIN":
        cell += "(figures NOT pin-comparable)"
    return cell


def host_population(rows, declared_hosts: List[str]) -> Dict[str, Any]:
    """WHICH MACHINES THIS ANSWER COVERS -- on the face of the report.

    A status that silently describes one machine is the same class of defect as
    a pin compared by its repository prefix: the answer is about the wrong
    object. So the hosts swept and the hosts NOT swept are both named, and a
    declared host nobody handed roots for is NOT_MEASURED with the reason --
    never assumed empty, and never quietly dropped from the denominator.
    """
    swept = sorted({r.get("host") or "" for r in rows} - {""})
    missing = [h for h in declared_hosts if h not in swept]
    return {"swept": swept, "declared": list(declared_hosts),
            "not_measured": [{"host": h, "reason": "no run root from this host was "
                              "given to this invocation"} for h in missing],
            "complete": not missing}


def render(rows: List[Dict[str, Any]], hosts: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append("HOSTS SWEPT: " + (", ".join(hosts["swept"]) or "(none named)"))
    if hosts["declared"]:
        out.append("HOSTS DECLARED: " + ", ".join(hosts["declared"]))
    for nm in hosts["not_measured"]:
        out.append(f"HOST {NOT_MEASURED}: {nm['host']} — {nm['reason']}")
    if not hosts["complete"]:
        out.append(
            "VERDICT WITHHELD: this is a per-host observation, NOT the IC status. "
            f"{len(hosts['not_measured'])} declared host(s) were not swept, and this "
            "program will not present a partial sweep as an answer about the ICs. "
            "To lift it, hand it those hosts' run roots (--host-root HOST:PATH) or "
            "their own --json output (--merge-json HOST:FILE).")
    out.append("")
    for row in rows:
        host = f"@{row.get('host')}" if row.get("host") else ""
        if row["status"] == NO_RUN:
            out.append(f"{row['declared_ic']:<24} {NO_RUN:<20} reason={row['reason']}")
            continue
        if row["status"] == NOT_MEASURED:
            label = row.get("declared_ic") or Path(row["run_root"]).name
            out.append(f"{label:<24} {NOT_MEASURED:<20} root={row['run_root']}{host} "
                       f"reason={row['reason']}")
            continue
        ic = row["ic"]
        label = ic["label"] + ("" if ic["label_source"] == "artefact" else "(path)")
        role = row.get("role")
        label = f"{label}[{role}]" if role else label
        phases = " ".join(f"{p['name']}={p['verdict']}" for p in (row["phases"] or [])) \
            or "phases=" + NOT_MEASURED
        sig = " ".join(_sig_cell(k, row["signoff"][k]) for k in
                       ("drc", "lvs", "sta", "lec", "antenna", "ir_drop"))
        has_signoff = any(row["signoff"][k].get("status") == "READ"
                          for k in ("drc", "lvs", "sta", "lec", "antenna", "ir_drop"))
        out.append(
            f"{label:<34} "
            f"{row['verdict'] + '/' + str(row.get('verdict_scope')):<32} "
            f"halted_at={row['halted_at']}  {_image_cell(row['image'], has_signoff)}  "
            f"{phases}  {sig}  root={row['run_root']}{host}")
        for d_ in row["disagreements"]:
            out.append(f"{'':<34} DISAGREEMENT: {d_}  (the run wins)")
        for pr in row.get("prose", []):
            b = pr.get("backing")
            if pr.get("table_cells") and b and b.get("status") == NOT_MEASURED:
                out.append(f"{'':<34} {pr['file']} renders {pr['table_cells']} in a table "
                           f"and its backing artefact is {NOT_MEASURED}: {b['reason']}")
        sel = row.get("selected_from") or {}
        if sel:
            out.append(f"{'':<34} selection: " +
                       "; ".join(f"{k}={v}" for k, v in sorted(sel.items())))
        if row["audit"].get("status") == "READ":
            out.append(f"{'':<34} audit={row['audit'].get('verdict')} "
                       f"steps={row['audit'].get('step_counts')}")
        else:
            out.append(f"{'':<34} audit={NOT_MEASURED} ({row['audit'].get('reason')})")
    return "\n".join(out)


def _split_host_arg(value: str, what: str) -> Tuple[str, str]:
    host, sep, tail = str(value).partition(":")
    if not sep or not host.strip() or not tail.strip():
        raise SystemExit(f"[ic_run_status_derive] {what} must be HOST:{what.upper()} "
                         f"-- got {value!r}; a root with no host would make the answer "
                         f"about an unnamed machine")
    return host.strip(), tail.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-root", action="append", default=[],
                    help="a run root on THIS host (repeatable)")
    ap.add_argument("--root", action="append", default=[],
                    help="a directory on THIS host to search (repeatable)")
    ap.add_argument("--host-root", action="append", default=[],
                    help="HOST:PATH — a search root whose runs came from HOST "
                         "(repeatable). Use when another host's tree is readable here.")
    ap.add_argument("--merge-json", action="append", default=[],
                    help="HOST:FILE — this program's own --json output produced ON "
                         "that host, merged in with every row attributed to it. This "
                         "is how a five-host answer is assembled without this "
                         "invocation running anywhere but here.")
    ap.add_argument("--declared-host", action="append", default=[],
                    help="a host that EXISTS and must be covered (repeatable). Any "
                         "declared host with no roots is NOT_MEASURED and the IC "
                         "verdict is WITHHELD.")
    ap.add_argument("--ic", action="append", default=[],
                    help="declare an IC that MUST have a run (repeatable)")
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--per-ic", choices=("all", "select"), default="all",
                    help="all (default): every run root is a row. select: two "
                         "labelled rows per IC — latest (newest run_at) and furthest "
                         "(furthest along the DECLARED flow order), collapsed to one "
                         "row labelled latest+furthest when they are the same run.")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    if not (args.run_root or args.root or args.host_root or args.merge_json):
        ap.error("name at least one --run-root, --root, --host-root or --merge-json")

    import socket                                        # noqa: PLC0415
    this_host = socket.gethostname()

    # A STATUS REPORT NEEDS NO DOCKER. Reading the pin asks this host's docker
    # (`_eda_pin`, since v1.22.4); with none (inside the image, or a host
    # without the pinned bytes) `ImageNotResolvable` used to escape as a
    # traceback and the whole report -- every run's verdict -- was lost over
    # the one column that needs the pin. It is now `pin: null` with the
    # refusal named, and each recorded image is NOT_COMPARABLE.
    try:
        pin, pin_refusal = _eda_pin.IMAGE_DIGEST, None
    except _eda_pin.ImageNotResolvable as exc:
        pin, pin_refusal = None, str(exc)
        print(f"ic_run_status_derive: {exc}", file=_sys.stderr)
    plugin_root = Path(__file__).resolve().parent.parent

    rows: List[Dict[str, Any]] = []
    seen_roots = set()

    def add(root: Path, host: str) -> None:
        if str(root) in seen_roots:
            return
        seen_roots.add(str(root))
        rows.append(derive(root, pin, host))

    for p_ in args.run_root:
        add(Path(p_), this_host)
    discovered, unreadable = discover([Path(p_) for p_ in args.root], args.max_depth)
    for d_ in discovered:
        add(d_, this_host)
    for spec in args.host_root:
        host, path = _split_host_arg(spec, "host-root")
        found, unread = discover([Path(path)], args.max_depth)
        for d_ in found:
            add(d_, host)
        unreadable.extend(unread)
    for u in unreadable:
        rows.append({"status": NOT_MEASURED, "run_root": u.split(":")[0],
                     "host": this_host,
                     "reason": f"search root unreadable: {u}"})
    for spec in args.merge_json:
        host, path = _split_host_arg(spec, "merge-json")
        doc, reason = _read_json(Path(path))
        if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
            rows.append({"status": NOT_MEASURED, "run_root": path, "host": host,
                         "reason": f"--merge-json {path}: {reason or 'no rows[]'}"})
            continue
        for r in doc["rows"]:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            own = r.get("host")
            # THE ROW'S OWN HOST WINS, the same way a run's verdict wins over a
            # lane's prose: `host` was recorded where the run was measured, and
            # the HOST: half of this flag is only a label the caller typed. A
            # disagreement is REPORTED rather than resolved silently, because a
            # relabelled row is a status attributed to the wrong machine.
            if own and own != host:
                r["host_label_disagreement"] = (
                    f"--merge-json labelled {path} as {host!r} but the row was "
                    f"measured on {own!r}; the row's own host wins")
            r["host"] = own or host
            r["merged_from"] = path
            rows.append(r)

    order = declared_phase_order(rows, plugin_root)
    if args.per_ic == "select":
        rows = _select_rows(rows, order)

    for ic in args.ic:
        hits = [r for r in rows if r["status"] == "RESOLVED"
                and (r["ic"].get("name") == ic)]
        if not hits:
            near = [r["run_root"] for r in rows if r["status"] == "RESOLVED"
                    and r["ic"].get("name") is None]
            rows.append({
                "status": NO_RUN, "declared_ic": ic,
                "reason": "no run under the search roots states this ic_name"
                          + (f"; {len(near)} run(s) state no ic_name at all" if near else ""),
            })

    hosts = host_population(rows, args.declared_host)
    resolved = [r for r in rows if r["status"] == "RESOLVED"]
    if args.as_json:
        print(json.dumps({"pin": pin, "pin_refusal": pin_refusal, "hosts": hosts, "declared_order": order,
                          "rows": rows,
                          "counts": {"resolved": len(resolved),
                                     "no_run": sum(1 for r in rows if r["status"] == NO_RUN),
                                     "not_measured": sum(1 for r in rows
                                                         if r["status"] == NOT_MEASURED)}},
                         indent=1, sort_keys=True))
    else:
        print(render(rows, hosts))

    rc = 0
    if any(r["status"] == NO_RUN for r in rows) or \
            any(r.get("disagreements") for r in rows):
        rc = 1
    if any(r["status"] == NOT_MEASURED for r in rows) or not hosts["complete"]:
        rc = 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
