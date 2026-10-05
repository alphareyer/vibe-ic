"""Native Step9 child: execute the source-owned LibreLane synthesis once."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import MISSING, fields
import json
from pathlib import Path
import re
import shutil
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic_artefact import write_json
import execution_modes as em
import execution_native_installation as installation
import execution_synthesis_engines as engines


def _translate(value, original: Path, project: Path, pdk_root: Path):
    if isinstance(value, list):
        return [_translate(item, original, project, pdk_root) for item in value]
    if isinstance(value, dict):
        return {key: _translate(item, original, project, pdk_root)
                for key, item in value.items()}
    if isinstance(value, str) and value.startswith('/'):
        old_project = str(original)
        if value == old_project or value.startswith(old_project.rstrip('/') + '/'):
            return str(project) + value[len(old_project):]
        return value
    return value


def _pdk_config(values: dict, pdk_root: Path, original: Path, project: Path):
    from phase3_one_shot_runner import PdkConfig
    translated = _translate(values, original, project, pdk_root)
    names = {item.name for item in fields(PdkConfig)}
    kwargs = {name: translated[name] for name in names if name in translated}
    # PDK assets remain at the declared host root.  LibreLane's contract
    # mounts that root read-only as /pdk; replacing paths with a one-file
    # fixture is the SCL-unresolved failure this worker must never hide.
    # Small source tests may use only name/liberty. Required fields are kept
    # explicit empty values; native LibreLane then refuses missing PDK data.
    for item in fields(PdkConfig):
        if (item.name not in kwargs and item.default is MISSING
                and item.default_factory is MISSING):
            kwargs[item.name] = "" if item.type in (str, 'str') else None
    return PdkConfig(**kwargs)


def _pinned_image_reference(spec: Mapping[str, object]) -> str:
    """Return the parent-verified reference paired with the measured image ID."""
    request = spec.get('native_installation_request')
    receipt = spec.get('native_installation_receipt')
    image_id = spec.get('image_id')
    if not isinstance(request, Mapping) or not isinstance(receipt, Mapping):
        raise em.Refusal('PRODUCTION_IMAGE_REFERENCE_UNBOUND', 'installation binding absent')
    image_ref = request.get('image_ref')
    if (not isinstance(image_ref, str)
            or re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image_ref) is None
            or request.get('step_id') != '9'
            or receipt.get('step_id') != '9'
            or request.get('image_id') != image_id
            or receipt.get('image_id') != image_id
            or receipt.get('image_ref') != image_ref
            or spec.get('native_installation_receipt_sha256') != receipt.get('receipt_sha256')
            or not installation.verify_receipt_digest(receipt)):
        raise em.Refusal('PRODUCTION_IMAGE_REFERENCE_UNBOUND', str(image_ref))
    return image_ref


def _output_hashes(outputs: Path) -> dict[str, str]:
    """Hash the producer-owned files before the receipt is written.

    The receipt deliberately excludes itself.  Every file named here is
    rehashed by the Step 9 consumer, so a filename left by an older run cannot
    satisfy the handoff merely by existing.
    """
    result = {}
    for path in outputs.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(outputs).as_posix()
        if (rel == engines.NATIVE_COMMANDS or
                rel.startswith("project/phase2/stage2/synth/") or
                rel.startswith("project/phase3/")):
            result[rel] = em.digest(path)
    return result


def execute(inputs: Path, outputs: Path) -> dict:
    spec = json.loads((inputs / 'request.json').read_text())
    engine = engines.require_spec(spec)
    binding = json.loads(__import__('os').environ['VIBEIC_EXECUTION_BINDING'])
    if spec.get('source_sha') != binding.get('source_sha'):
        raise em.Refusal('PRODUCTION_WORKER_SOURCE_UNBOUND', str(inputs))
    expected = {k: v for k, v in binding.get('inputs', {}).items() if k != 'request.json'}
    if spec.get('input_hashes') != expected:
        raise em.Refusal('PRODUCTION_WORKER_INPUT_MANIFEST_UNBOUND', str(inputs))
    for name, digest in spec.get('input_hashes', {}).items():
        if not (inputs / name).is_file() or em.digest(inputs / name) != digest:
            raise em.Refusal('FROZEN_INPUT_CHANGED', name)
    project = outputs / 'project'
    if project.exists():
        raise em.Refusal('PRODUCTION_WORKER_OUTPUT_REUSED', str(project))
    if (inputs / 'project').is_dir():
        shutil.copytree(inputs / 'project', project)
    else:
        raise em.Refusal('PRODUCTION_WORKER_PROJECT_MISSING', str(inputs))
    pdk_root = inputs / 'pdk'
    manifest_path = pdk_root / 'tree-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'vibe-ic/step9-pdk-tree/1':
        raise em.Refusal('PRODUCTION_PDK_MANIFEST_INVALID', str(manifest_path))
    declared_root = Path(str(spec.get('pdk_root_host') or manifest.get('root') or ''))
    if not declared_root.is_dir() or declared_root.resolve() != Path(str(manifest.get('root'))).resolve():
        raise em.Refusal('PRODUCTION_PDK_ROOT_UNBOUND', str(declared_root))
    for row in manifest.get('files', []):
        if not isinstance(row, dict):
            raise em.Refusal('PRODUCTION_PDK_MANIFEST_INVALID', str(manifest_path))
        path = declared_root / str(row.get('path', ''))
        if (not path.is_file() or path.is_symlink() or
                em.digest(path) != row.get('sha256')):
            raise em.Refusal('PRODUCTION_PDK_CHANGED', str(path))
    # Keep the frozen declared liberty alongside the run outputs for consumer
    # gates.  The native tool still reads the full declared root above; this
    # is a byte-for-byte mirror, never a synthetic SCL.
    frozen_pdk = outputs / 'pdk'
    frozen_pdk.mkdir(parents=True, exist_ok=True)
    for source in sorted(pdk_root.iterdir()):
        if not source.is_file():
            continue
        shutil.copyfile(source, frozen_pdk / source.name)
    pdk = _pdk_config(spec.get('pdk', {}), declared_root,
                      Path(spec['original_project']), project)
    image_ref = _pinned_image_reference(spec)
    switch = project / 'phase3/librelane_switch.json'
    switch.parent.mkdir(parents=True, exist_ok=True)
    options = json.loads(switch.read_text()) if switch.is_file() else {}
    options.update(image=image_ref, pdk_root_host=str(declared_root.parent),
                   pdk=spec.get('pdk', {}).get('name'))
    write_json(switch, options, sort_keys=True)
    from phase3_one_shot_runner import _step_synth_librelane
    command = {
        'executed': False,
        'native_entrypoint': engine.native_entrypoint,
        'image_id': spec['image_id'],
        'argv': ['phase3_one_shot_runner._step_synth_librelane', spec['top'],
                 image_ref],
    }
    try:
        # The native helper may refuse during image/PDK preflight before it
        # reaches LibreLane. Mark the journal only when its real run-chain
        # entrypoint is entered; a preflight refusal is NOT_MEASURED.
        import librelane_contract as _lc
        _run_chain = _lc.run_chain
        def _traced_run_chain(*args, **kwargs):
            command['executed'] = True
            return _run_chain(*args, **kwargs)
        _lc.run_chain = _traced_run_chain
        result = _step_synth_librelane(project, spec['top'], pdk, image_ref)
    except Exception as exc:
        result = None
        detail = repr(exc)
    else:
        detail = getattr(result, 'detail', '')
    commands = outputs / 'native_commands.jsonl'
    commands.parent.mkdir(parents=True, exist_ok=True)
    native_trace = []
    native_root = project / 'phase3/librelane'
    if native_root.is_dir():
        for path in sorted(native_root.glob('*/COMMANDS')):
            native_trace.append({'path': str(path.relative_to(project)),
                                 'sha256': em.digest(path)})
        for path in sorted(native_root.glob('*/vibeic_receipt.json')):
            native_trace.append({'path': str(path.relative_to(project)),
                                 'sha256': em.digest(path)})
    command['native_trace'] = native_trace
    commands.write_text(json.dumps(command, sort_keys=True) + '\n')
    status = getattr(result, 'status', 'NOT_MEASURED') if result is not None else 'NOT_MEASURED'
    # A native producer may return a measured FAIL during its own input or
    # clock preflight before Docker is entered.  Preserve that FAIL; turning
    # it into NOT_MEASURED based on a wrapper trace would erase the producer's
    # authoritative disposition.
    if status == 'PASS':
        # _step_synth_librelane publishes this handoff only after the native
        # Yosys product and its producer receipt have been checked.  Do not
        # recreate the canonical netlist from ``<top>_synth.v`` here: a legacy
        # filename is not evidence of a current native producer.
        import synth_handoff_netlist_check as _handoff
        mapped = project / 'phase2/stage2/synth' / f"{spec['top']}_synth.v"
        canonical = project / 'phase2/stage2/synth/netlist.v'
        handoff = _handoff.bound_handoff(project)
        if (not isinstance(handoff, dict) or handoff.get('verdict') != 'PASS' or
                handoff.get('mapped') != str(mapped) or
                handoff.get('canonical') != str(canonical) or
                not mapped.is_file() or not canonical.is_file() or
                em.digest(mapped) != em.digest(canonical)):
            detail = ((handoff or {}).get('findings') if isinstance(handoff, dict)
                      else None) or 'native synthesis handoff is absent'
            raise em.Refusal('PRODUCTION_NATIVE_HANDOFF_UNBOUND', str(detail))
        stat_candidates = [Path(p) for p in getattr(result, 'output_files', [])
                           if Path(p).name in ('stats.json', 'stat.json', 'stat.rpt')]
        # Prefer the source-owned stats emitter's schema over LibreLane's raw
        # Yosys ``reports/stat.json``.  Both are native files, but only the
        # former carries the measured area/unit and netlist digest contract.
        stat_candidates.sort(key=lambda p: (p.name != 'stats.json',
                                            'phase2/stage2/synth' not in p.as_posix()))
        stats = project / 'phase2/stage2/synth/stats.json'
        for path in stat_candidates:
            if path.is_file() and path.suffix == '.json':
                if path.resolve() != stats.resolve():
                    shutil.copyfile(path, stats)
                break
        # Preserve a native area report when a producer supplied one. The
        # canonical any-of contract accepts either this report or stats.json.
        area = project / 'phase2/stage2/synth/area.rpt'
        if not area.is_file():
            for path in stat_candidates:
                if path.is_file() and path.suffix == '.rpt':
                    shutil.copyfile(path, area)
                    break
    native_netlist = None
    for path in getattr(result, 'output_files', []) if result is not None else []:
        p = Path(path)
        if p.is_file() and p.suffix in ('.v', '.sv'):
            native_netlist = str(p)
            break
    if status == 'PASS' and native_netlist is None:
        raise em.Refusal('PRODUCTION_NATIVE_NETLIST_UNBOUND', 'native output list')
    input_hashes = spec.get('input_hashes', {})
    source_files = spec.get('source_files', {})
    native_relative = None
    native_output_rel = None
    if native_netlist is not None:
        native_path = Path(native_netlist).resolve()
        if not native_path.is_relative_to(project.resolve()):
            raise em.Refusal('PRODUCTION_NATIVE_NETLIST_UNBOUND', native_netlist)
        native_relative = str(native_path.relative_to(project.resolve()))
        native_output_rel = 'project/' + native_relative
    source_tree_sha256 = engines.tree_digest(source_files)
    input_tree_sha256 = engines.tree_digest(input_hashes)
    tool = {
        'image_id': spec.get('image_id'),
        'native_entrypoint': engine.native_entrypoint,
        'native_trace': native_trace,
    }
    tool_sha256 = engines.stable_digest(tool)
    output_hashes = _output_hashes(outputs)
    canonical_rel = 'project/' + engines.CANONICAL_NETLIST
    if status == 'PASS' and (canonical_rel not in output_hashes or
                             native_output_rel is None or
                             native_output_rel not in output_hashes):
        raise em.Refusal('PRODUCTION_NATIVE_OUTPUT_UNBOUND', canonical_rel)
    binding_record = None
    if status == 'PASS':
        binding_record = engines.consumer_binding(
            top=spec['top'], native_netlist=native_output_rel,
            canonical_sha256=output_hashes[canonical_rel],
            native_sha256=output_hashes[native_output_rel],
            source_tree_sha256=source_tree_sha256,
            input_tree_sha256=input_tree_sha256, tool_sha256=tool_sha256)
    receipt = {
        'schema': engines.RECEIPT_SCHEMA,
        'step_id': engines.STEP_ID,
        'top': spec.get('top'),
        'canonical_netlist': engines.CANONICAL_NETLIST,
        'source_tree_sha256': source_tree_sha256,
        'input_tree_sha256': input_tree_sha256,
        'tool': tool,
        'tool_sha256': tool_sha256,
        'output_hashes': output_hashes,
        'consumer_binding': binding_record,
        'binding': binding,
        'status': status,
        'detail': detail,
        'source_sha': spec['source_sha'],
        'source_files': source_files,
        'native_installation_receipt_sha256': spec.get('native_installation_receipt_sha256'),
        'native_entrypoint': engine.native_entrypoint,
        'synthesis_engine': engine.contract(),
        'input_hashes': input_hashes,
        'native_tool_netlist': native_netlist,
        'native_trace': native_trace,
        'native_result': (getattr(result, '__dict__', None) or {}),
    }
    write_json(outputs / 'producer.json', receipt, sort_keys=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate', choices=engines.REQUIRED_GATES, default=None)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.gate:
            import execution_production
            return execution_production.run_synthesis_gate(args.inputs.resolve(), args.outputs.resolve(), args.gate)
        receipt = execute(args.inputs.resolve(), args.outputs.resolve())
        print(json.dumps({'status': receipt['status'], 'detail': receipt['detail']}))
        return 0
    except (em.Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        write_json(args.outputs / 'worker-refusal.json',
                   {'status': 'NOT_MEASURED', 'detail': str(exc)}, sort_keys=True)
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
