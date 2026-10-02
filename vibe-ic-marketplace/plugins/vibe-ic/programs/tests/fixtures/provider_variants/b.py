"""Finite neutral text tool for controller acceptance; contains no EDA engine.

The two components really consume/write files and create real subprocess rc,
logs, partial output and synchronization intervals. Fault arguments model an
ordinary tool failing or emitting unusable evidence, not a mocked controller.
"""
import json
import os
import resource
from pathlib import Path
import sys
import time

inputs, outputs = Path(sys.argv[1]), Path(sys.argv[2])
action, fault, duration, cost = sys.argv[3:7]
print('neutral tool started', action, flush=True)
print('neutral stderr', flush=True, file=sys.stderr)
time.sleep(float(duration))
if action == 'transform':
    assert len((inputs / 'text.txt').read_text()) < 100001
    (outputs / 'value.txt').write_text(''.join(c.upper() for c in (inputs / 'text.txt').read_text()))
    if fault == 'process_error':
        sys.exit(7)
    if fault == 'ram_exceed':
        allocation = bytearray(200 * 1024 * 1024)
    if fault == 'mutate_frozen':
        (inputs / 'text.txt').chmod(0o644)
        (inputs / 'text.txt').write_text('changed by tool')
    if fault == 'partial':
        print('partial output preserved', flush=True)
        time.sleep(5)
    sys.exit(0)
if action != 'measure':
    sys.exit(9)
# This dependency is an actual file consumer. Running it before transform
# cannot produce the same result and fails as an ordinary command.
value = (outputs / 'value.txt').read_text()
binding = json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
if fault == 'wrong_source':
    binding['source_sha'] = '0' * 40
if fault == 'stale_input':
    binding['inputs']['text.txt'] = '0' * 64
if fault == 'wrong_objective':
    binding['objective'] = {'goal': 'different'}
if fault == 'no_report':
    sys.exit(0)
verdict = {'fail_gate': 'FAIL', 'unmeasured': 'NOT_MEASURED'}.get(fault, 'PASS')
report = dict(binding=binding, verdict=verdict, gates={'transform': verdict},
              metrics={'cost': float(cost)}, value=value,
              affinity=sorted(os.sched_getaffinity(0)),
              address_space_limit=resource.getrlimit(resource.RLIMIT_AS)[0])
(outputs / 'measurement.json').write_text(json.dumps(report))
