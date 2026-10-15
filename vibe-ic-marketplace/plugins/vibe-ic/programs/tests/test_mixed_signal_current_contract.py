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


from pathlib import Path

# Reuse the public one-interface ancestor fixture without selecting its native
# measurements. The STA receipt below binds SOURCE_CONTROL bytes, not a tool run.
from test_m3_native_supported import project, current_interface_project
from librelane_contract import validate_step_receipt as _validate_retained_step


def _namespace_case(project, monkeypatch, mode, record_property):
    import librelane_contract as lc
    sta = project / "phase3/librelane/sta"
    config_path = sta / "config.json"
    config = json.loads(config_path.read_text())
    pdk_root = project / "input/pdk"
    fields = {"verilog": "CELL_VERILOG_MODELS", "spice": "CELL_SPICE_MODELS"}
    if mode == "relative":
        for field in fields.values():
            config[field] = [str(Path(p).relative_to(project)) for p in config[field]]
    else:
        for kind, field in fields.items():
            if mode in ("guest_all", "guest_" + kind):
                config[field] = ["/pdk/" + str(Path(p).relative_to(pdk_root)) for p in config[field]]
    config_path.write_text(json.dumps(config) + "\n")
    log = sta / "tt/sta.log"
    if mode in ("guest_all", "guest_liberty"):
        log.write_text(log.read_text().replace(str(pdk_root), "/pdk"))
    root_record = {"cli_pdk_root": "/pdk", "mounts_under_it": [[str(pdk_root), "/pdk"]],
                   "stated_by": "run_chain(pdk_root=...)"}
    (sta / "pdk_root.json").write_text(json.dumps(root_record) + "\n")
    absolute_config = {key: [str(project / p) if not Path(p).is_absolute() else p for p in value]
                       for key, value in config.items() if key in fields.values()}
    fingerprint = {"step": "OpenROAD.STAPostPNR", "config": lc.digest(config_path),
                   "config_files": lc.config_file_hashes(absolute_config, [(pdk_root, "/pdk")])}
    # The library is selected by the actual STA log, not the config model list.
    library = pdk_root / "liberty/current_tt.lib"
    fingerprint["liberty_files"] = {"/pdk/liberty/current_tt.lib": lc.digest(library)}
    (sta / "input_fingerprint.json").write_text(json.dumps(fingerprint) + "\n")
    (sta / "invocation.log").write_text("SOURCE_CONTROL: no native STA execution credit\n")
    names = ("state_out.json", "config.json", "input_fingerprint.json", "pdk_root.json", "invocation.log", "tt/sta.log")
    (sta / "vibeic_receipt.json").write_text(json.dumps({"input": fingerprint,
        "sha256": {name: lc.digest(sta / name) for name in names}}) + "\n")
    ancestor_validator = lc.validate_step_receipt
    monkeypatch.setattr(lc, "validate_step_receipt", lambda folder, step:
                        _validate_retained_step(folder, step) if step == "OpenROAD.STAPostPNR"
                        else ancestor_validator(folder, step))
    return sta


def _namespace_inputs(project, record_property):
    paths = [p for p in project.rglob("*") if p.is_file()]
    record_property("public_input_sha256", hashlib.sha256(json.dumps(
        {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
        sort_keys=True).encode()).hexdigest())
    record_property("scope", "SOURCE_CONTROL ancestors and normal M3 input consumer; no native execution")


@pytest.mark.parametrize("mode", ["host", "relative", "guest_verilog", "guest_spice", "guest_liberty", "guest_all"])
def test_normal_current_interface_consumes_retained_pdk_namespace(current_interface_project, monkeypatch, record_property, mode):
    project = current_interface_project
    _namespace_case(project, monkeypatch, mode, record_property)
    _namespace_inputs(project, record_property)
    held = (project / consumer.CONTRACT).read_bytes()
    data = consumer.contract(project, "bridge_top")
    paths = consumer.input_paths(project, "bridge_top")
    assert data["scope"] == current.SCOPE
    assert data["digital"]["extra_sources"] == ["input/pdk/neutral/BUF.v", "phase3/analog/hardmacro/receiver/receiver.v"]
    assert data["_current"]["driver_sources"] == ["input/pdk/neutral/cells.sp"]
    assert {"input/pdk/neutral/BUF.v", "input/pdk/neutral/cells.sp", "input/pdk/liberty/current_tt.lib"} <= paths
    assert all((project / path).is_file() and not Path(path).is_absolute() for path in paths)
    assert (project / consumer.CONTRACT).read_bytes() == held
    assert not (project / "reports/analog/mixed_signal/native_runs").exists()


@pytest.mark.parametrize("change", ["missing_receipt", "wrong_producer", "changed_mount", "changed_model"])
def test_normal_current_interface_refuses_unbound_pdk_namespace(current_interface_project, monkeypatch, record_property, change):
    project = current_interface_project
    sta = _namespace_case(project, monkeypatch, "guest_all", record_property)
    if change == "missing_receipt":
        (sta / "vibeic_receipt.json").unlink()
    elif change == "wrong_producer":
        receipt = json.loads((sta / "vibeic_receipt.json").read_text())
        receipt["input"]["step"] = "OpenROAD.STAPrePNR"
        (sta / "vibeic_receipt.json").write_text(json.dumps(receipt))
    elif change == "changed_mount":
        root = json.loads((sta / "pdk_root.json").read_text())
        root["mounts_under_it"][0][0] = str(project / "input/other_pdk")
        (sta / "pdk_root.json").write_text(json.dumps(root))
    else:
        with (project / "input/pdk/neutral/BUF.v").open("a") as stream:
            stream.write("// changed after the retained producer\n")
    _namespace_inputs(project, record_property)
    with pytest.raises(Refusal) as refused:
        consumer.input_paths(project, "bridge_top")
    assert refused.value.rule == "CURRENT_INTERFACE_NOT_SUPPORTED"

