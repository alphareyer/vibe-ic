"""Route dependent canonical outputs survive a later step 32 refusal."""
from __future__ import annotations

import json
import os
from pathlib import Path

import phase3_one_shot_runner as R
from test_phase3_postpnr_disclosure_and_gds_guard import (
    TOP, OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, _drive, _plan, _project,
    _repair_producer, _pdk,
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


def _routed_project(root: Path) -> tuple[Path, Path]:
    pnr = R._pl.pnr_dir(root)
    pnr.mkdir(parents=True, exist_ok=True)
    route = pnr / "routed.def"
    body = "VERSION 5.8 ;\nDESIGN chip_top ;\nDIEAREA ( 0 0 ) ( 1000 1000 ) ;\nEND DESIGN\n"
    route.write_text(body)
    (pnr / f"{TOP}.def").write_text(body)
    (pnr / "constraint.sdc").write_text(
        "create_clock -name core_clk -period 7 [get_ports clk]\n"
        "set_input_delay 1 -clock core_clk [get_ports data]\n")
    return root, route


def test_same_route_is_byte_idempotent_and_changed_route_reextracts(
        tmp_path, monkeypatch):
    project, route = _routed_project(tmp_path)
    emitted = []

    def extract(_project, _top, _pdk, _container, output, _notes):
        emitted.append(R._sha256_file(route))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(SPEF + f"// route {emitted[-1]}\n")
        return True

    monkeypatch.setattr(R, "_emit_spef", extract)
    pdk = _pdk(project)
    first = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert first.status == "NOT_MEASURED", first.detail
    products = [R._pl.constraints_dir(project) / f"{TOP}.sdc",
                project / "reports/phase2/sdc_check.json",
                R._pl.pnr_dir(project) / "pdn.done",
                R._pl.cts_dir(project) / "clock_plan.json",
                R._pl.extracted_dir(project) / f"{TOP}.spef",
                project / "reports/phase2/gates/spef_extraction.json",
                project / "reports/phase3/postpnr_canonical_basis.json"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in products}
    again = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert again.status == "NOT_MEASURED" and not again.output_files
    assert emitted == [R._sha256_file(route)]
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in products}

    old_times = (route.stat().st_atime_ns, route.stat().st_mtime_ns)
    new_body = route.read_text().replace("1000 1000", "2000 2000")
    route.write_text(new_body)
    (R._pl.pnr_dir(project) / f"{TOP}.def").write_text(new_body)
    os.utime(route, ns=old_times)
    changed = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert changed.status == "NOT_MEASURED", changed.detail
    assert len(emitted) == 2 and emitted[0] != emitted[1]
    assert products[4].read_bytes() != before[products[4]][0]
    basis = json.loads(products[-1].read_text())
    assert basis["routed_def_sha256"] == emitted[-1]
    assert basis["spef_sha256"] == R._sha256_file(products[4])
    assert "gds" not in " ".join(str(p) for p in changed.output_files)


def test_missing_route_or_extraction_never_claims_spef(tmp_path, monkeypatch):
    project = tmp_path / "project"
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    (pnr / "constraint.sdc").write_text(
        "create_clock -name core_clk -period 7 [get_ports clk]\n")
    pdk = _pdk(project)
    missing = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert missing.status == "NOT_MEASURED"
    assert not (project / "reports/phase3/postpnr_canonical_basis.json").exists()
    assert not (R._pl.constraints_dir(project) / f"{TOP}.sdc").exists()

    _, route = _routed_project(project)
    monkeypatch.setattr(R, "_emit_spef", lambda *a, **k: False)
    failed = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert failed.status == "NOT_MEASURED"
    assert not (R._pl.extracted_dir(project) / f"{TOP}.spef").exists()
    assert not (project / "reports/phase2/gates/spef_extraction.json").exists()
    basis = json.loads((project / "reports/phase3/postpnr_canonical_basis.json").read_text())
    assert basis["status"] == "NOT_MEASURED"
    assert basis["routed_def_sha256"] == R._sha256_file(route)
    assert basis["spef_sha256"] is None


def test_same_route_changed_extraction_pdk_reextracts(tmp_path, monkeypatch):
    project, _route = _routed_project(tmp_path)
    pdk = _pdk(project)
    tech = Path(pdk.tech_lef)
    emitted = []

    def extract(_project, _top, _pdk, _container, output, _notes):
        emitted.append(R._sha256_file(tech))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(SPEF + f"// tech {emitted[-1]}\n")
        return True

    monkeypatch.setattr(R, "_emit_spef", extract)
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert len(emitted) == 1

    tech.write_text(tech.read_text() + "LAYER M1 TYPE ROUTING ; END M1\n")
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert len(emitted) == 2
    basis = json.loads((project / "reports/phase3/postpnr_canonical_basis.json").read_text())
    assert basis["extraction_inputs"]["pdk_files"][str(tech)] == emitted[-1]
    assert basis["spef_sha256"] == R._sha256_file(
        R._pl.extracted_dir(project) / f"{TOP}.spef")


def test_same_route_changed_declared_rc_rules_reextracts(tmp_path, monkeypatch):
    project, _route = _routed_project(tmp_path)
    pdk = _pdk(project)
    rules = tmp_path / "nom.rules"
    rules.write_text("model one\n")
    monkeypatch.setattr(R, "_openrcx_ruleset_declaration", lambda *_: {
        "status": "DECLARED", "declaration": [],
        "corners": {"nom": {"path": str(rules), "pattern": "nom_*",
                            "declared_by": "fixture config"}}, "detail": ""})
    emitted = []

    def extract(_project, _top, _pdk, _container, output, _notes):
        emitted.append(R._sha256_file(rules))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(SPEF + f"// rules {emitted[-1]}\n")
        return True

    monkeypatch.setattr(R, "_emit_spef", extract)
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    rules.write_text("model two\n")
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert len(emitted) == 2 and emitted[0] != emitted[1]
    basis = json.loads((project / "reports/phase3/postpnr_canonical_basis.json").read_text())
    assert basis["extraction_inputs"]["pdk_files"][str(rules)] == emitted[-1]


def test_step22_mode_change_reextracts_same_route(tmp_path, monkeypatch):
    project, _route = _routed_project(tmp_path)
    pdk = _pdk(project)
    mode = ["dual"]
    monkeypatch.setattr(R, "_librelane_signoff_modes", lambda _p: (mode[0], "direct"))
    calls = []

    def extract(_project, _top, _pdk, _container, output, _notes):
        calls.append(mode[0])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(SPEF + f"// mode {mode[0]}\n")
        return True

    monkeypatch.setattr(R, "_emit_spef", extract)
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    mode[0] = "direct"
    R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    assert calls == ["dual", "direct"]
    basis = json.loads((project / "reports/phase3/postpnr_canonical_basis.json").read_text())
    assert basis["extraction_inputs"]["mode"] == "direct"


def test_unmeasured_pdn_and_failed_sdc_do_not_pass_receipt(tmp_path, monkeypatch):
    project, _route = _routed_project(tmp_path)
    monkeypatch.setattr(R, "_emit_spef", lambda _p, _t, _d, _c, out, _n:
                        (out.parent.mkdir(parents=True, exist_ok=True),
                         out.write_text(SPEF), True)[-1])
    pdk = _pdk(project)
    first = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    basis_path = project / "reports/phase3/postpnr_canonical_basis.json"
    basis = json.loads(basis_path.read_text())
    assert first.status == basis["status"] == "NOT_MEASURED"
    assert basis["outputs"]["pdn"]["status"] == "NOT_MEASURED"
    assert basis["outputs"]["sdc_check"]["status"] == "PASS"

    (R._pl.pnr_dir(project) / "constraint.sdc").write_text(
        "create_clock -period banana [get_ports clk]\n")
    second = R._canonicalize_postpnr_prerequisites(project, TOP, pdk, "")
    basis = json.loads(basis_path.read_text())
    assert second.status == basis["status"] == "FAIL"
    assert basis["outputs"]["sdc_check"]["status"] == "FAIL"
