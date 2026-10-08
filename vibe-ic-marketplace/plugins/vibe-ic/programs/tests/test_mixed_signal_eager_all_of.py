"""M3/M4 must publish every independent blocking clause on one run.

The producer receipt is deliberately the first clause in both groups.  A
normal ``all_of`` short-circuits on that refusal, hiding the consumer's own
finding.  The explicit ``all_of_eager`` policy keeps the group fail-closed
while making every sibling execute.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from programs import flow_compliance_check as flow


FLOW = Path(__file__).resolve().parents[2] / "flow/phase1_phase2_phase3.yaml"


def _step(step_id: str) -> dict:
    document = yaml.safe_load(FLOW.read_text(encoding="utf-8"))
    return next(step for step in document["steps"] if str(step["id"]) == step_id)


@pytest.mark.parametrize("step_id", ("M3", "M4"))
def test_mixed_signal_eager_group_runs_consumers_after_first_failure(
    tmp_path, monkeypatch, step_id
):
    gate = _step(step_id)["gate"]
    assert gate.get("all_of_eager") is True
    commands = [
        clause["program_exit_zero"]
        for clause in gate["all_of"]
        if "program_exit_zero" in clause
    ]
    assert len(commands) >= 2

    seen = []

    def fake_check(_project, command):
        seen.append(command)
        # The producer is the first clause and refuses.  The remaining
        # independent consumers are still called, but the aggregate stays
        # failed because all_of_eager retains blocking AND semantics.
        return (False, "forced producer refusal") if len(seen) == 1 else (
            True, "consumer inspected")

    monkeypatch.setattr(flow, "_check_program_exit_zero", fake_check)
    passed, reasons = flow._evaluate_gate(tmp_path, gate)

    assert passed is False
    assert seen == commands
    assert any("forced producer refusal" in reason for reason in reasons)
