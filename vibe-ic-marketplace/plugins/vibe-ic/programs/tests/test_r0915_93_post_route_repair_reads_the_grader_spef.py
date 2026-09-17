"""R-0915-93: the post-route SDR repair runs under the grader SPEF, never an estimate.

Measured: subservient r30's own SDR child read grader WNS -5.924 ns and
`estimate_parasitics -global_routing` +5.500 ns (every violation hidden, RSZ-0098,
sign-off SS -0.62); r27 with no estimate closed at +0.03 (byte-identical bisect);
the same estimate is 5.8x off on opentitan_aes. The R-0915-92 guard is retired with
its subject. Pre-route repair is not governed and keeps its estimate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phase3_one_shot_runner as R                       # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

STAGES = ("postroute_drv_repair", "postroute_drv_reconverge")
REPAIRS = ("repair_design -max_wire_length", "repair_timing -setup")


def _loop(tmp_path, stage, deck=None):
    deck = deck or _full_pnr_tcl(tmp_path)
    ck = tmp_path / "ckpt.def"
    ck.write_text("CHECKPOINT\n")
    child = R._build_pnr_sdr_child_tcl_text(deck, checkpoint_def_c=str(ck), stage=stage)
    lo, hi = child.index("SDR_DRV_PASS"), child.index("SDR_CHILD_RECEIPT")
    return child[lo:hi], str(tmp_path / "out" / "sdr_pass.spef")


def _code(text):
    """Tcl with string literals blanked, so a disclosure that NAMES a command
    is not mistaken for the command."""
    return re.sub(r'"[^"\n]*"', '""', text)


def violations(loop, spef):
    """What makes a loop non-compliant with R-0915-93, as a list of reasons."""
    out = []
    code = _code(loop)
    if "estimate_parasitics" in code:
        out.append("estimate_parasitics in the post-route SDR loop")
    for call in REPAIRS:
        for m in re.finditer(re.escape(call), code):
            head = code[:m.start()]
            src = max(head.rfind(f"read_spef {spef}"), head.rfind("estimate_parasitics"))
            if src < 0 or head.rfind(f"read_spef {spef}") != src:
                out.append(f"{call!r} at {m.start()} is not preceded by read_spef {spef}")
    return out


def test_both_sdr_stages_repair_under_the_grader_spef_and_never_estimate(tmp_path):
    for stage in STAGES:
        loop, spef = _loop(tmp_path, stage)
        assert violations(loop, spef) == [], (stage, violations(loop, spef))


def test_the_aes_drv_fixture_keeps_its_repairs(tmp_path):
    """Retiring the estimate must not retire the repair (AES DRV loop)."""
    for stage in STAGES:
        loop, _ = _loop(tmp_path, stage)
        for call in REPAIRS:
            assert call in _code(loop), (stage, call)


def test_the_checker_fires_on_an_estimate_before_a_repair(tmp_path):
    """NEGATIVE CONTROL: the pre-ruling shape is caught."""
    loop, spef = _loop(tmp_path, STAGES[0])
    bad = loop.replace("    if {[catch {repair_timing -setup}",
                       "    estimate_parasitics -global_routing\n    if {[catch {repair_timing -setup}", 1)
    assert bad != loop
    reasons = violations(bad, spef)
    assert any("estimate_parasitics" in r for r in reasons), reasons
    assert any("repair_timing -setup" in r for r in reasons), reasons


def test_a_repair_with_no_spef_read_is_caught(tmp_path):
    loop, spef = _loop(tmp_path, STAGES[0])
    assert violations(loop.replace(f"read_spef {spef}", "read_spef /elsewhere.spef"), spef)


def test_the_outcome_is_named_either_way(tmp_path):
    loop, _ = _loop(tmp_path, STAGES[0])
    for tag in ("SDR_RSZ_PARASITICS", "SDR_SETUP_RSZ_PARASITICS"):
        i = loop.index(tag)
        win = loop[i:i + 600]
        assert "grader SPEF read" in loop and "FAILED" in win and "EST-0027" in win, tag


def test_pre_route_repair_is_untouched(tmp_path):
    """Ruling (3): the placement-stage estimate stays."""
    assert "{estimate_parasitics -placement}" in _full_pnr_tcl(tmp_path)


def test_the_why_is_written_where_the_code_is():
    doc = R._post_route_repair_parasitics_tcl.__doc__
    assert "-5.924" in doc and "+5.500" in doc and "5.8x" in doc
