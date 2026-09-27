"""Steps 31 / 37.3 / 37.4 / 37.5ic on LibreLane (lane mig105).

Every LibreLane step here is faked only at the tool boundary (``docker run``):
the fake writes the files LibreLane writes into a step folder
(``state_in.json``, ``state_out.json``, reports) and nothing else.  Image:
none (host tests); the real-tool proof is in the lane report.
"""
import importlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
contract = importlib.import_module("librelane_contract")
pv = importlib.import_module("librelane_pv_signoff")
PLUGIN = PROGRAMS / "librelane_plugins" / "librelane_plugin_vibeic"


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def _folder(root, name, step, before, after):
    """A LibreLane step folder as the tool leaves it."""
    folder = root / name
    _put(folder / "config.json", {"meta": {"step": step}})
    _put(folder / "state_in.json", {"metrics": before})
    _put(folder / "state_out.json", {"metrics": after})
    return folder


# ── judge_pv: THE ONE RULE ──────────────────────────────────────────────────
def test_clean_drc_chain_passes(tmp_path):
    folders = [
        _folder(tmp_path, "01-magic-drc", "Magic.DRC", {}, {"magic__drc_error__count": 0}),
        _folder(tmp_path, "02-klayout-drc", "KLayout.DRC", {"magic__drc_error__count": 0},
                {"magic__drc_error__count": 0, "klayout__drc_error__count": 0}),
    ]
    record = pv.judge_pv(folders, ("Magic.DRC", "KLayout.DRC"), tmp_path / "r.json")
    assert record["verdict"] == "PASS"
    assert record["metrics"]["klayout__drc_error__count"]["value"] == 0
    assert json.loads((tmp_path / "r.json").read_text())["verdict"] == "PASS"


def test_a_producer_that_wrote_nothing_is_not_measured_never_zero(tmp_path):
    folders = [
        _folder(tmp_path, "01-magic-drc", "Magic.DRC", {}, {"magic__drc_error__count": 0}),
        # KLayout.DRC skipped its runset (it only warns upstream): no metric.
        _folder(tmp_path, "02-klayout-drc", "KLayout.DRC", {"magic__drc_error__count": 0},
                {"magic__drc_error__count": 0}),
    ]
    record = pv.judge_pv(folders, ("Magic.DRC", "KLayout.DRC"), tmp_path / "r.json")
    assert record["verdict"] == "NOT_MEASURED"
    row = record["metrics"]["klayout__drc_error__count"]
    assert row["status"] == "NOT_MEASURED"
    assert "did not write" in row["reason"]


def test_a_carried_metric_is_not_this_steps_measurement(tmp_path):
    # KLayout.DRC's key already sat in the state it received (an earlier
    # chain's number): the step wrote nothing new, so it measured nothing.
    carried = {"klayout__drc_error__count": 0}
    folders = [_folder(tmp_path, "01-klayout-drc", "KLayout.DRC", carried, dict(carried))]
    record = pv.judge_pv(folders, ("KLayout.DRC",), tmp_path / "r.json")
    assert record["verdict"] == "NOT_MEASURED"
    assert "carried in" in record["metrics"]["klayout__drc_error__count"]["reason"]


def test_a_step_that_did_not_run_is_not_measured(tmp_path):
    record = pv.judge_pv([], ("Netgen.LVS",), tmp_path / "r.json")
    assert record["verdict"] == "NOT_MEASURED"
    assert all(row["status"] == "NOT_MEASURED" for row in record["metrics"].values())
    assert set(record["metrics"]) == set(pv.PRODUCED["Netgen.LVS"])


def test_a_folder_without_state_in_credits_nothing(tmp_path):
    folder = _folder(tmp_path, "01-magic-drc", "Magic.DRC", {}, {"magic__drc_error__count": 0})
    (folder / "state_in.json").unlink()
    record = pv.judge_pv([folder], ("Magic.DRC",), tmp_path / "r.json")
    assert record["verdict"] == "NOT_MEASURED"
    assert "state_in.json" in record["metrics"]["magic__drc_error__count"]["reason"]


@pytest.mark.parametrize("value,status", [(3, "FAIL"), (True, "INVALID"), (-1, "INVALID"),
                                          ("0", "INVALID")])
def test_a_nonzero_or_non_count_fails(tmp_path, value, status):
    folders = [_folder(tmp_path, "01-magic-drc", "Magic.DRC", {},
                       {"magic__drc_error__count": value})]
    record = pv.judge_pv(folders, ("Magic.DRC",), tmp_path / "r.json")
    assert record["verdict"] == "FAIL"
    assert record["metrics"]["magic__drc_error__count"]["status"] == status


def test_fail_outranks_not_measured(tmp_path):
    folders = [_folder(tmp_path, "01-magic-drc", "Magic.DRC", {},
                       {"magic__drc_error__count": 2})]
    record = pv.judge_pv(folders, ("Magic.DRC", "KLayout.DRC"), tmp_path / "r.json")
    assert record["verdict"] == "FAIL"


def test_one_clean_engine_beside_a_dirty_one_is_a_named_disagreement(tmp_path):
    folders = [
        _folder(tmp_path, "01-magic-drc", "Magic.DRC", {}, {"magic__drc_error__count": 15}),
        _folder(tmp_path, "02-klayout-drc", "KLayout.DRC", {"magic__drc_error__count": 15},
                {"magic__drc_error__count": 15, "klayout__drc_error__count": 0}),
    ]
    record = pv.judge_pv(folders, ("Magic.DRC", "KLayout.DRC"), tmp_path / "r.json")
    assert record["verdict"] == "FAIL"
    assert any(r.startswith("LL_DRC_ENGINES_DISAGREE") for r in record["reasons"])


# ── run_half through the real contract, the tool faked at docker run ─────────
def _fake_tool(monkeypatch, writes):
    """Fake ``docker run``: resolution, bridge conversion and step runs."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "--id" in cmd:
            folder = Path(cmd[cmd.index("-o") + 1])
            step = cmd[cmd.index("--id") + 1]
            incoming = json.loads(Path(cmd[cmd.index("-i") + 1]).read_text())
            _put(folder / "state_in.json", incoming)
            out = dict(incoming, metrics=dict(incoming.get("metrics") or {}))
            out["metrics"].update(writes.get(step, {}))
            if step == "Magic.SpiceExtraction":  # the view the step writes
                (folder / "chip.spice").write_text("* extracted")
                out["spice"] = str(folder / "chip.spice")
            _put(folder / "state_out.json", out)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "openroad" in cmd:
            tcl = Path(cmd[-1]).read_text()
            for line in tcl.splitlines():
                for verb in ("write_db", "write_verilog -include_pwr_gnd"):
                    if line.startswith(verb + " "):
                        Path(line[len(verb) + 1:].strip("{}")).write_text("derived")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(contract, "image_capability", lambda *a, **k: {})
    monkeypatch.setattr(contract, "openroad_home", lambda *a, **k: None)
    monkeypatch.setattr(contract.subprocess, "run", run)
    return calls


def _half_project(tmp_path, chain):
    project = tmp_path / "project"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    for name in ("chip.gds", "chip.def", "chip_pnr.v", "constraint.sdc"):
        (pnr / name).write_text(name)
    (pnr / "chip.def").write_text("DESIGN chip ;\nCOMPONENTS 0 ;\n")
    cfg = project / "cfg"
    views = {"Magic.DRC": (["gds"], []), "KLayout.DRC": (["gds"], []),
             "KLayout.Density": (["gds"], []),
             "Magic.SpiceExtraction": (["gds", "def"], ["spice"]),
             "Netgen.LVS": (["spice", "pnl"], [])}
    configs = {}
    for step in chain:
        configs[step] = _put(cfg / f"{step}.json", {"meta": {"step": step}, "DESIGN_NAME": "chip",
                                                    "TECH_LEFS": {"nom_*": "/pdk/t.lef"}})
        _put(contract.views_path(configs[step]), {"step": step, "inputs": views[step][0],
                                                   "outputs": views[step][1]})
    pdk_root = tmp_path / "pdk"
    (pdk_root / "procA").mkdir(parents=True)
    return project, pnr, configs, pdk_root


def test_drc_half_runs_the_tool_chain_on_the_shipped_gds(tmp_path, monkeypatch):
    project, pnr, configs, pdk_root = _half_project(tmp_path, pv.DRC_CHAIN)
    monkeypatch.setattr(pv, "resolve_step_configs", lambda *a, **k: configs)
    calls = _fake_tool(monkeypatch, {"Magic.DRC": {"magic__drc_error__count": 0},
                                     "KLayout.DRC": {"klayout__drc_error__count": 0},
                                     "KLayout.Density": {"klayout__density_error__count": 0}})
    record = pv.run_half(project, "img", pdk_root, "procA", "drc", gds=pnr / "chip.gds",
                         routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                         sdc=pnr / "constraint.sdc")
    assert record["verdict"] == "PASS"
    ran = [c[c.index("--id") + 1] for c in calls if "--id" in c]
    assert ran == list(pv.DRC_CHAIN)
    assert record["scope"]["gds_sha256"] == contract.digest(pnr / "chip.gds")
    assert (project / "phase3/librelane/31-drc/01-magic-drc/state_out.json").is_file()
    state = json.loads((project / "phase3/librelane/31-drc-config/bridge/state_in.json").read_text())
    assert state["gds"] == str((pnr / "chip.gds").resolve())


def test_lvs_half_bridges_a_powered_netlist_from_the_routed_database(tmp_path, monkeypatch):
    project, pnr, configs, pdk_root = _half_project(tmp_path, pv.LVS_CHAIN)
    monkeypatch.setattr(pv, "resolve_step_configs", lambda *a, **k: configs)
    _fake_tool(monkeypatch, {"Magic.SpiceExtraction": {"magic__illegal_overlap__count": 0},
                             "Netgen.LVS": {k: 0 for k in pv.PRODUCED["Netgen.LVS"]}})
    record = pv.run_half(project, "img", pdk_root, "procA", "lvs", gds=pnr / "chip.gds",
                         routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                         sdc=pnr / "constraint.sdc")
    assert record["verdict"] == "PASS"
    bridge = project / "phase3/librelane/31-lvs-config/bridge"
    state = json.loads((bridge / "state_in.json").read_text())
    assert state["pnl"] == str((bridge / "bridge.pnl.v").resolve())
    receipt = json.loads((bridge / "bridge_receipt.json").read_text())
    # the netlist is written from the database the routed DEF was read into
    assert receipt["derived"]["pnl"]["from"] == state["odb"]
    assert receipt["derived"]["odb"]["from"] == str((pnr / "chip.def").resolve())
    assert "write_verilog -include_pwr_gnd" in (bridge / "to_pnl.tcl").read_text()


def test_a_bridge_that_writes_no_powered_netlist_refuses(tmp_path, monkeypatch):
    project, pnr, configs, pdk_root = _half_project(tmp_path, pv.LVS_CHAIN)
    monkeypatch.setattr(pv, "resolve_step_configs", lambda *a, **k: configs)
    _fake_tool(monkeypatch, {})
    monkeypatch.setattr(contract.subprocess, "run",
                        lambda cmd, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(contract.Refusal, match="LL_BRIDGE_CONVERSION_FAILED"):
        pv.run_half(project, "img", pdk_root, "procA", "lvs", gds=pnr / "chip.gds",
                    routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                    sdc=pnr / "constraint.sdc")


def test_a_missing_shipped_view_refuses_by_name(tmp_path, monkeypatch):
    project, pnr, configs, pdk_root = _half_project(tmp_path, pv.DRC_CHAIN)
    monkeypatch.setattr(pv, "resolve_step_configs", lambda *a, **k: configs)
    (pnr / "chip.gds").unlink()
    with pytest.raises(contract.Refusal, match="LL_PV_VIEW_MISSING: gds"):
        pv.run_half(project, "img", pdk_root, "procA", "drc", gds=pnr / "chip.gds",
                    routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                    sdc=pnr / "constraint.sdc")


# ── 37.4: rows from judged State, bound by sha ──────────────────────────────
def _judged(tmp_path, project, half="drc", value=0):
    folder = _folder(project / "phase3/librelane/31-drc", "02-klayout-drc", "KLayout.DRC",
                     {}, {"klayout__drc_error__count": value})
    return folder, pv.judge_pv([folder], ("KLayout.DRC",),
                               project / pv.RECORD_REL.format(half=half))


def test_state_metrics_bind_each_value_to_the_state_that_measured_it(tmp_path):
    project = tmp_path / "p"
    folder, _ = _judged(tmp_path, project, value=5)
    rows = pv.state_metrics(project, [project / pv.RECORD_REL.format(half="drc")])
    row = rows["klayout__drc_error__count"]
    assert row["value"] == 5
    assert row["source"] == "phase3/librelane/31-drc/02-klayout-drc/state_out.json"
    assert row["sha256"] == contract.digest(folder / "state_out.json")


def test_a_state_changed_after_judgement_is_not_measured(tmp_path):
    project = tmp_path / "p"
    folder, _ = _judged(tmp_path, project, value=0)
    _put(folder / "state_out.json", {"metrics": {"klayout__drc_error__count": 0, "x": 1}})
    row = pv.state_metrics(project, [project / pv.RECORD_REL.format(half="drc")])[
        "klayout__drc_error__count"]
    assert row["value"] == "NOT_MEASURED"
    assert row["reason"].startswith("LL_STATE_CHANGED")


def _switch(project, mode):
    _put(project / "phase3/librelane_switch.json", {"steps": {"31": mode}})


def test_aggregator_reads_the_tool_state_only_when_step_31_is_on_librelane(tmp_path):
    agg = importlib.import_module("signoff_metrics_aggregate")
    project = tmp_path / "p"
    _judged(tmp_path, project, value=7)
    # direct (no switch): the direct rule answers, and this tree has no
    # direct DRC report, so the key is NOT_MEASURED by the direct rule.
    metrics, _ = agg.aggregate(project)
    assert metrics["klayout__drc_error__count"] == "NOT_MEASURED"
    assert "31-drc" not in json.dumps(metrics["__provenance__"]["klayout__drc_error__count"])
    _switch(project, "librelane")
    metrics, _ = agg.aggregate(project)
    assert metrics["klayout__drc_error__count"] == 7
    prov = metrics["__provenance__"]["klayout__drc_error__count"]
    assert prov["source"] == "phase3/librelane/31-drc/02-klayout-drc/state_out.json"
    # a key of the same chain the tool never wrote stays NOT_MEASURED
    assert metrics["magic__drc_error__count"] == "NOT_MEASURED"
    _switch(project, "dual")
    metrics, _ = agg.aggregate(project)
    assert metrics["klayout__drc_error__count"] == "NOT_MEASURED"


def test_aggregator_check_refuses_a_record_the_state_no_longer_states(tmp_path):
    agg = importlib.import_module("signoff_metrics_aggregate")
    project = tmp_path / "p"
    folder, _ = _judged(tmp_path, project, value=0)
    _switch(project, "librelane")
    assert agg.main([str(project)]) == 0
    assert agg.main([str(project), "--check"]) == 0
    _put(folder / "state_out.json", {"metrics": {"klayout__drc_error__count": 0, "later": 1}})
    assert agg.main([str(project), "--check"]) == 1


# ── the runner's step-31 switch ─────────────────────────────────────────────
def _row(name, status, detail=""):
    runner = importlib.import_module("phase3_one_shot_runner")
    kwargs = {"reason_class": "not_executed"} if status == "NOT_MEASURED" else {}
    return runner.StepResult(name, status, 0.0, detail, **kwargs)


@pytest.fixture
def runner(monkeypatch):
    module = importlib.import_module("phase3_one_shot_runner")
    monkeypatch.setattr(module, "_vacuous_on_unrouted", lambda *a, **k: None)
    return module


def test_direct_mode_never_touches_the_tool(tmp_path, runner, monkeypatch):
    seen = []
    monkeypatch.setattr(runner, "_step31_librelane",
                        lambda *a, **k: seen.append(a) or _row("drc", "PASS"))
    row = runner._step31_dispatch(tmp_path, "chip", None, "drc", lambda: _row("drc", "FAIL", "d"))
    assert (row.status, row.detail, seen) == ("FAIL", "d", [])


def test_librelane_mode_is_the_tools_verdict(tmp_path, runner, monkeypatch):
    _switch(tmp_path, "librelane")
    monkeypatch.setattr(runner, "_step31_librelane",
                        lambda p, t, k, half, publish: _row(half, "FAIL", f"tool publish={publish}"))
    row = runner._step31_dispatch(tmp_path, "chip", None, "lvs",
                                  lambda: pytest.fail("direct half ran in librelane mode"))
    assert (row.status, row.detail) == ("FAIL", "tool publish=True")


@pytest.mark.parametrize("direct,tool,expected,disagree", [
    ("PASS", "PASS", "PASS", False),
    ("PASS", "FAIL", "FAIL", True),
    ("FAIL", "PASS", "FAIL", True),
    ("PASS", "NOT_MEASURED", "NOT_MEASURED", False),
])
def test_dual_mode_needs_both_halves_clean(tmp_path, runner, monkeypatch,
                                           direct, tool, expected, disagree):
    _switch(tmp_path, "dual")
    published = []
    monkeypatch.setattr(runner, "_step31_librelane",
                        lambda p, t, k, half, publish: published.append(publish)
                        or _row(half, tool, "tool"))
    row = runner._step31_dispatch(tmp_path, "chip", None, "drc", lambda: _row("drc", direct))
    assert row.status == expected
    assert ("LL_PV_ARMS_DISAGREE" in row.detail) is disagree
    assert published == [False]
    assert (row.extras["dual_direct"], row.extras["dual_librelane"]) == (direct, tool)


def _published_half(tmp_path, runner, monkeypatch, half, record, files):
    project = tmp_path / "p"
    for path, text in files.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    monkeypatch.setattr(contract, "resolve_image", lambda p: "img")
    monkeypatch.setattr(contract, "resolve_pdk_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(pv, "run_half", lambda *a, **k: _put(
        project / pv.RECORD_REL.format(half=half), record) and record)
    pdk = SimpleNamespace(name="procA")
    return project, runner._step31_librelane(project, "chip", pdk, half, publish=True)


_DECK_LYRDB = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<report-database>\n"
               " <generator>drc: script='/pdk/procA/libs.tech/klayout/tech/drc/procA.drc'"
               "</generator>\n <top-cell>chip</top-cell>\n <categories>\n </categories>\n"
               " <cells>\n  <cell>\n   <name>chip</name>\n  </cell>\n </cells>\n"
               " <items>\n </items>\n</report-database>\n")


def test_librelane_drc_publishes_the_deck_report_where_the_gates_read(tmp_path, runner,
                                                                    monkeypatch):
    folder = "phase3/librelane/31-drc/02-klayout-drc"
    record = {"verdict": "PASS", "reasons": [], "metrics": {
        "klayout__drc_error__count": {"step": "KLayout.DRC", "status": "MEASURED", "value": 0,
                                      "folder": str(tmp_path / "p" / folder)}}}
    project, row = _published_half(tmp_path, runner, monkeypatch, "drc", record,
                                   {f"{folder}/reports/drc.klayout.lyrdb": _DECK_LYRDB})
    assert row.status == "PASS"
    assert (project / "reports/phase3/drc_signoff.rpt").read_text() == _DECK_LYRDB
    assert (project / "phase3/reports/drc.rpt").read_text() == _DECK_LYRDB


def test_a_report_that_is_not_a_deck_report_is_not_published(tmp_path, runner, monkeypatch):
    folder = "phase3/librelane/31-drc/02-klayout-drc"
    record = {"verdict": "PASS", "reasons": [], "metrics": {
        "klayout__drc_error__count": {"step": "KLayout.DRC", "status": "MEASURED", "value": 0,
                                      "folder": str(tmp_path / "p" / folder)}}}
    project, row = _published_half(tmp_path, runner, monkeypatch, "drc", record,
                                   {f"{folder}/reports/drc.klayout.lyrdb": "not a report"})
    assert not (project / "reports/phase3/drc_signoff.rpt").exists()
    assert "published" not in row.extras


def test_librelane_lvs_publishes_netgens_report_and_a_verdict_the_aggregator_reads(
        tmp_path, runner, monkeypatch):
    agg = importlib.import_module("signoff_metrics_aggregate")
    folder = "phase3/librelane/31-lvs/02-netgen-lvs"
    record = {"verdict": "PASS", "reasons": [], "metrics": {
        key: {"step": "Netgen.LVS", "status": "MEASURED", "value": 0,
              "folder": str(tmp_path / "p" / folder)} for key in pv.PRODUCED["Netgen.LVS"]}}
    project, row = _published_half(
        tmp_path, runner, monkeypatch, "lvs", record,
        {f"{folder}/reports/lvs.netgen.rpt": "Final result: Circuits match uniquely.\n"})
    assert row.status == "PASS"
    assert "match uniquely" in (project / "reports/phase3/lvs.rpt").read_text()
    verdict = json.loads((project / "reports/phase3/lvs_verdict.json").read_text())
    assert verdict["finding"].startswith("LVS_MATCH")
    assert verdict["compare_performed"] is True
    cell = agg._lvs_from_runner_verdict(project, "design__lvs_error__count", "x")
    assert cell.value == 0


def test_a_librelane_lvs_mismatch_is_not_published_as_a_match(tmp_path, runner, monkeypatch):
    folder = "phase3/librelane/31-lvs/02-netgen-lvs"
    record = {"verdict": "FAIL", "reasons": ["design__lvs_error__count=176 (Netgen.LVS)"],
              "metrics": {"design__lvs_error__count": {
                  "step": "Netgen.LVS", "status": "FAIL", "value": 176,
                  "folder": str(tmp_path / "p" / folder)}}}
    project, row = _published_half(
        tmp_path, runner, monkeypatch, "lvs", record,
        {f"{folder}/reports/lvs.netgen.rpt": "Netlists do not match.\n"})
    assert row.status == "FAIL"
    verdict = json.loads((project / "reports/phase3/lvs_verdict.json").read_text())
    assert (verdict["status"], verdict["finding"]) == ("FAIL", "LVS_LIBRELANE_FAIL")


def test_step_drc_and_step_lvs_route_through_the_switch(tmp_path, runner, monkeypatch):
    _switch(tmp_path, "librelane")
    monkeypatch.setattr(runner, "_step31_librelane",
                        lambda p, t, k, half, publish: _row(half, "PASS", "tool"))
    monkeypatch.setattr(runner, "_step_drc_direct", lambda *a, **k: pytest.fail("direct"))
    monkeypatch.setattr(runner, "_step_lvs_direct", lambda *a, **k: pytest.fail("direct"))
    assert runner.step_drc(tmp_path, "chip", None, "c").detail == "tool"
    assert runner.step_lvs(tmp_path, "chip", None, "c").detail == "tool"


def test_an_incomplete_pnr_skips_lvs_on_every_mode(tmp_path, runner, monkeypatch):
    _switch(tmp_path, "librelane")
    monkeypatch.setattr(runner, "_step31_librelane",
                        lambda *a, **k: pytest.fail("the tool ran after a dead pnr"))
    monkeypatch.setattr(runner, "_step_lvs_direct",
                        lambda *a, **k: _row("lvs", "NOT_MEASURED", "skipped: upstream"))
    row = runner.step_lvs(tmp_path, "chip", None, "c", upstream_pnr=_row("pnr", "FAIL"))
    assert row.detail == "skipped: upstream"


# ── the plugin steps' pure parts ────────────────────────────────────────────
def _load_plugin_module(name):
    spec = importlib.util.spec_from_file_location(f"_mig105_{name}", PLUGIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real8(value):
    """Encode a GDSII 8-byte real (excess-64, base 16)."""
    if value == 0:
        return b"\0" * 8
    exponent = 64
    while value >= 1:
        value /= 16.0
        exponent += 1
    while value < 1 / 16.0:
        value *= 16.0
        exponent -= 1
    return bytes([exponent]) + int(value * (1 << 56)).to_bytes(7, "big")


def _gds(path, user, meters, units_first=True):
    def rec(rtype, dtype, payload=b""):
        return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload
    units = rec(0x03, 0x05, _real8(user) + _real8(meters))
    body = [rec(0x00, 0x02, b"\x02\x58"), rec(0x01, 0x02, b"\0" * 24),
            rec(0x02, 0x06, b"LIB\0")]
    body += [units, rec(0x05, 0x02, b"\0" * 24)] if units_first else [rec(0x05, 0x02, b"\0" * 24), units]
    path.write_bytes(b"".join(body))
    return path


@pytest.mark.parametrize("meters", [1e-9, 5e-10, 2.5e-10])
def test_database_unit_reader_agrees_with_the_host_gds_reader(tmp_path, meters):
    units = _load_plugin_module("gds_units")
    geometry = importlib.import_module("_gds_geometry")
    path = _gds(tmp_path / "a.gds", 1e-3, meters)
    measured = units.gds_database_unit_um(str(path))
    assert measured == pytest.approx(meters * 1e6, rel=1e-12)
    assert measured == pytest.approx(geometry.read_layout(path).dbu_um, rel=1e-12)


def test_a_units_record_after_the_first_structure_is_not_read(tmp_path):
    units = _load_plugin_module("gds_units")
    assert units.gds_database_unit_um(str(_gds(tmp_path / "b.gds", 1e-3, 1e-9,
                                                units_first=False))) is None


def test_every_finishing_key_the_judge_requires_is_one_the_script_prints():
    script = (PLUGIN / "finishing_xor.drc").read_text()
    for key in pv.PRODUCED["Vibeic.FinishingXOR"]:
        stem = key[len("vibeic__finishing_xor__"):-len("__count")]
        assert (f'"{stem}"' in script or f"__{stem}__count" in script), key
    assert "%OL_METRIC_I vibeic__finishing_xor__defect__count" in script


def test_the_plugin_registers_both_steps_and_fingerprints_the_drc_script():
    init = (PLUGIN / "__init__.py").read_text()
    assert "from .signoff import DatabaseUnit, FinishingXOR" in init
    digests = contract._plugin_digests("Vibeic.FinishingXOR")
    assert "librelane_plugins/librelane_plugin_vibeic/finishing_xor.drc" in digests
    assert contract._plugin_digests("KLayout.DRC") == {}
