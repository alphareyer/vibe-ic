"""Regression controls for vibeic/vibe-ic#3447.

Step 11 must have one supervisor for ``fault chain``.  The leaf producer owns
the container-aware watchdog; the orchestration layer only waits for that
producer to return.  A second process-tree watchdog cannot see a Docker engine
after containerd reparents it and can therefore report a false stall.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as D  # noqa: E402
import fault_scan_chain_insert as S  # noqa: E402


def _calls(fn, attr: str):
    tree = ast.parse(inspect.getsource(fn))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def test_fault_chain_leaf_owns_container_aware_supervision():
    calls = [
        call for call in _calls(S.run_chain, "_run_docker")
        if len(call.args) >= 2
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "cmd"
    ]
    assert len(calls) == 1
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    supervised = keywords.get("supervised")
    assert isinstance(supervised, ast.Constant) and supervised.value is True
    assert "ceiling_s" in keywords, "the former runtime number must stay recorded"
    assert "timeout" not in keywords, (
        "a supervised engine has no terminating wall-clock timeout")


def test_step11_waits_for_leaf_instead_of_starting_a_second_watchdog():
    source = inspect.getsource(D._step_dft_insertion)
    tree = ast.parse(source)
    scan_launches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == "_scan_cmd":
            owner = node.func.value.id if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            ) else None
            scan_launches.append((owner, getattr(node.func, "attr", None)))
    assert scan_launches == [("subprocess", "run")]


