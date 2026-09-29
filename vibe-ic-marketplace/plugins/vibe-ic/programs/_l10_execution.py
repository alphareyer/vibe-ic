#!/usr/bin/env python3
"""Contract for independently-produced, per-case L10 execution evidence.

The L10 conformance consumer must not infer execution from testbench source
text.  The executor writes this record after the simulator finishes; absence,
staleness, malformed data, or a missing case row is ``NOT_EXECUTED``.

This module is a reader/writer contract, not a verdict gate.  The consumer
declares enforcement: ``FAIL`` and ``NOT_EXECUTED`` both block Step 4, while
remaining distinct claims about the design and the run respectively.

It also reads the ONE other per-case execution record a declared case can
have: the core-ISA conformance suite's receipt (``isa_conformance_credit``,
owner ruling R-0929-OWNER-SUB-ACCEPT (1)). That reader never changes
``case_state``; a consumer asks it explicitly, for a case this record left
``NOT_EXECUTED``, and discloses what it credited.
"""

from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Measured
# on the base tree: 454 of the 1385 top-level programs died that way. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------


import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _atomic_artefact import write_text as _atomic_write_text

SCHEMA = "vibeic.l10_execution.v1"
PASS = "PASS"
FAIL = "FAIL"
NOT_EXECUTED = "NOT_EXECUTED"
SIM_EXECUTED_KEY = "sim_executed"
CASES_KEY = "cases"

_RECORD_CANDIDATES = (
    "reports/phase2/sim/l10_execution.json",
    "reports/phase2/l10_execution.json",
    "phase2/stage1/sim/l10_execution.json",
    "reports/sim/l10_execution.json",
)


def record_path(project: Path) -> Path:
    """Canonical producer target."""
    return Path(project) / _RECORD_CANDIDATES[0]


def resolve_record(project: Path) -> Optional[Path]:
    """Resolve the first supported record location, if any."""
    for rel in _RECORD_CANDIDATES:
        candidate = Path(project) / rel
        if candidate.is_file():
            return candidate
    return None


def file_sha256(path: Path) -> str:
    """Hash an input as bytes so evidence can bind to the exact declaration."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clear_record(project: Path) -> None:
    """Remove prior evidence before an execution attempt starts.

    A failed or unavailable rerun must not leave an earlier PASS in place.
    Only the contract's producer-owned generated artefact locations are
    removed.  Clearing every supported read location prevents a stale legacy
    fallback from reappearing when the canonical file is removed.
    """
    for rel in _RECORD_CANDIDATES:
        try:
            (Path(project) / rel).unlink()
        except FileNotFoundError:
            pass


def write_record(project: Path, l10_path: Path,
                 rows: Iterable[Dict[str, Any]], *, producer: str,
                 tb_dir: Optional[Path] = None,
                 source_junit: Optional[Path] = None) -> Path:
    """Atomically publish a completed execution record."""
    target = record_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "producer": producer,
        "l10_sha256": file_sha256(l10_path),
        CASES_KEY: list(rows),
    }
    if tb_dir is not None:
        doc["tb_dir"] = str(tb_dir)
    if source_junit is not None:
        doc["source_junit"] = str(source_junit)
    _atomic_write_text(target, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return target


def _unavailable(reason: str, path: Optional[Path] = None,
                 malformed: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "path": str(path) if path else None,
        "producer": None,
        "schema_ok": False,
        "l10_binding_ok": False,
        "rows": {},
        "malformed": malformed or [],
    }


def _normalise_verdict(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    token = raw.strip().upper()
    aliases = {
        "PASS": PASS, "PASSED": PASS, "OK": PASS,
        "FAIL": FAIL, "FAILED": FAIL, "ERROR": FAIL,
        NOT_EXECUTED: NOT_EXECUTED,
    }
    return aliases.get(token)


def load_record(project: Path, l10_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a record, failing closed on every unreviewable shape.

    When ``l10_path`` is supplied, a record must bind to that exact file hash;
    an old record beside a changed declaration credits no case.
    """
    path = resolve_record(project)
    if path is None:
        return _unavailable("no_execution_record")
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return _unavailable(
            f"execution_record_unreadable ({type(exc).__name__})", path)
    if not isinstance(doc, dict):
        return _unavailable("execution_record_not_an_object", path)
    if doc.get("schema") != SCHEMA:
        return _unavailable("execution_record_schema_mismatch", path)
    if l10_path is not None:
        try:
            expected = file_sha256(Path(l10_path))
        except OSError as exc:
            return _unavailable(
                f"l10_binding_unreadable ({type(exc).__name__})", path)
        if doc.get("l10_sha256") != expected:
            return _unavailable("execution_record_l10_hash_mismatch", path)
    rows = doc.get(CASES_KEY)
    if not isinstance(rows, list):
        return _unavailable(f"execution_record_has_no_{CASES_KEY}_list", path)

    parsed: Dict[str, Dict[str, Any]] = {}
    malformed: List[str] = []
    duplicates = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            malformed.append(f"row {index}: not an object")
            continue
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            malformed.append(f"row {index}: no usable id")
            continue
        case_id = case_id.strip()
        if case_id in parsed:
            duplicates.add(case_id)
            malformed.append(f"row {index}: duplicate id {case_id!r}")
            continue
        verdict = _normalise_verdict(row.get("verdict"))
        parsed[case_id] = {
            "verdict": verdict,
            "raw": row.get("verdict"),
            "detail": str(row.get("detail") or ""),
            "tb_file": str(row.get("tb_file") or ""),
            # Execution is an observed boolean, not something inferred back
            # from the verdict word.  Missing, string-valued, or false keeps
            # the row at NOT_EXECUTED in ``case_state``.
            SIM_EXECUTED_KEY: row.get(SIM_EXECUTED_KEY) is True,
        }
    for duplicate in duplicates:
        parsed.pop(duplicate, None)

    return {
        "available": True,
        "reason": None,
        "path": str(path),
        "producer": doc.get("producer"),
        "schema_ok": True,
        "l10_binding_ok": True,
        "rows": parsed,
        "malformed": malformed,
    }


def case_state(case_id: str, record: Dict[str, Any]) -> Tuple[str, str]:
    """Return PASS, FAIL, or NOT_EXECUTED for one declared case."""
    row = (record.get("rows") or {}).get(case_id)
    if row is None:
        if not record.get("available"):
            why = record.get("reason") or "no_execution_record"
            return NOT_EXECUTED, f"no per-case execution evidence ({why})"
        return NOT_EXECUTED, "case absent from completed execution record"
    verdict = row.get("verdict")
    if verdict in (PASS, FAIL):
        if not row.get(SIM_EXECUTED_KEY):
            return NOT_EXECUTED, "row verdict is not backed by sim_executed=true"
        return verdict, f"execution record row (verdict={verdict})"
    if verdict == NOT_EXECUTED:
        return NOT_EXECUTED, row.get("detail") or "executor reported case not executed"
    return NOT_EXECUTED, f"unrecognised execution verdict {row.get('raw')!r}"


def unclaimed_rows(case_ids: Iterable[str], record: Dict[str, Any]) -> List[str]:
    """Rows naming no currently-declared L10 case."""
    declared = set(case_ids)
    return sorted(key for key in (record.get("rows") or {}) if key not in declared)


# ── CORE-ISA CONFORMANCE AT A TEST PARAMETER (R-0929-OWNER-SUB-ACCEPT (1)) ────
#
# THE OWNER'S RULE, 2026-09-29: a CPU core-ISA conformance suite run on the
# SAME RTL with only a memory-size PARAMETER changed COUNTS as the CPU
# functional-verification evidence for the ISA rows, and is DISCLOSED in the
# verdict (parameter name, delivered value vs test value, suite, programs and
# instructions passed); the delivered-memory run is still reported as measured
# or not. Any RTL change, a different top, or a failing suite gives no credit.
#
# WHY IT IS NEEDED — MEASURED on subservient x gf180mcuD (2026-09-29): the
# delivered 1024-byte SRAM cannot hold a single riscv-arch-test program
# (22848 B for `add`), so the delivered-memory arm reads RV32I 0/39 NOT_MEASURED
# and Zifencei 1/2, while the same 23 RTL files instantiated with the top's own
# memory-size parameter raised to 2 MiB pass 39/39 primary programs and 38/38
# applicable instructions. Nothing about the CPU is different between the two
# runs except the size of the array the testbench hands it.
#
# THIS IS A READER, NOT A PRODUCER. The receipt is the ISA-suite producer's
# (`ISA_RECEIPT_REL`, schema `ISA_RECEIPT_SCHEMA`). Every condition of the rule
# is re-checked HERE against the project's own files, never taken from the
# receipt's summary words:
#   * SAME RTL   an arm whose per-file sha256 set equals the delivered RTL
#                (`_path_layout.rtl_dir`, every *.v / *.sv) exactly -- same
#                names, same bytes, nothing extra, nothing missing;
#   * SAME TOP   the receipt's top equals the design declaration's top_module;
#   * PARAMETER  the receipt's memory-size parameter is a `parameter` of that
#                top in the delivered RTL, and its default there is the
#                receipt's delivered value (and equals the declaration's
#                core_parameters entry when the declaration states one);
#   * BOUND      the case's OWN current L10 text names every unit the receipt
#                binds to it (the producer's `bind_case` rule, re-applied
#                here: `RV<xlen>I` for the base, the extension's own
#                multi-letter token otherwise) -- a stale or foreign receipt
#                cannot credit a case that asks for something else; and the
#                case names no program image (a firmware row is rule (2)'s,
#                and the ISA suite never ran that program);
#   * PASSING    every PRIMARY program of every unit the case binds PASSed on
#                that arm at the test value, under every init pattern it ran,
#                and no program of those units FAILed there (any role) -- nor
#                on the same arm at the DELIVERED value: a measured FAIL of
#                the delivered configuration is never published inside a
#                credit;
#   * COUNTED    the passed programs and instructions are recomputed from the
#                per-program rows and must agree with the receipt's own totals;
#                the delivered-memory run is recomputed from the rows too.
# Anything short of that is a named refusal and the case keeps the verdict it
# already had. A receipt that binds no ISA unit to the case says nothing about
# it (None, None).
ISA_RECEIPT_REL = "reports/phase2/isa_suites/isa_suite_receipt.json"
ISA_RECEIPT_SCHEMA = "vibeic.isa_suite_receipt.v1"
ISA_CREDIT_KIND = "core_isa_conformance_at_test_parameter"
_DECLARATION_REL = "plugin_output/declaration.json"
_HDL_SUFFIXES = (".v", ".sv")
_HDL_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_INT_LITERAL_RE = re.compile(r"^\s*(?:\d+\s*'\s*[sS]?[dD]\s*)?(\d+)\s*$")


def _json_object(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(Path(path).read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _module_parameter_default(rtl_files: Iterable[Path], module: str,
                              param: str) -> Tuple[Optional[str], str]:
    """(default text, file name) of `parameter <param>` inside `module`."""
    head = re.compile(r"\bmodule\s+" + re.escape(module) + r"\b(.*?)\bendmodule\b",
                      re.S)
    decl = re.compile(
        r"\bparameter\b(?:\s+(?:integer|int|longint|shortint|logic|bit|reg|"
        r"signed|unsigned))*\s*(?:\[[^\]]*\]\s*)?" + re.escape(param)
        + r"\s*=\s*([^,;)]+)")
    for f in rtl_files:
        try:
            text = _HDL_COMMENT_RE.sub(" ", Path(f).read_text(errors="replace"))
        except OSError:
            continue
        m = head.search(text)
        if not m:
            continue
        pm = decl.search(m.group(1))
        return (pm.group(1).strip() if pm else None), Path(f).name
    return None, ""


def _int_literal(text: Any) -> Optional[int]:
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text
    m = _INT_LITERAL_RE.match(str(text or ""))
    return int(m.group(1)) if m else None


def _program_state(entry: Dict[str, Any], key: str) -> str:
    """PASS only when the program's run on `key` PASSed under every init."""
    if entry.get("pre"):
        return "NOT_MEASURED"
    arm = (entry.get("arms") or {}).get(key)
    if not isinstance(arm, dict):
        return "NOT_MEASURED"
    state = str(arm.get("state") or "")
    by_init = arm.get("by_init")
    if state == PASS:
        if not isinstance(by_init, list) or not by_init or any(
                not isinstance(x, dict) or x.get("state") != PASS
                for x in by_init):
            return "NOT_MEASURED"
    return state or "NOT_MEASURED"


def _arm_failed(entry: Dict[str, Any], key: str) -> bool:
    """True when the program's run on `key` FAILed, overall or under any init."""
    arm = (entry.get("arms") or {}).get(key)
    if not isinstance(arm, dict):
        return False
    if str(arm.get("state") or "") == FAIL:
        return True
    by_init = arm.get("by_init")
    return isinstance(by_init, list) and any(
        isinstance(x, dict) and x.get("state") == FAIL for x in by_init)


def _l10_case_row(project: Path, case_id: str) -> Optional[Dict[str, Any]]:
    """The project's own current L10 row named `case_id`, or None."""
    import _path_layout as _pl                      # noqa: E402 (lazy sibling)
    doc = _json_object(_pl.generated_docs_dir(project) / "L10_TEST_CASES.json")
    if doc is None:
        return None
    for key in ("test_cases", "cases", "vectors"):
        rows = doc.get(key)
        if not isinstance(rows, list):
            continue
        for r in rows:
            if isinstance(r, dict) and str(
                    r.get("name") or r.get("id") or r.get("case") or ""
            ) == case_id:
                return r
    return None


def isa_units_named(case: Dict[str, Any], units: Iterable[str],
                    xlen: int = 32) -> List[str]:
    """The ISA units `case`'s OWN text names -- the ISA-suite producer's
    `bind_case` rule: the base unit as `RV<xlen><base>` (RV32I), an extension
    by its own multi-letter token (Zifencei); a single-letter extension is
    never matched from prose."""
    text = " ".join(str(case.get(k) or "") for k in
                    ("stimulus", "expected", "description", "name"))
    out = []
    for u in units:
        if len(u) == 1 and u.upper() == "I":
            if re.search(rf"(?i)(?<![A-Za-z0-9])RV{xlen}I(?![A-Za-z])", text):
                out.append(u)
        elif len(u) > 1 and re.search(
                rf"(?i)(?<![A-Za-z0-9]){re.escape(u)}(?![A-Za-z0-9])", text):
            out.append(u)
    return out


def isa_goal_verdict(stated_pct: Any, dimension: Optional[str],
                     credit: Dict[str, Any]
                     ) -> Tuple[Optional[str], Optional[float], str]:
    """`(verdict, achieved_pct, why)` for an instruction-coverage GOAL the
    credit supplies a number for -- the ONE judgment both consumers apply.

    PASS only when the suite's achieved instruction coverage reaches the
    percentage the design states; FAIL when it falls short; `(None, None,
    why)` -- no credit, the goal keeps its verdict -- when the goal is not
    stated over the instruction dimension, states no percentage, or the
    credit carries no instruction count."""
    if dimension != "instruction":
        return None, None, (f"the goal is stated over the "
                            f"{dimension or 'no bound'} dimension, not "
                            f"instruction coverage, so the conformance suite "
                            f"supplies no number for it")
    ins = credit.get("instructions") or {}
    if not ins.get("total") or isinstance(stated_pct, bool) \
            or not isinstance(stated_pct, (int, float)):
        return None, None, ("the conformance receipt carries no instruction "
                            "count for this goal")
    achieved = 100.0 * ins["covered"] / ins["total"]
    verdict = PASS if achieved >= stated_pct else FAIL
    return verdict, achieved, (
        f"instruction coverage {achieved:g}% ({ins['covered']}/{ins['total']}) "
        f"vs the {stated_pct:g}% the design states")


def isa_goal_judgment(case: Dict[str, Any], credit: Dict[str, Any]
                      ) -> Optional[Tuple[Optional[str], Optional[float], str]]:
    """`isa_goal_verdict` for a declared L10 row, or None when the row is not a
    coverage GOAL (a stated vector is satisfied by the credit itself). The
    goal's stated percentage and dimension are read with the SAME classifier
    the Step-4 goal population is measured by."""
    import l10_coverage_goal_classify as _cgc       # noqa: E402 (lazy sibling)
    if _cgc.classify(case)[0] != _cgc.COVERAGE_GOAL:
        return None
    stated, _cite = _cgc.stated_acceptance_percentage(case)
    dim, _why = _cgc.bind_scope(_cgc.coverage_scope(case)[0])
    return isa_goal_verdict(stated, dim, credit)


def isa_conformance_credit(project: Path, case_id: str,
                           case: Optional[Dict[str, Any]] = None,
                           ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """`(credit, refusal)` for one declared L10 case; see the block above.

    `(None, None)` when no receipt exists or it binds no ISA unit to the case;
    `(None, why)` when it binds one and the rule is not met; `(credit, None)`
    when it is -- `credit["sentence"]` is the disclosure the verdict carries.
    `case` is the case's L10 row; when not given it is read from the
    project's own L10 declaration by name.
    """
    project = Path(project)
    receipt_path = project / ISA_RECEIPT_REL
    if not receipt_path.is_file():
        return None, None
    doc = _json_object(receipt_path)
    if doc is None:
        return None, None
    bound = doc.get("bound_cases")
    units = bound.get(case_id) if isinstance(bound, dict) else None
    if not (isinstance(units, list) and units
            and all(isinstance(u, str) and u for u in units)):
        return None, None
    if doc.get("schema") != ISA_RECEIPT_SCHEMA:
        return None, (f"{ISA_RECEIPT_REL} schema {doc.get('schema')!r} is not "
                      f"{ISA_RECEIPT_SCHEMA!r}")
    if doc.get("refusal"):
        return None, f"the ISA-suite producer refused: {doc.get('refusal')}"
    if doc.get("executor_rc") != 0:
        return None, (f"the ISA-suite run did not complete "
                      f"(executor_rc={doc.get('executor_rc')!r})")
    facts = doc.get("design_facts")
    if not isinstance(facts, dict):
        return None, "the receipt records no design facts"

    # BOUND -- the receipt's binding is re-derived from the case's own text.
    if not isinstance(case, dict):
        case = _l10_case_row(project, case_id)
    if not isinstance(case, dict):
        return None, (f"the receipt binds {case_id!r}, which is not a row of "
                      f"the project's L10 declaration, so the binding cannot "
                      f"be re-derived from the case's own text")
    xlen = _int_literal(facts.get("xlen")) or 32
    named = isa_units_named(case, units, xlen)
    unnamed = [u for u in units if u not in named]
    if unnamed:
        return None, (f"the receipt binds {case_id!r} to {units}, but the "
                      f"case's own L10 text names "
                      f"{named if named else 'no ISA unit'} (not "
                      f"{', '.join(unnamed)}): a stale or foreign binding "
                      f"credits nothing")
    import testbench_gen as _tbg                    # noqa: E402 (lazy sibling)
    images = _tbg.named_stimulus_images(str(case.get("stimulus") or ""))
    if images:
        return None, (f"a firmware row: its stimulus names "
                      f"{', '.join(images)}, a program the ISA suite did not "
                      f"run (R-0929-OWNER-SUB-ACCEPT (2) decides it, never "
                      f"(1))")
    top, param = facts.get("top"), facts.get("memsize_param")
    delivered = _int_literal(facts.get("memsize_declared"))
    test = _int_literal(doc.get("memsize_full"))
    if not (isinstance(top, str) and top and isinstance(param, str) and param
            and delivered and test):
        return None, ("the receipt does not name its top, its memory-size "
                      "parameter, the delivered value and the test value")

    decl = _json_object(project / _DECLARATION_REL) or {}
    decl_top = decl.get("top_module")
    if decl_top != top:
        return None, (f"a different top: the suite instantiated {top!r}, the "
                      f"design declares top_module {decl_top!r} "
                      f"({_DECLARATION_REL})")

    import _path_layout as _pl                      # noqa: E402 (lazy sibling)
    rtl_dir = _pl.rtl_dir(project)
    rtl_files = sorted(p for p in rtl_dir.glob("*")
                       if p.is_file() and p.suffix in _HDL_SUFFIXES) \
        if rtl_dir.is_dir() else []
    if not rtl_files:
        return None, f"no delivered RTL under {rtl_dir}"
    delivered_sha = {p.name: file_sha256(p) for p in rtl_files}
    arm_sha = doc.get("arm_rtl_sha256")
    arm_sha = arm_sha if isinstance(arm_sha, dict) else {}
    arm = next((a for a in (doc.get("arms") or [])
                if isinstance(a, str) and arm_sha.get(a) == delivered_sha), None)
    if arm is None:
        diffs = []
        for a in doc.get("arms") or []:
            got = arm_sha.get(a) if isinstance(arm_sha.get(a), dict) else {}
            changed = sorted(n for n in set(got) | set(delivered_sha)
                             if got.get(n) != delivered_sha.get(n))
            diffs.append(f"arm {a!r}: {len(changed)} file(s) differ "
                         f"({', '.join(changed[:4])})")
        return None, ("an RTL change: no arm of the suite ran the delivered "
                      f"RTL ({len(delivered_sha)} file(s) under "
                      f"{rtl_dir.name}/) byte for byte"
                      + (f" -- {'; '.join(diffs)}" if diffs else ""))

    default_text, top_file = _module_parameter_default(rtl_files, top, param)
    if default_text is None:
        return None, (f"{param!r} is not a parameter of {top!r} in the "
                      f"delivered RTL" + (f" ({top_file})" if top_file else
                                          " (no file defines the module)"))
    if _int_literal(default_text) != delivered:
        return None, (f"the delivered value of {param!r} is {default_text!r} in "
                      f"{top_file}, not the receipt's {delivered}")
    core = decl.get("core_parameters")
    if isinstance(core, dict) and param in core \
            and _int_literal(core.get(param)) != delivered:
        return None, (f"the declaration states {param}={core.get(param)!r}, "
                      f"not the receipt's delivered {delivered}")

    programs = doc.get("programs")
    programs = programs if isinstance(programs, dict) else {}
    key = f"{arm}_full"
    mine = {pid: e for pid, e in programs.items()
            if isinstance(e, dict) and e.get("unit") in units}
    primary = {pid: e for pid, e in mine.items() if e.get("role") == "primary"}
    if not primary:
        return None, f"the receipt carries no primary program for {units}"
    failed = sorted(pid for pid, e in mine.items()
                    if _program_state(e, key) == FAIL or _arm_failed(e, key))
    if failed:
        return None, (f"a failing suite: {len(failed)} program(s) FAILed at "
                      f"{param}={test} ({', '.join(failed[:4])})")
    dkey = f"{arm}_delivered"
    failed_delivered = sorted(pid for pid, e in mine.items()
                              if _arm_failed(e, dkey))
    if failed_delivered:
        return None, (f"a failing delivered configuration: "
                      f"{len(failed_delivered)} program(s) FAILed at the "
                      f"delivered {param}={delivered} "
                      f"({', '.join(failed_delivered[:4])})")
    short = sorted(pid for pid, e in primary.items()
                   if _program_state(e, key) != PASS)
    if short:
        return None, (f"{len(short)} of {len(primary)} primary program(s) did "
                      f"not PASS at {param}={test} ({', '.join(short[:4])})")

    summary = (doc.get("cases") or {}).get(case_id) \
        if isinstance(doc.get("cases"), dict) else None
    summary = summary if isinstance(summary, dict) else {}
    full = summary.get("full_parameter")
    full = full if isinstance(full, dict) else {}
    if full.get("passed") != len(primary) \
            or full.get("primary_programs") != len(primary) \
            or _int_literal(full.get("memsize")) != test:
        return None, ("the receipt is inconsistent with its own program rows: "
                      f"it states {full.get('passed')!r}/"
                      f"{full.get('primary_programs')!r} at memsize "
                      f"{full.get('memsize')!r}; the rows give "
                      f"{len(primary)}/{len(primary)} at {test}")
    instructions = None
    cov = full.get("coverage")
    if isinstance(cov, dict):
        total_rec = doc.get("instruction_total")
        total_rec = total_rec if isinstance(total_rec, dict) else {}
        excluded = sorted(str(x) for x in total_rec.get("excluded") or [])
        total = _int_literal(total_rec.get("total"))
        covered = sorted({str(e.get("instruction")) for e in primary.values()
                          if e.get("instruction")
                          and str(e.get("instruction")) not in excluded})
        if not total or cov.get("covered") != len(covered) \
                or cov.get("total") != total or len(covered) > total:
            return None, ("the receipt's instruction coverage "
                          f"{cov.get('covered')!r}/{cov.get('total')!r} does not "
                          f"match its program rows ({len(covered)}/{total!r})")
        instructions = {"covered": len(covered), "total": total,
                        "excluded": excluded,
                        "why_excluded": total_rec.get("why")}

    # The delivered-memory run, recomputed from the program rows (never the
    # receipt's summary words): PASS only when every primary program PASSed
    # at the delivered value; no FAIL reaches here (refused above).
    d_passed = sum(1 for e in primary.values()
                   if _program_state(e, dkey) == PASS)
    delivered_run = {"verdict": PASS if d_passed == len(primary)
                     else "NOT_MEASURED",
                     "passed": d_passed,
                     "primary_programs": len(primary),
                     "memsize": delivered}
    dsum = summary.get("delivered")
    dsum = dsum if isinstance(dsum, dict) else {}
    if dsum and (dsum.get("passed") != d_passed
                 or dsum.get("primary_programs") != len(primary)):
        return None, ("the receipt is inconsistent with its own program rows: "
                      f"it states the delivered run {dsum.get('passed')!r}/"
                      f"{dsum.get('primary_programs')!r}; the rows give "
                      f"{d_passed}/{len(primary)} at {delivered}")
    suites = sorted({str(e.get("suite")) for e in primary.values()
                     if e.get("suite")})
    disclosures = [str(d) for d in doc.get("deviation_disclosures") or [] if d]
    sentence = (
        f"core ISA conformance at test {param}={test} (delivered {param}="
        f"{delivered}; parameter only, same RTL: {len(delivered_sha)} file(s) "
        f"sha256-identical to {rtl_dir.name}/, top {top}): "
        f"{', '.join(suites)} {len(primary)}/{len(primary)} primary program(s) "
        f"PASS for {', '.join(units)}"
        + (f", {instructions['covered']}/{instructions['total']} instruction(s)"
           if instructions else "")
        + f"; delivered {param}={delivered} run: {delivered_run['verdict']} "
        f"{delivered_run['passed']}/{delivered_run['primary_programs']}"
        + "".join(f"; DISCLOSED {d}" for d in disclosures))
    return {
        "case": case_id,
        "credited_by": ISA_CREDIT_KIND,
        "units": list(units),
        "suites": suites,
        "suite_pins": [str(a) for a in doc.get("acquisition") or []],
        "top": top,
        "parameter": param,
        "delivered_value": delivered,
        "test_value": test,
        "rtl_identity": {"arm": arm, "files": len(delivered_sha),
                         "identical_to": str(rtl_dir.relative_to(project))
                         if rtl_dir.is_relative_to(project) else str(rtl_dir)},
        "programs_passed": len(primary),
        "programs_total": len(primary),
        "instructions": instructions,
        "delivered_run": delivered_run,
        "deviation_disclosures": disclosures,
        "receipt": ISA_RECEIPT_REL,
        "sentence": sentence,
    }, None
