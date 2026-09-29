"""Neutral source controls for canonical step -> dispatch -> producer binding."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import flow_step_executor_coverage_check as ec
import step_preflight as sp


MODULE = "phase3_one_shot_runner"
ENTRY = MODULE + ".step_synth"
DISPATCHER = '''
def dispatch(steps, fn, *args, **kwargs):
    return fn(*args, **kwargs)
def gate(project, runner, site, refusal_factory, fn, *args, **kwargs):
    return fn(*args, **kwargs)
'''


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "_HERE", tmp_path)
    monkeypatch.setattr(ec, "_RUNNER_FILES", [MODULE + ".py"])
    flow = tmp_path / "flow.json"
    flow.write_text(json.dumps({"flow": {"steps": [
        {"id": 9, "name": "synthesis", "required_outputs": ["synth/netlist.v"],
         "mcp_tools": [ENTRY]},
        {"id": 12, "name": "post DFT", "required_outputs": ["dft/post_dft_netlist.v"]},
        {"id": 18, "name": "spares", "required_outputs": ["pnr/spare_cells.json"],
         "mcp_tools": ["librelane:Vibeic.InsertSpareCells"]},
        {"id": 21, "name": "routing", "required_outputs": ["pnr/routed.def"],
         "mcp_tools": ["librelane:OpenROAD.DetailedRouting"]},
    ]}}))
    monkeypatch.setattr(ec, "_DEFAULT_FLOW", flow)
    (tmp_path / "step_preflight.py").write_text(DISPATCHER)
    ec._AST_CACHE.clear()

    def write(text):
        (tmp_path / (MODULE + ".py")).write_text(text)
        return ec.verify_executor(ENTRY, "9")

    return write


def test_real_synthesis_dispatch_does_not_authorize_step12():
    ok, why = ec.verify_executor(ENTRY, "12")
    assert not ok, why


@pytest.mark.parametrize("body", [
    "step_synth()",  # a call without canonical step identity
    "accept(step_synth)",  # arbitrary callable argument
    "lambda: sp.dispatch('9', step_synth)",  # uninvoked lambda
    "unused() if False else None",  # literal dead call
    "return\n    sp.dispatch('9', step_synth)",
    "if False:\n        sp.dispatch('9', step_synth)",
])
def test_unbound_or_dead_calls_cannot_earn_wiring(source, body):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def accept(fn): return fn\n"
                     "def unused(): sp.dispatch('9', step_synth)\n"
                     "def main():\n    " + body + "\n")
    assert not ok, why


def test_unused_nested_dispatch_cannot_earn_wiring(source):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def main():\n"
                     "    def unused(): sp.dispatch('9', step_synth)\n"
                     "    return None\n")
    assert not ok, why


def test_native_preflight_dispatch_binds_its_span(source):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def main():\n"
                     "    return sp.gate(None, 'phase3_one_shot_runner', 'synth', None, step_synth)\n")
    assert ok, why
    assert not ec.verify_executor(ENTRY, "12")[0]


def test_unpacking_in_native_producer_is_parseable(source):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): return {**{}, 'result': None}\n"
                     "def main(): sp.dispatch('9', step_synth)\n")
    assert ok, why


def test_recorded_callback_and_invoked_thunk_bind_the_producer(source):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def recorded(kind, fn):\n"
                     "    def wrapped(*args, **kwargs): return fn(*args, **kwargs)\n"
                     "    return wrapped\n"
                     "def run(thunk): return thunk()\n"
                     "def main():\n"
                     "    run(lambda: sp.gate(None, 'phase3_one_shot_runner', 'synth', None, recorded('synth', step_synth)))\n")
    assert ok, why


def test_noncalling_callback_wrapper_does_not_bind(source):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def recorded(kind, fn):\n"
                     "    def unused(*args, **kwargs): return fn(*args, **kwargs)\n"
                     "    return lambda: None\n"
                     "def main():\n"
                     "    sp.gate(None, 'phase3_one_shot_runner', 'synth', None, recorded('synth', step_synth))\n")
    assert not ok, why


@pytest.mark.parametrize("shadow", [
    "sp = object()\n    sp.dispatch('9', step_synth)",
    "step_synth = lambda: None\n    sp.dispatch('9', step_synth)",
])
def test_shadowed_import_or_producer_does_not_bind(source, shadow):
    ok, why = source("import step_preflight as sp\n"
                     "def step_synth(): pass\n"
                     "def main():\n    " + shadow + "\n")
    assert not ok, why


def test_output_obligation_is_bound_to_the_canonical_step(source):
    assert source("import step_preflight as sp\n"
                  "def step_synth(): pass\n"
                  "def main(): sp.dispatch('9', step_synth)\n")[0]
    doc = {"flow": {"steps": [{"id": 9, "name": "unrelated obligation",
                                "required_outputs": ["other/missing.proof"],
                                "mcp_tools": [ENTRY]}]}}
    row = ec.classify(doc, "")[0]
    assert row["classification"] == "ORPHANED", row
    assert row["verified_executors"] == []


def test_dispatcher_that_stops_calling_producer_is_refused(source, tmp_path):
    text = ("import step_preflight as sp\ndef step_synth(): pass\n"
            "def main(): sp.dispatch('9', step_synth)\n")
    assert source(text)[0]
    (tmp_path / "step_preflight.py").write_text(
        "def dispatch(steps, fn, *args, **kwargs): return None\n")
    # Same source path, changed helper bytes: the parse cache must not hide it.
    assert not source(text)[0]


def test_preflight_helper_must_forward_producer_and_arguments(source, tmp_path):
    (tmp_path / "step_preflight.py").write_text(
        "def forward(fn, args, kwargs): return fn(*args, **kwargs)\n"
        "def gate(project, runner, site, refusal_factory, fn, *args, **kwargs):\n"
        "    return forward(fn, args, kwargs)\n")
    text = ("import step_preflight as sp\ndef step_synth(): pass\n"
            "def main(): sp.gate(None, 'phase3_one_shot_runner', 'synth', None, step_synth)\n")
    assert source(text)[0]
    (tmp_path / "step_preflight.py").write_text(
        "def forward(fn, args, kwargs): return None\n"
        "def gate(project, runner, site, refusal_factory, fn, *args, **kwargs):\n"
        "    return forward(fn, args, kwargs)\n")
    assert not source(text)[0]


@pytest.mark.parametrize("constructor,expected", [
    ("ThreadPoolExecutor", True), ("object", False),
])
def test_only_real_pool_submission_invokes_gate(source, constructor, expected):
    text = ("import step_preflight as sp\n"
            "from concurrent.futures import ThreadPoolExecutor\n"
            "def step_synth(): pass\n"
            "def main():\n"
            f"    with {constructor}() as pool:\n"
            "        pool.submit(sp.gate, None, 'phase3_one_shot_runner', 'synth', None, step_synth)\n")
    assert source(text)[0] is expected


@pytest.mark.parametrize("body", [
    "pass",
    "report = {'producer': 'OpenROAD.DetailedRouting'}",
    "def unused(): ll.run_chain(None, None, [('OpenROAD.DetailedRouting', None, None)])",
])
def test_import_mapping_without_live_tool_dispatch_proves_nothing(source, body):
    source("import step_preflight as sp\nimport librelane_contract as ll\n"
           "def step_synth(): pass\n"
           "def step_pnr():\n    " + body + "\n"
           "def main(): sp.gate(None, 'phase3_one_shot_runner', 'pnr', None, step_pnr)\n")
    assert not ec.verify_executor("librelane:OpenROAD.DetailedRouting", "21")[0]


def test_plugin_class_must_flow_to_bound_tool_chain(source):
    source("import step_preflight as sp\nimport librelane_contract as ll\n"
           "def step_synth(): pass\n"
           "def step_pnr():\n"
           "    steps = []\n"
           "    steps.append('Vibeic.InsertSpareCells')\n"
           "    return ll.run_chain(None, None, [(s, None, None) for s in steps])\n"
           "def main(): sp.gate(None, 'phase3_one_shot_runner', 'pnr', None, step_pnr)\n")
    assert ec.verify_executor("librelane:Vibeic.InsertSpareCells", "18")[0]
    assert not ec.verify_executor("librelane:Vibeic.InsertSpareCells", "21")[0]


def test_dispatch_preserves_native_result_arguments_and_exception():
    calls = []
    result = object()

    def producer(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    assert sp.dispatch(("23", "29", "30", "34"), producer, "project",
                       prepv=True) is result
    assert calls == [(("project",), {"prepv": True})]
    error = RuntimeError("native refusal")

    def refused():
        raise error

    with pytest.raises(RuntimeError) as caught:
        sp.dispatch("9", refused)
    assert caught.value is error
