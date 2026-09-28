"""CR-2: the repaired route must meet the declared max-fanout limit."""
from __future__ import annotations

import subprocess
from pathlib import Path


_TCL = (Path(__file__).resolve().parents[1] / "librelane_plugins" /
        "librelane_plugin_vibeic" / "postroute_repair.tcl")


def _gate() -> str:
    text = _TCL.read_text()
    begin = "# CR-2 POSTROUTE FANOUT BEGIN"
    end = "# CR-2 POSTROUTE FANOUT END"
    assert begin in text and end in text
    return text.split(begin, 1)[1].split(end, 1)[0]


def _run_gate(count: int | None) -> subprocess.CompletedProcess[str]:
    setup = "namespace eval sta {}\nnamespace eval utl {}\n"
    setup += "proc utl::metric_integer {args} {}\n"
    if count is not None:
        setup += f"proc sta::max_fanout_violation_count {{}} {{return {count}}}\n"
    return subprocess.run(["tclsh"], input=setup + _gate() + "puts ACCEPTED\n",
                          text=True, capture_output=True, timeout=10)


def test_zero_fanout_residue_accepts_routed_candidate():
    result = _run_gate(0)
    assert result.returncode == 0
    assert "ACCEPTED" in result.stdout


def test_nonzero_fanout_residue_refuses_other_design():
    result = _run_gate(3)
    assert result.returncode == 1
    assert "vibeic_prr_fanout_violation: 3" in result.stderr
    assert "ACCEPTED" not in result.stdout


def test_unreadable_counter_is_not_measured():
    result = _run_gate(None)
    assert result.returncode == 2
    assert "vibeic_prr_fanout_not_measured" in result.stderr


def test_last_timing_repair_is_followed_by_drv_repair_and_routed_recheck():
    text = _TCL.read_text()
    last_timing = text.index("log_cmd repair_timing {*}$hold_args")
    drv_repair = text.index("log_cmd repair_design {*}$rd_args", last_timing)
    routed_check = text.index("# CR-2 POSTROUTE FANOUT BEGIN", drv_repair)
    assert last_timing < drv_repair < routed_check < text.index("write_views", routed_check)
