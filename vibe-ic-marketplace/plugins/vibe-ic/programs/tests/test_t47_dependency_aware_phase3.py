"""Exercise the real Phase-3 main dispatch and the release receipt consumer."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
import pytest
import yaml

import phase3_one_shot_runner as R
try:
    import layout_receipt_identity_check as C
except ModuleNotFoundError:
    C = None  # main-side behavioural control still reaches GDS dispatch
from test_phase3_postpnr_disclosure_and_gds_guard import (
    OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, TOP, _project, _drive, _plan,
)


def _run(tmp_path, monkeypatch, *, gate_pass: bool, diagnostic: bool = False,
         mutate_after_freeze: bool = False, verify_parallel: bool = False,
         verify_xor_parallel: bool = False):
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    drive = _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    pnr = R._pl.pnr_dir(project)
    (pnr / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")

    def fake_pnr(proj, top, pdk, container, die_um, util, **kw):
        drive.called.append("pnr")
        for name in (f"{top}.def", "routed.def", "placed.def"):
            (pnr / name).write_text("VERSION 5.8 ;\nNETS 0 ;\nEND NETS\nEND DESIGN\n")
        (pnr / "openroad.log").write_text(
            "[INFO DRT-0198] Complete detail routing\n"
            "[INFO DRT-0199] Number of violations = 0\n"
            "PRESTREAM_UNROUTED_NETS: 0\n")
        R._write_pnr_args_sidecar(pnr, die_um, util)
        return R.StepResult("pnr", "PASS", 0.0, "PnR ran on current layout")

    def fake_canonicalize(proj, top, pdk, container, **kw):
        if kw.get("prepv"):
            (pnr / "filled.def").write_text("post-fill\n")
            out = R._pl.gds_dir(proj) / f"{top}.gds"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes((pnr / f"{top}.gds").read_bytes())
        if not kw and mutate_after_freeze:
            (pnr / f"{top}.gds").write_bytes(b"CHANGED AFTER FREEZE\n")
        return R.StepResult("canonicalize_artefacts", "PASS", 0.0,
                            "current outputs staged")

    monkeypatch.setattr(R, "step_pnr", fake_pnr)
    if gate_pass or diagnostic:
        monkeypatch.setattr(R, "step_canonicalize_artefacts", fake_canonicalize)
    monkeypatch.setattr(R, "step_prestream_gate", lambda *a, **k: R.StepResult(
        "prestream_gate", "PASS" if gate_pass else "FAIL", 0.0,
        "synthetic routed gate verdict", extras={
            "layout_digest": "routed-basis",
            "failed_gates": [] if gate_pass else ["sta_corner"]}),
            raising=False)
    if verify_xor_parallel:
        rendezvous = threading.Barrier(2, timeout=15)
        real_signoff = R.step_declared_signoff_gates
        real_producers = R.run_pre_audit_producers

        def signoff(*a, **k):
            rendezvous.wait()
            return real_signoff(*a, **k)

        def producers(*a, **k):
            if k.get("only_names") == ("gds_xor",):
                rendezvous.wait()
            return real_producers(*a, **k)

        monkeypatch.setattr(R, "step_declared_signoff_gates", signoff)
        monkeypatch.setattr(R, "run_pre_audit_producers", producers)
    if verify_parallel:
        rendezvous = threading.Barrier(2, timeout=15)
        for name in ("drc", "lvs"):
            def reader(*a, _name=name, **k):
                rendezvous.wait()
                drive.called.append(_name)
                return R.StepResult(_name, "PASS", 0.0, "frozen layout read")
            monkeypatch.setattr(R, f"step_{name}", reader)
    if diagnostic:
        monkeypatch.setattr(sys, "argv", sys.argv + ["--diagnostic-continue"])
    R.main()
    return project, drive, _plan(project)


def test_failed_prestream_gate_blocks_gds_and_names_failed_gate(tmp_path, monkeypatch):
    project, drive, plan = _run(tmp_path, monkeypatch, gate_pass=False)
    assert "gds" not in drive.called
    assert plan["gds"]["status"] == "NOT_MEASURED"
    assert "sta_corner" in plan["gds"]["detail"]
    assert plan["drc"]["status"] == "NOT_MEASURED"
    assert C.check(project)["verdict"] != "PASS"


def test_real_prestream_gate_refuses_unidentified_routed_basis(tmp_path, monkeypatch):
    real_gate = R.step_prestream_gate
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    drive = _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    monkeypatch.setattr(R, "step_prestream_gate", real_gate)
    R.main()
    plan = _plan(project)
    assert "gds" not in drive.called
    assert plan["prestream_gate"]["status"] == "NOT_MEASURED"
    assert "layout input sdc" in plan["prestream_gate"]["detail"]
    assert plan["gds"]["status"] == "NOT_MEASURED"


def test_passing_gate_streams_and_freezes_one_identity(tmp_path, monkeypatch):
    project, drive, plan = _run(tmp_path, monkeypatch, gate_pass=True,
                                verify_parallel=True)
    assert "gds" in drive.called
    frozen = plan["layout_freeze"]["extras"]["layout_digest"]
    assert len(frozen) == 64
    assert plan["drc"]["extras"]["layout_digest"] == frozen
    assert plan["lvs"]["extras"]["layout_digest"] == frozen
    receipt = json.loads((project / "reports/phase3/layout_receipts.json").read_text())
    assert receipt["frozen_layout_digest"] == frozen


def test_xor_and_tapeout_precheck_overlap_on_frozen_identity(tmp_path, monkeypatch):
    project, _, plan = _run(tmp_path, monkeypatch, gate_pass=True,
                            verify_xor_parallel=True)
    frozen = plan["layout_freeze"]["extras"]["layout_digest"]
    receipt = json.loads((project / "reports/phase3/layout_receipts.json").read_text())
    bound = {row["name"]: row["extras"]["layout_digest"]
             for row in receipt["receipts"]}
    assert bound["gds_xor"] == frozen
    assert bound["tapeout_precheck"] == frozen


def test_diagnostic_continue_never_supplies_release_pass(tmp_path, monkeypatch):
    project, drive, plan = _run(tmp_path, monkeypatch, gate_pass=False,
                                diagnostic=True)
    assert "gds" not in drive.called
    assert plan["gds"]["status"] == "NOT_MEASURED"
    assert not list((project / "phase3/stage4/foundry_handoff").glob("*.gds"))
    receipt = json.loads((project / "reports/phase3/layout_receipts.json").read_text())
    assert receipt["release_scope"] == "DIAGNOSTIC_ONLY"
    assert receipt["release_verdict"] == "NOT_MEASURED_FOR_RELEASE"
    assert plan["drc"]["status"] != "PASS"
    assert C.check(project)["verdict"] != "PASS"


def test_layout_change_after_freeze_invalidates_pv(tmp_path, monkeypatch):
    project, _, plan = _run(tmp_path, monkeypatch, gate_pass=True,
                            mutate_after_freeze=True)
    assert plan["drc"]["status"] == "NOT_MEASURED"
    assert "invalidated" in plan["drc"]["detail"]
    assert C.check(project)["verdict"] != "PASS"


def _good_receipts(project: Path):
    pnr = project / "phase3/stage3/pnr"
    canonical = project / "phase3/stage4/gds"
    report = project / "reports/phase3"
    pnr.mkdir(parents=True)
    canonical.mkdir(parents=True)
    report.mkdir(parents=True)
    (pnr / "routed.def").write_bytes(b"VERSION 5.8 ;\nEND DESIGN\n")
    (pnr / "unit.gds").write_bytes(b"synthetic GDS bytes\n")
    (canonical / "unit.gds").write_bytes((pnr / "unit.gds").read_bytes())
    frozen = C._layout_sha(pnr / "unit.gds", pnr / "routed.def")
    (report / "prestream_gate.json").write_text(json.dumps({
        "verdict": "PASS", "layout_digest": "routed-basis"}))
    receipt = {
        "frozen_layout_digest": frozen,
        "current_layout_digest": frozen,
        "prestream_basis_digest": "routed-basis",
        "current_basis_digest": "routed-basis",
        "gds_relpath": "phase3/stage3/pnr/unit.gds",
        "gds_sha256": C._sha(pnr / "unit.gds"),
        "def_sha256": C._sha(pnr / "routed.def"),
        "release_scope": "RELEASE_CANDIDATE",
        "release_verdict": "ELIGIBLE_FOR_AUDIT",
        "receipts": [{"name": "drc", "status": "PASS",
                      "extras": {"layout_digest": frozen}}],
    }
    (report / "layout_receipts.json").write_text(json.dumps(receipt))
    return receipt


def test_receipt_checker_accepts_current_release_basis(tmp_path):
    _good_receipts(tmp_path)
    assert C.check(tmp_path)["verdict"] == "PASS"


def test_receipt_checker_rejects_changed_gds_and_diagnostic(tmp_path):
    receipt = _good_receipts(tmp_path)
    (tmp_path / "phase3/stage4/gds/unit.gds").write_bytes(b"changed\n")
    assert C.check(tmp_path)["verdict"] == "FAIL"
    (tmp_path / "phase3/stage4/gds/unit.gds").write_bytes(
        (tmp_path / "phase3/stage3/pnr/unit.gds").read_bytes())
    receipt["release_scope"] = "DIAGNOSTIC_ONLY"
    (tmp_path / "reports/phase3/layout_receipts.json").write_text(
        json.dumps(receipt))
    assert C.check(tmp_path)["verdict"] == "FAIL"


def test_flow_dependency_declares_the_delivered_layout():
    flow = yaml.safe_load((Path(__file__).resolve().parents[2]
                           / "flow/phase1_phase2_phase3.yaml").read_text())
    steps = {str(step["id"]): step for step in flow["steps"]}
    order = {str(step["id"]): i for i, step in enumerate(flow["steps"])}
    assert {"34", "37"} <= {str(v) for v in steps["31"]["blocks_on"]}
    assert "37" in {str(v) for v in steps["37.3"]["blocks_on"]}
    assert order["34"] < order["37"] < order["31"] < order["36"]


@pytest.mark.parametrize("top", ["chip_top", "subservient"])
def test_prestream_required_reports_have_real_writers(tmp_path, top):
    """Run both declared gate programs, including their failure output paths."""
    project = tmp_path / top
    project.mkdir()
    for name, program, out_rel, argv in R._PRESTREAM_GATES[:2]:
        row = R._run_declared_signoff_gate(project, name, program, out_rel, argv)
        report = project / out_rel
        assert report.is_file(), (top, name, row.status, row.detail)
        assert row.output_files == [str(report)]
        assert row.status != "PASS"  # This fixture has no EDA evidence.
