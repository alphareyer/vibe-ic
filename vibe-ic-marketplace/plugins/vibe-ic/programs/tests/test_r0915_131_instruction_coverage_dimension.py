#!/usr/bin/env python3
"""R-0915-131 — the `instruction` coverage dimension arrives WITH its instrument.

THE MEASUREMENT this deck is written from (subservient x gf180mcuD, FRONT DOOR,
run1 on main 48bf6576b, lane icsub5, 2026-09-22), taken AFTER every declared
functional VECTOR had been given a real oracle:

    step4_functional_evidence FAIL: ... and every declared functional vector
    executed its own oracle, but 0 of 1 declared coverage goal(s) met their
    stated percentage. rv32i_40 [NOT_MEASURED] case 'rv32i_40': the stated
    scope names no coverage dimension this run measures (branch/line/toggle):
    '整套 RV32I 指令(40+ 條)單元測試'

The design's L7 plan states an acceptance percentage over its INSTRUCTION SET.
`bind_scope` was right to refuse it -- the flow held no instrument -- and the
gate was right to refuse NOT_MEASURED. What was missing was the instrument.

The rules this deck pins, in both directions:
  * a scope naming instructions BINDS, and one naming a dimension the flow
    still has no instrument for does NOT (negative arm);
  * the three pre-existing dimensions and their words are UNTOUCHED (pinned);
  * the instrument is DECLARATION-DERIVED: no declared goal binding to the
    dimension => it does not run and publishes nothing (never a name pattern);
  * only a case that EXECUTED AND PASSED contributes a tally;
  * a goal that binds with NO contributing tally stays NOT_MEASURED **with the
    reason** -- absent is not zero and not a pass;
  * a published tally gives a verdict by the NUMBER: PASS at/above the stated
    percentage, FAIL below it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import l10_coverage_goal_classify as G            # noqa: E402
import instruction_coverage_measure as M          # noqa: E402
import cpu_functional_oracle_waiver_check as W    # noqa: E402
import _l10_execution as X                        # noqa: E402

#: An instruction-coverage goal derived from the L7 verification plan. Its
#: declared kind must say goal: an explicit functional_vector is executable.
GOAL_INSTRUCTION = {
    "name": "rv32i_40", "kind": "coverage_goal",
    "stimulus": "整套 RV32I 指令(40+ 條)單元測試",
    "expected": "100% PASS(可用 RISC-V Compliance suite 或 SERV 內附 testbench)",
}
#: A goal over a dimension nothing measures — the NEGATIVE arm. It must stay
#: NOT_MEASURED by name however many dimensions are added.
GOAL_UNBINDABLE = {
    "name": "message_length", "kind": "coverage_goal",
    "coverage_scope": "1 byte / 55 bytes / 64 bytes / 1024 bytes",
    "expected": "100% PASS",
}


# ── 1. the vocabulary, both directions, with the old three pinned ─────────
def test_an_instruction_scope_binds_to_the_instruction_dimension():
    dim, why = G.bind_scope("整套 RV32I 指令(40+ 條)單元測試")
    assert dim == "instruction"
    assert "instruction dimension" in why


@pytest.mark.parametrize("scope", [
    "all RV32I instructions unit tested",
    "every opcode in the command table",
    "full ISA coverage",
    "each mnemonic exercised once",
])
def test_the_instruction_vocabulary_is_not_one_language_or_one_word(scope):
    assert G.bind_scope(scope)[0] == "instruction"


def test_a_scope_naming_no_measured_dimension_still_binds_to_nothing():
    """NEGATIVE ARM. Adding a dimension must not make unbindable scopes bind."""
    dim, why = G.bind_scope(GOAL_UNBINDABLE["coverage_scope"])
    assert dim is None
    assert "names no coverage dimension" in why
    assert "55 bytes" in why          # the scope is QUOTED, not summarised
    assert "instruction" in why       # and the new dimension is listed as tried


def test_the_three_pre_existing_dimensions_are_untouched():
    """PIN. R-0915-131 ADDS a dimension; it changes none of the others."""
    assert G.DIMENSION_WORDS["line"] == (
        "line", "lines", "statement", "statements", "code coverage")
    assert G.DIMENSION_WORDS["toggle"] == (
        "toggle", "toggles", "bit toggle", "signal toggle")
    assert G.DIMENSION_WORDS["branch"] == (
        "branch", "branches", "decision", "decisions", "condition",
        "conditions")
    assert G.bind_scope("line coverage over the whole datapath")[0] == "line"
    assert G.bind_scope("toggle coverage")[0] == "toggle"
    assert G.bind_scope("branch coverage")[0] == "branch"


def test_a_scope_naming_two_dimensions_still_refuses_rather_than_picking():
    dim, why = G.bind_scope("instruction and line coverage")
    assert dim is None and "2 dimensions" in why


# ── 2. the fusion: one instrument, one dimension ──────────────────────────
def _project(tmp_path, *, goals, transcripts, states, verilator=True):
    """A project skeleton carrying just what the readers under test read."""
    p = tmp_path
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    l10 = gd / "L10_TEST_CASES.json"
    l10.write_text(json.dumps({"cases": goals}))
    # Written by the record's OWN writer, not by hand: `_l10_execution` fails
    # closed on any shape it does not recognise, and a hand-rolled fixture that
    # trips that check would be testing the fixture, not the instrument.
    X.write_record(p, l10,
                   [{"id": cid, "sim_executed": st == "PASS", "verdict": st,
                     "detail": "fixture", "tb_file": f"{cid}.v"}
                    for cid, st in states.items()],
                   producer="test_r0915_131")
    for cid, text in transcripts.items():
        t = p / "phase2" / "stage1" / "sim_professional" / "l10_unit_tb" / cid
        t.mkdir(parents=True)
        (t / "run.log").write_text(text)
    if verilator:
        c = p / "reports" / "phase2" / "coverage"
        c.mkdir(parents=True, exist_ok=True)
        (c / "coverage_verilator.json").write_text(json.dumps({"totals": {
            "line": {"covered": 50, "total": 100, "pct": 50.0},
            "toggle": {"covered": 10, "total": 100, "pct": 10.0},
            "branch": {"covered": 50, "total": 100, "pct": 50.0}}}))
    return p


EMIT_FULL = ("[TB rv32i_40] INSTRUCTION COVERAGE 41/41 = 100%\n"
             "VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction "
             "covered=41 total=41\n[TB rv32i_40] PASS\n")
EMIT_SHORT = ("VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction "
              "covered=30 total=41\n")


def test_the_instrument_publishes_the_number_when_the_case_passed(tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    r = M.measure(p)
    assert r["applicable"] is True
    assert r["totals"]["instruction"] == {"covered": 41, "total": 41,
                                          "pct": 100.0}
    assert r["per_goal"][0]["meets"] is True


def test_a_goal_below_its_stated_percentage_FAILS_it_does_not_vanish(tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_SHORT},
                 states={"rv32i_40": "PASS"})
    M.write_receipt(p, M.measure(p))
    totals, _src = W._coverage_totals(p)
    row = G.measure_goal(GOAL_INSTRUCTION, totals)
    assert row["verdict"] == G.FAIL
    assert row["achieved_pct"] == 73.17 and row["stated_pct"] == 100.0


def test_a_passing_goal_reads_PASS_through_the_gates_own_reader(tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    M.write_receipt(p, M.measure(p))
    totals, src = W._coverage_totals(p)
    assert sorted(totals) == ["branch", "instruction", "line", "toggle"]
    assert "instruction_coverage.json" in src
    row = G.measure_goal(GOAL_INSTRUCTION, totals)
    assert row["verdict"] == G.PASS and row["achieved_pct"] == 100.0


def test_a_per_dimension_receipt_never_overwrites_another_arms_number(tmp_path):
    """The verilator arm owns line/toggle/branch. A second arm ADDS only."""
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    rec = M.measure(p)
    rec["totals"]["line"] = {"covered": 999, "total": 999, "pct": 100.0}
    M.write_receipt(p, rec)
    totals, _ = W._coverage_totals(p)
    assert totals["line"]["pct"] == 50.0          # the verilator arm's, intact
    assert totals["instruction"]["pct"] == 100.0


# ── 3. the refusals — absent is not zero, and not a pass ──────────────────
def test_no_binding_goal_means_the_instrument_does_not_run(tmp_path):
    """DECLARATION-DERIVED. Keyed on the scope, never on a case name."""
    p = _project(tmp_path, goals=[GOAL_UNBINDABLE],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    r = M.measure(p)
    assert r["applicable"] is False and r["totals"] == {}
    assert "does not ask for it" in r["reason"]


def test_a_tally_from_a_case_that_did_not_pass_is_refused_and_named(tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "NOT_EXECUTED"})
    r = M.measure(p)
    assert r["totals"] == {}
    assert r["refusals"] and r["refusals"][0]["case"] == "rv32i_40"
    assert "did not execute and pass" in r["refusals"][0]["why"]


def test_a_tally_from_an_old_l10_record_is_refused(tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    l10 = p / "phase1" / "generated_docs" / "L10_TEST_CASES.json"
    doc = json.loads(l10.read_text())
    doc["cases"][0]["revision"] = "changed declaration"
    l10.write_text(json.dumps(doc))
    r = M.measure(p)
    assert r["totals"] == {}, r
    assert r["refusals"] and "execution_record_l10_hash_mismatch" in r["refusals"][0]["why"]


def test_a_binding_goal_with_no_tally_stays_NOT_MEASURED_with_the_reason(
        tmp_path):
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION],
                 transcripts={"rv32i_40": "[TB rv32i_40] PASS\n"},
                 states={"rv32i_40": "PASS"})
    M.write_receipt(p, M.measure(p))
    totals, _ = W._coverage_totals(p)
    assert "instruction" not in totals
    row = G.measure_goal(GOAL_INSTRUCTION, totals)
    assert row["verdict"] == G.NOT_MEASURED
    assert "rv32i_40" in row["why"]


def test_an_unbindable_goal_is_still_NOT_MEASURED_beside_a_measured_one(
        tmp_path):
    """NEGATIVE ARM, end to end: the new instrument rescues its OWN dimension
    and nothing else."""
    p = _project(tmp_path, goals=[GOAL_INSTRUCTION, GOAL_UNBINDABLE],
                 transcripts={"rv32i_40": EMIT_FULL},
                 states={"rv32i_40": "PASS"})
    M.write_receipt(p, M.measure(p))
    totals, _ = W._coverage_totals(p)
    s = G.measure_goals([GOAL_INSTRUCTION, GOAL_UNBINDABLE], totals)
    by = {r["case"]: r["verdict"] for r in s["rows"]}
    assert by["rv32i_40"] == G.PASS
    assert by["message_length"] == G.NOT_MEASURED
    assert s["passed_count"] == 1 and s["not_measured_count"] == 1


# ── 4. fusing several tallies ─────────────────────────────────────────────
def test_the_largest_enumeration_wins_so_a_smaller_one_cannot_inflate():
    """A tally over a SMALLER enumeration must not raise the percentage by
    shrinking the denominator."""
    assert M.fuse([{"covered": 41, "total": 41}, {"covered": 5, "total": 5}]) \
        == {"covered": 41, "total": 41, "pct": 100.0}
    assert M.fuse([{"covered": 30, "total": 41}, {"covered": 5, "total": 5}]) \
        == {"covered": 30, "total": 41, "pct": 73.17}


def test_fusing_nothing_is_None_not_zero_percent():
    assert M.fuse([]) is None
