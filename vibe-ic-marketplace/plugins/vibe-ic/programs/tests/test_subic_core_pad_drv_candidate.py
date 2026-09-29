"""Post-route DRV candidates must repair pad-input slew on routed views."""
import importlib
import sys
from pathlib import Path

import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
prr = importlib.import_module("librelane_postroute_repair")


def test_drv_controller_requests_routed_slew_repair_without_setup_moves():
    registry = yaml.safe_load((PROGRAMS.parent / "config/ppa_actuator_registry.yaml").read_text())
    plan = registry["controllers"]["postroute.repair_drv"]["plan"][0]
    assert plan["drv_only"] is True
    assert 0 < plan["slew_margin_pct"] <= 50
    assert prr.PARAM_VARS["drv_only"] == "VIBEIC_PRR_DRV_ONLY"
    assert prr.PARAM_VARS["slew_margin_pct"] == "VIBEIC_PRR_SLEW_MARGIN_PCT"


def test_pad_pin_slew_repair_sizes_the_actual_pad_driver_from_liberty():
    script = (PROGRAMS / "librelane_plugins/librelane_plugin_vibeic/"
              "postroute_repair.tcl").read_text()
    assert "report_check_types -max_slew -violators" in script
    assert "[[$pad getMaster] getType]" in script
    assert "[get_property $old is_buffer]" in script
    assert "drive_resistance_max_rise" in script
    assert "drive_resistance_max_fall" in script
    assert "replace_cell $name [get_property $best name]" in script
    assert "vic_annotate pad_eco_$pass" in script
    assert "vic_eco_route ::vic_pad_dirty pad_eco_$pass" in script
