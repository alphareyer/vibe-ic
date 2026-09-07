#!/usr/bin/env python3
"""clock_target_record_agreement_check.py — ONE RUN, ONE CLOCK-TARGET ANSWER.

THE DEFECT (measured, vibe-ic#2159, `subservient`, lane rbsub6 on 8HD-9)
========================================================================
Two records of the same run's clock target named DIFFERENT technologies and
DIFFERENT provenance tiers while agreeing on the number::

    reports/phase3/clock_target_provenance.json
        pdk = gf180mcuD   tier = declared_pdk_table   period_ns = 20.0
        cite = input/docs/L9_constraints_floorplan.md:34

    phase1/generated_docs/L19_CONSTRAINTS_PDK.json  fields.clock_target
        pdk = sky130      tier = l8_declared          period_ns = 20.0

The design's own table, in the very file the first record cites, gives
``sky130_fd_sc_hd -> 10 ns`` and ``gf180mcu_* -> 20 ns``.  So "sky130 -> 20 ns"
contradicts its own source: one of the two records had a PDK name attached to a
number that does not belong to it.

That is precisely the failure #2091's provenance exists to prevent.  A reader
who opens L19 and a reader who opens the run record come away with different
beliefs about WHERE the number the backend signed off against came from — and
one of them is reading a technology the run never built against.

THE CONTRACT THIS ENFORCES
==========================
The AUTHORITATIVE record is the one that names the PDK the run ACTUALLY BUILT
AGAINST — the run's own ``clock_target_provenance.json``, written by the
Phase-3 runner from the resolved technology.  #2136 settled the same question
for the area baseline: a technology-bound number belongs to the technology the
run used, never to one named somewhere in the documents.

L19 then either records THE SAME pdk and tier as the run, or records
``NOT_STATED`` — declining to name a technology it cannot bind to the number.
What it must never do is carry another PDK's tier under this PDK's number.
`l19_constraint_token_emit._clock_target_record` is the emitter side of that
rule; this program is the enforcement side, and it refuses by NAME, printing
both records.

WHAT IS COMPARED, AND WHAT IS DELIBERATELY NOT
==============================================
Compared, when BOTH records name the field:

  * ``pdk``     — different technologies for one run is the #2159 defect.
  * ``tier``    — different provenance for one run: the two readers above.
  * ``period_ns`` — different numbers for one run, which is worse than either.

NOT compared:

  * An L19 record whose status is ``NOT_STATED``.  An explicit absence is the
    contract's other permitted answer, not a disagreement; it is reported as
    such and it PASSES.
  * An L19 record that names no pdk (``pdk`` null / ``pdk_source`` NOT_STATED)
    is not bound to the run's technology, so its TIER is not held against the
    run's.  The record is not claiming to describe the run's build.  This is
    the shape a Phase-1 emission has before Phase 3 has published anything,
    and refusing it would redden every ordinary run for the flow's own
    ordering rather than for anything about the design.

PDK NAME COMPARISON.  Names agree when they are equal ignoring case, or when
one is a prefix of the other (a family name and one of its variants, e.g. a
family vs its `...D` variant).  A prefix is treated as agreement deliberately:
the harm #2159 measured is two DIFFERENT families, and a stricter rule would
manufacture refusals out of the one naming convention the fleet already uses.
The relation used is reported in ``pdk_match`` so the judgement is visible.

ABSENT VERSUS UNREADABLE, AND WHY THEY GET DIFFERENT VERDICTS
=============================================================
A run that publishes no run record, or whose L19 carries no clock-target
record at all, has made no second claim that could contradict a first one.
There is nothing here to be wrong about, so the honest verdict is
NOT_APPLICABLE (rc 0) — the same self-reported applicability
`sta_corner_record_completeness_check` uses for a run that declares no corner.
Reporting NOT CHECKED there would block every Phase-3-only and analog run for
the shape of the project rather than for anything about the design.

A record that EXISTS and cannot be parsed is the other case entirely: its
subject is present and this gate could not read it.  That is NOT CHECKED
(rc 2) — "could not read it" is never "read it and it agreed", and absence of
a READABLE input is not a pass (vibe-ic#1140).

EXIT CODES
==========
  0  PASS           — the two records agree, or L19 records an explicit
                      absence, or L19 names no technology and so claims
                      nothing about it.
  0  NOT_APPLICABLE — one of the two records is not published by this run.
  1  FAIL           — the two records disagree; the reason names both.
  2  NOT CHECKED    — a record exists on disk and could not be read.

Chip / PDK-AGNOSTIC: no chip, vendor, PDK or library literal appears here.
Every name compared comes from the run's own records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_text as atomic_write_text  # noqa: E402
# The subjects come from the WRITERS, never re-typed here: a checker that
# spells its own subject can drift into asserting about a file nobody writes
# and pass forever (the #2126 shape).
from clock_target_provenance import PROVENANCE_REL as _PROVENANCE_REL  # noqa: E402
from l19_constraint_token_emit import (  # noqa: E402
    _CLOCK_TARGET_KEY as _CT_KEY, _L19_NAME)

#: Where L19 lives, project-relative. Mirrors
#: `l19_constraint_token_emit._generated_docs`.
L19_REL = f"phase1/generated_docs/{_L19_NAME}"


def _load(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _names_agree(a: str, b: str) -> Optional[str]:
    """Return the relation when two technology names agree, else None."""
    x, y = a.strip().casefold(), b.strip().casefold()
    if not x or not y:
        return None
    if x == y:
        return "equal"
    if x.startswith(y) or y.startswith(x):
        return "family_prefix"
    return None


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(project: Path) -> Dict[str, object]:
    rep: Dict[str, object] = {
        "program": "clock_target_record_agreement_check",
        "project": str(project),
        "verdict": "NOT_CHECKED",
        "run_record": None,
        "l19_record": None,
        "run": {},
        "l19": {},
        "pdk_match": None,
        "disagreements": [],
        "reason": "",
    }
    prov_path = project / _PROVENANCE_REL
    if not prov_path.is_file():
        rep["verdict"] = "NOT_APPLICABLE"
        rep["reason"] = (f"NOT APPLICABLE — this run publishes no "
                         f"{_PROVENANCE_REL}, so there is no run record for a "
                         "second record to contradict")
        return rep
    prov = _load(prov_path)
    if prov is None:
        rep["reason"] = (f"NOT CHECKED — {_PROVENANCE_REL} exists and could "
                         "not be read; 'could not read it' is never 'read it "
                         "and it agreed'")
        return rep
    rep["run_record"] = str(prov_path)

    l19_path = project / L19_REL
    if not l19_path.is_file():
        rep["verdict"] = "NOT_APPLICABLE"
        rep["reason"] = (f"NOT APPLICABLE — this run publishes no {L19_REL}, "
                         "so there is no second record of the clock target")
        return rep
    l19doc = _load(l19_path)
    if l19doc is None:
        rep["reason"] = (f"NOT CHECKED — {L19_REL} exists and could not be "
                         "read; 'could not read it' is never 'read it and it "
                         "agreed'")
        return rep
    fields = l19doc.get("fields")
    ct = fields.get(_CT_KEY) if isinstance(fields, dict) else None
    if not isinstance(ct, dict):
        rep["verdict"] = "NOT_APPLICABLE"
        rep["reason"] = (f"NOT APPLICABLE — {L19_REL} carries no "
                         f"'{_CT_KEY}' record, so it claims nothing about "
                         "this run's clock target")
        return rep
    rep["l19_record"] = str(l19_path)

    run = {"pdk": str(prov.get("pdk") or ""), "tier": prov.get("tier"),
           "period_ns": _num(prov.get("period_ns")),
           "cite": prov.get("cite")}
    l19 = {"status": ct.get("status"), "pdk": str(ct.get("pdk") or ""),
           "tier": ct.get("tier"), "period_ns": _num(ct.get("period_ns")),
           "pdk_source": ct.get("pdk_source"), "evidence": ct.get("evidence")}
    rep["run"], rep["l19"] = run, l19

    if str(ct.get("status")) == "NOT_STATED":
        rep["verdict"] = "PASS"
        rep["reason"] = (
            f"{L19_REL} records the clock target as an EXPLICIT absence "
            "(status NOT_STATED), which is the contract's other permitted "
            f"answer; the run's own record names pdk '{run['pdk']}' tier "
            f"'{run['tier']}' at {run['period_ns']} ns")
        return rep

    dis: List[str] = []
    if run["pdk"] and l19["pdk"]:
        rel = _names_agree(run["pdk"], l19["pdk"])
        rep["pdk_match"] = rel or "different"
        if rel is None:
            dis.append(
                f"PDK: the run built against '{run['pdk']}' "
                f"({_PROVENANCE_REL}) and {L19_REL} names '{l19['pdk']}' for "
                f"the same run — one of them does not own this number")
    elif not l19["pdk"]:
        rep["pdk_match"] = "l19_names_none"

    # A tier is only comparable when L19 has bound itself to the run's
    # technology; see the module docstring.
    bound = rep["pdk_match"] in ("equal", "family_prefix")
    if bound and run["tier"] and l19["tier"] and run["tier"] != l19["tier"]:
        dis.append(
            f"tier: the run records provenance tier '{run['tier']}'"
            + (f" citing {run['cite']}" if run.get("cite") else "")
            + f" and {L19_REL} records '{l19['tier']}'"
            + (f" citing {l19['evidence']}" if l19.get("evidence") else "")
            + " for the same run")
    if (run["period_ns"] is not None and l19["period_ns"] is not None
            and run["period_ns"] != l19["period_ns"]):
        dis.append(
            f"period: the run signs off against {run['period_ns']} ns and "
            f"{L19_REL} records {l19['period_ns']} ns for the same run")

    rep["disagreements"] = dis
    if dis:
        rep["verdict"] = "FAIL"
        rep["reason"] = ("the two records of this run's clock target disagree: "
                         + "; ".join(dis))
        return rep
    rep["verdict"] = "PASS"
    rep["reason"] = (
        f"both records name the same clock target — pdk '{run['pdk']}' "
        f"(match: {rep['pdk_match']}), tier '{run['tier']}', "
        f"{run['period_ns']} ns"
        if bound else
        f"{L19_REL} names no technology for its clock target "
        f"(pdk_source '{l19['pdk_source']}'), so it claims nothing about the "
        f"run's build; the run's own record names pdk '{run['pdk']}' tier "
        f"'{run['tier']}' at {run['period_ns']} ns")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="One run, one clock-target answer: L19 and the run's own "
                    "provenance record must not name different PDKs or "
                    "different tiers for the same clock target.")
    ap.add_argument("project")
    ap.add_argument("--json", help="write the structured report here")
    args = ap.parse_args(argv)
    rep = check(Path(args.project))
    if args.json:
        atomic_write_text(Path(args.json), json.dumps(rep, indent=2) + "\n")
    print(f"[{rep['verdict']}] clock_target_record_agreement_check — "
          f"{rep['reason']}")
    print(json.dumps(rep, indent=2))
    return {"PASS": 0, "NOT_APPLICABLE": 0, "FAIL": 1}.get(
        str(rep["verdict"]), 2)


if __name__ == "__main__":
    sys.exit(main())
