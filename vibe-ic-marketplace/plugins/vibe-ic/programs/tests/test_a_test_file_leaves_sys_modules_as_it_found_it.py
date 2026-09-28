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

import os
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
    # a file whose OWN view replaces the entry, and whose test also
    # monkeypatches it: the own view installed outside that monkeypatch was
    # undone first, and monkeypatch's undo then wrote the file's copy back
    # (review of the per-module own view)
    ("test_issue559_drift_check_rule_b_blindspot.py", ["flow_compliance_check"]),
]


#: The suite's own conftest, loaded as a plugin under a name of its own, for a
#: synthetic test file that lives outside programs/tests (pytest applies a
#: conftest only below its directory, and `-p conftest` would resolve to a
#: different file of that name).
_CONFTEST_AS_PLUGIN = '''\
import importlib.util, sys
_spec = importlib.util.spec_from_file_location(
    "vibeic_programs_tests_conftest", {conftest!r})
_mod = importlib.util.module_from_spec(_spec)
sys.modules["vibeic_programs_tests_conftest"] = _mod
_spec.loader.exec_module(_mod)
globals().update({{k: v for k, v in vars(_mod).items() if not k.startswith("__")}})
'''


def _pytest(*paths: Path, plugin_dir: Path = None) -> subprocess.CompletedProcess:
    extra = []
    env = dict(os.environ)
    if plugin_dir is not None:
        (plugin_dir / "tests_conftest_as_plugin.py").write_text(
            _CONFTEST_AS_PLUGIN.format(conftest=str(_TESTS / "conftest.py")))
        extra = ["-p", "tests_conftest_as_plugin"]
        env["PYTHONPATH"] = os.pathsep.join(
            [str(plugin_dir)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         *extra, *[str(p) for p in paths]],
        cwd=str(_PLUGIN), capture_output=True, text=True, timeout=900, env=env)


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


def test_a_file_keeps_its_own_registrations_for_its_own_tests(tmp_path):
    """The restore must not reach INTO the registering file.

    `test_issue626_m1_pairs_the_designs_own_def` loads its own
    `def_gds_port_power_restore` at collection, and one of its tests loads a
    program that imports that name and must get the same object. Restoring an
    earlier importer's copy for that file's own tests failed it -- only when
    something collected earlier had imported the module (xdist; every worker
    collects the whole suite), never with the file alone."""
    first = tmp_path / "test_aaa_imports_it_first.py"
    first.write_text(
        "import sys\nsys.path.insert(0, %r)\n"
        "import def_gds_port_power_restore  # noqa: F401\n\n"
        "def test_imported():\n    pass\n" % str(_TESTS.parent))
    cp = _pytest(first, _TESTS / "test_issue626_m1_pairs_the_designs_own_def.py")
    out = cp.stdout + cp.stderr
    assert _passed(out, "test_issue626_m1_pairs_the_designs_own_def.py::"
                        "test_the_label_check_and_the_merge_share_ONE_rule"), \
        out[-3000:]
    assert cp.returncode == 0, out[-3000:]


def test_a_removed_at_collection_entry_is_restored_through_monkeypatch(tmp_path):
    """An own-view entry that is a REMOVAL (None), plus a test that registers
    a fresh copy through monkeypatch, must still leave the original behind.

    The own view is applied per test; if it is applied outside the test's
    `monkeypatch`, fixture teardown (LIFO) restores the global entry first and
    monkeypatch's undo then writes the test's copy over it."""
    name = "def_gds_port_power_restore"
    first = tmp_path / "test_aaa_order_probe_first.py"
    last = tmp_path / "test_zzz_order_probe_last.py"
    first.write_text(_PROBE_FIRST.format(programs=str(_TESTS.parent),
                                         names=[name]))
    last.write_text(_PROBE_LAST.format(programs=str(_TESTS.parent)))
    remover = tmp_path / "test_mmm_removes_at_collection.py"
    remover.write_text(
        "import importlib, sys\n"
        "sys.modules.pop(%r, None)          # removed while COLLECTED\n\n"
        "def test_runs_against_its_own_view(monkeypatch):\n"
        "    assert %r not in sys.modules\n"
        "    fresh = importlib.import_module(%r)\n"
        "    monkeypatch.setitem(sys.modules, %r, fresh)\n"
        % (name, name, name, name))
    # The remover lives outside programs/tests, so the suite's own conftest
    # is loaded as a plugin: the file then runs under the SAME fixtures a real
    # test file does (the isolation hooks, and the autouse fixture that sets
    # `monkeypatch` up before them -- the order the defect needs).
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    cp = _pytest(first, remover, last, plugin_dir=plugin_dir)
    out = cp.stdout + cp.stderr
    assert _passed(out, "::test_runs_against_its_own_view"), out[-3000:]
    assert _passed(out, "::test_the_program_modules_are_as_they_were"), \
        out[-3000:]
