#!/usr/bin/env python3
"""
gate_evidence_completeness_check.py — v0.100 L1

ENFORCEMENT: advisory

The line above is a DECLARATION, in the anchored form `flow_gate_enforcement_
audit.declared_intent` reads. This program is wired into the flow as an
`advisory_program_exit_zero` clause: it RUNS on every project that reaches its
step, its findings are printed, and its exit code cannot deny the step its PASS
tier. That is deliberate — it was wired to make a real check reachable, not to
block a landing on debt it did not create — and the declaration says so where
the audit looks. Without it, "wired where it cannot block" and "nobody decided"
are the same record, and the reliable way to stay clean is to say nothing.
Every gate listed as PASS in a FINAL_REPORT.md (or flow compliance JSON)
must have a corresponding evidence file (stdout dump, JSON output, or log).

Without evidence, a PASS claim is unauditable. This program flags gates
that claim PASS but have no backing artefact.

Usage:
    python3 gate_evidence_completeness_check.py <project_dir>
    python3 gate_evidence_completeness_check.py <project_dir> --json report.json
    python3 gate_evidence_completeness_check.py <project_dir> --report FINAL_REPORT.md

Exit codes:
    0  every PASS gate in the report has evidence — or the report was READ and
       claims no PASS gates at all. An empty artefact is not a missing one.
    1  GAPS FOUND: the report claims a PASS the run has no artefact for.
    2  NOT CHECKED: the question could not be put — the project dir is not a
       directory, the report is absent, or it is present and unparseable.

WHY 2 AND NOT 1 FOR AN ABSENT REPORT (measured 2026-08-31)
=========================================================
This program used to print `FAIL: nothing to audit` and return 1 when neither a
FINAL_REPORT.md nor a flow-compliance JSON existed. That contradicted the line
directly above it: rc 1 is defined here as "gaps found", and an absent report is
not a gap — it is the same I/O condition the two neighbouring branches already
route to 2 (`not a directory`, and `cannot parse JSON`). The absent-report
branch was the only one of the three that answered a question it had never
asked, and it answered it with the word FAIL.

Measured on a real completed run tree (spm x sky130): rc 1, and the flow's
advisory slot recorded it as `__ADVISORY_HINT__FINDING`, i.e. a finding against
a design this program had not read one byte of. `gate_zero_denominator_refuses_
check` already names this class -- "a gate ... returning a verdict about a
design it had not read" -- and lists `fpga_qsf_lint "ERROR: QSF file not found"
rc 1` as one of its three worked examples. That program is deliberately only the
PROBE and says each fix is its own measured change. This is that change, for
this one gate; no other gate's behaviour is touched.

The rc-0 branch below is deliberately NOT changed with it. A report that was
read and states no PASS claims is a real result over a real artefact, and rc 0
is correct for it -- the distinction this repo states as "an empty artefact is
not a missing one".
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


import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import _path_layout as _pl
import _vacuous_exit as _vx


#: The completion audit's own record. R-0915-46: this file is the run's
#: register of which gates claimed what, and it was NOT in the candidate list
#: below -- so on a real run tree `find_report` returned None and this check
#: booked INCOMPLETE for the absence of an artefact produced by the very audit
#: it is a component of. Circular: on a first run it could never be satisfied.
#: MEASURED on spm x gf180mcuD: `find_report(spm23)` -> None, while
#: `reports/audit/phase23_completion_audit.json` sat on disk with a
#: `gate_execution_ledger` of 246 entries.
COMPLETION_AUDIT_RECORD = "audit/phase23_completion_audit.json"


def find_report(project: Path) -> Optional[Path]:
    """Locate the completion audit record, the flow compliance JSON, or
    FINAL_REPORT.md.

    The audit record comes FIRST: it is this run's own register of gate
    verdicts, it is the population the completion audit is building, and it is
    the one artefact that exists on every completed run.
    """
    candidates = [
        _pl.report_path(project, COMPLETION_AUDIT_RECORD),
        project / "reports" / "audit" / "phase23_completion_audit.json",
        _pl.report_path(project, "flow_compliance.json"),
        project / "FINAL_REPORT.md",
        _pl.report_path(project, "FINAL_REPORT.md"),
    ]
    for c in candidates:
        if c.exists():
            return c
    for p in project.rglob("flow_compliance*.json"):
        return p
    for p in project.rglob("FINAL_REPORT*"):
        return p
    return None


_JSON_FLAG_RE = re.compile(r"--json[=\s]+(\S+)")


def declared_json_outputs(path: Path) -> Dict[str, Optional[str]]:
    """gate -> the `--json <path>` its OWN ledger row declares, or None.

    R-0915-46, third and final half, and the one that keeps this check
    honest. MEASURED on the SPM verdict candidate the moment the audit's
    full-scope record was read instead of a stage-scoped one: **146 PASS
    gates, 16 without evidence** -- `constants_validation`, `oracle_vector_gen`,
    `integration_spec_audit`, `spec_review_lint` and twelve more. A
    `find`-sweep of the whole run tree turns up NOTHING for any of them,
    because those gates are invoked WITHOUT `--json`: they print a verdict and
    the audit records it. They are working exactly as designed.

    So "a PASS claim needs a file" is the wrong rule for them, and applying it
    would be a FALSE FAIL over 16 correct gates. The rule that has teeth and
    is not vacuous is the one the flow already uses elsewhere: a gate whose
    own command NAMES an artefact must have produced it. A gate that names
    none is evidenced by its ledger row, which carries the command and the
    exit code -- a record of execution, not a self-assertion.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    ledger = data.get("gate_execution_ledger")
    if not isinstance(ledger, list):
        return {}
    out: Dict[str, Optional[str]] = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        name = row.get("gate")
        if not isinstance(name, str) or not name.strip():
            continue
        m = _JSON_FLAG_RE.search(str(row.get("cmd") or ""))
        out[name.strip()] = m.group(1) if m else None
    return out


def extract_pass_gates_from_json(path: Path) -> List[str]:
    """Extract the names that claim PASS from a flow/audit JSON.

    Two shapes, because two producers write one:
      * `steps[]` with `status` — the flow-compliance shape, unchanged;
      * `gate_execution_ledger[]` with `gate` and `verdict` — the completion
        audit's shape. Preferred when present, because this check is about
        GATES and the ledger names gates, where `steps` names steps.
    """
    data = json.loads(path.read_text())
    gates = []
    ledger = data.get("gate_execution_ledger")
    if isinstance(ledger, list) and ledger:
        for row in ledger:
            if not isinstance(row, dict):
                continue
            if str(row.get("verdict", "")).strip().upper() != "PASS":
                continue
            name = row.get("gate")
            if isinstance(name, str) and name.strip():
                gates.append(name.strip())
        return gates
    for step in data.get("steps", []):
        if step.get("status") == "PASS":
            name = step.get("name", f"step_{step.get('id', '?')}")
            gates.append(name)
    return gates


def extract_pass_gates_from_md(path: Path) -> List[str]:
    """Extract gate names that claim PASS from markdown report."""
    gates = []
    text = path.read_text(errors='replace')
    for m in re.finditer(r'[|✓✅]\s*(?:PASS)\s*[|]\s*(.+?)(?:\s*[|(])', text):
        gates.append(m.group(1).strip())
    for m in re.finditer(r'^[-*]\s+\**([\w_\s]+?)\**\s*[:—–-]\s*PASS', text, re.MULTILINE):
        gates.append(m.group(1).strip())
    for m in re.finditer(r'PASS\s*[-—:]\s*([\w_\s]+)', text):
        gates.append(m.group(1).strip())
    return gates


def collect_evidence_files(project: Path) -> Set[str]:
    """Collect all evidence file stems in standard locations.

    R-0915-46, second half. The locator globbed `reports/gates/*.json`, but
    THIS FLOW WRITES `reports/phase2/gates/*.json` and
    `reports/phase3/gates/*.json` -- so every per-gate report a run produces
    was invisible to it. MEASURED on spm x gf180mcuD the moment the circular
    report-lookup above was fixed: 17 PASS gates, "3 with evidence, 14
    without", naming `yosys_hilomap_required_check` among them while
    `reports/phase2/gates/yosys_hilomap.json` sat on disk. Fixing the lookup
    alone would have converted a circular INCOMPLETE into a FALSE FAIL, which
    is worse -- so the phase-scoped directories are globbed too.

    The matcher is untouched: a gate with no file anywhere still fails.
    """
    evidence = set()
    for pattern in [
        # phase-scoped, which is where this flow actually writes
        "reports/*/gates/*.json",
        "reports/*/gates/*.log",
        "reports/*/gates/*.txt",
        "reports/*/*.json",
        "reports/*/*.rpt",
        # A gate whose product is a TRANSCRIPT rather than a JSON row: the
        # audit's own `reports/audit/flow_compliance_check.log` is the
        # evidence for the `flow_compliance_check` gate, which writes no
        # per-gate JSON because its record IS the audit. Without this the
        # check reports the audit as the one unevidenced PASS in its own
        # audit -- the same circularity one level down.
        "reports/*/*.log",
        "reports/*/*/*.json",
        "reports/gates/*.json",
        "reports/gates/*.log",
        "reports/gates/*.txt",
        "reports/*.json",
        "reports/*.log",
        "reports/sta/*.json",
        "reports/dft/*.json",
        "reports/pnr/*.json",
        "reports/drc*.json",
        "reports/lvs*.json",
        "reports/ir_drop*.json",
        "reports/em*.json",
        "reports/power*.json",
        "sim*/results.xml",
        "phase2/stage2/dft/*.rpt",
        "phase3/stage3/sta/*.rpt",
    ]:
        for f in project.glob(pattern):
            evidence.add(f.stem.lower())
            evidence.add(f.name.lower())

    return evidence


def gate_has_evidence(gate_name: str, evidence: Set[str]) -> bool:
    """Check if a gate name matches any evidence file."""
    norm = re.sub(r'[\s()\-–—]+', '_', gate_name).lower().strip('_')
    words = [w for w in re.split(r'[\s_]+', norm) if len(w) > 2]

    for e in evidence:
        if norm in e or e in norm:
            return True

    for e in evidence:
        matched = sum(1 for w in words if w in e)
        if matched >= max(1, len(words) * 0.5):
            return True

    return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("project_dir", help="Project directory to audit")
    p.add_argument("--report", help="Override path to FINAL_REPORT.md or flow compliance JSON")
    p.add_argument("--json", help="Write JSON report to this path")
    args = p.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"gate_evidence_completeness_check: not a directory: {project}",
              file=sys.stderr)
        return 2

    report_path = Path(args.report) if args.report else find_report(project)
    if not report_path or not report_path.exists():
        # NOT CHECKED, not FAIL. Nothing was read, so there is no design to
        # return a verdict about. The `VACUOUS_PASS:` sentinel is the token
        # `flow_compliance_check._stdout_signals_vacuous` matches, so the
        # advisory slot records this as `n/a (input not present)` instead of
        # as a FINDING against an unread design.
        reason = "no FINAL_REPORT.md or flow-compliance JSON in this run"
        _vx.announce_vacuous("gate_evidence_completeness_check", reason)
        print(_vx.verdict_line("gate_evidence_completeness_check",
                               passed=True, skipped=True, reason=reason))
        return _vx.exit_code(passed=True, skipped=True)

    if report_path.suffix == '.json':
        try:
            pass_gates = extract_pass_gates_from_json(report_path)
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"gate_evidence_completeness_check: cannot parse JSON: {exc}",
                  file=sys.stderr)
            return 2
    else:
        pass_gates = extract_pass_gates_from_md(report_path)

    if not pass_gates:
        print("gate_evidence_completeness_check: no PASS gates found in report")
        print("PASS: nothing to check (no PASS claims)")
        return 0

    evidence = collect_evidence_files(project)
    # The artefact each gate's OWN command declares. Empty for a legacy
    # report; populated when the report is the audit's ledger.
    declared = declared_json_outputs(report_path)

    with_evidence = []
    without_evidence = []
    unfiled = []
    for gate in pass_gates:
        if gate in declared:
            named = declared[gate]
            if named is None:
                # The gate declares no artefact, so a file is not what its
                # PASS rests on -- its ledger row is, and the row exists by
                # construction of this list. Counted and reported, never
                # failed: demanding a file here would FAIL 16 correctly
                # designed gates on this very run.
                unfiled.append(gate)
                continue
            if (project / named).is_file():
                with_evidence.append(gate)
            else:
                without_evidence.append(f"{gate} (declared {named}, not on disk)")
            continue
        if gate_has_evidence(gate, evidence):
            with_evidence.append(gate)
        else:
            without_evidence.append(gate)

    print(f"Report: {report_path.name}")
    print(f"PASS gates: {len(pass_gates)} total, "
          f"{len(with_evidence)} with evidence, "
          f"{len(without_evidence)} without evidence"
          + (f", {len(unfiled)} declaring no artefact (evidenced by their "
             f"ledger row)" if unfiled else ""))

    if without_evidence:
        print(f"\nMissing evidence for:")
        for g in without_evidence:
            print(f"  - {g}")
        verdict = "FAIL"
    else:
        verdict = "PASS"

    print(f"\n{verdict}: gate_evidence_completeness_check")

    if args.json:
        report = {
            "program": "gate_evidence_completeness_check",
            "report_path": str(report_path),
            "total_pass_gates": len(pass_gates),
            "declaring_no_artefact": len(unfiled),
            "with_evidence": len(with_evidence),
            "without_evidence_count": len(without_evidence),
            "without_evidence_items": without_evidence,
            "verdict": verdict,
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2))

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
