"""The first PnR pass can reuse only a proven EM floor."""
import hashlib
import json
import re

import pytest
import phase3_one_shot_runner as R
import _eda_pin
from test_issue2108_pnr_approach_log_survives_the_next_approach import _build_project, _pdk


def _def(width):
    return ("VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\n"
            "SPECIALNETS 1 ;\n- VDD + USE POWER\n"
            f" + ROUTED met4 {int(width * 1000)} + SHAPE STRIPE "
            "( 1000 1000 ) ( 1000 9000 ) ;\n"
            "END SPECIALNETS\nEND DESIGN\n")


def _setup(tmp_path, monkeypatch, prior_width=4.0):
    project = _build_project(tmp_path, "widget")
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    for name in ("routed.def", "widget.def"):
        (pnr / name).write_text(_def(prior_width))
    digest = hashlib.sha256((pnr / "routed.def").read_bytes()).hexdigest()
    identity = {"sha256": "same-input", "tool_image": "sha256:fixture",
                "producer": {"plugin_version": "test", "recipe_sha256": "recipe"}}
    monkeypatch.setattr(R, "_pdn_em_input_identity", lambda *a: identity,
                        raising=False)
    floor = {"per_layer": {"met4": {"orig_name": "met4", "w_em_um": 3.95}},
             "margin": 0.1, "sizing_basis": "measured_max_segment", "i_drive_A": 0.002}
    (pnr / R._PDN_EM_RESIZE_SENTINEL).write_text(json.dumps({
        "reason": "pdn_em_first_pass_resize", "spent_on_def": digest,
        "short": [{"layer": "met4", "drawn_um": 1.6, "w_em_um": 3.95}],
        "input_identity": identity, "measurement_subject_sha256": digest,
        "floor": floor}))
    (pnr / ".pdn_em_layout_identity.json").write_text(json.dumps({
        "input_identity": identity, "def_sha256": digest}))
    calls = []
    def openroad(_container, command, timeout=None, **_kw):
        if "openroad -no_init" not in command:
            return 0, "", ""
        deck = (pnr / "pnr.tcl").read_text()
        match = re.search(r"add_pdn_stripe[^\n]*-layer met4 -width ([0-9.]+)", deck)
        width = float(match.group(1)) if match else 1.6
        calls.append(width)
        for name in ("widget.def", "routed.def"):
            (pnr / name).write_text(_def(width))
        (pnr / "openroad.log").write_text(
            "[INFO DRT-0199] Number of violations = 0.\n"
            "PG_NET_OWNERSHIP_AUDIT: total=600 no_net=0 masters=\n")
        return 0, (pnr / "openroad.log").read_text(), ""
    monkeypatch.setattr(R, "_docker_exec", openroad)
    return project, pnr, identity, calls


def test_effective_matching_floor_drawn_on_first_pass(tmp_path, monkeypatch):
    project, _, _, calls = _setup(tmp_path, monkeypatch)
    R.step_pnr(project, "widget", _pdk(), "fixture", "200x200", 0.30)
    assert calls and calls[0] >= 3.95


@pytest.mark.parametrize("change", ["netlist", "sdc", "floorplan", "pdk", "plugin", "tool"])
def test_each_identity_change_refuses_prior_floor(tmp_path, monkeypatch, change):
    project, _, identity, calls = _setup(tmp_path, monkeypatch)
    changed = dict(identity, sha256=change + "-changed")
    monkeypatch.setattr(R, "_pdn_em_input_identity", lambda *a: changed,
                        raising=False)
    R.step_pnr(project, "widget", _pdk(), "fixture", "200x200", 0.30)
    assert calls and calls[0] < 3.95


def test_ineffective_floor_is_redrawn_narrow(tmp_path, monkeypatch):
    project, _, _, calls = _setup(tmp_path, monkeypatch, prior_width=1.6)
    R.step_pnr(project, "widget", _pdk(), "fixture", "200x200", 0.30)
    assert calls and calls[0] < 3.95


def test_ineffective_floor_keeps_bounded_resize_path(tmp_path, monkeypatch):
    project, pnr, _, calls = _setup(tmp_path, monkeypatch, prior_width=1.6)
    pdk = _pdk()
    first = R.step_pnr(project, "widget", pdk, "fixture", "200x200", 0.30)
    assert first.status == "PASS"
    def emit(_project, _top, _pdk, _container, _ir, em, _notes):
        em.parent.mkdir(parents=True, exist_ok=True)
        em.write_text("Total power 0.02\nSupply voltage 5.0\n")
        (em.parent / "em.json").write_text(json.dumps({
            "subject_def_sha256": R._ppa_power._pdn_em_subject_digest(project),
            "max_segment_current_A": 0.002}))
        return True, True
    monkeypatch.setattr(R, "_emit_ir_em_reports", emit)
    floor = json.loads((pnr / R._PDN_EM_RESIZE_SENTINEL).read_text())["floor"]
    monkeypatch.setattr(R, "_pdn_em_width_floor", lambda *a, **k: floor)
    decision = R._pdn_em_first_pass_resize(project, "widget", pdk, "fixture")
    assert decision is not None and decision["short"][0]["layer"] == "met4"
    assert R._record_pdn_em_resize_spend(project, decision)
    spent = json.loads((pnr / R._PDN_EM_RESIZE_SENTINEL).read_text())
    assert spent["measurement_subject_sha256"] == spent["spent_on_def"]
    assert spent["input_identity"]
    second = R.step_pnr(project, "widget", pdk, "fixture", "200x200", 0.30,
                        em_floor_for_resize=decision["floor"])
    assert second.status == "PASS"
    assert len(calls) == 2 and calls[0] < 3.95 <= calls[1]
    third = R.step_pnr(project, "widget", pdk, "fixture", "200x200", 0.30)
    assert third.status == "PASS"
    assert len(calls) == 3 and calls[2] >= 3.95


def test_identical_inputs_make_identical_first_pass_command(tmp_path, monkeypatch):
    project, pnr, _, calls = _setup(tmp_path, monkeypatch)
    R.step_pnr(project, "widget", _pdk(), "fixture", "200x200", 0.30)
    first = (pnr / "pnr.tcl").read_text()
    R.step_pnr(project, "widget", _pdk(), "fixture", "200x200", 0.30)
    assert (pnr / "pnr.tcl").read_text() == first
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("change", ["netlist", "sdc", "floorplan", "pdk",
                                    "plugin", "tool", "die"])
def test_identity_digest_tracks_each_consumed_field(tmp_path, monkeypatch, change):
    project = _build_project(tmp_path, "widget")
    pdk = _pdk()
    for key in ("tech_lef", "cell_lef", "liberty"):
        path = tmp_path / key
        path.write_text(key + " initial\n")
        setattr(pdk, key, str(path))
    sdc = project / "input" / "constraints" / "design.sdc"
    sdc.parent.mkdir(parents=True)
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    floorplan = project / "input" / "floorplan.json"
    floorplan.write_text('{"die": 200}\n')
    image = ["sha256:tool-a"]
    producer = [{"plugin_version": "v1", "recipe_sha256": "recipe-a",
                 "source_sha": "source-a"}]
    monkeypatch.setattr(_eda_pin, "container_image_digest",
                        lambda _: (image[0], ""))
    monkeypatch.setattr(R, "_producer_identity_now", lambda: producer[0])
    def identity(die="200x200"):
        result = R._pdn_em_input_identity(project, "widget", pdk, "fixture",
                                          die, 0.30, None)
        assert result is not None
        return result["sha256"]
    before = identity()
    if change == "netlist":
        (R._pl.synth_dir(project) / "widget_synth.v").write_text("module widget; endmodule\n")
    elif change == "sdc":
        sdc.write_text("create_clock -period 11 [get_ports clk]\n")
    elif change == "floorplan":
        floorplan.write_text('{"die": 201}\n')
    elif change == "pdk":
        (tmp_path / "tech_lef").write_text("tech_lef changed\n")
    elif change == "plugin":
        producer[0] = dict(producer[0], recipe_sha256="recipe-b")
    elif change == "tool":
        image[0] = "sha256:tool-b"
    assert before != identity("201x201" if change == "die" else "200x200")
