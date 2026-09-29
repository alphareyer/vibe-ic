"""A Verilator expression record may contain a Verilog sized literal."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verilator_coverage_measure as coverage  # noqa: E402


def test_real_expression_record_with_apostrophe_enters_line_union(tmp_path):
    # From Verilator 5.053 coverage.dat on 2026-09-28. The 32'h1 tokens are
    # part of the TOOL record's quoted body, not its closing delimiter.
    src = str(tmp_path / "design.sv")
    record = (
        "C '\x01f\x02" + src + "\x01l\x021912\x01n\x0235\x01t\x02expr"
        "\x01page\x02v_expr/design\x01o\x02"
        "({32'h1{{ctrl_aux_shadowed_key_touch_forces_reseed_storage_err, "
        "ctrl_aux_shadowed_force_masks_storage_err}}}[0]==0 && "
        "{32'h1{{ctrl_aux_shadowed_key_touch_forces_reseed_storage_err, "
        "ctrl_aux_shadowed_force_masks_storage_err}}}[1]==0) => 0"
        "\x01h\x02tb.dut.u_reg' 98\n"
    )
    dat = tmp_path / "coverage.dat"
    dat.write_text(record)
    lines = coverage.union_line_map([str(dat)])
    assert lines[src][1912] == 98
    union = coverage.union_coverage_dats([str(dat)])
    assert union["unionisable"] is True
    assert union["distinct_points"] == 1


def test_malformed_record_does_not_enter_union(tmp_path):
    dat = tmp_path / "coverage.dat"
    dat.write_text("C '\x01f\x02bad.sv\x01l\x021' 1 trailing junk\n")
    assert coverage.union_line_map([str(dat)]) == {}
