#!/usr/bin/env python3
"""FX_LL21_DECLARED_OUTPUTS — steps 15 and 21 carry their declared outputs
whether or not the pre-stream gate admits the layout, and an empty router DRC
report has ONE answer.

MEASURED (spm x gf180mcuD, DIE deliverable, image 0.3.83, CMP3R_spm_fullchip
_status, 2026-09-28): the pre-stream gate failed (sta_record, antenna), and
steps 15 and 21 then FAILED `missing_artefact` for `pdn.done`,
`routed.drc.rpt` and `reports/phase3/drc_router.rpt` -- although the grid was
built (LibreLane's PDN step: 0 power-grid violations) and the route converged
(final `DRT-0199 ... = 0`).

ROOT CAUSE (reproduced here, and NOT specific to the LibreLane chain): all
three are written only by the POST-stream canonicalize pass. The pre-stream
pass returns before them, and the post-stream pass is skipped when the gate
fails -- the core-only DIRECT-path run that failed the same gate (N4,
2026-09-28) lacks the same three files. On the LibreLane path the PDN verdict
was also unreadable: `pnr.tcl` runs no `pdngen`, so the transcript carries no
`PDN_*` marker, and the old text would have said "NOT CONNECTED".

And the audit of a scope holding only EMPTY router reports said `read as
ZERO` at INFO and `No DRC violation categories` at ERROR about the same files.

chip-, PDK- and vendor-AGNOSTIC: synthetic fixtures only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(PROG / "tests"))

import phase3_one_shot_runner as R  # noqa: E402
import eda_report_audit as A  # noqa: E402
from _ppa import power as P  # noqa: E402
from test_postlayout_lec_nameerror import (  # noqa: E402
    TOP, _canonicalize_project, _pdk, _quiet_canonicalize)

# LibreLane's detailed-routing log, as `openroad.log` splices it in
_LL_ROUTE_LOG = (
    "# >>> LIBRELANE OpenROAD.DetailedRouting phase3/librelane/21-route/"
    "05-openroad-detailedrouting/openroad-detailedrouting.log\n"
    "[INFO DRT-0195] Start detail routing.\n"
    "[INFO DRT-0199]   Number of violations = 12.\n"
    "[INFO DRT-0199]   Number of violations = 0.\n"
    "[INFO DRT-0198] Complete detail routing.\n")


def _ll_chain(project: Path, counts=(None, 0, 0)) -> None:
    """A LibreLane step-15 chain and the handoff record that names it. The
    metric is carried forward, as LibreLane does."""
    chain = project / "phase3/librelane/15-floorplan"
    names = ["10-odb-addpdnobstructions", "11-openroad-generatepdn",
             "18-checker-powergridviolations"]
    for name, n in zip(names, counts):
        d = chain / name
        d.mkdir(parents=True)
        m = {} if n is None else {
            "design__power_grid_violation__count": n,
            "design__power_grid_violation__count__net:VDD": n,
            "design__power_grid_violation__count__net:VSS": 0}
        (d / "state_out.json").write_text(json.dumps({"metrics": m}))
    rec = project / "reports/phase3/librelane_floorplan_handoff.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    # an ABSOLUTE path, as the record stores it -- under another root, to
    # prove the chain is read under THIS project
    rec.write_text(json.dumps({"state": "/elsewhere/proj/phase3/librelane/"
                               "15-floorplan/11-openroad-generatepdn/state_out.json"}))


def _emit_pdn(project: Path) -> str:
    """The runner's step-15 block, as `step_canonicalize_artefacts` runs it."""
    pnr = R._pl.pnr_dir(project)
    d = pnr / f"{TOP}.def"
    ok, mk = R._pnr_pdn_status(project)
    text = P.pdn_done_text(project, pnr, d, ok, mk,
                           R._def_pdn_evidence(d.read_text()))
    if text is not None:
        (pnr / "pdn.done").write_text(text)
    return (pnr / "pdn.done").read_text()


def _prestream(project: Path, monkeypatch, tmp_path: Path):
    _quiet_canonicalize(monkeypatch)
    return R.step_canonicalize_artefacts(
        project, TOP, _pdk(str(tmp_path / "x.lib"), str(tmp_path / "x.lef")),
        "nocontainer", prestream=True)


# ── the declared outputs exist after a PRE-stream pass ─────────────────────

def test_the_prestream_pass_writes_the_step15_and_step21_declared_outputs(
        tmp_path, monkeypatch):
    """RED on main: the pre-stream pass returns before either is written, so
    a run the pre-stream gate stops has neither."""
    project = _canonicalize_project(tmp_path)
    (R._pl.pnr_dir(project) / "openroad.log").write_text(_LL_ROUTE_LOG)
    (R._pl.pnr_dir(project) / "constraint.sdc").write_text(
        "create_clock -name clk -period 24 [get_ports clk]\n")
    _ll_chain(project)
    res = _prestream(project, monkeypatch, tmp_path)
    assert res.name == "prestream_evidence", res
    pnr = R._pl.pnr_dir(project)
    for f in (pnr / "pdn.done", pnr / "routed.drc.rpt",
              R._pl.reports_phase3_dir(project) / "drc_router.rpt",
              R._pl.cts_dir(project) / "clock_plan.json"):
        assert f.is_file(), f"{f.name} not written by the pre-stream pass"
        assert str(f) in res.output_files
    drc = (pnr / "routed.drc.rpt").read_text()
    assert "violation report: 0" in drc and "DRC clean: YES" in drc


def test_the_librelane_grid_is_read_from_librelanes_own_measurement(tmp_path):
    """RED on main: no marker in the transcript -> `NOT CONNECTED`."""
    project = _canonicalize_project(tmp_path)
    pnr = R._pl.pnr_dir(project)
    (pnr / "openroad.log").write_text(_LL_ROUTE_LOG)
    _ll_chain(project)
    text = _emit_pdn(project)
    assert "# PDN status: CONNECTED" in text, text
    # the step that MEASURED it (not the last one carrying it forward), and
    # the bytes it was read from
    step = "phase3/librelane/15-floorplan/11-openroad-generatepdn"
    assert f"LibreLane {step}: design__power_grid_violation__count=0" in text
    sha = R._file_sha256(project / step / "state_out.json").split(":", 1)[1]
    assert f"# source: {step}/state_out.json sha256:{sha}" in text
    def_sha = R._file_sha256(pnr / f"{TOP}.def").split(":", 1)[1]
    assert f"# measured_def_sha256: {def_sha}" in text


def test_a_librelane_grid_with_violations_is_not_connected(tmp_path):
    project = _canonicalize_project(tmp_path)
    pnr = R._pl.pnr_dir(project)
    (pnr / "openroad.log").write_text(_LL_ROUTE_LOG)
    _ll_chain(project, counts=(None, 7, 7))
    text = _emit_pdn(project)
    assert "# PDN status: NOT CONNECTED" in text and "count=7" in text


def test_no_marker_and_no_librelane_measurement_is_not_measured(tmp_path):
    """A missing marker is not a disconnected grid."""
    project = _canonicalize_project(tmp_path)
    pnr = R._pl.pnr_dir(project)
    (pnr / "openroad.log").write_text("[INFO] nothing about a grid\n")
    text = _emit_pdn(project)
    assert "# PDN status: NOT MEASURED" in text, text
    assert "NOT CONNECTED" not in text


def test_the_flag_follows_the_def_it_describes(tmp_path):
    """Written pre-stream, it must not speak for a layout a later promotion
    wrote: a changed DEF re-measures, an unchanged one is left alone."""
    project = _canonicalize_project(tmp_path)
    pnr = R._pl.pnr_dir(project)
    (pnr / "openroad.log").write_text(_LL_ROUTE_LOG)
    _ll_chain(project)
    d = pnr / f"{TOP}.def"
    first = _emit_pdn(project)
    assert P.pdn_done_text(project, pnr, d, False, "no PDN insertion marker",
                           {}) is None, "an unchanged DEF must not be re-measured"
    assert _emit_pdn(project) == first
    d.write_text(d.read_text() + "# promoted\n")
    _emit_pdn(project)
    new_sha = R._file_sha256(d).split(":", 1)[1]
    assert f"# measured_def_sha256: {new_sha}" in (pnr / "pdn.done").read_text()


def test_the_prestream_return_is_preceded_by_both_emitters():
    """Both outputs are produced BEFORE the `if prestream:` return, so a later
    edit cannot move them behind it unnoticed."""
    import ast
    tree = ast.parse((PROG / "phase3_one_shot_runner.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "step_canonicalize_artefacts")
    ret = [n for n in ast.walk(fn) if isinstance(n, ast.If)
           and isinstance(n.test, ast.Name) and n.test.id == "prestream"
           and any(isinstance(s, ast.Return) for s in n.body)]
    assert len(ret) == 1
    ret_line = next(s for s in ret[0].body if isinstance(s, ast.Return)).lineno
    calls = [(getattr(c.func, "id", None) or getattr(c.func, "attr", None), c.lineno)
             for c in ast.walk(fn) if isinstance(c, ast.Call)]
    for name in ("pdn_done_text", "_emit_router_drc_report", "emit_clock_plan"):
        assert any(n == name and ln < ret_line for n, ln in calls), name


# ── one answer for an empty router report ──────────────────────────────────

def _drc_audit(project: Path):
    out = project / "audit.json"
    r = subprocess.run([sys.executable, str(PROG / "drc_report_check.py"),
                        str(project), "--mode", "drc", "--under",
                        "phase3/stage3/pnr", "--json", str(out)],
                       capture_output=True, text=True)
    return r.returncode, json.loads(out.read_text())


def _empty_scope(tmp_path: Path, log: str | None) -> Path:
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "routed_router.drc.rpt").write_text("")
    if log is not None:
        (pnr / "openroad.log").write_text(log)
    return tmp_path


def _rules(payload, sev):
    return {f["rule"] for f in payload["findings"] if f["severity"] == sev}


def test_an_empty_report_with_the_routers_final_zero_is_a_measured_zero(tmp_path):
    """RED on main: rc 1 with `DRC_CATEGORIES_EXIST` at ERROR beside
    `DRC_REPORT_EMPTY ... read as ZERO` at INFO."""
    rc, p = _drc_audit(_empty_scope(tmp_path, _LL_ROUTE_LOG))
    assert rc == 0 and p["passed"] is True, p["findings"]
    assert "DRC_CATEGORIES_EXIST" not in _rules(p, "ERROR")
    assert "DRC_EMPTY_ZERO_CORROBORATED" in _rules(p, "INFO")
    assert p["summary"]["empty_report_evidence"] == {
        "phase3/stage3/pnr/routed_router.drc.rpt": ["phase3/stage3/pnr/openroad.log"]}


def test_an_empty_report_with_no_completion_evidence_is_not_measured(tmp_path):
    rc, p = _drc_audit(_empty_scope(tmp_path, None))
    assert rc == 1 and p["passed"] is False
    assert "DRC_EMPTY_NOT_MEASURED" in _rules(p, "ERROR")
    assert "DRC_CATEGORIES_EXIST" not in _rules(p, "ERROR")


def test_an_empty_report_beside_a_nonzero_final_count_is_not_a_zero(tmp_path):
    rc, p = _drc_audit(_empty_scope(
        tmp_path, "[INFO DRT-0199]   Number of violations = 3.\n"))
    assert rc == 1 and p["passed"] is False
    msg = next(f["message"] for f in p["findings"]
               if f["rule"] == "DRC_EMPTY_NOT_MEASURED")
    assert "(3)" in msg


def test_a_zero_beside_a_disagreeing_final_count_does_not_corroborate(tmp_path):
    """One log says 0, another says 3: the evidence disagrees, so the empty
    report is not a measured zero."""
    project = _empty_scope(tmp_path, "[INFO DRT-0199]   Number of violations = 3.\n")
    (project / "phase3/stage3/pnr/earlier.log").write_text(
        "[INFO DRT-0199]   Number of violations = 0.\n")
    rc, p = _drc_audit(project)
    assert rc == 1 and p["passed"] is False
    assert "DRC_EMPTY_NOT_MEASURED" in _rules(p, "ERROR")
    assert p["summary"]["empty_report_evidence"] == {}
