"""U3 — Step 21's router-DRC projection exists whether or not Step 32 passes.

MEASURED (IC_BLOCKER_AUDIT 2026-09-29, spm #3; subservient IC run
`subic_ic_20260928` on 8hd-3, read-only): Step 21 declares
`phase3/stage3/pnr/routed.drc.rpt` and `reports/phase3/drc_router.rpt`. Both are
written only by `_emit_router_drc_report`, which runs in the pre-stream and
post-stream canonicalize passes -- and both passes are reached only when Step 32
passes. On a run whose Step 32 FAILs the route has run and converged, yet the
audit reads Step 21 FAIL(missing_artefact) and cascades "blocked by 21" onto
23-30. The failure is attributed to the step that did its work.

The projection reads only the router transcript, so it is emitted by
`_canonicalize_postpnr_prerequisites` (the post-PnR producer that runs BEFORE
Step 32) and refreshed there whenever the route changes.

chip-, PDK- and vendor-AGNOSTIC: synthetic fixtures only; the only EDA fake is
the router's transcript.
"""
from __future__ import annotations

import json
from pathlib import Path

import phase3_one_shot_runner as R
from test_phase3_postpnr_disclosure_and_gds_guard import (
    _restamp_ss_report_for_current_sdc,
    TOP, OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, _drive, _plan, _project,
    _repair_producer, _pdk,
)
from test_phase3_postpnr_canonical_before_step32 import SPEF, _routed_project

# the router's own transcript, as detailed_route writes it (final count 0)
_ROUTE_LOG = ("[INFO DRT-0195] Start detail routing.\n"
              "[INFO DRT-0199]   Number of violations = 7.\n"
              "[INFO DRT-0199]   Number of violations = 0.\n"
              "[INFO DRT-0198] Complete detail routing.\n")


def _step32_failed(tmp_path: Path, monkeypatch, producer: str) -> Path:
    """A converged route whose Step 32 FAILs, driven through `main()`."""
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    pnr = R._pl.pnr_dir(project)
    (pnr / "constraint.sdc").write_text(
        "create_clock -name core_clk -period 7 [get_ports clk]\n")
    _restamp_ss_report_for_current_sdc(project)
    for rpt in (pnr / "routed.drc.rpt",
                R._pl.reports_phase3_dir(project) / "drc_router.rpt"):
        rpt.unlink(missing_ok=True)
    _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    original_pnr = R.step_pnr

    def route(*args, **kwargs):
        row = original_pnr(*args, **kwargs)
        (pnr / "routed.def").write_bytes((pnr / f"{TOP}.def").read_bytes())
        (pnr / "openroad.log").write_text(_ROUTE_LOG)
        return row

    monkeypatch.setattr(R, "step_pnr", route)
    monkeypatch.setattr(R, "_emit_spef", lambda _p, _t, _d, _c, out, _n: (
        out.parent.mkdir(parents=True, exist_ok=True), out.write_text(SPEF),
        True)[-1])
    row = _repair_producer(monkeypatch, project, producer, "FAIL",
                           "measured DRV residual")
    R.main()
    assert _plan(project)[row]["status"] == "FAIL"
    return project


def test_step21_router_drc_reports_exist_after_a_step32_fail(tmp_path,
                                                             monkeypatch):
    """RED on main: neither report exists, so the audit blames Step 21."""
    project = _step32_failed(tmp_path, monkeypatch, "librelane")
    routed = R._pl.pnr_dir(project) / "routed.drc.rpt"
    mirror = R._pl.reports_phase3_dir(project) / "drc_router.rpt"
    for rpt in (routed, mirror):
        assert rpt.is_file(), f"{rpt.name} absent after a Step-32 FAIL"
    body = routed.read_text()
    # the router's FINAL count, read from its own transcript
    assert "violation report: 0" in body and "DRC clean: YES" in body, body
    assert mirror.read_text() == body


def test_the_direct_step32_producer_is_covered_too(tmp_path, monkeypatch):
    project = _step32_failed(tmp_path, monkeypatch, "direct")
    assert (R._pl.pnr_dir(project) / "routed.drc.rpt").is_file()
    assert (R._pl.reports_phase3_dir(project) / "drc_router.rpt").is_file()


def test_the_postpnr_producer_names_the_reports_and_stays_idempotent(
        tmp_path, monkeypatch):
    project, route = _routed_project(tmp_path)
    pnr = R._pl.pnr_dir(project)
    (pnr / "openroad.log").write_text(_ROUTE_LOG.replace("= 0.", "= 3."))
    monkeypatch.setattr(R, "_emit_spef", lambda *a, **k: False)
    pdk = _pdk(project)
    first = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    routed = pnr / "routed.drc.rpt"
    mirror = R._pl.reports_phase3_dir(project) / "drc_router.rpt"
    assert str(routed) in first.output_files
    assert str(mirror) in first.output_files
    # a dirty route is reported dirty -- the projection never invents a zero
    assert "violation report: 3" in routed.read_text()
    assert "DRC clean: NO" in routed.read_text()
    again = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert str(routed) not in again.output_files
    # a new route transcript is re-projected
    (pnr / "openroad.log").write_text(_ROUTE_LOG)
    third = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert str(routed) in third.output_files
    assert "DRC clean: YES" in routed.read_text()


def test_no_transcript_writes_no_report(tmp_path, monkeypatch):
    """Control: with no router transcript there is nothing to project, and no
    report is fabricated for Step 21."""
    project, _route = _routed_project(tmp_path)
    monkeypatch.setattr(R, "_emit_spef", lambda *a, **k: False)
    R._canonicalize_postpnr_prerequisites(project, TOP, _pdk(project), "")
    assert not (R._pl.pnr_dir(project) / "routed.drc.rpt").exists()
    assert not (R._pl.reports_phase3_dir(project) / "drc_router.rpt").exists()
