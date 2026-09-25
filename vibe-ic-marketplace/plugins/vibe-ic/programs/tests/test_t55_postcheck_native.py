"""A failed native PSM run cannot inherit an earlier segment screen."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import phase3_one_shot_runner as R


def _project(tmp_path):
    project = tmp_path / "project"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "subservient.def").write_text(
        "VERSION 5.8 ;\nSPECIALNETS 1 ;\n"
        "- VP + USE POWER ;\nEND SPECIALNETS\nEND DESIGN\n")
    return project


def test_postcheck_refuses_failed_native_even_with_fresh_report_and_stale_pass(tmp_path):
    project = _project(tmp_path)
    reports = project / "reports/phase3"
    reports.mkdir(parents=True)
    called = []

    def failed_native(_project, _top, _pdk, _container, _ir, em, notes):
        em.write_text("OpenROAD PSM invocation failed: rc=1\n")
        notes.append("native execution rc=1")
        return False, False

    def stale_authority(_project, _pdk, _container, _notes):
        called.append(True)
        (reports / "em_current_authority.json").write_text(
            json.dumps({"verdict": "PASS"}))
        return True

    verdict, reason = R._ppa_power._pdn_em_post_resize_check(
        project, "subservient", object(), "test", failed_native,
        stale_authority)
    assert verdict == "NOT_MEASURED"
    assert reason.startswith("PDN_EM_POSTCHECK_NATIVE_UNMEASURED")
    assert called == []


def test_native_failure_removes_previous_segment_csv(tmp_path, monkeypatch):
    project = _project(tmp_path)
    reports = project / "reports/phase3"
    reports.mkdir(parents=True)
    stale = reports / "em_segments.csv"
    stale.write_text("old-pass-segment\n")
    tech = tmp_path / "tech.tlef"
    tech.write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    pdk = SimpleNamespace(metal_prefix="Metal", tech_lef=str(tech),
                          cell_lef=str(tech), liberty=str(tech),
                          macro_lefs=[])
    monkeypatch.setattr(R, "_to_container_path", lambda path, _container: path)
    monkeypatch.setattr(R, "_liberty_operating_condition",
                        lambda _lib, _container: None)
    monkeypatch.setattr(R, "_docker_exec",
                        lambda *_a, **_kw: (1, "", "native failed"))
    notes = []
    ir_ok, em_ok = R._emit_ir_em_reports(
        project, "subservient", pdk, "test", reports / "ir_drop.rpt",
        reports / "em.rpt", notes)
    assert (ir_ok, em_ok) == (False, False)
    assert not stale.exists()
    assert json.loads((reports / "em.json").read_text())["verdict"] == "FAIL"


def test_second_pnr_stale_def_em_row_is_valid_and_does_not_run_psm(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    called = []
    monkeypatch.setattr(R, "_emit_ir_em_reports",
                        lambda *_args: called.append(True))
    # This is the sub4 path: PnR said PASS while canonical DEF still predates
    # the second dispatch. Its old EM report cannot measure the new layout.
    row = R._pdn_em_postcheck_step(
        project, "subservient", object(), "test", time.time_ns())
    assert row.status == "NOT_MEASURED"
    assert row.reason_class == "upstream_failed"
    assert row.detail == "PDN_EM_POSTCHECK_STALE_DEF"
    assert called == []
