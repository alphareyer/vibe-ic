#!/usr/bin/env python3
"""Step 14 on the tool path: judge the synthesis HANDOFF NETLIST, not its recipe.

When step 9 runs LibreLane Yosys.Synthesis the recipe is the tool's fixed code
(setundef -> hilomap -> opt_clean -purge), so auditing script text has nothing
to audit. What must still hold at the handoff is a property of the netlist:

* no constant reaches a cell pin or a port as a literal (`1'h0`, `1'b1`,
  `1'bx`...). A literal constant is what `hilomap` exists to remove; OpenROAD
  turns it into `zero_`/`one_` nets and the detailed router fails on them
  (DRT-0305, the fresh-agent v068 run). An x/z literal is an undefined value.
* the resolved configuration names both tie cells (SYNTH_TIEHI_CELL /
  SYNTH_TIELO_CELL), so the recipe had somewhere to map a constant.
* the tie cells it placed are counted, as evidence.

LibreLane's Checker.YosysUnmappedCells / YosysSynthChecks /
NetlistAssignStatements run beside this gate; they do not look at constants.

ENFORCEMENT: blocking — `phase3_one_shot_runner._step_synth_librelane` runs
this gate on the tool's netlist and a nonzero exit fails the step. Step 14's
flow gate runs it in PROJECT mode (`synth_handoff_netlist_check <project>`) on
the mapped netlist step 9 handed to PnR, whichever arm produced it (CUT_W4:
this netlist check replaced the four programs that audited the direct
recipe's TEXT). A netlist the tool produced is judged against the tool's
resolved configuration; a netlist the direct recipe produced has no tool
configuration, so only its constants are judged and the tie-cell row says so.

chip-AGNOSTIC: the tie-cell names come from the resolved tool config.
Exit: 0 PASS, 1 FAIL, 2 NOT_MEASURED (unreadable input).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
import instrument_calibration  # noqa: E402

#: A Verilog sized literal as Yosys `write_verilog` prints a constant.
_LITERAL = r"\d+'[bhdo][0-9a-fA-FxXzZ_?]+"
_ASSIGN_CONST_RE = re.compile(r"^\s*assign\s+(\S+)\s*=\s*(" + _LITERAL + r")\s*;", re.M)
_PIN_CONST_RE = re.compile(r"\.(\w+)\(\s*(" + _LITERAL + r")\s*\)")
_CONST_NET_RE = re.compile(r"\b(zero_|one_)\b")


def constant_connections(netlist: str) -> list[dict[str, str]]:
    """Every literal constant driving a port (assign) or a cell pin."""
    instrument_calibration.assert_calibrated(
        "synth_handoff_netlist_check::constant_connections")
    found = [{"kind": "assign", "target": m.group(1), "value": m.group(2)}
             for m in _ASSIGN_CONST_RE.finditer(netlist)]
    found += [{"kind": "pin", "target": m.group(1), "value": m.group(2)}
              for m in _PIN_CONST_RE.finditer(netlist)]
    found += [{"kind": "net", "target": m.group(1), "value": m.group(1)}
              for m in _CONST_NET_RE.finditer(netlist)]
    return found


def _master(cell: Any) -> Optional[str]:
    return str(cell).split("/", 1)[0] if cell else None


def check(netlist: Path, resolved: dict) -> dict:
    text = netlist.read_text(errors="replace")
    findings = []
    tiehi, tielo = _master(resolved.get("SYNTH_TIEHI_CELL")), _master(resolved.get("SYNTH_TIELO_CELL"))
    if not tiehi or not tielo:
        findings.append(f"TIE_CELL_UNDECLARED: SYNTH_TIEHI_CELL={resolved.get('SYNTH_TIEHI_CELL')!r} "
                        f"SYNTH_TIELO_CELL={resolved.get('SYNTH_TIELO_CELL')!r}")
    constants = constant_connections(text)
    undefined = [c for c in constants if re.search(r"[xXzZ?]", c["value"].split("'", 1)[-1])]
    if constants:
        findings.append(f"CONSTANT_NOT_TIED: {len(constants)} literal constant(s) at the handoff "
                        f"(first: {constants[0]['kind']} {constants[0]['target']} = {constants[0]['value']})")
    if undefined:
        findings.append(f"UNDEFINED_CONSTANT: {len(undefined)} x/z literal(s)")
    ties = {name: len(re.findall(r"^\s*" + re.escape(name) + r"\s", text, re.M))
            for name in (tiehi, tielo) if name}
    return {"program": "synth_handoff_netlist_check", "step": "14",
            "verdict": "FAIL" if findings else "PASS", "netlist": str(netlist),
            "tie_cells": ties, "constants": constants[:50],
            "constant_count": len(constants), "findings": findings}


#: Written by phase 3's step 9 beside the netlist it hands to PnR, on both
#: arms (`phase3_one_shot_runner._write_synth_inputs_sidecar`).
SYNTH_DIR = "phase2/stage2/synth"
SIDECAR = "synth_inputs.json"  # _path_layout.SYNTH_INPUTS_SIDECAR


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_project(project: Path) -> dict:
    """Judge the mapped netlist step 9 handed to PnR.

    The netlist is the one the step-9 sidecar names. When a LibreLane
    Yosys.Synthesis step directory holds a native netlist with the SAME bytes,
    the tool produced it and its resolved `config.json` supplies the tie
    cells. Otherwise the direct recipe produced it: the constant check still
    applies in full, and the tie-cell row is recorded as not applicable
    rather than judged against a configuration that does not exist."""
    sidecar = project / SYNTH_DIR / SIDECAR
    name = json.loads(sidecar.read_text()).get("netlist")
    if not name:
        raise ValueError(f"{sidecar} names no netlist")
    netlist = project / SYNTH_DIR / str(name)
    digest = _sha256(netlist)
    producer, resolved_path = None, None
    for state in sorted((project / "phase3/librelane").glob("*yosys-synthesis/state_out.json")):
        native = Path(str(json.loads(state.read_text()).get("nl") or ""))
        if native.is_file() and _sha256(native) == digest:
            producer, resolved_path = "LibreLane Yosys.Synthesis", state.parent / "config.json"
    if resolved_path is not None:
        report = check(netlist, json.loads(resolved_path.read_text()))
        report["resolved"] = str(resolved_path)
    else:
        producer = "direct recipe (step 9 mode direct)"
        report = check(netlist, {"SYNTH_TIEHI_CELL": "-", "SYNTH_TIELO_CELL": "-"})
        report["tie_cells"] = {}
        report["tie_cell_declaration"] = (
            "NOT_APPLICABLE: the direct recipe has no tool configuration; its "
            "constants are judged in full above")
    report["producer"], report["netlist_sha256"] = producer, digest
    # Harvest of yosys_script_template_check.audit_handoff_netlist: the
    # artefact handed to PnR must be a netlist, and the product of the RTL it
    # claims (vibe-ic#1253 / the step-14 "stub netlist" and "older than its
    # producer" rows). Both arms write the RTL fingerprint beside the netlist.
    body = [ln for ln in netlist.read_text(errors="replace").splitlines()
            if ln.strip() and not ln.strip().startswith("//")]
    if not body:
        report["findings"].append("NETLIST_EMPTY: the handoff netlist is empty or "
                                  "comment-only; PnR would consume nothing")
    # Harvest of yosys_script_template_check's `-flatten` token: "without
    # -flatten the ATPG flow breaks on backslash-escaped hierarchical names".
    # The direct recipe always flattens and the tool flattens when its
    # SYNTH_HIERARCHY_MODE says so; either way PnR must receive ONE module.
    import _design_module_set
    modules = sorted(_design_module_set.module_names_in_text(
        netlist.read_text(errors="replace")))
    report["modules"] = modules
    # LibreLane's default mode is `flatten`, and `deferred_flatten` flattens
    # after synthesis; a declared keep-hierarchy list keeps modules on purpose.
    tool_cfg = json.loads(resolved_path.read_text()) if resolved_path is not None else {}
    flattens = (tool_cfg.get("SYNTH_HIERARCHY_MODE") or "flatten") in ("flatten", "deferred_flatten")
    kept = any(tool_cfg.get(key) for key in (
        "SYNTH_KEEP_HIERARCHY_INSTANCES", "SYNTH_KEEP_HIERARCHY_MODULES",
        "SYNTH_KEEP_HIERARCHY_MIN_COST"))
    if flattens and not kept and len(modules) > 1:
        report["findings"].append(
            f"HIERARCHY_NOT_FLAT: {len(modules)} modules in a netlist the "
            f"recipe flattens ({modules[:5]})")
    rtl_doc = json.loads(sidecar.read_text()).get("rtl_sha256")
    import _path_layout
    rtl_dir = _path_layout.rtl_dir(project)
    if not isinstance(rtl_doc, dict) or not rtl_doc:
        report["findings"].append(f"HANDOFF_UNBOUND: {sidecar} records no RTL "
                                  "fingerprint, so the netlist cannot be tied to its RTL")
    else:
        current = {f.name: _sha256(f) for f in
                   sorted(rtl_dir.glob("*.sv")) + sorted(rtl_dir.glob("*.v"))}
        changed = sorted(name for name, sha in rtl_doc.items() if current.get(name) != sha)
        added = sorted(set(current) - set(rtl_doc))
        if changed or added:
            report["findings"].append(
                f"HANDOFF_STALE: the RTL changed after this netlist was synthesised "
                f"(changed or removed {changed}, added {added})")
    report["verdict"] = "FAIL" if report["findings"] else "PASS"
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path, nargs="?",
                        help="project mode: judge the netlist step 9 handed to PnR")
    parser.add_argument("--netlist", type=Path)
    parser.add_argument("--resolved", type=Path,
                        help="the synthesis step's resolved config (config.json)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if (args.project is None) == (args.netlist is None) or \
            (args.netlist is not None and args.resolved is None):
        parser.error("give a project, or --netlist with --resolved")
    try:
        if args.project is not None:
            report = check_project(args.project)
        else:
            resolved = json.loads(args.resolved.read_text())
            report = check(args.netlist, resolved)
    except (OSError, ValueError) as exc:
        report = {"program": "synth_handoff_netlist_check", "verdict": "NOT_MEASURED",
                  "findings": [str(exc)]}
    if args.json:
        write_json(args.json, report)
    print(f"[{report['verdict']}] " + "; ".join(report["findings"]))
    return {"PASS": 0, "FAIL": 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
