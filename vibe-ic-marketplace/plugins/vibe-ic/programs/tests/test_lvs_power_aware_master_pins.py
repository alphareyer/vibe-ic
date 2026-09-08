from pathlib import Path
import pytest
import lvs_power_aware_netlist_emit as p


def lef(tmp_path):
    f = tmp_path / "cells.lef"
    f.write_text("\n".join(
        "MACRO " + name + "\n" + "\n".join(
            " PIN " + pin + "\n  USE " + use + " ;\n END " + pin
            for pin, use in pins) + "\nEND " + name
        for name, pins in [
            ("gf180mcu_fd_sc_mcu7t5v0__physical", [("VDD", "POWER"), ("VSS", "GROUND")]),
            ("gf180mcu_fd_sc_mcu7t5v0__logic", [("VDD", "POWER"), ("VSS", "GROUND"),
                                               ("VNW", "POWER"), ("VPW", "GROUND"), ("A", "SIGNAL")])]))
    return f


def test_native_master_interface_limits_only_injected_pins(tmp_path):
    text = ("module dut(a);\ninput a;\n"
            "gf180mcu_fd_sc_mcu7t5v0__physical tap ();\n"
            "gf180mcu_fd_sc_mcu7t5v0__logic logic0 (.A(a));\nendmodule\n")
    out, stats = p.emit_power_aware_netlist(text, "gf180mcuD", cell_lef=lef(tmp_path),
                                          tie_wells_to_rails=True)
    tap = next(x for x in out.splitlines() if " tap " in x)
    logic = next(x for x in out.splitlines() if " logic0 " in x)
    assert ".VDD(VDD)" in tap and ".VSS(VSS)" in tap
    assert ".VNW(" not in tap and ".VPW(" not in tap
    assert ".VNW(VDD)" in logic and ".VPW(VSS)" in logic and ".A(a)" in logic
    assert stats["instances_patched"] == 2
    again, _ = p.emit_power_aware_netlist(out, "gf180mcuD", cell_lef=lef(tmp_path),
                                         tie_wells_to_rails=True)
    assert again == out


def test_conflicting_master_views_refuse(tmp_path):
    a = lef(tmp_path)
    b = tmp_path / "conflict.lef"
    b.write_text("MACRO gf180mcu_fd_sc_mcu7t5v0__physical\n PIN BAD\n  USE POWER ;\n END BAD\nEND gf180mcu_fd_sc_mcu7t5v0__physical\n")
    with pytest.raises(ValueError, match="LVS_PG_MASTER_PIN_VIEW_CONFLICT"):
        p.emit_power_aware_netlist("module dut();\nendmodule\n", "gf180mcuD",
                                  cell_lef=a, additional_lefs=[b])
