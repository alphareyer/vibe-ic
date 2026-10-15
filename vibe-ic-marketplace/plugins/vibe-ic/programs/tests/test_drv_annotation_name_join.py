"""Annotation spelling must join the actual census without excusing missing RC."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_drv_signoff_judge as fixtures
from test_drv_signoff_judge import _synthetic_scene_profile  # noqa: F401
import drv_signoff_annotation as annotation
import drv_signoff_census as census
import drv_signoff_judge as drv


def _scene(tmp_path, monkeypatch, reported):
    # Supply valid neutral source artifacts before the existing bundle helper
    # binds their hashes into its stream/LVS and extraction records.
    original_file = fixtures._file

    def source_file(root, name, body):
        if name == "routed.def":
            body = "VERSION 5.8 ;\nNETS 0 ;\nEND NETS\nEND DESIGN\n"
        elif name == "library.lib":
            body = body.rsplit("}", 1)[0] + (
                "cell (supply) { pin (VDD) { direction : inout; } }\n}")
        return original_file(root, name, body)

    monkeypatch.setattr(fixtures, "_file", source_file)
    bundle = fixtures._bundle(tmp_path)
    scene = bundle["scenes"][0]
    folder = tmp_path / "census"
    lef = fixtures._file(tmp_path, "pins.lef", """MACRO supply
 CLASS PAD POWER ;
 PIN VDD
  DIRECTION INOUT ;
  USE POWER ;
 END VDD
END supply
MACRO marker
 CLASS ENDCAP TOPRIGHT ;
 PIN BUS
  DIRECTION INOUT ;
  USE SIGNAL ;
 END BUS
END marker
""")
    scene["linked_lefs"] = [lef]
    refs = {
        "pin_census_report": fixtures._file(folder, "pin_census.tsv",
            "u/Y\tpin\toutput\t1\tu\tlogic\tY\tn\tinput\t0.1\t0.1\t0\tX\t0\n"
            "u_core/_supply_/VDD\tpin\tinout\t0\tu_core/_supply_\tsupply\tVDD\tvdd\tinput\t0\t0\t0\tX\t0\n"
            "u_corner/marker/BUS\tpin\tunknown\t0\tu_corner/marker\tmarker\tBUS\t\tinput\t0\t0\t0\tX\t0\n"),
        "net_census_report": fixtures._file(folder, "net_census.rpt",
            "Net n\n Total capacitance: 0.1\n Number of drivers: 1\n"
            " Number of loads: 0\n Number of pins: 1\n\nDriver pins\n u/Y output (logic)\n\n"
            "Net vdd\n Total capacitance: 0\n Number of drivers: 0\n"
            " Number of loads: 1\n Number of pins: 1\n\nLoad pins\n u_core/_supply_/VDD inout (supply)\n\n"),
        "disabled_edges_report": fixtures._file(folder, "disabled_edges.rpt", ""),
    }
    rows = drv.parse_check_types(Path(scene["all_limits_report"]["path"]).read_text(),
        scene=scene["name"], mode=scene["mode"], violators_only=False)
    population = census.derive(folder, scene["linked_liberties"], rows, [lef])
    bundle["pins"] = population["pins"]
    scene.update(refs, excluded_pins=population["excluded"],
        excluded_pins_report=fixtures._file(folder, "excluded.json", json.dumps(population["excluded"])),
        clock_network_pins=population["clock_network_pins"],
        driver_pin_census=population["driver_pins"], population=population["population"])
    scene["parasitic_annotation_report"] = fixtures._file(folder, "annotation.rpt",
        f"Found 1 unannotated drivers.\n{reported}\n\nFound 0 partially unannotated drivers.\n")
    derived = annotation.derive(folder, population["pins"], scene["linked_liberties"],
        [lef], Path(bundle["identity"]["artifacts"]["def"]["path"]), Path(scene["spef"]["path"]))
    scene.update(annotation_census=derived, unannotated_nets=len(derived["unresolved"]),
        annotation_census_report=fixtures._file(folder, "annotation.json", json.dumps(derived)))

    # The complete project consumer rereads the census. Its installed-PDK
    # lookup is a unit I/O boundary; no Docker or LOCAL authority is asserted.
    l7 = fixtures._file(tmp_path, "input/docs/L7.md", "verification plan\n")
    bundle["current"]["sources"]["l7"] = l7
    bundle["frozen"]["sources"]["l7"] = l7["sha256"]
    root = tmp_path / "installed_pdks"
    pdk = fixtures._file(root / "synthetic", "libs.tech/librelane/logic/config.tcl",
        Path(bundle["current"]["sources"]["pdk_config"]["path"]).read_text())
    bundle["current"]["sources"]["pdk_config"] = pdk
    bundle["frozen"]["sources"]["pdk_config"] = pdk["sha256"]
    anchor = {"path": "/image/synthetic/config.tcl", "sha256": pdk["sha256"],
              "pdk_commit": "installed-version"}
    bundle["threshold_anchor"] = anchor
    bundle["identity"]["pdk_commit"] = anchor["pdk_commit"]
    monkeypatch.setattr(drv, "image_pdk_anchor", lambda *args: anchor)
    fixtures._file(tmp_path, "phase3/librelane_pdk_root.provenance.json", json.dumps({
        "path": str(root), "derivation": {"image_id": bundle["identity"]["tool_image_digest"]}}))
    fixtures._install_test_freeze(bundle, tmp_path, monkeypatch)
    fixtures._refresh_scripts(bundle, tmp_path)
    return bundle, derived


@pytest.mark.parametrize("reported,reason,verdict", [
    ("u_core/_supply_/VDD", "lef_pg_no_liberty_arc", "PASS"),
    (r"u_core\/_supply_/VDD", "lef_pg_no_liberty_arc", "PASS"),
    (r"u\/Y", "missing_parasitic_or_geometry_proof", "NOT_MEASURED"),
    (r"u_corner\/absent/BUS", "not_in_pin_census", "NOT_MEASURED"),
], ids=["canonical_pg", "escaped_pg", "escaped_core_missing_rc", "absent_physical_pin"])
def test_annotation_census_name_join(tmp_path, monkeypatch, reported, reason, verdict):
    bundle, derived = _scene(tmp_path, monkeypatch, reported)
    result = drv.judge(bundle, project=tmp_path)
    # Persist actual API outputs beside the neutral inputs, including failures.
    fixtures._file(tmp_path, "API_OBSERVATION.json", json.dumps({
        "reported": reported, "census_pins": sorted(bundle["pins"]),
        "annotation": derived, "judge": result}, ensure_ascii=True, indent=2))
    assert derived["unannotated_names"] == [reported]
    assert set(bundle["pins"]) == {"u/Y", "u_core/_supply_/VDD", "u_corner/marker/BUS"}
    assert bundle["scenes"][0]["population"] == {kind: 1 for kind in drv.KINDS}
    assert derived["resolved"] + derived["unresolved"] == [{"pin": reported,
        **({"net": "n"} if reason == "missing_parasitic_or_geometry_proof" else {}),
        "reason": reason}]
    assert result["verdict"] == verdict, result
    assert not result["failures"], result
    assert result["not_measured"] == ([] if verdict == "PASS" else
        ["typ_nom: parasitic annotation incomplete"])
