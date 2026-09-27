"""A test file that loads a program by path must leave `sys.modules` as it
found it.

MEASURED (serial, one session, command-line order):

    test_arith_declaration_framing_persist.py
    test_authored_l10_oracle_survives_every_emitter.py   -> 1 failed

and 16 passed in the reverse order. The first file's `_load` executed a FRESH
copy of `arith_oracle_tb_gen`, `design_one_shot_runner` and
`arith_declaration_emit` and left each one in `sys.modules`. The second file
had bound `arith_oracle_tb_gen` at collection and monkeypatched THAT object,
while `testbench_gen._emit_case_golden_oracle` imports the module by name at
call time and got the fresh, unpatched copy. The real oracle generator then
returned None and the test failed for a reason in another file. Under xdist the
order is a scheduling accident, so the red came and went.

Both checks run pytest in a subprocess in the leaking order, so each observes
the whole session, and the pre-fix tree answers them (it is not refused).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_PLUGIN = _TESTS.parents[1]
_LEAKER = _TESTS / "test_arith_declaration_framing_persist.py"
_VICTIM = _TESTS / "test_authored_l10_oracle_survives_every_emitter.py"
_NAMES = ("arith_oracle_tb_gen", "design_one_shot_runner",
          "arith_declaration_emit")

# Imported at COLLECTION, i.e. before the leaking file's tests run, exactly as
# a sibling test file binds these modules; its one test runs after them.
_PROBE = '''\
import sys
sys.path.insert(0, {programs!r})
import {imports}
BEFORE = {{n: sys.modules[n] for n in {names!r}}}

def test_the_same_module_objects_are_still_registered():
    replaced = sorted(n for n, m in BEFORE.items() if sys.modules.get(n) is not m)
    assert replaced == [], "REPLACED in sys.modules: %s" % replaced
'''


def _pytest(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         *[str(p) for p in paths]],
        cwd=str(_PLUGIN), capture_output=True, text=True, timeout=900)


def test_the_leaking_file_restores_the_modules_it_loads(tmp_path):
    probe = tmp_path / "test_zz_sys_modules_probe.py"
    probe.write_text(_PROBE.format(programs=str(_TESTS.parent),
                                   imports=", ".join(_NAMES), names=_NAMES))
    cp = _pytest(_LEAKER, probe)
    out = cp.stdout + cp.stderr
    # The probe must have RUN and passed: a session that never reached it is
    # not evidence that nothing leaked.
    assert any(
        line.startswith("PASSED ") and line.endswith(
            "::test_the_same_module_objects_are_still_registered")
        for line in out.splitlines()), out[-3000:]
    assert cp.returncode == 0, out[-3000:]


def test_the_victim_passes_after_the_leaking_file():
    cp = _pytest(_LEAKER, _VICTIM)
    out = cp.stdout + cp.stderr
    victim_id = ("test_authored_l10_oracle_survives_every_emitter.py::"
                 "test_emitter_writes_then_refreshes_its_own_output"
                 "[_emit_case_golden_oracle]")
    assert "PASSED " in out and any(
        line.startswith("PASSED ") and line.endswith(victim_id)
        for line in out.splitlines()), out[-3000:]
    assert cp.returncode == 0, out[-3000:]
