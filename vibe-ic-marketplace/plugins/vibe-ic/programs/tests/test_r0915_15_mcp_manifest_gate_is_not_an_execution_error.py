"""A gate whose subject only an MCP run produces is N/A on a program-only run.

MEASURED 2026-09-15 (lane icspm3, R-0915-15) on `spm` x gf180mcuD.
`fpga_program_chain_attest_check` audits the mcp-eda execution manifest and
answered::

    error: manifest not found: <project>/latest_results.jsonl

which P0 booked `EXECUTION_ERROR` — a fault the design did not commit and this
flow cannot fix, because **nothing in this flow writes that file**. Step 31
already declares the SAME artefact N/A for the SAME reason, in the flow yaml::

    condition_files_exist: ["latest_results.jsonl"]
    … "Scoped to a run that drove the MCP-EDA server: the manifest is written
    by the MCP tools themselves and by nothing in this flow, so a program-only
    run has no execution record to read and this gate would be reporting on an
    absence rather than judging one."

P0's equivalent channel is `_P0_GATE_REQUIRED_CONTEXT`, so the question is
asked there in the same terms.

BOTH DIRECTIONS: no manifest → SKIP with `reason_class=DESIGN_DECLARED_NA`; a
manifest present → the gate stays live and blocking, exactly as before.

AND THE EARLY RETURN THAT MADE THE ROSTER UNREACHABLE. `_p0_contract_na_reason`
returned `None` for any gate with no declared argv contract — i.e. for most of
the umbrella — so a gate-keyed requirement could never fire on one. The
requirement map is keyed by GATE because its question is about the gate's
subject, not its CLI shape; a gate with NEITHER a contract nor a requirement is
unchanged and still runs fail-closed.

chip-AGNOSTIC: the manifest path is derived from the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402

GATE = "fpga_program_chain_attest_check"


def _proj(tmp_path, manifest: bool):
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text("module top(); endmodule\n")
    if manifest:
        (tmp_path / "latest_results.jsonl").write_text(
            '{"step":"fpga_compile","status":"PASS","session_id":"s1"}\n')
    return tmp_path, rtl


# ── direction 1: no manifest → declared N/A, not a fault ──────────────────

def test_a_program_only_run_makes_the_gate_NA(tmp_path):
    proj, rtl = _proj(tmp_path, manifest=False)
    why = F._p0_contract_na_reason(GATE, proj, rtl)
    assert why is not None, "the gate is still invoked with no manifest"
    assert "mcp_manifest" in why
    assert "fabricated" in why


def test_the_gate_is_registered_against_the_manifest():
    assert F._P0_GATE_REQUIRED_CONTEXT.get(GATE) == ("mcp_manifest",)


def test_the_context_reports_the_manifest_only_when_it_is_there(tmp_path):
    proj, rtl = _proj(tmp_path, manifest=False)
    assert F._p0_contract_context(proj, rtl).get("mcp_manifest") is None
    (proj / "latest_results.jsonl").write_text("{}\n")
    assert F._p0_contract_context(proj, rtl).get("mcp_manifest") is not None


def test_that_disposition_is_skip_eligible():
    """The consequence: a SKIP booked DESIGN_DECLARED_NA lets P0 reach PASS,
    which an EXECUTION_ERROR never can."""
    assert R.DESIGN_DECLARED_NA in R.SKIP_ELIGIBLE
    assert R.record_verdict(R.DESIGN_DECLARED_NA) == "SKIP"


# ── direction 2: a run that DID drive the MCP server keeps the gate ───────

def test_a_run_with_a_manifest_keeps_the_gate_live(tmp_path):
    proj, rtl = _proj(tmp_path, manifest=True)
    assert F._p0_contract_na_reason(GATE, proj, rtl) is None, (
        "a run that produced the manifest must still be audited")


# ── the early return that made the roster unreachable ─────────────────────

def test_a_gate_with_neither_a_contract_nor_a_requirement_still_runs(tmp_path):
    """The conservative direction, unchanged: this must not become a way for
    any gate to skip itself."""
    proj, rtl = _proj(tmp_path, manifest=False)
    for gate in ("waiver_staleness_check", "analog_flow_compliance_check"):
        assert F._STRUCTURAL_GATE_INVOCATION_CONTRACTS.get(gate) is None
        assert F._P0_GATE_REQUIRED_CONTEXT.get(gate) is None
        assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


def test_the_existing_roster_entry_is_undisturbed(tmp_path):
    """`pre_awake_silence_check` was the only gate-keyed requirement before."""
    assert F._P0_GATE_REQUIRED_CONTEXT.get(
        "pre_awake_silence_check") == ("l3_opcodes",)
