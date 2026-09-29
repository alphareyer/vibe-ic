"""Executable-call placement checks for the Phase-3 main dispatch path."""
import ast
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "phase3_one_shot_runner.py"


def _main():
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "main")
    parents = {child: parent for parent in ast.walk(main)
               for child in ast.iter_child_nodes(parent)}
    return main, parents


def _calls(main, name):
    return [n for n in ast.walk(main) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == name]


def guarded_producer_line(name, row):
    """Require one live main-path producer in the layout-refusal ternary.

    The executable call must be the append's success branch; its other branch
    must publish the corresponding NOT_MEASURED row. This checks both branches
    without depending on line wrapping or a particular indentation layout.
    """
    main, parents = _main()
    calls = _calls(main, name)
    assert len(calls) == 1, f"main() dispatches {name} {len(calls)} times"
    call = calls[0]
    expected = {
        "step_digital_hardmacro_gen": "step_digital_hardmacro_gen(project, pdk, args.container)",
        "step_ic_release_docs_gen": "step_ic_release_docs_gen(project)",
        "step_ip_release_docs_gen": "step_ip_release_docs_gen(project, args.ic_name or args.top_name, pdk.name)",
        "step_tapeout_docs_gen": "step_tapeout_docs_gen(project)",
        "step_signoff_metrics_aggregate": "step_signoff_metrics_aggregate(project)",
    }[name]
    assert ast.unparse(call) == expected, f"{name} arguments changed"
    gate = parents.get(call)
    assert isinstance(gate, ast.IfExp) and gate.orelse is call
    assert isinstance(gate.test, ast.Name) and gate.test.id == "_layout_refusal"
    refusal = gate.body
    assert isinstance(refusal, ast.Call)
    assert isinstance(refusal.func, ast.Name)
    assert refusal.func.id == "_upstream_signoff_not_measured"
    assert isinstance(refusal.args[0], ast.Constant) and refusal.args[0].value == row
    append = parents.get(gate)
    assert isinstance(append, ast.Call) and isinstance(append.func, ast.Attribute)
    assert isinstance(append.func.value, ast.Name)
    assert (append.func.value.id, append.func.attr) == ("plan", "append")
    return call.lineno


def gds_chain_dispatch_line():
    """Require the real stream-out callable inside preflight and _chain_ok."""
    main, parents = _main()
    calls = _calls(main, "step_gds")
    assert len(calls) == 0  # step_gds is passed as a callable, never called here
    names = [n for n in ast.walk(main) if isinstance(n, ast.Name)
             and n.id == "step_gds" and isinstance(n.ctx, ast.Load)]
    dispatches = []
    for name in names:
        chain = []
        node = name
        while node in parents:
            node = parents[node]
            chain.append(node)
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_recorded" for n in chain) and any(
                   isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "gate" for n in chain):
            dispatches.append((name, chain))
    assert len(dispatches) == 1, f"main() preflight GDS dispatches: {len(dispatches)}"
    name, chain = dispatches[0]
    assert any(isinstance(n, ast.If) and isinstance(n.test, ast.Name)
               and n.test.id == "_chain_ok" for n in chain)
    gates = [n for n in chain if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "gate"]
    assert len(gates) == 1
    gate = gates[0]
    assert [ast.unparse(a) for a in gate.args[-4:]] == [
        "project", "effective_top", "pdk", "args.container"]
    chain_calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name)
                   and n.func.id == "_pnr_chain_continues"]
    assert len(chain_calls) == 1
    assert [ast.unparse(arg) for arg in chain_calls[0].args] == [
        "_pnr_row"]
    return name.lineno
