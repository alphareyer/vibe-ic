"""The d7 artifact graph may not keep program ASTs alive past building.

MEASURED (S39_EXIT_STALL, lane fxspm1): a `test_matrix_d3_outputs_produced.py`
session ended holding 7.25 M GC-tracked objects (1.57 GB), almost all `ast`
nodes, because `matrix_d7_artifact_graph._trees()` and `_tree`'s unbounded
memo kept every program's parsed AST (1 502 files) for the whole process.
Each full collection walked them (~3 s on a quiet host), and pytest's
post-summary collection plus interpreter finalization made the process
linger ~17 s after its own summary — past the nested driver's 60 s stall
window at 5x load, where the census reported a hung outcome run.

These guards are STRUCTURAL: they count what the module's caches can reach,
not how long anything took.
"""
from __future__ import annotations

import ast
import gc
import types

import flow_matrix.flowref as F
import matrix_d7_artifact_graph as G


def _module_caches():
    return [(name, obj) for name, obj in vars(G).items()
            if callable(obj) and hasattr(obj, "cache_info")
            and getattr(obj, "__module__", None) == G.__name__]


def _asts_reachable_from_caches(depth: int = 6) -> dict:
    """``{cache name: count of ast.AST objects reachable from its entries}``.

    Walks the referents of each lru_cache wrapper, but never into a function,
    a module, a type or a module namespace — those reach everything and are
    not what the cache retains."""
    found = {}
    for name, wrapper in _module_caches():
        seen, frontier, count = set(), [wrapper], 0
        for _ in range(depth):
            nxt = []
            for obj in frontier:
                for ref in gc.get_referents(obj):
                    if id(ref) in seen:
                        continue
                    seen.add(id(ref))
                    if isinstance(ref, ast.AST):
                        count += 1
                        continue
                    if isinstance(ref, (types.FunctionType, types.ModuleType, type,
                                        types.BuiltinFunctionType)):
                        continue
                    if isinstance(ref, dict) and "__builtins__" in ref:
                        continue
                    nxt.append(ref)
            frontier = nxt
        if count:
            found[name] = count
    return found


def _tmp_tree(tmp_path, monkeypatch, n):
    for number in range(n):
        (tmp_path / f"program_{number}.py").write_text(
            "import argparse\nfrom pathlib import Path\n"
            f"Path('reports/r{number}.json').write_text('ok')\n")
    monkeypatch.setattr(F, "PROGRAMS_DIR", tmp_path)
    for _name, fn in _module_caches():
        fn.cache_clear()


def test_building_the_whole_tree_indices_retains_no_ast(tmp_path, monkeypatch):
    _tmp_tree(tmp_path, monkeypatch, 40)
    try:
        assert G.write_index() and G.literal_index()
        assert sum(len(v) for v in G.write_index().values()) == 40
        leaked = _asts_reachable_from_caches()
        assert not leaked, (
            f"after building write_index/literal_index the module's caches "
            f"still reach program ASTs: {leaked}. Keep the derived facts, not "
            f"the trees — a whole-tree AST store is 7.25 M objects on the real "
            f"tree and it made the outcome run linger past its stall window.")
    finally:
        for _name, fn in _module_caches():
            fn.cache_clear()


def test_the_single_program_memo_is_bounded(tmp_path, monkeypatch):
    n = G._TREE_MEMO + 20 if hasattr(G, "_TREE_MEMO") else 200
    _tmp_tree(tmp_path, monkeypatch, n)
    try:
        for number in range(n):
            assert G._tree(f"program_{number}") is not None
            G.program_literals(f"program_{number}")
            G._local_modules(f"program_{number}")
        info = G._tree.cache_info()
        assert info.maxsize is not None and info.currsize <= info.maxsize < n, (
            f"`_tree` holds {info.currsize} parsed programs (maxsize "
            f"{info.maxsize}) after {n} single lookups; an unbounded memo is "
            f"the whole-tree AST store again, one lookup at a time.")
        # the derived single-program answers are kept, the trees are not
        assert G.program_literals.cache_info().currsize == n
    finally:
        for _name, fn in _module_caches():
            fn.cache_clear()
