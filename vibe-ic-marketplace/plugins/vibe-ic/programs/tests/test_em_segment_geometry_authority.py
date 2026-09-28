"""The Jmax gate measures each conductor, including abutted pad PG ports."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import em_current_density_check as E  # noqa: E402


def _fixture(tmp_path, geometry: str):
    csv = tmp_path / "em_segments.csv"
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,0.5,10,Metal3,3.5,10,0.00026\n")
    jmax = tmp_path / "jmax.json"
    jmax.write_text(json.dumps({"layers": {"Metal3": {
        "kind": "routing", "width_um": 0.28,
        "jmax_mA_per_um": 0.67}}}))
    geom = tmp_path / "em_pg_geometry.tsv"
    geom.write_text("net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n"
                    + geometry)
    return csv, jmax, geom


def test_abutted_pg_ports_prove_seven_micron_pad_rail(tmp_path):
    csv, jmax, geom = _fixture(tmp_path, "".join(
        f"VDD\tMetal3\t{x}\t6.5\t{x+1}\t13.5\tpg_port\n"
        for x in range(4)))
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    edge = report["worst_segments"][0]
    assert edge["width_um"] == 7.0
    assert edge["width_source"] == "odb_pg_metal_geometry"
    assert edge["geometry_sources"] == ["pg_port"]
    assert edge["utilization"] < 0.9


def test_same_current_on_real_narrow_special_wire_fails(tmp_path):
    csv, jmax, _ = _fixture(tmp_path, "")
    routed = tmp_path / "routed.def"
    routed.write_text(
        "UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n"
        "- VDD + USE POWER + ROUTED Metal3 280 + SHAPE STRIPE "
        "( 0 10000 ) ( 4000 10000 ) ;\nEND SPECIALNETS\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 def_path=routed)
    assert verdict == "FAIL"
    assert report["offenders"][0]["width_um"] == pytest.approx(0.28)
    assert report["offenders"][0]["utilization"] > 1.0


def test_missing_geometry_is_not_measured_even_with_lef_default(tmp_path):
    csv, jmax, geom = _fixture(tmp_path, "")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["pass"] is False
    assert report["summary"]["segments_screened"] == 0
    assert report["not_measured_segments"][0]["status"] == "NOT_MEASURED"
    assert report["not_measured_segments"][0]["reason"] == (
        "psm_edge_without_same_net_metal")


def test_port_gap_cannot_inherit_neighbor_width(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t6.5\t1\t13.5\tpg_port\n"
                  "VDD\tMetal3\t2\t6.5\t4\t13.5\tpg_port\n")
    verdict, _ = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                            pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"


def test_one_measured_edge_cannot_make_missing_edge_pass(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t6.5\t4\t13.5\tpg_port\n")
    with csv.open("a") as fh:
        fh.write("VDD,Metal3,5,10,Metal3,6,10,0.000001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["summary"]["segments_screened"] == 1
    assert report["summary"]["segments_unscreened"] == 1


def test_macro_obs_without_net_owned_port_proves_no_width(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t6.5\t4\t13.5\tmacro_obs\n")
    verdict, _ = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                            pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"


def test_producer_collects_transformed_lef_pg_ports():
    assert "getITerms" in E.PG_GEOMETRY_TCL
    assert "getGeometries" in E.PG_GEOMETRY_TCL
    assert "pg_port" in E.PG_GEOMETRY_TCL
    assert "stdcell_pg_port" in E.PG_GEOMETRY_TCL


def test_edge_on_abutting_port_boundary_has_real_cross_section(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t9\t4\t10\tpg_port\n"
                  "VDD\tMetal3\t0\t10\t4\t11\tpg_port\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    assert report["worst_segments"][0]["width_um"] == pytest.approx(2.0)


def test_diagonal_inside_one_real_metal_box_uses_perpendicular_chord(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t9\t4\t11\tspecial_wire\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,0.5,9.5,Metal3,1.5,10.5,0.00026\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    assert report["worst_segments"][0]["width_um"] == pytest.approx(1.41421356)


def test_virtual_diagonal_leaving_port_is_named_not_measured(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t9\t1\t11\tpg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,0.5,10,Metal3,1.5,12,0.00026\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["not_measured_segments"][0]["reason"] == (
        "psm_virtual_diagonal_leaves_pg_port")


def test_diagonal_rail_to_abutting_stdcell_port_uses_real_neck(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t10\t4\t11\tspecial_wire\n"
                  "VDD\tMetal3\t1.5\t8\t1.8\t10\tstdcell_pg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,1.5,10.5,Metal3,1.65,9,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    edge = report["worst_segments"][0]
    assert edge["width_um"] > 0
    assert edge["width_um"] == pytest.approx(0.3)
    assert set(edge["geometry_sources"]) == {"special_wire", "stdcell_pg_port"}


def test_diagonal_rail_to_port_with_gap_stays_not_measured(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t10\t4\t11\tspecial_wire\n"
                  "VDD\tMetal3\t1.5\t8\t1.8\t9.9\tstdcell_pg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,1.5,10.5,Metal3,1.65,9,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["not_measured_segments"][0]["reason"] == (
        "psm_virtual_diagonal_two_shapes_path_unproven")


def test_two_ports_joined_by_one_real_followpin_rail_use_contact_width(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t10\t4\t11\tspecial_wire\n"
                  "VDD\tMetal3\t1\t9\t1.3\t10\tstdcell_pg_port\n"
                  "VDD\tMetal3\t1.1\t11\t1.4\t12\tstdcell_pg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,1.15,9.5,Metal3,1.25,11.5,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    edge = report["worst_segments"][0]
    assert edge["width_um"] == pytest.approx(0.3)
    assert set(edge["geometry_sources"]) == {"special_wire", "stdcell_pg_port"}


def test_virtual_diagonal_across_abutting_pad_rails_and_port(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t9\t1\t12\tpg_port\n"
                  "VDD\tMetal3\t1\t9\t2\t12\tpg_port\n"
                  "VDD\tMetal3\t2\t9\t3\t12\tother_pg_port\n"
                  "VDD\tMetal3\t3\t10\t6\t10.38\tother_pg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,0.5,10.5,Metal3,4,10.2,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "PASS"
    assert report["worst_segments"][0]["width_um"] == pytest.approx(0.38)


def test_virtual_diagonal_with_different_contact_widths_is_unmeasured(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t10\t4\t11\tspecial_wire\n"
                  "VDD\tMetal3\t1\t8\t1.3\t10\tstdcell_pg_port\n"
                  "VDD\tMetal3\t1.2\t8\t1.3\t10\tvia_metal\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,1.1,10.5,Metal3,1.25,9,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["not_measured_segments"][0]["reason"] == (
        "psm_virtual_diagonal_ambiguous_contact_width")


def test_virtual_diagonal_point_touch_proves_no_conductor_width(tmp_path):
    csv, jmax, geom = _fixture(
        tmp_path, "VDD\tMetal3\t0\t10\t1\t11\tspecial_wire\n"
                  "VDD\tMetal3\t1\t9\t2\t10\tstdcell_pg_port\n")
    csv.write_text(
        "Net,Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "VDD,Metal3,0.5,10.5,Metal3,1.5,9.5,0.00001\n")
    verdict, report = E.evaluate(csv, jmax, None, 0.1, 2.0, None, 20,
                                 pg_geometry_path=geom)
    assert verdict == "NOT_MEASURED"
    assert report["not_measured_segments"][0]["reason"] == (
        "psm_virtual_diagonal_two_shapes_path_unproven")
