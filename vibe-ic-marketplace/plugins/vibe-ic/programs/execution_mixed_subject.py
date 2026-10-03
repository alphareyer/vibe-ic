"""Resolve a mixed producer's canonical subject from issued Controller facts."""
from __future__ import annotations

import json
import os
from pathlib import Path


def project_subject(project: Path, step_id: str | None = None) -> str:
    """Use the issued canonical path only for its matching fixed-row worker.

    Ordinary Default callers have no Controller binding and keep the historical
    project.resolve() identity.  A worker may bind its staged project copy to
    the original path only when the sealed context objective carries that path.
    """
    local = Path(project).resolve()
    raw = os.environ.get("VIBEIC_EXECUTION_BINDING")
    if not raw:
        return str(local)
    try:
        binding = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("MIXED_ISSUED_BINDING_INVALID") from exc
    issued_step = binding.get("step_id") if isinstance(binding, dict) else None
    if (not isinstance(binding, dict) or issued_step not in ("M1", "M2", "M3", "M4") or
            (step_id is not None and issued_step != str(step_id))):
        raise ValueError("MIXED_ISSUED_SUBJECT_MISMATCH")
    objective = binding.get("objective")
    subject = objective.get("project_subject") if isinstance(objective, dict) else None
    if not isinstance(subject, str) or not Path(subject).is_absolute():
        raise ValueError("MIXED_ISSUED_SUBJECT_MISSING")
    canonical = Path(subject).resolve(strict=True)
    if not canonical.is_dir() or str(canonical) != subject:
        raise ValueError("MIXED_ISSUED_SUBJECT_INVALID")
    return str(canonical)
