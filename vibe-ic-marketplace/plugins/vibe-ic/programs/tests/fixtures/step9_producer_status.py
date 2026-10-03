"""Tiny supervised producer receipt fixture for Step9 gate precedence tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


outputs = Path(sys.argv[1])
status = sys.argv[2]
outputs.mkdir(parents=True, exist_ok=True)
(outputs / "native_commands.jsonl").write_text(
    json.dumps({"executed": False, "fixture": "gate-precedence-control"}) + "\n")
if status != "MISSING":
    receipt = {
        "binding": json.loads(os.environ["VIBEIC_EXECUTION_BINDING"]),
        "status": status,
        "detail": "test fixture disposition",
    }
    (outputs / "producer.json").write_text(json.dumps(receipt) + "\n")
