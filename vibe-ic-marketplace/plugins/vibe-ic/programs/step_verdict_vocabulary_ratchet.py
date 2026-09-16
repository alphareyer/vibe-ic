#!/usr/bin/env python3
"""Tree-wide ratchet: no producer writes a step status outside the five.

R-0915-85 reduced the flow's step-verdict vocabulary to the five words in
`programs/verdict.py`. This is the guard that keeps it reduced.

WHY A RATCHET AND NOT A TEST OF THE PRODUCERS
=============================================
The vocabulary did not grow by anybody deciding to grow it. It grew one string
at a time, in a branch nobody reviewed as a vocabulary change:

    #544   BLOCKED                     added to phase3, remembered into ONE of
                                       the two aggregators
    #654   VACUOUS_PASS                reached a `return "PASS"` catch-all
    #901   PARTIALLY-VACUOUS           registered in one classifier, not the other
    #2063  NOT-MEASURED                a second spelling beside INCOMPLETE
    #2148  PASS_WITH_ATTRIBUTION       reached the same catch-all
    #2153  WARN, PASS_W_WARN           found by walking the source, not by a test
    R-82   NOT_EXECUTED                added while this very ruling was being written

Every one of those was a one-line string. The module's own rule says a new
status word is a SCHEMA CHANGE, not a string; this program is what makes that
true rather than aspirational — a twenty-fourth word fails a test at the commit
that introduces it, where it is still cheap.

WHAT IT SCANS, AND WHY THE SCOPE IS DERIVED
===========================================
Not a file list. A file is IN SCOPE the moment it does either of the two things
that write a step status:

    * constructs a `StepResult(...)`
    * assigns to a `.status` attribute, or passes `status=` to a call

and every string literal reaching those positions must be one of the five, a
reference to `verdict.Verdict`, or a non-literal the scanner cannot resolve
(reported separately, never counted as a pass).

A list of producers would go quiet on exactly the file it did not know about —
which is how `analog_one_shot_runner` came to carry `PASS_STRUCTURE_ONLY`, a
sixth run-level word, for nine months.

WHAT IS DELIBERATELY NOT IN SCOPE
=================================
A TABLE-DRIVEN STATUS, and this is a MEASURED limit rather than an oversight.
`analog_one_shot_runner._A1_A3_PRODUCERS` held a step's status in a dict
literal that reaches a row through `prod["status"]`; three words
(`PASS_WITH_REAL_EXTRACT`, `PASS_WITH_DERIVED_TOPOLOGY`,
`PASS_WITH_REAL_NETLIST`) survived the first migration there and were found by a
TEST, not by this scan. Judging every `{"status": ...}` in a file that also
builds StepResults was tried and REJECTED: it reports 20 per-gate JSON payload
fields (`WELLTAP_PRESENT`, `NOT_REQUESTED`, `MANUAL_REVIEW`, `AUTOMATED`, `NA`)
that are a gate's own document and not a step verdict at all. Telling the two
apart needs dataflow, not a pattern, so this scan says what it does not cover
instead of crying wolf — and the three words it missed are pinned by name in
`test_verdict_five_words_and_one_cascade`. The runtime backstop is real and is
counted in every report: `verdict.parse` judges such a status when the row is
built.


`GATE PROGRAM` verdicts. A gate report's own `verdict` field is a DIFFERENT and
much larger vocabulary (~450 programs, `PASS` / `VACUOUS_PASS` / `SKIP` /
`NOT_INVOCABLE` / `INSUFFICIENT_DATA` / …), governed by `P0_GATE_VERDICTS` and
`_flow_reason_taxonomy`, and R-0915-85's subject is the word a STEP wears.
Scanning it here would report several hundred findings about a vocabulary this
ruling did not reduce — see the handback's follow-up note. The discrimination
is mechanical: `rep["verdict"] = …` is a gate's, `row.status = …` is a step's.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import verdict as _V  # noqa: E402

#: This file and the vocabulary module itself name every deleted word on
#: purpose — one to scan for them, the other to say what replaced each. A
#: ratchet that flagged its own subject would be unrunnable.
SELF_EXEMPT = {"step_verdict_vocabulary_ratchet.py", "verdict.py"}

ALLOWED = frozenset(v.value for v in _V.Verdict)


def _status_positions(call: ast.Call) -> List[ast.AST]:
    """The argument nodes that land in a step status field.

    `StepResult` is positional in three runners (`name, status, …`) and
    four-positional in the analog one (`name, block, status, …`), so the
    position is not fixed and is not guessed: every positional argument is
    examined, and only a STRING that is a known status word can match. A
    string that is a step name or a detail sentence is not in `ALLOWED` and is
    not in the deleted set either, so it is silently correct.
    """
    out: List[ast.AST] = list(call.args)
    out += [kw.value for kw in call.keywords if kw.arg == "status"]
    return out


def scan_source(src: str, path: str = "<src>") -> Dict[str, Any]:
    """Every step-status literal in one module."""
    tree = ast.parse(src)
    # SCOPE, DERIVED. A module is a step-status producer iff it builds a
    # `StepResult`. `status=` on any other call — `rep.update(status=...)`,
    # `Measurement(status=...)`, `Check(status=...)` — is a GATE PROGRAM's own
    # payload field and a different vocabulary (see the module docstring); the
    # first cut of this scanner did not make that distinction and reported 88
    # findings, 86 of them about words this ruling never touched.
    _builds_step_results = any(
        isinstance(n, ast.Call)
        and (n.func.attr if isinstance(n.func, ast.Attribute)
             else getattr(n.func, "id", "")) == "StepResult"
        for n in ast.walk(tree))
    # A SECOND `StepResult` EXISTS and is not the flow's. `mcp_execution_verify`
    # defines its own — `step / status / timestamp / tool / age_hours`, whose
    # five words (FOUND_PASS / FOUND_FAIL / FOUND_INCONCLUSIVE / NOT_FOUND /
    # STALE) are about an MCP tool-execution LEDGER ENTRY, not a flow step.
    # Told apart by SHAPE, not by a file name on an exemption list: the flow's
    # step row carries `reason_class`, the field R-0915-85 put beside the
    # verdict. A module that defines a `StepResult` WITHOUT it is defining a
    # different thing, and the discrimination is checkable in its own source.
    for _n in ast.walk(tree):
        if isinstance(_n, ast.ClassDef) and _n.name == "StepResult":
            _fields = {t.target.id for t in _n.body
                       if isinstance(t, ast.AnnAssign)
                       and isinstance(t.target, ast.Name)}
            if "reason_class" not in _fields:
                _builds_step_results = False
    findings: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    checked = 0

    def _record(node: ast.AST, word: str, where: str) -> None:
        nonlocal checked
        checked += 1
        if word not in ALLOWED:
            findings.append({"file": path, "line": getattr(node, "lineno", 0),
                             "word": word, "site": where})

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
                fn, "id", "")
            if name == "StepResult" and _builds_step_results:
                for arg in _status_positions(node):
                    if isinstance(arg, ast.Constant) and isinstance(
                            arg.value, str):
                        # Only a word that LOOKS like a status is judged: an
                        # ALL-CAPS token. A detail sentence is not one.
                        w = arg.value
                        if w and w.replace("_", "").replace(
                                "-", "").replace("/", "").isupper():
                            _record(arg, w, "StepResult")
                    elif isinstance(arg, (ast.Name, ast.Attribute,
                                          ast.IfExp, ast.Call, ast.Subscript)):
                        unresolved.append(
                            {"file": path, "line": node.lineno,
                             "site": "StepResult", "kind": type(arg).__name__})
        elif isinstance(node, ast.Assign) and _builds_step_results:
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == "status" and \
                        isinstance(node.value, ast.Constant) and \
                        isinstance(node.value.value, str):
                    _record(node.value, node.value.value, ".status =")
        elif isinstance(node, ast.AnnAssign) and _builds_step_results:
            if isinstance(node.target, ast.Attribute) and \
                    node.target.attr == "status" and \
                    isinstance(node.value, ast.Constant) and \
                    isinstance(node.value.value, str):
                _record(node.value, node.value.value, ".status:")
    # THE COMPARISON PASS. Same scope, same allowed set, different SHAPE — and
    # the helpers it uses are defined below, beside the note that says why.
    if _builds_step_results:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not _reads_a_step_status(node.left):
                continue
            for word in _compared_words(node):
                if word in DELETED_STEP_WORDS:
                    findings.append({"file": path, "line": node.lineno,
                                     "word": word, "site": "comparison"})
                    checked += 1
    return {"checked": checked, "findings": findings, "unresolved": unresolved}


#: THE SECOND HALF OF THE RATCHET: a COMPARISON is not an assignment.
#:
#: MEASURED, and it is why this exists. `design_one_shot_runner` carried
#: `publish_changes = result.status in ("PASS", "WAIVED")`, `phase3` carried
#: `if pnr_row.status != "WAIVED"`, and both scanned CLEAN under the literal
#: pass above -- nothing assigns the word, so nothing is judged. They were
#: DEAD: no row carries `WAIVED` any more, so the first stopped publishing a
#: waived generator's staging and the second dropped the GDS on every waived
#: PnR, which is vibe-ic#1412 exactly as that issue describes it.
#:
#: A dead comparison is worse than a wrong literal, because it fails SILENTLY
#: and in the safe-looking direction. Judged with the same scope rule as the
#: literals: only in a module that builds the FLOW's `StepResult`, and only on
#: an expression that reads a step's own status field.
_STATUS_READS = ("status", "signoff_status", "terminal_verdict", "run_verdict")

#: THE WORDS R-0915-85 DELETED, listed so a comparison against one can be
#: REFUSED. This is a refusal register, not a translation table: no entry has a
#: target, nothing here can be read as "X means Y", and no producer or consumer
#: imports it — it lives in the CHECKER, whose whole job is to say no.
#:
#: Why only these and not "any ALL-CAPS word": `status` is a common key name,
#: and a module that builds a flow StepResult also compares `record["status"]`
#: of a dozen other payloads — MANUAL_REVIEW, GUARDBAND, APPLIED, N/A. Those
#: are other vocabularies and are none of this ruling's business. What IS its
#: business is a comparison that can never again be true.
DELETED_STEP_WORDS = frozenset({
    "WAIVED", "WAIVED-DEFERRED", "DEFERRED-BY-UPSTREAM",
    "SKIP", "SKIPPED", "SKIPPED-CONDITION", "SKIPPED_CONDITION",
    "SKIPPED-SETUP-REQUIRED", "SKIPPED-BY-ENTRY", "SKIPPED-BY-EXIT",
    "MISSING", "INCOMPLETE", "NOT_CHECKED", "NOT-CHECKED",
    "NOT_EXECUTED", "NOT-EXECUTED", "NOT_EVALUATED", "NO_POPULATION",
    "VACUOUS_PASS", "VACUOUS-PASS", "PARTIALLY-VACUOUS", "PARTIALLY_VACUOUS",
    "PASS_VOIDED_BY_DEPENDENCY", "PASS-VOIDED-BY-DEPENDENCY",
    "PASS_STRUCTURE_ONLY", "STRUCTURE-ONLY", "STRUCTURE_ONLY",
    "PASS_WITH_OPEN_SOURCE_CONSTRAINTS", "PASS_WITH_ATTRIBUTION",
    "INSUFFICIENT_DATA", "ENV_UNAVAILABLE", "BLOCKED", "ADVISORY",
    "PASS_WITH_REAL_EXTRACT", "PASS_WITH_REAL_NETLIST",
})


#: The three keys that are UNAMBIGUOUS as dict reads. `status` is not among
#: them on purpose — see `_reads_a_step_status`.
_UNAMBIGUOUS_KEYS = ("signoff_status", "terminal_verdict", "run_verdict")


def _reads_a_step_status(node: ast.AST) -> bool:
    """True when `node` reads a FLOW STEP's own verdict field.

    ATTRIBUTE access is judged for all four keys: the flow's step row is a
    dataclass, so `row.status` is always a step status and never a gate
    report's payload.

    DICT access is judged only for the three unambiguous keys. `status` is one
    of the commonest key names in this tree, and a module that builds a flow
    StepResult also parses a dozen gate reports that carry their own
    `status` — `analog_block_list_schema_check` answers `VACUOUS_PASS` there,
    which is ITS vocabulary and none of this ruling's business. Judging
    `rep.get("status")` would report that as a defect, so it is NOT judged, and
    saying so here is the difference between a scope and a blind spot.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in _STATUS_READS
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value in _UNAMBIGUOUS_KEYS
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args
            and isinstance(node.args[0], ast.Constant)):
        return node.args[0].value in _UNAMBIGUOUS_KEYS
    return False


def _compared_words(cmp_node: ast.Compare) -> List[str]:
    out: List[str] = []
    for c in cmp_node.comparators:
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            out.append(c.value)
        elif isinstance(c, (ast.Tuple, ast.List, ast.Set)):
            for e in c.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    out.append(e.value)
    return out


def scan_tree(root: Path) -> Dict[str, Any]:
    files, findings, unresolved, checked = 0, [], [], 0
    for p in sorted(root.rglob("*.py")):
        if p.name in SELF_EXEMPT:
            continue
        if "/tests/" in p.as_posix() or p.name.startswith("test_"):
            continue
        try:
            r = scan_source(p.read_text(encoding="utf-8"),
                            p.relative_to(root).as_posix())
        except SyntaxError as e:                      # pragma: no cover
            findings.append({"file": p.name, "line": e.lineno or 0,
                             "word": "<unparseable>", "site": "syntax"})
            continue
        files += 1
        checked += r["checked"]
        findings += r["findings"]
        unresolved += r["unresolved"]
    return {"files": files, "literals_checked": checked,
            "findings": findings, "unresolved": unresolved,
            "allowed": sorted(ALLOWED)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=str(_HERE),
                    help="directory to scan (default: programs/)")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--ratchet", action="store_true",
                    help="exit 1 on any finding (the CI shape)")
    a = ap.parse_args(argv)
    rep = scan_tree(Path(a.root))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2) + "\n",
                                encoding="utf-8")
    print(f"step-verdict vocabulary ratchet: {rep['files']} file(s), "
          f"{rep['literals_checked']} status literal(s) judged against "
          f"{rep['allowed']}")
    if rep["unresolved"]:
        # NOT a finding and NOT silence. A status built at runtime cannot be
        # judged from the source, and saying so is the difference between "no
        # finding" and "nothing was looked at".
        print(f"  {len(rep['unresolved'])} status argument(s) are computed at "
              f"runtime and were NOT judged here; `verdict.parse` judges them "
              f"when the row is built.")
    if not rep["findings"]:
        print("PASS: every step status literal in the tree is one of the five.")
        return 0
    print(f"FAIL: {len(rep['findings'])} step status literal(s) outside the "
          f"five. A new status word is a SCHEMA CHANGE, not a string — read "
          f"the rule at the top of programs/verdict.py.", file=sys.stderr)
    for f in rep["findings"][:40]:
        print(f"  {f['file']}:{f['line']}  {f['word']!r}  ({f['site']})",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
