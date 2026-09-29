#!/usr/bin/env python3
"""R-0915-113(4) — a coverage GOAL has its own instrument and its own denominator.

THE MEASUREMENT this deck is written from (sha256 x sky130A, FRONT DOOR, run23
on main 751bed176, the design's own Phase-1 documents and the run's own
coverage arm):

    BASE   step4_functional_evidence: "only 6 of 11 declared L10 case(s)
           EXECUTED their own oracle. Not executed:
           single_block_nist_appa_abcdbcde_mnopqr, random_message_...,
           message_length, protocol, mode_switch"
    HEAD   vectors 6 of 7 executed  (the one left states a 448-bit LENGTH and
                                     no message — an INPUT defect, not a
                                     plugin one)
           goals   0 of 4 measured, each NOT_MEASURED with its own scope
                   quoted by name

Four of the five "not executed" rows declare `kind: coverage_goal` and state
an acceptance PERCENTAGE ("100% PASS") over a named SCOPE. Nothing can execute
an oracle for a percentage, so the old arithmetic demanded four impossible
things and hid the one real gap among them.

The rule this deck pins, in both directions:
  * a percentage-over-scope row IS a coverage goal — and so is one that
    declares the kind itself;
  * a stated-vector row is NOT, even when its scope text is full of numbers;
  * a goal is NEVER dropped and NEVER marked executed;
  * a goal whose scope names a dimension the run measured gets a verdict by
    the NUMBER, PASS or FAIL;
  * a goal whose scope binds to nothing is NOT_MEASURED with the scope quoted
    — and NOT_MEASURED is not a pass, so the gate still refuses.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import l10_coverage_goal_classify as G           # noqa: E402
import cpu_functional_oracle_waiver_check as W   # noqa: E402
import _l10_execution as X                       # noqa: E402

# ── the design's own rows, transcribed (the corpus is not on main) ─────────
VEC_KAV = {"name": "fips1804_sha256_abc", "kind": "known_answer_vector",
           "citation": "FIPS-180-4", "inputs": {"message": "616263"},
           "expected_outputs": {"digest": "ba7816bf" * 8}}
VEC_FUNC = {"name": "empty_nist_appa_single_block_abc",
            "kind": "functional_vector",
            "stimulus": "0x6162638000... + length 24 bit",
            "expected": "ba7816bf 8f01cfea 414140de 5dae2223 "
                        "b00361a3 96177a9c b410ff61 f20015ad"}
#: A vector whose SCOPE text is full of numbers. It must stay a vector.
VEC_NUMERIC_SCOPE = {"name": "numbers_in_the_stimulus",
                     "kind": "functional_vector",
                     "stimulus": "100 bytes at 0x61, 1000 cycles, 50 reads",
                     "expected": "cdc76e5c9914fb92"}
GOAL_DECLARED = {"name": "message_length", "kind": "coverage_goal",
                 "stimulus": "1 byte / 55 bytes / 64 bytes / 1024 bytes",
                 "coverage_scope": "1 byte / 55 bytes / 64 bytes / 1024 bytes",
                 "expected": "100% PASS"}
#: The same shape with NO declared kind — the percentage alone must classify it.
GOAL_BY_PERCENT = {"name": "protocol",
                   "stimulus": "INIT during BUSY / NEXT without prior INIT",
                   "expected": "100% PASS"}
GOAL_BINDABLE = {"name": "line_coverage_goal", "kind": "coverage_goal",
                 "coverage_scope": "line coverage over the whole datapath",
                 "expected": ">= 90%"}
TOTALS = {"line": {"covered": 107, "total": 112, "pct": 95.54},
          "toggle": {"covered": 3940, "total": 3976, "pct": 99.09},
          "branch": {"covered": 14, "total": 14, "pct": 100.0},
          # R-0915-131. The fourth dimension arrives WITH its instrument
          # (`instruction_coverage_measure`), which is the whole content of
          # `test_the_measured_dimensions_are_the_ones_the_run_publishes`
          # below: a dimension listed in DIMENSION_WORDS that nothing
          # publishes a number for would turn a goal from NOT_MEASURED into a
          # verdict over an empty population.
          "instruction": {"covered": 41, "total": 41, "pct": 100.0}}


# ── 1. classification is structural, and cites the line it read ───────────
def test_a_row_that_declares_the_kind_is_a_coverage_goal():
    kind, why = G.classify(GOAL_DECLARED)
    assert kind == G.COVERAGE_GOAL
    assert "kind='coverage_goal'" in why


def test_a_percentage_over_a_scope_is_a_coverage_goal_without_a_declared_kind():
    kind, why = G.classify(GOAL_BY_PERCENT)
    assert kind == G.COVERAGE_GOAL
    assert "100%" in why and "acceptance percentage" in why


@pytest.mark.parametrize("case", [VEC_KAV, VEC_FUNC, VEC_NUMERIC_SCOPE],
                         ids=lambda c: c["name"])
def test_a_stated_vector_is_never_a_coverage_goal(case):
    assert G.classify(case)[0] == G.STATED_VECTOR


def test_a_percentage_in_the_STIMULUS_does_not_make_a_goal():
    """An acceptance criterion lives in the expected half. A stimulus that
    says "0-2KB, 100% duty" is describing drive, not acceptance."""
    case = {"name": "duty", "kind": "functional_vector",
            "stimulus": "a 100% duty-cycle clock", "expected": "deadbeef"}
    assert G.classify(case)[0] == G.STATED_VECTOR


def test_partition_keeps_declaration_order_and_loses_nothing():
    rows = [VEC_KAV, GOAL_DECLARED, VEC_FUNC, GOAL_BY_PERCENT]
    vectors, goals = G.partition(rows)
    assert [c["name"] for c in vectors] == [VEC_KAV["name"], VEC_FUNC["name"]]
    assert [c["name"] for c in goals] == [GOAL_DECLARED["name"],
                                          GOAL_BY_PERCENT["name"]]
    assert len(vectors) + len(goals) == len(rows)


def test_a_non_dict_row_cannot_crash_the_partition():
    vectors, goals = G.partition([VEC_KAV, None, "nonsense", 7, GOAL_DECLARED])
    assert len(vectors) == 1 and len(goals) == 1


# ── 2. binding a scope to a dimension the run actually measured ───────────
def test_a_scope_that_names_a_measured_dimension_binds():
    dim, why = G.bind_scope("line coverage over the whole datapath")
    assert dim == "line" and "line dimension" in why


def test_a_stimulus_scope_binds_to_nothing_and_says_so_with_the_scope():
    scope = ("1 byte / 55 bytes(single-block boundary)/ 56 bytes / 64 bytes "
             "/ 119 bytes / 120 bytes / 1024 bytes")
    dim, why = G.bind_scope(scope)
    assert dim is None
    assert "names no coverage dimension" in why
    assert "55 bytes" in why            # the scope is QUOTED, not summarised


def test_a_scope_naming_two_dimensions_refuses_rather_than_picking_one():
    dim, why = G.bind_scope("line and branch coverage")
    assert dim is None and "2 dimensions" in why


def test_the_measured_dimensions_are_the_ones_the_run_publishes():
    assert set(G.DIMENSION_WORDS) == set(TOTALS)


# ── 3. the verdict is by the NUMBER, or by NAME — never by default ────────
def test_a_bound_goal_that_meets_its_percentage_passes():
    row = G.measure_goal(GOAL_BINDABLE, TOTALS)
    assert row["verdict"] == G.PASS
    assert (row["dimension"], row["stated_pct"], row["achieved_pct"]) == \
        ("line", 90.0, 95.54)


def test_a_bound_goal_that_misses_its_percentage_fails():
    case = dict(GOAL_BINDABLE, expected="100% of lines")
    row = G.measure_goal(case, TOTALS)
    assert row["verdict"] == G.FAIL
    assert row["achieved_pct"] == 95.54 and row["stated_pct"] == 100.0


def test_exactly_meeting_the_stated_percentage_passes():
    case = dict(GOAL_BINDABLE, expected="95.54%")
    assert G.measure_goal(case, TOTALS)["verdict"] == G.PASS


def test_an_unbindable_scope_is_NOT_MEASURED_and_names_the_case():
    row = G.measure_goal(GOAL_DECLARED, TOTALS)
    assert row["verdict"] == G.NOT_MEASURED
    assert GOAL_DECLARED["name"] in row["why"]


def test_a_run_with_no_coverage_totals_is_NOT_MEASURED_not_a_pass():
    row = G.measure_goal(GOAL_BINDABLE, {})
    assert row["verdict"] == G.NOT_MEASURED
    assert "no coverage totals" in row["why"]


def test_a_goal_with_no_stated_percentage_is_NOT_MEASURED():
    case = {"name": "vague", "kind": "coverage_goal",
            "coverage_scope": "line coverage", "expected": "good enough"}
    row = G.measure_goal(case, TOTALS)
    assert row["verdict"] == G.NOT_MEASURED and "vague" in row["why"]


def test_a_flat_pct_totals_shape_is_read_too():
    assert G.achieved_percentage({"line_pct": 88.0}, "line")[0] == 88.0


def test_the_goal_population_carries_its_own_denominator():
    s = G.measure_goals([GOAL_BINDABLE, GOAL_DECLARED, GOAL_BY_PERCENT],
                        TOTALS)
    assert (s["declared_count"], s["passed_count"], s["failed_count"],
            s["not_measured_count"]) == (3, 1, 0, 2)
    assert len(s["rows"]) == 3


# ── 4. a goal is never satisfied by having no instrument ──────────────────
def test_an_unmeasured_goal_still_produces_a_refusal():
    s = G.measure_goals([GOAL_DECLARED], TOTALS)
    why = G.coverage_goal_refusal(s)
    assert why and "NOT_MEASURED" in why and GOAL_DECLARED["name"] in why


def test_a_failed_goal_still_produces_a_refusal():
    s = G.measure_goals([dict(GOAL_BINDABLE, expected="100%")], TOTALS)
    why = G.coverage_goal_refusal(s)
    assert why and "FAIL" in why


def test_goals_that_all_pass_produce_no_refusal():
    assert G.coverage_goal_refusal(G.measure_goals([GOAL_BINDABLE],
                                                   TOTALS)) is None


def test_no_declared_goal_produces_no_refusal():
    assert G.coverage_goal_refusal(G.measure_goals([], TOTALS)) is None


# ── 5. the gate uses it: two populations, two denominators ────────────────
def _project(tmp_path: Path, cases, totals=TOTALS, record=True) -> Path:
    p = tmp_path / "proj"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase2" / "stage1" / "sim").mkdir(parents=True)
    (p / "reports" / "phase2" / "coverage").mkdir(parents=True)
    (p / "phase1" / "generated_docs" / "L10_TEST_CASES.json").write_text(
        json.dumps({"schema_version": 2, "doc_class": "test_cases",
                    "ic_name": "x", "test_cases": cases}))
    if totals is not None:
        (p / "reports" / "phase2" / "coverage"
         / "coverage_verilator.json").write_text(json.dumps(
             {"totals": totals}))
    return p


def test_the_vector_denominator_excludes_the_goals(tmp_path):
    p = _project(tmp_path, [VEC_KAV, VEC_FUNC, GOAL_DECLARED, GOAL_BY_PERCENT])
    ids = W._declared_l10_case_ids(p)
    assert ids == [VEC_KAV["name"], VEC_FUNC["name"]]


def test_the_goals_are_still_declared_and_reported(tmp_path):
    p = _project(tmp_path, [VEC_KAV, GOAL_DECLARED, GOAL_BY_PERCENT])
    s = W._coverage_goal_summary(p)
    assert s["declared_count"] == 2
    assert {r["case"] for r in s["rows"]} == {GOAL_DECLARED["name"],
                                              GOAL_BY_PERCENT["name"]}
    assert s["totals_source"].endswith("coverage_verilator.json")


def test_no_goal_is_ever_reported_as_executed(tmp_path):
    p = _project(tmp_path, [VEC_KAV, GOAL_DECLARED])
    s = W._coverage_goal_summary(p)
    assert all(r["verdict"] in (G.PASS, G.FAIL, G.NOT_MEASURED)
               for r in s["rows"])
    assert "EXECUTED" not in json.dumps(s)
    assert GOAL_DECLARED["name"] not in W._declared_l10_case_ids(p)


def _executed_record(name, verdict, tb):
    return {"available": True, "rows": {name: {
        "verdict": verdict, "raw": verdict, "sim_executed": True,
        "tb_file": str(tb), "detail": "", **X.capture_tb_identity(tb)}}}


def test_a_generated_golden_oracle_cannot_measure_its_own_scenario_goal(tmp_path):
    tb = tmp_path / "protocol.v"
    tb.write_text("// VIBEIC_TB_ORACLE: GENERATED by _emit_case_golden_oracle\n")
    row = G.measure_scenario_goal(GOAL_BY_PERCENT, 100.0, [GOAL_BY_PERCENT],
                                  _executed_record("protocol", "PASS", tb))
    assert row["verdict"] == G.NOT_MEASURED
    assert row["bound_cases"] == []


def test_an_executed_own_failure_stays_bound_even_with_declared_links(tmp_path):
    tb = tmp_path / "protocol.v"
    tb.write_text("// authored scenario test\n")
    goal = dict(GOAL_BY_PERCENT, covered_by=["vecA"])
    vec = {"name": "vecA"}
    record = _executed_record("protocol", "FAIL", tb)
    record["rows"]["vecA"] = {"verdict": "PASS", "raw": "PASS",
                                  "sim_executed": True, "tb_file": str(tb),
                                  "detail": "", **X.capture_tb_identity(tb)}
    row = G.measure_scenario_goal(goal, 100.0, [goal, vec], record)
    assert row["verdict"] == G.FAIL
    assert row["bound_cases"] == ["vecA", "protocol"]


def test_unavailable_execution_record_names_its_actual_reason():
    row = G.measure_scenario_goal(GOAL_BY_PERCENT, 100.0, [GOAL_BY_PERCENT],
                                  {"available": False,
                                   "reason": "execution_record_schema_mismatch",
                                   "rows": {}})
    assert row["verdict"] == G.NOT_MEASURED
    assert "execution_record_schema_mismatch" in row["why"]


def test_a_run_with_no_coverage_report_names_where_it_looked(tmp_path):
    p = _project(tmp_path, [GOAL_BINDABLE], totals=None)
    s = W._coverage_goal_summary(p)
    assert s["not_measured_count"] == 1
    assert "coverage_verilator.json" in s["totals_source"]


def test_the_refusal_separates_the_two_populations(tmp_path):
    p = _project(tmp_path, [VEC_KAV, GOAL_DECLARED])
    why = W._oracle_execution_refusal(p, "results.xml")
    assert why
    assert "1 declared L10 case(s) EXECUTED" in why   # the VECTOR denominator
    assert "0 of 1 declared coverage goal(s)" in why  # the GOAL denominator
    assert "Separately," in why


def test_a_design_with_no_vector_at_all_still_owes_the_goal_sentence(tmp_path):
    """An empty vector denominator says nothing about the goal population."""
    p = _project(tmp_path, [GOAL_DECLARED])
    why = W._oracle_execution_refusal(p, "results.xml")
    assert why and "declares no functional vector" in why
    assert GOAL_DECLARED["name"] in why


def _record_executed(project: Path, case_ids) -> None:
    """Publish a REAL execution record saying those cases ran and passed."""
    import _l10_execution as _l10x
    l10 = project / "phase1" / "generated_docs" / "L10_TEST_CASES.json"
    _l10x.write_record(
        project, l10,
        [{"id": cid, "verdict": _l10x.PASS, _l10x.SIM_EXECUTED_KEY: True,
          "detail": "unit fixture", "tb_file": f"{cid}.v"}
         for cid in case_ids],
        producer="test_r0915_113_4")


def test_a_run_whose_vectors_all_ran_still_owes_the_goal_sentence(tmp_path):
    """The goal population may not be satisfied by the vector population."""
    p = _project(tmp_path, [VEC_KAV, GOAL_DECLARED])
    _record_executed(p, [VEC_KAV["name"]])
    ran = W._oracles_that_actually_ran(p)
    assert (ran["executed_count"], ran["declared_count"]) == (1, 1)
    why = W._oracle_execution_refusal(p, "results.xml")
    assert why and "every declared functional vector executed" in why
    assert GOAL_DECLARED["name"] in why


def test_a_run_whose_vectors_ran_and_whose_goals_pass_is_not_refused(tmp_path):
    p = _project(tmp_path, [VEC_KAV, GOAL_BINDABLE])
    _record_executed(p, [VEC_KAV["name"]])
    assert W._oracle_execution_refusal(p, "results.xml") is None


def test_a_design_with_only_passing_goals_and_no_vectors_is_not_refused(
        tmp_path):
    p = _project(tmp_path, [GOAL_BINDABLE])
    assert W._oracle_execution_refusal(p, "results.xml") is None


def test_the_evidence_summary_publishes_both_denominators(tmp_path):
    p = _project(tmp_path, [VEC_KAV, GOAL_DECLARED, GOAL_BY_PERCENT])
    s = W._evidence_summary(p)
    assert s["coverage_goals"]["declared_count"] == 2
    assert s["functional_test_denominator"] is not None


def test_the_partition_is_reached_from_the_gate_not_merely_defined():
    """A helper with tests is not a fix until the gate calls it."""
    tree = ast.parse((PROG / "cpu_functional_oracle_waiver_check.py").read_text())
    fn = next(f for f in ast.walk(tree)
              if isinstance(f, ast.FunctionDef)
              and f.name == "_declared_l10_case_ids")
    called = {ast.unparse(n.func) for n in ast.walk(fn)
              if isinstance(n, ast.Call)}
    assert "_cgc.partition" in called


# ── 6. the deck's own hygiene ─────────────────────────────────────────────
def test_no_two_tests_in_this_file_share_a_name():
    tree = ast.parse(Path(__file__).read_text())
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(names) == len(set(names)), \
        sorted({n for n in names if names.count(n) > 1})
