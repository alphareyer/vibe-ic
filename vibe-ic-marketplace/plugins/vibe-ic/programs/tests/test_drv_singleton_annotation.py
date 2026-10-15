"""A bound single-driver net can carry no interconnect, while DRV rows remain."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_drv_annotation_name_join as names
import test_drv_signoff_judge as fixtures
from test_drv_signoff_judge import _synthetic_scene_profile  # noqa: F401
import drv_signoff_judge as drv


@pytest.mark.parametrize("case", [
    "singleton", "escaped_singleton", "routed", "extra_load", "missing_def_net",
    "wildcard", "unknown_pin", "over_limit",
])
def test_singleton_annotation_keeps_every_drv_row(tmp_path, monkeypatch, case):
    original_file = fixtures._file

    def source_file(root, name, body):
        if name == "routed.def":
            record = "- n ( u Y )"
            if case == "routed":
                record += " + ROUTED Metal1 ( 0 0 ) ( 10 0 )"
            elif case == "extra_load":
                record += " ( v A )"
            elif case == "wildcard":
                record += " ( * Y )"
            body = ("VERSION 5.8 ;\nNETS 0 ;\nEND NETS\nEND DESIGN\n"
                    if case == "missing_def_net" else
                    "VERSION 5.8 ;\nNETS 1 ;\n " + record + " + USE SIGNAL ;\nEND NETS\nEND DESIGN\n")
        elif name == "library.lib" and case == "extra_load":
            body = body.replace("cell (logic) {", "cell (logic) { pin (A) { direction : input; }")
        elif name == "pin_census.tsv" and case == "extra_load":
            # A real, known constant load remains in the native pin/net census.
            body += "v/A\tpin\tinput\t0\tv\tlogic\tA\tn\tinput\t0.1\t0.1\t0\t0\t0\n"
        elif name == "net_census.rpt" and case == "extra_load":
            body = body.replace(" Number of loads: 0\n Number of pins: 1",
                                " Number of loads: 1\n Number of pins: 2", 1)
            body = body.replace(" u/Y output (logic)\n\n",
                                " u/Y output (logic)\n\nLoad pins\n v/A input (logic)\n\n", 1)
        return original_file(root, name, body)

    monkeypatch.setattr(fixtures, "_file", source_file)
    reported = ("absent/Z" if case == "unknown_pin" else
                r"u\/Y" if case == "escaped_singleton" else "u/Y")
    bundle, derived = names._scene(tmp_path, monkeypatch, reported)
    scene = bundle["scenes"][0]
    before = drv.parse_check_types(Path(scene["all_limits_report"]["path"]).read_text(),
                                  scene=scene["name"], mode=scene["mode"], violators_only=False)
    if case == "over_limit":
        scene["all_limits_report"] = fixtures._file(tmp_path, "all.rpt", fixtures._report(slew=4, fanout=2))
        scene["report"] = fixtures._file(tmp_path, "violators.rpt", fixtures._report(slew=4, violators=True))
        scene["counters"]["max_slew"] = 1
        scene["counter_report"] = fixtures._file(tmp_path, "counter.log", "".join(
            f"DRV_COUNTER {kind} {scene['counters'][kind]}\n" for kind in drv.KINDS))
    result = drv.judge(bundle, project=tmp_path)
    fixtures._file(tmp_path, "API_OBSERVATION.json", json.dumps({
        "case": case, "pins": bundle["pins"], "population": scene["population"],
        "raw_rows_before": before, "annotation": derived, "judge": result}, indent=2))
    assert derived["unannotated_names"] == [reported]
    assert set(bundle["pins"]) == {"u/Y", "u_core/_supply_/VDD", "u_corner/marker/BUS"} | (
        {"v/A"} if case == "extra_load" else set())
    assert scene["population"] == {kind: 1 for kind in drv.KINDS}
    assert all([row["pin"] for row in before[kind]] == ["u/Y"] for kind in drv.KINDS)
    assert [row["pin"] for row in derived["pg_exclusions"]] == ["u_core/_supply_/VDD"]
    if case in ("singleton", "escaped_singleton", "over_limit"):
        assert derived["unresolved"] == []
        assert derived["resolved"] == [{"pin": reported, "net": "n",
            "reason": "singleton_driver_zero_routed_segments",
            "def_sections": ["NETS"], "def_endpoints": ["u/Y"]}]
        assert result["verdict"] == ("FAIL" if case == "over_limit" else "PASS"), result
        if case == "over_limit":
            assert result["counts"]["max_slew"] == 1, result
        else:
            assert result["not_measured"] == [], result
            assert result["failures"] == [], result
    else:
        assert derived["resolved"] == []
        assert [row["pin"] for row in derived["unresolved"]] == [reported]
        assert result["verdict"] == "NOT_MEASURED", result
        assert result["not_measured"] == ["typ_nom: parasitic annotation incomplete"], result
        assert result["failures"] == [], result
