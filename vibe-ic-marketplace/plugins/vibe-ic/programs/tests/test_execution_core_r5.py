"""R5 controls exercise the tracked providers and actual emitted run records."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest
from programs.tests import test_execution_modes as H
from programs.tests.test_execution_core_r4 import issued_context
from programs.tests.test_execution_receipt_chain import isolated_transport
import execution_modes as em


@pytest.mark.parametrize('spelling', ['warning-separate', 'xoption-separate',
                                    'warning-attached', 'xoption-attached',
                                    'clustered-warning', 'delimiter'])
def test_INDEPENDENCE_INTERPRETER_OPTION_ENTRY(tmp_path, spelling):
    ctx = issued_context(tmp_path)
    first = H.adapter('a')
    option_value = str((H.VARIANTS / 'b.py').resolve())
    options = {
        'warning-separate': ('-W', option_value),
        'xoption-separate': ('-X', option_value),
        'warning-attached': ('-W' + option_value,),
        'xoption-attached': ('-X' + option_value,),
        'clustered-warning': ('-BW', option_value),
        'delimiter': ('-B', '--'),
    }[spelling]
    first = replace(first, source_files={**first.source_files,
                                        option_value: em.digest(Path(option_value))})
    alias = replace(first, arm_id='alias', tool_id='alias', engine_families=('alias',),
                    components=tuple(replace(c, argv=(c.argv[0], *options, *c.argv[1:]))
                                     for c in first.components))
    controller = H.controller(first, alias)
    root = tmp_path / 'run'
    result = controller.run(ctx, root)
    plan = json.loads((root / 'plan.json').read_text())
    assert plan['arms'] == ['a']
    assert result['candidate_statuses'] == {'a': 'ELIGIBLE'}
    assert not (root / 'alias').exists()
    for row in plan['portfolio']:
        for invocation in row['invocation_sources'].values():
            assert invocation['entry'] == first.components[0].argv[1]
    assert plan['portfolio'][1]['admission'] == 'SAME_ENGINE_FAMILY'
    assert plan['portfolio'][0]['invocation_sources'] != plan['portfolio'][1]['invocation_sources']


def test_interpreter_options_preserve_distinct_actual_entries(tmp_path):
    ctx = issued_context(tmp_path)
    first, second = H.adapter('a'), H.adapter('b')
    second_entry = second.components[0].argv[1]
    second = replace(second, components=tuple(
        replace(c, argv=(c.argv[0], '-B', '-Wignore', *c.argv[1:]))
        for c in second.components))
    controller = H.controller(first, second)
    root = tmp_path / 'run'
    result = controller.run(ctx, root)
    plan = json.loads((root / 'plan.json').read_text())
    assert plan['arms'] == ['a', 'b']
    assert result['candidate_statuses'] == {'a': 'ELIGIBLE', 'b': 'ELIGIBLE'}
    assert {r['invocation_sources']['transform']['entry'] for r in plan['portfolio']} == {
        first.components[0].argv[1], second_entry}
    assert controller.adopt(ctx, root, H.choice(ctx, root))['status'] == 'ADOPTED'
    assert controller.verify_adoption(ctx, root)['status'] == 'ADOPTED'


@pytest.mark.parametrize('arguments', [('-cprint(1)',), ('-m', 'site'), ('-',),
                                     ('--unknown-option',), ('--help',), ('-W',)])
def test_unresolved_python_entry_refuses_before_registration(tmp_path, arguments):
    arm = H.adapter('a')
    component = arm.components[0]
    revised = replace(arm, components=(replace(
        component, argv=(component.argv[0], *arguments)),))
    with pytest.raises(em.Refusal, match='ENTRY_SOURCE_UNBOUND'):
        H.controller(revised)


@pytest.mark.parametrize('replacement', ['attribute-hook', 'descriptor', 'borrowed-binding'])
def test_CONTROLLER_MODULE_CONTEXT_SUBSTITUTION(tmp_path, monkeypatch, replacement):
    for name in list(os.environ):
        if name.startswith('VIBEIC_EXECUTION'):
            monkeypatch.delenv(name, raising=False)
    original = H.NeutralContext
    def value(name):
        if sys._getframe(2).f_code.co_name == '_context_binding':
            return 'PROGRAM_DEFAULT' if name == 'intent_label' else ''
        return 'USER_EXPLICIT_ULTRA' if name == 'intent_label' else 'a' * 64
    def getattribute(self, name):
        if name in ('intent_label', 'request_digest'):
            return value(name)
        return object.__getattribute__(self, name)
    attributes = {'__module__': H.__name__, 'binding': original.binding}
    if replacement == 'attribute-hook':
        attributes['__getattribute__'] = getattribute
    elif replacement == 'descriptor':
        for name in ('intent_label', 'request_digest'):
            attributes[name] = property(lambda self, name=name: value(name),
                                        lambda self, value: None)
    foreign = type('NeutralContext', (em.Context,), attributes)
    monkeypatch.setattr(H, 'NeutralContext', foreign)
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    result = controller.run(ctx, root, 'ultra-mode')
    assert result['status'] == 'REFUSED'
    assert result['reason'] == 'CONTEXT_IMPLEMENTATION_UNTRUSTED'
    assert controller.adopt(ctx, root, None)['status'] == 'REFUSED'
    assert not (root / 'a').exists()
    assert not (root / 'issued-plan.json').exists()
    assert not (root / 'adoption.json').exists()
    assert not (root / 'selected').exists()
    assert json.loads((root / 'result.json').read_text())['status'] == 'REFUSED'


def test_production_context_module_slot_does_not_grant_class_authority(tmp_path, monkeypatch):
    class Foreign(em.Context):
        pass
    monkeypatch.setattr(em, 'Context', Foreign)
    ctx = issued_context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    result = controller.run(ctx, root)
    assert result['reason'] == 'CONTEXT_IMPLEMENTATION_UNTRUSTED'
    assert not (root / 'a').exists()
    assert not (root / 'issued-plan.json').exists()
    assert not (root / 'adoption.json').exists()
    assert not (root / 'selected').exists()
