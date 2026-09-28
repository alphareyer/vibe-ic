"""Owner rule 2(c): an off-chip port needs a measured zero-route geometry proof."""

import sta_annotation_population as population
from librelane_signoff import _abutted_geometry_census


def _def(tmp_path, use="SIGNAL"):
    path = tmp_path / "chip_top.def"
    path.write_text(
        "VERSION 5.8 ;\nDESIGN chip_top ;\n"
        "COMPONENTS 1 ;\n- u_pad fixture_io + PLACED ( 0 0 ) N ;\n"
        "END COMPONENTS\n"
        "PINS 1 ;\n- p + NET p + DIRECTION INPUT + USE SIGNAL ;\nEND PINS\n"
        f"NETS 1 ;\n- p ( PIN p ) ( u_pad PAD ) + USE {use} ;\nEND NETS\n"
        "END DESIGN\n")
    return path


def _report(drivers):
    return ("STA_LINK_INSTANCE u_pad RESOLVED\n"
            f"Found {len(drivers)} unannotated drivers.\n" +
            "".join(f"{name}\n" for name in drivers) +
            "Found 0 partially unannotated drivers.\n")


def test_port_and_pad_need_their_own_native_zero_route_proof(tmp_path):
    report = _report(["p", "u_pad/PAD"])
    args = dict(io_masters={"fixture_io"}, strict_geometry=True)
    missing = population.classify(report, _def(tmp_path), **args)
    assert missing["complete"] is False
    proved = population.classify(report, _def(tmp_path),
                                  abutted_nets={"p"}, **args)
    assert proved["complete"] is True
    assert all(row["classification"] == population.OFF_DIE
               for row in proved["drivers"])


def test_native_census_requires_exact_complete_named_membership():
    log = ("STA_ANNOTATION_ABUTTED_NET: p\n"
           "STA_ANNOTATION_UNROUTED_NETS: 0\n"
           "STA_ANNOTATION_ABUTTED_NETS: 1\n"
           "STA_ANNOTATION_UNROUTED_SHAPE_BLIND: 0\n"
           "STA_ANNOTATION_CENSUS_COMPLETE: 1\n")
    assert _abutted_geometry_census(log)["abutted_nets"] == ["p"]
    assert _abutted_geometry_census(log.replace("NETS: 1", "NETS: 2"))["complete"] is False
    assert _abutted_geometry_census(log.replace("UNROUTED_NETS: 0", "UNROUTED_NETS: 1"))["complete"] is False
    assert _abutted_geometry_census(log.replace("CENSUS_COMPLETE: 1", "CENSUS_COMPLETE: 0"))["complete"] is False


def test_pg_exclusion_needs_independent_no_arc_proof(tmp_path):
    path = _def(tmp_path, use="POWER")
    report = _report(["u_pad/PAD"])
    unproved = population.classify(report, path, strict_geometry=True,
                                   io_masters={"fixture_io"})
    assert unproved["complete"] is False
    proved = population.classify(report, path, strict_geometry=True,
                                 pg_arc_free={"u_pad/PAD"},
                                 io_masters={"fixture_io"})
    assert proved["complete"] is True
    assert proved["drivers"][0]["classification"] == "EXPLICIT_PG_NOT_SIGNAL_PARASITICS"
