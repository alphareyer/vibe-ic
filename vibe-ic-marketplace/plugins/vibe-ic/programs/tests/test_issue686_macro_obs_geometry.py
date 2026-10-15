"""#686 — nothing intersected emitted geometry with a placed macro's OBS.

MEASURED on a routed DEF from a run the flow called clean but for one unrelated
gap: 28 of 292 MET1 FOLLOWPIN segments run straight through a placed macro's
full-footprint MET1 obstruction, declared in the very LEF the run loaded.

SILENT BY CONSTRUCTION, which is the part worth naming. Every existing check is

  * a COUNT OF ATTACHMENTS — `PG_NET_OWNERSHIP_AUDIT: total=3337 no_net=1` tests
    whether a terminal has a net. A wire crossing a blockage is attached to
    exactly the right net. (Spelled `PG_CONNECT_AUDIT: unconnected=N` through
    v1.9.62, until vibe-ic#699 renamed it to what it measures.)
  * a GEOMETRIC DRC AGAINST THE PDK DECK — `drc_signoff.json: passed: true`,
    `detailed route: violation report: 0`. A macro obstruction is not in the PDK
    deck; it is in the macro's LEF.

A macro obstruction is neither, so it was invisible to all of them at once.

MEASURED after the fix, on the shape the issue describes:

    40 supply segments, 28 of them crossing a 100x60 macro   -> 28 findings
    one of those shortened to END INSIDE the macro           -> 27
    an asymmetric 100x20 obstruction, orient N               -> 10
    the same, orient E                                       -> 40
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

_spec = importlib.util.spec_from_file_location(
    "macro_obs_geometry_intersect_check",
    _PROGRAMS / "macro_obs_geometry_intersect_check.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["macro_obs_geometry_intersect_check"] = M
try:
    _spec.loader.exec_module(M)
except SystemExit:
    pass

_LEF = """
MACRO big_ip
  SIZE 100.0 BY 60.0 ;
  OBS
    LAYER OVERLAP ;
      RECT 0 0 100.0 60.0 ;
    LAYER MET1 ;
      RECT 0 0 100.0 60.0 ;
  END
END big_ip
"""


def _def(n_through=28, n_clear=12, orient="N", short_first=False):
    rows = []
    for i in range(n_through):
        y = 102000 + i * 2000
        x2 = 250000 if (short_first and i == 0) else 400000
        rows.append(f"- VDD ( * VDD ) + USE POWER + ROUTED MET1 140 + SHAPE "
                    f"FOLLOWPIN ( 100000 {y} ) ( {x2} {y} ) ;")
    for i in range(n_clear):
        y = 20000 + i * 2000
        rows.append(f"- VSS ( * VSS ) + USE GROUND + ROUTED MET1 140 + SHAPE "
                    f"FOLLOWPIN ( 100000 {y} ) ( 400000 {y} ) ;")
    return ("UNITS DISTANCE MICRONS 1000 ;\nCOMPONENTS 1 ;\n"
            f"- u_ip big_ip + FIXED ( 200000 100000 ) {orient} ;\n"
            f"END COMPONENTS\nSPECIALNETS {len(rows)} ;\n" + "\n".join(rows)
            + "\nEND SPECIALNETS\n")


# ── the finding ───────────────────────────────────────────────────────────
def test_it_finds_the_segments_that_span_the_obstruction():
    r = M.audit(_def(), [_LEF])
    assert len(r["findings"]) == 28
    assert all(f["followpin"] for f in r["findings"])
    assert r["special_segments"] == 40, "and the 12 rows below it are not flagged"


def test_a_segment_that_ends_inside_is_not_a_span():
    """LOAD-BEARING. A fragment near an edge is ordinary; flagging it would bury
    the real finding under noise, and a gate people scroll past enforces
    nothing."""
    r = M.audit(_def(short_first=True), [_LEF])
    assert len(r["findings"]) == 27


def test_LAYER_OVERLAP_is_not_treated_as_metal():
    """OVERLAP declares the macro's extent. Counting it would make every macro
    block every layer and the gate would fire on everything."""
    o = M.parse_macro_obs(_LEF)["big_ip"]
    assert [layer for layer, *_ in o["obs"]] == ["MET1"]


# ── orientation, with a control that can tell the difference ──────────────
_ASYM = """
MACRO ip
  SIZE 100.0 BY 20.0 ;
  OBS
    LAYER MET1 ;
      RECT 0 0 100.0 20.0 ;
  END
END ip
"""


def _asym_def(orient):
    rows = "\n".join(
        f"- VDD ( * VDD ) + USE POWER + ROUTED MET1 140 + SHAPE FOLLOWPIN "
        f"( 100000 {102000 + i * 2000} ) ( 400000 {102000 + i * 2000} ) ;"
        for i in range(40))
    return ("UNITS DISTANCE MICRONS 1000 ;\nCOMPONENTS 1 ;\n"
            f"- u ip + FIXED ( 200000 100000 ) {orient} ;\n"
            "END COMPONENTS\nSPECIALNETS 1 ;\n" + rows + "\nEND SPECIALNETS\n")


def test_orientation_changes_the_answer():
    """An ASYMMETRIC obstruction, because a square one gives the same count
    either way and would prove nothing. Ignoring orientation measures a rotated
    macro against an unrotated obstruction, and a fabricated finding is worse
    than none."""
    n = len(M.audit(_asym_def("N"), [_ASYM])["findings"])
    e = len(M.audit(_asym_def("E"), [_ASYM])["findings"])
    assert (n, e) == (10, 40), (n, e)


# ── it must never pass by finding nothing ─────────────────────────────────
def test_no_macro_declares_an_OBS_is_not_a_pass(tmp_path):
    lef = tmp_path / "m.lef"
    lef.write_text("MACRO plain\n  SIZE 10.0 BY 10.0 ;\nEND plain\n")
    d = tmp_path / "phase3/stage3/pnr"
    d.mkdir(parents=True)
    (d / "routed.def").write_text(_def())
    rc = M.main([str(tmp_path), "--macro-lef", str(lef)])
    assert rc == 2


def test_no_placed_instance_is_not_a_pass(tmp_path):
    lef = tmp_path / "m.lef"
    lef.write_text(_LEF)
    d = tmp_path / "phase3/stage3/pnr"
    d.mkdir(parents=True)
    (d / "routed.def").write_text(
        "UNITS DISTANCE MICRONS 1000 ;\nCOMPONENTS 0 ;\nEND COMPONENTS\n"
        "SPECIALNETS 0 ;\nEND SPECIALNETS\n")
    assert M.main([str(tmp_path), "--macro-lef", str(lef)]) == 2


def test_no_def_is_not_a_pass(tmp_path):
    assert M.main([str(tmp_path)]) == 2


# ── both exit codes on a real tree shape ──────────────────────────────────
def test_it_blocks_when_it_finds_one(tmp_path):
    lef = tmp_path / "m.lef"
    lef.write_text(_LEF)
    d = tmp_path / "phase3/stage3/pnr"
    d.mkdir(parents=True)
    (d / "routed.def").write_text(_def())
    assert M.main([str(tmp_path), "--macro-lef", str(lef)]) == 1


def test_it_passes_when_nothing_crosses(tmp_path):
    """THE ACCEPT CASE. A design whose supply metal respects the obstruction
    must go green, or the gate is unusable."""
    lef = tmp_path / "m.lef"
    lef.write_text(_LEF)
    d = tmp_path / "phase3/stage3/pnr"
    d.mkdir(parents=True)
    (d / "routed.def").write_text(_def(n_through=0, n_clear=12))
    assert M.main([str(tmp_path), "--macro-lef", str(lef)]) == 0


def _spatial_fixture(orient, crossing):
    """Neutral CORE OBS and many irrelevant, same-layer H/V supply paths."""
    lef = """MACRO neutral_core
  CLASS CORE ;
  SIZE 10 BY 4 ;
  OBS
    LAYER Metal1 ;
      RECT 0 0 8 2 ;
      RECT 0 0 8 2 ;
    LAYER MET7 ;
      RECT 0 0 8 2 ;
  END
END neutral_core
"""
    bx1, by1 = 10000, 20000
    bx2, by2 = ((18000, 22000) if orient == "N" else (12000, 28000))
    mx, my = (bx1 + bx2) // 2, (by1 + by2) // 2
    paths = []
    hits = []

    def add(layer, seg, *, hit=False, shape="STRIPE"):
        paths.append((layer, seg, shape))
        if hit:
            hits.append({"inst": "u_core", "master": "neutral_core",
                         "layer": "Metal1", "net": "supply",
                         "followpin": shape == "FOLLOWPIN", "shape": shape,
                         "seg": list(seg)})

    for i in range(128):
        far = -10000 - 10 * i
        add("Metal1", (0, far, 90000, far))
        add("mEtAl1", (far, 0, far, 90000))
    if crossing:
        add("metal1", (0, my, 90000, my), hit=True)
        add("METAL1", (mx, 0, mx, 90000), hit=True, shape="FOLLOWPIN")
        add("metal1", (0, my, 90000, my), hit=True)  # exact duplicate
    # Strict longitudinal boundaries and internal endpoints never span.
    add("Metal1", (bx1, my, bx2 + 1000, my))
    add("Metal1", (bx1 - 1000, my, bx2 - 1, my))
    add("Metal1", (mx, by1, mx, by2 + 1000))
    add("Metal1", (mx, by1 - 1000, mx, by2 - 1))
    if crossing:
        # The transverse interval is inclusive on both ends, for BOTH axes.
        add("Metal1", (0, by1, 90000, by1), hit=True)
        add("Metal1", (bx1, 0, bx1, 90000), hit=True)
        add("Metal1", (0, by2, 90000, by2), hit=True)
        add("Metal1", (bx2, 0, bx2, 90000), hit=True)
    add("MET9", (0, my, 90000, my))  # wrong layer, never a finding
    routes = []
    for i, (layer, (x1, y1, x2, y2), shape) in enumerate(paths):
        head = "+ ROUTED" if i == 0 else "NEW"
        routes.append(f"  {head} {layer} 0 + SHAPE {shape} "
                      f"( {x1} {y1} ) ( {x2} {y2} )")
    design = ("UNITS DISTANCE MICRONS 1000 ;\nCOMPONENTS 1 ;\n"
              f"- u_core neutral_core + PLACED ( 10000 20000 ) {orient} ;\n"
              "END COMPONENTS\nSPECIALNETS 1 ;\n"
              "- supply ( * P ) + USE POWER\n" + "\n".join(routes)
              + "\n ;\nEND SPECIALNETS\nEND DESIGN\n")
    return design, lef, hits * 2, len(paths)


@pytest.mark.parametrize("orient", ["N", "E"])
@pytest.mark.parametrize("crossing", [True, False], ids=["crossing", "clean"])
def test_spatial_fixture_preserves_complete_order_and_metadata(orient, crossing):
    design, lef, findings, segments = _spatial_fixture(orient, crossing)
    report = M.audit(design, [lef])
    assert report["findings"] == findings
    assert report["findings_count"] == len(findings)
    assert report["special_segments"] == segments
    assert report["obs_rects_consumed"] == 3
    assert report["count_is_floor"] is False
    assert report["count_floor_reasons"] == []
    assert report["findings_by_layer"] == ({"metal1": 14} if crossing else {})
    assert report["obs_layers_compared"] == ["met7", "metal1"]
    assert report["obs_layer_applicability"]["met7"]["verdict"] == \
        "NOT_APPLICABLE_BY_STRUCTURE"


@pytest.mark.parametrize("orient", ["N", "E"])
@pytest.mark.parametrize("crossing", [True, False], ids=["crossing", "clean"])
def test_same_layer_irrelevant_paths_do_not_reach_spans(
        monkeypatch, orient, crossing):
    design, lef, findings, _ = _spatial_fixture(orient, crossing)
    original = M.spans
    calls = []

    def counted(segment, box):
        calls.append((segment, box))
        return original(segment, box)

    monkeypatch.setattr(M, "spans", counted)
    assert M.audit(design, [lef])["findings"] == findings
    print(f"ORIGINAL_SPANS_CALLS orient={orient} crossing={crossing}: {len(calls)}")
    assert len(calls) <= (22 if crossing else 8)


@pytest.mark.parametrize("orient", ["N", "E"])
@pytest.mark.parametrize("crossing", [True, False], ids=["crossing", "clean"])
def test_spatial_fixture_cli_matches_the_whole_audit(tmp_path, orient, crossing):
    design, lef, _, _ = _spatial_fixture(orient, crossing)
    abstract = tmp_path / "neutral.lef"
    abstract.write_text(lef)
    routed = tmp_path / "routed.def"
    routed.write_text(design)
    output = tmp_path / "report.json"
    rc = M.main([str(tmp_path), "--def", str(routed),
                 "--macro-lef", str(abstract), "--json", str(output)])
    assert rc == (1 if crossing else 0)
    assert json.loads(output.read_text()) == M.audit(design, [lef], [str(abstract)])
