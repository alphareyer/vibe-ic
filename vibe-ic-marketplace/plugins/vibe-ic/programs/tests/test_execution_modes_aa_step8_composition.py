"""Focused controls for the accepted Analog + Step-8 Adapter composition.

These tests exercise the three frozen conflict regions: the dual Adapter
schema, serialized identity/route binding, and live Git source authority.
They use only neutral finite-tool fixtures or a temporary Git repository.
"""
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from programs.tests import test_execution_modes as H
import execution_modes as em


def _head_tree() -> tuple[str, str]:
    root = Path(__file__).parents[2]
    head = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    return head, tree


def _source_bound_fixture() -> tuple[em.Adapter, str]:
    head, tree = _head_tree()
    arm = replace(H.adapter(), source_sha=head, source_tree=tree,
                  source_tree_sha=tree, route=('neutral.transform',))
    return arm, head


def test_dual_schema_aliases_register_and_serialize_all_material_identity():
    arm, _ = _source_bound_fixture()
    registry = em.Registry()
    registry.register(arm)
    observed = registry.adapters('1')[0]
    identity = observed.identity()
    assert observed.source_tree == observed.source_tree_sha
    assert identity['source_tree'] == observed.source_tree
    assert identity['source_tree_sha'] == observed.source_tree_sha
    assert identity['route'] == ['neutral.transform']


def test_contradictory_aliases_refuse_before_source_validation():
    head, tree = _head_tree()
    arm = replace(H.adapter(), source_sha=head, source_tree=tree,
                  source_tree_sha='0' * 40)
    with pytest.raises(em.Refusal, match='SOURCE_TREE_ALIAS_CONFLICT'):
        em.Registry().register(arm)


def test_route_cannot_supply_missing_tree_evidence_through_empty_alias():
    with pytest.raises(em.Refusal, match='SOURCE_TREE_REQUIRED_FOR_ROUTE'):
        em.Registry().register(replace(H.adapter(), route=('missing.tree',)))


def test_stale_tracked_source_refuses_the_canonical_identity():
    head, tree = _head_tree()
    arm = replace(H.adapter(), source_sha='0' * 40, source_tree=tree)
    with pytest.raises(em.Refusal, match='ADAPTER_SOURCE_IDENTITY_MISMATCH'):
        em.Registry().register(arm)


def test_dirty_tracked_source_refuses_before_live_execution(tmp_path):
    repo = tmp_path / 'source-repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
    source = repo / 'worker.py'
    source.write_text('value = 1\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'worker.py'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'source'], check=True)
    commit, tree = _git_identity(repo)
    arm = SimpleNamespace(
        arm_id='dirty', source_sha=commit, source_tree=tree, source_tree_sha='',
        source_files={str(source): em.digest(source)},
    )
    source.write_text('value = 2\n')
    with pytest.raises(em.Refusal, match='SOURCE_TREE_DIRTY'):
        em.Controller._source_current(arm)


def _git_identity(repo: Path) -> tuple[str, str]:
    return (
        subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
        subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
    )


def test_route_mutation_is_bound_to_receipt_identity(tmp_path):
    arm, head = _source_bound_fixture()
    ctx = H.context(tmp_path)
    ctx = replace(ctx, source_sha=head)
    controller = H.controller(arm)
    root = tmp_path / 'run'
    controller.run(ctx, root)
    receipt = H.read(root)
    mutated = replace(arm, route=('neutral.mutated',))
    with pytest.raises(em.Refusal, match='STALE_OR_UNBOUND_RECEIPT'):
        em.Controller._eligible(receipt, ctx, mutated)
