"""Finite F2 source controls. These do not stand in for native qualification."""
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_adapters_frontend as frontend
import execution_frontend_worker as worker


def context(project, *, roots=('input',), excluded=()):
    files, pop = frontend.lexical_tree(project, roots=roots, exclusions=excluded)
    return frontend.FrontendContext('P0', 'a'*40, files,
        {'metric': 'source_qualification', 'direction': 'min'}, ('native_producer',),
        project=project, roots=roots, exclusions=excluded, population=frontend.stable(pop))


@pytest.fixture
def project(tmp_path):
    (tmp_path/'input').mkdir()
    # Existing actual policy artifact, never a design oracle or benchmark.
    src = frontend.PROGRAMS/'data/execution_modes_portfolio.json'
    (tmp_path/'input/decl.json').write_bytes(src.read_bytes())
    return tmp_path


@pytest.mark.parametrize('mutation', ('added', 'deleted', 'changed', 'directory_added', 'directory_deleted', 'alias_retarget'))
def test_current_population_refuses_change(project, mutation):
    target = project/'input/decl.json'
    (project/'input/empty').mkdir()
    if mutation == 'alias_retarget':
        (project/'input/equal.json').write_bytes(target.read_bytes())
        (project/'input/current.json').symlink_to('decl.json')
    c = context(project); before = c.binding()
    if mutation == 'added':
        (project/'input/new.v').write_text('module additional; endmodule\n')
    elif mutation == 'deleted':
        target.unlink()
    elif mutation == 'changed':
        target.write_text('{"declaration_changed":true}\n')
    elif mutation == 'directory_added':
        (project/'input/new').mkdir()
    elif mutation == 'directory_deleted':
        (project/'input/empty').rmdir()
    else:
        (project/'input/current.json').unlink()
        (project/'input/current.json').symlink_to('equal.json')
    with pytest.raises(em.Refusal, match='FRONTEND_INPUT_POPULATION_CHANGED'):
        c.binding()


def test_output_is_mutable_and_upstream_report_is_input(project):
    (project/'reports').mkdir();(project/'reports/upstream.json').write_text('{"coverage":0.97}\n')
    c = context(project, roots=('input','reports'), excluded=('reports/output.json',))
    before = c.binding()
    (project/'reports/output.json').write_text('{"producer":"new"}\n')
    assert c.binding() == before
    (project/'reports/upstream.json').write_text('{"coverage":0.0}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_INPUT_POPULATION_CHANGED'):
        c.binding()


@pytest.mark.parametrize('kind', ('escape', 'unresolved', 'cycle'))
def test_unsafe_alias_is_refused(project, kind):
    link = project/'input/link'
    if kind == 'escape':
        link.symlink_to('/etc/passwd')
    elif kind == 'unresolved':
        link.symlink_to('no-such-target')
    else:
        link.symlink_to('.')
    with pytest.raises(em.Refusal):
        context(project)


def test_source_and_lease_refresh_is_blocking(project):
    source = project/'source.py';source.write_text('producer_version=1\n')
    lease = project/'lease.json';lease.write_text('{"cpus":1}\n')
    c = replace(context(project), sources=((str(source),em.digest(source)),),
                resources=((str(lease),em.digest(lease)),))
    c.binding()
    lease.write_text('{"cpus":2}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_SOURCE_OR_RESOURCE_CHANGED'):
        c.binding()
    lease.write_text('{"cpus":1}\n');source.write_text('producer_version=2\n')
    with pytest.raises(em.Refusal, match='FRONTEND_SOURCE_OR_RESOURCE_CHANGED'):
        c.binding()


@pytest.mark.parametrize('value', (None, {}, {'status':'NOT_PROVEN'}, {'status':'INCONCLUSIVE'}, {'all_proved':True}, {'passed':None}, True, False))
def test_unknown_or_presence_is_not_pass(value):
    assert worker.status(value) == 'NOT_MEASURED'


@pytest.mark.parametrize('value', ({'passed':False}, {'status':'FAIL'}, [{'status':'PASS'},{'status':'FAIL'}]))
def test_native_failure_is_preserved(value):
    assert worker.status(value) == 'FAIL'


def test_missing_selected_generation_cannot_match_missing_authority(project):
    c = context(project)
    class UnusedController:
        def _generation_current(self, generation):
            pytest.fail('missing generation reached issuer')
    for value in ({}, {'status':'ADOPTED','selected_generation':None},
                  {'status':'ADOPTED','selected_generation':{}}):
        with pytest.raises(em.Refusal):
            worker.consume_frontend(project,c,UnusedController(),project,value)


def test_added_output_is_not_hidden_by_digest_subset(project):
    root = project/'output';root.mkdir();(root/'netlist.v').write_text('module valid; endmodule\n')
    first = worker.output_manifest(root)
    (root/'extra.json').write_text('{"unexpected":true}\n')
    assert worker.output_manifest(root) != first
    (root/'alias').symlink_to('netlist.v')
    with pytest.raises(em.Refusal, match='FRONTEND_OUTPUT_ALIAS'):
        worker.output_manifest(root)


def test_physical_missing_and_invalid_declaration_refuse(project):
    with pytest.raises(em.Refusal):
        worker.declaration_handoff(project, {}, {'board_present':False})
    missing = project/'input/physical.json'
    with pytest.raises(em.Refusal):
        worker.declaration_handoff(project, {'declaration':str(missing)}, {'board_present':False})
    missing.write_text('{"board_present":false,"answered_by":"agent"}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_PHYSICAL_DECLARATION_INVALID'):
        worker.physical_declaration(project, {'board_present':False,
            'declaration_path':'input/physical.json'})


def test_typed_current_declaration_preserves_identity(project):
    path = project/'input/decl.json'
    result = worker.declaration_handoff(project, {'declaration':path}, {'observed':'input'})
    assert result['declaration'] == str(path)
    assert result['declaration_sha256'] == em.digest(path)
    assert worker.path_parameters({'declaration':path}) == {'declaration':str(path)}
    for invalid in (None, {}, 123):
        with pytest.raises(em.Refusal, match='FRONTEND_PATH_TYPE_INVALID'):
            worker.declaration_handoff(project, {'declaration':invalid}, {'observed':'input'})


def test_step_roots_bind_upstream_and_ignore_unrelated_downstream(project):
    upstream = project/'reports/upstream';upstream.mkdir(parents=True)
    (upstream/'timing.json').write_text('{"slack":-0.1}\n')
    roots = worker.input_roots(project, {'input_roots':{'input':project/'input',
        'reports/upstream':upstream}})
    c = context(project, roots=roots); before = c.binding()
    unrelated = project/'phase3/final';unrelated.mkdir(parents=True)
    (unrelated/'late.gds').write_bytes(b'unrelated downstream bytes')
    assert c.binding() == before
    (upstream/'new.json').write_text('{"corner":"additional"}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_INPUT_POPULATION_CHANGED'):
        c.binding()


def test_outputs_all_uses_actual_canonical_expansion_and_missing_inputs_refuse(project):
    specs = worker.required_input_specs('1')
    assert 'phase1/generated_docs/L8_RTL_CONSTANTS.json' in specs
    roots = worker.required_input_roots('1')
    assert 'phase1/generated_docs' in roots
    files, _ = frontend.lexical_tree(project, roots=roots)
    with pytest.raises(em.Refusal, match='FRONTEND_REQUIRED_INPUT_MISSING'):
        worker.validate_step_inputs(project,'1',roots,files)


def test_partial_glob_root_cannot_hide_additional_rtl(project):
    rtl = project/'phase2/stage1/rtl';rtl.mkdir(parents=True)
    file = rtl/'first.sv';file.write_text('module first; endmodule\n')
    files, _ = frontend.lexical_tree(project,roots=('phase2/stage1/rtl/first.sv',))
    with pytest.raises(em.Refusal, match='FRONTEND_REQUIRED_INPUT_ROOT_NOT_BOUND'):
        worker.validate_step_inputs(project,'P0',('phase2/stage1/rtl/first.sv',),files)


@pytest.mark.parametrize('value', (None, [], {'input':'/other/project/input'}, ['../input']))
def test_step_roots_have_no_global_fallback(project, value):
    with pytest.raises(em.Refusal):
        worker.input_roots(project, {'input_roots':value})


def test_canonical_adoption_journals_have_no_wildcards():
    for sid in frontend.STEP_IDS:
        paths = worker.adoption_paths(sid)
        assert paths and all(not any(ch in name for ch in '*?[') for name in paths)
        assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in paths)


def test_f1_lease_uses_real_parent_ticks_and_container_ram(project):
    import os
    stat = Path('/proc/self/stat').read_text()
    ticks = int(stat[stat.rindex(')')+2:].split()[19])
    directory = project/'lease';directory.mkdir()
    # Source fixture for validation only; this never requests native launch or
    # represents a capacity-issued admission.
    data = {'parent_pid':os.getpid(), 'parent_start_ticks':str(ticks),
            'cpus':1, 'host_ram_mb':512, 'container_ram_mb':1024}
    (directory/'lease.json').write_text(json.dumps(data))
    assert worker.lease_current(directory,{}) == data
    data['parent_start_ticks'] = str(ticks+1)
    (directory/'lease.json').write_text(json.dumps(data))
    with pytest.raises(em.Refusal, match='FRONTEND_RESOURCE_LEASE_NOT_LIVE'):
        worker.lease_current(directory,{})
    for bad in (ticks, '0', '01', '-1', '\u0661'):
        data['parent_start_ticks'] = bad
        (directory/'lease.json').write_text(json.dumps(data))
        with pytest.raises(em.Refusal, match='FRONTEND_RESOURCE_LEASE_INVALID'):
            worker.lease_current(directory,{})


def test_native_facts_are_parsed_current_file_and_not_a_path(project):
    path = project/'input/facts.json'
    facts = {'source_fixture':'never_native_admission', 'image_id':'immutable-test-identity'}
    path.write_text(json.dumps(facts))
    p = {'native_facts':facts, 'native_facts_file':path}
    assert worker.native_facts(p) == (path,em.digest(path))
    with pytest.raises(em.Refusal, match='FRONTEND_NATIVE_FACTS_CHANGED'):
        worker.native_facts(dict(p,native_facts_sha256='0'*64))
    path.write_text('{"source_fixture":"changed"}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_NATIVE_FACTS_MISMATCH'):
        worker.native_facts(p)


def test_f199_root_labels_preserve_actual_canonical_paths(project):
    roots = worker.input_roots(project, {'input_roots':{
        'source_input':project/'input', 'declaration':project/'input/decl.json',
        'native_facts':project.parent/'capacity-facts.json'}})
    assert roots == ('input','input/decl.json')


def test_f199_external_root_alias_retarget_and_missing_population(project):
    from dataclasses import replace
    first = project.parent/(project.name+'-facts-a.json')
    second = project.parent/(project.name+'-facts-b.json')
    link = project.parent/(project.name+'-facts-current.json')
    first.write_text('{"source_fixture":true}\n')
    second.write_bytes(first.read_bytes())
    link.symlink_to(first.name)
    files, population = frontend.lexical_tree(link, 'external/native_facts')
    assert population['external/native_facts']['alias'] == first.name
    c = replace(context(project), external_populations=(
        ('external/native_facts',str(link),frontend.stable(population)),))
    c.binding()
    link.unlink();link.symlink_to(second.name)
    with pytest.raises(em.Refusal, match='FRONTEND_EXTERNAL_POPULATION_CHANGED'):
        c.binding()
    missing = project.parent/(project.name+'-missing-control')
    _, before = frontend.lexical_tree(missing, 'external/missing')
    assert before['external/missing']['kind'] == 'missing'
    c = replace(context(project), external_populations=(
        ('external/missing',str(missing),frontend.stable(before)),))
    c.binding();missing.mkdir()
    with pytest.raises(em.Refusal, match='FRONTEND_EXTERNAL_POPULATION_CHANGED'):
        c.binding()


def test_f199_adoption_paths_are_disjoint_and_include_declared_outputs():
    for sid in frontend.STEP_IDS:
        paths = worker.adoption_paths(sid)
        assert all(not any(a != b and a.startswith(b+'/') for b in paths) for a in paths)
    assert 'plugin_output/declaration.json' in worker.adoption_paths('1')
    assert any(p == 'phase2/stage1/formal' or p.startswith('phase2/stage1/formal/')
               for p in worker.adoption_paths('5'))


def test_aggregate_canonical_pass_does_not_invent_per_program_pass():
    actual = {'status':'PASS', 'program_execution_records':[
        {'cmd':'sdc_syntax_check . --json reports/sdc.json', 'verdict':'PASS', 'rc':0, 'exit_code':0},
        {'cmd':'stage1_compliance .', 'verdict':'FAIL', 'rc':1, 'exit_code':1}]}
    assert worker.measured_program_gates(actual, (
        'sdc_syntax_check','pvt_matrix_check','stage1_compliance')) == {
        'sdc_syntax_check':'PASS','pvt_matrix_check':'NOT_MEASURED',
        'stage1_compliance':'NOT_MEASURED'}


@pytest.mark.parametrize('value', ({'passed':False,'not_measured':True},
    {'status':'FAIL','not_measured':True}, {'passed':True,'verdict':'FAIL'}))
def test_measured_failure_survives_partial_unmeasured_metadata(value):
    assert worker.status(value) == 'FAIL'


@pytest.mark.parametrize('event', ('accept','pre_refusal','concurrent_change','post_refusal'))
def test_selected_publication_preserves_old_work_and_rolls_back_only_own_writes(project, event):
    selected = project.parent/(project.name+'-selected');selected.mkdir()
    run = project.parent/(project.name+'-run');run.mkdir()
    rel = 'reports/audit/phase2/rtl_elab.json'
    old = project/rel;old.parent.mkdir(parents=True);old.write_text('old canonical bytes\n')
    untouched = old.with_name('old-unselected.json')
    untouched.write_text('unrelated owner bytes\n')
    untouched_mtime = untouched.stat().st_mtime_ns
    fresh = selected/rel;fresh.parent.mkdir(parents=True);fresh.write_text('exact selected bytes\n')
    (project/'input/alias.json').symlink_to('decl.json')
    c = context(project)
    journal = project/'provenance.jsonl';journal.write_text('old journal\n')
    (selected/'provenance.jsonl').write_text('old journal\nselected update\n')
    c = replace(c,journal_current={'provenance.jsonl':em.digest(journal)})
    original = c.binding()
    outputs = {rel:em.digest(fresh),'provenance.jsonl':em.digest(selected/'provenance.jsonl')}
    def before():
        if event == 'pre_refusal':
            raise em.Refusal('SOURCE_TEST_PREPUBLICATION_REFUSAL','')
        if event == 'concurrent_change':
            old.write_text('concurrent owner bytes\n')
    def after():
        assert old.read_text() == 'exact selected bytes\n'
        assert c.binding() == original
        if event == 'post_refusal':
            raise em.Refusal('SOURCE_TEST_POSTPUBLICATION_REFUSAL','')
    if event == 'accept':
        _, warning = worker.publish_selected(project,selected,outputs,run,c,original,before,after)
        assert warning is None
        assert journal.read_text() == 'old journal\nselected update\n'
    else:
        with pytest.raises(em.Refusal):
            worker.publish_selected(project,selected,outputs,run,c,original,before,after)
        expected = 'concurrent owner bytes\n' if event == 'concurrent_change' else 'old canonical bytes\n'
        assert old.read_text() == expected
        assert journal.read_text() == 'old journal\n'
        assert c.binding() == original
    assert (project/'input/alias.json').is_symlink()
    assert untouched.read_text() == 'unrelated owner bytes\n'
    assert untouched.stat().st_mtime_ns == untouched_mtime
    assert not list(project.glob('.vibeic-rtl-txn.*'))


def test_actual_semantic_consumer_refutes_wrong_clock_edge():
    # Neutral source-level graph for a known consumer. Native graph production
    # is the separately requested example and remains NOT_MEASURED here.
    import formal_structural_check as actual
    graph={'modules':{'neutral':{'ports':{'clk':{'direction':'input','bits':[2]},
        'q':{'direction':'output','bits':[3]}}, 'cells':{'state':{'type':'$dff',
        'parameters':{'CLK_POLARITY':'1','WIDTH':'1'},'connections':{'CLK':[2],'D':[3],'Q':[3]}}},
        'netnames':{'q':{'bits':[3],'hide_name':0}}}}}
    good=actual.check_claim(graph,'neutral',{'rule':'clock_edge','signal':'clk','value':'posedge'})
    bad=actual.check_claim(graph,'neutral',{'rule':'clock_edge','signal':'clk','value':'negedge'})
    assert good['verdict']=='PASS'
    assert bad['verdict']=='REFUTED'
