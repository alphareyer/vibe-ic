#!/usr/bin/env python3
"""mixed_signal_cosim_check.py — deterministic gate for mixed-signal co-simulation

Validates that mixed-signal co-simulation was performed for each analog
block connected to digital logic. Checks for cosim result files and
validates simulation outcomes.

SUBSTANCE VERIFICATION (M3-cosim-si, anti-fabrication)
------------------------------------------------------
This gate no longer trusts the self-produced ``all_scenarios_passed``
boolean. It independently parses the aggregate co-sim report
``phase3/mixed_signal/cosim/mixed_signal_results.json`` and walks EVERY
declared scenario, verifying that each one carries a real PASS status
string (PASS / passed / OK / true). The gate FAILs when:

  * the aggregate report is present but declares zero scenarios
    (a vacuous "all passed" with nothing actually simulated), OR
  * any single scenario is not a real pass (failed / error / unknown /
    missing status), OR
  * a block declared in analog_block_list.json has neither an aggregate
    scenario nor a per-block cosim result and is not a deterministic
    stub.

When analog blocks exist but NO co-sim substance is present at all (no
aggregate file AND no per-block files AND not stubs) the gate FAILs
(rc=1) — it never vacuous-PASSes on absence.

THE DENOMINATOR IS L22's, NOT THE PRODUCER'S (q5)
------------------------------------------------
Walking "every declared scenario" of the producer's own report let the
producer decide how many scenarios there were. When Phase 1 has written
``L22.verification_plan.cosim_scenarios`` (the key is present), that list is
the denominator:

  * a declared id with no result                → FAIL COSIM_SCENARIO_MISSING
  * a result whose verdict is FAIL / unreadable → FAIL
  * a result NOT_MEASURED or UNBOUNDED          → rc 2 (not verified; never PASS)
  * a result id L22 does not declare            → INFO undeclared_extra, uncounted
  * results stamped with another L22 sha256     → FAIL STALE_PLAN
  * L22 declares ZERO rows for analog blocks    → rc 2, citing cosim_scenario_gaps

A plan written before the key existed keeps the behaviour above unchanged.

Exit codes:
    0 = PASS  (real co-sim substance verified)
    1 = FAIL  (missing-when-required / failed / vacuous scenario set)
    2 = SKIP  (genuinely inapplicable: no analog blocks)  /  IO error

Usage:
    python3 mixed_signal_cosim_check.py <project_dir>
    python3 mixed_signal_cosim_check.py <project_dir> --json reports/gates/cosim.json
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
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional
import _path_layout as _pl
from _analog_stub_marker import is_stub_json  # v1.6.177 (#72 P1-6)


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    file: str = ""
    line: int = 0


@dataclass
class AuditResult:
    program: str = "mixed_signal_cosim_check"
    version: str = "2.0.0"
    passed: bool = True
    # skip == True means the step is genuinely inapplicable (no analog
    # blocks). The CLI maps it to rc=2 (VACUOUS_PASS at the orchestrator)
    # — distinct from passed==False which is an honest FAIL (rc=1).
    skip: bool = False
    # vacuous == True: the step applies and nothing FAILed, but what L22
    # declares was not all verified (NOT_MEASURED / UNBOUNDED / no rows).
    # rc 2 — the not-verified tier, never PASS.
    vacuous: bool = False
    findings: List[Finding] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# Status strings that count as a genuine per-scenario PASS. Anything
# else (failed / error / fail / "" / None / numeric 0) is NOT a pass.
_PASS_TOKENS = frozenset({"pass", "passed", "ok", "true", "success",
                          "succeeded", "p"})
_FAIL_TOKENS = frozenset({"fail", "failed", "error", "err", "false",
                          "no", "x", "fatal", "abort", "aborted"})


def _scenario_is_pass(scn: dict) -> Optional[bool]:
    """Return True iff a scenario dict carries a genuine PASS verdict,
    False for an explicit fail, None when no verdict can be read.

    Verdict is read from (priority order):
      * ``status`` / ``result`` / ``verdict``  — string token
      * ``passed`` / ``pass`` / ``ok``          — boolean
    chip-AGNOSTIC: keyed on field shape, not chip class.
    """
    if not isinstance(scn, dict):
        return None
    # string verdict fields
    for key in ("status", "result", "verdict", "outcome"):
        v = scn.get(key)
        if isinstance(v, str) and v.strip():
            tok = v.strip().lower()
            if tok in _PASS_TOKENS:
                return True
            if tok in _FAIL_TOKENS:
                return False
            # any other non-empty token is an unrecognised verdict →
            # not a proven pass
            return False
    # boolean verdict fields
    for key in ("passed", "pass", "ok"):
        v = scn.get(key)
        if isinstance(v, bool):
            return v
    return None


def _scenario_name(scn: dict, idx: int) -> str:
    if isinstance(scn, dict):
        for key in ("name", "scenario", "id", "test"):
            v = scn.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return f"#{idx}"


def _extract_scenarios(data: dict) -> Optional[list]:
    """Pull the scenario/test array from an aggregate cosim report.
    Returns the list (possibly empty) or None when no recognised array
    key is present at all."""
    if not isinstance(data, dict):
        return None
    for key in ("scenarios", "tests", "cases", "results"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return None


def _audit_aggregate(project: Path, result: AuditResult) -> Optional[dict]:
    """Independently verify EVERY scenario in the aggregate report
    ``phase3/mixed_signal/cosim/mixed_signal_results.json``.

    Returns a per-file summary dict, or None when the aggregate file is
    absent. Does NOT read/echo ``all_scenarios_passed`` — it recomputes
    the verdict from the per-scenario verdicts itself.
    """
    agg = _pl.mixed_signal_cosim_dir(project) / "mixed_signal_results.json"
    if not agg.exists():
        return None
    rel = str(agg)
    try:
        data = json.loads(agg.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        result.findings.append(Finding(
            rule="AGG_PARSE_ERROR",
            severity="ERROR",
            message="Cannot parse aggregate mixed_signal_results.json",
            file=rel,
        ))
        result.passed = False
        return {"file": rel, "pass": False, "reason": "parse_error"}

    if is_stub_json(data):
        result.findings.append(Finding(
            rule="AGG_STUB_ACCEPTED",
            severity="INFO",
            message=("Aggregate mixed_signal_results.json carries a "
                     "deterministic_stub marker (PASS_WITH_STUB tier)."),
            file=rel,
        ))
        return {"file": rel, "pass": True, "stub": True,
                "scenarios_total": 0}

    scenarios = _extract_scenarios(data)
    if scenarios is None:
        result.findings.append(Finding(
            rule="AGG_NO_SCENARIO_ARRAY",
            severity="ERROR",
            message=("Aggregate mixed_signal_results.json has no "
                     "scenarios[]/tests[] array — cannot verify "
                     "substance; refusing vacuous PASS."),
            file=rel,
        ))
        result.passed = False
        return {"file": rel, "pass": False, "reason": "no_scenario_array"}

    if len(scenarios) == 0:
        result.findings.append(Finding(
            rule="AGG_ZERO_SCENARIOS",
            severity="ERROR",
            message=("Aggregate mixed_signal_results.json declares ZERO "
                     "scenarios — nothing was co-simulated; refusing "
                     "vacuous 'all passed'."),
            file=rel,
        ))
        result.passed = False
        return {"file": rel, "pass": False, "reason": "zero_scenarios"}

    passed_n = 0
    failed = []
    unverifiable = []
    scenario_names = []
    for idx, scn in enumerate(scenarios):
        nm = _scenario_name(scn if isinstance(scn, dict) else {}, idx)
        scenario_names.append(nm)
        verdict = _scenario_is_pass(scn if isinstance(scn, dict) else {})
        if verdict is True:
            passed_n += 1
        elif verdict is False:
            failed.append(nm)
            result.findings.append(Finding(
                rule="SCENARIO_FAILED",
                severity="ERROR",
                message=(f"Scenario '{nm}': co-sim verdict is not PASS"),
                file=rel,
            ))
        else:
            unverifiable.append(nm)
            result.findings.append(Finding(
                rule="SCENARIO_NO_VERDICT",
                severity="ERROR",
                message=(f"Scenario '{nm}': no readable PASS/FAIL verdict "
                         f"(status/result/verdict/passed missing) — "
                         f"cannot prove it passed."),
                file=rel,
            ))

    if failed or unverifiable:
        result.passed = False

    result.findings.append(Finding(
        rule="AGG_SCENARIOS_OK" if not (failed or unverifiable)
        else "AGG_SCENARIOS_FAIL",
        severity="INFO" if not (failed or unverifiable) else "ERROR",
        message=(f"Aggregate cosim: {passed_n}/{len(scenarios)} scenarios "
                 f"verified PASS"
                 + (f", {len(failed)} failed" if failed else "")
                 + (f", {len(unverifiable)} unverifiable"
                    if unverifiable else "")),
        file=rel,
    ))

    return {
        "file": rel,
        "pass": not (failed or unverifiable),
        "scenarios_total": len(scenarios),
        "scenarios_passed": passed_n,
        "scenarios_failed": failed,
        "scenarios_unverifiable": unverifiable,
        "scenario_names": scenario_names,
    }


def _load_block_list(project: Path) -> List[str]:
    # The orchestrator gate condition keys on phase1/analog (canonical
    # block-list location); the older runner also wrote it under
    # phase3/analog. Check both so applicability matches the flow gate.
    candidates = [
        project / "phase1/analog/analog_block_list.json",
        _pl.analog_dir(project) / "analog_block_list.json",
    ]
    for bl in candidates:
        if not bl.exists():
            continue
        try:
            data = json.loads(bl.read_text(errors="replace"))
            if isinstance(data, dict) and "blocks" in data:
                return [b["name"] if isinstance(b, dict) else str(b)
                        for b in data["blocks"]]
            if isinstance(data, list):
                return [b["name"] if isinstance(b, dict) else str(b)
                        for b in data]
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    return []


def _scan_spec_blocks(project: Path) -> List[str]:
    analog_dir = _pl.analog_dir(project)
    if not analog_dir.is_dir():
        return []
    return sorted(d.name for d in analog_dir.iterdir()
                  if d.is_dir() and (d / "spec.json").exists())


def _block_is_stub(project: Path, block: str) -> bool:
    """v1.6.177 (#72 P1-6) — return True iff the per-block analog
    spec.json carries the deterministic_stub marker. The M-step
    cosim has no real data to co-simulate when the upstream analog
    block is a stub, so the cosim gate should not FAIL the block in
    that case — it should report PASS_WITH_STUB.
    chip-AGNOSTIC: marker is structural, not chip-class."""
    spec = _pl.analog_dir(project) / block / "spec.json"
    if not spec.exists():
        return False
    try:
        data = json.loads(spec.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return False
    return is_stub_json(data)


_NOT_VERIFIED_TOKENS = frozenset({"not_measured", "unbounded"})


def _l22_cosim_plan(project: Path):
    """(declared rows, gaps, L22 sha256) when L22 declares the key, else None."""
    import hashlib
    from l_doc_consumer_contract import l_doc_fields, load_l_doc
    path, doc = load_l_doc(project, "L22")
    if path is None or doc is None:
        return None
    plan = l_doc_fields(doc).get("verification_plan")
    if not isinstance(plan, dict) or "cosim_scenarios" not in plan:
        return None
    rows = plan.get("cosim_scenarios")
    return ([r for r in rows if isinstance(r, dict)]
            if isinstance(rows, list) else [],
            plan.get("cosim_scenario_gaps") or [],
            hashlib.sha256(path.read_bytes()).hexdigest())


def _audit_against_l22(project: Path, result: AuditResult,
                       declared: list, gaps: list, sha: str) -> dict:
    """Judge the aggregate report against the ids L22 declares."""
    ids = [str(r.get("id")) for r in declared]
    if not ids:
        result.vacuous = True
        result.findings.append(Finding(
            rule="COSIM_NO_DECLARED_SCENARIOS", severity="WARNING",
            message=("L22 declares no co-simulation scenario for a design "
                     "with analog blocks; A9 is not verified. Gaps: "
                     + (", ".join(str(g.get("category")) for g in gaps
                                  if isinstance(g, dict)) or "none recorded")),
        ))
        return {"source": "L22", "declared": [], "gaps": gaps}
    agg = _pl.mixed_signal_cosim_dir(project) / "mixed_signal_results.json"
    rel = str(agg)
    try:
        data = json.loads(agg.read_text(errors="replace"))
    except FileNotFoundError:
        data = None
    except (json.JSONDecodeError, OSError):
        result.passed = False
        result.findings.append(Finding(
            rule="AGG_PARSE_ERROR", severity="ERROR",
            message="Cannot parse aggregate mixed_signal_results.json",
            file=rel))
        return {"source": "L22", "declared": ids, "pass": False}
    if data is not None and is_stub_json(data):
        result.findings.append(Finding(
            rule="AGG_STUB_ACCEPTED", severity="INFO",
            message=("Aggregate mixed_signal_results.json carries a "
                     "deterministic_stub marker (PASS_WITH_STUB tier)."),
            file=rel))
        return {"source": "L22", "declared": ids, "stub": True}
    scenarios = (_extract_scenarios(data) if isinstance(data, dict) else None)
    if scenarios is None:
        scenarios = []
    stamped = {str(s.get("l22_sha256")) for s in scenarios
               if isinstance(s, dict) and s.get("l22_sha256")}
    if isinstance(data, dict) and data.get("l22_sha256"):
        stamped.add(str(data["l22_sha256"]))
    if stamped and stamped != {sha}:
        result.passed = False
        result.findings.append(Finding(
            rule="STALE_PLAN", severity="ERROR",
            message=(f"results were produced against L22 sha256 "
                     f"{sorted(stamped)}; the current L22 is {sha}"),
            file=rel))
    by_id = {}
    for idx, scn in enumerate(scenarios):
        if isinstance(scn, dict):
            by_id.setdefault(_scenario_name(scn, idx), scn)
    verdicts = {}
    for sid in ids:
        scn = by_id.get(sid)
        if scn is None:
            result.passed = False
            verdicts[sid] = "MISSING"
            result.findings.append(Finding(
                rule="COSIM_SCENARIO_MISSING", severity="ERROR",
                message=f"L22 declares scenario '{sid}' and no result has it",
                file=rel))
            continue
        word = str(scn.get("verdict") or scn.get("status") or "").strip()
        verdicts[sid] = word or "NONE"
        if word.lower() in _NOT_VERIFIED_TOKENS:
            result.vacuous = True
            result.findings.append(Finding(
                rule="SCENARIO_NOT_VERIFIED", severity="WARNING",
                message=(f"Scenario '{sid}': {word} — "
                         f"{scn.get('reason') or 'no reason given'}"),
                file=rel))
            continue
        verdict = _scenario_is_pass(scn)
        if verdict is True:
            continue
        result.passed = False
        result.findings.append(Finding(
            rule="SCENARIO_FAILED" if verdict is False else "SCENARIO_NO_VERDICT",
            severity="ERROR",
            message=f"Scenario '{sid}': co-sim verdict is {word or 'absent'}",
            file=rel))
    extra = sorted(set(by_id) - set(ids))
    if extra:
        result.findings.append(Finding(
            rule="UNDECLARED_EXTRA", severity="INFO",
            message=(f"results carry scenarios L22 does not declare "
                     f"(not counted): {extra}"),
            file=rel))
    return {"source": "L22", "declared": ids, "verdicts": verdicts,
            "undeclared_extra": extra, "l22_sha256": sha}


def run_audit(project: Path) -> AuditResult:
    result = AuditResult()

    blocks = _load_block_list(project) or _scan_spec_blocks(project)

    if not blocks:
        result.skip = True
        result.findings.append(Finding(
            rule="SKIP_NO_ANALOG",
            severity="INFO",
            message="No analog blocks detected; skipping mixed-signal cosim check",
        ))
        result.summary = {"skipped": True, "reason": "no_analog_blocks"}
        return result

    declared = _l22_cosim_plan(project)
    if declared is not None:
        detail = _audit_against_l22(project, result, *declared)
        result.summary = {
            "skipped": False, "total_blocks": len(blocks),
            "denominator": detail, "pass": result.passed,
            "verdict_tier": ("FAIL" if not result.passed
                             else "NOT_VERIFIED" if result.vacuous
                             else "PASS_WITH_STUB" if detail.get("stub")
                             else "PASS"),
        }
        return result

    # --- substance pass 1: aggregate report (the file the gate cites) ---
    # Independently verify every scenario; never echo all_scenarios_passed.
    agg_detail = _audit_aggregate(project, result)

    cosim_dir = _pl.mixed_signal_cosim_dir(project)
    simulated = 0
    missing = []
    failed = []
    stub_blocks = []   # v1.6.177 (#72 P1-6)

    # The aggregate report covers the whole design at the scenario level.
    # When it is present AND verified real scenarios (or is a stub), a
    # missing per-block file is informational — the substance lives in
    # the aggregate. Only when there is no aggregate substance at all do
    # per-block files become the sole evidence and a missing one FAILs.
    agg_covered = bool(agg_detail) and bool(agg_detail.get("pass"))
    agg_has_real_scenarios = (agg_covered
                              and (agg_detail.get("scenarios_total", 0) > 0
                                   or agg_detail.get("stub")))

    for block in blocks:
        cosim_file = cosim_dir / f"{block}_cosim_results.json"
        if not cosim_file.exists():
            # v1.6.177 (#72 P1-6) — if the analog spec is a stub,
            # the M-step cosim absence is expected (no real data to
            # co-simulate). Demote MISSING to PASS_WITH_STUB.
            if _block_is_stub(project, block):
                stub_blocks.append(block)
                simulated += 1
                result.findings.append(Finding(
                    rule="COSIM_STUB_ACCEPTED",
                    severity="INFO",
                    message=(
                        f"Block '{block}': analog spec is a "
                        f"deterministic stub; cosim absence accepted "
                        f"(PASS_WITH_STUB tier)."
                    ),
                ))
                continue
            if agg_has_real_scenarios:
                # Substance already verified at the aggregate level.
                result.findings.append(Finding(
                    rule="COSIM_AGG_COVERS_BLOCK",
                    severity="INFO",
                    message=(
                        f"Block '{block}': no per-block cosim file, but "
                        f"the aggregate mixed_signal_results.json carries "
                        f"verified scenarios covering the design."
                    ),
                ))
                continue
            missing.append(block)
            result.findings.append(Finding(
                rule="COSIM_MISSING",
                severity="ERROR",
                message=(
                    f"Block '{block}': no co-simulation results at "
                    f"cosim/{block}_cosim_results.json and no verified "
                    f"aggregate scenarios. Run mixed-signal-cosim skill."
                ),
            ))
            continue

        try:
            data = json.loads(cosim_file.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            result.findings.append(Finding(
                rule="COSIM_PARSE_ERROR",
                severity="ERROR",
                message=f"Block '{block}': cannot parse cosim results",
                file=str(cosim_file),
            ))
            failed.append(block)
            continue

        # v1.6.177 (#72 P1-6) — honor cosim_results.json stub marker.
        if is_stub_json(data):
            stub_blocks.append(block)
            simulated += 1
            result.findings.append(Finding(
                rule="COSIM_STUB_ACCEPTED",
                severity="INFO",
                message=(
                    f"Block '{block}': cosim_results carries "
                    f"deterministic_stub marker (PASS_WITH_STUB tier)."
                ),
                file=str(cosim_file),
            ))
            continue

        sim_passed = data.get("simulation_passed", False)
        if not sim_passed:
            failed.append(block)
            reason = data.get("failure_reason", "unknown")
            result.findings.append(Finding(
                rule="COSIM_FAILED",
                severity="ERROR",
                message=(
                    f"Block '{block}': co-simulation failed — {reason}"
                ),
                file=str(cosim_file),
            ))
        else:
            simulated += 1
            result.findings.append(Finding(
                rule="COSIM_PASSED",
                severity="INFO",
                message=f"Block '{block}': co-simulation passed",
            ))

    if missing or failed:
        result.passed = False

    # --- anti-fabrication: refuse a vacuous PASS on total absence ----
    # Blocks are declared but there is NO substance anywhere: no
    # aggregate report at all, no real/passing per-block cosim, and no
    # stub markers. This MUST be an honest FAIL — never PASS on absence.
    agg_present = agg_detail is not None
    real_block_evidence = simulated > 0  # includes stub-accepted blocks
    if result.passed and not agg_present \
            and not real_block_evidence and not failed:
        result.passed = False
        result.findings.append(Finding(
            rule="COSIM_NO_SUBSTANCE",
            severity="ERROR",
            message=(
                "Analog blocks are declared but no co-simulation "
                "substance exists: no aggregate mixed_signal_results.json "
                "and no per-block cosim results. Refusing vacuous PASS."
            ),
        ))

    # v1.6.177 (#72 P1-6) — verdict tier.
    verdict_tier = "PASS"
    all_stub = (result.passed and stub_blocks
                and len(stub_blocks) == len(blocks)
                and not (agg_detail and agg_detail.get("scenarios_total", 0)))
    if all_stub:
        verdict_tier = "PASS_WITH_STUB"
    elif result.passed and (stub_blocks
                            or (agg_detail and agg_detail.get("stub"))):
        verdict_tier = "PASS_WITH_STUB_PARTIAL"

    result.summary = {
        "skipped": False,
        "total_blocks": len(blocks),
        "simulated": simulated,
        "stub_blocks": stub_blocks,
        "missing": missing,
        "failed": failed,
        "aggregate": agg_detail,
        "pass": result.passed,
        "verdict_tier": verdict_tier,
    }
    return result


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("project_dir", type=Path)
    ap.add_argument("--json", default=None, help="JSON report output path")
    # v1.6.144 (#57) — FPGA-prototype-stage stub waiver.
    import _fpga_stub_waiver as _stub
    _stub.add_fpga_stub_argparse(ap)
    args = ap.parse_args(argv)

    if not args.project_dir.is_dir():
        print(f"ERROR: {args.project_dir} is not a directory", file=sys.stderr)
        return 2

    result = run_audit(args.project_dir)

    # Genuinely-inapplicable SKIP (no analog blocks) → rc=2. The
    # orchestrator's program_exit_zero maps rc=2 to VACUOUS_PASS; this is
    # the only path that PASSes on absence, and only because the step
    # truly does not apply.
    if result.skip:
        out = json.dumps(asdict(result), indent=2, ensure_ascii=False)
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(out)
        else:
            print("[SKIP] mixed_signal_cosim_check (no analog blocks)")
        return 2

    waiver_status: str = "PASS"
    if not result.passed and _stub.fpga_stub_waiver_active(args):
        result.passed = True
        waiver_status = "PASS_WITH_WAIVERS"
        result.summary["fpga_stub_waiver_applied"] = True
        result.summary["waiver_reason"] = _stub.fpga_stub_reason()
        for f in result.findings:
            if f.severity == "ERROR":
                f.severity = "WARNING"

    out = json.dumps(asdict(result), indent=2, ensure_ascii=False)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(out)

    if not args.json:
        status = ("FAIL" if not result.passed
                  else "NOT_VERIFIED" if result.vacuous else waiver_status)
        print(f"[{status}] mixed_signal_cosim_check")
        for f in result.findings:
            if f.severity in ("ERROR", "WARNING"):
                print(f"  [{f.severity}] {f.rule}: {f.message}")

    if result.passed and result.vacuous:
        return 2
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
