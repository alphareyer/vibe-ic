"""PSM current changes the real chip-top producer's supply pad population."""
import json
import subprocess
import sys
from pathlib import Path

from test_io_pad_power_domain_plan import _tree, GEN
from _hostpaths import require_repo

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
def _plan(die_side_um=500):
    return {"verdict": "PLANNED", "pair_count": 12,
            "subject_def_sha256": "a" * 64, "die_side_um": die_side_um}


def _produce(project, root, plan):
    path = project / "supply_plan.json"
    path.write_text(json.dumps(plan))
    return subprocess.run(
        [sys.executable, str(GEN), str(project), "--pdk-root", str(root),
         "--pdk", "testpdk", "--power-net", "VDD", "--ground-net", "VSS",
         "--supply-plan", str(path)], capture_output=True, text=True)


def test_psm_count_and_real_chip_top_keep_signal_order(tmp_path):
    project, root = _tree(tmp_path)
    plan = _plan()
    result = _produce(project, root, plan)
    assert result.returncode == 0, result.stdout + result.stderr
    from _ppa.power import pdn_supply_entry_count_plan
    count = pdn_supply_entry_count_plan(
        current_A=0.0284,
        ring_plan={"layers": ["metal4"], "recipe_widths_um": [1.6],
                   "measured_ring_peak_A": {"metal4": 0.01136}},
        pad_entries=[{"layer": "metal2", "drawn_width_um": 9.45,
                      "jmax_A_per_um": 0.00067}],
        jmax_A_per_um={"metal4": 0.00067}, margin=0.1)
    assert count["pair_count"] == 12
    assert count["pad_pair_floor"] == 5
    assert count["ring_pair_floor"] == 12
    assert count["limiting"]["structure"] == "ring"
    rec = json.loads((project / "reports/phase3/io_pad_chip_top.json").read_text())
    pads = rec["derived_answers"]["pad_order_by_side"]
    assert rec["power_pad_plan"]["pair_count"] == 12
    assert sorted(rec["power_pad_plan"]["pairs_by_side"].values()) == [3, 3, 3, 3]
    assert {side: [p for p in names if not p.startswith("u_pad_supply_")]
            for side, names in pads.items()} == {
                "south": ["u_pad_rst"], "east": ["u_pad_clk"],
                "north": ["u_pad_d_1", "u_pad_d_0"], "west": ["u_pad_q"]}
    wrapper = (project / rec["chip_top_verilog"]).read_text()
    assert wrapper.count("test_io__pbridge u_pad_supply_power_") == 12
    assert wrapper.count("test_io__gbridge u_pad_supply_ground_") == 12
    assert wrapper.count("inout VDD") == 1


def test_no_legal_site_is_named_refusal(tmp_path):
    project, root = _tree(tmp_path)
    result = _produce(project, root, _plan(die_side_um=110))
    assert result.returncode == 1
    rec = json.loads((project / "reports/phase3/io_pad_chip_top.json").read_text())
    assert rec["rule"] == "SUPPLY_ENTRY_NO_LEGAL_SITE"
    assert not (project / "phase3/stage3/pnr/chip_top_io.v").exists()


def test_checked_in_psm_sizing_artifact_drives_entry_floor():
    """A measured in-repo current artefact must affect the actual planner."""
    from _ppa.power import pdn_supply_entry_count_plan

    artifact = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "pdn_em_sizing", "arm_C.json")
    sizing = json.loads(artifact.read_text())
    layer = next(iter(sizing["per_layer"]))
    jmax = sizing["per_layer"][layer]["jmax_A_per_um"]
    current = sizing["i_total_A"]
    peak = sizing["max_segment_current_A"]
    recipe_width = 1.6
    entry_width = 9.45
    capacity = recipe_width * jmax * (1 - sizing["margin"])
    result = pdn_supply_entry_count_plan(
        current_A=current,
        ring_plan={"layers": [layer], "recipe_widths_um": [recipe_width],
                   "measured_ring_peak_A": {layer: peak}},
        pad_entries=[{"layer": layer, "drawn_width_um": entry_width,
                      "jmax_A_per_um": jmax}],
        jmax_A_per_um={layer: jmax}, margin=sizing["margin"])
    assert result["current_A"] == current
    assert result["pair_count"] == 7
    assert result["ring_pair_floor"] * capacity > peak
    assert (result["ring_pair_floor"] - 1) * capacity < peak
