"""Source-only backend worker seam.

ULTRACORE owns process execution and resource admission. This module keeps a
small callable for compatibility and never launches an EDA process or creates
a competing scheduler.
"""
from __future__ import annotations

import json
from pathlib import Path


def execute(inputs: Path, outputs: Path) -> dict:
    outputs = Path(outputs)
    outputs.mkdir(parents=True, exist_ok=True)
    result = {"schema": "vibeic/backend-provider-receipt/1",
              "verdict": "NOT_MEASURED",
              "reason": "native execution is owned by ULTRACORE and was not run in this source-only lane",
              "native_receipts": []}
    (outputs / "backend_result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    return result


__all__ = ["execute"]
