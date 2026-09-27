#!/usr/bin/env python3
"""Content check for flow step outputs; refusal exits nonzero.

ENFORCEMENT: advisory — step runners do not invoke this gate inline.

The required ``program_exit_zero`` clauses deny their owning step a PASS tier
in flow_compliance_check, but this gate cannot stop a producer step while
that step is running.

This checks the producer's actual bytes, never a previous gate report. It is
limited to structural evidence; design quality stays with the owning checks.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _path_layout as _pl  # noqa: E402
import p0_tool_frontend_check as _rtl_frontend


#: Where the rtl mode keeps the front end's record and the tools' whole
#: transcripts, so a refusal cites the tool's own words (path + sha256).
RTL_RECORD = "phase2/lint/rtl_content_check.json"


def _rtl_errors(project: Path) -> tuple[list[str], dict | None]:
    files = sorted((project / "phase2/stage1/rtl").glob("*.v")) + sorted((project / "phase2/stage1/rtl").glob("*.sv"))
    if not files:
        return ["no RTL source"], None
    errors = [f"{path}: empty RTL source" for path in files
              if not path.read_text(errors="replace").strip()]
    if errors:
        return errors, None
    # No image is resolved here: the front end resolves one only if a tool
    # has to run in docker (a tool on PATH, e.g. in-image, needs none).
    verdict = _rtl_frontend.check(project)
    _keep_record(project, verdict)
    return list(verdict["findings"]), verdict


def _keep_record(project: Path, verdict: dict) -> None:
    """Write each tool's whole transcript beside the record, cite it in the
    tool's row (path + sha256 of the written bytes), and write the record."""
    import hashlib
    from _atomic_artefact import write_json, write_text
    transcripts = verdict.pop("_transcripts", None) or {}
    record = _pl.report_path(project, RTL_RECORD)
    try:
        for name, text in transcripts.items():
            log = record.parent / "rtl_frontend" / f"{name}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            write_text(log, text)
            row = (verdict.get("tools") or {}).get(name)
            if isinstance(row, dict):
                row["log"] = str(log)
                row["log_sha256"] = ("sha256:"
                                     + hashlib.sha256(log.read_bytes()).hexdigest())
        record.parent.mkdir(parents=True, exist_ok=True)
        write_json(record, verdict)
    except OSError as exc:
        verdict.setdefault("record_not_written", str(exc))


def _tool_disclosure(project: Path, frontend: dict | None) -> list[str]:
    """What a refused rtl check must say: each failing tool's exit code, its
    whole transcript (path + sha256) and its own first error lines."""
    lines: list[str] = []
    for name, row in ((frontend or {}).get("tools") or {}).items():
        if not row.get("exit_code") and not row.get("errors"):
            continue
        log = row.get("log")
        try:
            log = str(Path(log).relative_to(project.resolve())) if log else None
        except ValueError:
            pass
        lines.append(f"  {name}: exit {row.get('exit_code')}"
                     + (f"; transcript {log} {row.get('log_sha256')}" if log else ""))
        lines += [f"    {e}" for e in row.get("errors") or []]
    return lines


def check(project: Path, mode: str) -> list[str]:
    errors: list[str] = []
    if mode == "rom_lint":
        path = project / "reports/phase2/lint/rom_init_lint.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, UnicodeError, ValueError) as exc:
            return [f"{path}: unreadable JSON: {exc}"]
        if not isinstance(data, list):
            return [f"{path}: expected findings list from rom_init_lint"]
        if any(not isinstance(row, dict) or
               not {"file", "line", "rule", "severity"} <= row.keys()
               for row in data):
            return [f"{path}: invalid rom_init_lint finding"]
    elif mode == "rtl":
        errors, _ = _rtl_errors(project)
    elif mode == "netlist":
        path = project / "phase2/stage2/synth/netlist.v"
        if not path.is_file():
            return [f"{path}: absent"]
        src = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(errors="replace"), flags=re.S)
        if not re.search(r"\bmodule\s+\w+\b", src) or not re.search(r"\bendmodule\b", src):
            errors.append(f"{path}: no complete module")
    else:
        # The analog stage is design-dependent. A digital-only run has no
        # analog block declaration and owes no stage-analog content here.
        if mode == "stage_analog" and not (project / "phase1/analog/analog_block_list.json").is_file():
            return []
        specs = {
            "si": ("reports/phase3/si_mcf_sta.json", ("nominal", "corners")),
            "repair": ("phase3/stage3/postroute_timing_repair/postroute_timing_repair_decision.json", ("repair_needed",)),
            "dfm": ("reports/phase3/dfm_screen.json", ("verdict", "findings")),
            "stage_analog": ("reports/analog/stage_analog_compliance.json", ("overall",)),
        }
        rel, keys = specs[mode]
        path = project / rel
        try:
            data = json.loads(path.read_text())
        except (OSError, UnicodeError, ValueError) as exc:
            return [f"{path}: unreadable JSON: {exc}"]
        if not isinstance(data, dict) or any(k not in data for k in keys):
            errors.append(f"{path}: missing required fields {keys}")
        elif mode == "repair" and not isinstance(data["repair_needed"], bool):
            errors.append(f"{path}: repair_needed must be boolean")
        elif mode == "dfm" and (data["verdict"] not in ("PASS", "PASS_WITH_ADVISORIES") or not isinstance(data["findings"], list)):
            errors.append(f"{path}: invalid DFM screen verdict or findings")
        elif mode == "stage_analog" and not isinstance(data["overall"], str):
            errors.append(f"{path}: invalid stage verdict")
        elif mode == "si" and (not isinstance(data["nominal"], dict) or
                                not isinstance(data["corners"], dict) or
                                not data["corners"]):
            errors.append(f"{path}: invalid SI corner evidence")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--mode", required=True, choices=("rtl", "rom_lint", "netlist", "si", "repair", "dfm", "stage_analog"))
    args = ap.parse_args()
    if not args.project.is_dir():
        print(f"FAIL: project directory absent: {args.project}")
        return 2
    if args.mode == "rtl":
        errors, frontend = _rtl_errors(args.project)
    else:
        errors = check(args.project, args.mode)
        frontend = None
    if frontend is not None and frontend.get("not_measured"):
        print(f"NOT_MEASURED: {frontend['not_measured']}")
        return 2
    for error in errors:
        print("FAIL:", error)
    if errors:
        for line in _tool_disclosure(args.project, frontend):
            print(line)
        return 1
    print(f"PASS: {args.mode} output content")
    if frontend is not None:
        print("TOOL_EVIDENCE:", json.dumps({
            "sources": frontend["sources"],
            "tools": {name: {"exit_code": row["exit_code"],
                             "execution": row["execution"],
                             "diagnostics": row.get("diagnostics", [])}
                      for name, row in frontend["tools"].items()},
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
