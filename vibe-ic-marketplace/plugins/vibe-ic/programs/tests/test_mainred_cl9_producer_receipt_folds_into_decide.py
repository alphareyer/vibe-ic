"""MAINRED_CL9 — the Step-32 producer receipt is folded in by `decide()` itself.

MEASURED (live main 06877e752; red since 421707b90, landed in #2806 /
2f486e4bc): 9 cases of `test_closed_loop_executable_coverage.py` failed with
`CLC-EVIDENCE-MISSING: edge 23/32 claims ROLLBACK_PROVEN ... has no live
fallback_guarded_by_trigger proof on _repair_dec.decide's repair_needed=True
branch`. 421707b90 (R-0929-STEP32-RECORD) carried a bound producer receipt's
repair_needed=true forward by storing into the decision dict between the
`decide()` call and its `repair_needed` guard; the prover stops at any store
into the trigger result, so both ROLLBACK_PROVEN edges fell to DECLARED_ONLY.

The rule (unchanged): the receipt can only ADD a repair demand. Where it is
applied moved into `decide(producer_receipt=...)`, so the trigger's own result
carries it and the runner no longer edits the decision.
"""
from __future__ import annotations

import ast
from pathlib import Path

import postroute_timing_repair_decision as D

RUNNER = Path(D.__file__).resolve().parent / "phase3_one_shot_runner.py"


def test_a_bound_true_receipt_adds_the_demand_with_its_reason():
    base = D.decide(None, True)
    assert base["repair_needed"] is False            # the premise
    out = D.decide(None, True, producer_receipt={
        "repair_needed": True, "action": "candidate_adopted"})
    assert out["repair_needed"] is True
    assert "bound Step-32 producer receipt records repair_needed=true" in out["reason"]
    assert "candidate_adopted" in out["reason"]


def test_the_receipt_never_removes_a_demand_and_none_changes_nothing():
    fired = D.decide(None, False, producer_receipt={"repair_needed": False})
    assert fired["repair_needed"] is True
    assert D.decide(None, True, producer_receipt={"repair_needed": False}) \
        == D.decide(None, True) == D.decide(None, True, producer_receipt=None)


def test_the_runner_passes_the_receipt_and_never_stores_into_the_decision():
    """RED on main: the runner stored `repair_needed`/`reason` into the result."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "step_canonicalize_artefacts")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "_repair_dec.decide"]
    assert len(calls) == 1
    assert "producer_receipt" in {k.arg for k in calls[0].keywords}
    stores = [ast.unparse(t) for n in ast.walk(fn)
              if isinstance(n, (ast.Assign, ast.AugAssign))
              for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
              if isinstance(t, ast.Subscript)
              and ast.unparse(t.value) == "_repair_decision"
              and ast.unparse(t.slice) in ("'repair_needed'", "'reason'")]
    assert not stores, stores
