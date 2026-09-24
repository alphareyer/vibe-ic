"""A Phase-3 window dispatches one real runner site without disturbing inputs."""
import json
from pathlib import Path
from types import SimpleNamespace

import phase3_one_shot_runner as p3


def test_gds_window_preserves_outside_files_and_marks_downstream(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "phase3" / "pnr").mkdir(parents=True)
    (project / "phase3" / "synth").mkdir(parents=True)
    (project / "phase3" / "pnr" / "top.def").write_text("supplied route\n")
    (project / "phase3" / "synth" / "top_synth.v").write_text("supplied netlist\n")
    (project / "reports" / "phase3").mkdir(parents=True)
    (project / "reports" / "phase3" / "drc.rpt").write_text("old DRC\n")
    before = p3._phase3_file_manifest(project)

    # This is the EDA container's stream-out write.  Dispatch, preflight,
    # manifest comparison, stale marking and report publication are real.
    def streamout(project_, top, pdk, container):
        out = project_ / "phase3" / "pnr" / f"{top}.gds"
        out.write_bytes(b"new layout")
        return p3.StepResult("gds", "PASS", 0.0, "streamed", [str(out)])

    monkeypatch.setattr(p3, "step_gds", streamout)
    args = SimpleNamespace(entry_step="37", exit_step="37", container="fake-eda")
    selected = p3._phase3_window_sites("37", "37")
    assert selected == ["gds"]
    assert p3._run_phase3_window(project, "top", object(), args, selected) == 0

    after = p3._phase3_file_manifest(project)
    changed = {name for name in set(before) | set(after)
               if before.get(name) != after.get(name)}
    assert all(name == "phase3/pnr/top.gds" or
               name.startswith(("reports/", "steps/")) for name in changed), changed
    assert after["phase3/pnr/top.def"] == before["phase3/pnr/top.def"]
    assert after["phase3/synth/top_synth.v"] == before["phase3/synth/top_synth.v"]
    assert after["reports/phase3/drc.rpt"] == before["reports/phase3/drc.rpt"]
    assert "phase3/pnr/top.gds" in after
    report = json.loads((project / "reports" / "orchestrator" /
                         "phase3_one_shot.json").read_text())
    assert report["bounded"] is True
    assert report["audit_verdict"] == "NOT_MEASURED"
    assert report["stale_downstream"]["drc"]["status"] == "NOT_MEASURED"
    assert "gds" in report["stale_downstream"]["drc"]["reason"]
    assert report["stale_downstream"]["lvs"]["status"] == "NOT_MEASURED"


def test_window_rejects_interior_and_excludes_gds_from_15_to_31():
    assert p3._phase3_window_sites("15", "31") == ["pnr", "drc", "lvs"]
    try:
        p3._phase3_window_sites("18", "22")
    except ValueError as exc:
        assert "no independent Phase-3 dispatch" in str(exc)
    else:
        raise AssertionError("interior PnR step accepted as an entry")
