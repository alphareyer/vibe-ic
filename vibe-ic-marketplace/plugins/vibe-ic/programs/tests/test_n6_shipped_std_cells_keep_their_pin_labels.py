"""N6 — the shipped GDS's standard cells carried NO pin labels.

MEASURED (spm x gf180mcuD, same-RTL arm, image vibeic-eda 0.3.83
`sha256:7a01d48e…`): the delivered `spm.gds` has 36 leaf cells and 0 of them
carry a single text; LibreLane's stream of the same library labels all 20 of
its cells (34/10 pin labels, 21/10 and 204/10 well labels, 63/63 library
texts). An external Magic-extract + Netgen LVS of the vibe-ic GDS therefore saw
anonymous device nodes (`a_36_68#`, `VSUBS`, `w_n86_352#`) where every cell's
pins are, and failed pin matching in 28 cells.

WHERE THE LABELS WENT. Not the stream: the runner's own Magic stream-out of the
run's routed DEF, re-run in the same image, labels 31 of 31 cells. The
manufacturing-grid pass (`_GDS_GRID_SNAP_PY`, #600) rebuilds every layer of
every cell through `pya.Region`, which carries polygons only, and carried back
the TOP cell's texts alone -- measured on that same GDS: 31 labelled cells in,
0 out. The child texts were dropped on purpose (#2181): a FLATTEN lifts a
library cell's labels into the top, where `top_lvl_pins` promotes each labelled
net to a formal pin (5,655 on spm). That hazard is real but it belongs to the
flatten, and the shipped spm GDS is never flattened (the Magic path runs no
layer merge; the merge flattens only when its deck probe says it must).

THE RULE NOW. The snap keeps EVERY cell's texts. A child cell's texts are
cleared in the working layout immediately before each flatten -- the snap's
exotic-transform fallback and the layer merge's flatten branch -- and nowhere
else, so a flattened GDS still carries only the top's own labels (byte-for-byte
the old behaviour) while a hierarchical one keeps the library's pin labels.

The behavioural tests execute the runner's own scripts under `pya` and skip
where KLayout's Python module is absent; run them in the pinned image. The
structural test below them runs everywhere.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

try:
    import pya  # noqa: F401
except ImportError:                               # the host has no KLayout
    pya = None

PIN = (34, 10)          # a label-purpose layer, as a library ships it
MET = (34, 0)           # the drawing layer the pin label sits on
TOPLBL = (36, 10)


def _need_pya():
    return pytest.importorskip("pya")


def _library_design(tmp_path, *, transform=None) -> Path:
    """A top with one instance of a 'library cell' that carries pin labels,
    one of them off the 5 nm grid, plus the top's own port label."""
    ly = pya.Layout()
    ly.dbu = 0.001
    met = ly.layer(*MET)
    pin = ly.layer(*PIN)
    top_l = ly.layer(*TOPLBL)
    cell = ly.create_cell("lib__inv_1")
    cell.shapes(met).insert(pya.Box(0, 0, 400, 1000))
    cell.shapes(pin).insert(pya.Text("A", pya.Trans(pya.Vector(100, 500))))
    cell.shapes(pin).insert(pya.Text("ZN", pya.Trans(pya.Vector(303, 700))))
    top = ly.create_cell("chip")
    top.insert(pya.CellInstArray(
        cell.cell_index(), transform or pya.Trans(pya.Vector(1000, 0))))
    top.shapes(top_l).insert(pya.Text("clk", pya.Trans(pya.Vector(0, 0))))
    p = tmp_path / "in.gds"
    ly.write(str(p))
    return p


def _run(script: str, gds_in: Path, gds_out: Path, **env) -> None:
    saved = {k: os.environ.get(k) for k in ("GDS_IN", "GDS_OUT", "MFG_GRID_UM",
                                            "KEEP_HIERARCHY", *env)}
    try:
        os.environ.update(GDS_IN=str(gds_in), GDS_OUT=str(gds_out),
                          MFG_GRID_UM="0.005")
        os.environ.pop("KEEP_HIERARCHY", None)
        os.environ.update({k: str(v) for k, v in env.items()})
        exec(compile(script, "<runner script>", "exec"), {"__name__": "__main__"})
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _texts(gds: Path):
    """{cell name: sorted [(string, x, y)]} for every cell with a text."""
    ly = pya.Layout()
    ly.read(str(gds))
    out = {}
    for c in ly.each_cell():
        got = []
        for li in ly.layer_indexes():
            for s in c.shapes(li).each():
                if s.is_text():
                    got.append((s.text_string, s.text.trans.disp.x,
                                s.text.trans.disp.y))
        if got:
            out[c.name] = sorted(got)
    return out


def test_the_grid_snap_keeps_every_cells_pin_labels(tmp_path):
    """RED ON MAIN: main's snap returns the library cell with 0 labels."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_GRID_SNAP_PY, _library_design(tmp_path), out)
    got = _texts(out)
    assert got.get("lib__inv_1") == [("A", 100, 500), ("ZN", 305, 700)], (
        "the grid snap must carry a library cell's pin labels through its "
        "Region round-trip (the off-grid anchor 303 snapped to 305, "
        f"nothing else moved); got {got}")
    assert got.get("chip") == [("clk", 0, 0)], got


def test_the_snap_flatten_fallback_does_not_lift_library_labels(tmp_path):
    """A non-orthogonal placement makes the snap flatten. A flattened
    library label would become a TOP label, which is the 5,655-pin shape."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_GRID_SNAP_PY,
         _library_design(tmp_path, transform=pya.ICplxTrans(1.0, 30.0, False,
                                                            1000, 0)),
         out)
    got = _texts(out)
    assert list(got) == ["chip"] and got["chip"] == [("clk", 0, 0)], (
        f"after a flatten only the top's own labels may remain: {got}")


def test_the_layer_merge_flatten_does_not_lift_library_labels(tmp_path):
    """RED ON MAIN: main's merge flattens a labelled library cell and puts
    its pin labels in the top."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_LAYER_MERGE_PY, _library_design(tmp_path), out)
    got = _texts(out)
    assert list(got) == ["chip"] and got["chip"] == [("clk", 0, 0)], (
        f"a flattened GDS carries only the top's own labels: {got}")


def test_the_layer_merge_keeping_hierarchy_keeps_library_labels(tmp_path):
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_LAYER_MERGE_PY, _library_design(tmp_path), out,
         KEEP_HIERARCHY="1")
    got = _texts(out)
    assert got.get("lib__inv_1") == [("A", 100, 500), ("ZN", 303, 700)], got
    assert got.get("chip") == [("clk", 0, 0)], got


def _flatten_guarded(script: str, clear_marker: str) -> None:
    """Every `.flatten(` in `script` sits after a child-text clear that is
    closer to it than any earlier flatten."""
    pos = 0
    n = 0
    while True:
        i = script.find(".flatten(", pos)
        if i < 0:
            break
        j = script.rfind(clear_marker, 0, i)
        k = script.rfind(".flatten(", 0, i)
        assert j >= 0 and j > k, (
            f"a flatten at offset {i} is not preceded by `{clear_marker}`; a "
            "library cell's pin labels would be lifted into the top")
        n += 1
        pos = i + 1
    assert n, "no flatten found; this guard would be vacuous"


def test_every_flatten_is_preceded_by_the_child_text_clear():
    """Host-runnable: the top-only rule lives at each flatten, and the snap's
    per-cell text collection is unconditional."""
    # The CALL (`...()` then a newline), never the `def _drop_child_texts():`
    # line, which would satisfy a bare name search from above every flatten.
    _flatten_guarded(R._GDS_GRID_SNAP_PY, "_drop_child_texts()\n")
    _flatten_guarded(R._GDS_LAYER_MERGE_PY, "clear(pya.Shapes.STexts)")
    body = R._GDS_GRID_SNAP_PY.split("def _snap_local_shapes")[1].split(
        "return n")[0]
    assert "_texts = [s_.text for s_ in sh.each() if s_.is_text()]" in body, (
        "the snap must collect EVERY cell's texts, not only the top's")
