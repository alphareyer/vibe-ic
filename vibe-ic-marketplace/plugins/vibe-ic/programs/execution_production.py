"""Production policy orchestration and the mapped-synthesis factory.

Domain factories are explicit source modules. Qualification is a measured run
receipt, separate from source coverage. All adoption stays in one live parent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import nullcontext
from functools import wraps
import glob
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
import re
import shlex
import subprocess
import sys
import time
import uuid

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_json
import execution_modes as em
import execution_policy as policy
import execution_step_protocol as protocol
from verdict import ReasonClass
from execution_resource_lease import host_lease, native_boundary

PROGRAMS = Path(__file__).resolve().parent
PROTECTED = {
    'W1A': '8ab5be061e27a6df3623e64301a7dfa3cfa4258c',
    'FRONT': '1c97185fdb4035817f4e63daa2ee5157de1b576b',
    'CUT7': '2c14403dcebe99230e012396a880701a758fb305',
    'CUT13': 'f3662b1f1797d7b699774feeffc0bf48306c8751',
    'CUT19': 'ddb64a49cc283aad9a8282a0a44d71addea65daf',
}
EXTRA_GATES = ('synth_handoff_netlist_check', 'native_synthesis_stat',
               'native_synthesis_checkers')
STEP_IDS = ('9',)


def _canonical_gate_clauses(step_id: str) -> dict:
    import _flow_yaml
    declared = next((item for item in _flow_yaml.load()['steps']
                     if str(item['id']) == step_id), None)
    if declared is None:
        raise em.Refusal('UNKNOWN_CANONICAL_STEP', step_id)
    rules = {}
    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key in ('all_of', 'any_of'):
                if isinstance(node.get(key), (dict, list)):
                    walk(node[key])
            for kind in ('program_exit_zero', 'optional_program_exit_zero',
                         'advisory_program_exit_zero'):
                if kind not in node:
                    continue
                spec = node[kind]
                spec = {'command': spec} if isinstance(spec, str) else spec
                if not isinstance(spec, dict) or not isinstance(spec.get('command'), str):
                    raise em.Refusal('CANONICAL_GATE_MALFORMED', step_id)
                tokens = shlex.split(spec['command'])
                if not tokens:
                    raise em.Refusal('CANONICAL_GATE_MALFORMED', step_id)
                rules.setdefault(Path(tokens[0]).stem, []).append((kind, spec))
    walk(declared['gate'])
    return rules


def canonical_gate_population(step_id: str, additional: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Full source-owned coverage, independently of current applicability.

    Factories declare this population at prepare, then produce lossless actual
    dispositions. No caller can remove a mandatory/advisory/optional clause.
    """
    row = next((item for item in em.load_portfolio()['steps'] if item['id'] == step_id), None)
    if row is None:
        raise em.Refusal('UNKNOWN_CANONICAL_STEP', step_id)
    return tuple(dict.fromkeys((*row['mandatory_gate_programs'],
                               *_canonical_gate_clauses(step_id), *additional)))


def canonical_gate_obligations(step_id: str, required: tuple[str, ...], gates: dict,
                               report: dict, *, project: Path | None = None,
                               _program_records: list | None = None) -> tuple[str, ...]:
    """Blocking view of a bound current canonical report; values stay intact.

    Conditional absence is checked against the real bound project, using only
    source-owned conditions. Actual advisory records retain rc/verdict/reason;
    a live refusal steps aside only on the canonical two-source agreement.
    Missing records, unknown enforcement and actual aggregate FAIL refuse.
    """
    import flow_compliance_check as compliance
    if not isinstance(report, dict) or not isinstance(gates, dict):
        raise em.Refusal('GATE_NOT_MEASURED', 'canonical disposition missing')
    rows = report.get('steps', ())
    matched_rows = [item for item in rows if isinstance(item, dict)
                    and str(item.get('id')) == step_id] if isinstance(rows, (list, tuple)) else []
    if len(matched_rows) != 1 or matched_rows[0].get('status') not in ('PASS', 'FAIL'):
        raise em.Refusal('GATE_NOT_MEASURED', 'current canonical step disposition missing')
    row = matched_rows[0]
    if row['status'] == 'FAIL':
        raise em.Refusal('GATE_FAIL', step_id)
    rules = _canonical_gate_clauses(step_id)
    if not set(rules).issubset(required):
        raise em.Refusal('REQUIRED_GATES_DROPPED', step_id)
    records = row.get('advisory_gate_records', [])
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise em.Refusal('GATE_NOT_MEASURED', 'invalid actual advisory records')
    blocking = []
    def executed(command, *, absent=False, advisory=None):
        if _program_records is None:
            return
        actual = [item for item in _program_records if item['cmd'] == command]
        if absent:
            if actual:
                raise em.Refusal('EVIDENCE_UNBOUND', 'absent clause reported execution: ' + command)
            return
        if len(actual) != 1:
            raise em.Refusal('GATE_NOT_MEASURED', 'canonical execution missing or duplicated: ' + command)
        observed = actual[0]
        if advisory is not None:
            if any(observed.get(key) != advisory.get(key)
                   for key in ('verdict', 'exit_code', 'structured_verdict', 'reason_class')):
                raise em.Refusal('EVIDENCE_UNBOUND', 'advisory execution record changed: ' + command)
        elif observed['verdict'] != 'PASS' or observed['exit_code'] != 0:
            raise em.Refusal('GATE_FAIL' if observed['verdict'] == 'FAIL'
                             else 'GATE_NOT_MEASURED', command)
    for name in required:
        clauses = rules.get(name, [])
        if not clauses or any(kind == 'program_exit_zero' for kind, _ in clauses):
            for kind, spec in clauses:
                if kind == 'program_exit_zero':
                    executed(spec['command'])
            blocking.append(name)
            continue
        observations = []
        for kind, spec in clauses:
            command = spec['command']
            patterns = spec.get('condition_files_exist')
            absent = False
            if patterns is not None:
                if (not isinstance(patterns, list) or not patterns
                        or any(not isinstance(pat, str) or not pat or Path(pat).is_absolute()
                               or '..' in Path(pat).parts for pat in patterns)
                        or project is None or not Path(project).is_dir()):
                    raise em.Refusal('GATE_NOT_MEASURED', 'conditional population unbound: ' + name)
                absent = not any(next(Path(project).glob(pat), None) is not None for pat in patterns)
            if absent:
                why = spec.get('absent_condition_reason')
                if not isinstance(why, str) or len(why.strip()) < compliance._MIN_ABSENT_CONDITION_REASON:
                    raise em.Refusal('GATE_NOT_MEASURED', 'conditional declaration missing: ' + name)
            if kind == 'optional_program_exit_zero':
                if not absent:
                    executed(command)
                    blocking.append(name)
                    break
                disclosures = row.get('declared_not_applicable', [])
                if (not isinstance(disclosures, list) or not any(isinstance(item, str)
                        and item.startswith(command + ' — condition_files_exist ')
                        and item.endswith('Declared not-applicable: ' + why.strip()) for item in disclosures)
                        or gates.get(name) not in ('NOT_MEASURED', 'NOT_APPLICABLE')):
                    raise em.Refusal('GATE_NOT_MEASURED', 'conditional disposition missing: ' + name)
                observations.append(dict(verdict='NOT_APPLICABLE', exit_code=None))
                executed(command, absent=True)
                continue
            matching = [record for record in records if record.get('gate') == name
                        and record.get('command') == command]
            if len(matching) != 1:
                raise em.Refusal('GATE_NOT_MEASURED', 'advisory disposition missing or duplicated: ' + name)
            record = matching[0]
            executed(command, absent=absent, advisory=record)
            enforcement, rc = record.get('enforcement'), record.get('exit_code')
            verdict = record.get('verdict')
            if not isinstance(verdict, str) or (rc is not None and type(rc) is not int):
                raise em.Refusal('GATE_NOT_MEASURED', 'advisory execution untyped: ' + name)
            norm = verdict.strip().upper().replace('_', '-')
            reason = compliance._reason_taxonomy.normalise(record.get('reason_class'))
            if absent:
                expected_reason = spec.get('absent_condition_reason_class', 'DESIGN_DECLARED_NA')
                if (enforcement != 'NOT_RUN_DECLARED' or verdict != 'NOT_APPLICABLE' or rc is not None
                        or reason != compliance._reason_taxonomy.normalise(expected_reason)
                        or reason not in compliance._reason_taxonomy.SKIP_ELIGIBLE):
                    raise em.Refusal('GATE_NOT_MEASURED', 'unbound conditional disposition: ' + name)
            elif enforcement == 'BLOCKING':
                if rc is None or not compliance._gate_is_two_source_advisory(name):
                    blocking.append(name)
                    break
            elif enforcement == 'DISCLOSED_INCOMPLETE':
                if (rc is None or (norm not in compliance._ADVISORY_INCOMPLETE_VERDICTS
                        and norm != 'BLOCKED' and reason not in compliance._reason_taxonomy.INCOMPLETE)
                        or (norm in compliance._ADVISORY_REFUSAL_VERDICTS
                            and reason != compliance._reason_taxonomy.ZERO_DENOMINATOR)):
                    raise em.Refusal('GATE_NOT_MEASURED', 'advisory incomplete disposition unbound: ' + name)
            elif enforcement == 'DISCLOSED_SKIP':
                if rc is None or norm not in compliance._ADVISORY_SKIP_VERDICTS or reason not in compliance._reason_taxonomy.SKIP_ELIGIBLE:
                    raise em.Refusal('GATE_NOT_MEASURED', 'advisory skip disposition unbound: ' + name)
            elif enforcement == 'PASSED':
                if rc != 0 or norm not in compliance._ADVISORY_PASS_VERDICTS:
                    raise em.Refusal('GATE_NOT_MEASURED', 'advisory pass disposition unbound: ' + name)
            elif enforcement == 'NON_BLOCKING_ADVISORY':
                if rc is None or not (norm in compliance._ADVISORY_NONBLOCKING_VERDICTS
                        or rc == 0 and norm not in compliance._ADVISORY_REFUSAL_VERDICTS):
                    raise em.Refusal('GATE_NOT_MEASURED', 'advisory disposition unbound: ' + name)
            else:
                raise em.Refusal('GATE_NOT_MEASURED', 'unknown advisory enforcement: ' + name)
            observations.append(record)
        else:
            # F2's measured_program_gates truthfully retains NOT_MEASURED for
            # an advisory FAIL under aggregate PASS. Its exact ledger-derived
            # projection is already checked by the semantic-report adapter.
            if _program_records is not None:
                continue
            expected = ('FAIL' if any(item.get('verdict') == 'FAIL' or item.get('exit_code') == 1
                                     for item in observations)
                        else 'PASS' if all(item.get('verdict') == 'PASS' and item.get('exit_code') == 0
                                          for item in observations) else 'NOT_MEASURED')
            if gates.get(name) != expected and not (expected == 'NOT_MEASURED'
                    and all(item.get('verdict') == 'NOT_APPLICABLE' for item in observations)
                    and gates.get(name) == 'NOT_APPLICABLE'):
                raise em.Refusal('EVIDENCE_UNBOUND', 'actual gate verdict changed: ' + name)
    return tuple(dict.fromkeys(blocking))


def _semantic_gate_obligations(step_id: str, required: tuple[str, ...], gates: dict,
                               semantic: dict, project: Path) -> tuple[str, ...]:
    """Current F2 canonical_semantics, with its actual row and execution ledger.

    Enforce the entire current canonical clause tree even when the factory's
    flat mandatory portfolio names only a subset. Derived observations are a
    local policy view; the issued binding and raw Evidence.gates stay intact.
    """
    import _flow_yaml
    current = next(row for row in _flow_yaml.load()['steps'] if str(row['id']) == step_id)
    if (not isinstance(semantic, dict) or semantic.get('canonical_row') != current
            or str(semantic.get('id')) != step_id):
        raise em.Refusal('EVIDENCE_UNBOUND', 'current canonical_row missing or changed')
    if semantic.get('status') == 'FAIL':
        raise em.Refusal('GATE_FAIL', step_id)
    if semantic.get('status') != 'PASS':
        raise em.Refusal('GATE_NOT_MEASURED', 'canonical semantic consumer incomplete')
    records = semantic.get('program_execution_records')
    if not isinstance(records, list):
        raise em.Refusal('GATE_NOT_MEASURED', 'actual program execution records missing')
    by_name = {}
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get('cmd'), str)
                or not isinstance(record.get('verdict'), str)
                or record.get('exit_code') is not None and type(record['exit_code']) is not int
                or record.get('rc') is not None and type(record['rc']) is not int
                or 'exit_code' not in record or record.get('rc') != record['exit_code']):
            raise em.Refusal('EVIDENCE_UNBOUND', 'untyped actual program execution')
        parts = shlex.split(record['cmd'])
        if not parts or record.get('gate') != Path(parts[0]).stem:
            raise em.Refusal('EVIDENCE_UNBOUND', 'actual program execution identity')
        by_name.setdefault(record['gate'], []).append(record)
    rules = _canonical_gate_clauses(step_id)
    measured = {name: 'PASS' if by_name.get(name) and all(
        row['verdict'] == 'PASS' and row['exit_code'] == 0 for row in by_name[name])
        else 'NOT_MEASURED' for name in rules}
    # This is the unchanged current F2 measured_program_gates projection.
    if any(gates.get(name) != measured[name] for name in required if name in rules):
        raise em.Refusal('EVIDENCE_UNBOUND', 'actual measured program projection changed')
    population = tuple(dict.fromkeys((*required, *rules)))
    blocking = canonical_gate_obligations(step_id, population, {**gates, **measured},
        {'steps': [semantic]}, project=project, _program_records=records)
    # Canonical programs absent from the flat binding were enforced directly
    # against their actual executions. No new gate key or PASS is issued.
    return tuple(name for name in blocking if name in required)


def _evidence_output_root(receipt: dict) -> Path:
    """Check all current issued artifacts before interpreting domain detail."""
    outputs = Path(receipt['output_root']).resolve()
    hashes = (receipt.get('evidence') or {}).get('outputs', {})
    if not isinstance(hashes, dict) or not hashes:
        raise em.Refusal('OUTPUT_INCOMPLETE', str(outputs))
    for name, expected in hashes.items():
        if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise em.Refusal('OUTPUT_OUTSIDE_ARM', str(name))
        path = outputs / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(outputs)
                or em.digest(path) != expected):
            raise em.Refusal('OUTPUT_DIGEST_MISMATCH', name)
    return outputs


class _GateObligationContext:
    def __init__(self, context, required):
        self.original, self.required_gates = context, required
    def binding(self):
        return self.original.binding()


class ProductionController(em.Controller):
    """Canonical production gate semantics with frozen controller integrity."""
    def __init__(self, registry, budget, *, host_scope=None, lease=None):
        super().__init__(registry,budget)
        self.host_scope, self.lease = host_scope, lease

    def _launch_component(self, argv, *, callback, **kwargs):
        if self.host_scope is None:
            return super()._launch_component(argv,callback=callback,**kwargs)
        import execution_resource_lease as api
        if 'pass_fds' not in inspect.signature(api.launch_host_worker).parameters:
            raise em.Refusal('RESOURCE_CALLBACK_LAUNCH_API_UNAVAILABLE','original114 FD preservation required')
        kwargs.pop('start_new_session')
        return api.launch_host_worker(self.lease,self.host_scope,list(argv),
            expected_lease_sha256=em.digest(self.lease/'lease.json'),
            readonly_mounts=[[str(kwargs['cwd'].parent/'inputs'),str(kwargs['cwd'].parent/'inputs')]],
            **kwargs)

    @staticmethod
    def _eligible(receipt, context, arm):
        evidence = receipt.get('evidence') or {}
        try:
            detail = json.loads(evidence.get('detail', ''))
        except (ValueError, TypeError):
            detail = {}
        relative = detail.get('canonical_gate_report') if isinstance(detail, dict) else None
        if isinstance(detail, dict) and 'canonical_semantics' in detail:
            binding = context.binding()
            consumer = inspect.getsourcefile(arm.validate)
            flow = str((PROGRAMS.parent/'flow/phase1_phase2_phase3.yaml').resolve())
            if (relative is not None or evidence.get('binding') != binding
                    or receipt.get('binding') != binding or receipt.get('adapter') != arm.identity()
                    or not consumer or str(Path(consumer).resolve()) not in arm.source_files
                    or arm.source_files.get(flow) != em.digest(Path(flow))):
                raise em.Refusal('EVIDENCE_UNBOUND', 'canonical semantic consumer/source binding')
            em.Controller._source_current(arm)
            outputs = _evidence_output_root(receipt)
            project = outputs/'project'
            if project.is_symlink() or not project.is_dir():
                raise em.Refusal('EVIDENCE_UNBOUND', 'canonical candidate project missing')
            required = _semantic_gate_obligations(context.step_id, context.required_gates,
                evidence.get('gates', {}), detail['canonical_semantics'], project)
            return em.Controller._eligible(receipt, _GateObligationContext(context, required), arm)
        if relative is None:
            return em.Controller._eligible(receipt, context, arm)
        if (not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts):
            raise em.Refusal('EVIDENCE_UNBOUND', 'canonical gate report path')
        path = Path(receipt['output_root']) / relative
        expected = evidence.get('outputs', {}).get(relative)
        if not expected or path.is_symlink() or not path.is_file() or em.digest(path) != expected:
            raise em.Refusal('EVIDENCE_UNBOUND', 'canonical gate report bytes')
        required = canonical_gate_obligations(context.step_id, context.required_gates,
                                              evidence.get('gates', {}), json.loads(path.read_text()),
                                              project=getattr(context, 'project', None))
        # This view keeps the full original binding; it neither changes the
        # immutable row/controller nor writes invented PASS values.
        return em.Controller._eligible(receipt, _GateObligationContext(context, required), arm)


def backend_working_derivation(inputs: Path, project: Path, binding) -> em.WorkingDerivation:
    """Reconsume frozen INPUT through the backend's actual source-owned transform.

    This grants working lineage only; current outputs and native execution
    retain their existing canonical validation and resource admission.
    """
    import execution_backend_worker as worker
    spec = json.loads((inputs/'request.json').read_text())
    if spec.get('step_id') != binding['step_id'] or spec.get('source_sha') != binding['source_sha']:
        raise em.Refusal('WORKING_DERIVATION_UNBOUND', binding['step_id'])
    mappings = [(spec['project'],str(project)), (spec['pdk_root'],str(inputs/'pdk'))]
    for name, old in spec['extra_inputs'].items():
        em._relative('external/'+name)
        mappings.append((str(old),str(inputs/'external'/name)))
    row = worker.ROWS[binding['step_id']]['canonical_row']
    required = [item.get('path','') for item in row.get('required_inputs',[])]
    outputs = tuple(alternative.strip() for pattern in row.get('required_outputs',[])
                    for alternative in pattern.split(' OR '))
    expected = {}
    for name, original in binding['inputs'].items():
        if not name.startswith('project/'):
            continue
        relative = name.removeprefix('project/')
        data = (inputs/em._relative(name)).read_bytes()
        if Path(relative).suffix == '.json':
            try:
                old = json.loads(data)
            except (ValueError,UnicodeDecodeError):
                pass  # The actual backend preserves non-JSON bytes.
            else:
                new = worker._translate(old,mappings)
                if new != old:
                    data = (json.dumps(new,sort_keys=True,indent=2)+'\n').encode()
        cleared = relative not in required and any(Path(relative).match(pattern) for pattern in outputs)
        expected[relative] = None if cleared else hashlib.sha256(data).hexdigest()
    return em.WorkingDerivation(expected,outputs)


def factories() -> protocol.FactoryRegistry:
    # One invocation's explicit/default selection must not inherit stale
    # registrations from a previous caller in the same interpreter.
    registry = protocol.FactoryRegistry()
    registry.register(sys.modules[__name__])
    transport = policy.request()
    for name in transport['factories']:
        if transport['factory_selection'] == 'default' and importlib.util.find_spec(name) is None:
            continue
        try:
            registry.register(name)
        except ImportError as exc:
            raise em.Refusal('PRODUCTION_FACTORY_MODULE_UNAVAILABLE',name) from exc
    return registry


def catalog(qualification_receipts: tuple[Path, ...] = ()) -> dict:
    coverage = factories().coverage()
    rows = []
    for row in em.load_portfolio()['steps']:
        registered = coverage.get(row['id'])
        measured = []
        for path in qualification_receipts:
            receipt = json.loads(Path(path).read_text())
            if (registered and receipt.get('step_id') == row['id']
                    and receipt.get('factory') == registered
                    and receipt.get('native_qualification') == 'MEASURED'
                    and receipt.get('resource_receipt') and receipt.get('semantic_gates')):
                measured.append(dict(path=str(path), sha256=em.digest(Path(path))))
        rows.append(dict(id=row['id'], programs=row['current_default']['source']['programs'],
                         status='IMPLEMENTED_UNQUALIFIED' if registered else 'NOT_IMPLEMENTED',
                         implementation_state='FACTORY_REGISTERED' if registered else 'OPEN_DOMAIN_FACTORY',
                         factory=registered, qualification_receipts=measured,
                         runtime='NOT_MEASURED', cited_qualification_state='UNVERIFIED_RECEIPTS' if measured else 'NONE',
                         native_producer_preserved=True,
                         mandatory_gates=row['mandatory_gate_programs']))
    return dict(schema=2, steps=rows, count=len(rows), registered_factories=len(coverage),
                authority='one-live-parent-interpreter',
                resource_scope='exclusive cooperating host dispatch; owned Docker run only',
                license_counts=policy.request()['licenses'], protected_prerequisites=PROTECTED,
                harvest='native dual obligations remain; no fork deletion authorized')


def source_identity(extra_sources: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    cp = subprocess.run(['git', '-C', str(PROGRAMS), 'rev-parse', 'HEAD'],
                        capture_output=True, text=True, timeout=10)
    if cp.returncode:
        raise em.Refusal('PRODUCTION_SOURCE_NOT_VERSIONED', cp.stderr[-300:])
    files = {}
    for root in (PROGRAMS, PROGRAMS.parent/'flow', PROGRAMS.parent/'agents', PROGRAMS.parent/'skills'):
        for p in root.rglob('*'):
            if (p.is_file() and not p.is_symlink() and
                    not {'tests', 'benchmark', 'harness', '__pycache__'}.intersection(p.relative_to(root).parts)
                    and p.suffix in ('.py', '.json', '.yaml', '.yml', '.tcl', '.sh', '.js', '.mjs',
                                     '.toml', '.rb', '.drc', '.md', '.lym', '.lydrc', '.lylvs', '.v', '.sv')):
                files[str(p.resolve())] = em.digest(p)
    # Match the registered analog source_dependencies declaration in full:
    # regular data bytes and source assets belong in the common issued map.
    suffixes = {'.py', '.json', '.yaml', '.yml', '.tcl', '.rb', '.v', '.sp', '.j2', '.jinja2', '.txt', '.toml'}
    for p in PROGRAMS.rglob('*'):
        if (p.is_file() and not p.is_symlink()
                and not {'tests', '__pycache__'}.intersection(p.relative_to(PROGRAMS).parts)
                and (p.suffix in suffixes or p.is_relative_to(PROGRAMS / 'data'))):
            files.setdefault(str(p.resolve()), em.digest(p))
    for p in (PROGRAMS / 'data/execution_modes_portfolio.json',
              PROGRAMS.parent / 'flow/phase1_phase2_phase3.yaml'):
        files[str(p.resolve())] = em.digest(p)
    binary = Path(sys.executable).resolve()
    files[str(binary)] = em.digest(binary)
    docker = shutil.which('docker')
    if docker:
        binary = Path(docker).resolve()
        files[str(binary)] = em.digest(binary)
    for name, expected in (extra_sources or {}).items():
        path = Path(name)
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or em.digest(path)!=expected:
            raise em.Refusal('PRODUCTION_NATIVE_SOURCE_MISMATCH', name)
        files[name]=expected
    return cp.stdout.strip(), files


def frontdoor_parameters(parameters: dict, transport: dict) -> tuple[dict, dict[str,str]]:
    """Normalize actual supplied values once, before building the common manifest.

    Native facts come from the capacity-placed executor, never CLI presence on
    this source-only host. Domain modules may not extend the issued manifest.
    """
    values=dict(parameters)
    if values.get('declaration') is not None:
        values['declaration']=Path(values['declaration'])
    if 'top_name' in values:
        if values.get('top') is not None and values['top']!=values['top_name']:
            raise em.Refusal('PRODUCTION_TOP_CONTRADICTION',str(values))
        values['top']=values['top_name']
    values.update(cpus=transport['cpus'],ram_mb=transport['ram_mb'])
    extra={}
    facts_path=transport.get('native_facts')
    if facts_path:
        path=Path(facts_path)
        if path.is_symlink() or not path.is_file():
            raise em.Refusal('PRODUCTION_NATIVE_FACTS_UNREADABLE',str(path))
        facts=json.loads(path.read_text())
        if not isinstance(facts,dict) or not facts.get('source_files'):
            raise em.Refusal('PRODUCTION_NATIVE_FACTS_INCOMPLETE',str(path))
        extra.update(facts['source_files']); extra[str(path)]=em.digest(path)
        values['native_facts']=facts
        values['native_facts_file']=path
        values['native_facts_sha256']=extra[str(path)]
        # Supplied per-step facts are explicit current input parameters. A
        # conflict with the actual front door is refused, never overwritten.
        per_step=facts.get('parameters',{}).get(values.get('step_id'),{})
        for key,value in per_step.items():
            current=values.get(key)
            supplied=Path(value) if isinstance(current,Path) else value
            if current is not None and current!=supplied:
                raise em.Refusal('PRODUCTION_PARAMETER_CONTRADICTION',key)
            values[key]=supplied
    if values.get('declaration') is not None:
        values['declaration']=Path(values['declaration'])
        declaration=values['declaration']
        if declaration.is_file() and not declaration.is_symlink():
            values['declaration_sha256']=em.digest(declaration)
    pdk=values.get('pdk')
    if pdk is not None and hasattr(pdk,'name'):
        if values.get('pdk_name') is not None and values['pdk_name']!=pdk.name:
            raise em.Refusal('PRODUCTION_PARAMETER_CONTRADICTION','pdk_name')
        values['pdk_name']=pdk.name
    switch=Path(values['project'])/'phase3/librelane_switch.json'
    if switch.is_file() and not switch.is_symlink():
        native=json.loads(switch.read_text())
        for key,native_key in (('pdk_root','pdk_root_host'),('image','image')):
            if native.get(native_key):
                if values.get(key) is not None and str(values[key])!=str(native[native_key]):
                    raise em.Refusal('PRODUCTION_PARAMETER_CONTRADICTION',key)
                values[key]=native[native_key]
    # The current factory/INPUT author supplies only this step's consumed
    # roots. Never infer a 19-step global population or subtract output globs.
    roots=values.get('input_roots')
    if roots is None and values.get('step_id')=='9' and values.get('pdk_root'):
        roots=_production_roots(Path(values['project']),Path(values['pdk_root']))
    if roots is not None:
        if (not isinstance(roots,dict) or not roots
                or any(not isinstance(name,str) or not name or Path(name).is_absolute()
                       or '..' in Path(name).parts for name in roots)
                or any(not isinstance(path,(str,Path)) or not Path(path).is_absolute()
                       for path in roots.values())):
            raise em.Refusal('PRODUCTION_INPUT_ROOTS_UNBOUND',str(values.get('step_id')))
        values['input_roots']={name:Path(path) for name,path in roots.items()}
        for name,key in (('declaration','declaration'),('native_facts','native_facts_file')):
            if values.get(key) is not None:
                values['input_roots'].setdefault(name,Path(values[key]))
    return values,extra


@dataclass(frozen=True)
class _SourceContext(em.Context):
    original: em.Context | None = None
    sources: dict[str,str] | None = None
    extra_sources: dict[str,str] | None = None

    def binding(self) -> dict:
        sha,files=source_identity(self.extra_sources)
        if sha!=self.source_sha or files!=self.sources:
            raise em.Refusal('PRODUCTION_SOURCE_POPULATION_CHANGED',self.step_id)
        return self.original.binding()

    def __getattr__(self,name):
        return getattr(self.original,name)


def _tree(inputs: dict[str, Path], population: dict, prefix: str, root: Path) -> None:
    """Bind lexical names, aliases, resolved bytes and the entire current tree."""
    boundary = root.resolve()

    def visit(path: Path, name: str, ancestors: tuple[Path, ...]) -> None:
        if not path.exists() and not path.is_symlink():
            population[name] = dict(path=str(path), kind='missing')
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path)) from exc
        if not resolved.is_relative_to(boundary):
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
        identity = dict(path=str(path), resolved=str(resolved),
                        alias=os.readlink(path) if path.is_symlink() else None)
        population[name] = identity
        if path.is_dir():
            if resolved in ancestors:
                raise em.Refusal('PRODUCTION_INPUT_LINK_CYCLE', str(path))
            identity['kind'] = 'directory'
            for child in sorted(path.iterdir()):
                visit(child, name + '/' + child.name, (*ancestors, resolved))
        elif path.is_file():
            identity.update(kind='file', sha256=em.digest(resolved))
            inputs[name] = resolved
        else:
            raise em.Refusal('PRODUCTION_INPUT_NOT_REGULAR', str(path))

    visit(root, prefix, ())


def _production_roots(project: Path, root: Path) -> dict[str,Path]:
    relative=('input','rtl','phase1/generated_docs','phase2/stage1/rtl',
              'phase2/stage2/constraints','pdk_local','waivers.json','SOURCE_MANIFEST.md',
              '.vibeic-state/impl-mode-v1.json','phase3/librelane_switch.json')
    return {**{'project/'+rel:project/rel for rel in relative},'pdk':root}


def _production_inputs(project: Path, root: Path) -> tuple[dict[str, Path], str]:
    inputs, population = {}, {}
    for rel in ('input', 'rtl', 'phase1/generated_docs', 'phase2/stage1/rtl',
                'phase2/stage2/constraints', 'pdk_local'):
        _tree(inputs, population, 'project/' + rel, project / rel)
    _tree(inputs, population, 'pdk', root)
    for rel in ('waivers.json', 'SOURCE_MANIFEST.md', '.vibeic-state/impl-mode-v1.json',
                'phase3/librelane_switch.json'):
        path = project / rel
        if path.is_symlink():
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
        _tree(inputs, population, 'project/' + rel, path)
    return inputs, json.dumps(population, sort_keys=True)


@dataclass(frozen=True)
class _ProductionContext(em.Context):
    project: Path = Path('.')
    pdk_root: Path = Path('.')
    population: str = ''
    source_files: dict[str,str] | None = None

    def binding(self) -> dict:
        # BLOCKING at every controller binding boundary, including adoption.
        # The frozen controller still owns authority and byte-input hashing.
        _, current = _production_inputs(self.project, self.pdk_root)
        if current != self.population:
            raise em.Refusal('PRODUCTION_INPUT_POPULATION_CHANGED', str(self.project))
        for name,expected in (self.source_files or {}).items():
            if not Path(name).is_file() or Path(name).is_symlink() or em.digest(Path(name))!=expected:
                raise em.Refusal('ADAPTER_SOURCE_MISMATCH',name)
        return super().binding()


def prepare(project: Path | protocol.StepRequest, top: str | None = None, pdk=None,
            lease: Path | None = None, record: Path | None = None,
            *, source_binding: tuple[str,dict[str,str]] | None = None,
            synthesis_engine: str = 'librelane-mapped-synthesis'):
    import execution_synthesis_engines as engines
    if isinstance(project, protocol.StepRequest):
        request = project
        parameters = request.parameters
        if (not parameters.get('top') or parameters.get('pdk') is None
                or not parameters.get('container')):
            raise em.Refusal('PRODUCTION_SYNTH_PARAMETERS_MISSING', request.step_id)
        if parameters.get('period_relax', 1.0) != 1.0:
            raise em.Refusal('PRODUCTION_PERIOD_RELAX_TARGET_PROOF_PENDING', str(parameters['period_relax']))
        engine = engines.require_engine(parameters.get('synthesis_engine', synthesis_engine))
        with native_boundary(request.lease):
            context, registry = prepare(request.project, parameters['top'], parameters['pdk'],
                                        request.lease, request.record,
                                        source_binding=(request.source_sha,dict(request.source_files)),
                                        synthesis_engine=engine.arm_id)
        if context.source_sha != request.source_sha:
            raise em.Refusal('PRODUCTION_FACTORY_SOURCE_CHANGED', request.step_id)
        return protocol.PreparedStep(context, registry, import_selected, adoption_paths=(
            'phase2/stage2/synth', 'provenance.jsonl', 'phase3/execution_modes_handoff.json'))
    import librelane_contract as lc
    import librelane_image_facts as facts
    engine = engines.require_engine(synthesis_engine)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', top):
        raise em.Refusal('PRODUCTION_TOP_PATH_UNSAFE', repr(top))
    switch = project / 'phase3/librelane_switch.json'
    switch_data = json.loads(switch.read_text()) if switch.is_file() else {}
    legacy_mode = lc.selected_mode(project, '9')
    layer = lc.impl_step_modes(project)
    if (switch_data.get('steps', {}).get('9') == 'direct' or (layer or {}).get('9') == 'direct'):
        raise em.Refusal('PRODUCTION_EXPLICIT_DIRECT_NOT_IMPLEMENTED', str(project))
    image = lc.resolve_image(project)
    measured = facts.image_facts(image)
    image_id = measured.get('image_id', '')
    if not image_id.startswith('sha256:') or not measured.get('librelane_version'):
        raise em.Refusal('PRODUCTION_IMAGE_UNQUALIFIED', image)
    native_steps = engine.native_steps
    available_steps = set(facts.flow_steps(measured, 'Chip'))
    if not set(native_steps).issubset(available_steps):
        raise em.Refusal('PRODUCTION_NATIVE_STEPS_UNAVAILABLE', image_id)
    # Existing PDK image materialization starts separate copy containers. It
    # has no production lease here, so require an already-declared host root.
    if not (switch_data.get('pdk_root_host') or os.environ.get('VIBEIC_LIBRELANE_PDK_ROOT')):
        raise em.Refusal('PRODUCTION_PDK_MATERIALIZATION_NOT_IMPLEMENTED', str(project))
    # Image facts admit an attempt. Only the actual synthesis child, native
    # checker outputs and primary consumers qualify a measured generation.
    # Do not launch an entrypoint-override --version surrogate as a producer.
    root = Path(lc.pdk_root_resolution(project, pdk.name, image=image_id)['path']).resolve()
    if not root.is_dir() or not Path(pdk.liberty).is_file():
        raise em.Refusal('PRODUCTION_HOST_PDK_NOT_MEASURED', str(root))
    if switch.is_file() and json.loads(switch.read_text()).get('development_librelane_source'):
        raise em.Refusal('PRODUCTION_FORK_MOUNT_UNQUALIFIED', str(switch))
    sha, sources = source_binding or source_identity()
    inputs, population = _production_inputs(project, root)
    inputs['lease.json'] = lease / 'lease.json'
    if not any(k.startswith('project/phase2/stage1/rtl/') for k in inputs):
        raise em.Refusal('PRODUCTION_RTL_INPUT_MISSING', str(project))
    objective = dict(metric='mapped_area_um2', direction='min', top=top)
    row = next(s for s in em.load_portfolio()['steps'] if s['id'] == '9')
    spec = dict(top=top, pdk=asdict(pdk), original_project=str(project),
                original_pdk_root=str(root), image_id=image_id, lease=str(lease),
                legacy_native_mode=legacy_mode, selected_native_mode='librelane',
                source_sha=sha, source_files=sources, qualification=measured,
                synthesis_engine=engine.contract(), engine_population=engines.population(),
                input_population=json.loads(population),
                input_hashes={k: em.digest(v) for k, v in inputs.items()})
    write_json(record, spec)
    inputs['request.json'] = record
    context = _ProductionContext('9', sha, inputs, objective,
                         tuple(dict.fromkeys(row['mandatory_gate_programs'] + list(EXTRA_GATES))),
                         native_mode='librelane', project=project, pdk_root=root, population=population,
                         source_files=sources)
    quota = json.loads((lease / 'lease.json').read_text())
    reservation = engines.lease_reservation(quota)
    netlist = f'project/phase2/stage2/synth/{top}_synth.v'
    stats = 'project/phase2/stage2/synth/stats.json'
    adapter = em.Adapter(
        engine.arm_id, engine.tool_id, '9', sha, sources, image_id,
        engine.engine_families, (em.Component('native-mapped-synthesis',
        (str(Path(sys.executable).resolve()), str(PROGRAMS / 'execution_native_worker.py'),
         '--inputs', '{inputs}', '--outputs', '{outputs}'), timeout_s=7200),),
        validate_synthesis, (netlist, stats, 'producer.json', 'native_commands.jsonl'), objective,
        qualification_evidence=json.dumps(measured, sort_keys=True),
        cpus=reservation['cpus'], ram_mb=reservation['ram_mb'],
        output_contract={name: (netlist,) if 'netlist' in name else (stats,)
                         for name in row['required_output_contract']})
    registry = em.Registry()
    registry.register(adapter)
    return context, registry


def classify(request: protocol.StepRequest) -> protocol.PreparedStep:
    """Classify Step 9 from source/input facts before host admission.

    Image probing and native producer setup belong to the second call to
    ``prepare`` under a live production lease.  This pass only rejects a
    malformed source request or an absent current RTL population.
    """
    parameters = request.parameters
    if (not parameters.get('top') or parameters.get('pdk') is None
            or not parameters.get('container')):
        raise em.Refusal('PRODUCTION_SYNTH_PARAMETERS_MISSING', request.step_id)
    if parameters.get('period_relax', 1.0) != 1.0:
        raise em.Refusal('PRODUCTION_PERIOD_RELAX_TARGET_PROOF_PENDING', str(parameters['period_relax']))
    import execution_synthesis_engines as engines
    engines.require_engine(parameters.get('synthesis_engine', 'librelane-mapped-synthesis'))
    switch = Path(request.project) / 'phase3/librelane_switch.json'
    if switch.is_file() and not switch.is_symlink():
        try:
            switch_data = json.loads(switch.read_text())
        except (OSError, ValueError) as exc:
            raise em.Refusal('PRODUCTION_INPUT_UNMEASURED', str(switch)) from exc
        if switch_data.get('steps', {}).get('9') == 'direct':
            raise em.Refusal('PRODUCTION_EXPLICIT_DIRECT_NOT_IMPLEMENTED', str(request.project))
    rtl = Path(request.project) / 'phase2/stage1/rtl'
    if not rtl.is_dir() or rtl.is_symlink() or not any(
            path.is_file() and not path.is_symlink() for path in rtl.rglob('*')):
        return protocol.PreparedStep(None, None, None, 'current_input_missing',
            'current Step 9 RTL input population is missing',
            {'facts': {'step_id': '9', 'source_sha': request.source_sha,
                       'source_files': dict(request.source_files),
                       'design_verdict': 'NOT_MEASURED'}})
    return protocol.PreparedStep(None, None, None, 'execute',
        'source-owned classification selected execution',
        {'facts': {'step_id': '9', 'source_sha': request.source_sha,
                   'source_files': dict(request.source_files),
                   'design_verdict': 'NOT_MEASURED'}})


def gate_verdict(name: str, rc: int, report: dict) -> str:
    """Consume native report semantics: rc0 alone cannot establish a gate."""
    if rc == 1:
        return 'FAIL'
    if rc != 0:
        return 'NOT_MEASURED'
    if name == 'provenance_check':
        if report.get('ok') is not True:
            return 'FAIL' if report.get('ok') is False else 'NOT_MEASURED'
        checks = report.get('checks') or []
        if any(check.get('status') == 'FAIL' for check in checks):
            return 'FAIL'
        return ('NOT_MEASURED' if report.get('unmeasured') or report.get('uncalibrated')
                or not checks or any(check.get('status') != 'PASS' for check in checks) else 'PASS')
    verdict = report.get('verdict')
    if verdict in ('PASS', 'FAIL'):
        return verdict
    if report.get('applicable') is False:
        return 'NOT_MEASURED'
    passed = report.get('summary', {}).get('pass')
    return 'PASS' if passed is True else 'FAIL' if passed is False else 'NOT_MEASURED'


def _gate(name: str, argv: list[str], directory: Path) -> str:
    report_path = directory / (name + '.json')
    command = [str(Path(sys.executable).resolve()), str(PROGRAMS / (name + '.py')),
               *argv, '--json', str(report_path)]
    try:
        cp = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        write_json(directory / (name + '.receipt.json'), dict(argv=command, rc=None,
            verdict='NOT_MEASURED', detail=str(exc), program_sha256=em.digest(PROGRAMS / (name + '.py'))))
        return 'NOT_MEASURED'
    try:
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    except ValueError:
        report = {}
    verdict = gate_verdict(name, cp.returncode, report)
    write_json(directory / (name + '.receipt.json'), dict(argv=command, rc=cp.returncode,
        verdict=verdict, program_sha256=em.digest(PROGRAMS / (name + '.py')),
        report_sha256=em.digest(report_path) if report_path.is_file() else None,
        stdout=cp.stdout, stderr=cp.stderr))
    return verdict


def validate_synthesis(outputs: Path, binding: dict) -> em.Evidence:
    """Fresh primary consumers on actual worker outputs; never a cached PASS."""
    outputs = Path(outputs)
    result_path = outputs / 'producer.json'
    gates = {name: 'NOT_MEASURED' for name in binding['required_gates']}
    if not result_path.is_file():
        return em.Evidence(binding, 'NOT_MEASURED', gates, {}, detail='producer receipt missing')
    producer = json.loads(result_path.read_text())
    if producer.get('binding') != binding:
        raise em.Refusal('PRODUCTION_WORKER_BINDING_CHANGED', str(result_path))
    if producer.get('input_hashes') != {k: v for k, v in binding['inputs'].items() if k != 'request.json'}:
        raise em.Refusal('PRODUCTION_WORKER_INPUT_MANIFEST_UNBOUND', str(result_path))
    hashes = {p.relative_to(outputs).as_posix(): em.digest(p) for p in outputs.rglob('*')
              if p.is_file() and (p == result_path or p == outputs / 'native_commands.jsonl' or
                  p.is_relative_to(outputs / 'project/phase3') or
                  p.is_relative_to(outputs / 'project/phase2/stage2/synth') or
                  p == outputs / 'project/provenance.jsonl')}
    for path in outputs.rglob('*'):
        if path.is_symlink():
            raise em.Refusal('PRODUCTION_OUTPUT_LINK', str(path))
    for name, expected in producer.get('input_hashes', {}).items():
        actual = outputs / name if name.startswith('project/') and name != 'project/phase3/librelane_switch.json' \
                 else outputs.parent / 'inputs' / name
        if not actual.is_file() or em.digest(actual) != expected:
            raise em.Refusal('PRODUCTION_WORKER_INPUT_CHANGED', name)
    status = producer.get('status')
    if status != 'PASS':
        # This is the actual existing native producer's failure/nonmeasurement.
        return em.Evidence(binding, 'FAIL' if status == 'FAIL' else 'NOT_MEASURED',
                           gates, hashes, detail=producer.get('detail', 'native producer not measured'))
    spec = json.loads((outputs.parent / 'inputs/request.json').read_text())
    import execution_synthesis_engines as engines
    engine = engines.consume_producer(spec, producer)
    project = outputs / 'project'
    original_switch = outputs.parent / 'inputs/project/phase3/librelane_switch.json'
    derived_switch = json.loads(original_switch.read_text()) if original_switch.is_file() else {}
    derived_switch.update(image=spec['image_id'], pdk_root_host=str(outputs.parent / 'inputs/pdk'))
    if json.loads((project / 'phase3/librelane_switch.json').read_text()) != derived_switch:
        raise em.Refusal('PRODUCTION_DERIVED_CONFIG_CHANGED', str(project))
    mapped = project / f"phase2/stage2/synth/{spec['top']}_synth.v"
    native_folder = Path(producer['native_folder'])
    if not native_folder.resolve().is_relative_to(project / 'phase3/librelane'):
        raise em.Refusal('PRODUCTION_NATIVE_FOLDER_UNBOUND', str(native_folder))
    state = json.loads((native_folder / 'state_out.json').read_text())
    native = Path(state['nl'])
    if not native.resolve().is_relative_to(native_folder) or not native.is_file():
        raise em.Refusal('PRODUCTION_NATIVE_NETLIST_UNBOUND', str(native))
    from _rtl_include_hub import silicon_rtl_selection
    rtl = silicon_rtl_selection(project / 'phase2/stage1/rtl')
    pdk = producer['pdk']
    directory = outputs.parent / 'validations' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    gates['synth_netlist_check'] = _gate('synth_netlist_check',
        ['--netlist', str(mapped), '--tool-netlist', str(native), '--rtl', *map(str, rtl)], directory)
    gates['area_total_vs_budget_check'] = _gate('area_total_vs_budget_check',
        [str(project), '--library', pdk['liberty']], directory)
    gates['pdk_consistency_check'] = _gate('pdk_consistency_check',
        ['--netlist', str(mapped), '--pdk-lib', pdk['liberty']], directory)
    gates['provenance_check'] = _gate('provenance_check',
        [str(project), '--output', str(mapped.relative_to(project)), '--tool', engine.provenance_tool, '--require-measured'], directory)
    gates['synth_handoff_netlist_check'] = _gate('synth_handoff_netlist_check',
        ['--netlist', str(native), '--resolved', str(native_folder / 'config.json')], directory)
    if 'FAIL' in gates.values():
        return em.Evidence(binding, 'FAIL', gates, hashes, detail='primary gate FAIL')
    import librelane_contract as lc
    try:
        lc.verify_synthesis_stat(native_folder / 'reports/stat.json', state,
                                 mapped.parent / 'stats.json', spec['top'], directory / 'stat.json')
        gates['native_synthesis_stat'] = 'PASS'  # only after the actual consumer returns
    except lc.Refusal as exc:
        gates['native_synthesis_stat'] = 'FAIL'
        write_json(directory / 'stat-refusal.json', dict(reason=exc.code, detail=str(exc)))
        return em.Evidence(binding, 'FAIL', gates, hashes, detail=str(exc))
    # BLOCKING: consume the exact current producer-owned INPUT and substantive
    # output population. A step name or an empty hash loop cannot prove PASS.
    checkers = producer.get('native_checkers', [])
    required = engine.native_steps[2:]
    folders = [native_folder.parent / f'{index:02d}-{step.lower().replace(".", "-")}'
               for index, step in enumerate(required, 3)]
    if len(checkers) != len(folders) or set(checkers) != set(map(str, folders)):
        raise em.Refusal('PRODUCTION_CHECKER_POPULATION_UNBOUND', repr(checkers))
    capability = producer.get('native_capability', {})
    if capability.get('image') != spec['image_id']:
        raise em.Refusal('PRODUCTION_CHECKER_IMAGE_UNBOUND', str(native_folder))
    previous = native_folder / 'state_out.json'
    for step, folder in zip(required, folders):
        receipt = json.loads((folder / 'vibeic_receipt.json').read_text())
        config = native_folder.parent / (step.lower().replace('.', '_') + '_config_resolved.json')
        current_input = protocol.step_input_fingerprint(spec['image_id'], config, previous, step)
        aliases = capability.get('openroad_aliases') or {}
        if aliases:
            current_input['openroad_aliases'] = aliases
        if (receipt.get('input') != current_input or
                json.loads((folder / 'input_fingerprint.json').read_text()) != current_input):
            raise em.Refusal('PRODUCTION_CHECKER_INPUT_UNBOUND', str(folder))
        current_hashes = protocol.step_output_hashes(folder, step)
        if receipt.get('sha256') != current_hashes:
            raise em.Refusal('PRODUCTION_CHECKER_OUTPUT_POPULATION_UNBOUND', str(folder))
        lc._check_state(json.loads((folder / 'state_out.json').read_text()), outputs=True)
        for name, expected in current_hashes.items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise em.Refusal('PRODUCTION_CHECKER_PATH_UNSAFE', name)
            if em.digest(folder / name) != expected:
                raise em.Refusal('PRODUCTION_CHECKER_RECEIPT_CHANGED', str(folder))
        previous = folder / 'state_out.json'
    if state.get('metrics', {}).get('design__instance_unmapped__count') == 0 and \
            state.get('metrics', {}).get('synthesis__check_error__count') == 0:
        gates['native_synthesis_checkers'] = 'PASS'
    verdict = 'FAIL' if 'FAIL' in gates.values() else 'PASS' if set(gates.values()) == {'PASS'} else 'NOT_MEASURED'
    stat = json.loads((mapped.parent / 'stats.json').read_text())
    area = stat.get('chip_area')
    metrics = {'mapped_area_um2': area} if type(area) in (int, float) else {}
    return em.Evidence(binding, verdict, gates, hashes, metrics)


def _choice(path: str | None, wait_s: int) -> dict | None:
    if not path:
        return None
    target = Path(path)
    deadline = time.monotonic() + wait_s
    while not target.is_file() and time.monotonic() < deadline:
        time.sleep(.1)
    if target.is_symlink():
        raise em.Refusal('PRODUCTION_CHOICE_LINK', str(target))
    return json.loads(target.read_text()) if target.is_file() else None


def import_selected(project: Path, context: em.Context, controller: em.Controller,
                    run: Path, adopted: dict) -> dict:
    """Copy the issued generation under a rollback journal, then primary gates."""
    import librelane_contract as lc
    import librelane_import as importer
    from phase3_one_shot_runner import _write_synth_inputs_sidecar
    generation = adopted['selected_generation']
    controller._generation_current(generation)
    try:
        current_binding = context.binding()
    except em.Refusal as exc:
        raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(project)) from exc
    if current_binding != adopted['evidence']['binding']:
        raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(project))
    selected = Path(generation['directory'])
    producer = json.loads((selected / 'producer.json').read_text())
    spec = json.loads((run / adopted['selected'] / 'inputs/request.json').read_text())
    import execution_synthesis_engines as engines
    engine = engines.consume_producer(spec, producer)
    if adopted['selected'] != engine.arm_id:
        raise em.Refusal('PRODUCTION_SYNTH_ENGINE_ADOPTION_CHANGED', adopted['selected'])
    original_output = run / adopted['selected'] / 'outputs'
    state_rel = Path(producer['native_folder']).relative_to(original_output) / 'state_out.json'
    netlist_rel = Path(f"phase2/stage2/synth/{spec['top']}_synth.v")
    copies = {netlist_rel: selected / 'project' / netlist_rel,
              Path('phase2/stage2/synth/netlist.v'): selected / 'project' / netlist_rel,
              Path('phase2/stage2/synth/stats.json'): selected / 'project/phase2/stage2/synth/stats.json'}
    enc = selected / 'project/phase2/stage2/synth/fsm_encoding.enc'
    if enc.is_file():
        copies[Path('phase2/stage2/synth/fsm_encoding.enc')] = enc
    elif (project / 'phase2/stage2/synth/fsm_encoding.enc').exists():
        raise em.Refusal('PRODUCTION_PRIOR_FSM_NOT_ADOPTED', str(project))
    journal = importer._Journal(project)
    try:
        for rel in copies:
            journal.touch(project / rel)
        journal.touch(project / 'phase2/stage2/synth/synth_inputs.json')
        journal.touch(project / 'provenance.jsonl')
        journal.touch(project / 'phase3/execution_modes_handoff.json')
        lc.handoff_to_direct(selected / state_rel, {'nl': project / netlist_rel},
            project / 'phase3/execution_modes_handoff.json',
            path_map={str(original_output): str(selected)})
        for rel, source in copies.items():
            if rel != netlist_rel:
                importer._copy(source, project / rel)
            if em.digest(project / rel) != em.digest(source):
                raise em.Refusal('PRODUCTION_SELECTED_IMPORT_CHANGED', str(rel))
        _write_synth_inputs_sidecar(project / netlist_rel, project / 'phase2/stage1/rtl')
        # Reuse the actual native producer row. Rebind only the canonical
        # output key; measurement and native logs remain unchanged and cited.
        rows = [json.loads(line) for line in (selected / 'project/provenance.jsonl').read_text().splitlines()]
        expected = 'sha256:' + em.digest(project / netlist_rel)
        matches = [row for row in rows if row.get('tool') == engine.provenance_tool and
                   row.get('exit_code') == 0 and expected in row.get('outputs', {}).values()]
        if not matches:
            raise em.Refusal('PRODUCTION_SELECTED_PROVENANCE_MISSING', str(netlist_rel))
        row = dict(matches[-1], outputs={netlist_rel.as_posix(): expected,
                   'phase2/stage2/synth/netlist.v':expected},
                   execution_generation=str(selected), source_receipt=str(run / adopted['selected'] / 'receipt.json'))
        with (project / 'provenance.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        directory = run / 'import-gates'
        directory.mkdir()
        primary = {
            'synth_netlist_check': _gate('synth_netlist_check', ['--netlist', str(project / netlist_rel),
                '--tool-netlist', str(selected / 'project' / netlist_rel), '--rtl',
                *map(str, sorted((project / 'phase2/stage1/rtl').rglob('*.v'))),
                *map(str, sorted((project / 'phase2/stage1/rtl').rglob('*.sv')))], directory),
            'pdk_consistency_check': _gate('pdk_consistency_check', ['--netlist', str(project / netlist_rel),
                '--pdk-lib', spec['pdk']['liberty']], directory),
            'area_total_vs_budget_check': _gate('area_total_vs_budget_check', [str(project),
                '--library', spec['pdk']['liberty']], directory),
            'provenance_check': _gate('provenance_check', [str(project), '--output', netlist_rel.as_posix(),
                '--tool', engine.provenance_tool, '--require-measured'], directory)}
        if set(primary.values()) != {'PASS'}:
            raise em.Refusal('GATE_FAIL' if 'FAIL' in primary.values() else 'GATE_NOT_MEASURED', repr(primary))
        controller._generation_current(generation)
        try:
            current_binding = context.binding()
        except em.Refusal as exc:
            raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(project)) from exc
        if current_binding != adopted['evidence']['binding']:
            raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(project))
        controller._source_current(controller.registry.adapters('9')[0])
        for rel, source in copies.items():
            if em.digest(project / rel) != em.digest(source):
                raise em.Refusal('PRODUCTION_SELECTED_IMPORT_CHANGED', str(rel))
        document = protocol.imported_result(step_id=context.step_id, source_sha=context.source_sha,
            selected_generation=generation, binding=current_binding, primary_gates=primary,
            copied={str(k): em.digest(project / k) for k in copies}, design_verdict='PASS',
            consumer_detail=dict(native_producer=producer, primary_gates=primary))
        write_json(run / 'import.json', document)
        journal.close()
        return document
    except Exception:
        journal.rollback()
        raise


class _ProgramDefaultConsumer:
    """Let an existing consumer revalidate its program adoption with the issuer.

    Consumers already call adopt again before copying. Keep that operation in
    the declared program authority without weakening Controller.adopt's AI API.
    """
    def __init__(self, controller, context, run, adopted):
        self.controller, self.context, self.run, self.adopted = controller, context, run, adopted

    def __getattr__(self, name):
        return getattr(self.controller, name)

    def adopt(self, context, run, choice):
        if (choice is not None or context is not self.context or Path(run).resolve() != self.run.resolve()
                or self.adopted.get('authority') != 'PROGRAM_DEFAULT'):
            raise em.Refusal('PROGRAM_DEFAULT_CONSUMER_UNBOUND', context.step_id)
        original = self.adopted['selected_generation']
        self.controller._generation_current(original)
        current = self.controller.adopt_default(context, run)
        if (current['selected'] != self.adopted['selected'] or current['run_id'] != self.adopted['run_id']
                or current['selected_generation']['outputs'] != original['outputs']):
            raise em.Refusal('PROGRAM_DEFAULT_CONSUMER_CHANGED', context.step_id)
        return current


def _consume(prepared: protocol.PreparedStep, request: protocol.StepRequest,
             controller: em.Controller, run: Path, adopted: dict) -> dict:
    """Fresh issued generation, bounded destination journal and domain consumer."""
    context = prepared.context
    generation = adopted['selected_generation']
    binding = adopted['evidence']['binding']
    arms = controller.registry.adapters(request.step_id)
    arm = next(a for a in arms if a.arm_id == adopted['selected'])
    paths = prepared.adoption_paths or tuple(request.parameters.get('adoption_paths', ()))
    if not paths:
        raise em.Refusal('PRODUCTION_ADOPTION_PATHS_MISSING', request.step_id)
    destinations = []
    for rel in paths:
        if (not isinstance(rel, str) or not rel or glob.has_magic(rel)
                or Path(rel).is_absolute() or '..' in Path(rel).parts
                or rel == '.' or any((request.project / Path(rel).parents[i]).is_symlink()
                                     for i in range(len(Path(rel).parents)))):
            raise em.Refusal('PRODUCTION_ADOPTION_PATH_UNSAFE', str(rel))
        destination = request.project / rel
        if destination.is_symlink() or not destination.resolve().is_relative_to(request.project):
            raise em.Refusal('PRODUCTION_ADOPTION_PATH_UNSAFE', rel)
        if any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination)
               for p in context.inputs.values()):
            raise em.Refusal('PRODUCTION_ADOPTION_OVERWRITES_INPUT', rel)
        if any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination)
               for p in destinations):
            raise em.Refusal('PRODUCTION_ADOPTION_PATH_OVERLAP', rel)
        destinations.append(destination)

    def current():
        request.check_source()
        controller._source_current(arm)
        try:
            controller._generation_current(generation)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise em.Refusal('PRODUCTION_GENERATION_UNBOUND',str(generation.get('directory'))) from exc
        if context.binding() != binding:
            raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(request.project))

    current()
    with tempfile.TemporaryDirectory(prefix='import-journal-', dir=run) as temp:
        backup = Path(temp)
        for i, path in enumerate(destinations):
            if path.is_dir():
                shutil.copytree(path, backup / str(i), symlinks=True)
            elif path.is_file():
                shutil.copy2(path, backup / str(i))
        measured_fail=False
        written = {}
        try:
            consumer_controller = (_ProgramDefaultConsumer(controller, context, run, adopted)
                if adopted.get('authority') == 'PROGRAM_DEFAULT' else controller)
            result = prepared.consume(request.project, context, consumer_controller, run, adopted)
            if isinstance(result, dict) and isinstance(result.get('copied'), dict):
                # The consumer's issued write hashes identify its writes. A
                # refusal before publication proves no outer-owned write.
                written = dict(result['copied'])
            write_json(run / 'consumer-result.json',protocol.json_parameters(result)
                       if isinstance(result,dict) else dict(invalid_result_type=type(result).__name__))
            # Measured blocking FAIL survives malformed handoffs; an actual
            # canonical advisory FAIL retains its original nonblocking tier.
            primary = result.get('primary_gates', {}) if isinstance(result, dict) else {}
            required = tuple(primary) if isinstance(primary, dict) else ()
            canonical = result.get('consumer_detail', {}).get('canonical_gate_report') if (
                isinstance(result, dict) and isinstance(result.get('consumer_detail'), dict)) else None
            if isinstance(canonical, dict) and result.get('design_verdict') != 'FAIL':
                required = canonical_gate_obligations(request.step_id, required, primary, canonical,
                                                     project=request.project)
            semantic = result.get('consumer_detail', {}).get('semantic') if (
                isinstance(result, dict) and isinstance(result.get('consumer_detail'), dict)) else None
            if semantic is not None and result.get('design_verdict') != 'FAIL':
                if set(primary) != set(context.required_gates):
                    raise em.Refusal('REQUIRED_GATES_DROPPED', request.step_id)
                required = _semantic_gate_obligations(request.step_id, context.required_gates,
                                                     primary, semantic, request.project)
            measured_fail=(isinstance(result,dict) and (result.get('design_verdict')=='FAIL'
                or any(primary.get(name) == 'FAIL' for name in required)))
            if measured_fail:
                raise em.Refusal('GATE_FAIL',repr(result.get('primary_gates')))
            if not isinstance(result, dict) or result.get('status') != 'IMPORTED':
                raise em.Refusal('PRODUCTION_CONSUMER_NOT_IMPORTED', repr(result))
            if (result.get('step_id')!=request.step_id or result.get('source_sha')!=request.source_sha
                    or result.get('binding')!=binding or result.get('selected_generation')!=generation
                    or not isinstance(result.get('consumer_detail'),dict)
                    or not result['consumer_detail']):
                raise em.Refusal('PRODUCTION_CONSUMER_BINDING_UNBOUND',request.step_id)
            if result.get('design_verdict')!='PASS':
                raise em.Refusal('GATE_NOT_MEASURED',str(result.get('design_verdict')))
            copied = result.get('copied')
            if not isinstance(copied, dict) or not copied:
                raise em.Refusal('PRODUCTION_CONSUMER_OUTPUTS_MISSING', request.step_id)
            for rel, expected in copied.items():
                path = request.project / rel
                if (Path(rel).is_absolute() or '..' in Path(rel).parts or path.is_symlink()
                        or not path.is_file() or em.digest(path) != expected
                        or not any(path == p or path.is_relative_to(p) for p in destinations)):
                    raise em.Refusal('PRODUCTION_SELECTED_IMPORT_CHANGED', rel)
                # Every adopted byte must originate in the exact issued output
                # generation. Domain transforms require their own issued output.
                if expected not in generation['outputs'].values():
                    raise em.Refusal('PRODUCTION_CONSUMER_OUTPUT_UNISSUED', rel)
            if not isinstance(result.get('primary_gates'),dict) or not result['primary_gates']:
                raise em.Refusal('GATE_NOT_MEASURED','blocking consumer gate results missing')
            if any(primary[name] != 'PASS' for name in required):
                raise em.Refusal('GATE_FAIL' if 'FAIL' in result.get('primary_gates', {}).values()
                                 else 'GATE_NOT_MEASURED', repr(result.get('primary_gates')))
            current()
            write_json(run / 'import.json', result)
            return result
        except Exception as exc:
            restored, retained = [], []
            for rel, expected in written.items():
                if (not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts
                        or not isinstance(expected, str)):
                    continue
                path = request.project / rel
                covered = next(((i, parent) for i, parent in enumerate(destinations)
                                if path == parent or path.is_relative_to(parent)), None)
                if covered is None:
                    continue
                i, parent = covered
                if (path.is_symlink() or not path.is_file()
                        or any(p.is_symlink() for p in path.parents if p.is_relative_to(request.project))
                        or em.digest(path) != expected):
                    retained.append(rel)
                    continue
                saved = backup / str(i)
                if path != parent:
                    saved = saved / path.relative_to(parent)
                if saved.is_file() and not saved.is_symlink():
                    restore = backup / ('restore-' + str(len(restored)))
                    shutil.copy2(saved, restore)
                    if em.digest(path) != expected:
                        retained.append(rel)
                        continue
                    os.replace(restore, path)
                elif not saved.exists():
                    path.unlink()
                else:
                    retained.append(rel)
                    continue
                restored.append(rel)
            write_json(run / 'import.json',dict(status='ROLLED_BACK',step_id=request.step_id,
                reason=getattr(exc,'code','PRODUCTION_CONSUMER_REFUSED'),detail=str(exc),
                design_verdict='FAIL' if measured_fail or getattr(exc,'code',None)=='GATE_FAIL' else 'NOT_MEASURED',
                rollback=dict(restored_own_files=restored, retained_changed_files=retained,
                              unproved_writes_preserved=not written,
                              authority='consumer write hashes and unchanged current bytes only'),
                raw_consumer_result=str(run/'consumer-result.json')))
            if measured_fail and getattr(exc,'code',None)!='GATE_FAIL':
                raise em.Refusal('GATE_FAIL','measured consumer FAIL; '+str(exc)) from exc
            raise


def _expert_handoff_payload(controller, context, run: Path) -> dict:
    """Preserve actual Step1 NM expert instructions from completed issued arms."""
    if context.step_id != '1':
        return {}
    plan = json.loads((run/'plan.json').read_text())
    if plan.get('binding') != context.binding():
        raise em.Refusal('EVIDENCE_UNBOUND', 'expert handoff plan binding')
    handoffs = []
    for arm in controller.registry.adapters(context.step_id):
        path = run/arm.arm_id/'receipt.json'
        if not path.is_file():
            continue
        receipt = json.loads(path.read_text())
        evidence = receipt.get('evidence') or {}
        try:
            detail = json.loads(evidence.get('detail', ''))
        except (TypeError, ValueError):
            continue
        raw = detail.get('raw_producer') if isinstance(detail, dict) else None
        if not isinstance(raw, dict) or not isinstance(raw.get('expert_handoff'), dict):
            continue
        controller._execution_authority(run, plan, receipt, arm)
        controller._current_admission(context, plan, arm)
        controller._source_current(arm)
        if (receipt.get('binding') != context.binding() or evidence.get('binding') != context.binding()
                or receipt.get('adapter') != arm.identity()):
            raise em.Refusal('EVIDENCE_UNBOUND', 'expert handoff receipt binding')
        for process in receipt['processes']:
            for channel in ('stdout', 'stderr'):
                if em.digest(Path(process[channel])) != process[channel+'_sha256']:
                    raise em.Refusal('PROCESS_LOG_CHANGED', arm.arm_id)
        outputs = _evidence_output_root(receipt)
        if 'producer.json' not in evidence['outputs']:
            raise em.Refusal('EVIDENCE_UNBOUND', 'expert raw producer artifact missing')
        producer = json.loads((outputs/'producer.json').read_text())
        if producer.get('binding') != context.binding() or producer.get('raw_producer') != raw:
            raise em.Refusal('EVIDENCE_UNBOUND', 'expert raw producer changed')
        handoff = raw['expert_handoff']
        if handoff.get('design_verdict') != 'NOT_MEASURED':
            raise em.Refusal('EVIDENCE_UNBOUND', 'expert handoff cannot certify execution')
        if any(key in handoff for key in ('source_path', 'source_sha256', 'staged_path', 'staged_sha256')):
            source = handoff.get('source_path')
            if not source or arm.source_files.get(source) != handoff.get('source_sha256'):
                raise em.Refusal('EVIDENCE_UNBOUND', 'expert skill not in issued source map')
            staged = Path(handoff.get('staged_path', ''))
            if not staged.is_absolute() or not staged.is_relative_to(outputs/'project'):
                raise em.Refusal('EVIDENCE_UNBOUND', 'expert staged skill outside issued project')
            if (evidence['outputs'].get(str(staged.relative_to(outputs))) != handoff.get('staged_sha256')
                    or handoff['staged_sha256'] != handoff['source_sha256']):
                raise em.Refusal('EVIDENCE_UNBOUND', 'expert staged skill bytes changed')
        handoffs.append(dict(arm_id=arm.arm_id, receipt=str(path), receipt_sha256=em.digest(path),
                             raw_producer=raw, expert_handoff=handoff))
    payload = dict(expert_handoffs=handoffs) if handoffs else {}
    if len(handoffs) == 1:
        payload.update(raw_producer=handoffs[0]['raw_producer'], expert_handoff=handoffs[0]['expert_handoff'])
    return payload


def dispatch_step(step_id: str, project: Path, parameters: dict, *, fixture_root: Path | None = None) -> dict:
    started = time.time()
    project = Path(project).resolve()
    transport = policy.request()
    budget = em.Budget(transport['cpus'], transport['ram_mb'], transport['workers'], transport['licenses'])
    # Outside the project and PDK: compatible with backend and release INPUT
    # populations. Keep the run/choice/control records for exact handback.
    root = Path(tempfile.mkdtemp(prefix='vibeic-execution-'+project.name+'-'))
    expert_payload = {}
    measured_fail = False
    try:
        write_json(root / 'catalog.json', catalog())
        write_json(root / 'request.json', transport)
        parameters,extras=frontdoor_parameters(dict(parameters,step_id=step_id,project=project),transport)
        factory_registry = factories()
        import execution_resource_lease as lease_api
        sha, sources = source_identity(extras)
        # Classification is source-owned and deliberately lease-free.  It
        # must be able to report a declaration, missing current input, or an
        # unavailable provider without asking the host broker to provision a
        # production cgroup.  An execute marker is only a preflight result;
        # the full factory is called again below under a live lease.
        classification_request = protocol.StepRequest(
            step_id, project, parameters, root / 'unissued-lease.json',
            root / 'classification-request.json', sha, sources)
        classified = factory_registry.classify(classification_request)
        if classified is not None and classified.disposition != 'execute':
            status = ('NOT_APPLICABLE' if classified.disposition == 'declared_inapplicable'
                      else 'NOT_MEASURED')
            handoff = dict(classified.handoff)
            result = dict(step_id=step_id, status=status, detail=classified.reason,
                handoff=handoff,
                output_files=[str(root / 'disposition.json')],
                reason_class='' if status == 'NOT_APPLICABLE' else ReasonClass.NOT_EXECUTED.value)
            if handoff.get('declaration') is not None:
                result['declared_by'] = str(handoff['declaration'])
            write_json(root / 'disposition.json', result)
            return result
        if fixture_root is None and not hasattr(lease_api,'prepare_host_scope'):
            raise em.Refusal('RESOURCE_HOST_PLACEMENT_API_UNAVAILABLE','original114 typed host scope required')
        placement = (nullcontext(None) if fixture_root is not None else
            lease_api.prepare_host_scope(budget,expected_source_sha256=em.digest(Path(lease_api.__file__))))
        with placement as host_scope, host_lease(budget, fixture_root=fixture_root) as lease:
            request = protocol.StepRequest(step_id, project, parameters, lease.resolve(),
                root / 'native-request.json', sha, sources)
            prepared = factory_registry.prepare(request)
            if prepared.disposition != 'execute':
                status = 'NOT_APPLICABLE' if prepared.disposition == 'declared_inapplicable' else 'NOT_MEASURED'
                result = dict(step_id=step_id, status=status, detail=prepared.reason,
                    handoff=dict(prepared.handoff),
                    output_files=[str(root / 'disposition.json')], reason_class='' if status == 'NOT_APPLICABLE'
                    else ReasonClass.NOT_EXECUTED.value)
                if prepared.handoff.get('declaration') is not None:
                    result['declared_by'] = str(prepared.handoff['declaration'])
                write_json(root / 'disposition.json', result)
                return result
            original=prepared.context
            context=_SourceContext(original.step_id,original.source_sha,original.inputs,
                original.objective,original.required_gates,original.native_mode,
                original=original,sources=sources,extra_sources=extras)
            prepared=replace(prepared,context=context)
            context, registry = prepared.context, prepared.registry
            # The current backend worker owns copy/translate/clear. Bind its
            # verifier to the actual component path, never a request flag or
            # a caller-authored allowed-hash list.
            worker = str((PROGRAMS/'execution_backend_worker.py').resolve())
            derived = em.Registry()
            for arm in registry.adapters(step_id):
                if any(len(c.argv)>1 and c.argv[1]==worker for c in arm.components):
                    arm = replace(arm,project_derivation=backend_working_derivation)
                derived.register(arm)
            registry = derived
            controller = ProductionController(registry, budget,host_scope=host_scope,lease=lease)
            run = root / 'run'
            result = controller.run(context, run, transport['mode'] + '-mode',
                                    superiority=policy.load_superiority(transport.get('superiority')))
            statuses = result.get('candidate_statuses', {})
            measured_fail = 'FAIL' in statuses.values()
            expert_payload = _expert_handoff_payload(controller, context, run)
            if measured_fail:
                return dict(step_id=step_id, status='FAIL', detail='actual native/semantic gate FAIL',
                            duration_s=time.time()-started, output_files=[str(run/'result.json')],
                            candidate_statuses=statuses, **expert_payload)
            if 'ELIGIBLE' not in statuses.values():
                instructions = '\n'.join(str(item['raw_producer'].get('detail') or
                    item['expert_handoff'].get('runner_detail') or item['expert_handoff'].get('reason', ''))
                    for item in expert_payload.get('expert_handoffs', ()))
                raise em.Refusal('PRODUCTION_NATIVE_NOT_MEASURED', instructions or json.dumps(result))
            # Qualified default artifacts use the issued program selection.
            # Ultra and Step9 retain their explicit current expert decision.
            if step_id == '9':
                from execution_expert_choice import request_choice
                choice = request_choice(controller, context, run, parameters,
                    root / 'ai-review-request.json', transport['choice'], transport['choice_wait_s'])
            elif transport['mode'] == 'default':
                adopted = controller.adopt_default(context, run)
            else:
                write_json(root / 'ai-review-request.json', dict(step_id=step_id, run=str(run),
                    source_sha=sha, binding=context.binding(), candidate_statuses=statuses,
                    choice_destination=transport['choice'], deadline_wait_s=transport['choice_wait_s'],
                    requested_review='Review exact design INPUT and producer/gate artifacts; select an eligible issued receipt.'))
                choice = _choice(transport['choice'], transport['choice_wait_s'])
            if step_id == '9' or transport['mode'] == 'ultra':
                adopted = controller.adopt(context, run, choice)
            imported = _consume(prepared, request, controller, run, adopted)
            return dict(step_id=step_id, status='PASS', duration_s=time.time()-started,
                detail='issued generation imported; primary gates PASS; ' + str(run),
                output_files=[str(project/rel) for rel in imported['copied']],
                selected=adopted['selected'], selected_generation=adopted['selected_generation'],
                candidate_statuses=statuses,
                adoption_authority=adopted.get('authority', 'AI_CHOICE'),
                independent_ai_qualification=adopted.get('independent_ai_qualification',
                    'NOT_MEASURED_BY_ADOPTION'),
                expert_qualification=adopted.get('expert_qualification', 'NOT_MEASURED_BY_ADOPTION'),
                native_accuracy='NOT_MEASURED_BY_ADOPTION',
                resource_scope='source-fixture' if fixture_root is not None else 'production')
    except (em.Refusal, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        code = getattr(exc, 'code', 'PRODUCTION_PROTOCOL_UNMEASURED')
        write_json(root / 'refusal.json', dict(status='REFUSED', reason=code, detail=str(exc), **expert_payload))
        return dict(step_id=step_id, status='FAIL' if measured_fail or code == 'GATE_FAIL' else 'NOT_MEASURED',
                    duration_s=time.time()-started, detail=str(exc), reason=code,
                    output_files=[str(root/'refusal.json')],
                    reason_class='' if measured_fail or code == 'GATE_FAIL' else ReasonClass.EXECUTION_ERROR.value,
                    **expert_payload)


def dispatch_synthesis(project: Path, top: str, pdk, container: str, period_relax: float = 1.0):
    import phase3_one_shot_runner as runner
    result = dispatch_step('9', project, dict(top=top, pdk=pdk, container=container, period_relax=period_relax))
    return _producer_row(runner, 'synth', result)


def dispatch_site(step_ids: tuple[str, ...], project: Path, parameters: dict) -> dict | None:
    """A native callable seam; no outer phase runner is ever a candidate.

    A family not yet installed retains its original native producer and an
    explicit OPEN coverage receipt. A partially installed compound site is
    refused: mixing selected output with the old compound producer is unsafe.
    """
    callback = em.current_issued_child_scope()
    # The actual docs implementation dispatches inside the design-issued
    # Python descendant. ContextVar state cannot cross that exec boundary.
    # The startup FD only locates the transport; the live issuer must admit
    # this exact descendant before a scope can be installed.
    if callback is None and em._CALLBACK_FD_ENV in os.environ:
        if len(step_ids) != 1:
            raise em.Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','single issued step required')
        frame = inspect.currentframe().f_back
        try:
            source = str(Path(frame.f_code.co_filename).resolve())
            depth = 2 if step_ids==('D1',) and source==str(PROGRAMS/'phase1_doc_one_shot_runner.py') else 1
            callback = em._issued_child_scope(Path(project),step_ids[0],issuer_depth=depth)
            callback.authorize(frame.f_globals['__name__']+'.'+frame.f_code.co_name,
                source)
        finally:
            del frame
        em._STEP1_CALLBACK.set(callback)
    if callback is not None:
        if step_ids != (callback.binding['step_id'],) or Path(project) != callback.project:
            raise em.Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND', 'different callback step/project')
        caller, source = parameters.get('native_callable'), parameters.get('native_source')
        if source is None:
            frame = inspect.currentframe().f_back
            try:
                caller = frame.f_globals['__name__']+'.'+frame.f_code.co_name
                source = str(Path(frame.f_code.co_filename).resolve())
            finally:
                del frame
        if not isinstance(caller,str) or not isinstance(source,str):
            raise em.Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND', 'callback callable/source missing')
        callback.authorize(caller,source)
        return None
    # Captured/source-only callable controls have a separate live capability.
    # It is issued by host_lease(fixture_root=...), bound to this exact
    # callable/source/step/project, and never authorizes a native boundary.
    import execution_resource_lease as lease_api
    fixture_scope = lease_api.current_source_fixture_scope()
    if fixture_scope is not None:
        if len(step_ids) != 1:
            raise em.Refusal('SOURCE_FIXTURE_SCOPE_UNBOUND', 'single callable step required')
        caller, source = parameters.get('native_callable'), parameters.get('native_source')
        if source is None:
            frame = inspect.currentframe().f_back
            try:
                caller = frame.f_globals['__name__']+'.'+frame.f_code.co_name
                source = str(Path(frame.f_code.co_filename).resolve())
            finally:
                del frame
        fixture_scope.authorize(step_ids=step_ids, project=Path(project),
                                caller=caller, source=source)
        return None
    transport = policy.request()
    if os.environ.get('VIBEIC_EXECUTION_NATIVE_STEP') in step_ids:
        if os.environ['VIBEIC_EXECUTION_NATIVE_STEP'] == '1':
            raise em.Refusal('STEP1_CALLBACK_UNBOUND','Step1 requires its live issued callback scope')
        raise em.Refusal('PRODUCTION_NATIVE_REENTRY_UNBOUND','live Controller-issued child scope required')
    coverage = factories().coverage()
    installed = [sid for sid in step_ids if sid in coverage]
    if not installed:
        destination = Path(project) / 'reports/execution_modes/native-retained' / (uuid.uuid4().hex+'.json')
        write_json(destination, dict(step_ids=list(step_ids), policy=transport,
            status='OPEN_DOMAIN_FACTORY', native_producer='retained at existing callable',
            native_qualification='NOT_MEASURED', source_coverage=False))
        return None
    if len(installed) != len(step_ids):
        return dict(status='NOT_MEASURED', duration_s=0, output_files=[],
            reason_class=ReasonClass.NOT_EXECUTED.value,
            detail='compound native site has incomplete factory coverage: '+str(step_ids))
    results = []
    for sid in step_ids:
        result = dispatch_step(sid, Path(project), parameters)
        results.append(result)
        if result['status'] not in ('PASS', 'NOT_APPLICABLE'):
            break
    terminal = next((r for r in results if r['status'] == 'FAIL'), None)
    terminal = terminal or next((r for r in results if r['status'] == 'NOT_MEASURED'), None) or results[-1]
    return {**terminal, 'duration_s': sum(r.get('duration_s',0) for r in results),
            'output_files': [p for r in results for p in r.get('output_files',[])],
            'detail': '; '.join(r['detail'] for r in results), 'execution_rows':results}


def issued_host_popen(argv, **kwargs):
    """F2's actual design watchdog uses this factory inside an issued scope."""
    scope = em.current_issued_child_scope()
    if scope is None:
        raise em.Refusal('PRODUCTION_CHILD_SCOPE_UNBOUND','actual issued host parent required')
    return scope.launch_host_program(argv,**kwargs)


def _producer_row(module, name: str, result: dict):
    """Project a dispatch row without dropping structured producer semantics."""
    fields = inspect.signature(module.StepResult).parameters
    values = dict(name=result.get('name', name), status=result['status'],
        duration_s=result.get('duration_s', 0), detail=result['detail'],
        output_files=result.get('output_files', []))
    for key in ('block', 'reason_class', 'declared_by', 'waiver_rows', 'attribution', 'disclosures'):
        if key in fields and key in result:
            values[key] = result[key]
    extra = dict(result.get('extras') or {})
    for key in ('expert_handoff', 'expert_handoffs', 'raw_producer', 'execution_rows',
                'candidate_statuses', 'adoption_authority', 'independent_ai_qualification',
                'expert_qualification', 'native_accuracy', 'resource_scope'):
        if key in result:
            extra[key] = result[key]
    if extra and 'extras' in fields:
        values['extras'] = extra
    return module.StepResult(**values)


def production_step(*step_ids: str, result_list: bool = False):
    """Minimal hook at a source-owned producer callable, including import callers."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def invoke(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = dict(bound.arguments)
            project = values.pop('project')
            values['native_callable'] = function.__module__+'.'+function.__qualname__
            values['native_source'] = str(Path(inspect.getsourcefile(function)).resolve())
            values['declaration'] = str(Path(project)/'input/submission_template/tapeout_declaration.json')
            result = dispatch_site(tuple(step_ids), Path(project), values)
            if result is None:
                row = function(*args, **kwargs)
                callback = em.current_issued_child_scope()
                if callback is not None:
                    try:
                        callback.authorize(values['native_callable'],values['native_source'])
                    except em.Refusal as exc:
                        if getattr(row,'status',None) != 'FAIL':
                            raise
                        if isinstance(getattr(row,'extras',None),dict):
                            row.extras['execution_callback_refusal'] = dict(reason=exc.code,detail=str(exc))
                return row
            module = sys.modules[function.__module__]
            row = _producer_row(module,
                function.__name__.removeprefix('step_').removeprefix('run_'), result)
            return [row] if result_list else row
        return invoke
    return decorate


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(catalog(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
