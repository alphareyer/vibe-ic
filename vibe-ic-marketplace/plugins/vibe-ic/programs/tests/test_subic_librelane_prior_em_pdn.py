"""A measured prior route may size the next LibreLane PDN, with provenance."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

from _ppa.pdn_em_presweep import librelane_pdn_config


def _fixture(tmp_path: Path) -> Path:
    p = tmp_path
    pnr = p / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    route = b"VERSION 5.8 ;\nEND DESIGN\n"
    (pnr / "routed.def").write_bytes(route)
    (pnr / "active_via_legalized.tlef").write_text(
        "MANUFACTURINGGRID 0.005 ;\n"
        + "".join(f"LAYER Metal{i}\n TYPE ROUTING ;\n DIRECTION "
                  f"{'VERTICAL' if i == 4 else 'HORIZONTAL'} ;\n"
                  " WIDTH 0.28 ;\n SPACING 0.3 ;\n THICKNESS 0.54 ;\n"
                  " DCCURRENTDENSITY AVERAGE 0.67 ;\n"
                  f"END Metal{i}\n" for i in (3, 4, 5)))
    netlist = p / "phase2/stage2/synth/unit_synth.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module unit(); endmodule\n")
    sha = hashlib.sha256(netlist.read_bytes()).hexdigest()
    (pnr / "step_identity.json").write_text(json.dumps({"pnr": {"evidence": {
        "inputs": [f"netlist=phase2/stage2/synth/unit_synth.v@{sha[:12]}"]}}}))
    cfg = p / "phase3/librelane/15-floorplan/11-openroad-generatepdn/config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"PDN_VERTICAL_LAYER": "Metal4",
                               "PDN_HORIZONTAL_LAYER": "Metal5",
                               "PDN_VWIDTH": 1.6, "PDN_HWIDTH": 1.6,
                               "PDN_VPITCH": 153.6, "PDN_HPITCH": 153.18}))
    out = p / "reports/phase3"
    out.mkdir(parents=True)
    (out / "em.json").write_text(json.dumps({
        "verdict": "MEASURED",
        "subject_def_sha256": hashlib.sha256(route).hexdigest(),
        "max_segment_current_A": 0.000388,
        "power_basis": {"calibration": "CALIBRATED"}}))
    (out / "em_current_authority.json").write_text(json.dumps({
        "jmax_screen": {"verdict": "FAIL", "summary": {"per_layer": {
            "Metal3": {"max_utilization": 1.852},
            "Metal4": {"max_utilization": 0.32},
            "Metal5": {"max_utilization": 0.01}}}}}))
    return p


def test_prior_measured_bottleneck_reduces_its_feeding_strap_pitch(tmp_path):
    p = _fixture(tmp_path)
    cfg, source = librelane_pdn_config(p)
    assert cfg["PDN_VPITCH"] == 51.2
    assert cfg["PDN_VWIDTH"] >= 1.6
    assert cfg["PDN_HPITCH"] == 153.18
    assert source == "reports/phase3/pdn_em_prior_config.json"
    record = json.loads((p / source).read_text())
    assert record["verdict"] == "CANDIDATE_REQUIRES_NEW_ROUTE_EM"
    assert record["derivation"]["Metal4"]["parallel_feed_factor"] == 3
    # Re-emission must preserve the decision byte for byte.
    before = (p / source).read_bytes()
    assert librelane_pdn_config(p) == (cfg, source)
    assert (p / source).read_bytes() == before


def test_stale_def_or_changed_netlist_cannot_supply_the_current(tmp_path):
    p = _fixture(tmp_path)
    (p / "phase3/stage3/pnr/routed.def").write_text("changed route")
    assert librelane_pdn_config(p) == ({}, None)
    assert not (p / "reports/phase3/pdn_em_prior_config.json").exists()
    p = _fixture(tmp_path / "other")
    (p / "phase2/stage2/synth/unit_synth.v").write_text("changed netlist")
    assert librelane_pdn_config(p) == ({}, None)


def test_measured_current_forces_wider_straps_and_stricter_jmax(tmp_path):
    p = _fixture(tmp_path)
    em_path = p / "reports/phase3/em.json"
    em = json.loads(em_path.read_text())
    em["max_segment_current_A"] = 0.0012
    em_path.write_text(json.dumps(em))
    # The authority report must follow the new measurement, as it does in a
    # real post-route analysis.  The numeric value, rather than the presence
    # of a new field or module, is this test's control.
    auth_path = p / "reports/phase3/em_current_authority.json"
    auth_path.write_text(auth_path.read_text())
    cfg, source = librelane_pdn_config(p)
    width = cfg.get("PDN_VWIDTH", 1.6)
    assert width > 1.6
    assert source == "reports/phase3/pdn_em_prior_config.json"
    assert cfg["PDN_VPITCH"] < 153.6
    record = json.loads((p / source).read_text())
    metal = record["derivation"]["Metal4"]
    assert 2 * em["max_segment_current_A"] / width < (
        metal["jmax_A_per_um"] * (1 - metal["margin"]))
    assert metal["new_pitch_um"] == cfg["PDN_VPITCH"]
