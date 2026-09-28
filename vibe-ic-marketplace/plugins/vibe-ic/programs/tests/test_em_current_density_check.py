#!/usr/bin/env python3
"""Tests for em_current_density_check.py — REAL EM current-density sign-off.

Covers the four required cases:
  (a) synthetic EM report all under Jmax               → PASS
  (b) one segment over Jmax                            → FAIL (names net/layer/
                                                          density-vs-limit)
  (c) absent EM report                                 → SKIPPED, never PASS
  (d) absent Jmax table                                → SKIPPED, never PASS

Plus the tech-LEF Jmax path and the "report present but nothing maps to a
Jmax layer" §4.05 negative. rc contract: 0 PASS / 1 FAIL / 2 arg / 3 SKIPPED.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "em_current_density_check.py"

# A per-width Jmax of 2.8 mA/um on met1 (t=0.35um) mirrors sky130 metal.
JMAX = {
    "layers": {
        "met1": {"kind": "routing", "thickness_um": 0.35, "width_um": 0.14,
                 "jmax_mA_per_um": 2.8},
        "met2": {"kind": "routing", "thickness_um": 0.35, "width_um": 0.14,
                 "jmax_A_per_um2": 8.0e-3},
        "via1": {"kind": "cut", "jmax_mA_per_cut": 0.29},
    }
}

# sky130-style tech-LEF fragment (routing + cut layers with DCCURRENTDENSITY).
TECH_LEF = """
LAYER met1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  WIDTH 0.14 ;
  THICKNESS 0.35 ;
  DCCURRENTDENSITY AVERAGE 2.8 ; # mA/um Iavg_max
END met1

LAYER via1
  TYPE CUT ;
  DCCURRENTDENSITY AVERAGE 0.29 ; # mA per via
END via1
"""

CSV_HEADER = ("Node0 Layer,Node0 X location,Node0 Y location,"
              "Node1 Layer,Node1 X location,Node1 Y location,Current\n")


def _run(*args) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROG), *[str(a) for a in args]]
    # The synthetic producer supplies placed conductor geometry separately,
    # as the real Step-25 OpenROAD producer does.
    if args and Path(args[0]).name == "em_segments.csv":
        geom = Path(args[0]).with_name("em_pg_geometry.tsv")
        if geom.is_file():
            cmd.extend(("--pg-geometry", str(geom)))
    return subprocess.run(cmd, capture_output=True, text=True)


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _jmax(tmp_path) -> Path:
    return _write(tmp_path / "jmax.json", JMAX)


def _csv(tmp_path, rows) -> Path:
    body = CSV_HEADER + "".join(
        f"{l},0,0,{l},1,0,{c}\n" for (l, c) in rows)
    geom = ("net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n" +
            "".join(f"{net}\t{layer}\t0\t-0.07\t1\t0.07\tpg_port\n"
                    for net in ("unknown", "VPWR")
                    for layer in ("met1", "met2")))
    _write(tmp_path / "em_pg_geometry.tsv", geom)
    return _write(tmp_path / "em_segments.csv", body)


# --------------------------------------------------------------- (a) PASS

def test_all_under_jmax_pass(tmp_path):
    # met1 Jmax per-width = 2.8mA/um = 2.8e-3 A/um. width=0.14um →
    # limit current ~= 3.92e-4 A. Currents here are ~1e-5 A → far under.
    csv = _csv(tmp_path, [("met1", 1.0e-5), ("met1", 3.0e-5), ("met2", 2.0e-5)])
    r = _run(csv, "--jmax", _jmax(tmp_path), "--json", tmp_path / "o.json")
    assert r.returncode == 0, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "PASS"
    assert rep["pass"] is True
    assert rep["summary"]["segments_screened"] == 3
    assert rep["summary"]["worst_case_lifetime_ratio"] is not None


def test_all_under_jmax_pass_via_tech_lef(tmp_path):
    csv = _csv(tmp_path, [("met1", 1.0e-5), ("met1", 2.0e-5)])
    lef = _write(tmp_path / "tech.lef", TECH_LEF)
    r = _run(csv, "--tech-lef", lef, "--json", tmp_path / "o.json")
    assert r.returncode == 0, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "PASS"
    assert "met1" in rep["summary"]["jmax_layers"]


# --------------------------------------------------------------- (b) FAIL

def test_one_segment_over_jmax_fail(tmp_path):
    # met1 limit current ≈ 2.8e-3 * 0.14 = 3.92e-4 A. 5e-4 A is over.
    csv = _csv(tmp_path, [("met1", 1.0e-5), ("met1", 5.0e-4), ("met2", 2.0e-5)])
    r = _run(csv, "--jmax", _jmax(tmp_path), "--net", "VPWR",
             "--json", tmp_path / "o.json")
    assert r.returncode == 1, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "FAIL"
    assert rep["pass"] is False
    assert rep["offender_count"] >= 1
    off = rep["offenders"][0]
    # must name net + layer + density-vs-limit
    assert off["net"] == "VPWR"
    assert off["layer"] == "met1"
    assert off["utilization"] >= (1.0 - rep["margin"])
    assert off["limit"] > 0 and off["value"] >= off["limit"] * (1.0 - rep["margin"])
    msg = " ".join(f["message"] for f in rep["findings"]
                   if f["rule"] == "EM_CURRENT_DENSITY_OVER_JMAX")
    assert "met1" in msg and "VPWR" in msg and "Jmax" in msg


# ------------------------------------------- (c) §4.05: absent EM report

def test_absent_em_report_skipped_not_pass(tmp_path):
    missing = tmp_path / "does_not_exist"  # nothing discovered
    r = _run(missing, "--jmax", _jmax(tmp_path), "--json", tmp_path / "o.json")
    assert r.returncode == 3, r.stdout          # SKIPPED, never PASS
    assert r.returncode != 0
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "SKIPPED"
    assert rep["pass"] is False
    assert rep["skip_reason"] == "em_report_absent"


def test_empty_dir_em_report_skipped(tmp_path):
    empty = tmp_path / "reports"
    empty.mkdir()
    r = _run(empty, "--jmax", _jmax(tmp_path))
    assert r.returncode == 3, r.stdout
    assert '"verdict": "SKIPPED"' in r.stdout
    assert '"pass": false' in r.stdout


# ------------------------------------------- (d) §4.05: absent Jmax table

def test_absent_jmax_table_skipped_not_pass(tmp_path):
    csv = _csv(tmp_path, [("met1", 1.0e-5)])   # a real, all-under report
    r = _run(csv, "--json", tmp_path / "o.json")  # NO --jmax / --tech-lef
    assert r.returncode == 3, r.stdout          # SKIPPED (no reference), not PASS
    assert r.returncode != 0
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "SKIPPED"
    assert rep["pass"] is False
    assert rep["skip_reason"] == "jmax_reference_absent"


def test_jmax_path_missing_file_skipped(tmp_path):
    csv = _csv(tmp_path, [("met1", 1.0e-5)])
    r = _run(csv, "--jmax", tmp_path / "nope.json")
    assert r.returncode == 3, r.stdout
    assert '"verdict": "SKIPPED"' in r.stdout


def test_lef_without_current_density_skipped(tmp_path):
    csv = _csv(tmp_path, [("met1", 1.0e-5)])
    lef = _write(tmp_path / "bare.lef",
                 "LAYER met1\n  TYPE ROUTING ;\n  WIDTH 0.14 ;\nEND met1\n")
    r = _run(csv, "--tech-lef", lef)
    assert r.returncode == 3, r.stdout   # no DCCURRENTDENSITY → no reference


# --------------------------- §4.05: report+jmax present but nothing maps

def test_report_present_but_no_layer_match_skipped(tmp_path):
    # Segments only on met9, Jmax only knows met1/met2/via1 → cannot judge.
    csv = _csv(tmp_path, [("met9", 1.0e-5), ("met9", 2.0e-5)])
    r = _run(csv, "--jmax", _jmax(tmp_path), "--json", tmp_path / "o.json")
    assert r.returncode == 3, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "SKIPPED"
    assert rep["pass"] is False
    assert rep["skip_reason"] == "no_segment_maps_to_jmax_reference"


# --------------------------- extra: via/cut per-cut screening + JSON input

def test_via_cut_over_limit_fail_json_segments(tmp_path):
    # via1 per-cut limit = 0.29mA = 2.9e-4 A; 4e-4 A over.
    seg = _write(tmp_path / "em.json", {
        "power_nets": ["VPWR"],
        "segments": [
            {"net": "VPWR", "layer0": "met1", "layer1": "via1",
             "current_A": 4.0e-4},
        ],
    })
    r = _run(seg, "--jmax", _jmax(tmp_path), "--json", tmp_path / "o.json")
    assert r.returncode == 1, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "FAIL"
    assert rep["offenders"][0]["basis"] == "per_cut"


def test_margin_makes_marginal_segment_fail(tmp_path):
    # Choose a current at ~85% of Jmax; margin 0.2 requires <80% → FAIL.
    # met1 limit current = 2.8e-3 * 0.14 = 3.92e-4 A; 85% = 3.332e-4 A.
    csv = _csv(tmp_path, [("met1", 3.332e-4)])
    r = _run(csv, "--jmax", _jmax(tmp_path), "--margin", "0.2",
             "--json", tmp_path / "o.json")
    assert r.returncode == 1, r.stdout
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "FAIL"
    # same segment PASSES with margin 0.0 (still strictly under Jmax)
    r2 = _run(csv, "--jmax", _jmax(tmp_path), "--margin", "0.0")
    assert r2.returncode == 0, r2.stdout


def test_bad_margin_arg_error(tmp_path):
    csv = _csv(tmp_path, [("met1", 1.0e-5)])
    r = _run(csv, "--jmax", _jmax(tmp_path), "--margin", "1.5")
    assert r.returncode == 2


VIA_TECH_LEF = """
LAYER lower
  TYPE ROUTING ;
  WIDTH 1 ;
  DCCURRENTDENSITY AVERAGE 1 ;
END lower
LAYER cut
  TYPE CUT ;
  DCCURRENTDENSITY AVERAGE 0.18 ;
END cut
LAYER upper
  TYPE ROUTING ;
  WIDTH 1 ;
  DCCURRENTDENSITY AVERAGE 1 ;
END upper
"""


def _via_subject(tmp_path):
    lef = _write(tmp_path / "tech.lef", VIA_TECH_LEF)
    routed = _write(tmp_path / "routed.def", """
UNITS DISTANCE MICRONS 1000 ;
VIAS 1 ;
  - array + CUTSIZE 200 200 + LAYERS lower cut upper
    + ROWCOL 1 3 ;
END VIAS
SPECIALNETS 1 ;
  - SUPPLY + USE POWER
    + ROUTED lower 1000 ( 10000 10000 ) array
    NEW lower 1000 + SHAPE STRIPE ( 1000 1000 ) ( 2000 1000 ) ;
END SPECIALNETS
""")
    return lef, routed


def _via_csv(tmp_path, current, *, with_metal=False, via_at=10):
    rows = (f"lower,0,0,upper,{via_at},10,{current}\n" +
            ("lower,1,1,lower,2,1,0.00001\n" if with_metal else ""))
    return _write(tmp_path / "em_segments.csv", CSV_HEADER + rows)


def test_metal_endpoint_via_over_cut_jmax_fails(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _via_csv(tmp_path, 0.0007)
    r = _run(csv, "--tech-lef", lef, "--def-file", routed,
             "--net", "SUPPLY", "--json", tmp_path / "out.json")
    rep = json.loads((tmp_path / "out.json").read_text())
    assert r.returncode == 1, rep
    assert rep["verdict"] == "FAIL"
    assert rep["offenders"][0]["layer"] == "cut"
    assert rep["offenders"][0]["cut_count"] == 3
    assert rep["offenders"][0]["value_A_per_cut"] > 0.00018


def test_unmatched_via_with_screened_metal_is_not_measured(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _via_csv(tmp_path, 0.00001, with_metal=True, via_at=11)
    r = _run(csv, "--tech-lef", lef, "--def-file", routed,
             "--net", "SUPPLY", "--json", tmp_path / "out.json")
    rep = json.loads((tmp_path / "out.json").read_text())
    assert r.returncode == 3, rep
    assert rep["verdict"] == "NOT_MEASURED" and rep["pass"] is False
    assert rep["summary"]["segments_screened"] == 1
    assert rep["summary"]["segments_unscreened"] == 1
    assert rep["summary"]["unscreened_reasons"] == {
        "via_cut_geometry_unavailable": 1}


def test_clean_multi_cut_via_and_metal_pass(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _via_csv(tmp_path, 0.0004, with_metal=True)
    r = _run(csv, "--tech-lef", lef, "--def-file", routed,
             "--net", "SUPPLY", "--json", tmp_path / "out.json")
    rep = json.loads((tmp_path / "out.json").read_text())
    assert r.returncode == 0, rep
    assert rep["verdict"] == "PASS"
    assert rep["summary"]["segments_screened"] == 2
    assert rep["summary"]["segments_unscreened"] == 0
    assert rep["summary"]["per_layer"]["cut"]["segments"] == 1


def test_explicit_rect_via_counts_each_cut(tmp_path):
    lef = _write(tmp_path / "tech.lef", VIA_TECH_LEF)
    routed = _write(tmp_path / "routed.def", """
UNITS DISTANCE MICRONS 1000 ;
VIAS 1 ;
  - boxes + RECT lower ( -200 -200 ) ( 200 200 )
    + RECT cut ( -100 -100 ) ( 0 0 )
    + RECT cut ( 0 0 ) ( 100 100 )
    + RECT upper ( -200 -200 ) ( 200 200 ) ;
END VIAS
SPECIALNETS 1 ;
  - SUPPLY + USE POWER + ROUTED lower 1000 ( 10000 10000 ) boxes ;
END SPECIALNETS
""")
    csv = _via_csv(tmp_path, 0.0004)
    r = _run(csv, "--tech-lef", lef, "--def-file", routed,
             "--net", "SUPPLY", "--json", tmp_path / "out.json")
    rep = json.loads((tmp_path / "out.json").read_text())
    assert r.returncode == 1, rep
    assert rep["offenders"][0]["cut_count"] == 2


def test_same_def_placed_cut_center_bounds_virtual_via_to_one_cut(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _write(tmp_path / "em_segments.csv", CSV_HEADER +
                 "lower,9,10,upper,9.2,10,0.0002\n")
    missing = _run(csv, "--tech-lef", lef, "--def-file", routed,
                   "--net", "SUPPLY", "--json", tmp_path / "missing.json")
    missing_rep = json.loads((tmp_path / "missing.json").read_text())
    assert missing.returncode == 3
    assert missing_rep["verdict"] == "NOT_MEASURED"
    geom = _write(tmp_path / "em_pg_geometry.tsv",
                  "net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n"
                  "SUPPLY\tcut\t8.9\t9.9\t9.1\t10.1\tvia_cut\n")
    found = _run(csv, "--tech-lef", lef, "--def-file", routed,
                 "--pg-geometry", geom, "--net", "SUPPLY",
                 "--json", tmp_path / "found.json")
    rep = json.loads((tmp_path / "found.json").read_text())
    assert found.returncode == 1, rep
    assert rep["verdict"] == "FAIL"
    offender = rep["offenders"][0]
    assert offender["cut_count"] == 1
    assert offender["cut_count_source"] == "odb_placed_cut_single_cut_bound"
    assert offender["cut_geometry"][0]["cut_bbox_um"] == [8.9, 9.9, 9.1, 10.1]


def test_named_via_at_virtual_edges_orthogonal_corner_bounds_one_cut(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _write(tmp_path / "em_segments.csv", CSV_HEADER +
                 "lower,10,9,upper,9,10,0.0002\n")
    no_legs = _run(csv, "--tech-lef", lef, "--def-file", routed,
                   "--net", "SUPPLY", "--json", tmp_path / "no_legs.json")
    no_legs_rep = json.loads((tmp_path / "no_legs.json").read_text())
    assert no_legs.returncode == 3, no_legs_rep
    assert no_legs_rep["verdict"] == "NOT_MEASURED"
    assert no_legs_rep["summary"]["unscreened_reasons"] == {
        "via_cut_geometry_unavailable": 1}
    geom = _write(tmp_path / "em_pg_geometry.tsv",
                  "net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n"
                  "SUPPLY\tlower\t9.5\t8.5\t10.5\t10.5\tspecial_wire\n"
                  "SUPPLY\tupper\t8.5\t9.5\t10.5\t10.5\tspecial_wire\n")
    result = _run(csv, "--tech-lef", lef, "--def-file", routed,
                  "--pg-geometry", geom, "--net", "SUPPLY",
                  "--json", tmp_path / "corner.json")
    rep = json.loads((tmp_path / "corner.json").read_text())
    assert result.returncode == 1, rep
    assert rep["verdict"] == "FAIL"
    offender = rep["offenders"][0]
    assert offender["cut_count"] == 1
    assert offender["cut_count_source"] == "def_named_corner_via_single_cut_bound"
    assert offender["cut_geometry"][0]["via_center_um"] == [10.0, 10.0]
    assert offender["cut_geometry"][0]["metal_leg_widths_um"] == [1.0, 1.0]


def test_measured_metal_offender_precedes_unmeasured_via(tmp_path):
    lef, routed = _via_subject(tmp_path)
    csv = _write(tmp_path / "em_segments.csv", CSV_HEADER +
                 "lower,1,1,lower,2,1,0.002\n"
                 "lower,0,0,upper,11,10,0.00001\n")
    result = _run(csv, "--tech-lef", lef, "--def-file", routed,
                  "--net", "SUPPLY", "--json", tmp_path / "mixed.json")
    rep = json.loads((tmp_path / "mixed.json").read_text())
    assert result.returncode == 1, rep
    assert rep["verdict"] == "FAIL"
    assert rep["offender_count"] == 1
    assert rep["summary"]["segments_unscreened"] == 1
    assert rep["summary"]["unscreened_reasons"] == {
        "via_cut_geometry_unavailable": 1}
