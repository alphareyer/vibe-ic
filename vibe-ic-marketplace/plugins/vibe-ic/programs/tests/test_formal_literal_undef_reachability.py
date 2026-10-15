"""Exercise the owning raw-undef scan on captured material and graph controls.

The scan is private to reset_control_mapping. Compile that exact nested body
with its two netlist bindings, so the same tests run on the old and new source
without launching unrelated native transition queries. These are source-only
controls, not evidence that the surrounding formal gate passed.
"""
import ast
import json
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import formal_structural_check as fsc


def _scan(drivers, q=()):
    source = PROGRAMS / "formal_structural_check.py"
    tree = ast.parse(source.read_text())
    outer = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "reset_control_mapping")
    body = next(n for n in ast.walk(outer)
                if isinstance(n, ast.FunctionDef) and n.name == "literal_undef")
    namespace = {"raw_drivers": drivers, "raw_q": set(q)}
    module = ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["literal_undef"]


def _driver(*inputs, ignored=()):
    return {"connections": {"A": list(inputs), "Y": list(ignored)},
            "port_directions": {"A": "input", "Y": "output"}}


@contextmanager
def _bounded():
    def expired(*_):
        raise AssertionError("raw undef reachability exceeded two seconds")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_actual_raw_arithmetic_material_completes_without_undefined_literals():
    path = Path(__file__).parent / "fixtures/formal_raw_undef/arithmetic_raw.json"
    modules = json.loads(path.read_text())["modules"]
    assert len(modules) == 1
    mod = next(iter(modules.values()))
    cells = fsc._cells(mod)
    q = {b for _, cell, kind in cells if kind == "ff" for b in fsc._bits(cell, "Q")}
    scan = _scan(fsc._drivers(mod), q)
    queried = [b for _, cell, kind in cells if kind == "ff"
               for pin, bits in cell["connections"].items() if pin != "Q" for b in bits]
    assert len(queried) > 32
    with _bounded():
        assert all(scan(bit) is False for bit in queried)


def test_neutral_reconvergent_graph_is_not_expanded_by_path():
    drivers = {i: _driver(i + 1, i + 1) for i in range(28)}
    drivers[28] = _driver("0", "1")
    with _bounded():
        assert _scan(drivers)(0) is False


def test_unchanged_simple_known_value_control():
    scan = _scan({1: _driver(2), 2: _driver("0", "1")})
    assert scan(1) is False
    assert scan(999) is False
    assert scan("1x") is False
    assert scan("X") is True


@pytest.mark.parametrize("literal", ["x", "X", "z", "Z"])
def test_unknown_literals_remain_detected(literal):
    assert _scan({1: _driver(2), 2: _driver(literal)})(1) is True


def test_current_q_is_a_boundary_even_when_its_driver_contains_x():
    assert _scan({1: _driver(2), 2: _driver("x")}, q=[2])(1) is False


def test_cycles_and_repeated_queries_preserve_reachable_unknown():
    scan = _scan({1: _driver(2), 2: _driver(1),
                  3: _driver(4), 4: _driver(3, "x")})
    assert scan(1) is False
    assert scan(3) is True
    assert scan(4) is True
    assert scan(2) is False
    reordered = _scan({1: _driver(2), 2: _driver("x", 1)})
    assert reordered(1) is True
    assert reordered(2) is True


def test_reconvergent_unknown_is_not_pruned_as_already_visited():
    scan = _scan({1: _driver(2, 3), 2: _driver(4),
                  3: _driver(4, "Z"), 4: _driver("0")})
    assert scan(1) is True


def test_output_connections_are_not_fanin():
    assert _scan({1: _driver("0", ignored=["x"])})(1) is False
