"""A stale technology transcription is refreshable; an operator answer is not."""
from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
import sys

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import _tapeout_declaration as TD  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402


def _project(tmp_path: Path, answer: float, technology: dict | None) -> Path:
    declaration = tmp_path / TD.DECLARATION_REL
    declaration.parent.mkdir(parents=True)
    doc = {"answers": {"database_unit_um": answer}}
    if technology is not None:
        doc[TD.TECHNOLOGY_KEY] = {"database_unit_um": technology}
    declaration.write_text(json.dumps(doc))
    return tmp_path


def _measure(monkeypatch, project: Path):
    monkeypatch.setattr(R, "_read_technology_text",
                        lambda *_: "DATABASE MICRONS 2000 ;\nMANUFACTURINGGRID 0.005 ;")
    monkeypatch.setattr(R, "_pdk_stream_database_unit_um", lambda *_: 0.001)
    monkeypatch.setattr(R._pl, "reports_dir", lambda p: p / "reports")
    pdk = SimpleNamespace(name="test-pdk", tech_lef="tech.lef",
                          cell_gds="cells.gds")
    return R.publish_database_unit_declaration(
        project, pdk, "unused", project / "routed.def")


def test_stale_technology_fact_is_replaced_by_measured_stream(monkeypatch, tmp_path):
    project = _project(tmp_path, 0.0005, {
        "value": 0.0005, "source": "tech.lef:40",
        "statement": "DATABASE MICRONS 2000", "pdk": "test-pdk"})
    (project / "routed.def").write_text("UNITS DISTANCE MICRONS 2000 ;\n")
    got = _measure(monkeypatch, project)
    doc, err = TD.load(project / TD.DECLARATION_REL)
    assert err is None
    assert got["published"] is True
    assert got["superseded_technology_fact"]["source"] == "tech.lef:40"
    assert TD.answer(doc, "database_unit_um") == 0.001
    fact = doc[TD.TECHNOLOGY_KEY]["database_unit_um"]
    assert fact["source"] == "cells.gds:UNITS"
    assert fact["value"] == 0.001


def test_unprovenanced_operator_answer_is_not_overwritten(monkeypatch, tmp_path):
    project = _project(tmp_path, 0.0005, None)
    (project / "routed.def").write_text("UNITS DISTANCE MICRONS 2000 ;\n")
    got = _measure(monkeypatch, project)
    doc, err = TD.load(project / TD.DECLARATION_REL)
    assert err is None
    assert got["published"] is False
    assert TD.answer(doc, "database_unit_um") == 0.0005
    assert TD.TECHNOLOGY_KEY not in doc


def test_default_373_publishes_real_units_before_the_public_xor_entry(
        monkeypatch, tmp_path):
    """Real retained readers/publisher/XOR entry, controlled native frontier.

    No stream comparison is executed or claimed. The retained native process
    fixture supplies the same filled subject that the ordinary producer reads.
    """
    import klayout.db as db
    import execution_backend_producers as EBP
    import _native373_current as native
    import _physical_current as PC
    import librelane_contract as LC
    import librelane_fill_dfm as LF
    import librelane_pv_signoff as PV
    import _klayout_launch as KL
    from test_default_stream_epoch_order import _transport
    from test_default_finishing import refresh_receipt

    project, _, calls, _ = _transport(tmp_path, monkeypatch)
    root = project / 'fixture_pdk'
    # This staged path deliberately has no libs.ref component: the selected
    # PDK identity must come from the producer, not a filename inference.
    tech = root / 'neutral/stream.lef'
    tech.write_text('UNITS\n DATABASE MICRONS 2000 ;\nEND UNITS\n'
                    'MANUFACTURINGGRID 0.005 ;\n')
    cell_gds = root / 'neutral/cells.gds'
    layout = db.Layout(); layout.dbu = 0.001
    cell = layout.create_cell('neutral_cell')
    cell.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 1000, 1000))
    layout.write(str(cell_gds))
    pdk = SimpleNamespace(name='neutral', tech_lef=str(tech), cell_gds=str(cell_gds))

    tool = json.loads((project / LF.RECORD_REL).read_text())['odb']['tool']
    state_path = Path(tool['state'])
    state = json.loads(state_path.read_text())
    for key, name, text in [('nl', 'cal_pg.nl.v', 'module cal_pg; endmodule\n'),
                            ('sdc', 'cal_pg.sdc', 'create_clock -period 10 [get_ports clk]\n')]:
        path = state_path.parent / name
        path.write_text(text)
        state[key] = str(path)
    state_path.write_text(json.dumps(state))
    refresh_receipt(state_path.parent)
    tool['state_sha256'] = LC.digest(state_path)
    LF.update_record(project, 'odb', {'tool': tool})
    stream = LF.default_stream(project, 'top', pdk, '')
    filled = Path(stream['filled_def']).resolve()
    declaration = project / TD.DECLARATION_REL
    declaration.write_text(json.dumps({'answers': {'database_unit_um': 0.001},
        TD.TECHNOLOGY_KEY: {'database_unit_um': {'value': 0.001, 'pdk': 'neutral',
            'source': str(cell_gds) + ':UNITS', 'image': 'SOURCE_FIXTURE',
            'tech_lef': str(tech), 'extra_retained_fact': 'must remain byte exact'}}}, indent=3) + '\n')
    accepted = declaration.read_bytes()
    units = project / 'reports/phase3/technology_units.json'
    assert not units.exists()
    monkeypatch.setattr(EBP, '_parameters', lambda *a: (R, LC, pdk, ''))
    monkeypatch.setattr(LC, 'pdk_root_resolution', lambda *a, **k: {'path': str(root)})
    monkeypatch.setattr(PV, 'tech_lef_overlay', lambda *a: {})
    # A refused base must not launch an alternative native instrument either.
    monkeypatch.setattr(KL, 'find_runner', lambda *a, **k: None)
    monkeypatch.setattr(LC, 'resolve_step_configs', lambda *a, **k: {
        step: project / 'phase3/librelane/34-config' / (step + '.json') for step in native.STEPS})
    reached = []

    class ReachedNativeTransport(Exception):
        pass

    def transport(project_arg, image, config, views, *args, **kwargs):
        reached.append(views)
        assert project_arg == project
        assert views == {'def': filled, 'nl': Path(state['nl']), 'sdc': Path(state['sdc'])}
        assert PC.pdk_of(project) == ('neutral', units)
        raise ReachedNativeTransport

    monkeypatch.setattr(LC, 'state_from_direct', transport)
    # Base lacks the unit publication and the real public XOR returns rc=2
    # before reaching native transport; this assertion is the wiring control.
    import pytest
    try:
        EBP.produce(project, {'step_id': '37.3', 'top': 'cal_pg',
            'execution_mode': 'default', 'pdk_root': str(root), 'image_id': 'SOURCE_FIXTURE'})
    except ReachedNativeTransport:
        pass
    else:
        refusal = json.loads((project / 'reports/phase3/gds_xor.json').read_text())
        assert 'technology_units.json' in refusal['native_dependency_refusal']
        pytest.fail('normal Step37.3 native input refused: technology_units.json was not published')
    assert len(reached) == 1
    assert calls == ['primary', 'seal', 'filler-density']
    assert declaration.read_bytes() == accepted
    report = json.loads(units.read_text())
    assert report['verdict'] == 'PASS' and report['database_unit_um'] == 0.001
    assert report['lef_database_units_per_um'] == report['def_database_units_per_um'] == 2000
    assert report['stream_database_unit_um'] == 0.001 and report['manufacturing_grid_um'] == 0.005
    assert report['pdk'] == pdk.name and report['tech_lef'] == str(tech)
    assert report['cell_gds'] == str(cell_gds) and report['def'] == str(filled)
    assert report['published'] is False and report['published_to'] is None
