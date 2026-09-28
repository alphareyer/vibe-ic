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


@pytest.fixture(autouse=True)
def _synthetic_scene_profile(tmp_path, monkeypatch):
    """Unit fixtures use a declared one-scene PDK profile, never a wildcard."""
    profiles = json.loads(drv._SCENE_PROFILES.read_text())
    profiles["synthetic"] = {"pvt": {"typ": {"nom_voltage": 5,
                            "nom_temperature": 25}}, "rc_corners": ["nom"]}
    path = tmp_path / "scene_profiles.json"
    path.write_text(json.dumps(profiles))
    monkeypatch.setattr(drv, "_SCENE_PROFILES", path)
    monkeypatch.setattr(drv, "_installed_image_digest",
                        lambda image: "sha256:" + "a" * 64
                        if image == "synthetic:test" else None)


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


def _refresh_scripts(bundle: dict, root: Path) -> None:
    scene = bundle["scenes"][0]
    reads = "".join(
        f"read_liberty {{{item['path']}}}\n"
        for item in scene["linked_liberties"])
    reads += (f"read_verilog {{{bundle['identity']['artifacts']['sta_netlist']['path']}}}\n"
              f"read_sdc {{{bundle['current']['sources']['signoff_sdc']['path']}}}\n"
              f"read_spef {{{scene['spef']['path']}}}\n")
    scene["tool_scripts"] = [
        _file(root, "measure.tcl", reads + drv._COMMAND + "\n"),
        _file(root, "control.tcl", reads +
              "set_max_fanout 1 [current_design]\n" + drv._COMMAND + "\n")]


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
              "scene_profile_sha256": drv._sha(drv._SCENE_PROFILES),
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
    all_limits = _file(tmp_path, "all.rpt", _report(fanout=2))
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
             "excluded_pins_report": _file(tmp_path, "excluded.json", "[]"),
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
            "spef_sha256": scene["spef"]["sha256"],
            "rc_corner": "nom",
            "extraction_command": _file(tmp_path, "extract.tcl",
                                        "extract rc=nom\n"),
            "extraction_command_sha256": drv._sha(tmp_path / "extract.tcl")}))
    digest = netlist["sha256"]
    bundle = {"identity": {"run_id": "run-1", "tree_sha": "tree-a",
             "run_started_at": "2026-09-28T12:00:00+08:00",
             "project": str(tmp_path), "pdk": "synthetic", "library": "logic",
             "spec_version": "contract-a", "sta_netlist": digest,
             "lvs_netlist": digest, "gds_netlist": digest,
             "artifacts": artifacts,
             "tool_image": "synthetic:test",
             "tool_image_digest": "sha256:" + "a" * 64,
             "openroad_commit": "tool-a", "opensta_commit": "tool-b",
             "pdk_commit": "pdk-a"},
            "frozen": frozen, "current": current, "stages": stages,
            "scenes": [scene], "pins": {"u/Y": {
                "net_class": "data", "cell_class": "std", "cell": "logic",
                "cell_pin": "Y", "liberty": "std", "driver_pin": "u/Y",
                "driver_cell": "logic", "loads": {"logical": 1,
                                                 "antenna_diode": 0, "cts_buffer": 0}}},
            "waiver_ledger": []}
    _refresh_scripts(bundle, tmp_path)
    return bundle


def _install_test_freeze(bundle: dict, root: Path, monkeypatch) -> None:
    frozen, identity = bundle["frozen"], bundle["identity"]
    static = root / "threshold_freezes.json"
    static.write_text(json.dumps({"schema_version": 1, "defaults": [{
        "pdk": identity["pdk"], "library": identity["library"],
        "pdk_config_sha256": frozen["sources"]["pdk_config"],
        "values": {"fanout": frozen["values"]["default_fanout_ceiling"],
                   "slew_ns": frozen["values"]["slew_ns"],
                   "cap_pf": frozen["values"]["cap_pf"]}}],
        "designs": [{"pdk": identity["pdk"], "library": identity["library"],
                     "sources": {k: frozen["sources"][k] for k in ("l7", "l9")},
                     **{k: copy.deepcopy(frozen[k]) for k in
                        ("values", "scope", "scenes", "rc_corners", "pvt",
                         "liberties", "scene_liberties", "scene_profile_sha256")},
                     "issued_at": "2026-09-27T00:00:00+00:00",
                     "owner_approval_citation": "approved synthetic test contract"}]}))
    signed = {"type": "threshold_freeze", "thresholds": copy.deepcopy(frozen),
              "standard_sha256": drv._sha(static),
              "owner_timestamp": "2026-09-27T00:00:00+00:00",
              "owner_quote": "approved synthetic test contract"}
    monkeypatch.setattr(drv, "_THRESHOLD_FREEZES", static, raising=False)
    monkeypatch.setattr(drv, "_owner_records",
                        lambda *args: ([signed], "OWNER_APPROVAL_ABSENT"))


def _violate(bundle: dict, root: Path, kind: str, *, value: float,
             limit: float, net_class="data", cell_class="std") -> None:
    scene = bundle["scenes"][0]
    numbers = {"fanout": 2, "fanout_limit": 4, "cap": .1,
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
    if net_class == "clock":
        scene["clock_network_pins"] = ["u/Y"]
    _refresh_scripts(bundle, root)


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
                        "report": _file(root, "timing.rpt",
                                        "worst slack max 1\nworst slack min 0.1\n")}],
            "signal_em": {"scene": "typ_nom", "worst_scene": True,
                          "netlist_sha256": netlist,
                          "report": _file(root, "em.rpt", json.dumps({
                              "currents": {"average": .1, "rms": .2, "peak": .3},
                              "limits": {"average": 1, "rms": 1, "peak": 1}})),
                          "drm_source": _file(root, "drm.txt", "current limits\n"),
                          "currents": {"average": .1, "rms": .2, "peak": .3},
            "limits": {"average": 1, "rms": 1, "peak": 1}}}


def _mock_verified_owner_record(monkeypatch, waiver, extra=()):
    # Isolate downstream waiver semantics from ssh-keygen verification. The
    # unsigned bundle ledger still has separate real-path refusal controls.
    monkeypatch.setattr(drv, "_owner_records",
                        lambda *args: ([{"type": "waiver", "waiver": waiver},
                                        *extra],
                                       "TEST_VERIFIED"))


def test_complete_measured_bundle_passes(tmp_path):
    assert drv.judge(_bundle(tmp_path))["verdict"] == "PASS"


def test_unknown_pdk_cannot_use_bundle_selected_scene_set(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["identity"]["pdk"] = "unknown_pdk"
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("scene profile unresolved" in reason
               for reason in result["not_measured"])


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


def test_extraction_command_hash_must_bind_real_file(tmp_path):
    bundle = _bundle(tmp_path)
    receipt = bundle["scenes"][0]["spef_extraction_receipt"]
    body = json.loads(Path(receipt["path"]).read_text())
    body["extraction_command_sha256"] = "b" * 64
    bundle["scenes"][0]["spef_extraction_receipt"] = _file(
        tmp_path, "spef_extraction.json", json.dumps(body))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("extraction input/output" in reason
               for reason in result["not_measured"])


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
    assert result["verdict"] == "FAIL"
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
    (lambda b: b["scenes"][0]["positive_control_counters"].update(max_slew=2),
     "positive control"),
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


def test_clock_label_without_tool_clock_network_proof_cannot_waive(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4,
             net_class="clock")
    bundle["scenes"][0].pop("clock_network_pins")
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("clock net classification" in reason
               for reason in result["not_measured"])


def test_std_cell_mislabeled_io_cannot_escape_cap_margin(tmp_path):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_capacitance", value=.3, limit=.2,
             cell_class="IO")
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert not result["io_margin_disclosures"]
    assert any("IO class lacks Liberty" in reason
               for reason in result["not_measured"])


@pytest.mark.parametrize("kind,value,limit", [
    ("max_slew", 4, 3), ("max_capacitance", .3, .2)])
def test_data_margin_can_only_be_waived_by_exact_owner_record(
        tmp_path, monkeypatch, kind, value, limit):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, kind, value=value, limit=limit)
    first = drv.judge(bundle)
    assert first["verdict"] == "FAIL"
    assert first["findings"][0]["failed_tier"] == "T3_MARGIN"
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path,
                                               first["findings"][0])]
    assert drv.judge(bundle)["verdict"] == "FAIL"
    assert "OWNER_SIGNATURE_UNAVAILABLE" in " ".join(drv.judge(bundle)["failures"])
    _mock_verified_owner_record(monkeypatch, bundle["waiver_ledger"][0])
    assert drv.judge(bundle)["verdict"] == "WAIVED"
    bundle["waiver_ledger"][0]["sdc_sha256"] = "0" * 64
    assert drv.judge(bundle)["verdict"] == "FAIL"


@pytest.mark.parametrize("false_report", ["timing", "em"])
def test_signed_waiver_cannot_self_report_good_timing_or_em(
        tmp_path, monkeypatch, false_report):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_capacitance", value=.3, limit=.2)
    row = drv.judge(bundle)["findings"][0]
    waiver = _owner_waiver(bundle, tmp_path, row)
    if false_report == "timing":
        waiver["timing"][0]["report"] = _file(
            tmp_path, "timing.rpt",
            "worst slack max -1\nworst slack min 0.1\n")
    else:
        waiver["signal_em"]["report"] = _file(
            tmp_path, "em.rpt", json.dumps({
                "currents": {"average": 2, "rms": .2, "peak": .3},
                "limits": {"average": 1, "rms": 1, "peak": 1}}))
    _mock_verified_owner_record(monkeypatch, waiver)
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert not result["waived"]


def test_clock_waiver_needs_signed_limits_preceding_run(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4,
             net_class="clock")
    row = drv.judge(bundle)["findings"][0]
    waiver = _owner_waiver(bundle, tmp_path, row)
    waiver.update(clock_reason="intentional_cts_tradeoff",
                  predeclared_clock_skew_limit=100,
                  predeclared_clock_latency_limit=100,
                  clock_metrics={"skew": .5, "latency": .8,
                                 "report": _file(tmp_path, "clock.json",
                                                 json.dumps({"skew": .5,
                                                             "latency": .8}))})
    _mock_verified_owner_record(monkeypatch, waiver)
    assert drv.judge(bundle)["verdict"] == "FAIL"
    prior = {"type": "clock_limits", "owner_quote": "Approve clock limits",
             "owner_timestamp": "2026-09-28T10:00:00+08:00",
             "skew_limit": 1, "latency_limit": 1}
    _mock_verified_owner_record(monkeypatch, waiver, [prior])
    assert drv.judge(bundle)["verdict"] == "WAIVED"
    prior["owner_timestamp"] = "2026-09-28T13:00:00+08:00"
    assert drv.judge(bundle)["verdict"] == "FAIL"


def test_io_default_fanout_one_catches_three_below_design_four(tmp_path):
    bundle = _bundle(tmp_path)
    io = _file(tmp_path, "io.lib", '''library (io) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_max_fanout : 1;
 default_max_capacitance : 999;
 cell (pad) { pad_cell : true; pin (Y) { direction : output; } }
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
 cell (pad) { pad_cell : true; pin (Y) { direction : output; } }
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


def test_antenna_excess_needs_exact_owner_ledger(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4, "antenna_diode": 1,
                                            "cts_buffer": 0}
    first = drv.judge(bundle)
    assert first["verdict"] == "FAIL"
    row = first["findings"][0]
    bundle["waiver_ledger"].append(_owner_waiver(bundle, tmp_path, row))
    assert drv.judge(bundle)["verdict"] == "FAIL"
    _mock_verified_owner_record(monkeypatch, bundle["waiver_ledger"][0])
    assert drv.judge(bundle)["verdict"] == "WAIVED"
    bundle["identity"]["run_id"] = "run-2"
    assert drv.judge(bundle)["verdict"] == "FAIL"
    bundle["identity"]["run_id"] = "run-1"
    bundle["waiver_ledger"][0]["sdc_sha256"] = "0" * 64
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
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_bundle(tmp_path)))
    row = runner._run_declared_signoff_gate(
        tmp_path, "drv_signoff", "drv_signoff_judge.py",
        "reports/phase3/sta/drv_signoff.json")
    assert row.status == "NOT_MEASURED"
    assert "fresh DRV capture did not complete" in row.detail
    assert any(s[0] == "drv_signoff" for s in runner._PRESTREAM_GATES)
    assert any(s[0] == "drv_signoff" for s in runner._DECLARED_SIGNOFF_GATES)


def test_consumer_preserves_waived_word_and_owner_row(tmp_path, monkeypatch):
    import subprocess
    import drv_signoff_capture as capture
    import drv_capture_plan as capture_plan
    import phase3_one_shot_runner as runner
    _file(tmp_path, "phase3/stage3/pnr/routed.def", "ROUTED DEF\n")
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4,
                                            "antenna_diode": 1,
                                            "cts_buffer": 0}
    row = drv.judge(bundle)["findings"][0]
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path, row)]
    _mock_verified_owner_record(monkeypatch, bundle["waiver_ledger"][0])
    _file(tmp_path, "reports/phase3/sta/drv_capture_plan.json", "{}")
    monkeypatch.setattr(capture, "capture", lambda *a, **k: bundle)
    monkeypatch.setattr(capture_plan, "capture_and_publish", lambda *a, **k: source)
    def run_judge(cmd, **kwargs):
        # A separately verified judge receipt is the consumer's contract.
        Path(cmd[-1]).write_text(json.dumps({
            "name": "DRV(tran/cap/fanout)", "verdict": "WAIVED",
            "waived": [{"key": {"driver_pin": "u/Y"},
                        "below_baseline_quality": True}]}))
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(runner._pr, "run", run_judge)
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(bundle))
    gate = runner._run_declared_signoff_gate(
        tmp_path, "drv_signoff", "drv_signoff_judge.py",
        "reports/phase3/sta/drv_signoff.json")
    assert gate.status == "WAIVED"
    assert gate.waiver_rows[0]["owner"] == "reyerchu"


def test_flow_step_refuses_unsigned_owner_ledger(tmp_path):
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
    assert result.status == "FAIL", result.reasons
    receipt = json.loads((tmp_path / "reports/phase3/sta/drv_signoff.json").read_text())
    assert "OWNER_SIGNATURE_UNAVAILABLE" in " ".join(receipt["failures"])
    bundle["waiver_ledger"][0]["origin"] = "flow"
    source.write_text(json.dumps(bundle))
    result = flow.check_step(tmp_path, step, {})
    assert result.status == "FAIL"


def test_step32_rechecks_final_state_identity_after_late_repair(tmp_path, monkeypatch):
    import librelane_postroute_repair as repair
    import drv_capture_plan
    monkeypatch.setattr(drv, "judge", lambda *a, **k: {
        "name": "DRV(tran/cap/fanout)", "verdict": "PASS",
        "failures": [], "not_measured": []})
    bundle = _bundle(tmp_path)
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(bundle))
    state = tmp_path / "final_state.json"
    state.write_text(json.dumps({"def": bundle["identity"]["artifacts"]["def"]["path"]}))
    sta_state = tmp_path / "sta_state.json"
    sta_state.write_text("{}")
    report = {"adopted_state": str(state), "final": {"sta_state": str(sta_state)}}
    monkeypatch.setattr(drv_capture_plan, "capture_and_publish",
                        lambda *a, **k: source.write_text(json.dumps(bundle)))
    repair._step32_drv_signoff(tmp_path, report)
    assert report["drv_signoff"]["verdict"] == "PASS"
    bundle["identity"]["artifacts"]["def"] = _file(
        tmp_path, "other.def", "OTHER ROUTE\n")
    source.write_text(json.dumps(bundle))
    repair._step32_drv_signoff(tmp_path, report)
    assert report["drv_signoff"]["verdict"] == "NOT_MEASURED"
    assert report["verdict"] == "NOT_MEASURED"


def test_step32_missing_final_state_refuses_stale_pass(tmp_path, monkeypatch):
    import librelane_postroute_repair as repair
    monkeypatch.setattr(drv, "judge", lambda *a, **k: {
        "name": "DRV(tran/cap/fanout)", "verdict": "PASS",
        "failures": [], "not_measured": []})
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    bundle = _bundle(tmp_path)
    source.write_text(json.dumps(bundle))
    state = tmp_path / "adopted_state.json"
    state.write_text(json.dumps({"def": bundle["identity"]["artifacts"]["def"]["path"]}))
    report = {"verdict": "PASS", "adopted_state": str(state)}
    repair._step32_drv_signoff(tmp_path, report)
    assert report["drv_signoff"]["verdict"] == "NOT_MEASURED"
    assert report["verdict"] == "NOT_MEASURED"
    assert not source.exists()


def test_capture_plan_reads_applied_clock_and_io_values():
    from drv_capture_plan import _clock_io_values
    sdc = ("# create_clock -period 99 [get_ports clk]\n"
           "create_clock -name clk -period 24.0 [get_ports clk]\n"
           "set_input_delay 4.8 -clock clk [all_inputs]\n"
           "set_output_delay 4.8 -clock clk [all_outputs]\n")
    assert _clock_io_values(sdc) == (24.0, 4.8)
    assert _clock_io_values(sdc + "set_output_delay 5 -clock clk [all_outputs]\n") == (24.0, None)


def test_scene_linked_liberty_is_the_hard_slew_limit(tmp_path):
    bundle = _bundle(tmp_path)
    ff = _file(tmp_path, "ff.lib", '''library (ff) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 cell (logic) { pin (Y) { max_transition : 2.6;
   max_capacitance : 20; } }
}''')
    bundle["current"]["liberties"] = [{"name": "ff", **ff}]
    bundle["frozen"]["liberties"] = {"ff": ff["sha256"]}
    bundle["frozen"]["scene_liberties"]["typ_nom"] = ["ff"]
    bundle["current"]["scene_liberties"]["typ_nom"] = ["ff"]
    bundle["scenes"][0]["linked_liberties"] = [{"name": "ff", **ff}]
    bundle["scenes"][0]["liberty"] = "ff"
    _violate(bundle, tmp_path, "max_slew", value=3.2, limit=2.6)
    result = drv.judge(bundle)
    assert result["findings"][0]["failed_tier"] == "T1_LIBERTY"
    assert result["findings"][0]["waivable"] is False


def test_clean_tool_flag_cannot_hide_wider_cap_limit(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["scenes"][0]["all_limits_report"] = _file(
        tmp_path, "all.rpt", _report(cap=.3, cap_limit=.4))
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert any("max_capacitance" in error and "limit" in error
               for error in result["failures"])


def test_tool_script_cannot_link_an_unrecorded_sdc(tmp_path):
    bundle = _bundle(tmp_path)
    script = Path(bundle["scenes"][0]["tool_scripts"][0]["path"])
    bad = script.read_text().replace(
        bundle["current"]["sources"]["signoff_sdc"]["path"],
        str(tmp_path / "substitute.sdc"))
    bundle["scenes"][0]["tool_scripts"][0] = _file(
        tmp_path, "measure.tcl", bad)
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("STA tool script reads" in reason
               for reason in result["not_measured"])


def test_bundle_image_digest_must_match_installed_image(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["identity"]["tool_image_digest"] = "sha256:" + "b" * 64
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("image digest" in reason for reason in result["not_measured"])


def test_control_expected_is_recomputed_from_real_population(tmp_path):
    bundle = _bundle(tmp_path)
    # Fanout one is part of the all-limits census but cannot violate limit one.
    # A fabricated control row/count of one used to make this bundle pass.
    bundle["scenes"][0]["all_limits_report"] = _file(
        tmp_path, "all.rpt", _report(fanout=1))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("max_fanout" in reason and "control" in reason
               for reason in result["not_measured"])


def test_capture_ignores_plan_population_and_excluded_pin_assertions(
        tmp_path, monkeypatch):
    import subprocess
    import drv_signoff_capture as capture
    plan = _bundle(tmp_path)
    plan["top"] = "top"
    scene = plan["scenes"][0]
    scene["population"] = {kind: 0 for kind in drv.KINDS}
    scene["excluded_pins"] = [{"pin": "u/Y", "reason": "constant",
                               "fanout": 0, "cap_pf": 0,
                               "slew_rise_ns": 0, "slew_fall_ns": 0}]
    scene["clock_network_pins"] = ["u/Y"]
    def fake_fresh(script, roots, *, image):
        folder = script.parent
        if script.name == "positive_control.tcl":
            (folder / "positive_control.rpt").write_text(_report(
                fanout=2, fanout_limit=1, cap_limit=.01,
                slew_limit=.01, violators=True))
            (folder / "positive_control_counters.log").write_text(
                "".join(f"DRV_COUNTER {kind} 1\n" for kind in drv.KINDS))
        else:
            (folder / "annotation.rpt").write_text(
                "Found 0 unannotated drivers.\n")
            (folder / "clocks.rpt").write_text("propagated\n")
            (folder / "violators.rpt").write_text(_report(violators=True))
            (folder / "all_limits.rpt").write_text(_report(fanout=2))
            (folder / "counters.log").write_text(
                "".join(f"DRV_COUNTER {kind} 0\n" for kind in drv.KINDS))
        return "OpenSTA 3.1 aaaaaaaaaa\n"
    monkeypatch.setattr(capture, "_run_fresh", fake_fresh)
    monkeypatch.setattr(capture.subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a, 0, json.dumps({
            "Id": "sha256:" + "a" * 64,
            "Config": {"Labels": {"org.opencontainers.image.version": "test"}}}), ""))
    bundle = capture.capture(plan, tmp_path / "captured", image="synthetic:test")
    row = bundle["scenes"][0]
    assert row["population"] == {kind: 1 for kind in drv.KINDS}
    assert row["all_limits_max_count"] > 1
    assert row["excluded_pins_recorded"] is False
    assert "excluded_pins" not in row
    assert "clock_network_pins" not in row


def test_capture_replaces_plan_pin_classes_with_opensta_and_liberty(tmp_path, monkeypatch):
    import subprocess
    import drv_signoff_capture as capture
    plan = _bundle(tmp_path)
    plan["top"] = "top"
    plan["pins"]["u/Y"]["cell_class"] = "IO"
    plan["scenes"][0]["clock_network_pins"] = ["u/Y"]
    plan["scenes"][0]["excluded_pins"] = [{"pin": "invented", "reason": "ideal"}]
    anchor = {"path": "/image/pdk/config.tcl", "sha256":
              plan["current"]["sources"]["pdk_config"]["sha256"],
              "pdk_commit": "installed-version"}
    monkeypatch.setattr(capture, "image_pdk_anchor", lambda *a: anchor, raising=False)

    def fresh(script, roots, *, image):
        folder = script.parent
        if script.name == "positive_control.tcl":
            (folder / "positive_control.rpt").write_text(_report(
                fanout=2, fanout_limit=1, cap_limit=.01,
                slew_limit=.01, violators=True))
            (folder / "positive_control_counters.log").write_text(
                "".join(f"DRV_COUNTER {kind} 1\n" for kind in drv.KINDS))
        else:
            (folder / "pin_census.tsv").write_text(
                "u/Y\tpin\toutput\t1\tu\tlogic\tY\tn\tinput\t0.02\t0.02\t0\tX\t0\n"
                "u2/Y\tpin\toutput\t1\tu2\tlogic\tY\tn2\tinput\t0.03\t0.04\t0\t1\t0\n")
            (folder / "net_census.rpt").write_text(
                "Net n\n Total capacitance: 0.1\n Number of drivers: 1\n"
                " Number of loads: 0\n Number of pins: 1\n\n"
                "Net n2\n Total capacitance: 0.05\n Number of drivers: 1\n"
                " Number of loads: 0\n Number of pins: 1\n")
            (folder / "disabled_edges.rpt").write_text("")
            (folder / "annotation.rpt").write_text("Found 0 unannotated drivers.\n")
            (folder / "clocks.rpt").write_text("propagated\n")
            (folder / "violators.rpt").write_text(_report(violators=True))
            (folder / "all_limits.rpt").write_text(_report(fanout=2))
            (folder / "counters.log").write_text(
                "".join(f"DRV_COUNTER {kind} 0\n" for kind in drv.KINDS))
        return "OpenSTA 3.1 aaaaaaaaaa\n"

    monkeypatch.setattr(capture, "_run_fresh", fresh)
    monkeypatch.setattr(capture.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps({
                            "Id": "sha256:" + "a" * 64,
                            "Config": {"Labels": {"org.opencontainers.image.version": "test"}}}), ""))
    result = capture.capture(plan, tmp_path / "captured", image="synthetic:test")
    scene = result["scenes"][0]
    assert result["pins"]["u/Y"]["cell_class"] == "std"
    assert scene["clock_network_pins"] == []
    assert scene["driver_pin_census"] == ["u/Y", "u2/Y"]
    assert scene["excluded_pins_recorded"] is True
    assert scene["excluded_pins"] == [{"pin": "u2/Y", "reason": "constant",
                                        "excluded_kinds": list(drv.KINDS),
                                        "fanout": 0.0, "cap_pf": .05,
                                        "slew_rise_ns": .03, "slew_fall_ns": .04}]
    assert result["threshold_anchor"] == anchor
    assert result["identity"]["pdk_commit"] == "installed-version"


def test_project_judge_rejects_plan_anchor_even_when_bundle_is_self_consistent(
        tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    anchor = {"path": "/image/pdk/config.tcl", "sha256":
              bundle["current"]["sources"]["pdk_config"]["sha256"],
              "pdk_commit": "installed-version"}
    bundle["threshold_anchor"] = dict(anchor, sha256="0" * 64)
    monkeypatch.setattr(drv, "image_pdk_anchor", lambda *a: anchor, raising=False)
    result = drv.judge(bundle, project=tmp_path)
    assert any("frozen threshold anchor differs from installed image" in reason
               for reason in result["not_measured"])


def test_net_census_rejects_negated_count(tmp_path):
    from drv_signoff_census import _nets
    report = tmp_path / "net.rpt"
    report.write_text("Net n\n Total capacitance: 0.1\n"
                      " Number of loads: NOT 0\n")
    with pytest.raises(ValueError, match="malformed"):
        _nets(report)


def test_annotation_exclusion_requires_lef_pg_and_no_liberty_arc(tmp_path):
    from drv_signoff_annotation import derive as annotation
    folder = tmp_path / "scene"
    _file(folder, "annotation.rpt", "Found 2 unannotated drivers.\n"
          " u/VDD\n p\nFound 0 partially unannotated drivers.\n")
    lef = _file(tmp_path, "pad.lef", "MACRO pad\n PIN VDD\n USE POWER ;\n"
                " END VDD\n PIN PAD\n USE SIGNAL ;\n END PAD\nEND pad\n")
    lib = _file(tmp_path, "pad.lib", '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 cell (pad) { pad_cell : true;
  pin (VDD) { direction : input; }
  pin (PAD) { direction : input; timing () { related_pin : "PAD"; } }
 }
}''')
    routed = _file(tmp_path, "route.def", "NETS 1 ;\n - p ( PIN p ) ( u PAD ) ;\nEND NETS\n")
    spef = _file(tmp_path, "route.spef", "*D_NET supply 0.1\n*END\n")
    pins = {
        "u/VDD": {"kind": "pin", "cell": "pad", "cell_pin": "VDD",
                  "cell_class": "IO", "net": "supply"},
        "p": {"kind": "port", "cell": None, "cell_pin": None,
              "cell_class": "port", "net": "p"},
        "u/PAD": {"kind": "pin", "cell": "pad", "cell_pin": "PAD",
                  "cell_class": "IO", "net": "p"},
    }
    args = (folder, pins, [lib], [lef], Path(routed["path"]), Path(spef["path"]))
    clean = annotation(*args)
    assert {row["reason"] for row in clean["resolved"]} == {
        "lef_pg_no_liberty_arc", "port_pad_zero_routed_segments"}
    assert clean["unresolved"] == []
    Path(lef["path"]).write_text(Path(lef["path"]).read_text().replace(
        "USE POWER", "USE SIGNAL"))
    Path(routed["path"]).write_text("NETS 1 ;\n - p ( PIN p ) ( u PAD ) + ROUTED metal1 ( 0 0 ) ( 1 0 ) ;\nEND NETS\n")
    bad = annotation(*args)
    assert {row["pin"] for row in bad["unresolved"]} == {"u/VDD", "p"}
    Path(spef["path"]).write_text("*D_NET p 0.1\n*END\n")
    restored = annotation(*args)
    assert [row["pin"] for row in restored["unresolved"]] == ["u/VDD"]


def test_project_clean_opensta_census_can_reach_pass(tmp_path, monkeypatch):
    from drv_signoff_census import derive
    bundle = _bundle(tmp_path)
    l7 = _file(tmp_path, "input/docs/L7.md", "verification plan\n")
    bundle["current"]["sources"]["l7"] = l7
    bundle["frozen"]["sources"]["l7"] = l7["sha256"]
    root = tmp_path / "installed_pdks"
    pdk = _file(root / "synthetic", "libs.tech/librelane/logic/config.tcl",
                Path(bundle["current"]["sources"]["pdk_config"]["path"]).read_text())
    bundle["current"]["sources"]["pdk_config"] = pdk
    bundle["frozen"]["sources"]["pdk_config"] = pdk["sha256"]
    anchor = {"path": "/image/synthetic/config.tcl", "sha256": pdk["sha256"],
              "pdk_commit": "installed-version"}
    bundle["threshold_anchor"] = anchor
    bundle["identity"]["pdk_commit"] = anchor["pdk_commit"]
    monkeypatch.setattr(drv, "image_pdk_anchor", lambda *a: anchor)
    _file(tmp_path, "phase3/librelane_pdk_root.provenance.json", json.dumps({
        "path": str(root),
        "derivation": {"image_id": bundle["identity"]["tool_image_digest"]}}))
    scene = bundle["scenes"][0]
    folder = tmp_path / "census"
    _file(folder, "pin_census.tsv",
          "u/Y\tpin\toutput\t1\tu\tlogic\tY\tn\tinput\t0.1\t0.1\t0\tX\t0\n")
    _file(folder, "net_census.rpt",
          "Net n\n Total capacitance: 0.1\n Number of drivers: 1\n"
          " Number of loads: 0\n Number of pins: 1\n")
    _file(folder, "disabled_edges.rpt", "")
    rows = drv.parse_check_types(Path(scene["all_limits_report"]["path"]).read_text(),
                                 scene=scene["name"], mode=scene["mode"],
                                 violators_only=False)
    census = derive(folder, scene["linked_liberties"], rows)
    bundle["pins"] = census["pins"]
    scene.update(excluded_pins=census["excluded"],
                 clock_network_pins=census["clock_network_pins"],
                 driver_pin_census=census["driver_pins"],
                 population=census["population"],
                 pin_census_report=_file(folder, "pin_census.tsv",
                                         (folder / "pin_census.tsv").read_text()),
                 net_census_report=_file(folder, "net_census.rpt",
                                         (folder / "net_census.rpt").read_text()),
                 disabled_edges_report=_file(folder, "disabled_edges.rpt", ""))
    _install_test_freeze(bundle, tmp_path, monkeypatch)
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "PASS", result
    scene["pin_census_report"] = {}
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("pin_census_report" in reason for reason in result["not_measured"])


def test_opensta_census_io_label_comes_from_linked_pad_cell(tmp_path):
    from drv_signoff_census import derive
    folder = tmp_path / "tool"
    _file(folder, "pin_census.tsv",
          "u/Y\tpin\toutput\t1\tu\tpad\tY\tn\tinput\t0.1\t0.1\t0\tX\t0\n")
    _file(folder, "net_census.rpt",
          "Net n\n Total capacitance: 0.1\n Number of drivers: 1\n"
          " Number of loads: 0\n Number of pins: 1\n")
    _file(folder, "disabled_edges.rpt", "")
    rows = drv.parse_check_types(_report(fanout=2), scene="typ_nom",
                                 mode="functional", violators_only=False)
    body = '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 cell (pad) { pad_cell : true; pin (Y) { direction : output; } }
}'''
    lib = _file(tmp_path, "pad.lib", body)
    assert derive(folder, [{"name": "io", **lib}], rows)["pins"]["u/Y"]["cell_class"] == "IO"
    lib = _file(tmp_path, "pad.lib", body.replace("pad_cell : true;", ""))
    assert derive(folder, [{"name": "io", **lib}], rows)["pins"]["u/Y"]["cell_class"] == "std"


def test_unreported_unexcluded_driver_blocks_census(tmp_path):
    from drv_signoff_census import derive
    folder = tmp_path / "tool"
    _file(folder, "pin_census.tsv",
          "u/Y\tpin\toutput\t1\tu\tlogic\tY\tn\tinput\t0.1\t0.1\t0\tX\t0\n"
          "u2/Y\tpin\toutput\t1\tu2\tlogic\tY\tn2\tinput\t0.1\t0.1\t0\tX\t0\n")
    _file(folder, "net_census.rpt",
          "Net n\n Total capacitance: 0.1\n Number of drivers: 1\n"
          " Number of loads: 0\n Number of pins: 1\n\n"
          "Net n2\n Total capacitance: 0.1\n Number of drivers: 1\n"
          " Number of loads: 0\n Number of pins: 1\n")
    _file(folder, "disabled_edges.rpt", "")
    lib = _file(tmp_path, "logic.lib", '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 cell (logic) { pin (Y) { direction : output; } }
}''')
    rows = drv.parse_check_types(_report(fanout=2), scene="typ_nom",
                                 mode="functional", violators_only=False)
    with pytest.raises(ValueError, match="omitted driver"):
        derive(folder, [{"name": "std", **lib}], rows)


def test_partially_unannotated_drivers_block_a_clean_verdict(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["scenes"][0]["parasitic_annotation_report"] = _file(
        tmp_path, "annotation.rpt",
        "Found 0 unannotated drivers.\n"
        "Found 1 partially unannotated drivers.\n")
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("unannotated" in reason for reason in result["not_measured"])


def test_excluded_constant_driver_is_rejudged_from_raw_values(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["pins"]["u/Y"]["net_class"] = "constant"
    excluded = [{"pin": "u/Y", "reason": "constant", "fanout": 5,
                 "cap_pf": .1, "slew_rise_ns": .1, "slew_fall_ns": .1}]
    bundle["scenes"][0]["excluded_pins"] = excluded
    bundle["scenes"][0]["excluded_pins_report"] = _file(
        tmp_path, "excluded.json", json.dumps(excluded))
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert any(row["pin"] == "u/Y" and row["excluded_reason"] == "constant"
               for row in result["findings"])


def test_plan_exclusion_without_tool_report_is_unmeasured(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["scenes"][0]["excluded_pins_report"] = {}
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("excluded pin report" in reason
               for reason in result["not_measured"])


def test_missing_routed_def_downgrades_waived_to_not_measured(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_fanout", value=5, limit=4)
    bundle["pins"]["u/Y"]["loads"] = {"logical": 4, "antenna_diode": 1,
                                          "cts_buffer": 0}
    row = drv.judge(bundle)["findings"][0]
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path, row)]
    _mock_verified_owner_record(monkeypatch, bundle["waiver_ledger"][0])
    _file(tmp_path, "reports/phase3/sta/drv_capture_plan.json", "{}")
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(bundle))
    output = tmp_path / "receipt.json"
    assert drv.main([str(tmp_path), "--json", str(output)]) != 0
    assert json.loads(output.read_text())["verdict"] == "NOT_MEASURED"


def test_dispatcher_drv_receipt_lookup_never_indexes_past_argv(tmp_path):
    # Review wave 57: argv[1] was read before its length was checked, so a
    # gate that resolved to one argv element became "program invocation
    # error: list index out of range" and lost its awaiting/incomplete tier.
    import flow_compliance_check as flow
    judge = str(Path(flow.__file__).with_name("drv_signoff_judge.py"))
    assert flow._drv_judge_receipt(tmp_path, ["python3"]) is None
    assert flow._drv_judge_receipt(tmp_path, []) is None
    assert flow._drv_judge_receipt(tmp_path, ["python3", judge, "."]) is None
    assert flow._drv_judge_receipt(
        tmp_path, ["python3", judge, ".", "--json"]) is None
    assert flow._drv_judge_receipt(
        tmp_path, ["python3", "other.py", ".", "--json", "x.json"]) is None
    assert flow._drv_judge_receipt(
        tmp_path, ["python3", judge, ".", "--json", "r/d.json"]) == tmp_path / "r/d.json"
    absolute = tmp_path / "abs.json"
    assert flow._drv_judge_receipt(
        tmp_path, ["python3", judge, "--json", str(absolute)]) == absolute


def test_step23_without_capture_plan_is_not_measured(tmp_path):
    import flow_compliance_check as flow
    step = {"id": 23, "name": "post-route STA", "stage": "phase3/stage3/sta",
            "required_outputs": [], "gate": {"all_of": [
                {"program_exit_zero": "drv_signoff_judge . --json "
                 "reports/phase3/sta/drv_signoff.json"}]}}
    result = flow.check_step(tmp_path, step, {})
    assert result.status == "NOT_MEASURED", result.reasons


def test_runner_cannot_reuse_stale_pass_receipt_after_judge_crash(tmp_path, monkeypatch):
    import subprocess
    import drv_signoff_capture as capture
    import phase3_one_shot_runner as runner
    bundle = _bundle(tmp_path)
    _file(tmp_path, "reports/phase3/sta/drv_capture_plan.json", "{}")
    monkeypatch.setattr(capture, "capture", lambda *a, **k: bundle)
    output = tmp_path / "reports/phase3/sta/drv_signoff.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"name": "DRV(tran/cap/fanout)",
                                  "verdict": "PASS"}))
    monkeypatch.setattr(runner._pr, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, "", "judge crashed"))
    row = runner._run_declared_signoff_gate(
        tmp_path, "drv_signoff", "drv_signoff_judge.py",
        "reports/phase3/sta/drv_signoff.json")
    assert row.status == "NOT_MEASURED"
    assert not output.exists()


def test_step32_adoption_requires_applied_drv_stage(tmp_path):
    bundle = _bundle(tmp_path)
    _file(tmp_path, "reports/phase3/librelane_postroute_repair.json",
          json.dumps({"adopted": "repair_candidate"}))
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "FAIL"
    assert any("postroute_repair: required applied constraints absent" in item
               for item in result["failures"])


def test_step32_in_chain_receipt_must_match_route_handoff(tmp_path):
    bundle = _bundle(tmp_path)
    _file(tmp_path, "reports/phase3/librelane_postroute_repair.json",
          json.dumps({"site": "after_route", "adopted": "repair_candidate"}))
    _file(tmp_path, "reports/phase3/librelane_route_handoff.json",
          json.dumps({"postroute_repair": {"report_sha256": "0" * 64}}))
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "FAIL"
    assert any("not bound to route handoff" in item
               for item in result["not_measured"])


def test_plan_override_flag_cannot_authorize_wider_slew(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["frozen"]["values"]["slew_ns"] = 4
    bundle["current"]["values"]["slew_ns"] = 4
    bundle["owner_design_override"] = {"slew_ns": "approved"}
    sdc = _file(tmp_path, "signoff_sdc",
                "set_max_fanout 4 [current_design]\n"
                "set_max_transition 4 [current_design]\n"
                "set_max_capacitance 0.2 [current_design]\n")
    bundle["current"]["sources"]["signoff_sdc"] = sdc
    bundle["frozen"]["sources"]["signoff_sdc"] = sdc["sha256"]
    signoff = next(s for s in bundle["stages"] if s["name"] == "signoff_sta")
    signoff["applied"]["slew_ns"] = 4
    signoff["sdc_snapshot"] = sdc
    bundle["scenes"][0]["all_limits_report"] = _file(
        tmp_path, "all.rpt", _report(fanout=2, slew_limit=4))
    result = drv.judge(bundle)
    assert result["verdict"] == "FAIL"
    assert any("design override" in error for error in result["failures"])


def test_directory_judge_binds_project_to_argument(tmp_path):
    bundle = _bundle(tmp_path)
    other = tmp_path / "other_project"
    l9 = other / "phase1/generated_docs/L9.md"
    l9.parent.mkdir(parents=True)
    l9.write_text("| SYNTH_MAX_FANOUT | 4 |\n")
    bundle["identity"]["project"] = str(other)
    source = tmp_path / "reports/phase3/sta/drv_signoff_bundle.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(bundle))
    routed = tmp_path / "phase3/stage3/pnr/routed.def"
    routed.parent.mkdir(parents=True)
    routed.write_text("ROUTED DEF\n")
    output = tmp_path / "receipt.json"
    drv.main([str(tmp_path), "--json", str(output)])
    result = json.loads(output.read_text())
    assert result["verdict"] == "NOT_MEASURED"
    assert any("project" in reason for reason in result["not_measured"])


def test_project_judge_refuses_pdk_config_outside_installed_root(tmp_path):
    bundle = _bundle(tmp_path)
    root = tmp_path / "installed_pdks"
    (root / "synthetic").mkdir(parents=True)
    _file(tmp_path, "phase3/librelane_pdk_root.provenance.json",
          json.dumps({"path": str(root),
                      "derivation": {"image_id": "sha256:" + "a" * 64}}))
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("PDK config is not inside installed PDK root" in reason
               for reason in result["not_measured"])


def test_json_only_cli_cannot_issue_project_signoff_pass(tmp_path):
    bundle = _bundle(tmp_path)
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle))
    output = tmp_path / "receipt.json"
    assert drv.main([str(source), "--json", str(output)]) != 0
    assert json.loads(output.read_text())["verdict"] == "NOT_MEASURED"


def test_declared_signoff_rollup_separates_waived_from_unchecked():
    import phase3_one_shot_runner as runner
    row = runner.StepResult(
        "drv_signoff", "WAIVED", waiver_rows=[{"id": "u/Y",
        "reason": "owner DRV deviation", "owner": "reyerchu"}])
    rollup = runner.declared_signoff_rollup([row])
    assert rollup["waived"] == ["drv_signoff"]
    assert rollup["not_checked"] == []
    assert "WAIVED" in rollup["line"]


def test_prestream_keeps_unmeasured_drv_distinct_from_failed_and_waived():
    import phase3_one_shot_runner as runner
    clean = runner.StepResult("route", "PASS")
    unmeasured = runner.StepResult(
        "drv_signoff", "NOT_MEASURED", reason_class="partial_population")
    waived = runner.StepResult(
        "drv_signoff", "WAIVED", waiver_rows=[{
            "id": "u/Y", "reason": "owner DRV deviation",
            "owner": "reyerchu"}])
    failed = runner.StepResult("route", "FAIL")
    assert runner._prestream_status([clean, unmeasured])[0] == "NOT_MEASURED"
    assert runner._prestream_status([clean, waived])[0] == "WAIVED"
    assert runner._prestream_status([clean, failed, unmeasured])[0] == "FAIL"
    assert runner._prestream_status([clean])[0] == "PASS"


def test_completion_audit_names_waived_without_a_pass_claim(
        tmp_path, monkeypatch, capsys):
    import phase23_completion_self_audit_check as audit
    monkeypatch.setattr(audit, "_run_compliance", lambda *a, **k:
                        (1, "Overall: WAIVED  (strict=True)\n"
                            "Steps: 34 total (33/34 executed PASS, 0 DEFERRED)\n"))
    monkeypatch.setattr(sys, "argv", ["phase23_completion_self_audit_check",
                                      str(tmp_path)])
    assert audit.main() == 1
    output = capsys.readouterr().out
    assert "[WAIVED] phase23_completion_self_audit_check" in output
    assert "below the recorded baseline quality" in output.lower()


def test_unrelated_missing_measurement_does_not_invalidate_owner_waiver(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    _violate(bundle, tmp_path, "max_capacitance", value=.3, limit=.2)
    row = drv.judge(bundle)["findings"][0]
    bundle["waiver_ledger"] = [_owner_waiver(bundle, tmp_path, row)]
    _mock_verified_owner_record(monkeypatch, bundle["waiver_ledger"][0])
    bundle["scenes"][0]["unannotated_nets"] = 1
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert result["waived"]
    assert not any("no valid owner waiver" in failure
                   for failure in result["failures"])


def test_scene_rc_suffix_must_match_extraction_rc(tmp_path):
    bundle = _bundle(tmp_path)
    scene = bundle["scenes"][0]
    bundle["frozen"]["rc_corners"].append("min")
    scene["rc_corner"] = "min"
    scene["spef_extraction_receipt"] = _file(
        tmp_path, "spef_extraction.json", json.dumps({
            "routed_def_sha256": bundle["identity"]["artifacts"]["def"]["sha256"],
            "spef_sha256": scene["spef"]["sha256"],
            "rc_corner": "min", "extraction_command_sha256": "a" * 64}))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("RC corner" in reason for reason in result["not_measured"])


def test_unmatched_l9_fanout_scope_cannot_fall_back_to_pdk_ten(tmp_path):
    bundle = _bundle(tmp_path)
    l9 = _file(tmp_path, "phase1/generated_docs/L9.md",
               "| Library | MAX_FANOUT_CONSTRAINT |\n"
               "| --- | --- |\n| neutral_* | 4 |\n")
    bundle["current"]["sources"]["l9"] = l9
    bundle["frozen"]["sources"]["l9"] = l9["sha256"]
    bundle["identity"]["library"] = "other_library"
    bundle["frozen"]["values"]["fanout"] = 10
    bundle["current"]["values"]["fanout"] = 10
    sdc = _file(tmp_path, "signoff_sdc",
                "set_max_fanout 10 [current_design]\n"
                "set_max_transition 3 [current_design]\n"
                "set_max_capacitance 0.2 [current_design]\n")
    bundle["current"]["sources"]["signoff_sdc"] = sdc
    bundle["frozen"]["sources"]["signoff_sdc"] = sdc["sha256"]
    for stage in bundle["stages"]:
        stage["applied"]["fanout"] = 10
        if stage["name"] == "synth":
            stage["abc_script"] = _file(tmp_path, "abc.script", "buffer -N 10\n")
        else:
            stage["sdc_snapshot"] = sdc
            stage["fanout_check_limit"] = 10
    bundle["scenes"][0]["all_limits_report"] = _file(
        tmp_path, "all.rpt", _report(fanout=2, fanout_limit=10))
    result = drv.judge(bundle)
    assert result["verdict"] == "NOT_MEASURED"
    assert any("L9 fanout scope" in reason for reason in result["not_measured"])


def test_independent_pre_run_freeze_rejects_plan_value_edit(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    profile = tmp_path / "scene_profile.json"
    profile.write_text(json.dumps({"synthetic": {
        "pvt": {"typ": {"nom_process": 1, "nom_voltage": 5,
                        "nom_temperature": 25}}, "rc_corners": ["nom"]}}))
    monkeypatch.setattr(drv, "_SCENE_PROFILES", profile)
    monkeypatch.setattr(drv, "_installed_image_digest",
                        lambda _: bundle["identity"]["tool_image_digest"])
    bundle["frozen"]["scene_profile_sha256"] = drv._sha(profile)
    _install_test_freeze(bundle, tmp_path, monkeypatch)
    baseline = drv.judge(bundle, project=tmp_path)
    assert not any("threshold source changed" in reason.lower()
                   for reason in baseline["failures"]), baseline
    bundle["frozen"]["values"]["period_ns"] = 25
    bundle["current"]["values"]["period_ns"] = 25
    edited = drv.judge(bundle, project=tmp_path)
    assert edited["verdict"] == "FAIL", edited
    assert any("threshold source changed" in reason.lower()
               for reason in edited["failures"]), edited


def test_missing_committed_threshold_freeze_is_not_measured(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(drv, "_THRESHOLD_FREEZES",
                        tmp_path / "absent.json", raising=False)
    result = drv.judge(bundle, project=tmp_path)
    assert result["verdict"] == "NOT_MEASURED", result
    assert any("threshold freeze record absent" in reason.lower()
               for reason in result["not_measured"]), result


def test_committed_threshold_freeze_digest_is_owner_controlled():
    assert drv._sha(drv._THRESHOLD_FREEZES) == (
        "4a55fc6ef37e484318e391d05930e641656bbe180dc6e54f46baec55105193a0"
    ), "a threshold freeze change needs a RULINGS owner citation"


def test_signed_pre_run_freeze_does_not_require_a_routed_netlist(tmp_path, monkeypatch):
    import subprocess
    bundle = _bundle(tmp_path)
    signers = tmp_path / "allowed_signers"
    signers.write_text("owner ssh-ed25519 test-key\n")
    monkeypatch.setattr(drv, "_OWNER_SIGNERS", signers)
    identity = bundle["identity"]
    frozen = bundle["frozen"]
    current = bundle["current"]
    binding = {"run_id": identity["run_id"], "tree_sha": identity["tree_sha"],
               "sdc_sha256": current["sources"]["signoff_sdc"]["sha256"],
               "l7_sha256": frozen["sources"]["l7"],
               "l9_sha256": frozen["sources"]["l9"],
               "pdk_config_sha256": frozen["sources"]["pdk_config"],
               "spec_version": identity["spec_version"]}
    record = {"type": "threshold_freeze", "identity": binding,
              "thresholds": frozen, "owner_quote": "approve before run",
              "owner_timestamp": "2026-09-27T00:00:00+00:00"}
    folder = tmp_path / "owner_approvals/drv"
    folder.mkdir(parents=True)
    (folder / "freeze.json").write_text(json.dumps(record))
    (folder / "freeze.json.sig").write_text("synthetic signature\n")
    monkeypatch.setattr(drv.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, b"", b""))
    approved, _ = drv._owner_records(tmp_path, identity, current, frozen)
    assert approved == [record]


def test_fresh_opensta_refuses_when_the_ceiling_is_opted_out(tmp_path, monkeypatch):
    # The shared helper returns [] when the operator opts out; the DRV capture
    # must refuse (NOT_MEASURED) rather than start an unbounded container.
    from types import SimpleNamespace
    import drv_signoff_capture as capture
    script = tmp_path / "s.tcl"
    script.write_text("puts ready\n")
    launched = []
    monkeypatch.setenv("VIBEIC_DOCKER_MEMORY", "off")
    monkeypatch.setattr(capture, "_watchdog", SimpleNamespace(
        run_supervised=lambda argv, **kw: launched.append(argv)), raising=False)
    with pytest.raises(RuntimeError, match="memory ceiling unavailable"):
        capture._run_fresh(script, {tmp_path}, image="test:image")
    assert launched == []


def test_image_pdk_anchor_probe_carries_the_ceiling(monkeypatch):
    import subprocess
    import drv_signoff_anchor as anchor
    seen = []
    monkeypatch.setenv("VIBEIC_DOCKER_MEMORY", "3g")
    monkeypatch.setattr(anchor.subprocess, "run", lambda argv, **kw: (
        seen.append(argv) or subprocess.CompletedProcess(argv, 1, "", "no")))
    with pytest.raises(ValueError):
        anchor.image_pdk_anchor("img:1", "pdkA", "libA")
    argv = seen[0]
    assert argv[:2] == ["docker", "run"]
    assert argv[argv.index("--memory") + 1] == "3g"
    assert argv[argv.index("--memory-swap") + 1] == "3g"


def test_fresh_opensta_has_memory_ceiling_and_progress_watchdog(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    import drv_signoff_capture as capture
    script = tmp_path / "measure.tcl"
    script.write_text("puts ready\n")
    seen = []

    def supervised(argv, **kw):
        seen.append((argv, kw))
        Path(kw["log_path"]).write_text("OpenSTA 3.1 aaaaaaaaaa\n")
        return SimpleNamespace(outcome="natural", rc=0, err="")

    monkeypatch.setattr(capture, "_docker_memory",
                        SimpleNamespace(memory_limit=lambda: "1g",
                                        docker_memory_flags=lambda: [
                                            "--memory", "1g", "--memory-swap", "1g"]), raising=False)
    monkeypatch.setattr(capture, "_watchdog",
                        SimpleNamespace(run_supervised=supervised), raising=False)
    monkeypatch.setattr(capture.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0,
                            "OpenSTA 3.1 aaaaaaaaaa\n", ""))
    assert "OpenSTA" in capture._run_fresh(script, {tmp_path}, image="test:image")
    assert len(seen) == 1
    argv, options = seen[0]
    assert argv[argv.index("--memory") + 1] == "1g"
    assert argv[argv.index("--memory-swap") + 1] == "1g"
    assert "--name" in argv and callable(options["cpu_probe"])
    assert callable(options["kill"]) and callable(options["abort_probe"])
    assert options["log_path"] == script.with_suffix(".tool.log")


def test_stalled_opensta_retains_raw_log_and_is_unmeasured(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    import drv_signoff_capture as capture
    script = tmp_path / "measure.tcl"
    script.write_text("puts ready\n")

    def supervised(argv, **kw):
        Path(kw["log_path"]).write_text("OpenSTA partial diagnostic\n")
        return SimpleNamespace(outcome="stalled", rc=125,
                               err="no forward progress")

    monkeypatch.setattr(capture, "_docker_memory",
                        SimpleNamespace(memory_limit=lambda: "1g",
                                        docker_memory_flags=lambda: [
                                            "--memory", "1g", "--memory-swap", "1g"]), raising=False)
    monkeypatch.setattr(capture, "_watchdog",
                        SimpleNamespace(run_supervised=supervised), raising=False)
    monkeypatch.setattr(capture.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0,
                            "OpenSTA 3.1 aaaaaaaaaa\n", ""))
    with pytest.raises(RuntimeError, match="NOT_MEASURED.*stalled"):
        capture._run_fresh(script, {tmp_path}, image="test:image")
    assert "partial diagnostic" in script.with_suffix(".tool.log").read_text()


def test_fresh_opensta_refuses_error_in_middle_of_raw_log(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import drv_signoff_capture as capture
    script = tmp_path / "measure.tcl"
    script.write_text("puts ready\n")
    raw = script.with_suffix(".tool.log")
    header = b"OpenSTA 3.1 aaaaaaaaaa\n"
    filler = b"x" * (capture._READ_LOG_BYTES - 4 - len(header)) + b"\n"
    transcript = (header + filler
                  + b"Error: linked design is incomplete\n"
                  + b"ordinary output\n" * 5000)
    assert transcript.index(b"Error:") == capture._READ_LOG_BYTES - 3
    assert len(transcript) - transcript.index(b"Error:") > 65536

    def supervised(argv, **kw):
        raw.write_bytes(transcript)
        return SimpleNamespace(outcome="natural", rc=0, err="")

    monkeypatch.setattr(capture, "_docker_memory",
                        SimpleNamespace(memory_limit=lambda: "1g",
                                        docker_memory_flags=lambda: [
                                            "--memory", "1g", "--memory-swap", "1g"]), raising=False)
    monkeypatch.setattr(capture, "_watchdog",
                        SimpleNamespace(run_supervised=supervised), raising=False)
    verdict = "MEASURED"
    detail = ""
    try:
        capture._run_fresh(script, {tmp_path}, image="test:image")
    except RuntimeError as exc:
        detail = str(exc)
        verdict = "NOT_MEASURED" if detail.startswith("NOT_MEASURED:") else "OTHER_ERROR"
    assert verdict == "NOT_MEASURED", detail
    assert "tool_error=True" in detail
    assert raw.read_bytes() == transcript


def test_fresh_opensta_refuses_log_at_byte_ceiling(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import drv_signoff_capture as capture
    script = tmp_path / "measure.tcl"
    script.write_text("puts ready\n")
    raw = script.with_suffix(".tool.log")

    def supervised(argv, **kw):
        raw.write_bytes(b"OpenSTA 3.1 aaaaaaaaaa\n".ljust(
            capture._MAX_TOOL_LOG_BYTES, b"x"))
        return SimpleNamespace(outcome="natural", rc=0, err="")

    monkeypatch.setattr(capture, "_docker_memory",
                        SimpleNamespace(memory_limit=lambda: "1g",
                                        docker_memory_flags=lambda: [
                                            "--memory", "1g", "--memory-swap", "1g"]), raising=False)
    monkeypatch.setattr(capture, "_watchdog",
                        SimpleNamespace(run_supervised=supervised), raising=False)
    verdict = "MEASURED"
    detail = ""
    try:
        capture._run_fresh(script, {tmp_path}, image="test:image")
    except RuntimeError as exc:
        detail = str(exc)
        verdict = "NOT_MEASURED" if detail.startswith("NOT_MEASURED:") else "OTHER_ERROR"
    assert verdict == "NOT_MEASURED", detail
    assert "raw log reached byte ceiling" in detail
