"""Every physical supply pad must be a PSM source on the one logical rail."""
import re

from test_a_chip_top_io_port_is_the_pad_terminal import (
    _floorplan, _moves, _pad,
)
import pad_ring_gen as G


def test_multiple_bond_pads_are_multiple_ports_of_one_supply_pin():
    pads = [
        _pad("u_pad_supply_power_0", "VDD", "fixture_io__in_c",
             100000, 200000, "N"),
        _pad("u_pad_supply_power_1", "VDD", "fixture_io__in_c",
             500000, 600000, "N"),
    ]
    moves, notes = _moves(pads)
    assert notes == [] and len(moves) == 2
    out, rewritten = G._rewrite_pins(_floorplan(["VDD"]), moves)
    assert rewritten == ["VDD"]
    assert "PINS 1 ;" in out
    pin = re.search(r"(?s)- VDD .*?;", out).group(0)
    assert pin.count("+ PORT") == 2
    assert "+ FIXED ( 100000 200000 ) N" in pin
    assert "+ FIXED ( 500000 600000 ) N" in pin


def test_signal_bterm_still_has_one_port():
    moves, _ = _moves([_pad("u_pad_signal", "a", "fixture_io__in_c",
                           100000, 200000, "N")])
    out, rewritten = G._rewrite_pins(_floorplan(["a"]), moves)
    assert rewritten == ["a"]
    assert re.search(r"(?s)- a .*?;", out).group(0).count("+ PORT") == 1
