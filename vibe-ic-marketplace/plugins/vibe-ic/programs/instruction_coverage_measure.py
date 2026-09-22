#!/usr/bin/env python3
"""instruction_coverage_measure.py — the INSTRUMENT for the `instruction`
coverage dimension (R-0915-131).

ENFORCEMENT: producer. It publishes `totals.instruction` for
`l10_coverage_goal_classify.measure_goal`, which is what turns an
instruction-dimension coverage GOAL from NOT_MEASURED into a verdict BY THE
NUMBER. It is not itself a gate: it never returns a verdict about the design.

WHY THIS EXISTS — the measurement, not a theory.

MEASURED, subservient x gf180mcuD, FRONT DOOR, run1 on main 48bf6576b
(lane icsub5, 2026-09-22), after every declared functional VECTOR had been
given a real oracle:

    step4_functional_evidence FAIL: ... and every declared functional vector
    executed its own oracle, but 0 of 1 declared coverage goal(s) met their
    stated percentage. rv32i_40 [NOT_MEASURED] case 'rv32i_40': the stated
    scope names no coverage dimension this run measures (branch/line/toggle):
    '整套 RV32I 指令(40+ 條)單元測試'

The design's L7 verification plan states an acceptance percentage over its
INSTRUCTION SET. That is a coverage dimension like any other -- it is simply
one the flow held no instrument for, so `bind_scope` correctly refused to
invent a number and the goal stayed NOT_MEASURED, and NOT_MEASURED is not a
pass, so the Step-4 gate refused. The honest fix is not to widen the
vocabulary and point it at a number that means something else (the run's line
coverage was 50.68% against a stated 100%: binding it there would have turned
NOT_MEASURED into a FAIL about the wrong thing). The honest fix is an
instrument.

WHAT IT MEASURES. The fraction of the design's own declared operation
enumeration that the run's simulations ACTUALLY EXERCISED, as reported by the
testbench that exercised them, on the testbench's own transcript:

    VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction covered=41 total=41

WHY THAT LINE AND NOT A STATIC SCAN. Only the running simulation knows which
instructions were reached. A static scan of the firmware image counts what is
PRESENT, and an instruction sitting on a path no run ever takes is not
covered. The reference implementation of the monitor (lane icsub5, subservient)
observes the DUT's own memory pins and marks an instruction covered only when
the DUT issues a fetch of its address; its mutation arm leaves an opcode's
bytes in the image but jumps over it and the monitor drops to 40/41 and fails.

THREE THINGS KEEP IT HONEST, and each is a refusal rather than a default:
  * DECLARATION-DERIVED, NEVER A NAME PATTERN. The instrument runs only when a
    declared L10 coverage GOAL's own scope binds to the `instruction`
    dimension, asked through `l10_coverage_goal_classify.bind_scope` -- the
    same reader the gate uses. No design name, no case name (`rv32i_40` is a
    name in one corpus and means nothing here), no IC class.
  * ONLY A CASE THAT EXECUTED AND PASSED CONTRIBUTES, asked through
    `_l10_execution.case_state`, the ONE execution reader. A transcript from a
    case that failed, or that never ran, is evidence of nothing.
  * ABSENT IS NOT ZERO AND NOT A PASS. If the goal binds but no contributing
    transcript carries the line, NO total is published and the reason is
    recorded, so the goal stays NOT_MEASURED **with the reason** rather than
    becoming a green over an empty population.

A goal naming a dimension this flow still has no instrument for is untouched:
it binds to nothing and stays NOT_MEASURED by name, exactly as before.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _path_layout as _pl  # noqa: E402
import _l10_execution as _l10x  # the ONE execution reader  # noqa: E402
import l10_coverage_goal_classify as _cgc  # noqa: E402
import cpu_functional_oracle_waiver_check as _w  # the ONE row reader  # noqa: E402

#: The dimension this instrument owns. One instrument, one dimension.
DIMENSION = "instruction"

#: The receipt. A separate file from the verilator arm's, so each instrument
#: OWNS its own dimensions and neither can overwrite the other's numbers.
RECEIPT_REL = "reports/phase2/coverage/instruction_coverage.json"

#: What a testbench emits to report a functional-coverage tally. The dimension
#: is part of the line, so one convention serves any future dimension without
#: a second parser.
EMISSION_RE = re.compile(
    r"VIBEIC_FUNCTIONAL_COVERAGE\s+dimension=(?P<dim>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s+covered=(?P<covered>\d+)\s+total=(?P<total>\d+)")

#: Where a per-case L10 testbench transcript lands, relative to the project.
_TRANSCRIPT_RELS = (
    "phase2/stage1/sim_professional/l10_unit_tb/{case}/run.log",
    "phase2/stage1/sim/tb/{case}.log",
)


def _declared_rows(project: Path) -> Tuple[List[dict], List[dict]]:
    """`(vectors, goals)` over the declared L10 rows, through the readers the
    gate already uses. A second reader here would be a second answer waiting
    to disagree with the gate's."""
    gd = _pl.generated_docs_dir(project)
    rows, _process_only = _w._split_executable(_w._declared_rows(
        gd / "L10_TEST_CASES.json", ("test_cases", "cases", "vectors")))
    rows, _design_na = _w.split_design_declared_na(
        rows, _w.design_selected_options(project))
    return _cgc.partition(rows)


def _goal_rows(project: Path) -> List[dict]:
    return _declared_rows(project)[1]


def _contributing_case_ids(project: Path) -> List[str]:
    """EVERY declared L10 case, vectors AND goals.

    `cpu_functional_oracle_waiver_check._declared_l10_case_ids` deliberately
    lists only the VECTOR population, because that is the population its own
    executed-versus-declared arithmetic is over. The instrument's question is
    different: WHICH TESTBENCH REPORTED A TALLY. A goal's own testbench is the
    likeliest one to (it is the testbench written for that goal), and it is
    not in the vector list -- which is exactly the bug this reader replaces:
    iterating the vector list found no transcript for the goal's own case and
    published nothing, with no refusal to show for it."""
    out: List[str] = []
    for row in (*_declared_rows(project)[0], *_declared_rows(project)[1]):
        name = row.get("name") or row.get("id") or row.get("case")
        if name:
            out.append(str(name))
    return out


def goals_for_dimension(project: Path, dimension: str = DIMENSION
                        ) -> List[dict]:
    """The declared goals whose OWN scope binds to `dimension`.

    This is the whole trigger: the design's documents ask for this dimension,
    or the instrument does not run."""
    out = []
    for g in _goal_rows(project):
        dim, _why = _cgc.bind_scope(_cgc.coverage_scope(g)[0])
        if dim == dimension:
            out.append(g)
    return out


def _transcripts(project: Path, case_id: str) -> List[Path]:
    return [p for p in (Path(project) / rel.format(case=case_id)
                        for rel in _TRANSCRIPT_RELS) if p.is_file()]


def _emissions(project: Path, dimension: str = DIMENSION) -> Tuple[
        List[dict], List[dict]]:
    """`(contributions, refusals)` over EVERY declared L10 case.

    A contribution is a tally from a case that EXECUTED AND PASSED. Anything
    else is a refusal that names itself, so the receipt shows what was looked
    at rather than only what was counted."""
    record = _l10x.load_record(project)
    contributions: List[dict] = []
    refusals: List[dict] = []
    for case_id in _contributing_case_ids(project):
        paths = _transcripts(project, case_id)
        if not paths:
            continue
        state, why = _l10x.case_state(case_id, record)
        for p in paths:
            try:
                text = p.read_text(errors="replace")
            except OSError as exc:
                refusals.append({"case": case_id, "transcript": str(p),
                                 "why": f"unreadable: {exc!r}"})
                continue
            for m in EMISSION_RE.finditer(text):
                if m.group("dim") != dimension:
                    continue
                row = {"case": case_id, "transcript": str(p),
                       "covered": int(m.group("covered")),
                       "total": int(m.group("total"))}
                if state != _l10x.PASS:
                    row["why"] = (f"case did not execute and pass "
                                  f"({state}: {why}) — a tally from a case "
                                  f"that did not pass is evidence of nothing")
                    refusals.append(row)
                else:
                    contributions.append(row)
    return contributions, refusals


def fuse(contributions: List[dict]) -> Optional[dict]:
    """The dimension's number over the contributing tallies, or None.

    Contributions may state DIFFERENT denominators — two testbenches can
    enumerate different subsets. The largest denominator is the fullest
    statement of the enumeration, so it wins, and among the tallies that state
    it the largest covered count wins. A smaller enumeration never raises the
    percentage by shrinking the denominator."""
    if not contributions:
        return None
    total = max(c["total"] for c in contributions)
    if total <= 0:
        return None
    covered = max(c["covered"] for c in contributions if c["total"] == total)
    covered = min(covered, total)
    return {"covered": covered, "total": total,
            "pct": round(100.0 * covered / total, 2)}


def measure(project: Path, dimension: str = DIMENSION) -> dict:
    """The receipt: what was asked for, what was found, and the number."""
    project = Path(project)
    goals = goals_for_dimension(project, dimension)
    receipt: Dict[str, Any] = {
        "schema": "vibeic.instruction_coverage.v1",
        "program": "instruction_coverage_measure",
        "project": str(project),
        "dimension": dimension,
        "asked_through": "l10_coverage_goal_classify.bind_scope",
        "goals": [g.get("name") or g.get("id") for g in goals],
        "goal_count": len(goals),
    }
    if not goals:
        receipt.update(
            applicable=False,
            reason=(f"no declared L10 coverage goal's scope binds to the "
                    f"{dimension!r} dimension, so this design does not ask "
                    f"for it and nothing is measured"),
            totals={})
        return receipt

    contributions, refusals = _emissions(project, dimension)
    receipt["contributions"] = contributions
    receipt["refusals"] = refusals
    fused = fuse(contributions)
    if fused is None:
        receipt.update(
            applicable=True,
            reason=(f"{len(goals)} goal(s) bind to the {dimension!r} "
                    f"dimension but no transcript of a case that EXECUTED AND "
                    f"PASSED carries a "
                    f"'VIBEIC_FUNCTIONAL_COVERAGE dimension={dimension}' "
                    f"line ({len(refusals)} refusal(s) recorded) — the goal "
                    f"stays NOT_MEASURED with this reason, never a pass"),
            totals={})
        return receipt

    receipt.update(applicable=True, totals={dimension: fused},
                   reason=(f"{fused['covered']}/{fused['total']} = "
                           f"{fused['pct']:g}% from "
                           f"{len(contributions)} contributing transcript(s)"))
    # Per goal, the arithmetic the gate will do, stated here too so the
    # receipt is readable without re-running the classifier.
    rows = []
    for g in goals:
        stated, stated_cite = _cgc.stated_acceptance_percentage(g)
        rows.append({
            "goal": g.get("name") or g.get("id"),
            "stated_pct": stated, "stated_source": stated_cite,
            "achieved_pct": fused["pct"],
            "covered": fused["covered"], "total": fused["total"],
            "meets": (None if stated is None else fused["pct"] >= stated),
        })
    receipt["per_goal"] = rows
    return receipt


def write_receipt(project: Path, receipt: dict) -> Path:
    out = Path(project) / RECEIPT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=1) + "\n")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("--dimension", default=DIMENSION)
    ap.add_argument("--print", action="store_true",
                    help="also print the receipt as JSON")
    args = ap.parse_args(argv)
    project = Path(args.project)
    receipt = measure(project, args.dimension)
    path = write_receipt(project, receipt)
    if args.print:
        print(json.dumps(receipt, ensure_ascii=False, indent=1))
    totals = receipt.get("totals") or {}
    if totals:
        d = totals[args.dimension]
        print(f"instruction_coverage_measure: {args.dimension} "
              f"{d['covered']}/{d['total']} = {d['pct']:g}% -> {path}")
    else:
        print(f"instruction_coverage_measure: no {args.dimension} total "
              f"published — {receipt.get('reason')} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
