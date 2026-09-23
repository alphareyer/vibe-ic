#!/usr/bin/env python3
"""Regression: the top runner must produce the stage-analog audit before Step 14.

The final compliance audit is a judge, not a producer belonging to the run.
If it is the first process to write ``stage_analog_compliance.json``, Step 14
correctly excludes that file as ``audit_created`` and cannot credit it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))


def test_runner_produces_scoped_analog_audit_after_analog(tmp_path, monkeypatch):
    import vibe_ic_one_shot_runner as runner

    project = tmp_path / "project"
    analog = project / "phase3" / "analog"
    analog.mkdir(parents=True)
    (analog / "analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "converter"}]}) + "\n")
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "chip_top.v").write_text("module chip_top(); endmodule\n")

    calls: list[tuple[str, str, list[str]]] = []

    def record(label, program, args, env=None):
        name = Path(program).stem
        calls.append((label, name, list(args)))
        if name == "flow_compliance_check":
            out = Path(args[args.index("--json") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"verdict": "FAIL"}) + "\n")
            return 1
        return 0

    monkeypatch.setattr(runner, "_run_phase", record)
    monkeypatch.setattr(runner, "_capture_container_image", lambda *_: {})
    monkeypatch.setattr(runner, "_capture_pdk_revision", lambda *_: {})
    monkeypatch.setattr(sys, "argv", [
        "vibe_ic_one_shot_runner", str(project), "--skip-phase1",
        "--no-dashboard",
    ])

    # R-0915-151 / icslot70 — WHAT THIS FIXTURE'S RUN LEGITIMATELY IS.
    #
    # This arm is about ORDERING and ARGV: analog runs before the compliance audit, and that
    # audit is invoked with the project and `--strict`. Every one of those assertions is below
    # and untouched.
    #
    # The `== 0` that used to stand here was scaffolding, and it encoded the behaviour the
    # front-door ruling changed. The stub returns 0 for phase2 and phase3 and writes NO report
    # for either, and it writes no completion audit at all -- so nothing in this run MEASURED
    # phase 2 or phase 3. The front door used to invent `PASS if rc == 0` from that and exit 0;
    # it now rolls the run up as NOT_MEASURED and says why, which is the whole point of
    # "the front door's verdict is the conjunction of its phases and the completion audit".
    # Asserting 0 here would be asserting the invented PASS back into existence.
    rc = runner.main()
    _doc = json.loads(
        (project / "reports" / "orchestrator" / "vibe_ic_one_shot.json").read_text())
    assert _doc["verdict"] == "NOT_MEASURED", (_doc["verdict"], _doc.get("verdict_reasons"))
    assert rc != 0, rc
    _why = " | ".join(_doc.get("verdict_reasons") or [])
    assert "phase2 NOT_MEASURED" in _why and "phase3 NOT_MEASURED" in _why, _why
    assert "completion audit is not a pass" in _why, _why

    names = [name for _label, name, _args in calls]
    analog_i = names.index("analog_one_shot_runner")
    audit_i = names.index("flow_compliance_check")
    assert analog_i < audit_i

    args = calls[audit_i][2]
    assert args[:1] == [str(project)]
    assert "--strict" in args
    assert args[args.index("--stage-id") + 1] == "stage_analog"
    assert Path(args[args.index("--json") + 1]) == (
        project / "reports" / "analog" / "stage_analog_compliance.json")

    written = json.loads(next(
        (project / "steps").glob("phase2/stage2/14_*/written.json")
    ).read_text())
    assert any(
        item["rel"] == "reports/analog/stage_analog_compliance.json"
        for item in written["produced"]
    ), written
    assert not any(
        finding.get("spec") == "reports/analog/stage_analog_compliance.json"
        for finding in written["findings"]
    ), written
