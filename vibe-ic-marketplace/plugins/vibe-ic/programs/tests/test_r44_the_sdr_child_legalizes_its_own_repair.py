"""r44 (subservient x gf180mcuD as a DIE): the post-route repair child must
legalize what it repaired, by DISPLACEMENT, and never by weakening the clock.

MEASURED. The child repaired and routed clean (`mutated=1 error=0 route_ok=1`)
but its placement carried 6 violations, so the parent refused the candidate as
`candidate_placement_illegal` — correctly, an overlapping placement is an
illegal database. The repair went with it: the shipped netlist kept 6 ns slews
and missed SS setup by 13.48 ns (internal reg->reg, -8.58 ns even with no
external I/O delay at all). The child's own legalization was two rungs,
default then diamond.

Both directions: the child now runs the same displacement ladder every other
repair site uses, it runs ONLY when a violation was measured (so a candidate
that legalized first time is byte-identical), and it carries NO clock-buffer
downsize rung — r42 measured that trade at -12.27 ns, and a child may not buy
legality by weakening a clock tree it inherited.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _repair_block() -> str:
    return R._post_route_spef_repair_tcl("/out", "/t.lef", "/c.lef",
                                         fork_repair_capable=True)


def _legalize_window(tcl: str) -> str:
    """The child's legalize-and-verify block, up to the count it prints."""
    end = tcl.index('puts "SDR_PLACEMENT_VIOLATIONS:')
    start = tcl.index("if {[catch {detailed_placement} _sdr_dp]}")
    return tcl[start:end]


def test_the_child_escalates_displacement_like_every_other_site():
    win = _legalize_window(_repair_block())
    assert "SDR_DPL_LEGALIZE_OK" in win
    assert "-max_displacement" in win
    assert "SDR_DPL_LEGALIZE_FAILED" in win        # it can still say no


def test_the_child_never_downsizes_the_clock_it_inherited():
    win = _legalize_window(_repair_block())
    assert "CLKBUF_DOWNSIZE" not in win
    # the builder is asked WITHOUT a sink buffer, which is what omits the rung
    assert "SDR_DPL_CLKBUF_DOWNSIZE" not in _repair_block()


def test_the_ladder_runs_only_when_a_violation_was_measured():
    win = _legalize_window(_repair_block())
    gate = win.index("$_sdr_pv > 0")
    assert gate < win.index("SDR_DPL_LEGALIZE_OK"), (
        "a candidate that legalized on the first detailed_placement must not "
        "run the ladder at all — that path is the macro path and stays "
        "byte-identical")


def test_the_count_the_parent_judges_is_taken_after_the_ladder():
    tcl = _repair_block()
    win = _legalize_window(tcl)
    last_ok = win.rindex("SDR_DPL_LEGALIZE")
    recheck = win.rindex("check_placement -no_abort")
    assert recheck > last_ok, "the parent must judge the POST-ladder placement"


def test_the_parents_refusal_is_unchanged():
    # the rule this lane added in r36 still owns the verdict
    fin = R._postroute_sdr_transaction_finish_tcl("postroute_drv_repair")
    assert "candidate_placement_illegal" in fin
    assert "$_sdr_pv != 0" in fin
