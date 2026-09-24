"""Sign a synthetic project's exact evidence for unrelated flow tests."""

import json
from pathlib import Path

import ai_signed_judgement


def sign(project: Path, step_id: str) -> None:
    digest = ai_signed_judgement.evidence_sha256(project, step_id)
    assert digest, f"fixture has no Step {step_id} evidence to review"
    receipt = project / "reports/audit/ai_judgements" / f"{step_id}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "schema": ai_signed_judgement.SCHEMA,
        "step_id": step_id,
        "evidence_sha256": digest,
        "verdict": "PASS",
        "signed_by": "synthetic-fixture-reviewer",
        "judgement": "Reviewed the synthetic evidence staged by this test fixture.",
    }) + "\n")
