"""Ordinary Default fill/DFM consumers, with substantive retained-byte mutations."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import _plugin_tree  # noqa: F401
from _hostpaths import require_repo
import _default_backend_fixtures as BF
import librelane_contract as LC
import librelane_fill_dfm as LF
import phase3_one_shot_runner as R
import metal_fill_density_check as MFD
import dfm_screen_check as DFM


def project(tmp_path):
    p = tmp_path / 'project'
    pnr = p / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    # Checked-in tool-facing calibration data, read through the portable resolver.
    artifact = require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic', 'programs',
                            'calibration', 'pg_supply_owned_negative.def')
    for name in ('top.def', 'routed.def'):
        (pnr / name).write_bytes(artifact.read_bytes())
    record = BF.fill_record(p)
    BF.consume_fill(p, record)
    LF.update_record(p, 'odb', {'mode': 'librelane', 'shipped': 'librelane',
        'def_sha256': LC.digest(pnr / 'routed.def'), 'tool': record})
    gds = p / 'phase3/stage4/gds/top.gds'
    gds.parent.mkdir(parents=True)
    gds.write_bytes(b'source fixture stream; geometry is not measured here')
    density = BF.density_record(p, gds, 0)
    LF.update_record(p, 'gds', {'shipped': 'librelane', 'librelane': density})
    switch = p / 'phase3/librelane_switch.json'
    doc = json.loads(switch.read_text())
    doc['steps'] = {'34': 'librelane'}
    BF.put(switch, doc)
    return p, record, density


def test_default_enters_integrated_fill_at_the_ordinary_callsite(tmp_path, monkeypatch):
    p, record, _ = project(tmp_path)
    switch = p / 'phase3/librelane_switch.json'
    doc = json.loads(switch.read_text())
    doc.pop('steps')
    BF.put(switch, doc)
    assert '34' not in json.loads((p / 'phase3/librelane_switch.json').read_text()).get('steps', {})
    monkeypatch.setattr(R, '_librelane_step_ctx', lambda *a: (BF.IMAGE, tmp_path))
    calls = []
    monkeypatch.setattr(LF, 'run_fill_insertion', lambda *a, **k: (calls.append(k), record)[1])
    monkeypatch.setattr(R, '_docker_exec', lambda *a, **k: pytest.fail('second producer ran'))
    assert R._emit_metal_fill(p, 'top', SimpleNamespace(name='neutral'), '',
                              p / 'phase3/stage3/pnr/filled.def', []) is True
    assert len(calls) == 1
    assert MFD.main([str(p), '--json', str(p / 'fill-gate.json')]) == 0
    assert DFM.audit(p)['density_ref']['step34_pass'] is True


@pytest.mark.parametrize('mutation', ['output', 'input', 'config', 'execution', 'wrong_design', 'wrong_stage', 'image', 'pdk'])
def test_default_refuses_unbound_fill_before_publication(tmp_path, monkeypatch, mutation):
    p, record, _ = project(tmp_path)
    if mutation == 'output':
        Path(record['filled_def']).write_text('DESIGN another ;\nEND DESIGN\n')
    elif mutation == 'input':
        Path(record['subject']).write_text('DESIGN another ;\nEND DESIGN\n')
    elif mutation == 'config':
        Path(record['binding']['config']).write_text('{"DESIGN_NAME":"another"}')
    elif mutation == 'execution':
        (Path(record['state']).parent / 'invocation.log').unlink()
    elif mutation == 'wrong_design':
        # Internally consistent output bytes, but for a different design.
        record = BF.fill_record(p, def_text='DESIGN another ;\nEND DESIGN\n')
    elif mutation == 'wrong_stage':
        record['state'] = str(p / 'phase2' / 'state_out.json')
    else:
        switch = p / 'phase3/librelane_switch.json'
        doc = json.loads(switch.read_text())
        if mutation == 'image':
            doc['image'] = BF.IMAGE.replace('a' * 64, 'b' * 64)
        else:
            root = p / 'different-pdk'
            (root / 'neutral').mkdir(parents=True)
            doc['pdk_root_host'] = str(root)
        BF.put(switch, doc)
    monkeypatch.setattr(R, '_librelane_step_ctx', lambda *a: (BF.IMAGE, tmp_path))
    monkeypatch.setattr(LF, 'run_fill_insertion', lambda *a, **k: record)
    notes = []
    ok = R._emit_metal_fill(p, 'top', SimpleNamespace(name='neutral'), '',
                            p / 'phase3/stage3/pnr/filled.def', notes)
    assert ok is False, notes
    assert not (p / 'phase3/stage3/pnr/metal_fill.done').exists()


def test_removed_downstream_handoff_cannot_publish_the_fill_marker(tmp_path, monkeypatch):
    p, record, _ = project(tmp_path)
    monkeypatch.setattr(R, '_librelane_step_ctx', lambda *a: (BF.IMAGE, tmp_path))
    monkeypatch.setattr(LF, 'run_fill_insertion', lambda *a, **k: record)
    monkeypatch.setattr(LC, 'handoff_to_direct', lambda *a, **k: {})
    R._emit_metal_fill(p, 'top', SimpleNamespace(name='neutral'), '',
                        p / 'phase3/stage3/pnr/filled.def', [])
    assert (p / 'phase3/stage3/pnr/metal_fill.done').exists() is False


@pytest.mark.parametrize('mutation', ['subject', 'execution', 'inherited', 'wrong_stage', 'handoff', 'foreign_path'])
def test_row_gate_and_dfm_reject_removed_or_wrong_current_evidence(tmp_path, mutation):
    p, record, density = project(tmp_path)
    assert MFD.tool_arm_findings(p)[0] == []
    state = Path(density['state'])
    if mutation == 'subject':
        Path(density['subject']).write_bytes(b'changed streamed geometry')
    elif mutation == 'execution':
        (state.parent / 'invocation.log').unlink()
    elif mutation == 'inherited':
        before = json.loads((state.parent / 'state_in.json').read_text())
        before['metrics'] = {LF.DENSITY_METRIC: 0}
        BF.producer(p, 'KLayout.Density', before, json.loads(state.read_text()),
                    lane='34-fill/02-klayout-density')
        # No State metric was produced in this step.
    elif mutation == 'wrong_stage':
        BF.producer(p, 'OpenROAD.STAPostPNR', json.loads((state.parent / 'state_in.json').read_text()),
                    json.loads(state.read_text()), lane='34-fill/02-klayout-density')
    elif mutation == 'handoff':
        (p / 'phase3/tool_arms/34/odb_handoff.json').unlink()
    else:
        outside = tmp_path / 'foreign-state.json'
        outside.write_bytes(state.read_bytes())
        density.update(state=str(outside), state_sha256=LC.digest(outside))
        LF.update_record(p, 'gds', {'shipped': 'librelane', 'librelane': density})
    findings, _ = MFD.tool_arm_findings(p)
    assert any(f.severity == 'ERROR' for f in findings), findings
    if mutation != 'handoff':
        assert DFM.audit(p)['density_ref']['step34_pass'] is False


def test_measured_density_fail_stays_fail(tmp_path):
    p, _, density = project(tmp_path)
    bad = BF.density_record(p, Path(density['subject']), 2)
    LF.update_record(p, 'gds', {'shipped': 'librelane', 'librelane': bad})
    findings, stats = MFD.tool_arm_findings(p)
    assert stats['density_errors'] == 2
    assert {f.category for f in findings} == {'DENSITY_DECK_FAIL'}
    assert DFM.audit(p)['density_ref']['errors'] == 2


@pytest.mark.parametrize('mutation', ['none', 'output', 'execution', 'wrong_stage', 'copy_removed', 'measured_fail'])
def test_gds_publication_consumes_the_current_density_result(tmp_path, monkeypatch, mutation):
    p, _, _ = project(tmp_path)
    canonical = p / 'phase3/stage4/gds/top.gds'
    original = canonical.read_bytes()
    filled = p / 'phase3/librelane/34-fill/tool.gds'
    filled.parent.mkdir(parents=True, exist_ok=True)
    filled.write_bytes(original + b'+filled')
    density = BF.density_record(p, filled, 2 if mutation == 'measured_fail' else 0)
    density['filler'] = {'script': 'SOURCE_FIXTURE'}
    if mutation == 'output':
        filled.write_bytes(b'other stream')
    elif mutation == 'execution':
        (Path(density['state']).parent / 'invocation.log').unlink()
    elif mutation == 'wrong_stage':
        old = Path(density['state']).parent
        foreign = p / 'phase2/density'
        import shutil
        shutil.copytree(old, foreign)
        density.update(state=str(foreign / 'state_out.json'),
                       state_sha256=LC.digest(foreign / 'state_out.json'))
    elif mutation == 'copy_removed':
        monkeypatch.setattr(R, '_declared_transform_exec', lambda *a, **k: (True, 'copy removed'))
    ctx = {'mode': 'librelane', 'tool': density, 'doc': {}}
    ok, _ = R._step34_gds_ship(p, canonical, ctx, (False, 'not run'))
    assert ok is (mutation == 'none')
    assert canonical.read_bytes() == (filled.read_bytes() if mutation == 'none' else original)
    if mutation == 'none':
        assert LF.tool_density(p)['status'] == 'MEASURED'
    else:
        assert json.loads((p / LF.RECORD_REL).read_text())['gds']['shipped'] is None


def test_clock_plan_names_the_program_that_actually_executed(tmp_path):
    pnr = tmp_path / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    routed = pnr / 'routed.def'
    routed.write_text('DESIGN neutral ;\nEND DESIGN\n')
    (pnr / 'constraint.sdc').write_text('create_clock -name c -period 10 [get_ports clk]\n')
    plan = tmp_path / 'phase3/stage3/cts/clock_plan.json'
    assert R.emit_clock_plan(tmp_path, plan, routed, pnr, []) == str(plan)
    result = json.loads(plan.read_text())
    assert result['tool'] == 'python'
    assert result['producer'] == 'phase3_one_shot_runner.emit_clock_plan'
    assert 'source_log' not in result
    import clock_plan_check as C
    assert C.main([str(tmp_path), '--json', str(tmp_path / 'clock-gate.json')]) == 0


@pytest.mark.parametrize('mutation', ['none', 'source_bytes', 'report_removed', 'wrong_project',
                                     'provenance_removed', 'wrong_type', 'report_changed'])
def test_ordinary_aggregation_and_its_gate_bind_both_current_products(tmp_path, mutation):
    import signoff_metrics_aggregate as AGG
    import tapeout_docs_gen as TDG
    source = BF.put(tmp_path / 'reports/phase3/drc_router.json',
                    {'summary': {'real_violation_total': 3,
                                 'producers': [{'producer': 'openroad'}]}})
    result = R.step_signoff_metrics_aggregate(tmp_path)
    assert result.status == 'PASS'
    metrics = tmp_path / 'phase3/final/metrics.json'
    report = tmp_path / AGG.REPORT_REL
    assert TDG.load_metrics(metrics)['route__drc_errors'] == 3
    assert json.loads(metrics.read_text())['design__lvs_error__count'] == AGG.NOT_MEASURED
    assert AGG.main([str(tmp_path), '--check']) == 0
    if mutation == 'source_bytes':
        # The value stays 3; only the actual producer bytes changed.
        source.write_text(source.read_text() + '\n ')
    elif mutation == 'report_removed':
        report.unlink()
    elif mutation == 'wrong_project':
        doc = json.loads(report.read_text())
        doc['project'] = str(tmp_path / 'other-design')
        BF.put(report, doc)
    elif mutation == 'provenance_removed':
        doc = json.loads(metrics.read_text())
        doc.pop('__provenance__')
        BF.put(metrics, doc)
    elif mutation == 'wrong_type':
        doc = json.loads(metrics.read_text())
        doc['route__drc_errors'] = 3.0
        BF.put(metrics, doc)
    elif mutation == 'report_changed':
        doc = json.loads(report.read_text())
        doc['measured'] += 1
        BF.put(report, doc)
    assert AGG.main([str(tmp_path), '--check']) == (0 if mutation == 'none' else 1)


@pytest.mark.parametrize('mutation', ['output', 'state', 'execution', 'pdk'])
def test_retained_tool_output_is_rerun_after_byte_mutation(tmp_path, monkeypatch, mutation):
    p = tmp_path / 'p'
    source = p / 'input.v'
    source.parent.mkdir()
    source.write_text('module neutral; endmodule')
    root = p / 'pdk'
    root.mkdir()
    lef = root / 'tech.lef'
    lef.write_text('VERSION 5.8 ;')
    config = BF.put(p / 'config.json', {'meta': {'step': 'OpenROAD.Floorplan'},
        'TECH_LEFS': {'*': '/pdk/tech.lef'}})
    initial = BF.put(p / 'input-state.json', {'nl': str(source)})
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        folder = Path(argv[argv.index('-o') + 1])
        output = folder / 'output.def'
        output.write_text('DESIGN neutral ;\nEND DESIGN\n')
        BF.put(folder / 'state_out.json', {'def': str(output)})
        return SimpleNamespace(returncode=0, stdout='OpenROAD ran', stderr='')
    monkeypatch.setattr(LC, 'image_capability', lambda *a: None)
    monkeypatch.setattr(LC.subprocess, 'run', run)
    kwargs = {'mounts': [(root, '/pdk')], 'pdk_root': '/pdk'}
    steps = [('OpenROAD.Floorplan', config, initial)]
    folder = LC.run_chain(p, BF.IMAGE, steps, **kwargs)[0]
    LC.run_chain(p, BF.IMAGE, steps, **kwargs)
    assert len(calls) == 1
    path = {'output': folder / 'output.def', 'state': folder / 'state_out.json',
            'execution': folder / 'invocation.log', 'pdk': lef}[mutation]
    path.write_text(path.read_text() + '\n')
    LC.run_chain(p, BF.IMAGE, steps, **kwargs)
    assert len(calls) == 2
