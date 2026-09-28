#!/usr/bin/env python3
"""ADVISORY: compare serial declaration to independently calibrated framing.

Existing published declarations have not been corpus swept.  A mismatch is
reported as FAIL here, while the Phase-2 runner records it and continues.
Missing measurement is NOT_MEASURED with a reason, never a clean PASS.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from serial_latency_contract import LATENCY_ORIGIN


def check(project: Path) -> dict:
    declaration = project / "plugin_output" / "declaration.json"
    manifest = (project / "phase2/stage1/sim_full_stack"
                / "arith_oracle_manifest.json")
    try:
        measured = json.loads(manifest.read_text())
        declared = json.loads(declaration.read_text())
    except (OSError, ValueError) as exc:
        return {"verdict": "NOT_MEASURED", "reason": str(exc),
                "latency_origin": LATENCY_ORIGIN}
    if not isinstance(measured, dict) or not isinstance(declared, dict):
        return {"verdict": "NOT_MEASURED", "reason": "invalid artifact shape",
                "latency_origin": LATENCY_ORIGIN}
    if (measured.get("latency_origin") != LATENCY_ORIGIN
            or type(measured.get("calibrated_latency")) is not int
            or measured.get("calibrated_bit_order") not in ("LSB_first", "MSB_first")
            or measured.get("calibrated_out_bit_order") not in ("LSB_first", "MSB_first")):
        return {"verdict": "NOT_MEASURED",
                "reason": "no calibrated serial framing with the declared edge origin",
                "latency_origin": LATENCY_ORIGIN}
    mismatches = {}
    for field, key in (("latency_cycles", "calibrated_latency"),
                       ("bit_order", "calibrated_bit_order")):
        if declared.get(field) != measured[key]:
            mismatches[field] = {"declared": declared.get(field),
                                 "measured": measured[key]}
    if declared.get("bit_order") != measured["calibrated_out_bit_order"]:
        mismatches["output_bit_order"] = {
            "declared": declared.get("bit_order"),
            "measured": measured["calibrated_out_bit_order"]}
    return {"verdict": "FAIL" if mismatches else "PASS",
            "latency_origin": LATENCY_ORIGIN, "mismatches": mismatches,
            "measured_source": measured.get("calibrated_source")}


def main(argv=None) -> int:
    project = Path((argv or sys.argv[1:])[0]).resolve()
    result = check(project)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
