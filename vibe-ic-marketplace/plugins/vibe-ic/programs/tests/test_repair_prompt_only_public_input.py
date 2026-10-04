"""A prompt-only staging must still be able to authorize an AI repair.

`_validate_repair_record` refused every repair whose
``public_original_input.status`` was not ``PRESENT``. Text-generation
benchmarks (VerilogEval et al.) stage a prompt and no separate public files,
so their tasks are ``NOT_PROVIDED`` **with** an immutable ``source_sha256``
(the staged prompt hash the whole lineage binds to). The coordinator still
emitted ``AI_SEMANTIC_REPAIR_REQUIRED`` handoffs, so the product DEMANDED a
repair record its own validator could never accept — a catch-22 in which a
proven semantic FAIL could never be repaired.
"""
import hashlib
import json
from pathlib import Path
import sys

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import benchmark_dispatch as bd


PROMPT_SHA = "0" * 64
SOURCE_SHA = "1" * 64
PARENT_SHA = "2" * 64
REPAIRED_SHA = "3" * 64
CHALLENGE_SHA = "4" * 64


def _task(status, source_sha256=SOURCE_SHA):
    return {
        "id": "ProbX_demo",
        "prompt_sha256": PROMPT_SHA,
        "rtl_sha256": PARENT_SHA,
        "public_original_input": {
            "schema": "vibeic.public_original_input.v1",
            "role": "public_original_input",
            "id": "ProbX_demo",
            "prompt_sha256": PROMPT_SHA,
            "source_sha256": source_sha256,
            "status": status,
            "files": [],
        },
    }


def _record(tmp_path):
    author = {"kind": "AI", "model": "glm-5.3"}
    contract = {
        "schema": "vibeic.source_bounded_completion.v1",
        "author": author,
        "oracle_accessed": False,
        "prompt_sha256": PROMPT_SHA,
        "source_sha256": SOURCE_SHA,
        "parent_rtl_sha256": PARENT_SHA,
        "candidate_sha256": REPAIRED_SHA,
        "challenge_sha256": CHALLENGE_SHA,
        "preservation": {"declaration": "sources named", "sources_read": []},
        "elaboration_matrix": {"declaration": "rows", "rows": []},
    }
    record = {
        "schema": "vibeic.benchmark.ai_repair_record.v1",
        "id": "ProbX_demo",
        "prompt_sha256": PROMPT_SHA,
        "parent_rtl_sha256": PARENT_SHA,
        "repaired_rtl_sha256": REPAIRED_SHA,
        "challenge_sha256": CHALLENGE_SHA,
        "author": author,
        "oracle_accessed": False,
        "rationale": "fixes the proven finding with prompt-derived reasoning" * 2,
        "repair_contract": contract,
    }
    path = tmp_path / "repair.json"
    path.write_text(json.dumps(record, indent=1))
    return path


def _reasons(tmp_path, status, source_sha256=SOURCE_SHA, monkeypatch=None):
    if monkeypatch is not None:
        # Not the seam under test: the on-disk public-input manifest lineage
        # is exercised by its own suites. Here we isolate the STATUS gate.
        monkeypatch.setattr(bd, "_public_input_reasons", lambda task: [])
    path = _record(tmp_path)
    provenance, reasons = bd._validate_repair_record(
        path, _task(status, source_sha256), REPAIRED_SHA,
        {"sha256": CHALLENGE_SHA})
    return provenance, reasons


def test_prompt_only_not_provided_with_source_hash_authorizes_repair(tmp_path, monkeypatch):
    provenance, reasons = _reasons(tmp_path, "NOT_PROVIDED", monkeypatch=monkeypatch)
    assert not reasons, reasons
    assert provenance is not None and provenance["id"] == "ProbX_demo"


def test_present_still_authorizes_repair(tmp_path, monkeypatch):
    provenance, reasons = _reasons(tmp_path, "PRESENT", monkeypatch=monkeypatch)
    assert not reasons, reasons
    assert provenance is not None


def test_not_measured_or_missing_source_still_refused(tmp_path):
    _, reasons = _reasons(tmp_path, "NOT_MEASURED")
    assert any("requires immutable public original input" in r for r in reasons)
    _, reasons = _reasons(tmp_path, "NOT_PROVIDED", source_sha256=None)
    assert any("requires immutable public original input" in r for r in reasons)
