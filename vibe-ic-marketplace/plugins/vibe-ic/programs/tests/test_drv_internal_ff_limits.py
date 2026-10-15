"""Keep FF state rows and resolve their declared limit through their cell."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_drv_signoff_judge as fixtures
from test_drv_signoff_judge import _synthetic_scene_profile  # noqa: F401
import drv_signoff_census as census
import drv_signoff_judge as drv


_LIB = '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 nom_process : 1; nom_voltage : 5; nom_temperature : 25;
 default_fanout_load : 1;
 cell (reg) {
  ff (IQ1, IQN1) { clocked_on : "CLK"; next_state : "D"; }
  pin (CLK) { direction : input; max_transition : 4; max_capacitance : 20; }
  pin (D) { direction : input; max_transition : 4; max_capacitance : 20; }
  pin (Q) { direction : output; function : "IQ1";
            max_transition : 4; max_capacitance : 20; }
 }
}'''


def _report(values, limits, *, violators=False):
    text = []
    for kind, title, measured in (("max_slew", "max slew", "slew"),
                                  ("max_fanout", "max fanout", "fanout"),
                                  ("max_capacitance", "max capacitance", "capacitance")):
        text.append(title + "\n\n")
        for pin, value in values[kind].items():
            limit = limits[kind]
            if not violators or value > limit:
                text.append(f"Pin {pin}" + (" ^\n" if kind != "max_fanout" else "\n") +
                    f"{title} {limit:.9f}\n{measured} {value:.9f}\n" +
                    f"Slack {limit - value:.9f} " + ("(VIOLATED)" if value > limit else "(MET)") + "\n\n")
    return "".join(text)


def _complete_scene(tmp_path, monkeypatch, case):
    lib = _LIB
    if case == "nonmodel_state":
        # A comment is not a sequential declaration.
        lib = lib.replace('ff (IQ1, IQN1) { clocked_on : "CLK"; next_state : "D"; }',
                          '/* ff (IQ1, IQN1) { clocked_on : "CLK"; next_state : "D"; } */')
    if case == "missing_physical_model":
        lib = lib.replace("pin (Q)", "pin (OTHER)")
    core = ("foreach _vibeic_core_master [list {reg}] {\n"
            " foreach _vibeic_core_cell [get_lib_cells -quiet */$_vibeic_core_master] {\n"
            "  set_max_transition 3 [$_vibeic_core_cell cell]\n }\n}\n")
    sdc = ("set_max_fanout 4 [current_design]\n"
           "set_max_capacitance 0.2 [current_design]\n" +
           ("set_max_transition 3 [current_design]\n" if case == "global_scope" else core))
    original_file = fixtures._file

    def source_file(root, name, body):
        return original_file(root, name, lib if name == "library.lib" else
                             sdc if name == "signoff_sdc" else body)

    monkeypatch.setattr(fixtures, "_file", source_file)
    bundle = fixtures._bundle(tmp_path)
    scene = bundle["scenes"][0]
    folder = tmp_path / "census"
    iq = "UNKNOWN" if case == "unknown_state" else "IQ1"
    pin_text = ("CLK\tport\tinput\t1\t\t\t\tCLK\tinput\t0\t0\t0\tX\t0\n"
                "D\tport\tinput\t1\t\t\t\tD\tclock\t0.1\t0.1\t1\tX\t0\n"
                "Q\tport\toutput\t0\t\t\t\tQ\tpropagated\t1.7\t1\t0\tX\t0\n"
                "u/CLK\tpin\tinput\t0\tu\treg\tCLK\tD\tclock\t0\t0\t1\tX\t0\n"
                "u/D\tpin\tinput\t0\tu\treg\tD\tD\tclock\t0.1\t0.1\t1\tX\t0\n"
                "u/Q\tpin\toutput\t1\tu\treg\tQ\tQ\tpropagated\t1.7\t1\t0\tX\t0\n" +
                f"u/{iq}\tpin\tinternal\t0\tu\treg\t{iq}\t\tunknown\t0\t0\t0\tX\t0\n"
                "u/IQN1\tpin\tinternal\t0\tu\treg\tIQN1\t\tunknown\t0\t0\t0\tX\t0\n")
    if case == "state_over_limit":
        pin_text = pin_text.replace("IQ1\t\tunknown\t0\t0", "IQ1\t\tunknown\t4\t0")
    elif case == "physical_over_limit":
        pin_text = pin_text.replace("Q\tpropagated\t1.7\t1", "Q\tpropagated\t4\t1")
    net_text = ("Net CLK\n Total capacitance: 0\n Number of drivers: 1\n Number of loads: 0\n"
                " Number of pins: 1\n\nDriver pins\n CLK input port\n\n"
                "Net D\n Total capacitance: 0.005636\n Number of drivers: 1\n Number of loads: 2\n"
                " Number of pins: 3\n\nDriver pins\n D input port\n\nLoad pins\n"
                " u/CLK input (reg)\n u/D input (reg)\n\n"
                "Net Q\n Total capacitance: 0.1\n Number of drivers: 1\n Number of loads: 1\n"
                " Number of pins: 2\n\nDriver pins\n u/Q output (reg)\n\nLoad pins\n Q output port\n\n")
    refs = {"pin_census_report": fixtures._file(folder, "pin_census.tsv", pin_text),
            "net_census_report": fixtures._file(folder, "net_census.rpt", net_text),
            "disabled_edges_report": fixtures._file(folder, "disabled_edges.rpt", "")}
    values = {"max_slew": {"u/CLK": 0, "u/D": .1, "u/Q": 4 if case == "physical_over_limit" else 1.7,
                           "u/" + iq: 4 if case == "state_over_limit" else 0, "u/IQN1": 0},
              "max_fanout": {"CLK": 0, "D": 2, "u/Q": 0},
              "max_capacitance": {"CLK": 0, "D": .005636, "u/Q": .1}}
    if case == "global_scope":
        values["max_slew"].update(CLK=0, D=.1, Q=1.7)
    limits = {"max_slew": 3, "max_capacitance": .2, "max_fanout": 4}
    all_text = _report(values, limits)
    parsed = drv.parse_check_types(all_text, scene=scene["name"], mode=scene["mode"], violators_only=False)
    linked = scene["linked_liberties"]
    if case == "ambiguous_model":
        linked = linked + [{"name": "other", **{k: linked[0][k] for k in ("path", "sha256")}}]
    population = census.derive(folder, linked, parsed,
        signoff_sdc=Path(bundle["current"]["sources"]["signoff_sdc"]["path"]), core_liberty="std")
    bundle["pins"] = population["pins"]
    scene.update(refs, excluded_pins=population["excluded"],
        excluded_pins_report=fixtures._file(folder, "excluded.json", json.dumps(population["excluded"])),
        clock_network_pins=population["clock_network_pins"], driver_pin_census=population["driver_pins"],
        population=population["population"], all_limits_report=fixtures._file(folder, "all.rpt", all_text),
        all_limits_max_count=1000, report=fixtures._file(folder, "violators.rpt", _report(values, limits, violators=True)))
    scene["counters"] = {kind: sum(v > limits[kind] for v in pins.values()) for kind, pins in values.items()}
    scene["counter_report"] = fixtures._file(folder, "counters.rpt", "".join(
        f"DRV_COUNTER {kind} {count}\n" for kind, count in scene["counters"].items()))
    control = {kind: dict(pins) for kind, pins in values.items()}
    control["max_slew"].update(CLK=0, D=.1, Q=values["max_slew"]["u/Q"])
    tight = scene["positive_control_limits"]
    scene["positive_control_all_limits_report"] = fixtures._file(folder, "control_all.rpt", _report(control, tight))
    scene["positive_control_report"] = fixtures._file(folder, "control.rpt", _report(control, tight, violators=True))
    scene["positive_control_counters"] = {kind: sum(v > tight[kind] for v in pins.values()) for kind, pins in control.items()}
    fixtures._install_test_freeze(bundle, tmp_path, monkeypatch)
    fixtures._refresh_scripts(bundle, tmp_path)
    return bundle


@pytest.mark.parametrize("case", ["core_scope", "global_scope", "state_over_limit", "physical_over_limit",
                                  "unknown_state", "nonmodel_state", "missing_physical_model", "ambiguous_model"])
def test_internal_ff_rows_keep_the_declared_limit(tmp_path, monkeypatch, case):
    if case in ("missing_physical_model", "ambiguous_model"):
        with pytest.raises(ValueError, match="lacks unique linked Liberty pin"):
            _complete_scene(tmp_path, monkeypatch, case)
        return
    bundle = _complete_scene(tmp_path, monkeypatch, case)
    result = drv.judge(bundle)
    fixtures._file(tmp_path, "API_OBSERVATION.json", json.dumps({
        "case": case, "pins": bundle["pins"], "scene": bundle["scenes"][0], "judge": result}, indent=2))
    assert len(bundle["pins"]) == 8
    assert bundle["scenes"][0]["population"] == {
        "max_slew": 8 if case == "global_scope" else 5, "max_capacitance": 3, "max_fanout": 3}
    if case in ("unknown_state", "nonmodel_state"):
        assert result["verdict"] == "NOT_MEASURED", result
        assert any("u/" + ("UNKNOWN" if case == "unknown_state" else "IQ1") in why
                   for why in result["not_measured"])
    elif case in ("state_over_limit", "physical_over_limit"):
        assert result["verdict"] == "FAIL", result
        assert any(row["pin"] == ("u/IQ1" if case == "state_over_limit" else "u/Q")
                   for row in result["findings"]), result
    else:
        assert result["verdict"] == "PASS", result
        assert result["not_measured"] == [], result


@pytest.mark.parametrize("case", ["quoted_ff", "description_latch"])
def test_ff_state_membership_requires_a_real_cell_group(tmp_path, monkeypatch, case):
    if case == "quoted_ff":
        model = _LIB.replace("ff (IQ1, IQN1)", 'ff ("IQ1", "IQN1")')
    else:
        model = _LIB.replace('ff (IQ1, IQN1) { clocked_on : "CLK"; next_state : "D"; }',
            'description : "example \\"quoted\\" ff (IQ1, IQN1) { ... } and {";\n'
            '  latch (IQ1, IQN1) { enable : "CLK"; data_in : "D"; }')
    monkeypatch.setitem(globals(), "_LIB", model)
    bundle = _complete_scene(tmp_path, monkeypatch, "core_scope")
    result = drv.judge(bundle)
    states = drv._liberty_limits(model)["ff_states"]["reg"]
    fixtures._file(tmp_path, "LEXICAL_API_OBSERVATION.json", json.dumps({
        "case": case, "ff_states": sorted(states), "pins": bundle["pins"],
        "population": bundle["scenes"][0]["population"], "judge": result}, indent=2))
    assert len(bundle["pins"]) == 8
    assert bundle["scenes"][0]["population"] == {"max_slew": 5, "max_capacitance": 3, "max_fanout": 3}
    if case == "quoted_ff":
        assert states == {"IQ1", "IQN1"}
        assert result["verdict"] == "PASS", result
    else:
        assert states == set()
        assert result["verdict"] == "NOT_MEASURED", result
        assert len(result["not_measured"]) == 2, result
