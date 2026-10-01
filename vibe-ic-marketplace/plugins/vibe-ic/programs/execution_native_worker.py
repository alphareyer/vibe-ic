"""Source-owned production child; calls the existing native implementation once.

There is no adoption/HMAC issuer in this worker. External native execution may
be captured in protocol controls; the existing semantic gates are never stubbed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_json
import execution_modes as em
from execution_resource_lease import native_boundary, record_worker


def translate(value, project: Path, pdk_root: Path, spec: dict):
    if isinstance(value, list):
        return [translate(item, project, pdk_root, spec) for item in value]
    if isinstance(value, dict):
        return {key: translate(item, project, pdk_root, spec) for key, item in value.items()}
    if isinstance(value, str) and value.startswith('/'):
        for old, new in ((spec['original_project'], project), (spec['original_pdk_root'], pdk_root)):
            if value == old or value.startswith(old.rstrip('/') + '/'):
                return str(new) + value[len(old):]
        raise em.Refusal('PRODUCTION_EXTERNAL_INPUT_UNBOUND', value)
    return value


def execute(inputs: Path, outputs: Path) -> dict:
    spec = json.loads((inputs / 'request.json').read_text())
    import execution_synthesis_engines as engines
    engine = engines.require_spec(spec)
    binding = json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
    if binding.get('source_sha') != spec['source_sha']:
        raise em.Refusal('PRODUCTION_WORKER_SOURCE_UNBOUND', str(inputs))
    if spec['input_hashes'] != {k: v for k, v in binding['inputs'].items() if k != 'request.json'}:
        raise em.Refusal('PRODUCTION_WORKER_INPUT_MANIFEST_UNBOUND', str(inputs))
    for name, expected in spec['source_files'].items():
        if em.digest(Path(name)) != expected:
            raise em.Refusal('ADAPTER_SOURCE_MISMATCH', name)
    for name, expected in spec['input_hashes'].items():
        if em.digest(inputs / name) != expected:
            raise em.Refusal('FROZEN_INPUT_CHANGED', name)
    project = outputs / 'project'
    if project.exists():
        raise em.Refusal('PRODUCTION_WORKER_OUTPUT_REUSED', str(project))
    if (inputs / 'project').is_dir():
        shutil.copytree(inputs / 'project', project)
    else:
        project.mkdir()
    pdk_root = inputs / 'pdk'
    pdk_values = translate(spec['pdk'], project, pdk_root, spec)
    from phase3_one_shot_runner import PdkConfig, _step_synth_librelane
    pdk = PdkConfig(**pdk_values)
    switch = project / 'phase3/librelane_switch.json'
    switch.parent.mkdir(parents=True, exist_ok=True)
    options = json.loads(switch.read_text()) if switch.is_file() else {}
    options.update(image=spec['image_id'], pdk_root_host=str(pdk_root))
    write_json(switch, options)
    for name in ('VIBE_IC_RUNNER_LOCK_TOKEN', 'VIBE_IC_CANONICAL_ADMISSION_TOKEN',
                 'VIBEIC_LIBRELANE_IMAGE', 'VIBEIC_LIBRELANE_PDK_ROOT'):
        os.environ.pop(name, None)
    lease = Path(spec['lease'])
    os.environ['VIBEIC_EXECUTION_NATIVE_STEP'] = binding['step_id']
    readonly = []
    # Overlays make the actual native container inputs immutable even though
    # its output project mount is writable. The input files are also rehashed.
    for rel in ('input', 'phase1/generated_docs', 'phase2/stage1/rtl',
                'phase2/stage2/constraints', 'pdk_local'):
        source = inputs / 'project' / rel
        if source.is_dir():
            readonly.append([str(source), str(project / rel)])
    record_worker(lease, readonly)
    with native_boundary(lease, expected_sha256=spec['input_hashes'].get('lease.json')):
        result = _step_synth_librelane(project, spec['top'], pdk, spec['image_id'])
    commands = lease / 'commands.jsonl'
    (outputs / 'native_commands.jsonl').write_bytes(commands.read_bytes() if commands.is_file() else b'')
    receipt = dict(binding=binding, status=result.status, detail=result.detail,
                   native_entrypoint=engine.native_entrypoint,
                   synthesis_engine=engine.contract(),
                   native_result=asdict(result), pdk=pdk_values,
                   input_hashes=spec['input_hashes'], native_folder=None, native_checkers=[])
    if result.status == 'PASS':
        import librelane_contract as lc
        # The chain already measured this pinned image's capability. Retain
        # its source-owned fingerprint extension for the fresh consumer.
        receipt['native_capability'] = lc.image_capability(spec['image_id'])
        state_paths = [Path(path) for path in result.output_files if Path(path).name == 'state_out.json']
        if len(state_paths) != 1:
            raise em.Refusal('PRODUCTION_NATIVE_STATE_AMBIGUOUS', str(result.output_files))
        receipt['native_folder'] = str(state_paths[0].parent)
        for native_receipt in (project / 'phase3/librelane').rglob('vibeic_receipt.json'):
            step = json.loads(native_receipt.read_text()).get('input', {}).get('step', '')
            if step.startswith('Checker.'):
                receipt['native_checkers'].append(str(native_receipt.parent))
    write_json(outputs / 'producer.json', receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = execute(args.inputs.resolve(), args.outputs.resolve())
        print(json.dumps(dict(status=receipt['status'], detail=receipt['detail'])))
        # rc0 means this worker completed its protocol. The parent separately
        # consumes the actual producer and mandatory gate verdicts.
        return 0
    except (em.Refusal, OSError, ValueError, KeyError) as exc:
        write_json(args.outputs / 'worker-refusal.json', dict(status='NOT_MEASURED', detail=str(exc)))
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
