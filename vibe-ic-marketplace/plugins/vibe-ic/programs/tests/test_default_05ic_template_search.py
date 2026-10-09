"""Canonical Default lookup preserves absent and present operator evidence."""
import json

import pytest
import execution_policy as policy
import phase1_one_shot_runner as phase1
from programs.tests.test_execution_receipt_chain import isolated_transport, real_entry  # noqa: F401
from programs.tests.test_default_d1_entry import _raw_docs_project


def test_derived_answer_documents_reach_the_real_gate_and_remain_bound(tmp_path):
    import area_budget_basis_gen as area
    import submission_template_check
    import tapeout_declaration_check
    from programs.tests.test_r0929_deliverable_consistency import _project
    from programs.tests._route_fixture import stage_owner_route

    project = _project(tmp_path)
    path = project / 'input/step_0_5ic_answers.json'
    answers = json.loads(path.read_text())
    answers['operator_template']['absent_reason'] = (
        'This neutral input specifies no operator, purchased slot or submission '
        'template; the owner explicitly requires an independent DIE delivery.')
    path.write_text(json.dumps(answers) + '\n')
    stage_owner_route(project, 'ic')
    assert area.main([str(project), '--answers', str(path), '--out', str(path)]) == 0
    docs = {str(p.relative_to(project)): p.read_bytes()
            for p in (project / 'input/docs').iterdir() if p.is_file()}
    real_entry('IC', 'default', project)
    policy.bootstrap(project, parameters={'skip_analog': True}, phase1_only=True)
    assert phase1._run_step_0_5ic(project) == 0
    output = project / 'reports/phase1'
    assert all((output / name).is_file() for name in (
        'submission_template.json', 'submission_template_answers.json',
        'submission_template_fetch.json', 'tapeout_declaration.json'))
    # Default is the canonical producer path.  The flow gates consume those
    # reports directly; there is no Controller plan, worker receipt, or
    # adoption record to make a component look like a whole-flow result.
    gate_dir = project / 'reports/execution_gates'
    gate_dir.mkdir()
    assert submission_template_check.main([
        str(project), '--json', str(gate_dir / 'submission_template_check.json')]) == 0
    assert tapeout_declaration_check.main([
        str(project), '--json', str(gate_dir / 'tapeout_declaration_check.json')]) == 0
    assert json.loads((gate_dir / 'submission_template_check.json').read_text())[
        'check']['verdict'] == 'NOT_APPLICABLE'
    assert json.loads((gate_dir / 'tapeout_declaration_check.json').read_text())[
        'verdict'] == 'PASS'
    assert not list((project / 'reports/execution').glob('*/*/plan.json'))
    assert not list((project / 'reports/execution').glob('*/0.5ic/adoption.json'))
    # The direct producer reads the staged documents; it does not create a
    # second mutable copy whose hash would become a separate adoption claim.
    for name, content in docs.items():
        assert (project / name).read_bytes() == content


@pytest.mark.parametrize('present', [False, True], ids=['missing', 'present'])
def test_default_05ic_uses_canonical_search_and_real_gates(tmp_path, present):
    import submission_template_check
    import tapeout_declaration_check
    project = tmp_path / 'subject'
    if present:
        _raw_docs_project(project)
        answer_path = project / 'input/step_0_5ic_answers.json'
        answers = json.loads(answer_path.read_text())
        del answers['operator_template']['path']
        answer_path.write_text(json.dumps(answers) + '\n')
    else:
        # The real field input declared DIE, but supplied neither an operator
        # template nor a reason for its absence. Do not complete its answers.
        answer_path = project / 'input/step_0_5ic_answers.json'
        answer_path.parent.mkdir(parents=True)
        answer_path.write_text(json.dumps({
            'schema': 'vibe-ic/step_0_5ic_answers/1',
            'answers': {'deliverable': 'DIE'},
            'answer_provenance': {'deliverable': {
                'answered_by': 'owner', 'citation': 'Owner requested an IC delivery.'}}
        }) + '\n')
    real_entry('IC', 'default', project)
    owner_bytes = answer_path.read_bytes()
    policy.bootstrap(project, parameters={'skip_analog': True}, phase1_only=True)
    rc = phase1._run_step_0_5ic(project)
    assert answer_path.read_bytes() == owner_bytes
    output = project / 'reports/phase1'
    assert all((output / name).is_file() for name in (
        'submission_template.json', 'submission_template_answers.json',
        'submission_template_fetch.json', 'tapeout_declaration.json'))
    assert not list((project / 'reports/execution').glob('*/*/receipt.json'))
    assert not list((project / 'reports/execution').glob('*/0.5ic/adoption.json'))
    ingest = json.loads((output / 'submission_template.json').read_text())['ingest']
    declaration = json.loads((output / 'tapeout_declaration.json').read_text())
    gate_dir = project / 'reports/execution_gates'
    gate_dir.mkdir()
    submission_rc = submission_template_check.main([
        str(project), '--json', str(gate_dir / 'submission_template_check.json')])
    tapeout_rc = tapeout_declaration_check.main([
        str(project), '--json', str(gate_dir / 'tapeout_declaration_check.json')])
    for name in ('submission_template', 'tapeout_declaration'):
        assert (output / (name + '.json')).stat().st_size
    assert ingest['lookup']['attempted'] is True
    assert len(ingest['lookup']['searched']) == 1
    assert ingest['lookup']['searched'][0].endswith('/input/submission_template_source')
    if present:
        assert submission_rc == 0 and tapeout_rc == 0
        assert rc == 0
        assert ingest['status'] == 'INGESTED'
        assert json.loads((gate_dir / 'submission_template_check.json').read_text())[
            'check']['verdict'] == 'PASS'
        assert json.loads((gate_dir / 'tapeout_declaration_check.json').read_text())[
            'verdict'] == 'PASS'
        assert json.loads((output / 'submission_template.json').read_text())[
            'ingest']['slots'][0]['source_relpath'] == 's1.yaml'
        assert declaration['route'] == 'SHUTTLE'
    else:
        # The producer records the absence; the blocking verdict belongs to
        # the canonical gate that consumes its report.
        assert rc == 0
        assert ingest['status'] == 'ABSENT'
        assert ingest['no_template_reason'] is None
        assert ingest['path_selector']['declared'] is False
        assert submission_rc == 1
        gate = json.loads((gate_dir / 'submission_template_check.json').read_text())
        assert gate['check']['verdict'] == 'FAIL'
        assert 'NO_TEMPLATE_WITHOUT_REASON' in {row['rule'] for row in gate['check']['refusals']}
        # The flow evaluator stops at this first blocking clause.  Running the
        # second gate above is only a direct consumer check in this test; it
        # must not turn the first FAIL into a pass.
        assert tapeout_rc in (0, 2)
        assert not (project / 'input/submission_template/NO_TEMPLATE.txt').exists()
        # The declaration producer retires the ingest marker and publishes the
        # self-tapeout route for a DIE, even though the submission gate still
        # refuses the missing reason.
        assert (project / 'input/submission_template/SELF_TAPEOUT.txt').is_file()
        assert not (project / 'input/submission_template_source').exists()
