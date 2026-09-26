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
this gate on the tool's netlist and a nonzero exit fails the step.

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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--resolved", type=Path, required=True,
                        help="the synthesis step's resolved config (config.json)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
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
