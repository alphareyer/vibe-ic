"""A Phase-3 window dispatches one real runner site without disturbing inputs."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import phase3_one_shot_runner as p3


def test_real_runner_cli_exposes_window():
    proc = subprocess.run([sys.executable, str(Path(p3.__file__)), "--help"],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "--entry-step" in proc.stdout and "--exit-step" in proc.stdout


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
    allowed_reports = {
        "reports/audit/step_preflight.json",
        "reports/audit/phase23_completion_audit.json",
        "reports/audit/steps_view.json",
        "reports/orchestrator/phase3_one_shot.json",
        "reports/write_ledger.json",
    }
    assert all(name == "phase3/pnr/top.gds" or
               name in allowed_reports or name.startswith("steps/")
               for name in changed), changed
    assert after["phase3/pnr/top.def"] == before["phase3/pnr/top.def"]
    assert after["phase3/synth/top_synth.v"] == before["phase3/synth/top_synth.v"]
    assert after["reports/phase3/drc.rpt"] == before["reports/phase3/drc.rpt"]
    assert "phase3/pnr/top.gds" in after
    report = json.loads((project / "reports" / "orchestrator" /
                         "phase3_one_shot.json").read_text())
    assert report["bounded"] is True
    assert report["steps_view"]["status"] == "OK"
    assert report["audit_verdict"] == "NOT_MEASURED"
    audit = json.loads((project / "reports" / "audit" /
                        "phase23_completion_audit.json").read_text())
    assert audit["scope"]["whole_flow"] is False
    assert audit["audit_kind"].startswith("bounded_invalidation")
    assert report["stale_downstream"]["drc"]["status"] == "NOT_MEASURED"
    assert "gds" in report["stale_downstream"]["drc"]["reason"]
    assert report["stale_downstream"]["lvs"]["status"] == "NOT_MEASURED"


def test_window_rejects_interior_and_excludes_gds_from_15_to_31():
    assert p3._phase3_window_sites("15", "31") == ["pnr", "drc", "lvs"]
    assert p3._phase3_window_sites("15", "22") == ["pnr"]
    assert p3._phase3_window_sites("9", "23") == ["synth", "pnr"]
    try:
        p3._phase3_window_sites("18", "22")
    except ValueError as exc:
        assert "no independent Phase-3 dispatch" in str(exc)
    else:
        raise AssertionError("interior PnR step accepted as an entry")


def test_changed_route_cannot_sign_off_old_gds(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "phase3" / "synth").mkdir(parents=True)
    (project / "phase3" / "pnr").mkdir(parents=True)
    (project / "phase3" / "synth" / "top_synth.v").write_text("netlist")
    (project / "phase3" / "pnr" / "top.gds").write_bytes(b"old GDS")
    (project / "phase2" / "stage2" / "synth").mkdir(parents=True)
    (project / "phase2" / "stage2" / "synth" /
     "post_dft_netlist.v").write_text("netlist")

    def route(project_, top, pdk, container, **kwargs):
        out = project_ / "phase3" / "pnr" / f"{top}.def"
        out.write_text("new route")
        canonical = project_ / "phase3" / "stage3" / "pnr" / "routed.def"
        canonical.parent.mkdir(parents=True)
        canonical.write_text("new route")
        return p3.StepResult("pnr", "PASS", 0.0, "routed", [str(out)])

    def should_not_run(*args, **kwargs):
        raise AssertionError("PV consumed the old GDS")

    monkeypatch.setattr(p3, "step_pnr", route)
    monkeypatch.setattr(p3, "step_drc", should_not_run)
    monkeypatch.setattr(p3, "step_lvs", should_not_run)
    args = SimpleNamespace(entry_step="15", exit_step="31", container="fake-eda",
                           die_um="auto", util=0.3, spare_density=0.02)
    selected = p3._phase3_window_sites("15", "31")
    assert p3._run_phase3_window(project, "top", object(), args, selected) == 1
    report = json.loads((project / "reports" / "orchestrator" /
                         "phase3_one_shot.json").read_text())
    assert report["steps"][-1]["name"] == "drc"
    assert report["steps"][-1]["status"] == "NOT_MEASURED"
    assert "gds is outside this window" in report["steps"][-1]["detail"]
    assert (project / "phase3" / "pnr" / "top.gds").read_bytes() == b"old GDS"


def test_real_gds_step_with_container_write_keeps_other_stage_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    pnr = project / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / "top.def").write_text("DESIGN top ;\nEND DESIGN\n")
    (project / "phase3" / "synth").mkdir()
    (project / "phase3" / "synth" / "top_synth.v").write_text("netlist")
    (project / "reports" / "phase3").mkdir(parents=True)
    (project / "reports" / "phase3" / "drc.rpt").write_text("prior DRC")
    before = p3._phase3_file_manifest(project)

    # The real step_gds builds its script and reports. Only the container's
    # stream-out file write is faked; no Phase-3 dispatch function is replaced.
    monkeypatch.setattr(p3, "_magic_def_to_gds",
                        lambda *a, **k: (False, "unavailable"))

    calls = []

    def container(_name, _cmd, *args, **kwargs):
        calls.append(_cmd)
        for output in kwargs.get("outputs", []):
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(b"fake container GDS")
        return 0, "streamed", ""

    monkeypatch.setattr(p3, "_docker_exec", container)
    pdk = p3.PdkConfig(name="fixture", liberty="lib", tech_lef="tech.lef",
                       cell_lef="cell.lef", cell_gds="cell.gds", site="site",
                       drc_deck=None)
    args = SimpleNamespace(entry_step="37", exit_step="37", container="fake-eda")
    p3._run_phase3_window(project, "top", pdk, args, ["gds"])
    after = p3._phase3_file_manifest(project)
    changed = {name for name in set(before) | set(after)
               if before.get(name) != after.get(name)}
    allowed_reports = {
        "reports/audit/phase23_completion_audit.json",
        "reports/audit/step_preflight.json",
        "reports/audit/steps_view.json",
        "reports/orchestrator/phase3_one_shot.json",
        "reports/phase3/signoff_merge_probe.json",
        "reports/phase3/tapeout_declaration_publish.json",
        "reports/phase3/technology_units.json",
        "reports/write_ledger.json",
    }
    assert all(name.startswith(("phase3/stage3/pnr/", "steps/")) or
               name in allowed_reports for name in changed), sorted(changed)
    assert calls, "the real GDS step never reached the container"
    assert any(name.endswith(".gds") for name in after)
    assert after["phase3/stage3/pnr/top.def"] == before["phase3/stage3/pnr/top.def"]
    assert after["phase3/synth/top_synth.v"] == before["phase3/synth/top_synth.v"]
    assert after["reports/phase3/drc.rpt"] == before["reports/phase3/drc.rpt"]
