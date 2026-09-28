"""Synthetic AI route completion for tests of later product gates.

Only synthetic test prompts are read. The helper never bypasses route validation:
the same coordinator-issued task and --resume path as the real caller are used.
"""
from __future__ import annotations

import json
from pathlib import Path


def complete_ai_routes(dispatch, bench: str, dataset, run, *, jobs: int = 1,
                       heavy_jobs=None, worker_threads: int = 0,
                       fallback_nature: str = "debug") -> int:
    tasks = dispatch._read_jsonl(Path(run) / dispatch._ROUTE_WORKLIST)
    for task in tasks:
        proposal = task["program_proposal"].get("entry_nature")
        nature = proposal or fallback_nature
        disposition = "CONFIRM" if proposal else "OVERRIDE"
        response = {
            "schema": dispatch._AI_ROUTE_SCHEMA,
            "id": task["id"], "task_sha256": task["task_sha256"],
            "prompt_sha256": task["prompt_sha256"],
            "source_sha256": task["public_original_input"].get("source_sha256"),
            "routing_contract_sha256": task["routing_contract_sha256"],
            "disposition": disposition,
            "ai_nature": nature,
            "author": {"kind": "AI", "model": "synthetic-route-control"},
            "blind": {"oracle_accessed": False},
            "rationale": ("This test route uses only its synthetic public prompt "
                          "and the declared input context, never an oracle. " * 3),
            "prompt_evidence": [],
        }
        path = Path(task["response_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(response))
    return dispatch.cmd_resume(
        bench, str(dataset), str(run), jobs=jobs, heavy_jobs=heavy_jobs,
        worker_threads=worker_threads)
