"""#2181 — the streamed GDS lost every port label, so LVS had no pins to match.

REPRODUCED on live main `9c653d47f` (v1.20.18) in the pinned image
`vibeic-eda@sha256:89a8fd72...2a76e3f49`, driving the runner's OWN emitted
streamout chain over a 4-pin DEF that declares exactly the pins #2181 names:

    stage           live main        main + this fix
    streamout       VDD VNW VPW VSS  VDD VNW VPW VSS
    grid snap       (none)           VDD VNW VPW VSS
    layer merge     (none)           VDD VNW VPW VSS

ONE root cause, not three failures. `pya.Region` carries POLYGONS ONLY, so the
`Region(sh) / sh.clear() / sh.insert(reg)` round-trip in BOTH post-streamout
passes destroys every text on the layer. The DEF's PIN labels are the only
thing naming a top port, so the sign-off GDS ships with none, extraction
produces a top with ZERO formal pins, and netgen reports exactly what the issue
records: `VDD / VNW / VPW / VSS |(no matching pin)`. It is not a power-grid or
pad-ring construction defect; the netlist and the deck never disagreed, because
the layout side had no pins to disagree about.

Measured on the real spm design (older base, same code path, stated for
provenance): the pre-fix sign-off GDS carries **0** text shapes in the entire
layout; the post-fix GDS carries exactly the **38** ports `routed.def` declares
(`clk p rst y x[0..31] VDD VSS`, layer 81/10), and a full re-extraction of it
yields `.SUBCKT chip_top` with **38** formal pins.

Only the TOP cell's own texts survive a FLATTEN. Preserving every cell's texts
through one yields 5,655 pins with collided names (`A|AB$17`) once the merge pass
flattens the hierarchy and `top_lvl_pins` promotes each labelled top net. (N6
moved that rule from the snap to the flattens: a hierarchical GDS now keeps
the library cells' pin labels.)

Locally verifiable here: that both Region passes carry texts, that the values
are copied out BEFORE `clear()` (reading a Shape handle after clear aborts the
interpreter), that only the top cell is preserved, and that the label anchor is
snapped rather than the transform rebuilt.
"""
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402


def test_grid_snap_carries_the_top_cells_texts():
    """The top cell's texts survive the snap -- and, since N6, every other
    cell's too. The top-only rule this test used to pin inside the snap
    (`_keep_texts = cell.cell_index() in _top_ids`) now sits where its hazard
    is, immediately before each FLATTEN (`_drop_child_texts()` in the snap's
    exotic-transform fallback; the STexts clear in the layer merge), so a
    flattened GDS still carries only the top's own labels -- the 5,655-pin
    measurement below -- while the shipped hierarchical GDS keeps the library
    cells' pin labels (N6: 0 of 31 cells labelled before, 31 of 31 after).
    Behaviour is pinned in test_n6_shipped_std_cells_keep_their_pin_labels."""
    s = R._GDS_GRID_SNAP_PY
    assert "is_text()" in s, "the snap pass must carry texts across Region"
    assert "_top_ids = set(c.cell_index() for c in ly.top_cells())" in s
    body = s.split("def _snap_local_shapes")[1].split("return n")[0]
    assert "_texts = [s_.text for s_ in sh.each() if s_.is_text()]" in body
    i_drop = s.index("_drop_child_texts()\n    for tc in ly.top_cells():")
    assert i_drop < s.index("tc.flatten(-1, True)", i_drop), (
        "a flatten must be preceded by the child-text clear")


def test_layer_merge_carries_texts():
    s = R._GDS_LAYER_MERGE_PY
    assert "is_text()" in s, "the merge pass must carry texts across Region"
    assert "sh.insert(_tt)" in s


def test_both_passes_read_text_values_before_clear():
    """A Shape handle does not survive sh.clear(); reading one aborts."""
    for s in (R._GDS_GRID_SNAP_PY, R._GDS_LAYER_MERGE_PY):
        i_collect = s.index("s_.text for s_ in sh.each()")
        i_clear = s.index("sh.clear()", i_collect)
        i_restore = s.index("sh.insert(_tt)", i_clear)
        assert i_collect < i_clear < i_restore, (
            "collect text VALUES -> clear -> reinsert, in that order")
        assert "s_.dup()" not in s, "copy the Text value, not the Shape handle"


def test_grid_snap_snaps_the_label_anchor():
    s = R._GDS_GRID_SNAP_PY
    assert "_tr.disp = pya.Vector(_snap_dbu(_tr.disp.x)," in s, (
        "a restored label must land on the same grid as the polygons")
    body = s.split("for _tt in _texts:")[1].split("n += 1")[0]
    assert "pya.Trans(" not in body, (
        "set only the displacement; do not rebuild rotation/mirror")


def test_layer_merge_does_not_move_labels():
    """Merging is a polygon operation and says nothing about labels."""
    body = R._GDS_LAYER_MERGE_PY.split("for _tt in _texts:")[1]
    assert "_snap_dbu" not in body and "disp" not in body


def test_label_layer_and_map_defaults_are_untouched():
    """Input completeness, not a layer-map, deck or waiver change."""
    s = R._GDS_STREAMOUT_PY
    for never in ("labels_datatype", "labels_suffix",
                  "lef_labels_datatype", "lef_labels_suffix"):
        assert f"_cfg.{never}" not in s
