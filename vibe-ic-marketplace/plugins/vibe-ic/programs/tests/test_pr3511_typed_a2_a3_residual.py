"""Public A2 -> A3 controls for the remaining typed PR3511 geometry seam."""
import copy
import json

import pytest

import _plugin_tree  # noqa: F401
import analog_a2_topology_emit as a2
import analog_a3_netlist_emit as a3


SPEC = {"order": 2.0, "osr": 512.0, "enob": 14.0, "vref": 1.0,
        "vdd": 1.2, "vindiff": 1.0, "fclk": 1.0, "fclk_max": 1.0}
MEASURED = {"cap_area_ff_per_um2": 1.5, "cap_perim_ff_per_um": 0.04,
            "rsheet_ohm_per_sq": 260.0, "k_prime_n_ua_per_v2": 328.0,
            "vth_n_extracted_v": 0.197}


def _context(tmp_path):
    models = tmp_path / "models.lib"
    models.write_text(
        "* Valid range for model: L = (0.1 - 5)um\n"
        "* W = (0.2 - 10)um\n"
        ".lib typ\n"
        ".subckt neutral_n d g s b w=1u l=1u ng=1\n"
        "Ncore d g s b native_n w=w l=l nf='ng'\n"
        ".ends neutral_n\n"
        ".subckt neutral_p d g s b w=1u l=1u ng=1\n"
        "Ncore d g s b native_p w=w l=l nf='ng'\n"
        ".ends neutral_p\n"
        ".subckt neutral_r p n b w=1u l=1u\n.ends neutral_r\n"
        ".subckt neutral_c p n w=1u l=1u\n.ends neutral_c\n"
        ".endl typ\n")
    return {
        "status": "OK", "family": "neutral", "registry_family": "neutral",
        "model_lib": str(models), "typ_section": "typ",
        "role_models": {"nmos": "neutral_n", "pmos": "neutral_p",
                        "res": "neutral_r", "cap": "neutral_c"},
        "device_terminals": {"nmos": 4, "pmos": 4, "res": 3, "cap": 2},
        "geometry_units": {role: "metric"
                           for role in ("nmos", "pmos", "res", "cap")},
        "unresolved_roles": [],
    }


def _sizing(enabled=True):
    return {
        "knobs": {"ota_input_drain_cascodes": enabled},
        "device_parameters": {
            **{name: {"w": 18, "ng": 2}
               for name in ("mn_in1", "mn_ref1", "mn_in2", "mn_ref2")},
            **{name: {"w": 24, "l": 0.5, "ng": 3}
               for name in ("mn_cas_in1", "mn_cas_ref1",
                            "mn_cas_in2", "mn_cas_ref2")},
            "r_cas_top": {"w": 0.8, "l": 120},
            "r_cas_bottom": {"w": 0.8, "l": 40},
        },
    }


def _write_inputs(project, sizing):
    bdir = project / "phase3/analog/converter"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "sizing_inputs.json").write_text(json.dumps(sizing))
    (bdir / "spec.json").write_text(json.dumps({
        "specs": [{"name": name, "target": value}
                  for name, value in SPEC.items()]}))
    return bdir


def _build(project, sizing):
    _write_inputs(project, sizing)
    return a2.build_ir(
        "converter", "delta_sigma", {"name": "converter"},
        a2.LIBRARY["delta_sigma"], SPEC, None, project, None, {},
        measured_params=MEASURED)


def test_public_a2_to_a3_emits_typed_cascode_graph_and_source_bound_fingers(
        tmp_path):
    """Portable parent control: uses public A2/A3 APIs, not new helpers."""
    context = _context(tmp_path)
    try:
        ir = _build(tmp_path, _sizing())
        problems = a3._validate_ir(ir, context, tmp_path)
        text = a3.render_netlist(
            ir, context, [], a3._resolve_params(ir, SPEC)[0],
            deck_dir=tmp_path / "phase3/analog/converter")
        actual = {
            "status": "EMITTED" if not problems else "REFUSED",
            "problems": problems,
            "cascode": next(d for d in ir["devices"]
                            if d["name"] == "mn_cas_in1"),
            "has_bias_top": "xr_cas_top vdd ncas_bias vss neutral_r" in text,
            "has_native_fingers": (
                "xmn_cas_in1 nd1_1 ncas_bias ncas_mid_in1 vss neutral_n "
                "w=24u l=0.5u ng=3") in text,
        }
    except Exception as exc:  # parent records an actual value, not collection failure
        actual = {"status": type(exc).__name__, "detail": str(exc)}
    assert actual["status"] == "EMITTED", actual
    assert actual["cascode"]["nets"] == [
        "nd1_1", "ncas_bias", "ncas_mid_in1", "vss"]
    assert actual["has_bias_top"] is True
    assert actual["has_native_fingers"] is True


def test_selected_model_source_derives_fingers_without_a_design_literal(tmp_path):
    """Portable parent control for the existing A3 render consumer."""
    context = _context(tmp_path)
    ir = {"block": "neutral", "block_type": "ota", "topology": "stage",
          "ports": ["out", "in", "vss"],
          "devices": [{"name": "signal_device", "role": "nmos",
                       "nets": ["out", "in", "vss", "vss"],
                       "w": 16.0, "l": 1.0}]}
    text = a3.render_netlist(ir, context, [], {})
    assert "xsignal_device out in vss vss neutral_n w=16u l=1u ng=2" in text


@pytest.mark.parametrize("mutation", [
    "partial", "wrong_cascode_gate", "wrong_original_drain",
    "wrong_resistor_end", "wrong_resistor_size", "stale_record",
    "disabled_record", "source_enable_mismatch",
])
def test_a3_refuses_invalid_actual_cascode_electrical_graph(tmp_path, mutation):
    context = _context(tmp_path)
    sizing = _sizing()
    ir = _build(tmp_path, sizing)
    devices = {device["name"]: device for device in ir["devices"]}
    if mutation == "partial":
        ir["devices"].remove(devices["mn_cas_in1"])
    elif mutation == "wrong_cascode_gate":
        devices["mn_cas_in1"]["nets"][1] = "vcm"
    elif mutation == "wrong_original_drain":
        devices["mn_in1"]["nets"][0] = "nd1_1"
    elif mutation == "wrong_resistor_end":
        devices["r_cas_bottom"]["nets"][1] = "vss"
    elif mutation == "wrong_resistor_size":
        devices["r_cas_top"]["w"] = 0.9
    elif mutation == "stale_record":
        ir["input_drain_cascodes"]["branches"][0]["original_drain"] = "vss"
    elif mutation == "disabled_record":
        ir["knobs"]["ota_input_drain_cascodes"] = False
        sizing["knobs"]["ota_input_drain_cascodes"] = False
    else:
        sizing["knobs"]["ota_input_drain_cascodes"] = False
    _write_inputs(tmp_path, sizing)
    problems = a3._validate_ir(ir, context, tmp_path)
    assert any("INPUT_DRAIN_CASCODE_INVALID" in problem
               for problem in problems), problems


def test_disabled_option_preserves_legacy_device_graph(tmp_path):
    disabled = {"knobs": {"ota_input_drain_cascodes": False},
                "device_parameters": {}}
    new = _build(tmp_path / "disabled", disabled)
    old_library = copy.deepcopy(a2.LIBRARY["delta_sigma"])
    old_library.pop("input_drain_cascodes")
    old_library["spec_knobs"] = []
    for group in old_library["stage"]:
        group.pop("input_drain_cascodes", None)
    old_project = tmp_path / "legacy"
    _write_inputs(old_project, {"knobs": {}, "device_parameters": {}})
    old = a2.build_ir("converter", "delta_sigma", {"name": "converter"},
                      old_library, SPEC, None, old_project, None, {},
                      measured_params=MEASURED)
    assert new["devices"] == old["devices"]
    assert new["internal_nets"] == old["internal_nets"]
    assert "input_drain_cascodes" not in new
