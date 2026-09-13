"""A pin on a routing layer must not globally disable min-area repair.

The patcher can only measure routing-database wire geometry.  A cluster that
actually touches an instance/top-level terminal may include cell pin metal and
must stay excluded; a cluster elsewhere on the same layer must still be
considered.  This regression pins that distinction at the Tcl producer.
"""
import re
from pathlib import Path


PROG = Path(__file__).resolve().parents[1] / "phase3_one_shot_runner.py"
SRC = PROG.read_text()


def _tcl() -> str:
    match = re.search(r'_MIN_AREA_PATCH_TCL = r"""(.*?)"""', SRC, re.S)
    assert match, "min-area Tcl template missing"
    return match.group(1)


def test_pin_protection_is_per_cluster_not_global_per_layer():
    """GF180 masters use M1--M5 pins; that must not skip all M2/M3 wires."""
    tcl = _tcl()
    assert "proc ma_term_rects_of_net" in tcl
    assert "proc ma_cluster_touches_term" in tcl
    assert "ma_cluster_touches_term $cl $termrs" in tcl
    assert "if {[dict exists $pinlayers [$lay getName]]} { continue }" not in tcl
    main = tcl[tcl.index("proc min_area_patch"):]
    assert main.index("ma_cluster_touches_term $cl $termrs") < main.index(
        "set ar [ma_union_area $cl]"
    )


def test_terminal_metal_is_a_patch_blockage():
    """A patch extension must not intrude into another net's pin metal."""
    tcl = _tcl()
    main = tcl[tcl.index("proc min_area_patch"):]
    first_pass = main[main.index("# 1st pass:"):main.index("set patched 0")]
    assert "set netterms" in first_pass
    assert "ma_term_rects_of_net $net" in first_pass
    assert "dict lappend all $ln $tr" in first_pass
