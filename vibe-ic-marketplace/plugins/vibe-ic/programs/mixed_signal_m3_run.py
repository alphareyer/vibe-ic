#!/usr/bin/env python3
"""M3's current-input producer boundary, with honest unavailable coverage.

ENFORCEMENT: blocking in M3, advisory to the independent digital track.
This lane cannot qualify A9's A3-netlist observer as a post-layout wrapper
simulation, or si_signoff_timing_aware's advisory screen as noise signoff.
Until those native integrations exist, production writes NOT_MEASURED and
returns 2. Audit returns 1 (never the compliance engine's vacuous rc=2).
No simulation, measurement, tool version or successful command is invented.
--check-only reads existing bytes; it never produces the required outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from _atomic_artefact import write_json
from l_doc_consumer_contract import load_l_doc, l_doc_fields

PROGRAM = "mixed_signal_m3_run"
SCHEMA = "vibeic.mixed_signal.m3.unmeasured.v1"
DIR = "reports/analog/mixed_signal"
COSIM = "phase3/mixed_signal/cosim/mixed_signal_results.json"
SI = DIR + "/interface_si.json"
RECEIPT = DIR + "/m3_run.json"
OUTPUTS = {"cosim": COSIM, "si": SI}


class Refusal(ValueError):
    def __init__(self, rule, message):
        self.rule = rule
        super().__init__(message)


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def read(path):
    try:
        obj = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Refusal("MISSING_OR_MALFORMED_EVIDENCE", str(path)) from exc
    if not isinstance(obj, dict):
        raise Refusal("MALFORMED_EVIDENCE", str(path))
    return obj


def subject(project, top):
    if not isinstance(top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise Refusal("INVALID_DESIGN", "an explicit top module is required")
    return {"project": str(project.resolve()), "top": top}


def inputs(project, top):
    """Current design inputs only; outputs/audit files never enter this set."""
    subject(project, top)
    rels = {"phase1/analog/analog_block_list.json",
            "phase3/mixed_signal/top_merged.gds",
            f"phase3/stage3/pnr/{top}_pnr.v", "phase3/stage3/pnr/routed.def",
            f"phase2/stage2/constraints/{top}.sdc"}
    l22, _ = load_l_doc(project, "L22")
    if l22:
        rels.add(str(l22.resolve().relative_to(project.resolve())))
    for pattern in ("phase3/stage3/pnr/**/*.spef", "input/pdk/liberty/**/*.lib",
                    "phase3/analog/**/*.lib", "phase3/analog/**/*.v",
                    "phase3/analog/**/*.sp", "phase3/analog/**/*.gds",
                    "phase3/analog/**/a7_post_layout.json",
                    "phase3/analog/**/layout_provenance.json",
                    "phase3/analog/**/pre_vs_post.json",
                    "phase3/librelane/analog/**/a7_resim/*.sp"):
        rels.update(str(p.relative_to(project)) for p in project.glob(pattern) if p.is_file())
    bound = {}
    for rel in sorted(rels):
        path = project / rel
        if not path.resolve().is_relative_to(project.resolve()):
            raise Refusal("FOREIGN_INPUT", rel)
        bound[rel] = digest(path) if path.is_file() else None
    # Missing L22 must also be distinguishable from a newly supplied plan.
    return bound


def plan(project):
    _, doc = load_l_doc(project, "L22")
    value = l_doc_fields(doc).get("verification_plan", {}) if doc else {}
    rows = value.get("cosim_scenarios", []) if isinstance(value, dict) else []
    return rows if isinstance(rows, list) else []


def produce(project, top):
    who = subject(project, top)
    for rel in (*OUTPUTS.values(), RECEIPT):
        if not (project / rel).resolve().is_relative_to(project.resolve()):
            raise Refusal("FOREIGN_OUTPUT", rel)
    before = inputs(project, top)
    rows = plan(project)
    missing = [rel for rel, sha in before.items() if sha is None]
    common = {"producer": PROGRAM, "schema": SCHEMA, **who,
              "verdict": "NOT_MEASURED", "execution": [], "tool_versions": {},
              "missing_inputs": missing}
    cosim = {**common, "all_scenarios_passed": False,
             "declared_scenarios": rows, "scenarios": [],
             "blockers": [
                 "M3_CURRENT_WRAPPER_COSIM_UNAVAILABLE: A9 composes A3 decks and an observer; "
                 "no qualified coupling of current A7 extraction to the current digital wrapper",
                 *([] if rows else ["M3_SCENARIO_PLAN_ABSENT: no current L22 scenarios"])]}
    si = {**common, "all_interfaces_clean": False, "interfaces": [],
          "coverage": {k: "NOT_MEASURED" for k in ("timing", "noise", "crosstalk")},
          "blockers": [
              "M3_NATIVE_INTERFACE_SI_UNAVAILABLE: OpenSTA/SPEF advisory screening has "
              "no qualified hardmacro receiver/noise model coverage",
              *([] if any(r.endswith(".spef") for r in before) else ["M3_POST_ROUTE_SPEF_ABSENT"]),
              *([] if any(r.startswith("phase3/analog/") and r.endswith(".lib") for r in before)
                else ["M3_HARDMACRO_LIBERTY_ABSENT"])]}
    if inputs(project, top) != before:
        raise Refusal("STALE_INPUT", "inputs changed during production")
    # Withdraw old evidence, including A9's legacy aggregate, before publishing
    # the explicitly unmeasured replacement. Receipt appears last.
    for rel in (*OUTPUTS.values(), RECEIPT):
        (project / rel).unlink(missing_ok=True)
    for rel, data in ((COSIM, cosim), (SI, si)):
        write_json(project / rel, data)
    record = {"program": PROGRAM, "schema": SCHEMA, **who,
              "verdict": "NOT_MEASURED", "inputs": before,
              "outputs": {rel: digest(project / rel) for rel in OUTPUTS.values()},
              "execution": [], "tool_versions": {},
              "blockers": cosim["blockers"] + si["blockers"]}
    if inputs(project, top) != before:
        raise Refusal("STALE_INPUT", "inputs changed before receipt publication")
    write_json(project / RECEIPT, record)
    return record


def verify(project, kind):
    """Validate exact local bytes before reporting the unavailable capability.

    This schema represents only unmeasured production. Re-hashing an asserted
    PASS, native-looking transcript, or fixture cannot extend its capability.
    """
    record = read(project / RECEIPT)
    if record.get("schema") != SCHEMA or record.get("program") != PROGRAM:
        raise Refusal("WRONG_PRODUCER", "no M3 current-input production")
    if record.get("project") != str(project.resolve()):
        raise Refusal("WRONG_PROJECT", "receipt belongs to another project")
    top = record.get("top")
    if inputs(project, top) != record.get("inputs"):
        raise Refusal("STALE_INPUT", "current M3 inputs differ from production")
    if set(record.get("outputs", {})) != set(OUTPUTS.values()):
        raise Refusal("WRONG_OUTPUT_PATH", "M3 output binding must name both canonical files")
    for rel, sha in record["outputs"].items():
        path = project / rel
        if (not path.resolve().is_relative_to(project.resolve()) or
                not path.is_file() or digest(path) != sha):
            raise Refusal("STALE_OUTPUT", rel)
    data = read(project / OUTPUTS[kind])
    if any(data.get(k) != v for k, v in {"producer": PROGRAM, "schema": SCHEMA,
                                       **subject(project, top)}.items()):
        raise Refusal("WRONG_DESIGN", "output producer/design differs from receipt")
    rows = data.get("scenarios" if kind == "cosim" else "interfaces")
    if not isinstance(rows, list) or not rows:
        raise Refusal("EMPTY_SCENARIOS" if kind == "cosim" else "EMPTY_INTERFACES",
                      "no executed scenarios/measurements")
    if any(not isinstance(row, dict) or row.get("status") == "FAIL" or
           row.get("verdict") == "FAIL" for row in rows):
        raise Refusal("FAILED_MEASUREMENT", "a measured result failed or is malformed")
    execution = record.get("execution")
    if not isinstance(execution, list) or not execution:
        raise Refusal("MISSING_EXECUTION", "no native simulation/timing execution")
    if not any(isinstance(e, dict) and e.get("stage") == "measurement" for e in execution):
        raise Refusal("SYNTAX_ONLY", "compilation is not measurement")
    if any(e.get("exit_code") != 0 for e in execution if isinstance(e, dict)):
        raise Refusal("NATIVE_EXECUTION_FAILED", "native execution did not succeed")
    # No positive schema exists in this bounded lane. Neither command metadata
    # nor report booleans can impersonate the missing native integrations.
    raise Refusal("M3_NATIVE_INTEGRATION_NOT_VERIFIED",
                  "current wrapper co-simulation / qualified interface SI is unavailable")


def audit(project, kind):
    report = {"program": PROGRAM, "verdict": "NOT_VERIFIED", "kind": kind,
              "passed": False, "ready": False, "findings": []}
    try:
        verify(project, kind)
    except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        report["findings"].append({"rule": getattr(exc, "rule", "INVALID_EVIDENCE"),
                                   "message": str(exc)})
    path = project / OUTPUTS[kind]
    report["output_sha256"] = digest(path) if path.is_file() else None
    return report


def guard_audit_path(project, output, protected):
    if output:
        path = Path(output).resolve()
        if path.is_relative_to(project.resolve()):
            rel = path.relative_to(project.resolve())
            if rel.parts and rel.parts[0] in ("phase1", "phase2", "phase3", "input", "input_doc"):
                raise Refusal("AUDIT_OVERWRITES_EVIDENCE", "audit cannot write design/native input trees")
        if path in {(project / rel).resolve() for rel in protected}:
            raise Refusal("AUDIT_OVERWRITES_EVIDENCE", "audit cannot create or replace evidence")


def audit_output(project, output, report, protected):
    guard_audit_path(project, output, protected)
    if output:
        path = Path(output).resolve()
        write_json(path, report)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--top", required=False)
    ap.add_argument("--container", default="host", help="reserved native execution context; no tool launched")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--kind", choices=OUTPUTS, default="cosim")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    project = args.project.resolve()
    try:
        top = args.top
        if args.check_only and (project / RECEIPT).is_file():
            top = read(project / RECEIPT).get("top")
        protected = (*OUTPUTS.values(), RECEIPT, *inputs(project, top)) if top else (
            *OUTPUTS.values(), RECEIPT, "phase1/analog/analog_block_list.json",
            "phase3/mixed_signal/top_merged.gds")
        guard_audit_path(project, args.json, protected)
        report = audit(project, args.kind) if args.check_only else produce(project, args.top)
        rc = 1 if args.check_only else 2
        audit_output(project, args.json, report, protected)
    except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[{PROGRAM}] {getattr(exc, 'rule', 'INVALID_EVIDENCE')}: {exc}", file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
