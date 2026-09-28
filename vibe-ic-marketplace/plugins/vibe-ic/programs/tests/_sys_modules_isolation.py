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
  * a test module's own tests still see what that module registered while it
    was collected (installed for each of its tests, removed after), and a
    test's own registrations stay for the duration of that test;
  * entries a test ADDS are left alone -- that is an ordinary first import,
    and every later importer shares it;
  * non-program modules (stdlib, third-party, stubs of those) are not ours to
    police here.
"""
from __future__ import annotations

import contextlib
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


#: test-module path -> the program entries that module registered (or removed:
#: None) while it was collected. Its OWN tests run against that view.
_OWN_VIEW: Dict[str, Dict[str, object]] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """Record what collecting this test module did to program entries, then
    undo it for everybody else.

    Undoing it for the module's OWN tests too was a regression, measured:
    `test_issue626_m1_pairs_the_designs_own_def` loads its own
    `def_gds_port_power_restore` at collection and, in a test, loads a program
    that imports that name and must get the SAME object. With another file's
    copy put back, it got that one and failed -- but only when an earlier file
    had imported the module, i.e. under xdist, never with the file alone."""
    if not isinstance(collector, pytest.Module):
        yield
        return
    before = dict(sys.modules)
    yield
    own: Dict[str, object] = {}
    for name, mod in list(sys.modules.items()):
        if before.get(name) is not mod and _is_program(mod):
            own[name] = mod
    for name, mod in before.items():
        if name not in sys.modules and _is_program(mod):
            own[name] = None
    if own:
        _OWN_VIEW[str(collector.path)] = own
    restore_program_modules(before)


@pytest.fixture(autouse=True)
def _program_modules_are_restored_after_each_test(request, monkeypatch):
    """Run each test against its own module's collection-time view, and
    leave the global view as it found it.

    The own view goes in through the test's OWN `monkeypatch`, never by
    writing `sys.modules` directly. Fixture teardown is LIFO and `monkeypatch`
    is set up before this fixture, so its undo runs AFTER this restore. A test
    that `monkeypatch.setitem`s an entry the own view had already replaced
    records the own-view copy as "the old value"; with a direct install, the
    restore below put the global copy back and monkeypatch then wrote the
    file's copy over it, for every later test in the worker (review of the
    per-module own view: `test_issue559_drift_check_rule_b_blindspot` left its
    `flow_compliance_check` behind). Through the same instance the undo stack
    unwinds past the test's own entry to the own view's, and ends on the
    value this fixture found."""
    before = dict(sys.modules)
    for name, mod in _OWN_VIEW.get(str(request.node.path), {}).items():
        if mod is None:
            monkeypatch.delitem(sys.modules, name, raising=False)
        else:
            monkeypatch.setitem(sys.modules, name, mod)
    yield
    restore_program_modules(before)


@contextlib.contextmanager
def module_namespaces_restored(*modules):
    """Undo `importlib.reload` (or any rebinding) of `modules` on exit.

    A reload re-executes a module IN PLACE: the object in `sys.modules` stays
    the same, and every attribute on it -- functions, classes, constants --
    is replaced. A file that bound those attributes earlier then holds the old
    class while the module hands out the new one. The entry-level restore above
    cannot see that, so a test that reloads puts the namespace back itself."""
    saved = [(m, dict(vars(m))) for m in modules]
    try:
        yield
    finally:
        for mod, ns in saved:
            mod.__dict__.clear()
            mod.__dict__.update(ns)
