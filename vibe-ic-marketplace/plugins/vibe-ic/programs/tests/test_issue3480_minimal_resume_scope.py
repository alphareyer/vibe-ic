"""Issue #3480: ordinary recovery selects evidence-bounded existing entries."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as phase3  # noqa: E402
import vibe_ic_one_shot_runner as front  # noqa: E402


ORDER = ["7", "8", "9", "15", "16", "17", "18", "32"]
GRAPH = {
    "7": set(), "8": {"7"}, "9": {"7"}, "15": {"9", "7"},
    "16": {"15", "7"}, "17": {"16", "15"}, "18": {"17"},
    "32": {"18"},
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checkpoint_tree(project: Path):
    pointers = {}
    records = {}
    for sid in ORDER:
        inp = project / f"inputs/{sid}.txt"
        out = project / f"outputs/{sid}.txt"
        inp.parent.mkdir(parents=True, exist_ok=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        inp.write_bytes(f"input-{sid}".encode())
        out.write_bytes(f"output-{sid}".encode())
        folder = f"phase/stage/{sid}_retained"
        record_path = project / "steps" / folder / "outputs.json"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": sid, "status": "PASS", "canonical_pass": True,
            "inputs": {f"inputs/{sid}.txt": {"sha256": _sha(inp.read_bytes())}},
            "outputs": [{"rel": f"outputs/{sid}.txt",
                          "sha256": _sha(out.read_bytes())}],
        }
        record_path.write_text(json.dumps(record))
        pointers[sid] = {"folder": folder, "inputs": record["inputs"]}
        records[sid] = record_path
    (project / "steps/latest_pass.json").write_text(json.dumps({"steps": pointers}))
    return records


def _request(consumer="32", **extra):
    return {"schema": "vibeic/minimal-resume-request/1",
            "consumer_step": consumer, **extra}


def _install_graph(monkeypatch):
    monkeypatch.setattr(front, "_resume_dependency_graph",
                        lambda: (ORDER, GRAPH))


def test_same_sdc_bytes_path_repair_launches_only_reader(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    old = tmp_path / "inputs/8.txt"
    new = tmp_path / "repaired/path/constraint.sdc"
    new.parent.mkdir(parents=True)
    new.write_bytes(old.read_bytes())
    old.unlink()

    plan = front._minimal_resume_plan(tmp_path, _request(
        "8", revalidate_consumer=True, reason="SDC resolver repaired",
        equivalent_relocations=[{"retained_path": "inputs/8.txt",
                                 "current_path": "repaired/path/constraint.sdc"}]))

    assert plan["execution_steps"] == ["8"]
    assert not ({"9", "15", "16", "17", "18"} & set(plan["execution_steps"]))
    assert front._minimal_resume_entry(plan) == ("8", "8")


def test_unrelated_source_change_has_zero_producer_launches(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    plan = front._minimal_resume_plan(
        tmp_path, _request(reason="unrelated plugin source changed"))
    assert plan["status"] == "NO_ACTION"
    assert plan["execution_steps"] == []


def test_changed_constraint_selects_earliest_affected_producer(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    (tmp_path / "inputs/15.txt").write_text("new timing constraint")
    plan = front._minimal_resume_plan(tmp_path, _request())
    assert plan["execution_steps"] == ["15"]
    assert "input inputs/15.txt changed" in plan["reason"]
    # The selected action is consumed by the actual Default Phase-3 selector.
    assert phase3._phase3_window_sites(*front._minimal_resume_entry(plan)) == ["default_pnr"]


def test_missing_output_selects_owner_and_defers_dependents(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    (tmp_path / "outputs/17.txt").unlink()
    plan = front._minimal_resume_plan(tmp_path, _request())
    assert plan["execution_steps"] == ["17"]
    assert "32" in plan["deferred_for_replanning"]
    assert "15" in plan["reused_checkpoints"]


def test_proven_bad_output_overrides_unchanged_hashes(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    plan = front._minimal_resume_plan(tmp_path, _request(
        disproven_outputs=[{"step_id": "16",
                            "evidence": "consumer receipt rejects clock topology"}]))
    assert plan["execution_steps"] == ["16"]
    assert "disproven" in plan["reason"]


def test_insufficient_provenance_is_unresolved_without_launch(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    index = json.loads((tmp_path / "steps/latest_pass.json").read_text())
    del index["steps"]["16"]
    (tmp_path / "steps/latest_pass.json").write_text(json.dumps(index))
    plan = front._minimal_resume_plan(tmp_path, _request())
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("step 16" in item for item in plan["unknown_impact"])


def test_explicit_rerun_reason_is_retained(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    plan = front._minimal_resume_plan(tmp_path, _request(
        explicit_rerun_steps=["15"], reason="owner requested fresh floorplan"))
    assert plan["execution_steps"] == ["15"]
    assert plan["reason"] == "explicit rerun request: owner requested fresh floorplan"


def test_planning_preserves_historical_records_and_neutral_project(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    project = tmp_path / "neutral_uart"
    records = _checkpoint_tree(project)
    before = {sid: (path.read_bytes(), path.stat().st_mtime_ns)
              for sid, path in records.items()}
    plan = front._minimal_resume_plan(project, _request(
        "8", revalidate_consumer=True, reason="reader repair"))
    after = {sid: (path.read_bytes(), path.stat().st_mtime_ns)
             for sid, path in records.items()}
    assert plan["execution_steps"] == ["8"]
    assert plan["historical_records_mutated"] is False
    assert before == after


def test_reverse_old_broad_window_would_launch_forbidden_upstream(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    plan = front._minimal_resume_plan(tmp_path, _request(
        "8", revalidate_consumer=True, reason="same-byte reader repair"))
    old_broad_decision = ORDER[ORDER.index("7"):ORDER.index("18") + 1]
    assert {"9", "15", "16", "17", "18"} <= set(old_broad_decision)
    assert not ({"9", "15", "16", "17", "18"} & set(plan["execution_steps"]))
