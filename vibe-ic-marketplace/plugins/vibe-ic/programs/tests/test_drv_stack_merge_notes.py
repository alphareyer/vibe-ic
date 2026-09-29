"""The DRV stack x F15 / CAPSCOPE merge notes (root, 2026-09-29), folded.

1. F15's "the DRV bundle's routed DEF must be the DEF STAPostPNR timed" binds
   the step-23 (in-flow) capture only; the FINAL capture binds the shipped
   DEF/GDS through its post-stream derivation (step 32 repair / step 34 fill
   may change the DEF after step 23 timed it).
2. One DEF-identity comparison, shared by the CLI and step 32.
3. The runner names the judge's REFUSED record instead of "receipt unreadable".
4. io_disclosure requires measured > the tool limit: with the design-scope
   0.2 pF margin every IO pin carries limit 0.2 (R-0929-CAP-MARGIN-SCOPE).
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import drv_signoff_judge as drv  # noqa: E402
import test_f15_step23_gates_read_the_tool_arm as F  # noqa: E402
from test_drv_signoff_judge import (_bundle, _file, _violate,  # noqa: E402
                                    _synthetic_scene_profile)  # noqa: F401 (autouse)


def _other_def_bundle(tmp_path):
    project = F.gate_inputs(F.tool_project(tmp_path))
    routed = F._write(project / "phase3/stage3/pnr/routed.def", "DESIGN other ;\n")
    F._write(project / "reports/phase3/sta/drv_signoff_bundle.json", json.dumps({
        "identity": {"artifacts": {"def": {"sha256": F._sha(routed)}}}}))
    return project


def _timed(project, tmp_path, *argv):
    out = tmp_path / "drv.json"
    drv.main([str(project), "--json", str(out), *argv])
    return [r for r in json.loads(out.read_text()).get("not_measured", [])
            if "STAPostPNR timed" in r]


def test_the_final_capture_is_not_bound_to_the_def_step23_timed(tmp_path):
    project = _other_def_bundle(tmp_path)
    assert _timed(project, tmp_path, "--capture-point", "post_stream") == []


def test_an_in_flow_capture_still_is(tmp_path):
    """Control (F15's own rule, both spellings of an in-flow gate)."""
    project = _other_def_bundle(tmp_path)
    assert _timed(project, tmp_path, "--capture-point", "in_flow")
    assert _timed(project, tmp_path / "b")


def test_one_def_identity_comparison(tmp_path):
    layout = _file(tmp_path, "a.def", "DESIGN a ;\n")
    bundle = {"identity": {"artifacts": {"def": {"sha256": layout["sha256"]}}}}
    assert drv.def_identity(bundle, Path(layout["path"])) is None
    assert drv.def_identity(bundle, tmp_path / "missing.def") == "absent"
    assert drv.def_identity(bundle, Path(_file(tmp_path, "b.def", "x\n")["path"])) == "differs"
    assert drv.def_identity({}, Path(layout["path"])) == "differs"


def test_step32_names_its_final_def_through_the_shared_comparison(tmp_path, monkeypatch):
    import librelane_postroute_repair as prr
    import drv_capture_plan
    project = tmp_path / "p"
    bundle = {"identity": {"artifacts": {"def": {"sha256": "0" * 64}}}}
    monkeypatch.setattr(drv_capture_plan, "capture_and_publish", lambda *a, **k: _file(
        project, "reports/phase3/sta/drv_signoff_bundle.json", json.dumps(bundle)))
    monkeypatch.setattr(drv, "judge", lambda b, project=None: {"verdict": "PASS"})
    seen = []
    real = drv.def_identity
    monkeypatch.setattr(drv, "def_identity", lambda b, p: seen.append(p) or real(b, p))
    final = _file(project, "final.def", "DESIGN final ;\n")
    state = _file(project, "state.json", json.dumps({"def": final["path"]}))
    report = {"final": {"sta_state": state["path"]}, "adopted_state": state["path"]}
    prr._step32_drv_signoff(project, report)
    assert seen == [Path(final["path"])]
    assert report["drv_signoff"]["verdict"] == "NOT_MEASURED"
    assert "step 32 final routed DEF differs from judged layout identity" in \
        report["drv_signoff"]["not_measured"]


def test_the_runner_names_the_judges_refusal(tmp_path, monkeypatch):
    import phase3_one_shot_runner as R
    import drv_capture_plan
    monkeypatch.setattr(drv_capture_plan, "capture_and_publish", lambda *a, **k: None)
    out = tmp_path / "reports/phase3/sta/drv_signoff.json"

    def run(cmd, **kw):
        _file(tmp_path, "reports/phase3/sta/drv_signoff.json", json.dumps({
            "name": "DRV(tran/cap/fanout)", "verdict": "REFUSED",
            "refusal": "LL_STA_ARTEFACT_UNBOUND", "reason": "tool arm unreadable"}))
        return SimpleNamespace(returncode=1, stdout="REFUSED DRV", stderr="")
    monkeypatch.setattr(R._pr, "run", run)
    row = R._run_declared_signoff_gate(tmp_path, "drv_signoff", "drv_signoff_judge.py",
                                       str(out.relative_to(tmp_path)), ())
    assert row.status != "PASS"
    assert "DRV judge REFUSED (LL_STA_ARTEFACT_UNBOUND)" in row.detail, row.detail
    assert "unreadable" not in row.detail.split("tool arm unreadable")[0]


def _io_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    io = _file(tmp_path, "io.lib", '''library (io) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_capacitance : 999;
 cell (pad) { pad_cell : true; pin (Y) { direction : output; } }
}''')
    bundle["current"]["liberties"].append({"name": "io", **io})
    bundle["frozen"]["liberties"]["io"] = io["sha256"]
    for key in ("frozen", "current"):
        bundle[key]["scene_liberties"]["typ_nom"].append("io")
    bundle["scenes"][0]["linked_liberties"].append({"name": "io", **io})
    bundle["scenes"][0]["liberty"] = "io"
    bundle["pins"]["u/Y"].update(liberty="io", cell="pad", driver_cell="pad",
                                 cell_class="IO", net_class="IO")
    return bundle


def test_an_io_pin_under_its_tool_limit_is_not_a_disclosure(tmp_path):
    bundle = _io_bundle(tmp_path)
    _violate(bundle, tmp_path, "max_capacitance", value=.1, limit=.2,
             net_class="IO", cell_class="IO")
    bundle["scenes"][0]["counters"]["max_capacitance"] = 0
    scene = bundle["scenes"][0]
    scene["counter_report"] = _file(tmp_path, "counter.log", "".join(
        f"DRV_COUNTER {k} {scene['counters'][k]}\n" for k in drv.KINDS))
    from test_drv_signoff_judge import _report, _refresh_scripts
    scene["report"] = _file(tmp_path, "violators.rpt", _report(
        fanout=2, cap=.1, cap_limit=.2, violators=True))
    _refresh_scripts(bundle, tmp_path)
    result = drv.judge(bundle)
    assert result["io_margin_disclosures"] == [], result["io_margin_disclosures"]
    assert result["verdict"] == "PASS", result["not_measured"] + result["failures"]


def test_an_io_pin_over_its_tool_limit_still_is(tmp_path):
    """Control (DRV standard section 4: listed, not gated)."""
    bundle = _io_bundle(tmp_path)
    _violate(bundle, tmp_path, "max_capacitance", value=.3, limit=.2,
             net_class="IO", cell_class="IO")
    result = drv.judge(bundle)
    assert result["verdict"] == "PASS"
    assert [r["failed_tier"] for r in result["io_margin_disclosures"]] == [
        "IO_STD_CELL_MARGIN_DISCLOSURE"]
