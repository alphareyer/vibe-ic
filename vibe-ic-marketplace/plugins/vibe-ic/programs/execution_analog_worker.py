"""Source-only worker boundary for the analog provider catalog.

The worker records a typed NOT_MEASURED result. It deliberately does not run
SPICE, layout, PDK, or mixed-signal tools; those remain owned by the canonical
runner and its gates. In particular, rows without a real producer are never
given a worker route here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def run(step_id: str, inputs: Path, outputs: Path) -> dict:
    outputs.mkdir(parents=True, exist_ok=True)
    result = {
        "step_id": step_id,
        "status": "NOT_MEASURED",
        "reason": "source-bound catalog only; canonical producer and gates were not executed",
        "inputs": str(inputs),
        "outputs": str(outputs),
    }
    (outputs / "provider_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--outputs", required=True, type=Path)
    args = parser.parse_args(argv)
    run(args.step, args.inputs, args.outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
