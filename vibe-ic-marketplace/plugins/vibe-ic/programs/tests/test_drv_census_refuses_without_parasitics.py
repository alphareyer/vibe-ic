#!/usr/bin/env python3
"""R-0915-83 — a DRV census taken with no parasitics in STA reports 0.

THE MEASUREMENT THAT FORCED THIS, and the mistake it corrects. Investigating
the opentitan_aes wall I ran three "extraction" arms that each called
`extract_parasitics` and then counted violators, got 0 from all three, and
reported that the loop's 41,956 was a phantom. It was not. The control, one
session on that run's own checkpoint DEF at the max corner:

    extract_parasitics ONLY ............................    0 violators
    the SAME extraction, then write_spef + read_spef ...   45,237 violators

`extract_parasitics` populates the ODB; OpenSTA does not see it until a SPEF is
read back. A census taken after extraction alone is not a clean design -- it is
a design nobody measured, and the two are byte-indistinguishable. Worse, the
loop reads 0 as `SDR_CONVERGED: pass 1` and stops, so the unmeasured case is
the one that looks best.

The second silent zero is in the same block: `report_check_types ... > rpt` was
wrapped in a bare `catch`, so a failed report left the counter at 0 and the
loop called that convergence too.

SEPARATELY, and NOT fixed here because it is OpenROAD's own behaviour: on all
four passes of that run the tool printed `[WARNING EST-0027] no estimated
parasitics. Using wire load models.` right after the census. The census is
sound -- `read_spef` feeds STA -- but `repair_design` wants ESTIMATED
parasitics, finds none, and optimises against a wire-load model while being
graded on SPEF. The deck now says so in its own log rather than leaving it in a
tool warning.

The fixture is the shipping log's own four occurrences.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tcl_walk                    # noqa: E402
from _pnr_tcl_stub import STUB as _STUB          # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

# The four occurrences as they appear in the shipping log, with the census line
# each one followed. Pass, extraction net count, census total.
SHIPPING_LOG = """\
[INFO RCX-0045] Extract 41087 nets, 197710 rsegs, 197710 caps, 461909 ccs
SDR_DRV_BY_KIND: total=41956 max_capacitance=5805
[WARNING EST-0027] no estimated parasitics. Using wire load models.
[INFO RCX-0045] Extract 49218 nets, 224874 rsegs, 224874 caps, 561189 ccs
SDR_DRV_BY_KIND: total=19013 max_capacitance=4983
[WARNING EST-0027] no estimated parasitics. Using wire load models.
[INFO RCX-0045] Extract 49964 nets, 226925 rsegs, 226925 caps, 569809 ccs
SDR_DRV_BY_KIND: total=2647 max_capacitance=599
[WARNING EST-0027] no estimated parasitics. Using wire load models.
[INFO RCX-0045] Extract 50268 nets, 227506 rsegs, 227506 caps, 574383 ccs
SDR_DRV_BY_KIND: total=1896 max_capacitance=437
[WARNING EST-0027] no estimated parasitics. Using wire load models.
"""


def _child(tmp_path: Path) -> str:
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / "ckpt.def"
    ckpt.write_text("CHECKPOINT\n")
    return R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage="postroute_drv_repair")


# ─────────────── the fixture is real, and it says what I claim ─────────────

def test_the_fixture_is_the_shipping_logs_own_four_occurrences():
    """NEGATIVE CONTROL for the fixture. A test built on a log excerpt is only
    as good as the excerpt; assert its shape before asserting anything from
    it."""
    est = re.findall(r"EST-0027", SHIPPING_LOG)
    totals = re.findall(r"SDR_DRV_BY_KIND: total=(\d+)", SHIPPING_LOG)
    assert len(est) == 4, est
    assert totals == ["41956", "19013", "2647", "1896"], totals
    # every census is IMMEDIATELY followed by the warning -- that adjacency is
    # the whole claim about which command lacked parasitics.
    lines = SHIPPING_LOG.strip().split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("SDR_DRV_BY_KIND"):
            assert "EST-0027" in lines[i + 1], (i, lines[i + 1])


# ───────────────────── the census refuses, by name ─────────────────────────

def test_the_census_refuses_when_parasitics_did_not_reach_sta(tmp_path):
    child = _child(tmp_path)
    assert "SDR_DRV_CENSUS_NOT_MEASURED" in child, (
        "the census can still report a number with no parasitics in STA, "
        "which measures 0 and reads as a clean design")
    i = child.index("SDR_DRV_CENSUS_NOT_MEASURED")
    win = child[max(0, i - 600):i + 900]
    assert "parasitics_in_sta=" in win and "violator_report=" in win, (
        "the refusal does not say WHICH precondition failed")


def test_the_refusal_names_no_number(tmp_path):
    """A refusal that also prints a count would be read as the count."""
    child = _child(tmp_path)
    i = child.index("SDR_DRV_CENSUS_NOT_MEASURED")
    j = child.index("break", i)
    assert "SDR_DRV_BY_KIND" not in child[i:j], (
        "the refusal path still emits a DRV number")


def test_the_loop_does_not_run_on_a_refused_census(tmp_path):
    child = _child(tmp_path)
    i = child.index("SDR_DRV_CENSUS_NOT_MEASURED")
    seg = child[i:i + 900]
    assert "set _sdr_tx_error 1" in seg and "break" in seg, (
        "a refused census does not stop the repair loop")
    # and the refusal precedes the repair, not the other way round
    assert i < child.index("repair_design -max_wire_length"), (
        "the census refusal is emitted after the repair it is meant to gate")


def test_the_parasitics_flag_is_set_only_by_a_successful_read_spef(tmp_path):
    """The flag must be evidence, not an assumption. It is cleared before the
    read and set only on the line after it returns."""
    child = _child(tmp_path)
    clears = [m.start() for m in re.finditer(r"set _sdr_par_ok 0", child)]
    sets = [m.start() for m in re.finditer(r"set _sdr_par_ok 1", child)]
    # The deck emits this block at more than one site, so the invariant is
    # per-occurrence, not a global count: EVERY claim that the parasitics
    # arrived must be preceded by its own clear and its own read_spef.
    assert clears and len(sets) == len(clears), (
        f"{len(clears)} clear(s) vs {len(sets)} claim(s) that parasitics "
        "arrived -- every claim needs its own clear")
    for clear, setok in zip(clears, sets):
        rd = child.index("read_spef", clear)
        assert clear < rd < setok, (
            "the flag is not cleared before read_spef and set after it")


def test_a_failed_violator_report_is_not_a_zero(tmp_path):
    """The second silent zero: a bare `catch` around report_check_types left
    the counter at 0, which the loop read as convergence."""
    child = _child(tmp_path)
    assert "SDR_DRV_REPORT_FAILED" in child, (
        "a failed report_check_types is still swallowed silently")
    assert "set _sdr_rpt_ok 0" in child


# ───────────── the wire-load fallback is disclosed, not hidden ─────────────

def test_the_repair_model_is_disclosed_next_to_the_number(tmp_path):
    child = _child(tmp_path)
    assert "SDR_REPAIR_MODEL" in child, (
        "the log still leaves the wire-load fallback in a tool warning")
    assert "EST-0027" in child, (
        "the disclosure does not name the warning a reader must look for")
    assert child.index("SDR_REPAIR_MODEL") < child.index(
        "repair_design -max_wire_length"), (
        "the disclosure is emitted after the repair it describes")


# ────────────────── with-parasitics control: it GRADES ────────────────────

def test_with_parasitics_the_census_still_reports_its_number(tmp_path):
    """THE CONTROL THAT MAKES THE REFUSAL MEAN SOMETHING. A gate that refuses
    everything passes this file for free. Drive the real deck with a working
    read_spef and a violator report, and require a REAL number out."""
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / "ckpt.def"
    ckpt.write_text("CHECKPOINT\n")
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage="postroute_drv_repair")
    harness = (
        "namespace eval ord { proc get_db_block {} { return ::_vic_blk } }\n"
        "proc ::_vic_blk {op args} {\n"
        "  switch -- $op { getDefUnits { return 1000 } "
        "getDieArea { return ::_vic_die } default { return \"\" } }\n"
        "}\n"
        "proc ::_vic_die {op args} {\n"
        "  switch -- $op { dx - dy { return 1150000 } default { return \"\" } }\n"
        "}\n"
        "proc read_spef {path} { return }\n"
        "proc write_spef {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts $f SPEF; close $f\n"
        "}\n"
        "proc extract_parasitics {args} { return }\n"
        "proc report_check_types {args} {\n"
        "  set path \"\"; set take 0\n"
        "  foreach a $args {\n"
        "    if {$take} { set path $a; set take 0; continue }\n"
        "    if {$a eq \">\"} { set take 1 } elseif {[string index $a 0] eq \">\"} "
        "{ set path [string range $a 1 end] }\n"
        "  }\n"
        "  if {$path eq \"\"} { return }\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]\n"
        "  puts $f \"max capacitance\"\n"
        "  for {set i 0} {$i < 7} {incr i} "
        "{ puts $f \"  net_$i 0.1 0.2 -0.1 (VIOLATED)\" }\n"
        "  close $f\n"
        "}\n"
        "proc detailed_route {args} {\n"
        "  # -output_drc\n"
        "  return\n"
        "}\n"
        "proc global_route {args} { return }\n"
        "proc write_db {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts -nonewline $f ODB; close $f\n"
        "}\n"
        "proc write_def {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts -nonewline $f DEF; close $f\n"
        "}\n"
        "proc write_verilog {path} {}\n")
    out, err, route = _tcl_walk.walk(
        "source [lindex $argv 0]\n", _STUB + harness + child, tmp_path)
    assert "SDR_DRV_CENSUS_NOT_MEASURED" not in out, (
        f"[{route}] the census refused a run that HAD parasitics and a report:"
        f"\n{out[-1500:]}")
    assert "SDR_DRV_BY_KIND: total=7" in out, (
        f"[{route}] the census did not grade: {out[-1500:]}")


def test_the_child_deck_is_still_valid_tcl(tmp_path):
    """NEGATIVE CONTROL for the whole change."""
    out, err, route = _tcl_walk.walk(
        "source [lindex $argv 0]\n", _STUB + _child(tmp_path), tmp_path)
    assert "missing close-bracket" not in err, err
    assert "SDR_CHILD_DONE" in out, f"[{route}] {out[-1200:]}"
