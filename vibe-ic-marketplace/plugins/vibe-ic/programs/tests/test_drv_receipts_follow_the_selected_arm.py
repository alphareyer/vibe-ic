"""A DRV stage receipt is the SHIPPED arm's, never the arm that ran last.

Review wave 58 (DRVSTACK_correctness, MAJOR): `run_chain` records a receipt for
every chain that runs a DRV stage step, and a later run replaces an earlier one.
Step 21 runs the base arm, then every seed arm (and step 32's dual reruns
`21-route-pregrt`), so the post-GRT receipt came from whichever arm ran last.
Probe on the rebased tree: the shipped arm applied set_max_fanout 10, the seed
arm 4, and the plan read 4, which hid the broader stage (DRV standard s.1
false PASS). A shipped direct arm (no probe) still read ran=True.

The route is driven through `librelane_route.execute` with the harness of
test_librelane_route (only the LibreLane edge is faked); the fake chain writes
each stage's probe files and records receipts exactly as `run_chain` does.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import test_librelane_route as T  # noqa: E402
import drv_stage_receipts as S  # noqa: E402

GOOD = T.GOOD


def _with_receipts(monkeypatch):
    real = T._fake_tools

    def fake(project, **kw):
        chain, resolve, seen = real(project, **kw)

        def chain2(proj, image, triples, **k):
            folders = chain(proj, image, triples, **k)
            for (sid, _, _), folder in zip(triples, folders):
                if sid in S.STAGE_STEPS:
                    _, command = S.STAGE_STEPS[sid]
                    for kind in ("args", "behavior.rpt", "pre.sdc"):
                        T.write(folder / S.PROBE_DIR / f"{command}.all.1.{kind}",
                                f"{k.get('lane')} {kind}\n")
            S.record_chain(proj, [(s, f, False) for (s, _, _), f in zip(triples, folders)])
            return folders

        def resolve2(proj, image, pdk, ids, **k):
            out = resolve(proj, image, pdk, ids, **k)
            gates = proj / "phase3/librelane" / k["folder"] / "flow_gates.json"
            doc = json.loads(gates.read_text())
            doc["OpenROAD.RepairDesignPostGRT"] = {"RUN_POST_GRT_DESIGN_REPAIR": True}
            gates.write_text(json.dumps(doc))
            return out
        return chain2, resolve2, seen
    monkeypatch.setattr(T, "_fake_tools", fake)


def _run(tmp_path, monkeypatch, **kw):
    _with_receipts(monkeypatch)
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    S.claim(tmp_path / "proj")
    run = T._run(tmp_path, monkeypatch, **kw)
    assert run.rc == 0, run.out
    doc, state = S.read_receipt(run.project, "post_grt_repair")
    assert state == "recorded", state
    return run, doc


def test_a_seed_arm_written_last_does_not_supply_the_shipped_row(tmp_path, monkeypatch):
    run, doc = _run(tmp_path, monkeypatch,
                    switch={"steps": {"21": "dual"}, "route_seeds": [7]},
                    route_ll=GOOD, seed_ll=dict(GOOD, wl=5000.0),
                    direct=dict(GOOD, wl=6000.0))
    assert json.loads((run.project / "phase3/tool_arms/21/selection.json")
                      .read_text())["selection"] == "librelane"
    lanes = [lane for lane, _, _ in run.seen["chains"]]
    assert lanes.index("21-route-seed7") > lanes.index("21-route")   # seed ran last
    assert doc["ran"] is True
    assert "/21-route/" in doc["tool_step"]["folder"], doc["tool_step"]


def test_a_selected_seed_arm_supplies_its_own_row(tmp_path, monkeypatch):
    """Control: when the seed arm ships, its receipt is the shipped one."""
    run, doc = _run(tmp_path, monkeypatch,
                    switch={"steps": {"21": "dual"}, "route_seeds": [7]},
                    route_ll=GOOD, seed_ll=dict(GOOD, wl=900.0, vias=290),
                    direct=dict(GOOD, wl=1100.0))
    assert json.loads((run.project / "phase3/tool_arms/21/selection.json")
                      .read_text())["selection"] == "librelane_seed7"
    assert "/21-route-seed7/" in doc["tool_step"]["folder"], doc["tool_step"]


def test_a_shipped_direct_arm_records_post_grt_as_not_run(tmp_path, monkeypatch):
    run, doc = _run(tmp_path, monkeypatch, switch={"steps": {"21": "dual"}},
                    route_ll=dict(GOOD, wl=1200.0, vias=350), direct=GOOD)
    assert json.loads((run.project / "phase3/tool_arms/21/selection.json")
                      .read_text())["selection"] == "openroad"
    assert doc["ran"] is False, doc
    assert "openroad" in doc["reason"] and "direct arm" in doc["reason"]


def test_rebind_lane_reads_the_last_folder_of_the_stage(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    S.claim(project)
    lane = project / "phase3/librelane/19-cts-hold"
    for i in (1, 4):
        folder = lane / f"{i:02d}-openroad-cts"
        T.write(folder / S.PROBE_DIR / "clock_tree_synthesis.all.1.args", f"{i}\n")
    S.rebind_lane(project, lane, ("cts",), "librelane")
    doc, _ = S.read_receipt(project, "cts")
    assert doc["tool_step"]["folder"].endswith("04-openroad-cts")


# -- step 19 dual: the CTS receipt ---------------------------------------------
import test_librelane_cts_hold as C  # noqa: E402


def _cts(tmp_path, monkeypatch, *, hold, arm_hold):
    real = C._fake_tool_run

    def fake(project, corners, **kw):
        chain, resolve, seen = real(project, corners, **kw)

        def chain2(proj, image, triples, **k):
            folders = chain(proj, image, triples, **k)
            for (sid, _, _), folder in zip(triples, folders):
                if sid in S.STAGE_STEPS:
                    T.write(folder / S.PROBE_DIR / f"{S.STAGE_STEPS[sid][1]}.all.1.args",
                            f"{k.get('lane')}\n")
            S.record_chain(proj, [(s, f, False) for (s, _, _), f in zip(triples, folders)])
            return folders
        return chain2, resolve, seen
    monkeypatch.setattr(C, "_fake_tool_run", fake)
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    S.claim(tmp_path / "proj")
    run = C._run_split(tmp_path, monkeypatch, switch={"steps": {"19": "dual", "20": "dual"}},
                       hold=hold, arm_hold=arm_hold)
    assert run.rc == 0, run.out
    doc, state = S.read_receipt(run.project, "cts")
    assert state == "recorded", state
    return run, doc


def test_a_shipped_direct_cts_arm_records_cts_as_not_run(tmp_path, monkeypatch):
    better = {c: 0.3 for c in C.CORNERS}
    worse = dict(better, nom_ss_125C_4v50=-0.5)
    run, doc = _cts(tmp_path, monkeypatch, hold=worse, arm_hold=better)
    assert json.loads((run.project / "phase3/tool_arms/19/selection.json")
                      .read_text())["selection"] == "openroad"
    assert doc["ran"] is False and "openroad" in doc["reason"], doc


def test_a_shipped_librelane_cts_arm_keeps_its_receipt(tmp_path, monkeypatch):
    """Control."""
    better = {c: 0.3 for c in C.CORNERS}
    worse = dict(better, nom_ss_125C_4v50=-0.5)
    run, doc = _cts(tmp_path, monkeypatch, hold=better, arm_hold=worse)
    assert doc["ran"] is True and "/19-cts-hold/" in doc["tool_step"]["folder"]


# -- step 32 dual: the pregrt arm reruns step 21's chain ---------------------
import test_t102_librelane_postroute_repair as P32  # noqa: E402


def _step32(tmp_path, monkeypatch, scenario):
    P32.state_the_image(monkeypatch)
    monkeypatch.setattr(P32.native, "measure", P32._tool_written_native_scene)
    project, shim, sdc, route = P32._chain_setup(tmp_path, monkeypatch, scenario)
    S.claim(project)
    lanes = {}
    for lane in ("21-route", "21-route-pregrt"):
        folder = project / "phase3/librelane" / lane / "03-openroad-repairdesignpostgrt"
        T.write(folder / S.PROBE_DIR / "repair_design.all.1.args", f"{lane}\n")
        lanes[lane] = folder
    S.record_step(project, "OpenROAD.RepairDesignPostGRT", lanes["21-route"])
    pre_state = P32.put(project / "phase3/librelane/21-route-pregrt/13-fill/state_out.json",
                        {"odb": "p.odb", "def": "p.def"})

    def variant_arm(lane, extra):
        # the rerun of step 21's chain records its receipt last, as run_chain does
        S.record_step(project, "OpenROAD.RepairDesignPostGRT", lanes[lane])
        return {"final": pre_state, "route_drc": [{"run": "drt-run-0", "markers": 0}]}
    report = P32.prr.run_in_chain(project, mode="dual", image="img", pdk="pdk",
                                  pdk_root=tmp_path, sdc=sdc, derate=(0.95, 1.05),
                                  route_state=route, route_drc=0, variant_arm=variant_arm,
                                  programs_dir=shim)
    doc, state = S.read_receipt(project, "post_grt_repair")
    assert state == "recorded", state
    return report, doc


def test_step32_postdrt_selection_ships_step21s_post_grt_row(tmp_path, monkeypatch):
    report, doc = _step32(tmp_path, monkeypatch, {
        "32-postdrt-base": P32._base(4.0, -0.3),
        "32-postdrt-cand01": P32._cand(3.99995, 0.30),
        "32-pregrt-base": P32._base(4.2, 0.10),
        "32-pregrt_postdrt-base": P32._base(4.2, 0.10)})
    assert report["selected_arm"] == "postdrt"
    assert "/21-route/" in doc["tool_step"]["folder"], doc["tool_step"]


def test_step32_pregrt_selection_ships_the_pregrt_row(tmp_path, monkeypatch):
    """Control."""
    report, doc = _step32(tmp_path, monkeypatch, {
        "32-postdrt-base": P32._base(4.0, -0.3),
        "32-pregrt-base": P32._base(4.2, 0.10),
        "32-pregrt_postdrt-base": P32._base(4.1, 0.05)})
    assert report["selected_arm"] == "pregrt"
    assert "/21-route-pregrt/" in doc["tool_step"]["folder"], doc["tool_step"]


# -- step 17 dual: the placement-repair receipt --------------------------------
import pytest  # noqa: E402
import test_mig97_placement_spares as M  # noqa: E402


@pytest.mark.parametrize("selection,ships_librelane", [
    ("librelane", True), ("openroad", False),
    ("UNDETERMINED", False)])
def test_the_placement_receipt_is_the_shipped_arms(tmp_path, monkeypatch, selection,
                                                     ships_librelane):
    monkeypatch.setattr(M.runner, "_select_placement_arm",
                        lambda *a, **k: {"selection": selection,
                                         "reason": "LL_ARM_NOT_MEASURED",
                                         "frontier": ["librelane", "openroad"]})
    orig = M.runner._prepare_librelane_floorplan_for_route

    def dual(*a, **k):
        k["placement"]["mode"] = "dual"
        return orig(*a, **k)
    monkeypatch.setattr(M.runner, "_prepare_librelane_floorplan_for_route", dual)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    S.claim(project)
    _p, _o, _s, result, _c, _plan = M._placement_producer(tmp_path, monkeypatch)
    assert result.status == "PASS", result.detail
    doc, state = S.read_receipt(project, "placement_repair")
    assert state == "recorded", state
    if ships_librelane:
        assert doc["tool_step"]["id"] == "OpenROAD.RepairDesignPostGPL", doc
        assert "/15-floorplan/" in doc["tool_step"]["folder"], doc
    else:
        assert doc["ran"] is False and "direct arm" in doc["reason"], doc
