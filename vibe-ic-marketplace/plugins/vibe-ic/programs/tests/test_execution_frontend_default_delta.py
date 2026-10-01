"""Changed F2 source obligations only; no native or whole-family qualification."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_adapters_frontend as frontend
import execution_frontend_worker as worker
import execution_modes as em
import execution_production as production
import execution_step_protocol as protocol
import phase3_one_shot_runner as phase3


@pytest.fixture(autouse=True)
def _default_diagnostic_directory(tmp_path, monkeypatch):
    if not os.environ.get('F2_CHANGED_PROOF_DETAILS'):
        monkeypatch.setenv('F2_CHANGED_PROOF_DETAILS', str(tmp_path/'diagnostics'))


def detail(name, value):
    destination = Path(os.environ['F2_CHANGED_PROOF_DETAILS'])
    destination.mkdir(exist_ok=True)
    (destination/(name+'.json')).write_text(json.dumps(value, indent=2)+'\n')


def test_current_typed_pdk_is_the_same_configuration(tmp_path):
    library = tmp_path/'neutral.lib'
    library.write_text('library(neutral) {}\n')
    typed = phase3.PdkConfig(name='source_fixture', liberty=str(library),
        tech_lef='', cell_lef='', cell_gds=None, site='', drc_deck=None)
    result = worker.pdk_config({'pdk': typed})
    assert result is typed
    assert asdict(worker.pdk_config(protocol.json_parameters({'pdk': typed}))) == asdict(typed)
    detail('typed-pdk', {'configuration': asdict(result),
        'typed_identity_preserved': result is typed, 'physical_views': 'NOT_MEASURED'})


def test_real_handoff_refuses_equal_byte_external_alias_retarget(tmp_path, monkeypatch):
    project = tmp_path/'project'; (project/'input').mkdir(parents=True)
    declaration = project/'input/board.json'
    declaration.write_text('{"board_present":false,"answered_by":"owner"}\n')
    outside = tmp_path/'external'; outside.mkdir()
    for name in ('first.json', 'equal.json'):
        (outside/name).write_text('{"control":"same bytes"}\n')
    alias = outside/'current.json'; alias.symlink_to('first.json')
    parameters = {'route':'DIE', 'objective':{'metric':'source_control','direction':'min'},
        'declaration':declaration, 'physical_handoff':{'board_present':False,
        'declaration_path':'input/board.json'}, 'input_roots':{'input':project/'input',
        'control':outside}}
    parameters, extra = production.frontdoor_parameters(
        dict(parameters, project=project, step_id='6'), production.policy.request())
    sha, sources = production.source_identity(extra)
    request = protocol.StepRequest('6', project, parameters, tmp_path/'lease-not-admitted',
        tmp_path/'request.json', sha, sources)
    prepared = frontend.prepare(request)
    assert prepared.disposition == 'external_handoff'
    # Complete the actual canonical declaration read, then model another
    # owner's equal-byte namespace change before the handoff returns.
    original = worker.declaration_handoff
    def owner_changes_namespace(*args, **kwargs):
        result = original(*args, **kwargs)
        alias.unlink(); alias.symlink_to('equal.json')
        return result
    monkeypatch.setattr(worker, 'declaration_handoff', owner_changes_namespace)
    with pytest.raises(em.Refusal, match='FRONTEND_EXTERNAL_POPULATION_CHANGED'):
        frontend.prepare(request)
    observed = prepared.handoff['facts']['external_populations']
    population = json.loads(next(pop for prefix, root, pop in observed if prefix == 'external/control'))
    assert population['external/control/current.json']['alias'] == 'first.json'
    assert population['external/control/current.json']['sha256'] == em.digest(outside/'equal.json')
    assert alias.readlink() == Path('equal.json')
    assert not request.lease.exists()
    detail('external-handoff', {'source_sha':sha, 'source_files':sources,
        'original_external_populations':observed, 'owner_alias_after_refusal':str(alias.readlink()),
        'refusal':'FRONTEND_EXTERNAL_POPULATION_CHANGED', 'native_admission':'NOT_ADMITTED'})


def test_rtl_default_preserves_actual_authored_guard(tmp_path):
    project = tmp_path/'project'; (project/'phase1/input_doc').mkdir(parents=True)
    text = ('Module name:\n fixture\nInput ports:\n clk: Clock.\n'
        ' reset: Synchronous active-high reset.\n din: One-bit input.\n'
        'Output ports:\n matched: One-bit output.\n'
        'This Moore FSM searches for the sequence "101" in the input bit stream. '
        'After finding it, set matched to 1, forever, until reset. '
        'Synchronous active-high reset clears matched to zero.\n')
    (project/'phase1/input_doc/design.md').write_text(text)
    rtl = project/'phase2/stage1/rtl'; rtl.mkdir(parents=True)
    authored = rtl/'fixture.v'
    authored.write_text('module fixture(input clk, reset, din, output matched);\n'
                        'assign matched = din;\nendmodule\n')
    before = (authored.read_bytes(), authored.stat().st_mtime_ns)
    raw = worker.produce(project, '1', {'ic_class':'unregistered_source_fixture'})
    assert (authored.read_bytes(), authored.stat().st_mtime_ns) == before
    detail('rtl-authored-guard', {'raw_producer':raw, 'authored_sha256':em.digest(authored),
        'authored_bytes_and_mtime_preserved':True, 'native_accuracy':'NOT_MEASURED'})


def test_current_program_first_expert_handoff_carries_actual_skill_bytes(tmp_path):
    project = tmp_path/'project'; (project/'phase1/input_doc').mkdir(parents=True)
    (project/'phase1/input_doc/design.md').write_text(
        'Module name: fixture. The operator must still supply the complete transition rules.\n')
    raw = worker.produce(project, '1', {'ic_class':'unregistered_source_fixture'})
    assert worker.status(raw) == 'NOT_MEASURED'
    handoff = raw['expert_handoff']
    assert handoff['skill'] == 'spec-to-rtl'
    assert handoff['design_verdict'] == 'NOT_MEASURED'
    assert Path(handoff['staged_path']).read_bytes() == Path(handoff['source_path']).read_bytes()
    assert handoff['staged_sha256'] == em.digest(Path(handoff['source_path']))
    assert not (project/'phase2/stage1/rtl/fixture.v').exists()
    detail('rtl-expert-handoff', {'raw_producer':raw,
        'required_expert_sources':worker.rtl_expert_sources({'ic_class':'unregistered_source_fixture'}),
        'expert_execution':'NOT_MEASURED'})


def test_actual_step8_clause_records_disclose_not_run_and_blocking_failure(tmp_path):
    project = tmp_path/'project'
    constraints = project/'phase2/stage2/constraints'; constraints.mkdir(parents=True)
    sdc = constraints/'fixture.asic.sdc'
    timing = 'set_input_delay 1 -clock clk [get_ports rst_n]\n'
    sdc.write_text('create_clock -name clk -period 10 [get_ports clk]\n'+timing)
    rtl = project/'phase2/stage1/rtl'; rtl.mkdir(parents=True)
    (rtl/'fixture.v').write_text('module fixture(input clk,rst_n,output reg [3:0] count);\n'
        "always @(posedge clk or negedge rst_n) if (!rst_n) count<=0; else count<=count+1'b1;\n"
        'endmodule\n')
    # Required output comes from its real producer, before canonical checking.
    seed = subprocess.run([sys.executable, str(PROGRAMS/'sdc_syntax_check.py'),
        str(project), '--json', str(project/'reports/phase2/sdc_check.json')],
        capture_output=True, text=True)
    assert seed.returncode == 0, (seed.stdout, seed.stderr)
    observation = worker.record_step_outputs(project, '8')
    assert any(row['rel'] == 'reports/phase2/sdc_check.json'
               for row in observation['step_observation']['produced'])
    positive = worker.semantic_consumer(project, '8')
    detail('canonical-step8-positive', positive)
    assert positive['status'] == 'PASS', positive
    assert positive['canonical_row'] == worker.contract('8')
    measured = worker.measured_program_gates(positive, ('sdc_syntax_check','sdc_validator_check'))
    assert measured == {'sdc_syntax_check':'PASS','sdc_validator_check':'NOT_MEASURED'}
    assert positive['declared_not_applicable']
    assert worker.design_verdict('PASS', positive['status']) == 'PASS'
    # A real unconditional gate failure stays blocking under the same AST.
    sdc.write_text('create_clock -name clk -period -1 [get_ports clk]\n'+timing)
    negative = worker.semantic_consumer(project, '8')
    assert negative['status'] == 'FAIL', negative
    assert worker.design_verdict('PASS', negative['status']) == 'FAIL'
    assert any(row.get('verdict') == 'FAIL' and row.get('exit_code') == 1
               for row in negative['program_execution_records'])
    detail('canonical-step8', {'positive':positive, 'negative':negative,
        'positive_measured_program_gates':measured,
        'candidate_eligibility':'F1 shared clause-policy dependency; no default adoption claimed'})
