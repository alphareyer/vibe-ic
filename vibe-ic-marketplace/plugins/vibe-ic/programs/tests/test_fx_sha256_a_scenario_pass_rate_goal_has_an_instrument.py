"""A "100% PASS" coverage goal over SCENARIOS is a pass-rate goal, and it has
an instrument: the L10 cases bound to it that ran their own oracle.

MEASURED (sha256 x sky130A, front door, spec-to-rtl author pass): step 4 was
FAIL with every functional vector run and passing, because the design's L7
states four goals as "100% PASS" over a set of scenarios (message lengths,
protocol corners, mode switching, random messages vs a reference). The scope
names no line/toggle/branch/instruction dimension, so the coverage arm had no
number and the goal read NOT_MEASURED -- forever, whatever the author did.

The classification is the landed one (R-0915-113(4): these rows ARE coverage
goals) and is unchanged. What was missing is the instrument:
  * bound cases = the cases the L10 rows link to the goal (`covered_by` on the
    goal, `covers` on a case) and the goal's own scenario oracle (an execution
    row under the goal's own name);
  * PASS iff every bound case ran its own oracle and the pass fraction meets
    the stated percentage (for 100%: all passed);
  * FAIL when they all ran and the fraction misses it (for 100%: any failed);
  * NOT_MEASURED, naming why, when nothing is bound or unrun cases could still
    change whether the stated threshold is met.

Drives the real Step-4 gate helpers over a real execution record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _l10_execution as X                        # noqa: E402
import cpu_functional_oracle_waiver_check as W    # noqa: E402
import l10_coverage_goal_classify as G            # noqa: E402

# sha256's four goals, as Phase 1 emits them from L7 7.1.2 (shape transcribed;
# the corpus is not on main).
GOALS = [
    {"name": "random_message_functional_equivalence_vs_nist_go",
     "kind": "coverage_goal", "expected": "100% PASS (binary)",
     "coverage_scope": ">= 1000 random message lengths (0-2KB) vs a "
                       "reference hash"},
    {"name": "message_length", "kind": "coverage_goal",
     "expected": "100% PASS",
     "coverage_scope": "1 byte / 55 bytes / 56 bytes / 64 bytes / 119 bytes"},
    {"name": "protocol", "kind": "coverage_goal", "expected": "100% PASS",
     "coverage_scope": "INIT during BUSY / NEXT without prior INIT"},
    {"name": "mode_switch", "kind": "coverage_goal", "expected": "100% PASS",
     "coverage_scope": "INIT mode A -> INIT mode B -> INIT mode A"},
]
VEC = {"name": "stated_vector_one", "kind": "functional_vector",
       "stimulus": "0x6162638000...", "expected": "ba7816bf"}


def _project(tmp_path: Path, cases) -> Path:
    p = tmp_path / "proj"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase2" / "stage1" / "sim").mkdir(parents=True)
    (p / "phase1" / "generated_docs" / "L10_TEST_CASES.json").write_text(
        json.dumps({"schema_version": 2, "doc_class": "test_cases",
                    "ic_name": "x", "test_cases": cases}))
    return p


def _record(p: Path, verdicts: dict) -> None:
    """A REAL execution record: {case: PASS|FAIL|NOT_RUN}."""
    rows = []
    for cid, v in verdicts.items():
        if v == "NOT_RUN":
            rows.append({"id": cid, "verdict": X.NOT_EXECUTED,
                         X.SIM_EXECUTED_KEY: False, "detail": "did not run",
                         "tb_file": f"{cid}.v"})
        else:
            rows.append({"id": cid, "verdict": getattr(X, v),
                         X.SIM_EXECUTED_KEY: True, "detail": "fixture",
                         "tb_file": f"{cid}.v"})
    X.write_record(p, p / "phase1" / "generated_docs" / "L10_TEST_CASES.json",
                   rows, producer="test_fx_sha256_scenario_pass_rate")


def _rows(summary) -> dict:
    return {r["case"]: r for r in summary["rows"]}


def test_sha256s_four_goals_are_measured_pass_on_their_own_oracles(tmp_path):
    """RED on main: all four NOT_MEASURED ('names no coverage dimension')."""
    p = _project(tmp_path, [VEC] + GOALS)
    _record(p, {VEC["name"]: "PASS",
                **{g["name"]: "PASS" for g in GOALS}})
    s = W._coverage_goal_summary(p)
    assert (s["passed_count"], s["declared_count"]) == (4, 4), s["rows"]
    for r in s["rows"]:
        assert r["instrument"] == G.SCENARIO_PASS_RATE, r
        assert r["bound_cases"] == [r["case"]], r
    assert W._oracle_execution_refusal(p, "results.xml") is None


def test_a_goal_whose_bound_case_failed_is_FAIL(tmp_path):
    goal = dict(GOALS[1], covered_by=[VEC["name"]])
    p = _project(tmp_path, [VEC, goal])
    _record(p, {VEC["name"]: "FAIL"})
    row = _rows(W._coverage_goal_summary(p))[goal["name"]]
    assert row["verdict"] == G.FAIL, row
    assert VEC["name"] in row["why"]


def test_an_unbound_goal_is_NOT_MEASURED_and_says_nothing_is_bound(tmp_path):
    p = _project(tmp_path, [VEC, GOALS[2]])
    _record(p, {VEC["name"]: "PASS"})
    row = _rows(W._coverage_goal_summary(p))[GOALS[2]["name"]]
    assert row["verdict"] == G.NOT_MEASURED
    assert "no case is bound" in row["why"], row["why"]
    assert GOALS[2]["name"] in row["why"]


def test_a_bound_case_that_did_not_run_is_NOT_MEASURED_and_named(tmp_path):
    goal = dict(GOALS[3], covered_by=[VEC["name"], "second_vector"])
    second = dict(VEC, name="second_vector")
    p = _project(tmp_path, [VEC, second, goal])
    _record(p, {VEC["name"]: "PASS", "second_vector": "NOT_RUN"})
    row = _rows(W._coverage_goal_summary(p))[goal["name"]]
    assert row["verdict"] == G.NOT_MEASURED, row
    assert "second_vector" in row["why"]


@pytest.mark.parametrize("stated,expect", [
    (100, G.FAIL),
    (75, G.FAIL),
    (50, G.NOT_MEASURED),
])
def test_observed_failure_and_unrun_case_use_the_maximum_possible_rate(
        tmp_path, stated, expect):
    goal = {"name": "rate_goal", "kind": "coverage_goal",
            "expected": f"{stated}% PASS", "coverage_scope": "four scenarios",
            "covered_by": ["a", "b", "c", "d"]}
    vecs = [dict(VEC, name=n) for n in ("a", "b", "c", "d")]
    p = _project(tmp_path, vecs + [goal])
    _record(p, {"a": "PASS", "b": "FAIL", "c": "FAIL", "d": "NOT_RUN"})
    row = _rows(W._coverage_goal_summary(p))["rate_goal"]
    assert row["verdict"] == expect, row
    assert row["failed"] == ["b", "c"], row
    assert "b" in row["why"] and "c" in row["why"], row
    assert "d" in row["why"], row


def test_a_failed_case_defeats_a_two_case_100_percent_goal_even_if_one_did_not_run(
        tmp_path):
    goal = {"name": "rate_goal", "kind": "coverage_goal",
            "expected": "100% PASS", "coverage_scope": "a and b scenarios",
            "covered_by": ["a", "b"]}
    p = _project(tmp_path, [dict(VEC, name="a"), dict(VEC, name="b"), goal])
    _record(p, {"a": "FAIL", "b": "NOT_RUN"})
    row = _rows(W._coverage_goal_summary(p))["rate_goal"]
    assert row["verdict"] == G.FAIL, row
    assert row["failed"] == ["a"], row
    assert "a" in row["why"] and "b" in row["why"], row

def test_a_failed_case_makes_the_goal_unreachable_even_with_a_missing_case(tmp_path):
    goal = dict(GOALS[1], covered_by=["failed_vector", "missing_vector"])
    p = _project(tmp_path, [dict(VEC, name="failed_vector"),
                            dict(VEC, name="missing_vector"), goal])
    _record(p, {"failed_vector": "FAIL"})
    row = _rows(W._coverage_goal_summary(p))[goal["name"]]
    assert row["verdict"] == G.FAIL, row
    assert row["failed"] == ["failed_vector"]
    assert "missing_vector" in row["why"]


def test_changed_l10_cannot_reuse_an_old_pass_record(tmp_path):
    goal = dict(GOALS[1], covered_by=[VEC["name"]])
    p = _project(tmp_path, [VEC, goal])
    _record(p, {VEC["name"]: "PASS"})
    l10 = p / "phase1" / "generated_docs" / "L10_TEST_CASES.json"
    doc = json.loads(l10.read_text())
    doc["test_cases"][0]["stimulus"] = "changed stimulus"
    l10.write_text(json.dumps(doc))
    row = _rows(W._coverage_goal_summary(p))[goal["name"]]
    assert row["verdict"] == G.NOT_MEASURED, row
    assert "execution_record_l10_hash_mismatch" in row["why"]
    ran = W._oracles_that_actually_ran(p)
    assert ran["executed_count"] == 0, ran
    assert ran["record_reason"] == "execution_record_l10_hash_mismatch"


def test_a_case_can_declare_the_goal_it_covers(tmp_path):
    vec = dict(VEC, covers=[GOALS[1]["name"]])
    p = _project(tmp_path, [vec, GOALS[1]])
    _record(p, {vec["name"]: "PASS"})
    row = _rows(W._coverage_goal_summary(p))[GOALS[1]["name"]]
    assert (row["verdict"], row["bound_cases"]) == (G.PASS, [vec["name"]])


@pytest.mark.parametrize("verdicts,expect", [
    ({"a": "PASS", "b": "PASS", "c": "PASS", "d": "FAIL"}, "PASS"),   # 75%
    ({"a": "PASS", "b": "PASS", "c": "FAIL", "d": "FAIL"}, "FAIL"),   # 50%
])
def test_a_stated_rate_below_100_is_a_fraction_of_the_bound_cases(
        tmp_path, verdicts, expect):
    goal = {"name": "rate_goal", "kind": "coverage_goal",
            "expected": "75% PASS", "coverage_scope": "four scenarios",
            "covered_by": list(verdicts)}
    vecs = [dict(VEC, name=n) for n in verdicts]
    p = _project(tmp_path, vecs + [goal])
    _record(p, verdicts)
    assert _rows(W._coverage_goal_summary(p))["rate_goal"]["verdict"] == expect


def test_a_dimension_goal_is_still_measured_by_its_number(tmp_path):
    """Control, GREEN on main: the coverage arm's number still decides it."""
    goal = {"name": "line_goal", "kind": "coverage_goal",
            "coverage_scope": "line coverage over the datapath",
            "expected": ">= 90%"}
    row = G.measure_goal(goal, {"line": {"pct": 95.0}})
    assert row["verdict"] == G.PASS and row["dimension"] == "line"


def test_a_goal_without_a_pass_rate_is_not_given_the_instrument(tmp_path):
    """'100%' of what is not stated: no pass rate, so no scenario instrument."""
    goal = {"name": "bare_pct", "kind": "coverage_goal",
            "expected": "100%", "coverage_scope": "every documented scenario",
            "covered_by": [VEC["name"]]}
    p = _project(tmp_path, [VEC, goal])
    _record(p, {VEC["name"]: "PASS"})
    row = _rows(W._coverage_goal_summary(p))["bare_pct"]
    assert row["verdict"] == G.NOT_MEASURED
