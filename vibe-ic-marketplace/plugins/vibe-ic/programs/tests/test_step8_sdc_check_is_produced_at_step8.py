"""FX_STEP8_SDC_CHECK: step 8's declared `reports/phase2/sdc_check.json` is
produced AT step 8, and later consumers keep it only while it is current.

MEASURED (spm x gf180mcuD DIE, after FX_STEP7_ASIC_SDC): step 7 PASS, step 8
FAIL `missing_artefact` -- the report's only in-run producer was
`step_canonicalize_artefacts` at the phase-3 TAIL, after PnR, so a backend that
stopped before the tail left step 8 with nothing, and step 15 was blocked by
it. That tail also wrote the report only when ABSENT, so a report checked
against an older SDC was kept as current.

Also here: the route-layer and staged-config readers of the design's scoped
flow config resolve the standard-cell library from the PDK liberty (not from
PnR-time artefacts, where the pad record's IO library could win).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R                       # noqa: E402
sys.path.insert(0, str(TESTS))
from _ppa import timing as T                              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fx_step8_step7_fixtures",
    TESTS / "test_step7_asic_sdc_is_authored_once_at_step7.py")
S7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S7)

REPORT = Path(T.SDC_CHECK_REL)
BASIS = Path(T.SDC_CHECK_BASIS_REL)


def _after_step8(tmp_path, monkeypatch):
    proj = S7._project(tmp_path)
    S7._step7(proj, S7._pdk(monkeypatch))
    return proj


def _basis(proj):
    return json.loads((proj / BASIS).read_text())


# --------------------------------------------------------------------------- #
# RED on main: step 8's declared output is absent after steps 7/8
# --------------------------------------------------------------------------- #
def test_step8_writes_its_declared_report_before_pnr(tmp_path, monkeypatch):
    proj = _after_step8(tmp_path, monkeypatch)
    report = proj / REPORT
    assert report.is_file(), "step 8 produced no sdc_check.json before PnR"
    b = _basis(proj)
    assert b["step"] == 8 and b["rc"] == 0, b
    step7 = S7._record(proj)
    assert b["step7_sdc"] == {"path": step7["path"],
                              "sha256": step7["sha256"]}
    assert step7["path"] in b["sdc_files"]
    assert b["report_sha256"] == T.hashlib.sha256(
        report.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# a later consumer keeps a CURRENT report and regenerates a stale one
# --------------------------------------------------------------------------- #
def test_the_tail_keeps_a_current_report_without_rerunning(tmp_path,
                                                          monkeypatch):
    proj = _after_step8(tmp_path, monkeypatch)

    def _no_rerun(*a, **k):
        raise AssertionError("a current step-8 report was regenerated")
    monkeypatch.setattr(T, "emit_step8_sdc_check", _no_rerun)
    basis, action = T.sdc_check_for_consumer(R, proj)
    assert action == "current" and basis == _basis(proj)


def test_a_changed_sdc_makes_the_report_stale_and_it_is_regenerated(
        tmp_path, monkeypatch):
    """Before: the tail wrote the report only when ABSENT, so this stale one
    was kept as current."""
    proj = _after_step8(tmp_path, monkeypatch)
    sdc = proj / "phase3/stage3/pnr/constraint.sdc"
    sdc.write_text(sdc.read_text() + "set_max_fanout 3 [current_design]\n")
    basis, action = T.sdc_check_for_consumer(R, proj)
    assert action == "regenerated"
    assert "phase3/stage3/pnr/constraint.sdc" in basis["regenerated"]
    assert basis == _basis(proj)
    assert basis["sdc_files"]["phase3/stage3/pnr/constraint.sdc"] == \
        T.hashlib.sha256(sdc.read_bytes()).hexdigest()


def test_a_report_step8_did_not_write_is_regenerated(tmp_path, monkeypatch):
    proj = _after_step8(tmp_path, monkeypatch)
    (proj / REPORT).write_text(json.dumps({"passed": True, "forged": 1}))
    basis, action = T.sdc_check_for_consumer(R, proj)
    assert action == "regenerated"
    assert "not the report step 8 wrote" in basis["regenerated"]
    assert "forged" not in (proj / REPORT).read_text()


def test_an_old_project_without_a_basis_is_regenerated(tmp_path, monkeypatch):
    proj = _after_step8(tmp_path, monkeypatch)
    (proj / BASIS).unlink()
    basis, action = T.sdc_check_for_consumer(R, proj)
    assert action == "regenerated" and "no step-8 basis" in basis["regenerated"]


def test_the_tail_no_longer_owns_step8(tmp_path):
    """The tail's block consults the consumer check; it no longer runs the
    checker only when the report is absent."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    tail = src.split("def step_canonicalize_artefacts(", 1)[1].split(
        "\ndef ", 1)[0]
    assert "sdc_check_for_consumer(" in tail
    assert "not sdc_check_json.is_file()" not in tail


# --------------------------------------------------------------------------- #
# the remaining library readers take the PDK liberty (not the IO library)
# --------------------------------------------------------------------------- #
_LIB = S7._LIB


def _scoped_project(tmp_path, key, value):
    proj = S7._project(tmp_path)
    rec = S7._pad_record(proj)
    rec.write_text(json.dumps(dict(json.loads(rec.read_text()), io_lefs=[
        "/foss/pdks/x/libs.ref/gf180mcu_fd_io/lib/gf180mcu_fd_io__tt.lib"])))
    (proj / "input").mkdir(exist_ok=True)
    (proj / "input" / "config.json").write_text(json.dumps({
        "DESIGN_NAME": "chip_top",
        "scl::gf180mcu_fd_sc_mcu7t5v0": {key: value}}))
    return proj


def test_the_route_ceiling_uses_the_std_cell_library_scope(tmp_path):
    import test_signal_routing_floor_keeps_a_pin_access_layer as SF
    proj = _scoped_project(tmp_path, "RT_MAX_LAYER", "Metal4")
    layers = ["Metal1", "Metal2", "Metal3", "Metal4", "Metal5"]
    t = tmp_path / "tech.tlef"
    c = tmp_path / "cells.lef"
    t.write_text(SF._tech_lef(layers))
    c.write_text(SF._cell_lef({"Metal1": 50, "Metal2": 5}))
    pdk = SF._Pdk(t, c, name="gf180mcuD")
    pdk.liberty = _LIB
    got = R._v1_8_100_routing_layer_range(pdk, str(proj), "")
    assert got is not None and got[2] == "Metal4", got


def test_the_staged_config_fanout_tier_uses_the_std_cell_library_scope(
        tmp_path):
    proj = _scoped_project(tmp_path, "MAX_FANOUT_CONSTRAINT", 6)
    assert R._l9_declared_max_fanout(proj, "gf180mcuD", _LIB) == 6
