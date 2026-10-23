"""Lossless DEF FILLS -> native LibreLane/KLayout stream bridge.

Derives missing FILL purposes from immutable producer rules and a PDK map.
Never edits GDS, the PDK map or fill-rule values. Ambiguous masks/OPC/grid
representations refuse. Geometry is compared per cell without flattening.
This producer/consumer component alone is not final finishing or RCX signoff.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _atomic_artefact import write_json
from librelane_contract import Refusal

SCHEMA = 'vibeic.fill-stream.v1'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(project, path):
    path = Path(path).resolve()
    return {'path': str(path.relative_to(project)) if path.is_relative_to(project) else str(path),
            'sha256': _sha(path)}


def _bound(project, ref):
    if not isinstance(ref, dict) or not ref.get('path') or not ref.get('sha256'):
        raise Refusal('FILL_STREAM_INPUT_UNBOUND', str(ref))
    path = (project / ref['path']).resolve()
    if not path.is_file() or _sha(path) != ref['sha256']:
        raise Refusal('FILL_STREAM_INPUT_CHANGED', str(path))
    return path


def fill_records(def_file):
    text = Path(def_file).read_text()
    units = re.findall(r'\bUNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;', text)
    top = re.findall(r'^\s*DESIGN\s+(\S+)\s*;', text, re.M)
    sections = list(re.finditer(r'(?ms)^\s*FILLS\s+(\d+)\s*;\s*\n(.*?)^\s*END FILLS\s*\n', text))
    if len(units) != 1 or len(top) != 1 or len(sections) > 1:
        raise Refusal('FILL_STREAM_DEF_UNSUPPORTED', 'unique units/design/fill section required')
    shapes = []
    bare = text
    if sections:
        section = sections[0]
        records = [row.strip() for row in section[2].split(';') if row.strip()]
        for row in records:
            m = re.fullmatch(r'-\s+LAYER\s+([A-Za-z_][A-Za-z0-9_]*)\s+RECT\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)', row)
            if not m:
                raise Refusal('FILL_STREAM_MASK_OR_SHAPE_UNSUPPORTED', row)
            name, *coords = m.groups()
            coords = list(map(int, coords))
            if coords[0] >= coords[2] or coords[1] >= coords[3]:
                raise Refusal('FILL_STREAM_INVALID_RECTANGLE', row)
            shapes.append({'layer': name, 'rect': coords})
        if len(shapes) != int(section[1]):
            raise Refusal('FILL_STREAM_CENSUS_MISMATCH', str(def_file))
        bare = text[:section.start()] + text[section.end():]
    return {'units': int(units[0]), 'top': top[0], 'shapes': shapes, 'bare_def': bare}


def _map_entries(text):
    result = {}
    for line in text.splitlines():
        words = shlex.split(line, comments=True)
        if not words or words[0] == 'NAME':
            continue
        if len(words) != 4 or not words[2].isdigit() or not words[3].isdigit():
            raise Refusal('FILL_STREAM_MAP_GRAMMAR_UNSUPPORTED', line)
        for purpose in words[1].split(','):
            result.setdefault((words[0], purpose), set()).add((int(words[2]), int(words[3])))
    return result


def derive_map(base_map, rules_file, filled_def, output):
    """Existing explicit FILL authority is valid without any 109 step ID."""
    parsed = fill_records(filled_def)
    entries = _map_entries(Path(base_map).read_text())
    rules = json.loads(Path(rules_file).read_text()) if rules_file is not None else None
    by_name = {}
    if rules is not None:
        if not isinstance(rules.get('layers'), dict):
            raise Refusal('FILL_STREAM_RULES_UNSUPPORTED', str(rules_file))
        for row in rules['layers'].values():
            name = row.get('name')
            if not name or name in by_name:
                raise Refusal('FILL_STREAM_RULES_AMBIGUOUS', str(name))
            by_name[name] = row
    mapping, added = {}, []
    for name in sorted({shape['layer'] for shape in parsed['shapes']}):
        existing = entries.get((name, 'FILL'), set())
        if len(existing) > 1:
            raise Refusal('FILL_STREAM_MAP_AMBIGUOUS', name)
        if rules is not None:
            row = by_name.get(name)
            if not row or row.get('opc') or not isinstance(row.get('non-opc'), dict):
                raise Refusal('FILL_STREAM_RULE_AUTHORITY_ABSENT', name)
            number, datatype, drawn = row.get('layer'), row['non-opc'].get('datatype'), row.get('datatype')
            if any(type(value) is not int or value < 0 or value > 65535 for value in (number, datatype, drawn)):
                raise Refusal('FILL_STREAM_MASK_OR_DATATYPE_AMBIGUOUS', name)
            routing = entries.get((name, 'NET'), set()) | entries.get((name, 'SPNET'), set())
            if routing != {(number, drawn)}:
                raise Refusal('FILL_STREAM_RULE_MAP_MISMATCH', name)
            pair = (number, datatype)
            if existing and existing != {pair}:
                raise Refusal('FILL_STREAM_RULE_MAP_MISMATCH', name + ' FILL')
        elif existing:
            pair = next(iter(existing))
        else:
            raise Refusal('FILL_STREAM_RULE_AUTHORITY_ABSENT', name)
        mapping[name] = list(pair)
        if not existing:
            added.append(f'{name} FILL {pair[0]} {pair[1]}')
    text = Path(base_map).read_text()
    if added:
        text += '\n# Derived FILL purposes; original routing/pin/via entries retained.\n' + '\n'.join(added) + '\n'
    Path(output).write_text(text)
    return {'layer_map': mapping, 'fill_count': len(parsed['shapes']),
            'added_purposes': added, 'base_map_sha256': _sha(base_map),
            'rules_sha256': _sha(rules_file) if rules_file is not None else None,
            'derived_map_sha256': _sha(output)}


def native_script():
    from librelane.common import get_script_dir
    return Path(get_script_dir()) / 'klayout/stream_out.py'


def operation_sources():
    paths = [Path(__file__), HERE / 'flow109_density_fill.py', HERE / 'flow109_density_fill.tcl']
    return {**{'programs/' + p.name: _sha(p) for p in paths},
            'native/librelane/scripts/klayout/stream_out.py': _sha(native_script())}


def _instances(cell):
    return sorted((i.cell.name, i.cplx_trans.to_s(), i.a.to_s(), i.b.to_s(), i.na, i.nb)
                  for i in cell.each_inst())


def geometry_proof(filled_def, bare_gds, filled_gds, mapping):
    import klayout.db as db
    parsed = fill_records(filled_def)
    layouts = []
    for path in (bare_gds, filled_gds):
        layout = db.Layout()
        layout.read(str(path))
        layouts.append(layout)
    bare, filled = layouts
    if bare.dbu != filled.dbu or {c.name for c in bare.each_cell()} != {c.name for c in filled.each_cell()}:
        raise Refusal('FILL_STREAM_HIERARCHY_CHANGED', 'dbu/cell population')
    top_name = parsed['top']
    if bare.cell(top_name) is None or filled.cell(top_name) is None:
        raise Refusal('FILL_STREAM_TOP_ABSENT', top_name)
    expected = {}
    for shape in parsed['shapes']:
        if shape['layer'] not in mapping:
            raise Refusal('FILL_STREAM_MAPPING_MISSING', shape['layer'])
        pair = tuple(mapping[shape['layer']])
        coords = [Fraction(n, parsed['units']) / Fraction(str(filled.dbu)) for n in shape['rect']]
        if any(n.denominator != 1 for n in coords):
            raise Refusal('FILL_STREAM_GRID_UNREPRESENTABLE', str(shape))
        expected.setdefault(pair, db.Region()).insert(db.Box(*map(int, coords)))
    pairs = {(i.layer, i.datatype) for layout in layouts for i in layout.layer_infos()} | set(expected)
    def shapes(layout, cell, pair):
        index = layout.find_layer(*pair)
        return cell.shapes(index) if index is not None else None
    def region(layout, cell, pair):
        value = shapes(layout, cell, pair)
        return db.Region(value) if value is not None else db.Region()
    checked_cells = 0
    for cell in bare.each_cell():
        other = filled.cell(cell.name)
        if _instances(cell) != _instances(other):
            raise Refusal('FILL_STREAM_HIERARCHY_CHANGED', cell.name)
        for pair in pairs:
            a, b = shapes(bare, cell, pair), shapes(filled, other, pair)
            if cell.name == top_name and pair in expected:
                ar, br = region(bare, cell, pair), region(filled, other, pair)
                if not (ar & expected[pair]).is_empty():
                    raise Refusal('FILL_STREAM_FILL_OVERLAPS_BASELINE', str(pair))
                if not (br ^ (ar | expected[pair])).is_empty():
                    raise Refusal('FILL_STREAM_FILL_GEOMETRY_MISMATCH', str(pair))
                # Region equality cannot attest text labels.
                at = sorted(s.to_s() for s in a.each() if s.is_text()) if a is not None else []
                bt = sorted(s.to_s() for s in b.each() if s.is_text()) if b is not None else []
                if at != bt:
                    raise Refusal('FILL_STREAM_NONFILL_GEOMETRY_CHANGED', cell.name + str(pair))
            else:
                av = sorted(s.to_s() for s in a.each()) if a is not None else []
                bv = sorted(s.to_s() for s in b.each()) if b is not None else []
                if av != bv:
                    raise Refusal('FILL_STREAM_NONFILL_GEOMETRY_CHANGED', cell.name + str(pair))
        checked_cells += 1
    return {'fill_count': len(parsed['shapes']), 'layers': mapping,
            'fill_area_dbu2': {f'{pair[0]}/{pair[1]}': region.area() for pair, region in expected.items()},
            'checked_cells': checked_cells, 'hierarchy_preserved': True,
            'nonfill_geometry_preserved': True, 'fill_geometry_matches_def': True,
            'scope': 'direct per-cell geometry and hierarchy; no full-chip flatten',
            'final_finishing_acceptance': 'NOT_MEASURED'}


def _stream_command(paths, def_file, output, derived_map):
    cmd = [sys.executable, str(native_script()), str(def_file), '--output', str(output),
           '--top', fill_records(def_file)['top'], '--lyt', str(paths['lyt']),
           '--lyp', str(paths['lyp']), '--lym', str(derived_map)]
    for path in paths['lefs']:
        cmd.extend(['--input-lef', str(path)])
    for path in paths['gds_files']:
        cmd.extend(['--with-gds-file', str(path)])
    if paths.get('seal_gds') is not None:
        cmd.extend(['--seal-gds-file', str(paths['seal_gds'])])
    return cmd


def stream_component(project, filled_def_ref, rules_ref, native_inputs, attempt, *, before_launch):
    """Native shared producer; its caller owns admission and final seal adoption.

    Inputs are ordinary typed source references, not arbitrary caller stamps.
    A legacy input with a legal PDK FILL map does not require a D62 ID.
    """
    project, attempt = Path(project).resolve(), Path(attempt).resolve()
    attempt.mkdir(parents=True, exist_ok=False)
    paths = {key: _bound(project, native_inputs[key]) for key in ('base_map', 'lyt', 'lyp')}
    for key in ('lefs', 'gds_files'):
        if not isinstance(native_inputs.get(key), list) or (key == 'lefs' and not native_inputs[key]):
            raise Refusal('FILL_STREAM_INPUT_UNBOUND', key)
        paths[key] = [_bound(project, ref) for ref in native_inputs[key]]
    if native_inputs.get('seal_gds') is not None:
        paths['seal_gds'] = _bound(project, native_inputs['seal_gds'])
    filled_def = _bound(project, filled_def_ref)
    rules = _bound(project, rules_ref) if rules_ref is not None else None
    sources = operation_sources()
    inputs = [filled_def_ref, *[ref for key, value in native_inputs.items()
                               for ref in (value if isinstance(value, list) else [value]) if ref is not None]]
    if rules_ref is not None:
        inputs.append(rules_ref)
    derived = attempt / 'fill.map'
    map_proof = derive_map(paths['base_map'], rules, filled_def, derived)
    bare = attempt / 'bare.def'
    bare.write_text(fill_records(filled_def)['bare_def'])
    inputs.extend([_record(project, derived), _record(project, bare)])
    def current():
        before_launch()
        if operation_sources() != sources:
            raise Refusal('FILL_STREAM_OPERATION_CHANGED', str(attempt))
        for ref in inputs:
            _bound(project, ref)
    commands = []
    for label, source in [('bare', bare), ('filled', filled_def)]:
        output, log = attempt / (label + '.gds'), attempt / (label + '.log')
        cmd = _stream_command(paths, source, output, derived)
        current()
        with log.open('w') as stream:
            proc = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT)
        commands.append({'argv': cmd, 'rc': proc.returncode, 'log': _record(project, log)})
        if proc.returncode or not output.is_file():
            raise Refusal('FILL_STREAM_NATIVE_FAILED', str(log))
    current()
    proof = geometry_proof(filled_def, attempt / 'bare.gds', attempt / 'filled.gds', map_proof['layer_map'])
    current()
    outputs = {name: _record(project, attempt / name) for name in ('bare.gds', 'filled.gds', 'fill.map', 'bare.def')}
    receipt = {'schema': SCHEMA, 'component_status': 'PASS', 'operation_sources': sources,
               'filled_def': filled_def_ref, 'fill_rules': rules_ref, 'native_inputs': native_inputs,
               'inputs': inputs, 'outputs': outputs, 'mapping': map_proof, 'commands': commands,
               'geometry_proof': proof, 'final_geometry_adoption': 'NOT_MEASURED',
               'flow109_acceptance': 'NOT_MEASURED'}
    write_json(attempt / 'bridge.json', receipt)
    return _record(project, attempt / 'bridge.json')


def validate_bridge(project, receipt_ref):
    project = Path(project).resolve()
    receipt = json.loads(_bound(project, receipt_ref).read_text())
    if receipt.get('schema') != SCHEMA or receipt.get('component_status') != 'PASS':
        raise Refusal('FILL_STREAM_RECEIPT_UNACCEPTED', str(receipt_ref))
    if receipt.get('operation_sources') != operation_sources():
        raise Refusal('FILL_STREAM_OPERATION_CHANGED', str(receipt_ref))
    for ref in receipt['inputs']:
        _bound(project, ref)
    for ref in receipt['outputs'].values():
        _bound(project, ref)
    for command in receipt['commands']:
        _bound(project, command['log'])
    filled_def = _bound(project, receipt['filled_def'])
    outputs = receipt['outputs']
    proof = geometry_proof(filled_def, _bound(project, outputs['bare.gds']),
                           _bound(project, outputs['filled.gds']), receipt['mapping']['layer_map'])
    if proof != receipt['geometry_proof']:
        raise Refusal('FILL_STREAM_PROOF_CHANGED', str(receipt_ref))
    result = {'gds': outputs['filled.gds'], 'fill_rules': receipt['fill_rules'],
              'geometry_proof': proof, 'receipt': receipt_ref,
              'final_geometry_adoption': receipt['final_geometry_adoption']}
    if receipt.get('d62_receipt') is not None:
        import flow109_density_fill as density
        d62 = density.validate_filled_odb(project, receipt['d62_receipt'])
        if d62['odb'] != receipt['odb']:
            raise Refusal('FILL_STREAM_ODB_CHAIN_CHANGED', str(receipt_ref))
        result.update(odb=d62['odb'], d62_receipt=receipt['d62_receipt'])
    return result


def stream_d62(project, d62_receipt_ref, native_inputs, attempt, *, before_launch):
    import flow109_density_fill as density
    d62 = density.validate_filled_odb(project, d62_receipt_ref)
    source = json.loads(_bound(Path(project), d62_receipt_ref).read_text())
    ref = stream_component(project, source['outputs']['filled.def'], source['inputs']['rules'],
                           native_inputs, attempt, before_launch=before_launch)
    path = _bound(Path(project), ref)
    receipt = json.loads(path.read_text())
    receipt.update(d62_receipt=d62_receipt_ref, odb=d62['odb'])
    write_json(path, receipt)
    return _record(Path(project), path)
