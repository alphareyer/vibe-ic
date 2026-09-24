"""D9: a real declared output's red reaches the completion audit.

The checked-in slice is derived from run23: one invocation record, its
declared output, and the run's RTL so the P0 umbrella executes. The report's
GDS location was made project-relative for portability, and the fixture
record explicitly marks that transformation and hashes the transformed bytes.
The full 64-row/42-path corpus was first copied to scratch and checked PASS.
Only the test flow's unrelated dependency edge is removed; the production
provenance gate, P0 dispatch, verdict projection, and audit writer are used.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

import flow_compliance_check as F  # noqa: E402
from _hostpaths import require_repo  # noqa: E402

GATE = "provenance_output_hash_completeness_check"


def test_real_declared_output_verdict_reaches_completion_audit(
        tmp_path, monkeypatch, capsys):
    corpus = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                          "programs", "tests", "data", "reemit_d9_run23")
    project = tmp_path / "run"
    shutil.copytree(corpus, project)
    source = yaml.safe_load((PROGRAMS.parent / "flow" /
                             "phase1_phase2_phase3.yaml").read_text())
    p0 = copy.deepcopy(next(s for s in source["steps"] if s["id"] == "P0"))
    p0["blocks_on"] = []  # isolate this gate from unrelated Phase-1 evidence
    source["steps"] = [p0]
    source["stages"] = [s for s in source["stages"] if s["id"] == p0["stage"]]
    source["total_steps"] = 1
    flow = tmp_path / "flow.yaml"
    flow.write_text(yaml.safe_dump(source))
    monkeypatch.setattr(F, "_STRUCTURAL_RTL_GATES", (GATE,))

    def audit():
        rc = F.main([str(project), "--flow-def", str(flow),
                     "--strict-structural"])
        capsys.readouterr()
        report = json.loads((project / "reports" / "audit" /
                             "phase23_completion_audit.json").read_text())
        return rc, report

    rc, good = audit()
    assert (rc, good["verdict"]) == (0, "PASS")
    gate = next(g for g in good["steps"][0]["gate_records"]
                if g["name"] == GATE)
    assert gate["verdict"] == "PASS" and gate["evidence"]["exit_code"] == 0

    ledger = project / "provenance.jsonl"
    row = json.loads(ledger.read_text())
    rel = next(iter(row["outputs"]))
    row["outputs"][rel] = "sha256:" + "0" * 64
    ledger.write_text(json.dumps(row) + "\n")  # exactly one record mutated
    rc, bad = audit()
    assert rc == 1 and bad["verdict"] == "FAIL"
    assert GATE in bad["failed_gates"]
    assert any(g["name"] == GATE and g["verdict"] == "FAIL"
               for g in bad["gates"])
