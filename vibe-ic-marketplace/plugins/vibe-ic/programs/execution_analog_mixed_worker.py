"""Substantive native-backed missing M2/M3/M4 and A8 characterization emitters.

Numerical limits, power states, stimulus decks, pins and model identity must
come from current declared INPUT. Raw native outputs remain separate from
source qualification. Failed/incomplete measurements never create readiness.
"""
from __future__ import annotations

import fnmatch
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_modes as em
from _prose_polarity import is_denied, sentence_scope
from execution_adapters_analog import PROGRAMS, write_json
from execution_analog_worker import checked_json, semantic_gates


def _path(project: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        old = Path(json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])['objective']['original_project'])
        if path.is_relative_to(old):
            path = project / path.relative_to(old)
        else:
            pdk = Path(json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])['objective']['pdk_root'])
            if not path.is_relative_to(pdk):
                raise em.Refusal('ANALOG_EXTERNAL_NATIVE_INPUT_UNBOUND', str(path))
    else:
        path = project / em._relative(value)
    if not path.is_file() or path.is_symlink():
        raise em.Refusal('ANALOG_NATIVE_INPUT_MISSING', str(path))
    return path


def _tcl(value: str) -> str:
    # Tcl double-quoted literal, including literal $/[] and whitespace.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace(']', '\\]') + '"'


def _run(argv: list[str], directory: Path, label: str, timeout_s: int, env=None) -> dict:
    if os.environ.get('VIBEIC_F5_NATIVE_LOCAL') != '1':
        raise em.Refusal('ANALOG_NATIVE_ADMISSION_REQUIRED', label)
    directory.mkdir(parents=True, exist_ok=True)
    out, err = directory / (label + '.stdout'), directory / (label + '.stderr')
    with out.open('w') as stdout, err.open('w') as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr,
                                   env={**os.environ, **(env or {})})
        try:
            rc = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise em.Refusal('ANALOG_NATIVE_FINITE_DEADLINE', label)
    receipt = {'argv': argv, 'pid': process.pid, 'rc': rc,
               'stdout_sha256': em.digest(out), 'stderr_sha256': em.digest(err)}
    write_json(directory / (label + '.receipt.json'), receipt)
    if rc != 0:
        raise em.Refusal('ANALOG_NATIVE_PROCESS_FAILED', label + ':' + str(rc))
    return receipt


def _cell_roles(liberties: list[Path]) -> dict:
    """Read explicit Liberty protection attributes; names are never heuristics."""
    roles = {}
    for path in liberties:
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
        for match in re.finditer(r'\bcell\s*\(\s*([^()]+)\s*\)\s*\{', text):
            depth, end, quoted = 1, match.end(), False
            while end < len(text) and depth:
                char = text[end]
                if char == '"' and (end == 0 or text[end - 1] != '\\'):
                    quoted = not quoted
                if not quoted:
                    depth += (char == '{') - (char == '}')
                end += 1
            if depth:
                raise em.Refusal('ANALOG_LIBERTY_MALFORMED', str(path))
            body = text[match.end():end - 1]
            roles[match.group(1).strip().strip('"')] = {
                'level_shifter': bool(re.search(r'\bis_level_shifter\s*:\s*true\s*;', body)),
                'isolation': bool(re.search(r'\bis_isolation_cell\s*:\s*true\s*;', body)),
                'liberty': str(path), 'liberty_sha256': em.digest(path)}
    return roles


def derive_power_reports(physical: dict, logical: dict, states: dict, roles: dict) -> tuple[dict, dict, dict]:
    """Trace actual inserted protection paths through native connectivity."""
    domains, cells = physical.get('domains'), physical.get('cells')
    module = (logical.get('modules') or {}).get(physical.get('top'))
    if not domains or not cells or not isinstance(module, dict) or not module.get('cells'):
        raise em.Refusal('ANALOG_NATIVE_POWER_MODEL_EMPTY', 'OpenDB/Yosys populations required')
    if set(states.get('domains', {})) != set(domains):
        raise em.Refusal('ANALOG_POWER_STATE_POPULATION_MISMATCH', repr(list(domains)))
    for name, domain in domains.items():
        state = states['domains'][name]
        if (type(domain.get('voltage')) not in (int, float) or not math.isfinite(domain['voltage'])
                or domain['voltage'] <= 0 or type(state.get('can_power_down')) is not bool):
            raise em.Refusal('ANALOG_POWER_INTENT_UNMEASURED', name)
        domain['off_capable'] = state['can_power_down']
        if domain.get('power_switches') and not domain['off_capable']:
            raise em.Refusal('ANALOG_POWER_STATE_CONTRADICTS_SWITCH', name)
    logical_cells = module['cells']
    for name, cell in logical_cells.items():
        if cells.get(name) != cell.get('type'):
            raise em.Refusal('ANALOG_POSTPNR_LOGICAL_PHYSICAL_DISAGREE', name)
    def domain_of(instance):
        matches = []
        for name, dom in domains.items():
            for element in dom.get('elements', []):
                if element in ('.', '/') or instance == element or instance.startswith(element.rstrip('/') + '/') or fnmatch.fnmatchcase(instance, element):
                    matches.append((len(element) if element not in ('.', '/') else 0, name))
        if not matches:
            raise em.Refusal('ANALOG_INSTANCE_POWER_DOMAIN_UNBOUND', instance)
        best = max(score for score, _ in matches)
        names = {name for score, name in matches if score == best}
        if len(names) != 1:
            raise em.Refusal('ANALOG_INSTANCE_POWER_DOMAIN_AMBIGUOUS', instance)
        return names.pop()
    ownership = {name: domain_of(name) for name in logical_cells}
    bit_names = {}
    for name, net in module.get('netnames', {}).items():
        for index, bit in enumerate(net['bits']):
            if type(bit) is int:
                bit_names.setdefault(bit, name if len(net['bits']) == 1 else f'{name}[{index}]')
    drivers, sinks = {}, {}
    for instance, cell in logical_cells.items():
        if not cell.get('port_directions') or not cell.get('connections'):
            raise em.Refusal('ANALOG_NATIVE_PIN_DIRECTION_MISSING', instance)
        for pin, bits in cell['connections'].items():
            direction = cell['port_directions'].get(pin)
            for bit in bits:
                if type(bit) is not int:
                    continue
                net_name = bit_names.get(bit)
                actual = physical.get('nets', {}).get(net_name)
                if actual and actual.get('signal_type') in ('POWER', 'GROUND'):
                    continue
                if not actual or not any(t['instance'] == instance and t['pin'] == pin for t in actual['terminals']):
                    raise em.Refusal('ANALOG_DEF_NET_CONNECTION_UNBOUND', instance + '/' + pin)
                if direction == 'output':
                    drivers.setdefault(bit, []).append((instance, pin))
                elif direction == 'input':
                    sinks.setdefault(bit, []).append((instance, pin))
                elif direction != 'inout':
                    raise em.Refusal('ANALOG_NATIVE_PIN_DIRECTION_INVALID', instance + '/' + pin)
    crossing, ls_entries, iso_entries = [], [], []
    protected_types = {name for name, role in roles.items() if role['level_shifter'] or role['isolation']}
    def endpoints(bit, visited=()):
        if bit in visited:
            raise em.Refusal('ANALOG_PROTECTION_PATH_CYCLE', str(bit))
        for instance, pin in sinks.get(bit, []):
            cell = logical_cells[instance]
            if cell['type'] not in protected_types:
                yield instance, pin, bit, []
                continue
            outputs = [b for p, bs in cell['connections'].items()
                       if cell['port_directions'].get(p) == 'output' for b in bs if type(b) is int]
            if not outputs:
                raise em.Refusal('ANALOG_PROTECTION_CELL_OUTPUT_MISSING', instance)
            for outbit in outputs:
                for receiver, receiver_pin, finalbit, chain in endpoints(outbit, (*visited, bit)):
                    yield receiver, receiver_pin, finalbit, [instance, *chain]
    for bit, sources in drivers.items():
        for driver, pin in sources:
            if logical_cells[driver]['type'] in protected_types:
                continue
            for receiver, receiver_pin, finalbit, chain in endpoints(bit):
                src, dst = ownership[driver], ownership[receiver]
                if src == dst:
                    continue
                net = bit_names[bit]
                needs_ls = domains[src]['voltage'] != domains[dst]['voltage']
                needs_iso = domains[src]['off_capable'] or domains[dst]['off_capable']
                ls = [name for name in chain if roles[logical_cells[name]['type']]['level_shifter']]
                iso = [name for name in chain if roles[logical_cells[name]['type']]['isolation']]
                row = {'net': net, 'to_net': bit_names[finalbit], 'driver': driver + '/' + pin,
                    'receiver': receiver + '/' + receiver_pin, 'driver_domain': src, 'receiver_domain': dst,
                    'vdd_from': domains[src]['voltage'], 'vdd_to': domains[dst]['voltage'],
                    'from_power_down': domains[src]['off_capable'], 'to_power_down': domains[dst]['off_capable'],
                    'level_shifter_required': needs_ls, 'isolation_required': needs_iso,
                    'inserted_path': chain, 'protected': (not needs_ls or bool(ls)) and (not needs_iso or bool(iso))}
                crossing.append(row)
                for instances, entries in ((ls, ls_entries), (iso, iso_entries)):
                    entries.extend({'net': net, 'instance': name, 'master': logical_cells[name]['type'],
                        'liberty_sha256': roles[logical_cells[name]['type']]['liberty_sha256']} for name in instances)
    if not drivers or not sinks:
        raise em.Refusal('ANALOG_NATIVE_CONNECTIVITY_EMPTY', physical['top'])
    common = {'native_engine': ['OpenROAD.read_upf/OpenDB', 'Yosys'], 'domains': domains,
              'native_population': {'instances': len(cells), 'nets': len(physical['nets']),
                                    'logical_instances': len(logical_cells)}}
    pd = {**common, 'crossings': crossing, 'all_crossings_protected': all(c['protected'] for c in crossing)}
    ls = {'level_shifters': ls_entries, 'all_required_inserted': all(
          not c['level_shifter_required'] or any(e['net'] == c['net'] for e in ls_entries) for c in crossing)}
    iso = {'isolation_cells': iso_entries, 'all_required_inserted': all(
           not c['isolation_required'] or any(e['net'] == c['net'] for e in iso_entries) for c in crossing)}
    return pd, ls, iso


def power_domains(project: Path, params: dict, records: Path) -> dict:
    required = ('top', 'upf', 'post_pnr_netlist', 'post_pnr_def', 'lefs', 'liberties', 'power_states')
    if any(not params.get(k) for k in required):
        raise em.Refusal('ANALOG_NATIVE_POWER_INPUTS_REQUIRED', ','.join(required))
    upf, netlist, deffile, statesfile = [_path(project, params[k]) for k in
                                      ('upf', 'post_pnr_netlist', 'post_pnr_def', 'power_states')]
    lefs = [_path(project, p) for p in params['lefs']]
    liberties = [_path(project, p) for p in params['liberties']]
    native_dir = project / 'reports/execution/analog/M2'
    native_dir.mkdir(parents=True, exist_ok=True)
    odbfile, physicalfile, logicalfile = [native_dir / name for name in ('power.odb', 'physical.json', 'logical.json')]
    script = native_dir / 'power.tcl'
    script.write_text('\n'.join([*(f'read_liberty {_tcl(str(p))}' for p in liberties),
        *(f'read_lef {_tcl(str(p))}' for p in lefs), f'read_def {_tcl(str(deffile))}',
        f'read_upf {_tcl(str(upf))}', f'write_db {_tcl(str(odbfile))}']) + '\n')
    receipts = [_run(['openroad', '-exit', str(script)], records, 'openroad-upf', params['timeout_s']),
        _run(['openroad', '-python', str(PROGRAMS / 'execution_analog_odb.py')], records,
             'openroad-db-export', params['timeout_s'], {'VIBEIC_F5_ODB_INPUT': str(odbfile),
                                                       'VIBEIC_F5_ODB_OUTPUT': str(physicalfile)})]
    yscript = native_dir / 'connectivity.ys'
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', params['top']):
        raise em.Refusal('ANALOG_TOP_IDENTIFIER_INVALID', params['top'])
    yscript.write_text('\n'.join([*(f'read_liberty -lib {json.dumps(str(p))}' for p in liberties),
        f'read_verilog {json.dumps(str(netlist))}', f'hierarchy -check -top {params["top"]}',
        f'write_json {json.dumps(str(logicalfile))}']) + '\n')
    receipts.append(_run(['yosys', '-s', str(yscript)], records, 'yosys-connectivity', params['timeout_s']))
    states = checked_json(statesfile)
    if states.get('upf_sha256') != em.digest(upf):
        raise em.Refusal('ANALOG_POWER_STATES_UPF_UNBOUND', str(statesfile))
    reports = derive_power_reports(checked_json(physicalfile), checked_json(logicalfile), states, _cell_roles(liberties))
    provenance = {'upf_sha256': em.digest(upf), 'def_sha256': em.digest(deffile),
                  'netlist_sha256': em.digest(netlist), 'physical_sha256': em.digest(physicalfile),
                  'logical_sha256': em.digest(logicalfile), 'power_states_sha256': em.digest(statesfile),
                  'native_receipts': receipts}
    for name, report in zip(('power_domain', 'level_shifter', 'isolation'), reports):
        report['_provenance'] = provenance
        write_json(project / f'reports/analog/mixed_signal/{name}.json', report)
    return {'entrypoint': 'execution_analog_mixed_worker.power_domains', 'native_receipts': receipts,
            'design_verdict': 'PASS' if reports[0]['all_crossings_protected'] else 'FAIL'}


def grade_si(plan: dict, samples: dict) -> dict:
    interfaces = plan.get('interfaces')
    if not isinstance(interfaces, list) or not interfaces:
        raise em.Refusal('ANALOG_SI_INTERFACE_POPULATION_EMPTY', 'declared interfaces required')
    rows, seen, measured = [], set(), set()
    for interface in interfaces:
        name, metrics = interface.get('name'), interface.get('metrics')
        if not name or name in seen or not isinstance(metrics, dict) or not metrics:
            raise em.Refusal('ANALOG_SI_INTERFACE_DECLARATION_INVALID', str(name))
        seen.add(name)
        result = {'name': name, 'pin': interface['pin'], 'metrics': {}}
        for metric, requirement in metrics.items():
            key = name + ':' + metric
            value = samples.get(key)
            if (not isinstance(value, dict) or type(value.get('value')) not in (int, float)
                    or not math.isfinite(value['value']) or value.get('unit') != requirement.get('unit')):
                raise em.Refusal('ANALOG_SI_MEASUREMENT_UNBOUND', key)
            if type(requirement.get('limit')) not in (int, float) or not math.isfinite(requirement['limit']):
                raise em.Refusal('ANALOG_SI_LIMIT_UNSTATED', key)
            relation = requirement.get('relation')
            if relation not in ('min', 'max'):
                raise em.Refusal('ANALOG_SI_RELATION_UNSTATED', key)
            passed = value['value'] >= requirement['limit'] if relation == 'min' else value['value'] <= requirement['limit']
            measured.add(key)
            result['metrics'][metric] = {'measured': value['value'], 'unit': value['unit'],
                'limit': requirement['limit'], 'relation': relation, 'verdict': 'PASS' if passed else 'FAIL'}
        rows.append(result)
    if measured != set(samples):
        raise em.Refusal('ANALOG_SI_MEASUREMENT_POPULATION_CHANGED', repr(set(samples) - measured))
    return {'interfaces': rows, 'all_interfaces_clean': all(m['verdict'] == 'PASS'
            for r in rows for m in r['metrics'].values()), 'native_engine': 'OpenSTA'}


def interface_si(project: Path, params: dict, records: Path) -> dict:
    planfile = _path(project, params['si_plan'])
    plan = checked_json(planfile)
    native = project / 'reports/execution/analog/M3'
    native.mkdir(parents=True, exist_ok=True)
    samplefile, script = native / 'si.tsv', native / 'si.tcl'
    libs = [_path(project, p) for p in params['liberties']]
    netlist, sdc, spef = [_path(project, params[k]) for k in ('post_pnr_netlist', 'sdc', 'spef')]
    commands = [*(f'read_liberty {_tcl(str(p))}' for p in libs),
        f'read_verilog {_tcl(str(netlist))}', f'link_design {_tcl(params["top"])}',
        f'read_sdc {_tcl(str(sdc))}', f'read_spef {_tcl(str(spef))}',
        'set_units -time s', f'set f [open {_tcl(str(samplefile))} w]']
    supported = {'slew_max_rise', 'slew_max_fall', 'slack_max', 'slack_min',
                 'slack_max_rise', 'slack_max_fall', 'slack_min_rise', 'slack_min_fall'}
    for interface in plan.get('interfaces', []):
        pin = interface['pin']
        commands += [f'set p [get_pins {_tcl(pin)}]',
                     'if {[llength $p] != 1} {error "SI pin identity not unique"}']
        for metric, requirement in interface['metrics'].items():
            if metric not in supported or requirement.get('unit') != 's':
                raise em.Refusal('ANALOG_NATIVE_SI_METRIC_NOT_QUALIFIED', metric)
            key = interface['name'] + ':' + metric
            if any(c in key for c in '\t\r\n'):
                raise em.Refusal('ANALOG_SI_IDENTIFIER_INVALID', key)
            commands += [f'set v [get_property [lindex $p 0] {metric}]',
                         f'puts $f [join [list {_tcl(key)} $v s] "\\t"]']
    commands += ['close $f']
    script.write_text('\n'.join(commands) + '\n')
    receipt = _run(['sta', '-exit', str(script)], records, 'opensta-interface', params['timeout_s'])
    samples = {}
    for line in samplefile.read_text().splitlines():
        key, value, unit = line.split('\t')
        if key in samples:
            raise em.Refusal('ANALOG_SI_DUPLICATE_MEASUREMENT', key)
        samples[key] = {'value': float(value), 'unit': unit}
    result = grade_si(plan, samples)
    result['_provenance'] = {'plan_sha256': em.digest(planfile), 'samples_sha256': em.digest(samplefile),
        'netlist_sha256': em.digest(netlist), 'sdc_sha256': em.digest(sdc), 'spef_sha256': em.digest(spef),
        'liberties': {str(p): em.digest(p) for p in libs}, 'native_receipt': receipt}
    write_json(project / 'reports/analog/mixed_signal/interface_si.json', result)
    return {'entrypoint': 'execution_analog_mixed_worker.interface_si', 'native_receipt': receipt,
            'design_verdict': 'PASS' if result['all_interfaces_clean'] else 'FAIL'}


def characterize(project: Path, block: str, params: dict, records: Path) -> dict:
    """Generate Liberty from exact declared decks and measured timing arcs."""
    planfile = project / f'phase3/analog/{block}/characterization_plan.json'
    plan = checked_json(planfile)
    if not plan.get('pins') or not plan.get('arcs') or plan.get('block') != block:
        raise em.Refusal('ANALOG_CHARACTERIZATION_PLAN_REQUIRED', block)
    measurements, receipts, arcs = {}, [], []
    for index, arc in enumerate(plan['arcs']):
        deck = _path(project, arc['deck'])
        receipt = _run(['ngspice', '-b', str(deck)], records, f'characterize-{block}-{index}', params['timeout_s'])
        text = (records / f'characterize-{block}-{index}.stdout').read_text()
        if re.search(r'(?:\.meas|measure).*failed|failed.*(?:\.meas|measure)|timestep too small', text, re.I):
            raise em.Refusal('ANALOG_CHARACTERIZATION_MEASURE_FAILED', str(deck))
        values = {}
        for match in re.finditer(r'^\s*(\w+)\s*=\s*([-+0-9.eE]+)', text, re.M):
            lo, hi = sentence_scope(text, match.start(1), match.end(2), extra_breaks=('\n',))
            if is_denied(text[lo:hi]):
                raise em.Refusal('ANALOG_CHARACTERIZATION_MEASURE_FAILED', str(deck))
            values[match.group(1)] = float(match.group(2))
        row = {}
        for quantity in ('cell_rise', 'cell_fall', 'rise_transition', 'fall_transition'):
            name = arc['measurements'].get(quantity)
            if not name or name not in values or not math.isfinite(values[name]) or values[name] < 0:
                raise em.Refusal('ANALOG_CHARACTERIZATION_MEASUREMENT_MISSING', quantity)
            row[quantity] = values[name]
        if arc.get('unit') != 's':
            raise em.Refusal('ANALOG_CHARACTERIZATION_UNIT_REQUIRED', block)
        if arc['from'] not in plan['pins'] or arc['to'] not in plan['pins']:
            raise em.Refusal('ANALOG_CHARACTERIZATION_PIN_UNBOUND', block)
        arcs.append((arc, row))
        measurements[str(index)] = {'deck_sha256': em.digest(deck), 'values': row}
        receipts.append(receipt)
    # This deliberately represents only the declared scalar characterization
    # point. It never invents a slew/load grid or unmeasured voltage/temperature.
    hdir = project / f'phase3/analog/hardmacro/{block}'
    original = (hdir / f'{block}.lib').read_text()
    # Preserve the established supply/ground PG declarations and all macro
    # header fields. Characterization may replace only declared signal pins;
    # a measured timing arc must never erase the A8 supply HARVEST contract.
    original_pins = re.findall(r'(?m)^\s*pin\s*\(\s*([^()]+)\s*\)\s*\{', original)
    if set(original_pins) != set(plan['pins']):
        raise em.Refusal('ANALOG_CHARACTERIZATION_PIN_POPULATION_CHANGED', block)
    replacement = {}
    for pin, definition in plan['pins'].items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', pin) or definition.get('direction') not in ('input', 'output', 'inout'):
            raise em.Refusal('ANALOG_CHARACTERIZATION_INTERFACE_INVALID', pin)
        lines = [f'pin ({pin}) {{', f'direction : {definition["direction"]};', 'is_analog : true;']
        for arc, row in arcs:
            if arc['to'] != pin:
                continue
            if arc.get('timing_sense') not in ('positive_unate', 'negative_unate', 'non_unate'):
                raise em.Refusal('ANALOG_CHARACTERIZATION_SENSE_REQUIRED', pin)
            lines += ['timing () {', f'related_pin : "{arc["from"]}";',
                      f'timing_sense : {arc["timing_sense"]};']
            for name, value in row.items():
                lines.append(f'{name} (scalar) {{ values ("{value:.17g}"); }}')
            lines.append('}')
        lines.append('}')
        replacement[pin] = '\n'.join(lines)
    spans = []
    for match in re.finditer(r'(?m)^\s*pin\s*\(\s*([^()]+)\s*\)\s*\{', original):
        depth, end = 1, match.end()
        while end < len(original) and depth:
            depth += (original[end] == '{') - (original[end] == '}')
            end += 1
        if depth:
            raise em.Refusal('ANALOG_LIBERTY_MALFORMED', block)
        spans.append((match.start(), end, replacement[match.group(1)]))
    for begin, end, text in reversed(spans):
        original = original[:begin] + text + original[end:]
    original = re.sub(r'time_unit\s*:\s*"[^"]+";', 'time_unit : "1s";', original)
    original = original.replace('interface_timing : false;', 'interface_timing : true;')
    (hdir / f'{block}.lib').write_text(original)
    result = {'native_engine': 'ngspice', 'plan_sha256': em.digest(planfile),
              'measurements': measurements, 'native_receipts': receipts,
              'scope': 'declared scalar timing arcs only; not full PVT/library acceptance'}
    write_json(hdir / 'characterization.json', result)
    return {'entrypoint': 'execution_analog_mixed_worker.characterize', 'native_receipts': receipts}


def strict_upstreams(project: Path) -> dict:
    from mixed_signal_signoff_check import _UPSTREAM
    from execution_analog_worker import _gate_status
    result = {}
    for spec in _UPSTREAM:
        candidates = [project / p for p in spec['paths']]
        path = next((p for p in candidates if p.is_file()), candidates[0])
        data = checked_json(path)
        if 'stub' in json.dumps(data).lower() or data.get('verdict') in ('WAIVED', 'PASS_WITH_WAIVERS'):
            raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', str(path))
        if spec['verdict_based']:
            passed = data.get('verdict') == 'PASS'
        else:
            passed = data.get(spec['field']) is True
        if not passed:
            raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', str(path))
        result[spec['key']] = {'path': str(path.relative_to(project)), 'sha256': em.digest(path)}
    # Reconsume every existing M1/M2/M3 blocking semantic checker; booleans
    # alone (even all true) do not prove signoff readiness.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='strict-signoff-') as temp:
        for step in ('M1', 'M2', 'M3'):
            results = semantic_gates(step, project, Path(temp) / step)
            if not results['blocking'] or any(v != 'PASS' for v in results['blocking'].values()):
                raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', step + ':' + str(results['blocking']))
    return result


def signoff(project: Path, params: dict, records: Path) -> dict:
    import analog_a6_native_pv as pv
    gds = project / 'phase3/mixed_signal/top_merged.gds'
    top, resolution = params['top'], params['pv_resolution']
    output = project / 'reports/analog/mixed_signal/top_pv'
    output.mkdir(parents=True, exist_ok=True)
    # Native shape census uses the actual merged layout, never an empty shell.
    script = output / 'shape_census.py'
    script.write_text('import json,os,pya\n'
        'l=pya.Layout();l.read(os.environ["F5_GDS"])\n'
        'tops=list(l.top_cells())\n'
        'assert len(tops)==1 and tops[0].name==os.environ["F5_TOP"]\n'
        'count=0\n'
        'for layer in l.layer_indexes():\n'
        ' i=tops[0].begin_shapes_rec(layer)\n'
        ' while not i.at_end():count+=1;i.next()\n'
        'assert count>0\n'
        'open(os.environ["F5_SHAPES"],"w").write(json.dumps({"top":tops[0].name,"shapes":count}))\n')
    receipt = _run(['klayout', '-b', '-r', str(script)], records, 'top-shapes', params['timeout_s'],
        {'F5_GDS': str(gds), 'F5_TOP': top, 'F5_SHAPES': str(output / 'shapes.json')})
    drc, drc_meta = pv._default_drc_runner(str(_path(project, resolution['drc_deck'])), str(gds), top,
                                           'host', output / 'drc.lyrdb')
    lvs, lvs_meta = pv._default_lvs_runner(str(gds), str(_path(project, params['top_schematic'])), top,
                                           'host', output, layermap=resolution.get('layermap'))
    native = {'gds_sha256': em.digest(gds), 'top': top, 'shape_census': checked_json(output / 'shapes.json'),
              'drc_violations': drc, 'drc': drc_meta, 'lvs': lvs, 'lvs_details': lvs_meta,
              'shape_receipt': receipt}
    write_json(output / 'native.json', native)
    upstreams, ready, reason = {}, False, ''
    try:
        upstreams = strict_upstreams(project)
        ready = (type(drc) is int and drc == 0 and lvs == 'MATCH'
                 and (lvs_meta.get('layout_devices') or 0) > 0)
        if not ready:
            reason = 'Merged-top PV is failing or has no proven extracted devices.'
    except em.Refusal as exc:
        reason = str(exc)
    result = {'ready_for_tapeout': ready, 'verdict': 'PASS' if ready else 'FAIL',
              'upstream_bindings': upstreams, 'top_pv': native, 'reason': reason,
              'source_qualification_is_signoff': False}
    write_json(project / 'reports/analog/mixed_signal/signoff.json', result)
    return {'entrypoint': 'execution_analog_mixed_worker.signoff', 'design_verdict': result['verdict']}


def substantive_mixed(project: Path, step: str) -> None:
    if step == 'M2':
        pd = checked_json(project / 'reports/analog/mixed_signal/power_domain.json')
        if not pd.get('native_population') or not pd.get('_provenance') or not pd.get('native_engine'):
            raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', 'M2 native model/connectivity missing')
    elif step == 'M3':
        data = checked_json(project / 'reports/analog/mixed_signal/interface_si.json')
        from mixed_signal_interface_si_check import _audit_substance
        passed, _, summary = _audit_substance('interface_si.json', data)
        if not passed or summary.get('stub') or not data.get('_provenance') or data.get('native_engine') != 'OpenSTA':
            raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', 'M3 lacks measured, bounded SI')
    elif step == 'M4':
        data = checked_json(project / 'reports/analog/mixed_signal/signoff.json')
        pv = data.get('top_pv') or {}
        if (data.get('ready_for_tapeout') is not True or data.get('verdict') != 'PASS'
                or type(pv.get('drc_violations')) is not int or pv['drc_violations'] != 0
                or pv.get('lvs') != 'MATCH' or not (pv.get('shape_census') or {}).get('shapes')
                or not (pv.get('lvs_details') or {}).get('layout_devices') or not data.get('upstream_bindings')):
            raise em.Refusal('ANALOG_MIXED_SUBSTANCE_FAIL', 'M4 lacks strict measured merged-top PV')
