"""T72: the streamed layout's own antenna and route evidence."""
import json
import phase3_one_shot_runner as R


def test_antenna_gates_consume_only_the_declared_router_report():
    for gates in (R._PRESTREAM_GATES, R._FINAL_LAYOUT_GATES):
        antenna = next(spec for spec in gates if spec[0].startswith("antenna"))
        assert antenna[3] == ("--mode", "antenna", "--under",
                              "reports/phase3/antenna.rpt")
    deck = R._antenna_isolated_scoped_retry_tcl("seed.odb", "candidate.odb", "diode")
    assert "candidate.odb.targets.txt" in deck
    assert ".targets.rpt" not in deck


def test_final_route_census_is_measured_and_bound_to_layout(tmp_path, monkeypatch):
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    (pnr / "routed.def").write_text("VERSION 5.8 ;\nNETS 1 ;\nEND NETS\nEND DESIGN\n")
    class Pdk:
        tech_lef = tmp_path / "tech.lef"
        cell_lef = tmp_path / "cells.lef"
        macro_lefs = ()
    for lef in (Pdk.tech_lef, Pdk.cell_lef):
        lef.write_text("VERSION 5.8 ;\n")
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("current-layout", ""))
    monkeypatch.setattr(R, "_to_container_path", lambda path, container: path)
    calls = []
    def openroad(container, command, **kw):
        calls.append(command)
        deck = (tmp_path / "reports/phase3/prestream_route_census.tcl").read_text()
        assert "read_def " + str(pnr / "routed.def") in deck
        assert "PRESTREAM_UNROUTED_NETS" in deck
        return 0, ("PRESTREAM_UNROUTED_NETS: 0\nPRESTREAM_ABUTTED_NETS: 2\n"
                   "PRESTREAM_UNROUTED_SHAPE_BLIND: 0\n"
                   "PRESTREAM_CENSUS_COMPLETE: 1\n"), ""
    monkeypatch.setattr(R, "_docker_exec", openroad)
    rec, error = R._prestream_route_census(tmp_path, "unit", Pdk(), "eda", "current-layout")
    assert error == ""
    assert rec["unrouted_nets"] == 0 and rec["abutted_nets"] == 2
    assert rec["layout_digest"] == "current-layout"
    assert len(calls) == 1
    saved = json.loads((tmp_path / "reports/phase3/prestream_route_census.json").read_text())
    assert saved == rec


def test_failed_or_stale_route_census_is_never_saved_as_measurement(tmp_path, monkeypatch):
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    (pnr / "routed.def").write_text("VERSION 5.8 ;\n")
    class Pdk:
        tech_lef = tmp_path / "tech.lef"
        cell_lef = tmp_path / "cells.lef"
        macro_lefs = ()
    monkeypatch.setattr(R, "_to_container_path", lambda path, container: path)
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("different-layout", ""))
    monkeypatch.setattr(R, "_docker_exec", lambda *a, **kw: (0,
        "PRESTREAM_UNROUTED_NETS: 0\nPRESTREAM_ABUTTED_NETS: 0\n"
        "PRESTREAM_UNROUTED_SHAPE_BLIND: 0\nPRESTREAM_CENSUS_COMPLETE: 1\n", ""))
    rec, error = R._prestream_route_census(tmp_path, "unit", Pdk(), "eda", "old-layout")
    assert rec is None and "layout changed" in error
    assert not (tmp_path / "reports/phase3/prestream_route_census.json").exists()
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("old-layout", ""))
    monkeypatch.setattr(R, "_docker_exec", lambda *a, **kw: (1, "", "OpenROAD error"))
    rec, error = R._prestream_route_census(tmp_path, "unit", Pdk(), "eda", "old-layout")
    assert rec is None and "incomplete" in error
    assert not (tmp_path / "reports/phase3/prestream_route_census.json").exists()


def test_prestream_route_row_uses_new_census_instead_of_old_log(tmp_path, monkeypatch):
    import si_mcf_repair
    import si_mcf_verdict_basis
    import drc_feedback_repair
    pnr = R._pl.pnr_dir(tmp_path)
    pnr.mkdir(parents=True)
    (pnr / "routed.def").write_text("VERSION 5.8 ;\nNETS 1 ;\nEND NETS\nEND DESIGN\n")
    (pnr / "placed.def").write_text("VERSION 5.8 ;\n")
    (pnr / "openroad.log").write_text(
        "[INFO DRT-0198] Complete detail routing\n"
        "[INFO DRT-0199] Number of violations = 0\n")
    class Pdk:
        tech_lef = tmp_path / "tech.lef"
        cell_lef = tmp_path / "cells.lef"
        macro_lefs = ()
        drc_deck = None
    for lef in (Pdk.tech_lef, Pdk.cell_lef):
        lef.write_text("VERSION 5.8 ;\n")
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("current-layout", ""))
    monkeypatch.setattr(R, "step_canonicalize_artefacts", lambda *a, **kw:
                        R.StepResult("canonicalize_artefacts", "PASS", 0, "ok"))
    monkeypatch.setattr(R, "_run_declared_signoff_gate", lambda project, name, *a:
                        R.StepResult(name, "PASS", 0, "ok"))
    monkeypatch.setattr(R, "_signoff_regen", lambda *a: False)
    monkeypatch.setattr(R, "_si_mcf_repair_seam", lambda *a: None)
    monkeypatch.setattr(si_mcf_repair, "run_once", lambda *a, **kw: {"decision": "NO_OP"})
    monkeypatch.setattr(si_mcf_verdict_basis, "apply", lambda *a: None)
    monkeypatch.setattr(drc_feedback_repair, "has_reviewed_rule", lambda *a: False)
    monkeypatch.setattr(R, "_to_container_path", lambda path, container: path)
    monkeypatch.setattr(R, "_docker_exec", lambda *a, **kw: (0,
        "PRESTREAM_UNROUTED_NETS: 0\nPRESTREAM_ABUTTED_NETS: 0\n"
        "PRESTREAM_UNROUTED_SHAPE_BLIND: 0\nPRESTREAM_CENSUS_COMPLETE: 1\n", ""))
    R.step_prestream_gate(tmp_path, "unit", Pdk(), "eda")
    rows = json.loads((tmp_path / "reports/phase3/prestream_gate.json").read_text())["gates"]
    route = next(r for r in rows if r["name"] == "route_connectivity")
    assert route["status"] == "PASS", route
    assert route["output_files"] == [str(tmp_path / "reports/phase3/prestream_route_census.json")]


import pytest


@pytest.mark.parametrize('phase', ['prestream', 'final'])
def test_later_signoff_readers_preserve_completed_ir_em_owner_reports(
        tmp_path, phase):
    """Real report checkers write their own scope and retain measured failure.

    Native tools are not invoked: the existing neutral current/limit fixture
    supplies readable reports, segment currents, wire geometry and Jmax.
    """
    import test_em_peak_current_authority_check as current
    specs = R._PRESTREAM_GATES if phase == 'prestream' else R._FINAL_LAYOUT_GATES
    programs = {'ir_drop_report_check.py', 'em_report_check.py',
                'em_peak_current_authority_check.py'}
    selected = [spec for spec in specs if spec[1] in programs]
    aliases = {'ir_drop_signoff.json': 'eda_report_audit:ir_drop',
               'em_signoff.json': 'eda_report_audit:em',
               'em_current_authority.json': 'em_peak_current_authority_check'}
    for peak, expected in [('1.0e-06', 'PASS'), ('5.0', 'FAIL')]:
        project = current._project(tmp_path / expected, peak=peak,
                                   with_csv=True, with_jmax=True)
        reports = project / 'reports/phase3'
        (reports / 'ir_drop.rpt').write_text(
            'OpenROAD PSM IR drop analysis\n'
            'power grid mesh nodes: 12458\n'
            'worst voltage drop: 6.8 mV (0.2% Vdd) static IR\n'
            'worst dynamic IR: 9.1 mV (0.3% Vdd)\n'
            + '# synthetic report detail\n' * 100)
        em_report = reports / 'em.rpt'
        em_report.write_text(em_report.read_text() + '# synthetic report detail\n' * 100)
        retained = {}
        for name, program in aliases.items():
            path = reports / name
            path.write_text(json.dumps({'program': program, 'verdict': 'PASS',
                                        'retained_owner': name}) + '\n')
            retained[path] = path.read_bytes()
        native_inputs = {path: path.read_bytes() for path in (
            reports / 'ir_drop.rpt', reports / 'em.rpt', reports / 'em.json',
            reports / 'em_segments.csv', reports / 'em_jmax.json',
            project / 'phase3/stage3/pnr/routed.def')}
        rows = [R._run_declared_signoff_gate(project, *spec) for spec in selected]
        assert {path: path.read_bytes() for path in retained} == retained
        assert {path: path.read_bytes() for path in native_inputs} == native_inputs
        assert len(rows) == (3 if phase == 'prestream' else 2)
        for spec, row in zip(selected, rows):
            report = project / spec[2]
            assert row.output_files == [str(report)]
            assert report not in retained
            doc = json.loads(report.read_text())
            if spec[1] == 'em_peak_current_authority_check.py':
                assert doc['verdict'] == expected
            else:
                assert doc['passed'] is (row.status == 'PASS')
        authority = next(row for spec, row in zip(selected, rows)
                         if spec[1] == 'em_peak_current_authority_check.py')
        assert authority.status == expected, authority.detail
        if expected == 'PASS':
            assert all(row.status == 'PASS' for row in rows), rows
        else:
            assert any(row.status == 'FAIL' for row in rows), rows
