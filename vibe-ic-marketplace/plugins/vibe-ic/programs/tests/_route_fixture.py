"""Owner-declared delivery route for synthetic front-door project inputs."""
import json
from pathlib import Path


def stage_owner_route(project: Path, route: str) -> None:
    delivery = {"ic": "DIE", "ip": "HARDMACRO"}[route]
    path = project / "input/step_0_5ic_answers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(path.read_text()) if path.exists() else {"answers": {}}
    existing = doc.get("answers", {}).get("deliverable")
    if existing not in (None, delivery):
        raise ValueError(f"fixture route {route} conflicts with {existing}")
    doc["answers"]["deliverable"] = delivery
    doc.setdefault("answer_provenance", {})["deliverable"] = {
        "answered_by": "owner",
        "citation": f"Owner: deliverable is {delivery} for this test project.",
    }
    path.write_text(json.dumps(doc) + "\n")


def stage_mixed_source_control(project: Path, program: str, args) -> None:
    """Bounded M2/M3 callers: SOURCE_CONTROL ancestors, never native proof.

    Record the actual callback separately from real M2/M3 receipts. Only the
    already mocked analog/phase2/phase3 transports receive controlled reports.
    """
    from programs.tests.test_the_front_door_rollup_edges import (
        _report, _save_complete_acceptance,
    )
    path = project / "reports/fixture/mixed_source_control_callbacks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(path.read_text()) if path.exists() else {
        "scope": "SOURCE_CONTROL ancestors; actual callback transport; not native whole-row proof",
        "callbacks": [],
    }
    record["callbacks"].append(program)
    path.write_text(json.dumps(record) + "\n")
    # The actual Default _run_phase retains, then invalidates this invocation's
    # audit before spawning M2/M3/M4. Keep the same transport contract here.
    if program in {"mixed_signal_power_domain_run", "mixed_signal_m3_run",
                   "mixed_signal_signoff_run"} and "--json" in args:
        audit = Path(args[args.index("--json") + 1])
        if audit.is_file():
            prior = path.parent / (program + "_prior_audit.json")
            prior.write_bytes(audit.read_bytes())
        audit.unlink(missing_ok=True)
    report = {
        "analog_one_shot_runner": "analog_one_shot.json",
        "phase2_one_shot_runner": "phase2_one_shot.json",
        "phase3_one_shot_runner": "phase3_one_shot.json",
    }.get(program)
    if report is None:
        return
    _report(project, report, {"verdict": "PASS", "scope": "SOURCE_CONTROL"})
    if program == "analog_one_shot_runner":
        _save_complete_acceptance(project, "A9", "PASS", runner="analog_one_shot_runner")
