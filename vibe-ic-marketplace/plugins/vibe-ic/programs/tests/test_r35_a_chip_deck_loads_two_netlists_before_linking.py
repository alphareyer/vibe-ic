"""r35 (subservient x gf180mcuD as a DIE): the checkpoint deck recognises a
design-load BLOCK, not only a `read_verilog` / `link_design` pair.

MEASURED: the chip-path pnr.tcl reads the core netlist, a comment, the pad
wrapper, a blank line, then `link_design chip_top`. The deck builder demanded
`link_design` on the line after the first `read_verilog`, so both SDR child
decks were refused (SDR_CHILD_DECK_NOT_WRITTEN) and no post-route DRV repair
ran on the die.

Both directions: the whole block is replaced by exactly one restore; the plain
pair still works; any other command inside the block still refuses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

HEAD = "read_lef tech.lef\nread_liberty lib.lib\n"
TAIL = (f"read_sdc x.sdc\n{R._PNR_RESUME_ELIDE_BEGIN}\nfloorplan\n"
        f"{R._PNR_RESUME_ELIDE_END}\nwrite_def out.def\n")


def _deck(load: str) -> str:
    return HEAD + load + TAIL


def _lines(text):
    return R._pnr_deck_from_checkpoint(text, checkpoint_def_c="/c/ckpt.def")


def test_a_chip_path_load_block_becomes_one_restore():
    out = _lines(_deck("read_verilog core.v\n# the pad-carrying chip top\n"
                       "read_verilog chip_top_io.v\n\nlink_design chip_top\n"))
    assert not any(ln.startswith(("read_verilog ", "link_design ")) for ln in out)
    assert sum(ln == "read_def /c/ckpt.def" for ln in out) == 1
    assert "read_sdc x.sdc" in out and "floorplan" not in out


def test_the_plain_pair_is_unchanged():
    out = _lines(_deck("read_verilog core.v\nlink_design core\n"))
    assert sum(ln == "read_def /c/ckpt.def" for ln in out) == 1
    assert not any(ln.startswith(("read_verilog ", "link_design ")) for ln in out)


@pytest.mark.parametrize("between", ["read_def other.def", "set x 1",
                                     "source hook.tcl"])
def test_a_command_inside_the_block_still_refuses(between):
    with pytest.raises(R.PnrResumeUnavailable, match="design-load site"):
        _lines(_deck(f"read_verilog core.v\n{between}\n"
                     "read_verilog chip_top_io.v\nlink_design chip_top\n"))


def test_no_link_design_still_refuses():
    with pytest.raises(R.PnrResumeUnavailable, match="design-load site"):
        _lines(_deck("read_verilog core.v\n# comment\n\n"))
