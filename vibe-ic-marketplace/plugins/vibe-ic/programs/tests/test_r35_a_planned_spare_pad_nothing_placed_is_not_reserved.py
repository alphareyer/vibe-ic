"""r35 (subservient x gf180mcuD as a DIE): the route deck reserves only spare
instances the design actually carries.

MEASURED: with a pad ring the spare plan names `spare_pad_in_0` /
`spare_pad_out_0`, no producer instantiates them, and the EM-resize re-route
reached `RESERVED_INSTANCE_MISSING spare_pad_in_0` and killed PnR.

Both directions: every planned spare CELL stays reserved whatever the wrapper
says; a spare PAD is reserved exactly when the wrapper instantiates it, and an
unplaced one is returned for disclosure, never silently lost.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

PLAN = {"instances": [{"name": "spare_inv_0"}, {"name": "spare_dff_1"}],
        "spare_pads": [{"name": "spare_pad_in_0", "kind": "input"},
                       {"name": "spare_pad_out_0", "kind": "output"}]}


def test_unplaced_spare_pads_are_disclosed_not_reserved(tmp_path):
    wrapper = tmp_path / "chip_top_io.v"
    wrapper.write_text("module chip_top(); core u_core (); endmodule\n")
    names, unplaced = R._reserved_instance_names(PLAN, wrapper)
    assert names == ["spare_inv_0", "spare_dff_1"]
    assert unplaced == ["spare_pad_in_0", "spare_pad_out_0"]


def test_a_spare_pad_the_wrapper_instantiates_is_reserved(tmp_path):
    wrapper = tmp_path / "chip_top_io.v"
    wrapper.write_text("module chip_top();\n  pad_in spare_pad_in_0 (.PAD());\n"
                       "endmodule\n")
    names, unplaced = R._reserved_instance_names(PLAN, wrapper)
    assert "spare_pad_in_0" in names and unplaced == ["spare_pad_out_0"]


def test_a_name_that_only_prefixes_a_token_does_not_count(tmp_path):
    wrapper = tmp_path / "chip_top_io.v"
    wrapper.write_text("module chip_top(); x spare_pad_in_0_shadow (); endmodule\n")
    names, unplaced = R._reserved_instance_names(PLAN, wrapper)
    assert "spare_pad_in_0" not in names and "spare_pad_in_0" in unplaced


def test_spare_cells_stay_reserved_without_any_wrapper(tmp_path):
    names, unplaced = R._reserved_instance_names(PLAN, tmp_path / "absent.v")
    assert names[:2] == ["spare_inv_0", "spare_dff_1"]
    no_pads = {"instances": PLAN["instances"], "spare_pads": []}
    assert R._reserved_instance_names(no_pads, None) == (
        ["spare_inv_0", "spare_dff_1"], [])
