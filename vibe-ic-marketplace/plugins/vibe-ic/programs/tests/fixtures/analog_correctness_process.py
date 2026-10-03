"""Supported supervised correctness fixture; NO native/PDK qualification.

Uses the existing A6 producer's explicit tool-runner seam. Every tool result
comes from a real bounded Python subprocess reading frozen synthetic input.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

P = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(P))
import execution_modes as em
import execution_analog_worker as worker
from execution_adapters_analog import write_json, component_sources


def fixture_produce(inputs, project, spec, records):
    import analog_a6_native_pv as pv
    records.mkdir(parents=True, exist_ok=True)
    processes = records / 'native-processes.jsonl'
    def process(kind):
        argv = [sys.executable, str(Path(__file__).resolve()), '--tool', kind,
                str(project / 'input/correctness.json')]
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={k: v for k, v in os.environ.items() if not k.startswith('VIBEIC_EXECUTION')})
        out, err = child.communicate(timeout=5)
        with processes.open('a') as stream:
            stream.write(json.dumps(dict(argv=argv, pid=child.pid, rc=child.returncode,
                stdout=out, stderr=err, scope='correctness fixture only')) + '\n')
        if child.returncode != 0:
            return None
        return json.loads(out)
    def drc(*args):
        result = process('drc')
        return (result['violations'], dict(method='supervised correctness fixture',
                     rules_pass=1, rules_skip=0)) if result else (None, {})
    def lvs(*args):
        result = process('lvs')
        return (result['verdict'], dict(method='supervised correctness fixture')) if result else (None, {})
    result = pv.run_block_pv(project, 'unit', dict(drc_deck='synthetic.rule', lvs_deck='synthetic.rule'),
                             'host', drc_runner=drc, lvs_runner=lvs)
    calls = [dict(entrypoint='analog_a6_native_pv.run_block_pv', pid=os.getpid(), block='unit',
                  source_sha256=em.digest(Path(pv.__file__)), result=result)]
    write_json(records / 'calls.json', calls)
    return calls


def main():
    if sys.argv[1] == '--tool':
        kind, path = sys.argv[2], Path(sys.argv[3])
        data = json.loads(path.read_text())
        if kind == 'lvs' and data.get('missing_lvs'):
            return 2
        result = dict(violations=sum(width < data['minimum'] for width in data['widths'])) if kind == 'drc' else dict(verdict='MATCH' if data['expected_nodes'] == data['observed_nodes'] else 'MISMATCH')
        print(json.dumps(result))
        return 0
    parser = __import__('argparse').ArgumentParser()
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    args = parser.parse_args()
    # This is an explicit source fixture of the production worker's producer
    # boundary. No Controller/issuer/validator is replaced or fabricated.
    worker.native_produce = fixture_produce
    worker.execute(args.inputs, args.outputs, source_files=component_sources((Path(__file__).resolve(),)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
