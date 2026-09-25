"""The shipped route must repair and gate the hold view it measured."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as runner


def test_ship_producer_repairs_measured_hold_before_route_and_each_retry():
    deck = runner._ship_signoff_spef_repair_tcl(
        "chip", "/tech.lef", "/cell.lef", "/ss.lib", "/pnr",
        "/cap.max", "Metal", 2)
    assert "repair_timing -hold" in deck
    assert deck.index("repair_timing -hold") < deck.index("SHIP_REPAIR_NOOP")
    assert "SHIP_CVG_HOLD_REPAIR_NONFATAL" in deck
    assert "$_cvg_hold >= 0" in deck


def test_ship_promotion_rejects_negative_or_unmeasured_final_hold():
    good = {"wns_before": 0.3, "wns_after_repair": 0.5,
            "wns_postroute": 0.6, "route_violations": 0,
            "done": True, "hold_postroute": 0.1}
    assert runner._ship_repair_should_promote(good, True, True)
    assert not runner._ship_repair_should_promote(
        {**good, "hold_postroute": -0.01}, True, True)
    assert not runner._ship_repair_should_promote(
        {**good, "hold_postroute": None}, True, True)
    parsed = runner._parse_ship_repair_log(
        "SHIP_HOLD_GATE_EXPECTED: 1\nSHIP_WNS_BEFORE: 0.3\n"
        "SHIP_WNS_AFTER_REPAIR: 0.5\nSHIP_WNS_POSTROUTE: 0.6\n"
        "Number of violations = 0\nSHIP_SIGNOFF_REPAIR_DONE\n")
    assert "hold_postroute" in parsed
    assert parsed["hold_postroute"] is None
    assert not runner._ship_repair_should_promote(parsed, True, True)


def test_hold_closure_can_spend_positive_setup_slack_without_a_new_red():
    parsed = {"wns_before": 0.36, "wns_after_repair": 0.35,
              "wns_postroute": 0.34, "hold_before": -0.45,
              "hold_postroute": 0.04, "route_violations": 0, "done": True}
    assert runner._ship_repair_should_promote(parsed, True, True)
    assert not runner._ship_repair_should_promote(
        {**parsed, "wns_postroute": -0.01}, True, True)
    assert not runner._ship_repair_should_promote(
        {**parsed, "hold_postroute": -0.01}, True, True)
