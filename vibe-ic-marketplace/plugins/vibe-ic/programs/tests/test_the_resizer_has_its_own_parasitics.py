#!/usr/bin/env python3
"""R-0915-84(a) — the repair optimised against a wire-load model.

MEASURED (opentitan_aes x sky130A, 2026-09-16). On ALL FOUR passes of the
post-route DRV loop, OpenROAD printed this immediately after the census:

    [INFO RCX-0045] Extract 41087 nets, ...
    SDR_DRV_BY_KIND: total=41956 max_capacitance=5805
    [WARNING EST-0027] no estimated parasitics. Using wire load models.

The census is sound: `read_spef` feeds STA, and R-0915-83's refusal proves it.
But the RESIZER keeps its own parasitics source and `read_spef` does not set
it, so `repair_design` / `repair_timing` ran on a WIRE LOAD MODEL while being
GRADED on the extracted SPEF. Four passes moved DRV 41,956 -> 19,013 -> 2,647
-> 1,896 and never closed, which is what a repair whose model disagrees with
its grader looks like. The same run's three-way table showed the scale of the
disagreement: the estimate under-reads extraction by 1.3x on a 0.8 um net and
7.2x on a 1,375 um one.

`estimate_parasitics -global_routing` is the resizer's own source, and is what
ORFS runs before its repair steps.

THE FIXTURE IS THE SHIPPING LOG. A test that only asserts on emitted text can
drift away from the failure it was written for, so the RED fixture here is the
four real occurrences and the GREEN one is a log of the same shape with the
warning absent.
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
from _pnr_tcl_stub import ANNOTATED_SESSION  # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

RED_LOG = """\
SDR_DRV_BY_KIND: total=41956 max_capacitance=5805
[WARNING EST-0027] no estimated parasitics. Using wire load models.
SDR_DRV_BY_KIND: total=19013 max_capacitance=4983
[WARNING EST-0027] no estimated parasitics. Using wire load models.
SDR_DRV_BY_KIND: total=2647 max_capacitance=599
[WARNING EST-0027] no estimated parasitics. Using wire load models.
SDR_DRV_BY_KIND: total=1896 max_capacitance=437
[WARNING EST-0027] no estimated parasitics. Using wire load models.
"""

GREEN_LOG = """\
SDR_DRV_BY_KIND: total=41956 max_capacitance=5805
SDR_RSZ_PARASITICS: estimate_parasitics -global_routing OK
SDR_DRV_BY_KIND: total=19013 max_capacitance=4983
SDR_RSZ_PARASITICS: estimate_parasitics -global_routing OK
"""


def blind_passes(log: str) -> list[int]:
    """Which passes repaired on wire load models, by the tool's own warning."""
    out, pending = [], None
    for ln in log.split("\n"):
        m = re.match(r"SDR_DRV_BY_KIND: total=(\d+)", ln)
        if m:
            pending = int(m.group(1))
        # THE TOOL'S OWN WARNING, not the token. The deck's own disclosure
        # line names EST-0027 so a reader knows what to look for, and a
        # detector that matched the bare token would count that disclosure as
        # the failure it warns about.
        elif ln.lstrip().startswith("[WARNING EST-0027]") and pending is not None:
            out.append(pending)
            pending = None
    return out


def _child(tmp_path: Path) -> str:
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / "ckpt.def"
    ckpt.write_text("CHECKPOINT\n")
    return R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage="postroute_drv_repair")


# ───────────────── the fixture detector works both ways ────────────────────

def test_the_detector_finds_all_four_blind_passes_in_the_shipping_log():
    """RED fixture. If this ever stops finding four, the fixture rotted."""
    assert blind_passes(RED_LOG) == [41956, 19013, 2647, 1896]


def test_the_detector_finds_none_in_a_clean_log():
    """GREEN fixture, and the control that keeps the detector from being a
    function that always returns a non-empty list."""
    assert blind_passes(GREEN_LOG) == []


# ───────────────── the deck gives the resizer a source ─────────────────────

def test_every_repair_in_the_sdr_loop_is_governed_by_the_census_read(
        tmp_path):
    """R-0915-94 (supersedes 84(a)'s estimate-before-repair contract): each
    repair is governed by the pass's ONE census `read_spef`, and nothing on the
    main path re-reads a SPEF or estimates between that read and the repair.
    The EST-0104 recovery inside a failed repair_design is r27's and excluded."""
    import re
    child = _child(tmp_path)
    lo = child.index("write_spef")
    hi = child.index("SDR_CHILD_RECEIPT", lo)
    reg = re.sub(r"# --- R9 EST-0104 recovery.*?EST0104_RECOVERED[^\n]*\n", "",
                 child[lo:hi], flags=re.S)
    code = re.sub(r'"[^"\n]*"', '""', reg)
    census = code.find("read_spef")
    assert census >= 0 and "sdr_pass.spef} _sdr_sr" in reg, "no census SPEF read"
    for call in ("repair_design -max_wire_length", "repair_timing -setup"):
        i = code.find(call, census)
        assert i > census, f"{call!r} is not after the census read"
        between = code[census + len("read_spef"):i]
        assert "read_spef" not in between and "estimate_parasitics" not in between, (
            f"{call!r} is not governed by the census read")


def test_the_outcome_is_named_either_way(tmp_path):
    """R-0915-94: the repair's parasitics are disclosed ONCE, beside the census,
    naming EST-0027 and the wire-load fallback — not by a per-repair Tcl line."""
    child = _child(tmp_path)
    assert "_RSZ_PARASITICS" not in child
    i = child.index("SDR_REPAIR_MODEL")
    win = child[i:i + 400]
    assert "EST-0027" in win and "WIRE LOAD MODELS" in win, win


def test_both_repair_kinds_are_attributed_by_the_transaction_record(tmp_path):
    """R-0915-94: attribution moved from per-repair Tcl markers to the SDR
    transaction record (`repair_parasitics`, per stage)."""
    child = _child(tmp_path)
    assert "SDR_RSZ_PARASITICS" not in child and "SDR_SETUP_RSZ_PARASITICS" not in child
    assert "repair_parasitics" in Path(R.__file__).read_text()


# ───────────────────────── it still grades ─────────────────────────────────

def test_with_parasitics_the_census_still_reports_a_real_number(tmp_path):
    """CONTROL. A change that made the loop refuse everything would pass every
    assertion above; require a real count out of the real deck."""
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
        "proc estimate_parasitics {args} { puts STUB_ESTIMATE_CALLED; return }\n"
        "set ::EP 0\n"
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
        "source [lindex $argv 0]\n", _STUB + ANNOTATED_SESSION + harness + child, tmp_path)
    assert "SDR_DRV_BY_KIND: total=7" in out, (
        f"[{route}] the census stopped grading: {out[-1500:]}")
    assert "_RSZ_PARASITICS" not in out, (
        "R-0915-94: the parasitics disclosure is recorded, not emitted into the loop")
    assert "STUB_ESTIMATE_CALLED" not in out, (
        "R-0915-94: the post-route SDR repair must never call estimate_parasitics")
    assert blind_passes(out) == [], (
        f"[{route}] a pass still repaired on wire load models")


def test_the_child_deck_is_still_valid_tcl(tmp_path):
    out, err, route = _tcl_walk.walk(
        "source [lindex $argv 0]\n", _STUB + _child(tmp_path), tmp_path)
    assert "missing close-bracket" not in err, err
    assert "SDR_CHILD_DONE" in out, f"[{route}] {out[-1200:]}"
