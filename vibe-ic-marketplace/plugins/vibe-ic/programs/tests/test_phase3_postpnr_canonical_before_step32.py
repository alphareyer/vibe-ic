"""Route dependent canonical outputs survive a later step 32 refusal."""
from __future__ import annotations

import json
from pathlib import Path

import phase3_one_shot_runner as R
from test_phase3_postpnr_disclosure_and_gds_guard import (
    TOP, OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, _drive, _plan, _project,
    _repair_producer,
)


SPEF = ('*SPEF "IEEE 1481-1998"\n*DESIGN "chip_top"\n'
        + ''.join(f'*D_NET net_{i} 0.1\n*CAP\n1 net_{i} 0.1\n*END\n'
                  for i in range(30)))


def _step32_failed_project(tmp_path: Path, monkeypatch, *, stale_sdc=False) -> Path:
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    pnr = R._pl.pnr_dir(project)
    canonical_sdc = R._pl.constraints_dir(project) / f"{TOP}.sdc"
    if stale_sdc:
        canonical_sdc.write_text(
            "create_clock -name core_clk -period 10 [get_ports clk]\n")
    else:
        canonical_sdc.unlink(missing_ok=True)
    (pnr / "constraint.sdc").write_text(
        "create_clock -name core_clk -period 7 [get_ports clk]\n"
        "set_input_delay 1 -clock core_clk [get_ports data]\n")
    for path in (pnr / "pdn.done", R._pl.cts_dir(project) / "clock_plan.json",
                 R._pl.extracted_dir(project) / f"{TOP}.spef",
                 R._pl.extracted_dir(project) / "parasitic.spef",
                 project / "reports/phase2/sdc_check.json",
                 project / "reports/phase2/gates/spef_extraction.json"):
        path.unlink(missing_ok=True)
    _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    original_pnr = R.step_pnr

    def route(*args, **kwargs):
        row = original_pnr(*args, **kwargs)
        (pnr / "routed.def").write_bytes((pnr / f"{TOP}.def").read_bytes())
        return row

    monkeypatch.setattr(R, "step_pnr", route)

    def extract(_project, _top, _pdk, _container, output, _notes):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(SPEF)
        return True

    monkeypatch.setattr(R, "_emit_spef", extract)
    _repair_producer(monkeypatch, project, "direct", "FAIL", "step 32 refused")
    R.main()
    assert _plan(project)["signoff_spef_repair"]["status"] == "FAIL"
    return project


def test_step32_failure_keeps_current_route_artefacts(tmp_path, monkeypatch):
    project = _step32_failed_project(tmp_path, monkeypatch)
    pnr = R._pl.pnr_dir(project)
    route_sha = R._sha256_file(pnr / "routed.def")

    sdc = R._pl.constraints_dir(project) / f"{TOP}.sdc"
    assert "-period 7" in sdc.read_text()
    assert (project / "reports/phase2/sdc_check.json").is_file()
    pdn = (pnr / "pdn.done").read_text()
    assert f"measured_def_sha256: {route_sha}" in pdn
    assert "PDN status: NOT MEASURED" in pdn
    plan = json.loads((R._pl.cts_dir(project) / "clock_plan.json").read_text())
    assert any(clock["period_ns"] == 7 for clock in plan["clocks"])
    spef = R._pl.extracted_dir(project) / f"{TOP}.spef"
    assert spef.read_text() == SPEF
    extraction = json.loads((project / "reports/phase2/gates/spef_extraction.json").read_text())
    assert extraction["summary"]["d_nets"] == 30
    basis = json.loads((project / "reports/phase3/postpnr_canonical_basis.json").read_text())
    assert basis["routed_def_sha256"] == route_sha
    assert basis["spef_sha256"] == R._sha256_file(spef)


def test_step32_failure_refreshes_old_constraint_value(tmp_path, monkeypatch):
    project = _step32_failed_project(tmp_path, monkeypatch, stale_sdc=True)
    sdc = R._pl.constraints_dir(project) / f"{TOP}.sdc"
    assert "-period 7" in sdc.read_text()
