"""A Step-5 functional record with one REAL trace, for Step-38 kit fixtures.

U18: the Step-38 test-pattern item is owned by this flow and is closed only by
patterns converted from the traces Step 5 recorded. A fixture that wants a
complete kit therefore carries what a real run carries: the L10 seed, the
Step-5 record naming the seed PASSED, and the trace, hashed in that record.

The trace is the first 400 ns of the VCD `full_stack_functional_tb` wrote for
the `case_3` L10 case through a pad-ring chip top (iverilog in vibeic-eda
0.3.86, 2026-09-29, spm run_v5c tree re-run with the U18 trace top), cut at a
timestamp boundary. The design and case names are the fixture's only link to
that run; nothing here reads them as a rule.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

FIXTURE_VCD = Path(__file__).resolve().parent / "fixtures" / "u18_step38" \
    / "case_3.head.vcd"
CASE = "case_3"
STEP5_REL = "phase2/stage1/sim_full_stack/functional/functional_cases.json"
DUT_PORTS = [
    {"name": "clk", "direction": "input"},
    {"name": "rst", "direction": "input"},
    {"name": "x", "direction": "input"},
    {"name": "y", "direction": "input"},
    {"name": "p", "direction": "output"},
    {"name": "VDD", "direction": "inout"},
    {"name": "VSS", "direction": "inout"},
]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plant(project: Path, state: str = "passed") -> dict:
    """Write the L10 seed, the trace and the Step-5 record; return the record."""
    project = Path(project)
    docs = project / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    l10 = docs / "L10_TEST_CASES.json"
    data = {"test_cases": []}
    if l10.is_file():
        data = json.loads(l10.read_text())
    ids = [c.get("id") for c in data.get("test_cases") or []
           if isinstance(c, dict)]
    if CASE not in ids:
        data.setdefault("test_cases", []).append({"id": CASE})
    l10.write_text(json.dumps(data, indent=2))
    run = project / "phase2/stage1/sim_full_stack/functional/run" / CASE
    run.mkdir(parents=True, exist_ok=True)
    vcd = run / f"{CASE}.vcd"
    shutil.copyfile(FIXTURE_VCD, vcd)
    rel = str(vcd.relative_to(project))
    rec = {
        "program": "full_stack_functional_tb",
        "schema": "vibeic.full_stack_functional.v1",
        "verdict": "PASS",
        "dut_ports": DUT_PORTS,
        "cases": [{
            "name": CASE, "state": state, "instantiates": "chip_top",
            "trace": {"vcd": rel, "scope": f"{CASE}/dut",
                      "dut_module": "chip_top", "scope_verified": True,
                      "sha256": sha256(vcd)},
        }],
    }
    out = project / STEP5_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=2))
    return rec
