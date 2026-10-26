"""Issue #3480: ordinary recovery selects evidence-bounded existing entries."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


PROGRAMS = Path(os.environ.get(
    "VIBEIC_TEST_PROGRAMS", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as phase3  # noqa: E402
import step_output_collector as collector  # noqa: E402
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


def _failed_consumer_receipt(project: Path, producer="16", consumer="32"):
    output_rel = f"outputs/{producer}.txt"
    output_sha = _sha((project / output_rel).read_bytes())
    attempt = collector.begin_step(
        project, consumer, {output_rel: {"sha256": output_sha}})
    collector.finish_step(
        project, attempt, verdict="FAIL", canonical_pass=False,
        result={"canonical_consumer": {"verdict": "FAIL"}})
    path = project / attempt["storage_root"] / attempt["folder"] / "outputs.json"
    return {
        "step_id": producer,
        "receipt": str(path.relative_to(project)),
        "receipt_sha256": _sha(path.read_bytes()),
        "output_path": output_rel,
        "output_sha256": output_sha,
    }


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


def test_free_form_disproval_cannot_schedule_producer(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    plan = front._minimal_resume_plan(tmp_path, _request(
        disproven_outputs=[{"step_id": "16",
                            "evidence": "consumer receipt rejects clock topology"}]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []


def test_hash_bound_failed_consumer_receipt_invalidates_unchanged_output(
        tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["execution_steps"] == ["16"]
    assert "failed consumer 32 receipt" in plan["reason"]


def test_mismatched_consumer_receipt_binding_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    proof["output_sha256"] = "0" * 64
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("subject hash mismatch" in item for item in plan["unknown_impact"])


def test_forged_consumer_receipt_hash_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    proof["receipt_sha256"] = "f" * 64
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("receipt hash mismatch" in item for item in plan["unknown_impact"])


def test_self_hashed_unindexed_consumer_receipt_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    authentic = tmp_path / proof["receipt"]
    forged = tmp_path / "steps/forged/not-indexed/32_fake/outputs.json"
    forged.parent.mkdir(parents=True)
    forged.write_bytes(authentic.read_bytes())
    proof["receipt"] = str(forged.relative_to(tmp_path))
    proof["receipt_sha256"] = _sha(forged.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("registered complete consumer attempt" in item
               for item in plan["unknown_impact"])


def test_escaping_consumer_receipt_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    outside = tmp_path.parent / "outside-consumer-outputs.json"
    outside.write_bytes((tmp_path / proof["receipt"]).read_bytes())
    proof["receipt"] = str(Path("..") / outside.name)
    proof["receipt_sha256"] = _sha(outside.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []


def test_incomplete_consumer_capture_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    receipt = tmp_path / proof["receipt"]
    record = json.loads(receipt.read_text())
    del record["capture_errors"]
    receipt.write_text(json.dumps(record))
    proof["receipt_sha256"] = _sha(receipt.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []


def test_wrong_consumer_identity_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    receipt = tmp_path / proof["receipt"]
    record = json.loads(receipt.read_text())
    record["id"] = "18"
    receipt.write_text(json.dumps(record))
    proof["receipt_sha256"] = _sha(receipt.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []


def test_wrong_declared_consumer_input_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    receipt = tmp_path / proof["receipt"]
    attempt_path = receipt.parent / "attempt.json"
    record = json.loads(receipt.read_text())
    attempt = json.loads(attempt_path.read_text())
    for document in (record, attempt):
        document["inputs"][proof["output_path"]]["sha256"] = "e" * 64
    receipt.write_text(json.dumps(record))
    attempt_path.write_text(json.dumps(attempt))
    proof["receipt_sha256"] = _sha(receipt.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("subject hash mismatch" in item for item in plan["unknown_impact"])


def test_wrong_captured_consumer_input_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    receipt = tmp_path / proof["receipt"]
    attempt_path = receipt.parent / "attempt.json"
    record = json.loads(receipt.read_text())
    attempt = json.loads(attempt_path.read_text())
    for document in (record, attempt):
        document["input_files"][0]["sha256"] = "e" * 64
    receipt.write_text(json.dumps(record))
    attempt_path.write_text(json.dumps(attempt))
    proof["receipt_sha256"] = _sha(receipt.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("input capture does not bind" in item
               for item in plan["unknown_impact"])


def test_nonfailed_consumer_receipt_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    receipt = tmp_path / proof["receipt"]
    record = json.loads(receipt.read_text())
    record["status"] = "PASS"
    record["canonical_pass"] = True
    receipt.write_text(json.dumps(record))
    proof["receipt_sha256"] = _sha(receipt.read_bytes())
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("not a bound failed consumer" in item
               for item in plan["unknown_impact"])


def test_stale_consumer_receipt_binding_is_unresolved(tmp_path, monkeypatch):
    _install_graph(monkeypatch)
    _checkpoint_tree(tmp_path)
    proof = _failed_consumer_receipt(tmp_path)
    (tmp_path / proof["output_path"]).write_text("later output bytes")
    plan = front._minimal_resume_plan(
        tmp_path, _request(disproven_outputs=[proof]))
    assert plan["status"] == "UNRESOLVED"
    assert plan["execution_steps"] == []
    assert any("stale" in item for item in plan["unknown_impact"])


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
    assert front._minimal_resume_entry(plan) == ("15", "15")


def test_automatic_or_disjoint_multi_step_selection_has_no_window(monkeypatch):
    _install_graph(monkeypatch)
    assert front._minimal_resume_entry({
        "status": "READY", "execution_steps": ["17", "18"]}) is None
    assert front._minimal_resume_entry({
        "status": "READY", "selection": "explicit_owner_window",
        "execution_steps": ["17", "32"]}) is None


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


def test_actual_yaml_graph_excludes_external_pseudo_sources(tmp_path, monkeypatch):
    import step_output_collector as collector
    order, graph = front._resume_dependency_graph()
    assert "external" not in graph
    assert all("external" not in deps for deps in graph.values())
    monkeypatch.setattr(collector, "continuation_evidence", lambda *_: {
        "checkpoints": [
            {"step_id": sid, "observation": "unchanged", "differences": []}
            for sid in order
        ]
    })
    plan = front._minimal_resume_plan(
        tmp_path, _request("8", revalidate_consumer=True,
                           reason="same-byte reader repair"))
    assert plan["status"] == "READY"
    assert plan["execution_steps"] == ["8"]


def test_actual_frontdoor_resume_request_bounds_existing_entry(
        tmp_path, monkeypatch):
    import step_output_collector as collector
    from _route_fixture import stage_owner_route
    order, _ = front._resume_dependency_graph()
    monkeypatch.setattr(collector, "continuation_evidence", lambda *_: {
        "requested_steps": order,
        "automatically_added_steps": [],
        "unknown_impact": [],
        "checkpoints": [
            {"step_id": sid, "observation": "unchanged", "differences": []}
            for sid in order
        ],
        "retained_unchanged_steps": order,
    })
    request = tmp_path / "reports/orchestrator/resume_request.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps(_request(
        "17", revalidate_consumer=True, reason="ordinary placement consumer recovery")))
    stage_owner_route(tmp_path, "ic")
    monkeypatch.setattr(front, "_capture_container_image", lambda *_: {})

    class ReachedExistingEntry(Exception):
        pass

    def dispatch(label, runner, argv, **kwargs):
        assert Path(runner).name == "phase3_one_shot_runner.py"
        assert argv[argv.index("--entry-step") + 1] == "17"
        assert argv[argv.index("--exit-step") + 1] == "17"
        plans = list((tmp_path / "reports/orchestrator").glob(
            "minimal_resume_plan-*.json"))
        assert len(plans) == 1
        assert json.loads(plans[0].read_text())["execution_steps"] == ["17"]
        raise ReachedExistingEntry

    monkeypatch.setattr(front, "_run_phase", dispatch)
    monkeypatch.setattr(sys, "argv", [
        "runner", str(tmp_path), "--route", "ic", "--execution-mode", "default",
        "--resume-request", str(request), "--container", "", "--no-dashboard",
    ])
    try:
        front.main()
    except ReachedExistingEntry:
        pass
    else:
        raise AssertionError("ordinary resume did not reach the existing exact entry")


def test_actual_frontdoor_owner_contiguous_window_reaches_existing_entry(
        tmp_path, monkeypatch):
    from _route_fixture import stage_owner_route
    order, _ = front._resume_dependency_graph()
    monkeypatch.setattr(collector, "continuation_evidence", lambda *_: {
        "requested_steps": order, "automatically_added_steps": [],
        "unknown_impact": [], "retained_unchanged_steps": order,
        "checkpoints": [{"step_id": sid, "observation": "unchanged",
                         "differences": []} for sid in order],
    })
    request = tmp_path / "reports/orchestrator/resume_request.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps(_request(
        explicit_rerun_steps=["17", "18"],
        reason="owner requested fresh placement through CTS")))
    stage_owner_route(tmp_path, "ic")
    monkeypatch.setattr(front, "_capture_container_image", lambda *_: {})

    class ReachedExistingWindow(Exception):
        pass

    def dispatch(label, runner, argv, **kwargs):
        assert Path(runner).name == "phase3_one_shot_runner.py"
        assert argv[argv.index("--entry-step") + 1] == "17"
        assert argv[argv.index("--exit-step") + 1] == "18"
        plan_path, = (tmp_path / "reports/orchestrator").glob(
            "minimal_resume_plan-*.json")
        plan = json.loads(plan_path.read_text())
        assert plan["execution_steps"] == ["17", "18"]
        assert plan["reason"] == "explicit rerun request: owner requested fresh placement through CTS"
        raise ReachedExistingWindow

    monkeypatch.setattr(front, "_run_phase", dispatch)
    monkeypatch.setattr(sys, "argv", [
        "runner", str(tmp_path), "--route", "ic", "--execution-mode", "default",
        "--resume-request", str(request), "--container", "", "--no-dashboard",
    ])
    try:
        front.main()
    except ReachedExistingWindow:
        pass
    else:
        plan_path, = (tmp_path / "reports/orchestrator").glob(
            "minimal_resume_plan-*.json")
        plan = json.loads(plan_path.read_text())
        assert plan["status"] == "READY"
        assert plan["execution_steps"] == ["17", "18"]
        raise AssertionError("owner contiguous rerun did not reach the existing window")


def test_loop2converge_ordinary_guidance_uses_same_program_decision():
    text = (PROGRAMS.parent / "skills/loop2converge/SKILL.md").read_text()
    section = text.split("### 1.2 — Establish the smallest necessary continuation", 1)[1]
    section = section.split("## 2 —", 1)[0]
    assert "Route every ordinary recovery" in section
    assert "--resume-request <json>" in section
    assert "minimal_resume_plan-<timestamp>.json" in section
    assert "Program enforcement of this continuation selection is tracked separately" not in section
