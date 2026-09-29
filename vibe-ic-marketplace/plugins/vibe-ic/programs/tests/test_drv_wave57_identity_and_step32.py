"""Review wave 57 on DRVWIRE 98cdcab70 and ruling R-0929-DRV-IDENTITY.

* An in-flow DRV capture binds the identity that exists then: the design-state
  digests, the run's code identity recorded at run start and the Phase-1 spec
  version.  GDS / LVS-netlist identity is "bound at" the final post-stream
  capture -- neither missing evidence nor NOT_MEASURED in flow -- and every
  other capture still has to bind it.  Fed the PLAN's own identity (not a
  synthetic one) a clean run can reach a complete in-flow verdict.
* Step 32 never reads another round's report, and its DRV verdict never
  blocks the handoff of an adopted candidate (R-0929-STEP32-ADOPT); it is
  Step 32's own verdict.
* The zero-segment proof is for port-to-PAD nets only (no core load).
* The plan's postroute_repair stage row follows step 32's adoption.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
_TESTS = Path(__file__).resolve().parent
for _p in (str(_PROGRAMS), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drv_capture_plan as capture_plan  # noqa: E402
import drv_run_identity  # noqa: E402
import drv_signoff_judge as drv  # noqa: E402
from test_drv_signoff_judge import (_bundle, _file, _librelane_final_sta,  # noqa: E402
                                    _port_pad_scene, _stage_receipts, _step32_report,
                                    _synthetic_scene_profile)  # noqa: F401 (autouse)


def _plan_identity(tmp_path, monkeypatch, *, record=True, docs=True):
    project, adopted = _librelane_final_sta(tmp_path, monkeypatch)
    (project / "phase3/stage3/pnr/routed.def").write_bytes(
        Path(json.loads(adopted.read_text())["def"]).read_bytes())
    if docs:
        _file(project, "phase1/generated_docs/L1_DATASHEET.json", '{"ic_name": "x"}\n')
    if record:
        drv_run_identity.record(project)
    _stage_receipts(project)
    return project, capture_plan.build(project)


def _with_plan_identity(tmp_path, plan: dict) -> dict:
    """A clean synthetic bundle carrying the plan's own run identity and its
    pre-stream netlist identity (the review's reproduction)."""
    bundle = _bundle(tmp_path / "bundle")
    identity = bundle["identity"]
    for key in ("run_id", "tree_sha", "spec_version", "capture_point", "bound_at",
                "code_identity"):
        identity[key] = plan["identity"].get(key)
    identity["lvs_netlist"] = identity["gds_netlist"] = None
    identity["artifacts"]["lvs_netlist"] = identity["artifacts"]["gds_netlist"] = {}
    return bundle


def test_the_plan_binds_run_code_and_spec_identity_recorded_at_run_start(tmp_path, monkeypatch):
    project, plan = _plan_identity(tmp_path, monkeypatch)
    record = drv_run_identity.load(project)
    identity = plan["identity"]
    assert identity["run_id"] == record["run_id"]
    assert identity["tree_sha"] == record["plugin_tree_sha256"]
    assert identity["spec_version"] == drv_run_identity.spec_version(project)
    assert identity["spec_version"].startswith("phase1-docs-sha256:")
    assert identity["capture_point"] == "in_flow"
    assert set(identity["bound_at"]) == {"lvs_netlist", "gds_netlist"}


def test_the_plans_own_identity_reaches_a_complete_in_flow_verdict(tmp_path, monkeypatch):
    _, plan = _plan_identity(tmp_path, monkeypatch)
    result = drv.judge(_with_plan_identity(tmp_path, plan))
    assert result["verdict"] == "PASS", result["not_measured"] + result["failures"]
    assert result["capture_point"] == "in_flow"
    assert result["final_signoff_capture"] is False
    assert set(result["identity_bound_later"]) == {"lvs_netlist", "gds_netlist"}


def test_an_absent_run_record_or_spec_is_not_measured(tmp_path, monkeypatch):
    _, plan = _plan_identity(tmp_path, monkeypatch, record=False, docs=False)
    assert plan["identity"]["run_id"] is None and plan["identity"]["spec_version"] is None
    result = drv.judge(_with_plan_identity(tmp_path, plan))
    assert result["verdict"] == "NOT_MEASURED"
    assert "run ID, tree SHA or specification version absent" in result["not_measured"]


def test_a_post_stream_capture_must_bind_gds_and_lvs_identity(tmp_path, monkeypatch):
    _, plan = _plan_identity(tmp_path, monkeypatch)
    bundle = _with_plan_identity(tmp_path, plan)
    bundle["identity"]["capture_point"] = "post_stream"   # bound_at is ignored
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert {"lvs_netlist sha256 absent", "gds_netlist sha256 absent"} <= set(result["not_measured"])
    assert result["final_signoff_capture"] is True


# --- step 32 -----------------------------------------------------------------

def _fake_step32_capture(monkeypatch, tmp_path, verdict: str):
    import drv_signoff_capture as capture
    monkeypatch.setattr(capture, "capture", lambda plan, out_dir, *, image=None: {
        k: plan[k] for k in ("identity", "frozen", "current", "stages", "pins", "scenes")})
    monkeypatch.setattr(drv, "judge", lambda *a, **k: {
        "name": "DRV(tran/cap/fanout)", "verdict": verdict,
        "failures": ["u/Y max_fanout 9 > 4"] if verdict == "FAIL" else [],
        "not_measured": []})


def test_step32_never_reads_the_previous_rounds_report(tmp_path, monkeypatch):
    import librelane_postroute_repair as repair
    project, adopted = _librelane_final_sta(tmp_path, monkeypatch)
    stale = project / repair.REPORT_REL
    _file(project, repair.REPORT_REL, json.dumps({"adopted": "32-cand07", "site": "after_direct_route"}))
    repair._clear_declared_repair(project)       # what run()/run_in_chain do first
    assert not stale.exists()


def test_step32_drv_verdict_does_not_gate_the_actuator_verdict(tmp_path, monkeypatch):
    import librelane_postroute_repair as repair
    project, adopted = _librelane_final_sta(tmp_path, monkeypatch)
    _fake_step32_capture(monkeypatch, tmp_path, "NOT_MEASURED")
    report = _step32_report(project, adopted)
    repair._step32_drv_signoff(project, report)
    assert report["verdict"] == "PASS"             # handoff predicate input untouched
    assert report["drv_signoff_verdict"] == "NOT_MEASURED"


def test_an_adopted_candidate_is_handed_off_and_step32_carries_the_drv_fail(tmp_path, monkeypatch):
    import librelane_postroute_repair as llprr
    import phase3_one_shot_runner as R
    project = tmp_path / "p"
    pnr = project / "phase3/stage3/pnr"
    _file(pnr, "routed.def", "ROUTE\n")
    _file(pnr, "top_pnr.v", "module top; endmodule\n")
    handed = []
    monkeypatch.setattr(llprr, "handoff", lambda project, report, targets: handed.append(targets))
    monkeypatch.setattr(R, "_record_route_promotion", lambda *a, **k: None)
    report = {"verdict": "FAIL", "adopted": "32-cand01", "code": "LL_PRR_FANOUT_VIOLATION",
              "drv_signoff_verdict": "FAIL",
              "drv_signoff": {"failures": ["u/Y max_fanout 9 > 4"], "not_measured": []},
              "baseline": {"drv_count": 9}, "final": {"drv_count": 1}}
    row = R._postroute_repair_librelane_result(project, pnr, report, 0.0,
                                               handed=True, top="top")
    assert handed, "the adopted candidate must be handed off"
    assert row.status == "FAIL" and "step-32 DRV FAIL" in row.detail
    # A clean actuator with a clean DRV judgement stays PASS.
    handed.clear()
    report.update(verdict="PASS", drv_signoff_verdict="PASS", drv_signoff={})
    assert R._postroute_repair_librelane_result(project, pnr, report, 0.0,
                                                handed=True, top="top").status == "PASS"
    assert handed


def test_the_in_chain_handoff_follows_adoption_not_the_verdict(tmp_path, monkeypatch):
    import librelane_contract as ll
    import librelane_postroute_repair as llprr
    import phase3_one_shot_runner as R
    project = tmp_path / "p"
    cand = _file(project, "phase3/librelane/32-cand01/01-vibeic-postrouterepair/state_out.json",
                 json.dumps({"odb": str(project / "c.odb"), "def": str(project / "c.def")}))
    route_def = _file(project, "route.def", "ROUTE\n")
    monkeypatch.setattr(R, "_librelane_postroute_repair_mode", lambda project: "librelane")
    monkeypatch.setattr(R, "_pg_global_connect_reassert_tcl", lambda deck: "")

    def fake_run_in_chain(project, **kw):
        _file(project, llprr.REPORT_REL, "{}")
        return {"verdict": "FAIL", "adopted": "32-cand01", "adopted_state": cand["path"],
                "drv_signoff_verdict": "NOT_MEASURED"}

    monkeypatch.setattr(llprr, "run_in_chain", fake_run_in_chain)
    monkeypatch.setattr(ll, "digest", lambda path: "0" * 64)
    out = R.postroute_repair_after_route(
        project=project, pdk=type("P", (), {"name": "synthetic"})(), image="img",
        pdk_root=tmp_path, sdc=tmp_path / "x.sdc", deck="", route_state=tmp_path / "s.json",
        route_views={"def": Path(route_def["path"])}, route_drc=0, variant_arm=None)
    assert out["views"] == {"odb": project / "c.odb", "def": project / "c.def"}


# --- annotation --------------------------------------------------------------

def test_a_zero_wiring_net_with_a_core_load_is_not_port_to_pad(tmp_path):
    from drv_signoff_annotation import derive
    args = list(_port_pad_scene(tmp_path, "*D_NET other 0.1\n*END\n",
                                " - p ( PIN p ) ( u PAD ) ( c A ) + USE SIGNAL ;\n"))
    args[1] = {**args[1], "c/A": {"kind": "pin", "cell": "logic", "cell_pin": "A",
                                  "cell_class": "std", "net": "p"}}
    result = derive(*args)
    assert result["resolved"] == []
    assert [row["pin"] for row in result["unresolved"]] == ["p"]


# --- the plan's postroute_repair row ---------------------------------------------

def test_step32_plan_carries_the_postroute_repair_row_when_adopted(tmp_path, monkeypatch):
    project, adopted = _librelane_final_sta(tmp_path, monkeypatch)
    _stage_receipts(project)
    plan = capture_plan.build(project, final_state=_step32_report(project, adopted))
    assert plan["postroute_repair_ran"] is True
    assert [row["name"] for row in plan["stages"]][-1] == "postroute_repair"
    kept = capture_plan.build(project, final_state={**_step32_report(project, adopted),
                                                    "adopted": None})
    assert kept["postroute_repair_ran"] is False
    assert "postroute_repair" not in [row["name"] for row in kept["stages"]]
