"""Canonical backend consumer seam used after ULTRACORE adoption."""
from __future__ import annotations

from pathlib import Path
import json

import execution_modes as em
from execution_backend_gates import evaluate


def import_selected(project, context, controller, run, adopted):
    """Re-read current canonical gates without importing another runner stack."""
    project, run = Path(project), Path(run)
    if adopted.get("status") != "ADOPTED":
        raise em.Refusal("BACKEND_ADOPTION_UNBOUND", str(project))
    result_path = run / "backend_result.json"
    if not result_path.is_file():
        raise em.Refusal("BACKEND_SELECTED_SUBSTANCE_REFUSED", context.step_id)
    result = json.loads(result_path.read_text())
    if result.get("verdict") == "FAIL":
        raise em.Refusal("GATE_FAIL", context.step_id)
    if result.get("verdict") != "PASS":
        raise em.Refusal("BACKEND_CANONICAL_CONSUMER_REFUSED", context.step_id)
    gates = evaluate(project, {"id": context.step_id, "gate": {}})
    return {"status": "CONSUMED", "step_id": context.step_id,
            "source_sha": context.source_sha, "consumer": gates,
            "design_verdict": "PASS"}


__all__ = ["import_selected"]
