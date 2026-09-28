"""DRV standard: a measured bad value must fail, and a clean value must pass."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drv_signoff_judge as drv  # noqa: E402


def _file(root: Path, name: str, body: str) -> dict:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return {"path": str(path), "sha256": hashlib.sha256(body.encode()).hexdigest()}


def _report(*, fanout=1, fanout_limit=4, cap=.1, cap_limit=.2,
            slew=.1, slew_limit=3, violators=False) -> str:
    parts = []
    for title, limit_label, measured_label, value, limit, direction in (
        ("max slew", "max slew", "slew", slew, slew_limit, "^"),
        ("max fanout", "max fanout", "fanout", fanout, fanout_limit, ""),
        ("max capacitance", "max capacitance", "capacitance", cap, cap_limit, "^"),
    ):
        parts.append(title + "\n")
        if not violators or value > limit:
            slack = limit - value
            parts.extend((f"Pin u/Y {direction}\n", f"{limit_label} {limit:.6f}\n",
                          f"{measured_label} {value:.6f}\n",
                          f"Slack {slack:.6f} " +
                          ("(VIOLATED)" if slack < 0 else "(MET)") + "\n"))
        parts.append("\n")
    return "".join(parts)


def _bundle(tmp_path: Path) -> dict:
    sources = {n: _file(tmp_path, n, n + "\n") for n in
               ("l7", "l9", "pdk_config", "signoff_sdc")}
    sources["l9"] = _file(tmp_path, "phase1/generated_docs/L9.md",
                           "| SYNTH_MAX_FANOUT | 4 |\n")
    sources["pdk_config"] = _file(
        tmp_path, "pdk_config",
        "set ::env(MAX_FANOUT_CONSTRAINT) 10\n"
        "set ::env(MAX_TRANSITION_CONSTRAINT) 3\n"
        "set ::env(MAX_CAPACITANCE_CONSTRAINT) 0.2\n")
    sources["signoff_sdc"] = _file(
        tmp_path, "signoff_sdc",
        "set_max_fanout 4 [current_design]\n"
        "set_max_transition 3 [current_design]\n"
        "set_max_capacitance 0.2 [current_design]\n")
    lib = _file(tmp_path, "library.lib", '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_transition : 20;
 default_max_capacitance : 20;
 cell (logic) { pin (Y) {
  max_transition : 20; max_capacitance : 20;
 } }
}''')
    values = {"fanout": 4, "slew_ns": 3, "cap_pf": .2,
              "period_ns": 24, "io_delay_ns": 4.8,
              "default_fanout_ceiling": 10}
    frozen = {"sources": {n: v["sha256"] for n, v in sources.items()},
              "liberties": {"std": lib["sha256"]}, "values": values,
              "scope": "whole final netlist", "scenes": ["typ_nom"],
              "scene_liberties": {"typ_nom": ["std"]},
              "rc_corners": ["nom"],
              "pvt": {"typ_nom": {"nom_process": 1,
                                   "nom_voltage": 5,
                                   "nom_temperature": 25}}}
    current = {"sources": sources, "liberties": [{"name": "std", **lib}],
               "values": copy.deepcopy(values), "scope": frozen["scope"],
               "scenes": frozen["scenes"],
               "scene_liberties": copy.deepcopy(frozen["scene_liberties"])}
    evidence = _file(tmp_path, "stage.rpt", drv._COMMAND + "\n" +
                     "max_slew violators=0\n"
                     "max_capacitance violators=0\n"
                     "max_fanout violators=0\n")
    stages = []
    for name in drv._REQUIRED_STAGES:
        stage = {"name": name, "ran": True, "behavior_report": evidence,
                 "applied": {"fanout": 4, "slew_ns": 3, "cap_pf": .2}}
        if name == "synth":
            stage.update(abc_script=_file(tmp_path, "abc.script", "buffer -N 4\n"),
                         synth_abc_buffering=True, ideal_clock_excluded=True)
        else:
            stage.update(sdc_snapshot=_file(tmp_path, name + ".sdc",
                         Path(sources["signoff_sdc"]["path"]).read_text()),
                         fanout_check_limit=4)
        if name == "cts":
            stage["cts_parameters"] = {"sink_clustering_size": 4}
            stage["clock_driver_fanout"] = []
        stages.append(stage)
    clean = _file(tmp_path, "violators.rpt", _report(violators=True))
    all_limits = _file(tmp_path, "all.rpt", _report())
    positive = _file(tmp_path, "control.rpt", _report(fanout=2, fanout_limit=1,
                       cap_limit=.01, slew_limit=.01, violators=True))
    layout = _file(tmp_path, "routed.def", "ROUTED DEF\n")
    netlist = _file(tmp_path, "netlist.v", "FINAL NETLIST\n")
    artifacts = {n: netlist for n in ("sta_netlist", "lvs_netlist", "gds_netlist")}
    artifacts.update(odb=_file(tmp_path, "routed.odb", "ODB\n"), def_=layout)
    artifacts["def"] = artifacts.pop("def_")
    scene = {"name": "typ_nom", "mode": "functional", "fresh_process": True,
             "postroute": True, "unannotated_nets": 0,
             "parasitic_annotation_report": _file(
                 tmp_path, "annotation.rpt", "Found 0 unannotated drivers.\n"),
             "propagated_clocks": True,
             "clock_properties": _file(tmp_path, "clocks.rpt", "propagated\n"),
             "excluded_pins_recorded": True, "excluded_pins": [],
             "spef": _file(tmp_path, "x.spef", "SPEF\n"),
             "rc_corner": "nom", "spef_layout_sha256": layout["sha256"],
             "liberty": "std", "liberty_header_match": True,
             "linked_liberties": [{"name": "std", **lib}],
             "command": drv._COMMAND, "report": clean,
             "counters": {k: 0 for k in drv.KINDS},
             "counter_report": _file(tmp_path, "counter.log", "".join(
                 f"DRV_COUNTER {k} 0\n" for k in drv.KINDS)),
             "all_limits_report": all_limits,
             "all_limits_max_count": 1,
             "population": {k: 1 for k in drv.KINDS},
             "positive_control_report": positive,
             "positive_control_expected": {k: 1 for k in drv.KINDS},
             "positive_control_counters": {k: 1 for k in drv.KINDS},
             "positive_control_limits": {"max_fanout": 1,
                                         "max_slew": .01,
                                         "max_capacitance": .01},
             "positive_control_fresh_process": True}
    scene["spef_extraction_receipt"] = _file(
        tmp_path, "spef_extraction.json", json.dumps({
            "routed_def_sha256": layout["sha256"],
            "spef_sha256": scene["spef"]["sha256"]}))
    digest = netlist["sha256"]
    return {"identity": {"run_id": "run-1", "tree_sha": "tree-a",
             "project": str(tmp_path), "pdk": "synthetic", "library": "logic",
             "spec_version": "contract-a", "sta_netlist": digest,
             "lvs_netlist": digest, "gds_netlist": digest,
             "artifacts": artifacts,
             "openroad_commit": "tool-a", "opensta_commit": "tool-b",
             "pdk_commit": "pdk-a"},
            "frozen": frozen, "current": current, "stages": stages,
            "scenes": [scene], "pins": {"u/Y": {
                "net_class": "data", "cell_class": "std", "cell": "logic",
                "cell_pin": "Y", "liberty": "std", "driver_pin": "u/Y",
                "driver_cell": "logic", "loads": {"logical": 1,
                                                 "antenna_diode": 0, "cts_buffer": 0}}},
            "waiver_ledger": []}


def _violate(bundle: dict, root: Path, kind: str, *, value: float,
             limit: float, net_class="data", cell_class="std") -> None:
    scene = bundle["scenes"][0]
    numbers = {"fanout": 1, "fanout_limit": 4, "cap": .1,
               "cap_limit": .2, "slew": .1, "slew_limit": 3}
    numbers[{"max_slew": "slew", "max_capacitance": "cap",
             "max_fanout": "fanout"}[kind]] = value
    numbers[{"max_slew": "slew_limit", "max_capacitance": "cap_limit",
             "max_fanout": "fanout_limit"}[kind]] = limit
    scene["report"] = _file(root, "violators.rpt", _report(**numbers, violators=True))
    scene["all_limits_report"] = _file(root, "all.rpt", _report(**numbers))
    scene["counters"][kind] = 1
    scene["counter_report"] = _file(root, "counter.log", "".join(
        f"DRV_COUNTER {k} {scene['counters'][k]}\n" for k in drv.KINDS))
    bundle["pins"]["u/Y"].update(net_class=net_class, cell_class=cell_class)


def _owner_waiver(bundle: dict, root: Path, row: dict) -> dict:
    netlist = bundle["identity"]["sta_netlist"]
    sdc = bundle["current"]["sources"]["signoff_sdc"]["sha256"]
    return {"netlist_sha256": netlist, "tree_sha": "tree-a", "run_id": "run-1",
            "driver_pin": "u/Y", "driver_cell": "logic",
            "measured": row["measured"], "limit": row["effective_limit"],
            "scene_or_mode": (row["mode"] if row["kind"] == "max_fanout"
                              else row["scene"]), "sdc_sha256": sdc,
            "issuer": "reyerchu", "origin": "owner",
            "owner_quote": "Approve this one measured net",
            "owner_timestamp": "2026-09-28T10:00:00+08:00", "approved_value": row["measured"],
            "timing": [{"scene": "typ_nom", "netlist_sha256": netlist,
                        "sdc_sha256": sdc, "period_ns": 24,
                        "io_delay_ns": 4.8, "setup_slack_ns": 1,
                        "hold_slack_ns": .1,
                        "report": _file(root, "timing.rpt", "setup 1 hold .1\n")}],
            "signal_em": {"scene": "typ_nom", "worst_scene": True,
                          "netlist_sha256": netlist,
                          "report": _file(root, "em.rpt", "EM screened\n"),
                          "drm_source": _file(root, "drm.txt", "current limits\n"),
                          "currents": {"average": .1, "rms": .2, "peak": .3},
                          "limits": {"average": 1, "rms": 1, "peak": 1}}}


def test_complete_measured_bundle_passes(tmp_path):
    assert drv.judge(_bundle(tmp_path))["verdict"] == "PASS"


def test_broader_implementation_stage_is_disclosed_when_final_route_clean(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["stages"][1]["applied"]["fanout"] = 5
    bundle["stages"][1]["fanout_check_limit"] = 5
    bundle["stages"][1]["sdc_snapshot"] = _file(tmp_path, "placement_repair.sdc",
        "set_max_fanout 5 [current_design]\n"
        "set_max_transition 3 [current_design]\n"
        "set_max_capacitance 0.2 [current_design]\n")
    result = drv.judge(bundle)
    assert result["verdict"] == "PASS"
    assert result["flow_defects"]


@pytest.mark.parametrize("mutation,needle", [
    (lambda b: b["current"]["values"].update(fanout=10), "門檻來源已變更"),
    (lambda b: b["stages"][0].update(synth_abc_buffering=False), "synth"),
    (lambda b: b["stages"][-1]["applied"].update(fanout=10), "sign-off SDC"),
    (lambda b: b["identity"].update(gds_netlist="b" * 64), "identity mismatch"),
])
def test_nonnegotiable_preconditions_fail(tmp_path, mutation, needle):
    bundle = _bundle(tmp_path)
    mutation(bundle)
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert needle in " ".join(result["failures"])


def test_stage_snapshot_cannot_claim_a_tighter_applied_limit(tmp_path):
    bundle = _bundle(tmp_path)
    stage = bundle["stages"][1]
    stage["sdc_snapshot"] = _file(tmp_path, "placement_repair.sdc",
        "set_max_fanout 10 [current_design]\n"
        "set_max_transition 3 [current_design]\n"
        "set_max_capacitance 0.2 [current_design]\n")
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert any("snapshot != applied" in f for f in result["failures"])


def test_gf180_cannot_shrink_the_frozen_scene_set_to_one(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["identity"]["pdk"] = "gf180mcuD"
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("3 PVT x 3 RC" in n for n in result["not_measured"])


def test_extraction_receipt_must_bind_same_def_and_spef(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["scenes"][0]["spef_extraction_receipt"] = _file(
        tmp_path, "spef_extraction.json", json.dumps({
            "routed_def_sha256": "a" * 64,
            "spef_sha256": bundle["scenes"][0]["spef"]["sha256"]}))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("extraction input/output" in n for n in result["not_measured"])


def test_all_limits_report_cannot_hide_a_named_violator(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["scenes"][0]["report"] = _file(
        tmp_path, "violators.rpt", _report(violators=True))
    bundle["scenes"][0]["counters"]["max_fanout"] = 0
    bundle["scenes"][0]["counter_report"] = _file(
        tmp_path, "counter.log", "".join(
            f"DRV_COUNTER {k} 0\n" for k in drv.KINDS))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("all-limits vs violator names" in n
               for n in result["not_measured"])


def test_signoff_sdc_ten_against_declared_four_fails_even_if_report_clean(tmp_path):
    bundle = _bundle(tmp_path)
    source = _file(tmp_path, "signoff_sdc",
                   "set_max_fanout 10 [current_design]\n"
                   "set_max_transition 3 [current_design]\n"
                   "set_max_capacitance 0.2 [current_design]\n")
    bundle["current"]["sources"]["signoff_sdc"] = source
    bundle["frozen"]["sources"]["signoff_sdc"] = source["sha256"]
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert any("set_max_fanout != declared" in f for f in result["failures"])


@pytest.mark.parametrize("mutation,needle", [
    (lambda b: b["scenes"][0]["counters"].update(max_fanout=1), "names != counter"),
    (lambda b: b["scenes"][0].update(unannotated_nets=1), "annotation"),
    (lambda b: b["scenes"][0].update(propagated_clocks=False), "propagated"),
    (lambda b: b["scenes"][0]["positive_control_expected"].update(max_slew=2), "planted"),
])
def test_missing_instrument_evidence_is_not_measured(tmp_path, mutation, needle):
    bundle = _bundle(tmp_path)
    mutation(bundle)
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert needle in " ".join(result["not_measured"])


def test_data_fanout_residue_is_unwaivable(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["failed_tier"] == "DATA_NET_RESIDUE"


def test_clock_slew_is_hard_fail(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_slew", value=4, limit=3,
             net_class="clock")
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["failed_tier"] == "T2_CLOCK_SLEW"


@pytest.mark.parametrize("kind,value,limit", [
    ("max_slew", 4, 3), ("max_capacitance", .3, .2)])
def test_data_margin_can_only_be_waived_by_exact_owner_record(
        tmp_path, kind, value, limit):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, kind, value=value, limit=limit)
    first = drv.judge(bundle)
    assert first["verdict"] == "FAIL"
    assert first["findings"][0]["failed_tier"] == "T3_MARGIN"
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path,
                                               first["findings"][0])]
    assert drv.judge(bundle)["verdict"] == "WAIVED"
    bundle["waiver_ledger"][0]["sdc_sha256"] = "0" * 64
    assert drv.judge(bundle)["verdict"] == "FAIL"


def test_io_default_fanout_one_catches_three_below_design_four(tmp_path):
    bundle = _bundle(tmp_path)
    io = _file(tmp_path, "io.lib", '''library (io) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_fanout : 1;
 default_max_capacitance : 999;
 cell (pad) { pin (Y) { direction : output; } }
}''')
    bundle["current"]["liberties"].append({"name": "io", **io})
    bundle["frozen"]["liberties"]["io"] = io["sha256"]
    bundle["frozen"]["scene_liberties"]["typ_nom"].append("io")
    bundle["current"]["scene_liberties"]["typ_nom"].append("io")
    bundle["scenes"][0]["linked_liberties"].append({"name": "io", **io})
    bundle["scenes"][0]["liberty"] = "io"
    bundle["pins"]["u/Y"].update(liberty="io", cell="pad",
                                    driver_cell="pad", cell_class="IO",
                                    net_class="IO")
    _violate(bundle, tmp_path, "max_fanout", value=3, limit=1,
             net_class="IO", cell_class="IO")
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["failed_tier"] == "T1_LIBERTY_FANOUT"


def test_std_cell_cap_margin_on_io_is_disclosed_separately(tmp_path):
    bundle = _bundle(tmp_path)
    io = _file(tmp_path, "io.lib", '''library (io) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_capacitance : 999;
 cell (pad) { pin (Y) { direction : output; } }
}''')
    bundle["current"]["liberties"].append({"name": "io", **io})
    bundle["frozen"]["liberties"]["io"] = io["sha256"]
    bundle["frozen"]["scene_liberties"]["typ_nom"].append("io")
    bundle["current"]["scene_liberties"]["typ_nom"].append("io")
    bundle["scenes"][0]["linked_liberties"].append({"name": "io", **io})
    bundle["scenes"][0]["liberty"] = "io"
    bundle["pins"]["u/Y"].update(liberty="io", cell="pad",
                                    driver_cell="pad", cell_class="IO",
                                    net_class="IO")
    _violate(bundle, tmp_path, "max_capacitance", value=.3, limit=.2,
             net_class="IO", cell_class="IO")
    result = drv.judge(bundle)
    assert result["verdict"] == "PASS"
    assert result["io_margin_disclosures"][0]["failed_tier"] == (
        "IO_STD_CELL_MARGIN_DISCLOSURE")


@pytest.mark.parametrize("kind,value,limit", [
    ("max_slew", 21, 3), ("max_capacitance", 21, .2)])
def test_liberty_slew_and_cap_are_hard_floors(tmp_path, kind, value, limit):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, kind, value=value, limit=limit)
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["failed_tier"] == "T1_LIBERTY"


def test_antenna_excess_needs_exact_owner_ledger(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4, "antenna_diode": 1,
                                            "cts_buffer": 0}
    first = drv.judge(bundle)
    assert first["verdict"] == "FAIL"
    row = first["findings"][0]
    bundle["waiver_ledger"].append(_owner_waiver(bundle, tmp_path, row))
    assert drv.judge(bundle)["verdict"] == "WAIVED"
    bundle["identity"]["run_id"] = "run-2"
    assert drv.judge(bundle)["verdict"] == "FAIL"
    bundle["identity"]["run_id"] = "run-1"
    bundle["waiver_ledger"][0]["origin"] = "flow"
    assert drv.judge(bundle)["verdict"] == "FAIL"


def test_real_pinned_opensta_verbose_calibration_samples():
    root = Path(__file__).resolve().parents[1] / "calibration"
    bad = drv.parse_check_types((root / "drv_signoff_0384_violators.rpt").read_text(),
                                scene="s", mode="m", violators_only=True)
    good = drv.parse_check_types((root / "drv_signoff_0384_clean.rpt").read_text(),
                                 scene="s", mode="m", violators_only=True)
    assert {kind: len(rows) for kind, rows in bad.items()} == {k: 2 for k in drv.KINDS}
    assert all(not rows for rows in good.values())


def test_linked_io_liberty_continued_cap_unit_and_default_fanout():
    limits = drv._liberty_limits('''library (io) {
 time_unit : "1ns";
 capacitive_load_unit(1.000000, \\
   "pf");
 default_max_capacitance : 999;
 default_max_fanout : 1;
 cell (pad) { pin (Y) { direction : output; } }
}''')
    assert limits["defaults"]["max_fanout"] == 1
    assert limits["defaults"]["max_capacitance"] == 999


def test_negated_table_title_cannot_be_a_measurement():
    body = "not max slew\nPin u/Y ^\nmax slew 3\nslew 4\nSlack -1 (VIOLATED)\n"
    parsed = drv.parse_check_types(body, scene="s", mode="m",
                                   violators_only=True)
    assert parsed["max_slew"] == []


def test_negated_counter_marker_is_not_a_measurement():
    import drv_signoff_capture as capture
    text = "NOT DRV_COUNTER max_fanout 0\nDRV_COUNTER max_slew 1\n"
    assert capture._COUNTER.findall(text) == [("max_slew", "1")]


def test_prestream_signoff_consumer_reads_judge_receipt_not_exit_code(tmp_path):
    import phase3_one_shot_runner as runner
    _file(tmp_path, "phase3/stage3/pnr/routed.def", "ROUTED DEF\n")
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(_bundle(tmp_path)))
    row = runner._run_declared_signoff_gate(
        tmp_path, "drv_signoff", "drv_signoff_judge.py",
        "reports/phase3/sta/drv_signoff.json")
    assert row.status == "PASS"
    assert any(s[0] == "drv_signoff" for s in runner._PRESTREAM_GATES)
    assert any(s[0] == "drv_signoff" for s in runner._DECLARED_SIGNOFF_GATES)


def test_consumer_preserves_waived_word_and_owner_row(tmp_path):
    import phase3_one_shot_runner as runner
    _file(tmp_path, "phase3/stage3/pnr/routed.def", "ROUTED DEF\n")
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4,
                                            "antenna_diode": 1,
                                            "cts_buffer": 0}
    row = drv.judge(bundle)["findings"][0]
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path, row)]
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(bundle))
    gate = runner._run_declared_signoff_gate(
        tmp_path, "drv_signoff", "drv_signoff_judge.py",
        "reports/phase3/sta/drv_signoff.json")
    assert gate.status == "WAIVED"
    assert gate.waiver_rows[0]["owner"] == "reyerchu"


def test_flow_step_keeps_owner_drv_waiver_out_of_pass(tmp_path):
    import flow_compliance_check as flow
    _file(tmp_path, "phase3/stage3/pnr/routed.def", "ROUTED DEF\n")
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4,
                                          "antenna_diode": 1,
                                          "cts_buffer": 0}
    row = drv.judge(bundle)["findings"][0]
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path, row)]
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(bundle))
    step = {"id": 23, "name": "post-route STA", "stage": "phase3/stage3/sta",
            "required_outputs": [], "gate": {"all_of": [
                {"program_exit_zero": "drv_signoff_judge . --json "
                 "reports/phase3/sta/drv_signoff.json"}]}}
    result = flow.check_step(tmp_path, step, {})
    assert result.status == "WAIVED", result.reasons
    bundle["waiver_ledger"][0]["origin"] = "flow"
    source.write_text(json.dumps(bundle))
    result = flow.check_step(tmp_path, step, {})
    assert result.status == "FAIL"


def test_step32_rechecks_final_state_identity_after_late_repair(tmp_path):
    import librelane_postroute_repair as repair
    bundle = _bundle(tmp_path)
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(bundle))
    state = tmp_path / "final_state.json"
    state.write_text(json.dumps({"def": bundle["identity"]["artifacts"]["def"]["path"]}))
    report = {"adopted_state": str(state)}
    repair._step32_drv_signoff(tmp_path, report)
    assert report["drv_signoff"]["verdict"] == "PASS"
    bundle["identity"]["artifacts"]["def"] = _file(
        tmp_path, "other.def", "OTHER ROUTE\n")
    source.write_text(json.dumps(bundle))
    repair._step32_drv_signoff(tmp_path, report)
    assert report["drv_signoff"]["verdict"] == "FAIL"
