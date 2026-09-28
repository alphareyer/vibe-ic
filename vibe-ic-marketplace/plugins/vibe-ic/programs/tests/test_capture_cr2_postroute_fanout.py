"""CR-2: routed fanout is judged from the separate STAPostPNR corner runs."""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import librelane_postroute_repair as prr  # noqa: E402

_TCL = (PROGRAMS / "librelane_plugins" / "librelane_plugin_vibeic" /
        "postroute_repair.tcl")
CORNERS = ("fast", "slow")


def _report(counts):
    metrics = {}
    for corner, count in zip(CORNERS, counts):
        metrics[f"design__max_fanout_violation__count__corner:{corner}"] = count
    report = {"final": prr.summarize(metrics, CORNERS), "corners": CORNERS}
    prr._set_final_fanout_verdict(report)
    return report


def test_zero_fanout_residue_accepts_routed_candidate():
    report = _report((0, 0))
    assert report["verdict"] == "PASS"
    assert report["final_fanout"]["violations"] == 0


def test_nonzero_fanout_residue_refuses_other_design():
    report = _report((0, 3))
    assert report["verdict"] == "FAIL"
    assert report["code"] == "LL_PRR_FANOUT_VIOLATION"
    assert report["final_fanout"]["violations"] == 3


def test_unreadable_counter_is_not_measured():
    report = _report((0, None))
    assert report["verdict"] == "NOT_MEASURED"
    assert report["code"] == "LL_PRR_FANOUT_NOT_MEASURED"


def test_last_timing_repair_is_followed_by_drv_repair_and_safe_routed_recheck():
    text = _TCL.read_text()
    last_timing = text.index("log_cmd repair_timing {*}$hold_args")
    drv_repair = text.index("log_cmd repair_design {*}$rd_args", last_timing)
    no_op = text.index("if {$::vic_changed == 0}", drv_repair)
    assert last_timing < drv_repair < no_op
    assert "sta::max_fanout_violation_count}" not in text
    assert prr.MEASURE_STEPS[-1] == "OpenROAD.STAPostPNR"
