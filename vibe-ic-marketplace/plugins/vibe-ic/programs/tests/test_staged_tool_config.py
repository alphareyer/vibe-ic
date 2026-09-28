"""staged_tool_config: a design's staged tool config, the §4.05 deny list, and
the owner's precedence (design L-docs > staged knobs in the ingest map > tool
default). The real module and the real phase-3 ingest map and grammar; the
filesystem is a tmp project.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
stc = importlib.import_module('staged_tool_config')
contract = importlib.import_module('librelane_contract')

#: LibreLane config variables and their declared defaults, MEASURED 2026-09-28
#: on vibeic-eda 0.3.83 (librelane 9cf84954): every Step of the Classic and Chip
#: flows, `get_all_config_variables()`, filtered to the families the map touches.
MEASURED_REGISTRY_0383 = {
    'CTS_DISTANCE_BETWEEN_BUFFERS': {'default': '0'},
    'CTS_MACRO_CLUSTERING_MAX_DIAMETER': {'default': None},
    'CTS_MACRO_CLUSTERING_SIZE': {'default': None},
    'CTS_SINK_CLUSTERING_ENABLE': {'default': 'True'},
    'CTS_SINK_CLUSTERING_MAX_DIAMETER': {'default': None},
    'CTS_SINK_CLUSTERING_SIZE': {'default': None},
    'FP_CORE_UTIL': {'default': '50'},
    'GRT_ADJUSTMENT': {'default': '0.3'},
    'GRT_LAYER_ADJUSTMENTS': {'default': None},
    'GRT_RESIZER_HOLD_MAX_UTIL_PCT': {'default': None},
    'GRT_RESIZER_HOLD_REPAIR_TNS_PCT': {'default': None},
    'GRT_RESIZER_SETUP_MAX_UTIL_PCT': {'default': None},
    'GRT_RESIZER_SETUP_REPAIR_TNS_PCT': {'default': None},
    'PL_RESIZER_HOLD_MAX_UTIL_PCT': {'default': None},
    'PL_RESIZER_HOLD_REPAIR_TNS_PCT': {'default': None},
    'PL_RESIZER_SETUP_MAX_UTIL_PCT': {'default': None},
    'PL_RESIZER_SETUP_REPAIR_TNS_PCT': {'default': None},
    'PL_TARGET_DENSITY_PCT': {'default': None},
    'SYNTH_ABC_BUFFERING': {'default': 'False'},
    'SYNTH_ADDER_TYPE': {'default': 'YOSYS'},
}

#: A QoR-rules artefact's shape (metric -> expected value + operator); the
#: values are neutral, no benchmark's oracle is copied here.
RULES_SHAPE = {'synth__design__instance__area__stdcell': {'value': 1, 'compare': '<=', 'level': 'warning'},
               'globalroute__timing__setup__ws': {'value': 0, 'compare': '>=', 'level': 'warning'}}


def stage(project, rel, text):
    path = project / 'input' / 'reference_flow' / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def resolve(project, design=None, sources=None, registry=MEASURED_REGISTRY_0383, tool='librelane'):
    return stc.resolve(project, tool, design or {}, sources or {}, tool_registry=registry)


def notes_by(record, disposition):
    return [n for n in record['notes'] if n['disposition'] == disposition]


# ── the vocabulary ─────────────────────────────────────────────────────────

def test_the_map_disposes_of_exactly_the_ingest_map():
    """Tier 2 admits ONLY names the phase-3 reference-flow ingest honours, and
    every one of them has a stated disposition here. A knob added to the ingest
    map turns this red until someone decides what it does under the flag."""
    runner = importlib.import_module('phase3_one_shot_runner')
    assert set(stc.LIBRELANE_MAP) == set(runner._ORFS_HONOURED_KNOBS)
    assert stc.ingest_map() == frozenset(runner._ORFS_HONOURED_KNOBS)


def test_every_mapped_variable_is_one_the_image_declares():
    mapped = {t[0] for m in stc.LIBRELANE_MAP.values() if not isinstance(m, str) for t in m}
    assert mapped and mapped <= set(MEASURED_REGISTRY_0383), mapped - set(MEASURED_REGISTRY_0383)


def test_a_stale_map_refuses_only_where_something_is_staged_for_the_target(tmp_path):
    """Review W21: a renamed target with nothing staged for it must not fail
    every flagged run; a staged value it would drop must."""
    registry = dict(MEASURED_REGISTRY_0383)
    del registry['CTS_SINK_CLUSTERING_SIZE']
    row = resolve(tmp_path, registry=registry)['record']['variables']['CTS_SINK_CLUSTERING_SIZE']
    assert (row['tier'], row['disposition']) == ('tool_default', 'TARGET_UNKNOWN')
    stage(tmp_path, 'flow.mk', 'export CTS_CLUSTER_SIZE = 20\n')
    with pytest.raises(contract.Refusal, match='STAGED_KNOB_TARGET_UNKNOWN.*CTS_SINK_CLUSTERING_SIZE is staged'):
        resolve(tmp_path, registry=registry)


def test_only_librelane_has_a_map_in_v1(tmp_path):
    with pytest.raises(contract.Refusal, match='STAGED_TOOL_UNSUPPORTED'):
        resolve(tmp_path, tool='orfs')


# ── precedence ─────────────────────────────────────────────────────────────

def test_design_beats_staged_beats_tool_default(tmp_path):
    stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 50\nexport CTS_CLUSTER_SIZE = 20\n')
    result = resolve(tmp_path, {'FP_CORE_UTIL': 40}, {'FP_CORE_UTIL': 'L19 constraint_declarations'})
    rows, overlay = result['record']['variables'], result['overlay']
    assert rows['FP_CORE_UTIL'] == {'tier': 'design_declared', 'value': 40, 'design_key': 'FP_CORE_UTIL',
                                    'source': 'L19 constraint_declarations'}
    assert [n['name'] for n in notes_by(result['record'], 'SUPERSEDED_BY_DESIGN')] == ['CORE_UTILIZATION']
    assert overlay['CTS_SINK_CLUSTERING_SIZE'][0] == 20
    assert 'input/reference_flow/flow.mk:2 CTS_CLUSTER_SIZE=20' in overlay['CTS_SINK_CLUSTERING_SIZE'][1]
    assert rows['CTS_SINK_CLUSTERING_SIZE']['tier'] == 'staged_tool_config'
    # tier 3 is never emitted: the tool applies its own variable default
    assert 'FP_CORE_UTIL' not in overlay and 'CTS_SINK_CLUSTERING_MAX_DIAMETER' not in overlay
    assert rows['CTS_DISTANCE_BETWEEN_BUFFERS'] == {
        'tier': 'tool_default', 'emitted': False, 'tool_default': '0',
        'source': 'librelane variable default (not emitted)'}
    assert set(overlay) == {v for v, r in rows.items() if r['tier'] == 'staged_tool_config'}


def test_a_design_key_the_contract_would_supersede_still_wins(tmp_path):
    """`resolve_step_configs(overlay=)` pops `PL_TARGET_DENSITY` when the overlay
    carries `PL_TARGET_DENSITY_PCT` (`_LEVER_SUPERSEDES`). A staged PLACE_DENSITY
    must therefore not reach the overlay when the design declared the old key,
    or the staged knob would delete the design's own declaration."""
    stage(tmp_path, 'flow.mk', 'export PLACE_DENSITY = 0.7\n')
    result = resolve(tmp_path, {'PL_TARGET_DENSITY': 0.6}, {'PL_TARGET_DENSITY': 'L9'})
    assert 'PL_TARGET_DENSITY_PCT' not in result['overlay']
    row = result['record']['variables']['PL_TARGET_DENSITY_PCT']
    assert row['tier'] == 'design_declared' and row['design_key'] == 'PL_TARGET_DENSITY'
    for new, olds in contract._LEVER_SUPERSEDES.items():
        assert not (new in result['overlay'] and set(olds) & {'PL_TARGET_DENSITY'}), result['overlay']


def test_nothing_staged_is_all_tool_default(tmp_path):
    result = resolve(tmp_path)
    assert result['overlay'] == {} and result['record']['staged_root'] is None
    assert {r['tier'] for r in result['record']['variables'].values()} == {'tool_default'}


def test_without_a_registry_the_default_is_named_not_invented(tmp_path):
    result = resolve(tmp_path, registry=None)
    row = result['record']['variables']['FP_CORE_UTIL']
    assert row['tool_default'] is None and 'registry not supplied' in row['source']


# ── the §4.05 deny list ────────────────────────────────────────────────────

def test_a_rules_file_is_denied_by_name_and_never_opened(tmp_path, monkeypatch):
    stage(tmp_path, 'flow_rules.json', 'not even json')
    opened = []
    real = Path.read_text

    def spy(self, *a, **k):
        opened.append(self.name)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, 'read_text', spy)
    files = resolve(tmp_path)['record']['files']
    assert files == [{'path': 'input/reference_flow/flow_rules.json', 'disposition': 'DENIED_ORACLE_NAME'}]
    assert 'flow_rules.json' not in opened


def test_a_rules_shaped_file_is_denied_whatever_its_name(tmp_path):
    stage(tmp_path, 'baseline.json', json.dumps(RULES_SHAPE))
    stage(tmp_path, 'settings.json', json.dumps({'FP_CORE_UTIL': 77}))
    files = {f['path'].rsplit('/', 1)[1]: f['disposition'] for f in resolve(tmp_path)['record']['files']}
    assert files == {'baseline.json': 'DENIED_ORACLE_SHAPE', 'settings.json': 'NOT_PARSED'}


def test_a_recipe_inside_an_oracle_tree_never_reaches_the_tool(tmp_path):
    stage(tmp_path, 'golden/flow.mk', 'export CORE_UTILIZATION = 33\n')
    stage(tmp_path, 'flow.mk', 'export CTS_CLUSTER_SIZE = 8\n')
    result = resolve(tmp_path)
    assert 'FP_CORE_UTIL' not in result['overlay']
    assert result['record']['variables']['FP_CORE_UTIL']['tier'] == 'tool_default'
    denied = [f for f in result['record']['files'] if f['disposition'] == 'DENIED_ORACLE_TREE']
    assert [f['path'] for f in denied] == ['input/reference_flow/golden/flow.mk']
    assert all('golden' not in n['source'] for n in result['record']['notes'])


def test_a_tool_example_config_is_never_the_default(tmp_path):
    """A LibreLane-style config (what an example design ships) is not parsed:
    its values reach neither the overlay nor the tool-default tier."""
    stage(tmp_path, 'config.json', json.dumps({'FP_CORE_UTIL': 77, 'PL_TARGET_DENSITY_PCT': 88}))
    stage(tmp_path, 'config.yaml', 'FP_CORE_UTIL: 77\n')
    result = resolve(tmp_path)
    assert result['overlay'] == {}
    assert '77' not in json.dumps(result['record']['variables']) and '88' not in json.dumps(result['record']['variables'])
    assert {f['disposition'] for f in result['record']['files']} == {'NOT_PARSED'}


# ── reading ────────────────────────────────────────────────────────────────

def test_the_last_assignment_in_a_file_wins(tmp_path):
    stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 40\n# later\nCORE_UTILIZATION := 50\n')
    assert resolve(tmp_path)['overlay']['FP_CORE_UTIL'][0] == 50


def test_two_files_disagreeing_refuse_and_agreeing_do_not(tmp_path):
    stage(tmp_path, 'a.mk', 'export CTS_CLUSTER_SIZE = 20\n')
    stage(tmp_path, 'b.tcl', 'set ::env(CTS_CLUSTER_SIZE) 20\n')
    result = resolve(tmp_path)
    assert [c['source'] for c in result['record']['variables']['CTS_SINK_CLUSTERING_SIZE']['from']] == [
        'input/reference_flow/a.mk:1', 'input/reference_flow/b.tcl:1']
    stage(tmp_path, 'b.tcl', 'set ::env(CTS_CLUSTER_SIZE) 24\n')
    with pytest.raises(contract.Refusal, match='STAGED_KNOB_CONFLICT: CTS_SINK_CLUSTERING_SIZE'):
        resolve(tmp_path)


def test_two_names_for_one_variable_disagreeing_refuse(tmp_path):
    stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 50\nexport FP_CORE_UTIL = 40\n')
    with pytest.raises(contract.Refusal, match='STAGED_KNOB_CONFLICT: FP_CORE_UTIL'):
        resolve(tmp_path)


def test_values_are_converted_exactly_or_not_at_all(tmp_path):
    stage(tmp_path, 'flow.mk', '\n'.join([
        'export PLACE_DENSITY = 0.57',           # fraction -> percent, exact (float: 56.99999999999999)
        'export TNS_END_PERCENT = 100',          # one knob, four stage variables
        'export CTS_CLUSTER_DIAMETER = $(DIAM)',  # unexpanded flow variable
        'export CTS_CLUSTER_SIZE = 20.5',         # not an integer
        'export PL_RESIZER_SETUP_MAX_UTIL_PCT = 150',  # out of range
        'export PL_RESIZER_HOLD_MAX_UTIL_PCT = nan',   # not finite
        'export ADDER_MAP_FILE :=',               # a deliberate clear
    ]) + '\n')
    result = resolve(tmp_path)
    overlay = result['overlay']
    assert overlay['PL_TARGET_DENSITY_PCT'][0] == 57 and isinstance(overlay['PL_TARGET_DENSITY_PCT'][0], int)
    assert {k: v[0] for k, v in overlay.items() if 'REPAIR_TNS' in k} == {
        'PL_RESIZER_SETUP_REPAIR_TNS_PCT': 100, 'PL_RESIZER_HOLD_REPAIR_TNS_PCT': 100,
        'GRT_RESIZER_SETUP_REPAIR_TNS_PCT': 100, 'GRT_RESIZER_HOLD_REPAIR_TNS_PCT': 100}
    invalid = {n['name'] for n in notes_by(result['record'], 'VALUE_INVALID')}
    assert invalid == {'CTS_CLUSTER_DIAMETER', 'CTS_CLUSTER_SIZE',
                       'PL_RESIZER_SETUP_MAX_UTIL_PCT', 'PL_RESIZER_HOLD_MAX_UTIL_PCT'}
    for var in ('CTS_SINK_CLUSTERING_MAX_DIAMETER', 'CTS_SINK_CLUSTERING_SIZE',
                'PL_RESIZER_SETUP_MAX_UTIL_PCT', 'PL_RESIZER_HOLD_MAX_UTIL_PCT'):
        assert var not in overlay and result['record']['variables'][var]['tier'] == 'tool_default', var
    assert [n['name'] for n in notes_by(result['record'], 'CLEARED')] == ['ADDER_MAP_FILE']


def test_names_outside_the_map_and_without_an_equivalent_are_recorded(tmp_path):
    stage(tmp_path, 'flow.mk', 'export DESIGN_NAME = top\nexport SWAP_ARITH_OPERATORS = 1\n'
                               'export PLACE_DENSITY_LB_ADDON = 0.2\n')
    record = resolve(tmp_path)['record']
    assert [n['name'] for n in notes_by(record, 'NOT_IN_INGEST_MAP')] == ['DESIGN_NAME']
    assert {n['name'] for n in notes_by(record, 'NO_TOOL_EQUIVALENT')} == {
        'SWAP_ARITH_OPERATORS', 'PLACE_DENSITY_LB_ADDON'}
    assert all(n['reason'] for n in notes_by(record, 'NO_TOOL_EQUIVALENT'))


def test_a_dialect_nothing_recognises_is_stated(tmp_path):
    stage(tmp_path, 'flow.tcl', 'set_flow_var core_util 50\nputs done\n')
    record = resolve(tmp_path)['record']
    [row] = record['files']
    assert (row['path'], row['disposition']) == ('input/reference_flow/flow.tcl', 'UNRECOGNISED_DIALECT')
    assert len(row['sha256']) == 64 and record['notes'] == []


@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0, reason='root reads a mode-000 file')
def test_an_unreadable_recipe_refuses(tmp_path):
    path = stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 50\n')
    path.chmod(0)
    try:
        with pytest.raises(contract.Refusal, match='STAGED_CONFIG_UNREADABLE'):
            resolve(tmp_path)
    finally:
        path.chmod(0o644)


# ── review W21 (wave 4b) ───────────────────────────────────────────────────

def _spy_reads(monkeypatch):
    opened = []
    real = Path.read_text

    def spy(self, *a, **k):
        opened.append(self.name)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, 'read_text', spy)
    return opened


@pytest.mark.parametrize('rel', ['golden.mk', 'expected_results.tcl', 'flow.golden.tcl',
                                 'rules.tcl', 'metrics/flow.mk', 'results/cfg.tcl',
                                 'golden.sdc', 'golden_netlist.v'])
def test_an_oracle_named_file_is_denied_before_it_is_opened_recipes_included(tmp_path, monkeypatch, rel):
    body = 'set ::env(CTS_CLUSTER_SIZE) 9\n' if rel.endswith('.tcl') else 'export CORE_UTILIZATION = 33\n'
    stage(tmp_path, rel, body)
    opened = _spy_reads(monkeypatch)
    result = resolve(tmp_path)
    [row] = result['record']['files']
    assert row['disposition'] in ('DENIED_ORACLE_NAME', 'DENIED_ORACLE_TREE'), row
    assert Path(rel).name not in opened
    assert result['overlay'] == {}


@pytest.mark.parametrize('rel,disposition', [
    ('score/flow.mk', 'DENIED_ORACLE_TREE'), ('canonical_samples/flow.mk', 'DENIED_ORACLE_TREE'),
    ('pnr_ref.tcl', 'DENIED_ORACLE_NAME'), ('verified_cfg.mk', 'DENIED_ORACLE_NAME'),
    ('flow_test.mk', 'DENIED_ORACLE_NAME'), ('testbench.tcl', 'DENIED_ORACLE_NAME')])
def test_the_repos_own_scoring_oracle_forms_are_denied(tmp_path, rel, disposition):
    stage(tmp_path, rel, 'export CORE_UTILIZATION = 21\nset ::env(CORE_UTILIZATION) 21\n')
    result = resolve(tmp_path)
    assert [f['disposition'] for f in result['record']['files']] == [disposition]
    assert result['overlay'] == {}


def test_a_symlink_is_judged_by_its_resolved_target(tmp_path):
    outside = tmp_path / 'elsewhere' / 'golden' / 'config.mk'
    outside.parent.mkdir(parents=True)
    outside.write_text('export CORE_UTILIZATION = 21\n')
    root = tmp_path / 'input' / 'reference_flow'
    root.mkdir(parents=True)
    (root / 'config.mk').symlink_to(outside)
    stage(tmp_path, 'golden/inner.mk', 'export CTS_CLUSTER_SIZE = 7\n')
    (root / 'alias.mk').symlink_to(root / 'golden' / 'inner.mk')
    stage(tmp_path, 'real.mk', 'export CTS_CLUSTER_DIAMETER = 40\n')
    (root / 'plain.mk').symlink_to(root / 'real.mk')
    result = resolve(tmp_path)
    files = {Path(f['path']).name: f['disposition'] for f in result['record']['files']}
    assert files['config.mk'] == 'DENIED_OUTSIDE_STAGED_TREE'
    assert files['alias.mk'] == 'DENIED_ORACLE_TREE' and files['inner.mk'] == 'DENIED_ORACLE_TREE'
    assert files['plain.mk'] == files['real.mk'] == 'READ'
    assert set(result['overlay']) == {'CTS_SINK_CLUSTERING_MAX_DIAMETER'}


def test_only_a_value_make_itself_would_produce_is_emitted(tmp_path):
    stage(tmp_path, 'a.mk', '\n'.join([
        'export CORE_UTILIZATION = 50',
        'export CORE_UTILIZATION += 10',          # Make: "50 10", not a number
        'CTS_CLUSTER_SIZE ?= 12',                 # first assignment: sets
        'CTS_CLUSTER_SIZE ?= 99',                 # already set: no effect
        'TNS_END_PERCENT += 80',                  # += on an undefined name acts as =
        'ifeq ($(PLATFORM),x)',
        'export PLACE_DENSITY = 0.4',
        'else',
        'export PLACE_DENSITY = 0.6',
        'endif',
        'define HELP',
        'CTS_CLUSTER_DIAMETER = 5',
        'endef',
    ]) + '\n')
    stage(tmp_path, 'b.tcl', 'if {$::env(X)} {\n  set ::env(PL_RESIZER_SETUP_MAX_UTIL_PCT) 30\n}\n')
    result = resolve(tmp_path)
    overlay = result['overlay']
    assert 'FP_CORE_UTIL' not in overlay and 'PL_TARGET_DENSITY_PCT' not in overlay
    assert 'PL_RESIZER_SETUP_MAX_UTIL_PCT' not in overlay
    assert 'CTS_SINK_CLUSTERING_MAX_DIAMETER' not in overlay   # inside define/endef
    assert overlay['CTS_SINK_CLUSTERING_SIZE'][0] == 12
    assert overlay['PL_RESIZER_SETUP_REPAIR_TNS_PCT'][0] == 80
    by = {(n['name'], n['disposition']) for n in result['record']['notes']}
    assert ('CORE_UTILIZATION', 'VALUE_INVALID') in by
    assert ('PLACE_DENSITY', 'UNRESOLVED_CONDITIONAL') in by
    assert ('PL_RESIZER_SETUP_MAX_UTIL_PCT', 'UNRESOLVED_CONDITIONAL') in by


@pytest.mark.parametrize('other', ['CORE_UTILIZATION :=', 'CORE_UTILIZATION = $(UTIL)',
                                   'CORE_UTILIZATION += 5\nCORE_UTILIZATION += 6',
                                   'ifdef X\nCORE_UTILIZATION = 50\nendif'])
def test_a_clear_an_invalid_or_a_conditional_in_another_file_is_a_disagreement(tmp_path, other):
    stage(tmp_path, 'a.mk', 'CORE_UTILIZATION = 50\n')
    stage(tmp_path, 'b.mk', other + '\n')
    with pytest.raises(contract.Refusal, match='STAGED_KNOB_CONFLICT: FP_CORE_UTIL'):
        resolve(tmp_path)


def test_files_that_agree_on_a_clear_leave_the_tool_default(tmp_path):
    stage(tmp_path, 'a.mk', 'CORE_UTILIZATION :=\n')
    stage(tmp_path, 'b.mk', 'CORE_UTILIZATION :=\n')
    result = resolve(tmp_path)
    assert 'FP_CORE_UTIL' not in result['overlay']
    assert result['record']['variables']['FP_CORE_UTIL']['tier'] == 'tool_default'


def test_a_disagreement_the_design_already_decides_is_not_a_conflict(tmp_path):
    stage(tmp_path, 'a.mk', 'CORE_UTILIZATION = 50\n')
    stage(tmp_path, 'b.mk', 'CORE_UTILIZATION = 45\n')
    result = resolve(tmp_path, {'FP_CORE_UTIL': 40}, {'FP_CORE_UTIL': 'L19'})
    assert result['record']['variables']['FP_CORE_UTIL']['value'] == 40
    assert [n['source'] for n in notes_by(result['record'], 'SUPERSEDED_BY_DESIGN')] == [
        'input/reference_flow/a.mk:1', 'input/reference_flow/b.mk:1']


def test_an_overflowing_value_is_invalid_not_a_traceback(tmp_path, capsys):
    stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 1e999999999\nexport PLACE_DENSITY = 9e999999999\n')
    result = resolve(tmp_path)
    assert result['overlay'] == {}
    assert {n['name'] for n in notes_by(result['record'], 'VALUE_INVALID')} == {'CORE_UTILIZATION', 'PLACE_DENSITY'}
    assert stc.main([str(tmp_path)]) == 0


def test_a_routing_layer_statement_is_recorded_not_dropped(tmp_path):
    stage(tmp_path, 'fastroute.tcl',
          'set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.2\n')
    [note] = notes_by(resolve(tmp_path)['record'], 'ROUTING_STATEMENT_NOT_MAPPED')
    assert note['source'] == 'input/reference_flow/fastroute.tcl:1'
    assert note['statement'].endswith(' 0.2') and 'GRT_ADJUSTMENT' in note['reason']


# ── the command line ───────────────────────────────────────────────────────

def test_the_cli_writes_the_record_and_refuses_with_rc2(tmp_path, capsys):
    stage(tmp_path, 'flow.mk', 'export CORE_UTILIZATION = 45\n')
    design = tmp_path / 'design.json'
    design.write_text(json.dumps({'CLOCK_PERIOD': 24}))
    design.with_suffix('.provenance.json').write_text(json.dumps({'CLOCK_PERIOD': 'L8'}))
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps(MEASURED_REGISTRY_0383))
    out = tmp_path / 'record.json'
    assert stc.main([str(tmp_path), '--design-config', str(design), '--tool-registry', str(registry),
                     '--out', str(out)]) == 0
    record = json.loads(out.read_text())
    assert record['variables']['FP_CORE_UTIL']['value'] == 45
    assert json.loads(capsys.readouterr().out) == record
    stage(tmp_path, 'other.mk', 'export CORE_UTILIZATION = 46\n')
    assert stc.main([str(tmp_path)]) == 2
    assert 'STAGED_KNOB_CONFLICT' in capsys.readouterr().err


# ── review wave 8 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('rel', [
    # the repo's own oracle-tree words, now in the one name vocabulary
    'oracle.mk', 'qor_oracle.tcl', 'solution.mk', 'answers.tcl', 'ground_truth.mk',
    'oracle_run/flow.mk', 'solutions_v2/flow.mk', 'ground_truth_qor/flow.mk', 'answer_key/cfg.tcl',
    # a word followed by a digit, a space or a capital is still the word
    'golden2.mk', 'results1/flow.mk', 'golden flow.mk', 'GoldenConfig.mk', 'expectedQoR.tcl'])
def test_every_oracle_word_denies_a_name_before_it_is_opened(tmp_path, monkeypatch, rel):
    body = 'set ::env(CTS_CLUSTER_SIZE) 9\n' if rel.endswith('.tcl') else 'export CORE_UTILIZATION = 33\n'
    stage(tmp_path, rel, body)
    opened = _spy_reads(monkeypatch)
    result = resolve(tmp_path)
    [row] = result['record']['files']
    assert row['disposition'] in ('DENIED_ORACLE_NAME', 'DENIED_ORACLE_TREE'), row
    assert Path(rel).name not in opened
    assert result['overlay'] == {}


@pytest.mark.parametrize('rel', ['orfs_config.mk', 'coracle.mk', 'truth_table.mk', 'answerable.mk',
                                 'resultant.mk', 'pre_syn/tcl/sta_run_reports.mk'])
def test_a_word_inside_another_word_is_not_an_oracle_word(tmp_path, rel):
    """Over-match controls: a vocabulary word only counts as a WHOLE word."""
    stage(tmp_path, rel, 'export CORE_UTILIZATION = 33\n')
    result = resolve(tmp_path)
    assert [f['disposition'] for f in result['record']['files']] == ['READ']
    assert result['overlay']['FP_CORE_UTIL'][0] == 33


def test_an_assignment_after_a_closed_block_is_unconditional_again(tmp_path):
    """endif, endef and a Tcl `}` each close their block: the knob after it is
    read (review wave 8 -- nothing pinned the closings)."""
    stage(tmp_path, 'a.mk', '\n'.join([
        'ifeq ($(PLATFORM),x)', 'export PLACE_DENSITY = 0.4', 'endif',
        'export CORE_UTILIZATION = 50',
        'define HELP', 'CTS_CLUSTER_DIAMETER = 5', 'endef',
        'CTS_CLUSTER_SIZE = 12',
    ]) + '\n')
    stage(tmp_path, 'b.tcl', 'if {$::env(X)} {\n  set ::env(PL_RESIZER_SETUP_MAX_UTIL_PCT) 30\n}\n'
                             'set ::env(TNS_END_PERCENT) 80\n')
    overlay = resolve(tmp_path)['overlay']
    assert overlay['FP_CORE_UTIL'][0] == 50
    assert overlay['CTS_SINK_CLUSTERING_SIZE'][0] == 12
    assert overlay['PL_RESIZER_SETUP_REPAIR_TNS_PCT'][0] == 80
    assert 'CTS_SINK_CLUSTERING_MAX_DIAMETER' not in overlay


def test_a_directory_link_and_a_link_loop_are_recorded_not_dropped(tmp_path):
    golden = tmp_path / 'shared' / 'golden'
    golden.mkdir(parents=True)
    (golden / 'flow.mk').write_text('export CORE_UTILIZATION = 21\n')
    stage(tmp_path, 'real/cts.mk', 'export CTS_CLUSTER_SIZE = 7\n')
    root = tmp_path / 'input' / 'reference_flow'
    (root / 'platform').symlink_to(golden, target_is_directory=True)
    (root / 'alias_dir').symlink_to(root / 'real', target_is_directory=True)
    (root / 'loop.mk').symlink_to(root / 'loop.mk')
    result = resolve(tmp_path)
    files = {f['path'].split('reference_flow/', 1)[1]: f['disposition'] for f in result['record']['files']}
    assert files['platform'] == 'DENIED_OUTSIDE_STAGED_TREE'
    assert files['alias_dir'] == 'NOT_TRAVERSED'
    assert files['loop.mk'] == 'DENIED_UNRESOLVABLE_LINK'
    assert files['real/cts.mk'] == 'READ'
    assert 'FP_CORE_UTIL' not in result['overlay']
    assert result['overlay']['CTS_SINK_CLUSTERING_SIZE'][0] == 7
