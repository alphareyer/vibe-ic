"""No test file may leave a different copy of a program in `sys.modules`.

A test that loads a program BY PATH (`spec_from_file_location` +
`sys.modules[name] = mod`, `del sys.modules[name]` to force a re-import, a
stub put in place) and leaves it there splits that program in two: every file
that imported it earlier holds one module object, and everything that imports
it by name afterwards -- including the programs under test, which import their
siblings at call time -- gets another. A monkeypatch on one copy is then
invisible through the other.

MEASURED: `test_authored_l10_oracle_survives_every_emitter[_emit_case_golden_oracle]`
failed whenever `test_arith_declaration_framing_persist.py` ran before it in
the same session, and passed in the other order. An order-reproduction sweep
over every test file that touches `sys.modules` found 101 files that leave a
program's entry replaced or removed: 69 while being COLLECTED (module-level
loads), 34 inside a test (two do both). Under xdist the order is a scheduling
accident, so each is a red that comes and goes for a reason in another file.

So the restore lives in one place instead of 101. Around the collection of
every test module and around every test, an entry that named a PROGRAM module
(one whose file lives under `programs/`) and now names a different object, or
nothing, is put back. Nothing else is touched:
  * a test's own copy stays bound in the test's globals, and stays registered
    for the duration of the test that registered it;
  * entries a test ADDS are left alone -- that is an ordinary first import,
    and every later importer shares it;
  * non-program modules (stdlib, third-party, stubs of those) are not ours to
    police here.
"""
from __future__ import annotations

import os
import sys
from typing import Dict

import pytest

_TESTS = os.path.realpath(os.path.dirname(__file__))
_PROGRAMS = os.path.dirname(_TESTS)


def _is_program(mod) -> bool:
    path = getattr(mod, "__file__", None)
    if not path:
        return False
    real = os.path.realpath(path)
    return real.startswith(_PROGRAMS + os.sep) and \
        not real.startswith(_TESTS + os.sep)


def restore_program_modules(before: Dict[str, object]) -> list:
    """Put back every program entry of `before` that is now replaced or gone.
    Returns the names restored."""
    restored = []
    for name, mod in before.items():
        if sys.modules.get(name) is mod:
            continue
        if _is_program(mod):
            sys.modules[name] = mod
            restored.append(name)
    return restored


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    if not isinstance(collector, pytest.Module):
        yield
        return
    before = dict(sys.modules)
    yield
    restore_program_modules(before)


@pytest.fixture(autouse=True)
def _program_modules_are_restored_after_each_test():
    before = dict(sys.modules)
    yield
    restore_program_modules(before)
