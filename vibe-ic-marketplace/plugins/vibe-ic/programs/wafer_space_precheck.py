#!/usr/bin/env python3
"""Run the official wafer.space flow with LibreLane's typed initial state.

The pinned operator CLI passes its --with-initial-state filename directly to
Flow.start. LibreLane requires a State object. This adapter only loads that
object and calls the operator's unchanged main; all operator steps run.
An engineering slot is a technical target, not a purchased reservation or ID.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import traceback

from _atomic_artefact import write_json


def _file(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path), "sha256": h.hexdigest(), "bytes": path.stat().st_size}


def run(args):
    from librelane.state import State

    official = Path(args.official_source).resolve() / "precheck.py"
    spec = importlib.util.spec_from_file_location("wafer_space_official_precheck", official)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    initial = State.load(json.loads(Path(args.with_initial_state).read_text()))
    input_record = _file(args.input)
    initial_record = _file(args.with_initial_state)
    official_record = _file(official)
    rc = 0
    try:
        module.main(args.input, args.output, args.top, args.dir,
                    args.id if args.id is not None else "FFFFFFFF", args.cob,
                    args.slot, str(args.threads), args.workers, args.run_tag,
                    False, None, None, None, initial)
    except SystemExit as exc:
        rc = int(exc.code or 0)
    except Exception:
        traceback.print_exc()
        rc = 1
    run_dir = Path(args.dir).resolve() / "runs" / args.run_tag
    steps = []
    for folder in sorted(run_dir.iterdir()) if run_dir.is_dir() else []:
        if folder.is_dir() and folder.name[:2].isdigit():
            state = folder / "state_out.json"
            steps.append({"folder": str(folder),
                          "state_out": _file(state) if state.is_file() else None})
    output = _file(args.output) if Path(args.output).is_file() else None
    expected = [step.id for step in module.PrecheckFlow.Steps]
    complete = (rc == 0 and len(steps) == len(expected)
                and all(step["state_out"] for step in steps) and output is not None)
    record = {
        "producer": "wafer_space_precheck",
        "scope": "official provider precheck; not LVS, purchase or platform acceptance",
        "returncode": rc,
        "verdict": "PASS" if complete else "FAIL",
        "input": input_record,
        "input_unchanged": _file(args.input) == input_record,
        "initial_state": initial_record,
        "official_entry": official_record,
        "slot": args.slot, "packaging": "cob" if args.cob else "bare_die",
        "assigned_id": args.id,
        "uses_official_unassigned_test_default": args.id is None,
        "all_steps_requested": True,
        "expected_steps": expected, "steps": steps,
        "output": output,
    }
    if not record["input_unchanged"]:
        record["verdict"] = "FAIL"
    write_json(Path(args.record), record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("official-source", "input", "output", "top", "slot", "dir",
                 "run-tag", "with-initial-state", "record"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--id")
    parser.add_argument("--cob", action="store_true")
    result = run(parser.parse_args(argv))
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else (result["returncode"] or 1)


if __name__ == "__main__":
    sys.exit(main())
