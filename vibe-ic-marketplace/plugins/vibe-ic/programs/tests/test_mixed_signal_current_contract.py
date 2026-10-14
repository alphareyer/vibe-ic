"""Source-only current-interface deck and table-format consumer controls."""
import copy
import hashlib
import json

import pytest

import _mixed_signal_current as current
import _mixed_signal_native as consumer
from mixed_signal_m3_run import Refusal
from test_mixed_signal_receiver_noise_contract import _observations, _source_case


def _current_case(project):
    data, noise = _source_case(project)
    driver = project / "input/models/driver.sp"
    driver.write_text("* SOURCE_ONLY model\n.subckt neutral_buffer in out positive negative\nRdrive in out 2\n.ends\n")
    data["analog"].update(drive_transition_ns=0.25, adc_low_v=0.2, adc_high_v=0.8)
    data["canonical"] = {"macro_drive": "drive", "macro_sense": "sense",
        "noise": copy.deepcopy(noise),
        "crosstalk": {"measure_from_ns": 10,
                      "criteria": [{"metric": "peak_crosstalk_v", "units": "V_PEAK", "max": 0.2}]}}
    data["_current"] = {"block": "detector", "post": noise["model"], "ports": ["drive", "sense"], "rails": {},
        "driver_cell": "neutral_buffer", "driver_input": "in", "driver_output": "out",
        "driver_ports": ["in", "out", "positive", "negative"], "driver_sources": ["input/models/driver.sp"],
        "voltages": {"positive": 1, "negative": 0}, "temperature_c": 25,
        "nodes": ["driver:out", "macro:in", "macro:out"],
        "pin_roles": {"driver": "driver:out", "drive": "macro:in", "sense": "macro:out"},
        "rc": {"resistors": [("driver:out", "macro:in", 2)],
               "ground_caps": [("macro:in", 3e-12), ("macro:out", 0)],
               "coupling_caps": [("macro:in", "macro:out", 5e-12)]}}
    row = {"id": noise["id"], "criteria": [{"native_metric": "unknown_samples", "max": 0}],
           "native": {"clock_period_ns": 10, "tran_step_ns": 0.1, "measure_from_ns": 0, "cycles": 3}}
    drive = [{"time_ns": 0, "drive": "0"}, {"time_ns": 10, "drive": "1"},
             {"time_ns": 20, "drive": "0"}]
    return data, row, drive


def test_current_deck_preserves_extracted_rc_population_through_existing_dispatch(tmp_path):
    data, row, drive = _current_case(tmp_path)
    held = copy.deepcopy(data)
    wave = tmp_path / "run_token/case/wave.txt"
    deck = consumer.deck(tmp_path, data, row, drive, wave)

    assert deck == current.deck(tmp_path, data, row, drive, wave)
    assert 'Xlivedriver stimulus live_0 live_driver_positive live_driver_negative neutral_buffer' in deck
    assert 'Xlivereceiver live_1 live_2 detector' in deck
    assert 'Xreferencereceiver reference_1 reference_2 detector' in deck
    assert 'Rlive0 live_0 live_1 2' in deck and 'Rreference0 reference_0 reference_1 2' in deck
    assert sum(line.startswith('Clivec') for line in deck.splitlines()) == 1
    assert not any(line.startswith('Creferencec') for line in deck.splitlines())
    assert not any(line.startswith(('Cliveg1', 'Creferenceg1')) for line in deck.splitlines())
    assert 'wrdata coupling.txt v(live_2) v(reference_2)' in deck
    assert 'noise v(live_2) Vdrive dec 20 100 1000' in deck
    digest = hashlib.sha256((tmp_path / data["_current"]["post"]).read_bytes()).hexdigest()
    assert f'M3NOISE_RUN run_token noise_case {digest}' in deck
    assert data == held
    assert not wave.parent.exists()


@pytest.mark.parametrize("change,rule", [
    ("adc_bounds", "CURRENT_INTERFACE_NOT_SUPPORTED"),
    ("transition", "CURRENT_INTERFACE_NOT_SUPPORTED"),
    ("temperature", "CURRENT_INTERFACE_NOT_SUPPORTED"),
    ("bias", "CURRENT_INTERFACE_NOT_SUPPORTED"),
    ("unbounded_criterion", "CURRENT_INTERFACE_NOT_SUPPORTED"),
])
def test_current_deck_refuses_unsupported_declared_electrical_bounds(tmp_path, change, rule):
    data, row, drive = _current_case(tmp_path)
    if change == "adc_bounds":
        data["analog"]["adc_high_v"] = 2
    elif change == "transition":
        data["analog"]["drive_transition_ns"] = 5
    elif change == "temperature":
        data["_current"]["temperature_c"] = None
    elif change == "bias":
        data["canonical"]["noise"]["dc_drive_v"] = 2
    else:
        del data["canonical"]["crosstalk"]["criteria"][0]["max"]
    with pytest.raises(Refusal) as refused:
        consumer.deck(tmp_path, data, row, drive, tmp_path / "run/case/wave.txt")
    assert refused.value.rule == rule


@pytest.mark.parametrize("population", [[], ["other"], ["detector", "extra"]])
def test_actual_contract_reader_refuses_unresolved_full_population_and_ignores_caller_current(tmp_path, population):
    data, _, _ = _current_case(tmp_path)
    data.update(scope=current.SCOPE)
    data["canonical"].update(block="detector", instance="u_detector")
    path = tmp_path / consumer.CONTRACT
    path.write_text(json.dumps(data))
    declared = tmp_path / "phase1/analog/analog_block_list.json"
    declared.parent.mkdir(parents=True)
    declared.write_text(json.dumps({"blocks": population}))
    held = path.read_bytes()
    with pytest.raises(Refusal) as refused:
        consumer.contract(tmp_path, data["top"])
    assert refused.value.rule == "CURRENT_INTERFACE_NOT_SUPPORTED"
    assert "complete master block population" in str(refused.value)
    assert path.read_bytes() == held
    assert not (tmp_path / "reports").exists()


def _coupling_observations(work, data, row):
    spec = {**data["canonical"]["noise"], "id": row["id"], "top": data["top"], "scope": current.SCOPE}
    _observations(work, spec, log_name="ngspice.log")
    (work / "coupling.txt").write_text(
        "time v(live_2) v(reference_2)\n0 0.2 0\n1e-8 0.15 0.1\n2e-8 0.2 0.1\n3e-8 0.1 0.1\n")


def test_current_measurement_parser_observes_window_and_propagates_stricter_failure(tmp_path):
    data, row, _ = _current_case(tmp_path)
    work = tmp_path / "observations"
    _coupling_observations(work, data, row)
    got = current.measure(tmp_path, data, row, work, "source_run")
    assert got["scope"] == current.SCOPE
    assert got["crosstalk"]["sample_count"] == 3
    assert got["crosstalk"]["criteria"][0]["measured"] == pytest.approx(0.1)
    assert got["noise"]["metrics"]["integrated_output_noise_v_rms"]["measured"] == 3e-8
    assert got["verdict"] == "PASS"
    data["canonical"]["crosstalk"]["criteria"][0]["max"] = 0.09
    assert current.measure(tmp_path, data, row, work, "source_run")["verdict"] == "FAIL"


@pytest.mark.parametrize("change,rule", [
    ("missing_window_end", "FAILED_MEASUREMENT"),
    ("unordered_times", "FAILED_MEASUREMENT"),
    ("empty_window", "CURRENT_INTERFACE_NOT_SUPPORTED"),
])
def test_current_measurement_refuses_incomplete_or_unordered_window(tmp_path, change, rule):
    data, row, _ = _current_case(tmp_path)
    work = tmp_path / "observations"
    _coupling_observations(work, data, row)
    path = work / "coupling.txt"
    if change == "empty_window":
        data["canonical"]["crosstalk"]["measure_from_ns"] = 30
    elif change == "missing_window_end":
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    else:
        path.write_text(path.read_text().replace("2e-8 0.2", "1e-8 0.2"))
    with pytest.raises(Refusal) as refused:
        current.measure(tmp_path, data, row, work, "source_run")
    assert refused.value.rule == rule
