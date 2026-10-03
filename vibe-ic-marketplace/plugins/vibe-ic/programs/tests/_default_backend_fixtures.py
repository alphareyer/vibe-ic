"""Neutral retained-file fixtures. These are source controls, never native evidence."""
import json
import re
from pathlib import Path

import librelane_contract as LC
import librelane_fill_dfm as LF

IMAGE = 'ghcr.io/vibeic/vibeic-eda@sha256:' + 'a' * 64


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def producer(project, step, before, after, *, lane, config_folder='34-config', name='neutral'):
    project = project.resolve()
    folder = project / 'phase3/librelane' / lane
    folder.mkdir(parents=True, exist_ok=True)
    root = project / 'fixture_pdk'
    (root / 'neutral').mkdir(parents=True, exist_ok=True)
    switch = project / 'phase3/librelane_switch.json'
    doc = json.loads(switch.read_text()) if switch.exists() else {}
    doc.update(image=IMAGE, pdk='neutral', pdk_root_host=str(root))
    put(switch, doc)
    put(project / 'phase3/librelane_pdk_root.provenance.json',
        {'source': 'declared', 'path': str(root), 'pdk': 'neutral'})
    config = put(project / 'phase3/librelane' / config_folder / f'{step}.json',
                 {'meta': {'step': step}, 'DESIGN_NAME': name, 'PDK': 'neutral'})
    initial = put(project / 'phase3/librelane' / f'{lane.replace("/", "-")}-state.json', before)
    put(folder / 'state_in.json', before)
    put(folder / 'state_out.json', after)
    put(folder / 'config.json', json.loads(config.read_text()))
    fp = {'step': step, 'image': IMAGE, 'config': LC.digest(config),
          'state': LC.digest(initial),
          'state_files': {str(p): LC.digest(p) for p in LC._walk_paths(
              {k: v for k, v in before.items() if k != 'metrics'})}, 'config_files': {}}
    put(folder / 'input_fingerprint.json', fp)
    put(folder / 'pdk_root.json', {'cli_pdk_root': '/pdk',
        'stated_by': 'run_chain(pdk_root=...)', 'mounts_under_it': [[str(root / 'neutral'), '/pdk/neutral']]})
    (folder / 'invocation.log').write_text('SOURCE_FIXTURE: substituted tool process\n')
    hashes = {str(p.relative_to(folder)): LC.digest(p) for p in folder.rglob('*')
              if p.is_file() and p.name != 'vibeic_receipt.json'}
    for path in LC._walk_paths({k: v for k, v in after.items() if k != 'metrics'}):
        if path.is_relative_to(folder):
            hashes[str(path.relative_to(folder))] = LC.digest(path)
    receipt = put(folder / 'vibeic_receipt.json', {'input': fp, 'sha256': hashes})
    return folder, {'image': IMAGE, 'config': str(config), 'input_state': str(initial),
        'mounts': [[str(root / 'neutral'), '/pdk/neutral']], 'receipt_sha256': LC.digest(receipt)}


def fill_record(project, *, subject=None, def_text=None, refusals=()):
    pnr = project / 'phase3/stage3/pnr'
    subject = subject or pnr / 'top.def'
    text = subject.read_text()
    name = re.search(r'\bDESIGN\s+(\S+)\s*;', text).group(1)
    folder = project / 'phase3/librelane/34/01-openroad-fillinsertion'
    folder.mkdir(parents=True, exist_ok=True)
    filled = folder / 'filled.def'
    filled.write_text(def_text or text + '\n# filled\n')
    (folder / 'openroad-fillinsertion.log').write_text('SOURCE_FIXTURE filler_placement\n')
    folder, binding = producer(project, LF.ODB_FILL_STEP,
        {'def': str(subject.resolve()), 'metrics': {}}, {'def': str(filled.resolve()), 'metrics': {}},
        lane='34/01-openroad-fillinsertion', name=name)
    return {'step': LF.ODB_FILL_STEP, 'state': str(folder / 'state_out.json'),
        'state_sha256': LC.digest(folder / 'state_out.json'), 'subject': str(subject.resolve()),
        'subject_sha256': LC.digest(subject), 'filled_def': str(filled.resolve()),
        'filled_def_sha256': LC.digest(filled), 'binding': binding,
        'patterns': {}, 'census': {'added': 7, 'removed': 0,
            'per_class': {'decap': 5, 'fill': 2, 'other': 0}, 'per_master': {}, 'class_of_master': {}},
        'occupancy': {'after': {'row_utilization_pct': 100.0}},
        'supply_ownership': {'verdict': 'PASS'}, 'refusals': list(refusals)}


def density_record(project, gds, count, *, lane='34-fill/02-klayout-density', config_folder='34-config'):
    folder, _ = producer(project, 'KLayout.Density',
        {'gds': str(gds.resolve()), 'metrics': {}},
        {'gds': str(gds.resolve()), 'metrics': {LF.DENSITY_METRIC: count}},
        lane=lane, config_folder=config_folder)
    return {LF.DENSITY_METRIC: count, 'state': str(folder / 'state_out.json'),
        'state_sha256': LC.digest(folder / 'state_out.json'),
        'subject': str(gds), 'subject_sha256': LC.digest(gds), 'rules': {'DENSITY': count}}


def consume_fill(project, record):
    pnr = project / 'phase3/stage3/pnr'
    LC.handoff_to_direct(Path(record['state']), {'def': pnr / 'filled.def'},
                        project / 'phase3/tool_arms/34/odb_handoff.json')
    return record
