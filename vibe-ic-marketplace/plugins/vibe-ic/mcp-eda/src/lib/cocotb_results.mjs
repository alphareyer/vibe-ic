// cocotb's native JUnit testcase population owns the counts. Run the stdlib
// XML reader beside the native run, where its results file actually lives.
import { shq } from "./shell_safety.mjs";

const MARKER = "===VIBEIC_COCOTB_JUNIT===";

export function cocotbJunitScript(resultsFile) {
  return `python3 - ${shq(resultsFile)} <<'VIBEIC_COCOTB_JUNIT_PY'
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])
result = dict(results_file=str(path), junit_valid=False, tests_passed=0,
              tests_failed=0, tests_errors=0, tests_skipped=0, tests_total=0)
local = lambda tag: tag.rsplit('}', 1)[-1]
try:
    root = ET.parse(path).getroot()
    if local(root.tag) not in ('testsuite', 'testsuites'):
        raise ValueError('not a JUnit testsuite')
    for case in root.iter():
        if local(case.tag) != 'testcase':
            continue
        result['tests_total'] += 1
        children = {local(child.tag) for child in case}
        if 'error' in children:
            result['tests_errors'] += 1
            result['tests_failed'] += 1
        elif 'failure' in children:
            result['tests_failed'] += 1
        elif 'skipped' in children:
            result['tests_skipped'] += 1
        else:
            result['tests_passed'] += 1
    result['junit_valid'] = True
except (OSError, ValueError, ET.ParseError) as exc:
    for key in ('tests_passed', 'tests_failed', 'tests_errors', 'tests_skipped', 'tests_total'):
        result[key] = None
    result['error'] = 'COCOTB_JUNIT_UNAVAILABLE: ' + type(exc).__name__ + ': ' + str(exc)
print('${MARKER}' + json.dumps(result, separators=(',', ':')))
VIBEIC_COCOTB_JUNIT_PY`;
}

export function cocotbJunitResult(nativeResult, resultsFile) {
  const empty = { junit_valid: false, tests_passed: null, tests_failed: null,
    tests_errors: null, tests_skipped: null, tests_total: null,
    error: "COCOTB_JUNIT_UNAVAILABLE: current result was not read" };
  let summary = empty;
  const line = nativeResult.output.split("\n").filter(s => s.startsWith(MARKER)).at(-1);
  try {
    const value = JSON.parse(line?.slice(MARKER.length));
    const counts = ["tests_passed", "tests_failed", "tests_errors", "tests_skipped", "tests_total"];
    if (value.results_file === resultsFile && value.junit_valid === false) {
      summary = { ...empty, error: value.error || empty.error };
    } else if (value.results_file === resultsFile && value.junit_valid === true
        && counts.every(key => Number.isSafeInteger(value[key]) && value[key] >= 0)
        && value.tests_errors <= value.tests_failed
        && value.tests_passed + value.tests_failed + value.tests_skipped === value.tests_total) {
      summary = value;
    }
  } catch { /* No valid current native XML summary: never fall back to stdout. */ }
  const success = nativeResult.success && summary.junit_valid && summary.tests_failed === 0
    && summary.tests_passed > 0;
  return { ...summary, success,
    status: success ? "PASS" : (!nativeResult.success || summary.tests_failed > 0) ? "FAIL" : "INCONCLUSIVE",
    native_exit_code: nativeResult.exitCode ?? (nativeResult.success ? 0 : null),
    ...(!nativeResult.success ? { error: nativeResult.error || "native cocotb invocation failed" } : {}),
  };
}
