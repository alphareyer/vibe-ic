"""Bounded Step 15 state handoff remains portable and canonical."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as producers
import execution_modes as em


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _make_published_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "producer"
    raw = project / "phase3/librelane/15-floorplan"
    pnr = project / "phase3/stage3/pnr"
    raw.mkdir(parents=True)
    pnr.mkdir(parents=True)
    floorplan = _write(raw / "floorplan.def", "VERSION 5.8 ;\nDESIGN bounded ;\n")
    odb = _write(raw / "librelane_floorplan.odb", "ODB-BYTES\n")
    netlist = _write(raw / "step15.nl.v", "module bounded; endmodule\n")
    sdc = _write(raw / "constraint.sdc", "create_clock -period 10 [get_ports clk]\n")
    upstream_sdc = _write(pnr / 'constraint.sdc', 'upstream-clock\n')
    canonical_def = pnr / "floorplan.def"
    canonical_odb = pnr / "librelane_floorplan.odb"
    shutil.copyfile(floorplan, canonical_def)
    shutil.copyfile(odb, canonical_odb)

    state = _write(raw / "state_out.json", json.dumps({
        "metrics": {"design__instance__count": 2},
        "def": str(floorplan), "odb": str(odb),
        "nl": str(netlist), "sdc": str(sdc),
    }, sort_keys=True))
    pdn = _write(raw / "pdn-state_out.json", json.dumps({
        "step": "OpenROAD.GeneratePDN", "state": "done",
    }, sort_keys=True))
    handoff = project / "reports/phase3/execution-backend-15-floorplan-handoff.json"
    _write(handoff, json.dumps({
        "state_sha256": producers.snap.sha(state),
        "views": {
            "def": {"source_sha256": producers.snap.sha(canonical_def),
                    "dest_sha256": producers.snap.sha(canonical_def)},
            "odb": {"source_sha256": producers.snap.sha(canonical_odb),
                    "dest_sha256": producers.snap.sha(canonical_odb)},
        },
    }, sort_keys=True))
    producers._publish_step15_bounded_state(project, SimpleNamespace(extras={
        "bounded_state": True,
        "native_state": str(state), "pdn_state": str(pdn),
        "handoff": str(handoff), "floorplan": str(canonical_def),
        "odb": str(canonical_odb),
    }))
    isolated = tmp_path / "isolated-consumer"
    shutil.copytree(pnr, isolated / "phase3/stage3/pnr")
    return project, isolated


def test_step15_publisher_to_isolated_reader_consumes_canonical_def_and_odb(tmp_path):
    _, isolated = _make_published_project(tmp_path)

    state = producers._read_step15_bounded_state(isolated)

    assert state["views"]["def"] == isolated / "phase3/stage3/pnr/floorplan.def"
    assert state["views"]["odb"] == isolated / "phase3/stage3/pnr/librelane_floorplan.odb"
    assert state["views"]["sdc_derived"] == isolated / "phase3/stage3/pnr/step15.constraint.sdc"
    assert (isolated / 'phase3/stage3/pnr/constraint.sdc').read_text() == 'upstream-clock\n'
    assert (isolated / 'phase3/stage3/pnr/step15.constraint.sdc').read_text() == 'create_clock -period 10 [get_ports clk]\n'


def test_step15_reader_refuses_coordinated_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    canonical = pnr / "floorplan.def"
    alternate = pnr / "alternate.def"
    shutil.copyfile(canonical, alternate)
    canonical.write_text("CHANGED-CANONICAL\n")

    record_path = pnr / "step15_state.json"
    handoff_path = pnr / "step15_handoff.json"
    record = json.loads(record_path.read_text())
    handoff = json.loads(handoff_path.read_text())
    alternate_sha = producers.snap.sha(alternate)
    record["views"]["def"] = {
        "path": "phase3/stage3/pnr/alternate.def", "sha256": alternate_sha,
    }
    handoff["views"]["def"]["source_sha256"] = alternate_sha
    handoff["views"]["def"]["dest_sha256"] = alternate_sha
    record["handoff_views"]["def"] = handoff["views"]["def"]
    handoff_path.write_text(json.dumps(handoff, sort_keys=True))
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_VIEW_CHANGED"



@pytest.mark.parametrize('which', ['upstream', 'derived'])
def test_step15_reader_refuses_changed_upstream_or_derived_sdc(tmp_path, which):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    target = pnr / ('constraint.sdc' if which == 'upstream' else 'step15.constraint.sdc')
    target.write_text('changed-' + which + '\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_step15_reader_refuses_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record['views']['sdc_derived']['path'] = 'phase3/stage3/pnr/escape.sdc'
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)

def test_step15_reader_refuses_non_mapping_handoff_with_stable_code(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    handoff_path = pnr / "step15_handoff.json"
    record_path = pnr / "step15_state.json"
    record = json.loads(record_path.read_text())
    handoff_path.write_text("[]\n")
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_ABSENT"


def test_portable_gate_publisher_uses_project_relative_paths(tmp_path):
    import phase3_one_shot_runner as phase3
    project = tmp_path / 'project'; project.mkdir()
    (project / 'phase3/stage3/pnr').mkdir(parents=True)
    report = {'netlist': str(project / 'phase2/stage2/synth/top_synth.v'),
              'sdc': str(project / 'phase3/stage3/pnr/step15.constraint.sdc'),
              'source': str(project / 'phase3/librelane/prelayout/state_out.json'),
              'netlist_sha256': 'a' * 64}
    portable = phase3._portable_prelayout_gate(project, report)
    assert portable['sdc'] == 'phase3/stage3/pnr/step15.constraint.sdc'
    assert not portable['sdc'].startswith('/')
    import librelane_contract as llc
    with pytest.raises(llc.Refusal, match='LL_PRELAYOUT_GATE_PATH_ESCAPE'):
        phase3._portable_prelayout_gate(project, {'sdc': '/outside/constraint.sdc'})


def test_legacy_schema_one_sdc_binding_accepts_then_refuses_changed_bytes(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    derived = record['views'].pop('sdc_derived')
    record.pop('upstream_sdc')
    record['schema'] = 'vibeic/step15-state-handoff/1'
    record['views']['sdc'] = {'path': 'phase3/stage3/pnr/constraint.sdc',
                              'sha256': producers.snap.sha(pnr / 'constraint.sdc')}
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    assert producers._step15_sdc(isolated) == pnr / 'constraint.sdc'
    (pnr / 'constraint.sdc').write_text('legacy-changed\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_legacy_reader_refuses_changed_raw_handoff_even_with_same_sdc(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record.pop('upstream_sdc')
    record['views'].pop('sdc_derived')
    record['views']['sdc'] = {'path': 'phase3/stage3/pnr/constraint.sdc',
                              'sha256': producers.snap.sha(pnr / 'constraint.sdc')}
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    handoff = pnr / 'step15_handoff.json'
    handoff.write_text('changed-handoff\n')
    record = json.loads((pnr / 'step15_state.json').read_text())
    record['handoff']['sha256'] = producers.snap.sha(handoff)
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_legacy_reader_refuses_escaped_canonical_deck_path(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record.pop('upstream_sdc')
    record['views'].pop('sdc_derived')
    record['views']['sdc'] = {'path': '../outside/constraint.sdc', 'sha256': 'a' * 64}
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_legacy_reader_refuses_non_mapping_record(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    (isolated / 'phase3/stage3/pnr/step15_state.json').write_text('[]\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_actual_state_chain_bridge_passes_physical_sdc_to_state_from_direct(tmp_path, monkeypatch):
    import librelane_contract as ll
    import phase3_one_shot_runner as phase3
    _, isolated = _make_published_project(tmp_path)
    config = isolated / 'fixture-config.json'
    config.write_text(json.dumps({'meta': {'step': 'OpenROAD.GlobalPlacementSkipIO'},
                                  'DESIGN_NAME': 'bounded',
                                  'inputs': ['def', 'odb', 'nl', 'sdc'], 'outputs': []}))
    (isolated / 'fixture-config.views.json').write_text(json.dumps({'step': 'OpenROAD.GlobalPlacementSkipIO', 'inputs': ['def', 'odb', 'nl', 'sdc'], 'outputs': []}))
    captured = {}
    monkeypatch.setattr(ll, 'resolve_step_configs', lambda *a, **k: {'OpenROAD.GlobalPlacementSkipIO': config})
    monkeypatch.setattr(phase3, '_resolved_cell_policy', lambda configs, root, pdk: (configs, []))
    def run_chain(project, image, tasks, **kwargs):
        captured['state'] = tasks[0][2]
        return []
    monkeypatch.setattr(ll, 'run_chain', run_chain)
    params = {'image_id': 'fixture-image', 'pdk_name': 'fixture',
              'pdk_root': str(tmp_path), 'step_id': '17', 'top': 'bounded'}
    producers._state_chain(isolated, params, ['OpenROAD.GlobalPlacementSkipIO'], 'floorplan.def')
    state = json.loads(Path(captured['state']).read_text())
    assert state['sdc'] == str((isolated / 'phase3/stage3/pnr/step15.constraint.sdc').resolve())
    assert 'sdc_derived' not in state
