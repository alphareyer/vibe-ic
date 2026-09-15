#!/usr/bin/env python3
"""R-0915-28 (as amended) — when a PRE-LAYOUT step-10 FAIL is SUPERSEDED.

WHY THIS LIVES HERE AND NOT IN THE GATE
=======================================
The first version of this ruling put the tier inside `eda_report_audit`: a
violation whose report declared `STA_BASIS: PRE_LAYOUT_ESTIMATE` was reported at
INFO and the gate passed.  That REVERSES a landed anti-laundering contract,
`test_sta_basis_scope_residuals::test_widening_step10_does_not_buy_a_green`,
whose docstring is the specification:

    "A VIOLATED corner report inside the widened scope must FAIL step 10.
     The scope is the perfect instrument for buying a green; it may not."

It is right.  A LABEL a report writes about itself must never be able to turn a
measured negative slack into a pass, because the label is exactly what an
instrument for buying a green would forge.  So the gate is left alone -- step 10
still FAILS -- and the question is asked ONE LEVEL UP, by the completion audit,
where it can be keyed on EVIDENCE instead of on a label:

    a step-10 FAIL is SUPERSEDED only when a SEPARATE, PASSING, post-route
    multi-corner sign-off exists for the corner the pre-layout report failed on.

That artefact is not something the failing report can write about itself.  A
relabelled post-route report buys nothing, because supersession needs the OTHER
file to exist and to pass; forging the label without producing a passing
sign-off leaves the FAIL exactly where it was.

MEASURED, `subservient` x gf180mcuD (lane icsub2, run r13), one design, one SDC
(20 ns, derived from the design's own L9 table):

    pre-layout   SS 125C 4v50   worst setup -6.10 ns   TNS -371.15
    post-route   SS 125C 4v50   worst setup +0.97 ns
    sign-off     SS (multi-corner OCV)      worst setup +0.03 ns, closed

Placement, CTS, resizing and the repair passes are what close that corner, so
the pre-layout number is a FORECAST of what the physical passes must recover.
It is still reported, in full, with both slacks beside each other -- what
changes is only that it no longer blocks a run whose sign-off corner DID close.

FAIL-CLOSED IN EVERY DIRECTION
==============================
No sign-off artefact            -> not superseded.
Sign-off exists but did not close -> not superseded.
Sign-off does not cover that corner -> not superseded.
The failing report declares no basis -> not superseded.
The corner cannot be determined -> not superseded.

Chip-AGNOSTIC: every value is read from the run's own reports; no design, PDK or
vendor literal is written here.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: The run's post-route multi-corner sign-off stance. Written by the multi-
#: corner OCV step, NOT by the step-10 gate, which is the whole point: the
#: failing report cannot author its own supersession.
SIGNOFF_STANCE_REL = "reports/phase3/mcorner_ocv_stance.json"

#: A process-corner token as the reports spell it (SS / TT / FF and the
#: library-qualified spellings around them).
_CORNER_RE = re.compile(r"(?<![A-Za-z])(ss|tt|ff|sf|fs)(?![A-Za-z])", re.I)


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def corner_of(text: str) -> Optional[str]:
    """The process corner a report or a path names, upper-cased, or None.

    Deliberately conservative: exactly one distinct corner token must appear,
    because a file that names two has not told us which one failed."""
    found = {m.group(1).upper() for m in _CORNER_RE.finditer(text or "")}
    return found.pop() if len(found) == 1 else None


def _pre_layout_evidence(report: Dict[str, Any]) -> Optional[str]:
    """The file the gate itself named as the violation's evidence."""
    for f in report.get("findings") or []:
        if isinstance(f, dict) and f.get("rule") == "STA_REAL_VIOLATION_FOUND":
            return str(f.get("file") or "") or None
    return None


def _worst_setup(path: Path) -> Optional[float]:
    """The worst setup slack in a report, via the shipped extractor."""
    try:
        import sta_corner_record_completeness_check as _sta_slack
        return _sta_slack.extract_slacks(
            path.read_text(errors="replace")).get("setup_wns_ns")
    except (OSError, ImportError, AttributeError):        # pragma: no cover
        return None


def supersession(project: Path,
                 gate_report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The disclosure record when a step-10 FAIL is superseded, else None.

    `gate_report` is the JSON the failing `sta_report_check` invocation wrote.
    Every clause below must hold; any one of them failing leaves the FAIL."""
    if not isinstance(gate_report, dict) or gate_report.get("passed"):
        return None
    summary = gate_report.get("summary")
    if not isinstance(summary, dict):
        return None
    # 1. THE FAILING REPORT MUST DECLARE ITSELF PRE-LAYOUT. A report that
    #    declares nothing has not earned the question -- silence is not a basis.
    if summary.get("declared_sta_basis") != "PRE_LAYOUT":
        return None
    if not summary.get("real_violation_found"):
        return None
    # 2. WHICH CORNER FAILED. Read off the file the gate itself named.
    evidence = _pre_layout_evidence(gate_report)
    if not evidence:
        return None
    corner = corner_of(Path(evidence).name)
    if corner is None:
        return None
    # 3. THE SEPARATE, PASSING SIGN-OFF. Not authored by the failing gate.
    stance_path = project / SIGNOFF_STANCE_REL
    stance = _load(stance_path)
    if stance is None or not stance.get("timing_closed_multi_corner"):
        return None
    covered = {str(stance.get("setup_process_corner") or "").upper(),
               str(stance.get("hold_process_corner") or "").upper()}
    if corner not in covered:
        return None
    # 4. AND IT MUST BE A DIFFERENT ARTEFACT. A report cannot supersede itself.
    signoff_report = str(stance.get("report") or "")
    if signoff_report and Path(signoff_report).name == Path(evidence).name:
        return None
    setup_signoff = stance.get("setup_worst_slack_ns")
    if not isinstance(setup_signoff, (int, float)) or setup_signoff < 0:
        return None
    return {
        "corner": corner,
        "pre_layout_report": evidence,
        # The gate writes this path PROJECT-RELATIVE; read it back the same way
        # (an absolute path still resolves, because `/` on an absolute right
        # operand returns the right operand).
        "pre_layout_setup_slack_ns": _worst_setup(project / evidence),
        "signoff_report": signoff_report or SIGNOFF_STANCE_REL,
        "signoff_setup_slack_ns": float(setup_signoff),
        "signoff_hold_slack_ns": stance.get("hold_worst_slack_ns"),
    }


def disclosure(rec: Dict[str, Any]) -> str:
    """One sentence a reader can act on: both slacks, the corner, both files."""
    pre = rec.get("pre_layout_setup_slack_ns")
    pre_txt = f"{pre:+.4g} ns" if isinstance(pre, (int, float)) else "unstated"
    return (f"step 10 measured a REAL timing violation at corner "
            f"{rec['corner']} on a report whose own declared basis is "
            f"PRE_LAYOUT (worst setup {pre_txt}, {rec['pre_layout_report']}), "
            f"and the post-route multi-corner sign-off for that same corner "
            f"CLOSED at {rec['signoff_setup_slack_ns']:+.4g} ns setup "
            f"({rec['signoff_report']}). The pre-layout number is a forecast of "
            f"what placement, CTS and the repair passes had to recover, and "
            f"they did; the step-10 gate still FAILS and its finding stands "
            f"unchanged. SUPERSEDED means measured-and-answered, not waived: "
            f"remove the passing sign-off and this step blocks again")
