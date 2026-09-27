"""A test file must leave the program modules as it found them.

MEASURED (serial, one session, command-line order):

    test_arith_declaration_framing_persist.py
    test_authored_l10_oracle_survives_every_emitter.py   -> 1 failed

and 16 passed in the reverse order. The first file's `_load` executed a FRESH
copy of `arith_oracle_tb_gen`, `design_one_shot_runner` and
`arith_declaration_emit` and left each one in `sys.modules`. The second file
had bound `arith_oracle_tb_gen` at collection and monkeypatched THAT object,
while `testbench_gen._emit_case_golden_oracle` imports the module by name at
call time and got the fresh, unpatched copy. Under xdist the order is a
scheduling accident, so the red came and went.

The same order-reproduction over every test file that touches `sys.modules`
found 101 files that leave a program's entry replaced or removed (at
collection, or inside a test) and more that re-execute a program in place with
`importlib.reload`. The first shape is restored centrally
(`_sys_modules_isolation`, wired in conftest); the second by the files that
reload (`module_namespaces_restored`).

Each case runs pytest in a subprocess: a probe collected FIRST snapshots the
program entries and the named modules' attribute bindings, the leaking file
runs, and a probe run LAST compares. The pre-fix tree answers every case (no
new interface is called), so a red on it is the leak, not a refusal.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_PLUGIN = _TESTS.parents[1]

_PROBE_FIRST = '''\
import sys, types, importlib
sys.path.insert(0, {programs!r})
for _n in {names!r}:
    importlib.import_module(_n)
_st = types.ModuleType("_order_probe_state")
_st.mods = dict(sys.modules)
_st.attrs = {{n: {{k: id(v) for k, v in vars(sys.modules[n]).items()}}
             for n in {names!r}}}
sys.modules["_order_probe_state"] = _st

def test_probe_first():
    pass
'''

_PROBE_LAST = '''\
import os, sys
_PROGRAMS = os.path.realpath({programs!r})
_TESTS = os.path.join(_PROGRAMS, "tests")

def _is_program(m):
    f = os.path.realpath(getattr(m, "__file__", None) or "")
    return f.startswith(_PROGRAMS + os.sep) and not f.startswith(_TESTS + os.sep)

def test_the_program_modules_are_as_they_were():
    st = sys.modules["_order_probe_state"]
    moved = sorted(n for n, m in st.mods.items()
                   if _is_program(m) and sys.modules.get(n) is not m)
    rebound = sorted("%s.%s" % (n, k) for n, ids in st.attrs.items()
                     for k, i in ids.items()
                     if not k.startswith("__")
                     and id(vars(sys.modules[n]).get(k)) != i)
    assert (moved, rebound) == ([], []), (
        "REPLACED/REMOVED in sys.modules: %s; REBOUND: %s" % (moved, rebound[:8]))
'''

# (leaking file, program modules it disturbs) -- one of each measured shape.
CASES = [
    # the file behind the measured red: fresh copies left registered in tests
    ("test_arith_declaration_framing_persist.py",
     ["arith_oracle_tb_gen", "design_one_shot_runner",
      "arith_declaration_emit"]),
    # a module-level load: the entry is replaced while the file is COLLECTED
    ("test_issue603_gate_l20_applicability.py", ["dft_atpg_coverage_check"]),
    # a load inside a test
    ("test_agentic_jsonl_input_layout.py", ["agentic_jsonl_to_shape_d"]),
    # an entry DELETED to force a re-import, and never put back
    ("test_flow_dashboard_web.py", ["flow_dashboard_data"]),
    # `importlib.reload`: same object, every attribute rebound
    ("test_phase2_issue13_null_crc_attribute_error.py", ["aid_class_rtl_gen"]),
]


def _pytest(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         *[str(p) for p in paths]],
        cwd=str(_PLUGIN), capture_output=True, text=True, timeout=900)


def _passed(out: str, suffix: str) -> bool:
    return any(line.startswith("PASSED ") and line.endswith(suffix)
               for line in out.splitlines())


@pytest.mark.parametrize("leaker,names", CASES, ids=[c[0] for c in CASES])
def test_a_file_leaves_the_program_modules_as_it_found_them(tmp_path, leaker,
                                                            names):
    first = tmp_path / "test_aaa_order_probe_first.py"
    last = tmp_path / "test_zzz_order_probe_last.py"
    first.write_text(_PROBE_FIRST.format(programs=str(_TESTS.parent),
                                         names=names))
    last.write_text(_PROBE_LAST.format(programs=str(_TESTS.parent)))
    cp = _pytest(first, _TESTS / leaker, last)
    out = cp.stdout + cp.stderr
    # The last probe must have RUN: a session that never reached it is not
    # evidence that nothing leaked.
    assert _passed(out, "::test_the_program_modules_are_as_they_were"), \
        out[-3000:]


def test_the_victim_passes_after_the_leaking_file():
    cp = _pytest(_TESTS / "test_arith_declaration_framing_persist.py",
                 _TESTS / "test_authored_l10_oracle_survives_every_emitter.py")
    out = cp.stdout + cp.stderr
    assert _passed(out, "test_authored_l10_oracle_survives_every_emitter.py::"
                        "test_emitter_writes_then_refreshes_its_own_output"
                        "[_emit_case_golden_oracle]"), out[-3000:]
    assert cp.returncode == 0, out[-3000:]
