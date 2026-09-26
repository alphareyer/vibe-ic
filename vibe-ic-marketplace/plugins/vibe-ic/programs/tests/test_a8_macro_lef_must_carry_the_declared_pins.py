"""The A8 gate of record certifies a macro LEF only when it carries the
declared pins (T94, A8 harvest #1).

MEASURED on vibeic-eda 0.3.77 / u_hawaii_adc/ldo: `analog_a8_hardmacro_emit`
wrote a LEF with SIZE and OBS and ZERO `PIN` statements while printing
"4 pin(s)" (a count of the topology's ports), and
`analog_a8_hardmacro_gen_check` PASSED it — the only reader that noticed,
`analog_hardmacro_pinname_consistency_check`, is advisory in the runner.
LibreLane Magic.WriteLEF on the same GDS writes the same pin-less LEF: A5's
port labels sit on a cut layer. Digital PnR cannot connect to a macro with no
pins, so the gate that certifies the package now asks the LEF.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path

PROG = Path(_plugin_tree.plugin_path("programs")) / \
    "analog_a8_hardmacro_gen_check.py"

HEAD = ("VERSION 5.7 ;\n  DIVIDERCHAR \"/\" ;\n  BUSBITCHARS \"[]\" ;\n"
        "MACRO blk\n  CLASS BLOCK ;\n  FOREIGN blk ;\n  ORIGIN 0 0 ;\n"
        "  SIZE 510.920 BY 247.660 ;\n")
OBS = ("  OBS\n      LAYER Metal2 ;\n        RECT 4.000 3.840 514.070 250.950 ;\n"
       "      LAYER Metal3 ;\n        RECT 7.760 3.840 514.070 250.950 ;\n"
       "  END\nEND blk\nEND LIBRARY\n")


def _pin(name, use):
    return (f"  PIN {name}\n    DIRECTION INOUT ;\n    USE {use} ;\n    PORT\n"
            f"      LAYER Metal2 ;\n        RECT 1 1 2 2 ;\n    END\n"
            f"  END {name}\n")


def _project(tmp_path, lef_body):
    project = tmp_path / "proj"
    a = project / "phase3" / "analog"
    (a / "blk").mkdir(parents=True)
    (a / "analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    (a / "blk" / "topology.json").write_text(json.dumps(
        {"ports": ["vin", "vss", "vref", "vout"],
         "rails": {"vdd": "vin", "vss": "vss"}}))
    (a / "blk" / "corner_results.json").write_text(json.dumps(
        {"design_content": "structure_and_geometry"}))
    h = a / "hardmacro" / "blk"
    h.mkdir(parents=True)
    (h / "blk.lef").write_text(HEAD + lef_body + OBS)
    (h / "blk.lib").write_text("library (blk) {\n" + "  /* interface */\n" * 12
                               + "  cell (blk) { area : 1.0; }\n}\n")
    (h / "blk.v").write_text("module blk (\n    inout vin,\n    inout vss,\n"
                             "    inout vref,\n    inout vout\n);\n"
                             "// analog macro interface: the supplies are the block's own\n"
                             "// declared rails; analog ports carry no direction\n"
                             "endmodule\n")
    return project


def _gate(project):
    cp = subprocess.run([sys.executable, str(PROG), str(project),
                         "--block", "blk"], capture_output=True, text=True)
    cp.stdout = (cp.stdout or "") + (cp.stderr or "")
    return cp


def test_a_lef_with_no_pins_is_not_a_macro(tmp_path):
    cp = _gate(_project(tmp_path, ""))
    assert cp.returncode == 1, cp.stdout
    assert "A8_HARDMACRO_LEF_NO_PINS" in cp.stdout


def test_every_declared_port_must_be_a_pin(tmp_path):
    body = _pin("vin", "POWER") + _pin("vss", "GROUND") + _pin("vout", "SIGNAL")
    cp = _gate(_project(tmp_path, body))
    assert cp.returncode == 1 and "A8_HARDMACRO_LEF_PIN_MISSING" in cp.stdout
    assert "vref" in cp.stdout


def test_a_rail_pin_must_be_power_or_ground(tmp_path):
    body = (_pin("vin", "SIGNAL") + _pin("vss", "GROUND") + _pin("vref", "SIGNAL")
            + _pin("vout", "SIGNAL"))
    cp = _gate(_project(tmp_path, body))
    assert cp.returncode == 1 and "A8_HARDMACRO_RAIL_NOT_PG" in cp.stdout
    assert "'vin'" in cp.stdout


def test_a_macro_with_every_declared_pin_passes(tmp_path):
    body = (_pin("vin", "POWER") + _pin("vss", "GROUND") + _pin("vref", "SIGNAL")
            + _pin("vout", "SIGNAL"))
    cp = _gate(_project(tmp_path, body))
    assert cp.returncode == 0, cp.stdout
