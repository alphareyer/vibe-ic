"""One exclusion policy reaches cell inserters and counts their real DEFs."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest
from _hostpaths import require_repo

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import librelane_cts_hold as C
import librelane_postroute_repair as P
import librelane_contract as L
import phase3_one_shot_runner as R
import excluded_master_census_check as EMC


def _corner_configs(tmp_path: Path):
    root = tmp_path / "pdk"
    libdir = root / "famxD/lib"
    libdir.mkdir(parents=True)
    (libdir / "tt.lib").write_text("library(x) {\n cell (famx__dly_1) { }\n}\n")
    (libdir / "ss.lib").write_text("library(x) {\n cell (famx__dly_2) { }\n}\n")
    real = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                        "programs", "tests", "fixtures", "real_benchmark",
                        "pdk_liberty_tie_cell_block.lib")
    configs = {}
    for step in ("OpenROAD.Floorplan", "OpenROAD.CTS",
                 "OpenROAD.ResizerTimingPostCTS"):
        path = tmp_path / f"{step}.json"
        path.write_text(json.dumps({
            "CELL_LIBS": {"tt": ["/pdk/famxD/lib/tt.lib", str(real)],
                          "ss": ["/pdk/famxD/lib/ss.lib"]},
            "EXTRA_EXCLUDED_CELLS": ["famx__probe_1"]}))
        configs[step] = path
    return root, configs


def test_resolved_corner_libraries_cover_floorplan_cts_and_hold(tmp_path):
    root, configs = _corner_configs(tmp_path)
    resolve = getattr(R, "_resolved_cell_policy",
                      lambda paths, *_a, **_k: (paths, []))
    updated, covered = resolve(configs, root, "famxD",
                               required=tuple(configs))
    expected = ["famx__dly_1", "famx__dly_2", "famx__probe_1"]
    for step in configs:
        assert json.loads(updated[step].read_text())["EXTRA_EXCLUDED_CELLS"] == expected
    assert set(covered) == set(configs)


def test_unreadable_active_corner_refuses_incomplete_policy(tmp_path):
    root, configs = _corner_configs(tmp_path)
    (root / "famxD/lib/ss.lib").unlink()
    resolve = getattr(R, "_resolved_cell_policy",
                      lambda paths, *_a, **_k: (paths, []))
    with pytest.raises(L.Refusal, match="LL_CELL_POLICY_LIBERTY_UNREADABLE"):
        resolve(configs, root, "famxD", required=tuple(configs))


def test_cts_hold_overlay_merges_the_run_policy_with_pdk_exclusions(
        tmp_path, monkeypatch):
    monkeypatch.setattr(L, "emit_config", lambda *_a, **_k: {
        "EXTRA_EXCLUDED_CELLS": ["famx__probe_1"]})
    monkeypatch.setattr(C, "_switch_knobs", lambda _p: {})
    expected = ["famx__dly_1", "famx__probe_1"]
    args = (R, tmp_path, "famxD", {}, 3, "declared", tmp_path / "scratch")
    try:
        got = C.overlay(*args, excluded_cells=["famx__dly_1"])
    except TypeError:
        got = C.overlay(*args)  # main's actual value, not an import failure
    assert got.get("EXTRA_EXCLUDED_CELLS", ([],))[0] == expected


def test_postroute_repair_reads_its_own_corner_liberty(tmp_path):
    root = tmp_path / "pdk"
    lib = root / "famxD" / "lib" / "tt.lib"
    lib.parent.mkdir(parents=True)
    lib.write_text("library(famx) {\n cell (famx__buf_1) { }\n"
                   " cell (famx__dly_1) { }\n}\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"CELL_LIBS": {"tt": ["/pdk/famxD/lib/tt.lib"]}}))
    reader = getattr(P, "repair_dont_use", lambda *_a: [])
    got = reader(config, root, "famxD",
                 lambda text: R._dont_use_family_cells(
                     R._V1_6_596_RE_CELL_DECL.findall(text)))
    assert got == ["famx__dly_1"]


def _fixture(tmp_path: Path, *, design: str, bad: bool):
    project = tmp_path / design
    project.mkdir(parents=True)
    master = "famx__dly_1" if bad else "famx__buf_1"
    layout = project / "stage.def"
    layout.write_text(
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\n"
        "ROW row0 site 0 0 N DO 10 BY 1 STEP 100 0 ;\n"
        f"COMPONENTS 2 ;\n- u_core famx__logic_1 + PLACED ( 0 0 ) N ;\n"
        f"- u_cell {master} + PLACED ( 100 0 ) N ;\n"
        "END COMPONENTS\nEND DESIGN\n")
    config = project / "step.json"
    config.write_text(json.dumps({"EXTRA_EXCLUDED_CELLS": ["famx__dly_1"]}))
    state = project / "state_out.json"
    state.write_text(json.dumps({"def": str(layout)}))
    return config, state


def _audit(config: Path, state: Path):
    checker = getattr(R, "_emc", None)
    if checker is None:
        return {"verdict": "NOT_MEASURED", "excluded_count": None}
    assert checker is EMC, "the runner must use the tested census producer"
    return checker.audit(config, state, "OpenROAD.ResizerTimingPostCTS")


def test_second_design_clean_and_bad_cell_are_both_counted(tmp_path):
    clean = _fixture(tmp_path, design="logic_a", bad=False)
    bad = _fixture(tmp_path, design="logic_b", bad=True)
    assert _audit(*clean)["verdict"] == "PASS"
    result = _audit(*bad)
    assert result["verdict"] == "FAIL" and result["excluded_count"] == 1
    assert result["excluded_instances"][0]["instance"] == "u_cell"


def test_unreadable_def_is_not_an_empty_clean_census(tmp_path):
    config, state = _fixture(tmp_path, design="logic_c", bad=False)
    Path(json.loads(state.read_text())["def"]).unlink()
    assert _audit(config, state)["verdict"] == "NOT_MEASURED"


def test_reverse_mutation_removing_policy_exposes_the_bad_master(tmp_path):
    config, state = _fixture(tmp_path, design="logic_d", bad=True)
    assert _audit(config, state)["verdict"] == "FAIL"
    config.write_text(json.dumps({"EXTRA_EXCLUDED_CELLS": []}))
    assert _audit(config, state)["verdict"] == "PASS"


@pytest.mark.parametrize("lib_kind", ["native", "empty", "null", "invalid_member"])
def test_resolved_cell_policy_native_lib_contract(tmp_path, lib_kind):
    """Use the resolved native LIB; an explicit invalid LIB cannot borrow CELL_LIBS."""
    root, configs = _corner_configs(tmp_path)
    source = configs["OpenROAD.Floorplan"]
    doc = json.loads(source.read_text())
    libs = doc["CELL_LIBS"]
    doc["DEFAULT_CORNER"] = "tt"
    doc["STA_CORNERS"] = ["tt", "ss"]
    if lib_kind == "native":
        doc["LIB"] = doc.pop("CELL_LIBS")
    else:
        doc["LIB"] = {"empty": {}, "null": None,
                      "invalid_member": {"tt": [None]}}[lib_kind]
    config = tmp_path / "Yosys.JsonHeader.json"
    config.write_text(json.dumps(doc))
    before = config.read_bytes()
    if lib_kind != "native":
        with pytest.raises(L.Refusal, match="LL_CELL_POLICY_LIBERTY_UNDECLARED"):
            R._resolved_cell_policy({"Yosys.JsonHeader": config}, root, "famxD",
                                    required=("Yosys.JsonHeader",))
        assert config.read_bytes() == before
        return
    updated, covered = R._resolved_cell_policy(
        {"Yosys.JsonHeader": config}, root, "famxD", required=("Yosys.JsonHeader",))
    result = json.loads(updated["Yosys.JsonHeader"].read_text())
    assert result["EXTRA_EXCLUDED_CELLS"] == [
        "famx__dly_1", "famx__dly_2", "famx__probe_1"]
    assert result["LIB"] == libs
    assert result["DEFAULT_CORNER"] == doc["DEFAULT_CORNER"]
    assert result["STA_CORNERS"] == doc["STA_CORNERS"]
    assert covered == ["Yosys.JsonHeader"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("lib_kind", ["native", "empty", "null", "invalid_member"])
def test_postroute_policy_native_lib_contract(tmp_path, lib_kind):
    """The repair owner uses its native LIB and never falls back from an explicit value."""
    root, configs = _corner_configs(tmp_path)
    doc = json.loads(configs["OpenROAD.Floorplan"].read_text())
    if lib_kind == "native":
        doc["LIB"] = doc.pop("CELL_LIBS")
    else:
        doc["LIB"] = {"empty": {}, "null": None,
                      "invalid_member": {"tt": [None]}}[lib_kind]
    config = tmp_path / "OpenROAD.RepairDesignPostGRT.json"
    config.write_text(json.dumps(doc))
    before = config.read_bytes()
    rule = lambda text: R._dont_use_family_cells(R._V1_6_596_RE_CELL_DECL.findall(text))
    if lib_kind != "native":
        code = ("LL_PRR_LIBERTY_UNREADABLE" if lib_kind == "invalid_member"
                else "LL_PRR_LIBERTY_UNDECLARED")
        with pytest.raises(L.Refusal, match=code):
            P.repair_dont_use(config, root, "famxD", rule)
        assert config.read_bytes() == before
        return
    assert P.repair_dont_use(config, root, "famxD", rule) == [
        "famx__dly_1", "famx__dly_2"]
    assert config.read_bytes() == before
