#!/usr/bin/env python3
"""_sim_results_bridge.py — shared, chip-AGNOSTIC reader for the professional
cocotb testbench result (the NEW TB path emitted by professional_tb_gen under
``phase2/stage1/sim_professional/<top>/results.xml``).

Motivation (2026-07-13): the canonical Step-4 (Simulation) verdict historically
keyed ONLY on the AID reference-TB chain — ``phase2/stage1/sim/results.xml``
(a CONNECTIVITY_PASS bridge written by the WAIVED reference TB for the no-oracle
classes) and ``reports/phase2/coverage/coverage_actual.json`` (SKIPPED-CONDITION
"no reference-TB transcript", #436). For a class whose functional oracle IS
derivable (e.g. a bit-serial multiplier), ``professional_tb_gen`` already RAN a
real cocotb streaming-scoreboard against the real rtl/ and wrote a standard
JUnit ``results.xml`` with ``failures="0"`` — genuine functional evidence that
the reference-TB-only verdict never looked at. This bridge lets the Step-4
aggregator RECOGNISE that real PASS.

It is deliberately a pure, side-effect-free parser:
  * ``parse_junit(path)`` — sum tests/failures/errors/skipped across every
    ``<testsuite>`` in a cocotb/JUnit ``results.xml``. Returns None for a
    non-JUnit document (e.g. the ``<results><verdict>…`` connectivity bridge),
    so it can NEVER mistake the connectivity waiver for a functional PASS.
  * ``professional_tb_union(project)`` — EVERY sibling suite under
    ``phase2/stage1/sim_professional/``, counted when it produced a JUnit and
    NAMED as ``not_measured`` when it did not. The Step-4 verdict is a function
    of this union.
  * ``find_professional_tb_pass(project)`` — the union's summary, returned ONLY
    when the union is a real functional PASS (``tests > 0`` AND
    ``failures == 0`` AND ``errors == 0`` AND ``passed > 0``). Globs on ``*``
    (the DUT sub-dir) so it is chip-AGNOSTIC — it keys on the JUnit structure
    and the standard path, never on a chip / vendor / SKU literal.

#2073 — IT WAS FIRST-PASS-WINS ACROSS SIBLING SUITES. More than one producer
writes into this slot (the cocotb professional TB, the L10 unit-TB executor,
the analog acceptance TB), so a project legitimately carries SEVERAL sibling
suite directories. The reader returned the FIRST one that passed and never
looked at the rest: measured through the front door on a mixed analog/digital
project (lane czacctb, #2064), where an analog
acceptance suite with 10 failures beside a 7-test green digital suite published
verdict PASS while the SAME record's ``functional_test_denominator`` — which
already summed the union — read ``tests_run 17 / passed 7 / failed 10``. A
verdict that contradicts its own denominator in one record is a defect of this
reader, not of either suite. The union is now the subject of the predicate, and
a suite that produced no parsable JUnit is named, never silently skipped.

Anti-fabrication (§4.05): this module only ADDS recognition of a REAL passing
transcript. A missing / unparsable / failing / vacuous (zero-test) professional
result yields None, so every caller degrades to EXACTLY its prior behaviour.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard emission path for professional_tb_gen: sim_professional/<top>/results.xml
_PROFESSIONAL_GLOB = "phase2/stage1/sim_professional/*/results.xml"

#: The sibling SUITE directories the glob above draws its results from. A suite
#: that exists here and produced no parsable JUnit is a NOT_MEASURED fact the
#: union reports by name — the glob alone cannot see it, which is how a suite
#: could be skipped in silence.
_PROFESSIONAL_SUITE_GLOB = "phase2/stage1/sim_professional/*"

#: The union verdicts. ABSENT (no suite tree at all) and NOT_MEASURED (suites
#: exist, none produced a readable JUnit) are DIFFERENT facts and must not
#: collapse: "could not read it" is not "read it and it was empty".
UNION_PASS = "PASS"
UNION_FAIL = "FAIL"
UNION_VACUOUS = "VACUOUS"
UNION_NOT_MEASURED = "NOT_MEASURED"
UNION_ABSENT = "ABSENT"


def parse_junit(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a cocotb / JUnit ``results.xml`` and aggregate the test counts.

    Returns a dict ``{tests, failures, errors, skipped, passed}`` (ints) or
    ``None`` when the file is absent, unparsable, or is NOT a JUnit document
    (no ``<testsuite>`` / ``<testcase>`` elements — e.g. the ``<results>``
    connectivity bridge, which must never be read as a functional PASS)."""
    try:
        if not path.is_file():
            return None
        root = ET.fromstring(path.read_text(errors="replace"))
    except (OSError, ET.ParseError):
        return None

    # Collect every <testsuite> — the root may itself be <testsuites> (the
    # cocotb wrapper) or a single <testsuite>. Anything else is not JUnit.
    suites: List[ET.Element] = []
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    else:
        # Not a JUnit document (e.g. the <results><verdict>… bridge).
        return None
    if not suites:
        return None

    def _int(el: ET.Element, attr: str) -> int:
        try:
            return int(el.get(attr, "0") or "0")
        except (TypeError, ValueError):
            return 0

    tests = sum(_int(s, "tests") for s in suites)
    failures = sum(_int(s, "failures") for s in suites)
    errors = sum(_int(s, "errors") for s in suites)
    skipped = sum(_int(s, "skipped") for s in suites)
    # Some emitters omit the tests= attribute; fall back to counting <testcase>.
    if tests == 0:
        tc = sum(1 for s in suites for _ in s.iter("testcase"))
        if tc:
            tests = tc
    passed = max(tests - failures - errors - skipped, 0)
    # #—: WHICH producer wrote this transcript. The slot is a PATH, and more
    # than one producer legitimately writes into it (the cocotb professional TB
    # and the L10 unit-TB executor). A message that names the slot's historical
    # producer for a result another producer wrote is a small lie in the one
    # sentence a reader trusts, so carry the suite's own name.
    names = [s.get("name") or "" for s in suites if (s.get("name") or "")]
    return {
        "suite_names": names,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": passed,
    }


def professional_tb_union(project: Path) -> Dict[str, Any]:
    """Every sibling suite under ``phase2/stage1/sim_professional/``, COUNTED
    when it produced a JUnit and NAMED when it did not.

    The Step-4 functional verdict is a function of this union and of nothing
    smaller. Returned keys:

      ``suites``        one entry per suite that produced a parsable JUnit,
                        each ``{rel_path, suite_names, tests, failures,
                        errors, skipped, passed}``.
      ``not_measured``  one entry per suite DIRECTORY that produced no parsable
                        JUnit, each ``{rel_dir, reason}``. This is the half a
                        glob over ``*/results.xml`` cannot see: a suite that
                        never ran is absent from the glob and was therefore
                        skipped in silence.
      ``rel_paths``     the measured transcripts, project-relative POSIX.
      ``suite_names``   the ``<testsuite name=…>`` of every measured suite.
      ``failing``       one reviewable sentence per suite that carries a
                        failure or an error — "every failure named".
      ``tests`` / ``failures`` / ``errors`` / ``skipped`` / ``passed``
                        the UNION totals, the same sums
                        ``cpu_functional_oracle_waiver_check``'s
                        ``functional_test_denominator`` publishes.
      ``verdict``       one of ``PASS`` (tests > 0, failures == errors == 0,
                        passed > 0), ``FAIL`` (a failure or an error anywhere in
                        the union), ``VACUOUS`` (measured, but nothing passed),
                        ``NOT_MEASURED`` (suites exist, none produced a readable
                        JUnit) or ``ABSENT`` (no suite tree at all).

    Pure and side-effect-free. chip-AGNOSTIC: the standard path and the JUnit
    structure, never a chip / vendor / SKU literal."""
    measured: List[Dict[str, Any]] = []
    not_measured: List[Dict[str, str]] = []
    try:
        suite_dirs = sorted(d for d in project.glob(_PROFESSIONAL_SUITE_GLOB)
                            if d.is_dir())
    except OSError:
        suite_dirs = []
    for d in suite_dirs:
        try:
            rel_dir = d.relative_to(project).as_posix()
        except ValueError:                        # pragma: no cover — defensive
            rel_dir = d.as_posix()
        res = d / "results.xml"
        summ = parse_junit(res)
        if summ is None:
            # NAME IT. Absent and unreadable are both "we did not measure this
            # suite", and neither may be spent as a pass or as a failure.
            not_measured.append({
                "rel_dir": rel_dir,
                "reason": ("produced no results.xml" if not res.is_file() else
                           "results.xml is not a readable JUnit document"),
            })
            continue
        measured.append({"rel_path": f"{rel_dir}/results.xml", **summ})

    totals = {k: sum(int(m.get(k, 0) or 0) for m in measured)
              for k in ("tests", "failures", "errors", "skipped", "passed")}
    failing = [f"{m['rel_path']}: tests={m['tests']} failures={m['failures']} "
               f"errors={m['errors']}"
               for m in measured if (m["failures"] or m["errors"])]
    suite_names: List[str] = []
    for m in measured:
        suite_names.extend(m.get("suite_names") or [])

    if not measured and not not_measured:
        verdict = UNION_ABSENT
    elif not measured:
        verdict = UNION_NOT_MEASURED
    elif totals["failures"] or totals["errors"]:
        verdict = UNION_FAIL
    elif totals["tests"] > 0 and totals["passed"] > 0:
        verdict = UNION_PASS
    else:
        verdict = UNION_VACUOUS

    return {
        "suites": measured,
        "not_measured": not_measured,
        "rel_paths": [m["rel_path"] for m in measured],
        "suite_names": suite_names,
        "failing": failing,
        "verdict": verdict,
        **totals,
    }


def union_disclosure(union: Dict[str, Any]) -> str:
    """One reviewable clause naming what the union could NOT measure, or "".

    Every caller that CREDITS or REFUSES on this union appends it, so a suite
    that produced no transcript reaches the reader by name instead of being
    dropped between the glob and the sentence."""
    nm = union.get("not_measured") or []
    if not nm:
        return ""
    return ("NOT_MEASURED: " + "; ".join(
        f"{e.get('rel_dir')} ({e.get('reason')})" for e in nm))


def find_professional_tb_pass(project: Path) -> Optional[Dict[str, Any]]:
    """Return a summary of the professional cocotb TB result IFF the UNION of
    every sibling suite is a real functional PASS, else ``None``.

    "Real functional PASS" = across EVERY suite under
    ``phase2/stage1/sim_professional/<top>/`` that produced a JUnit
    ``results.xml``: ``tests > 0`` AND ``failures == 0`` AND ``errors == 0``
    AND ``passed > 0``. A vacuous (zero-test), failing, or all-skipped union
    returns ``None`` — the professional path did not close functional
    verification, so the caller keeps its prior verdict. #2073: a FAILING
    sibling can no longer be out-sorted by a passing one.

    Summary keys: ``rel_path`` (POSIX, project-relative — the first measured
    transcript, kept single because callers dereference it), ``rel_paths``
    (every measured transcript), ``not_measured`` (every suite that produced
    none, by name), ``suite_names``, and the UNION ``tests``, ``failures``,
    ``errors``, ``skipped``, ``passed``. chip-AGNOSTIC."""
    union = professional_tb_union(project)
    if union["verdict"] != UNION_PASS:
        return None
    return {
        "rel_path": union["rel_paths"][0],
        "rel_paths": list(union["rel_paths"]),
        "not_measured": list(union["not_measured"]),
        "suite_names": list(union["suite_names"]),
        "tests": union["tests"],
        "failures": union["failures"],
        "errors": union["errors"],
        "skipped": union["skipped"],
        "passed": union["passed"],
    }


def substantiated_functional_evidence(project: Path,
                                      rel_path: str) -> Optional[Dict[str, Any]]:
    """Validate a record's own ``<functional_evidence>`` pointer.

    A record that CLAIMS ``functional_verified=true`` is a forgery unless it can
    SHOW the transcript. This resolves the pointer against the project and
    applies the same predicate `find_professional_tb_pass` applies — a real
    JUnit document with ``tests > 0``, ``failures == 0``, ``errors == 0`` and
    ``passed > 0``. Anything else (absent, unparsable, non-JUnit, vacuous,
    failing, or escaping the project) returns None, so a caller that refuses on
    None keeps refusing every forgery it refused before.

    chip-AGNOSTIC: a project-relative path and the JUnit structure, nothing else.
    """
    rel = (rel_path or "").strip()
    if not rel:
        return None
    try:
        cand = (project / rel).resolve()
        # A pointer that leaves the project is not this run's evidence.
        cand.relative_to(project.resolve())
    except (OSError, ValueError):
        return None
    summ = parse_junit(cand)
    if not summ:
        return None
    if (summ["tests"] > 0 and summ["failures"] == 0
            and summ["errors"] == 0 and summ["passed"] > 0):
        return {"rel_path": rel, **summ}
    return None
