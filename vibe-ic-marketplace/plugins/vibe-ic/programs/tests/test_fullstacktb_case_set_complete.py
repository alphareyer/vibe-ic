#!/usr/bin/env python3
"""FULLSTACKTB final-review follow-up (2026-09-29) — the Step-5 functional
record must carry EXACTLY the case set its hash-bound L10 declares.

`bit_level_full_stack_tb_check.functional_full_stack_verdict` re-derived each
case's stated population from the L10 it had just hash-checked, but it never
checked that every L10 case was in `record.cases`. The producer writes one row
per L10 case whatever became of it (passed, failed, errored, short, no_oracle,
excluded), so a record with its errored or short row deleted outright was
judged on the rows left and read PASS. A row the L10 does not declare was
counted the same way.

The gate now refuses both, NOT_MEASURED + INCOMPLETE, naming the cases:
missing L10 cases, and record cases the L10 does not declare (compared as a
multiset, so a duplicated row cannot stand in for a deleted one).

chip-AGNOSTIC: every fixture is a synthetic design.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_fullstacktb_functional_population as base  # noqa: E402

#: Rebased onto R-0929-STEP5-BAR (v1.26.45): the landed gate reports a record
#: that omits a declared case as `functional_record_inconsistent`
#: (test_r0929_step5_bar_datapath pins it); the exact-set check reports
#: missing / extra / duplicated rows through that same rule.
RULE = "functional_record_inconsistent"


def _fsf():
    return importlib.import_module("full_stack_functional_tb")


@pytest.fixture
def arith_class(monkeypatch):
    import testbench_gen as tbg
    monkeypatch.setattr(tbg, "_detect_ic_class",
                        lambda project: "digital_arithmetic_primitive")


def _rewrite(proj, rec):
    _fsf().record_path(proj).write_text(json.dumps(rec))


def _reset_times_out():
    inner = base._FakeSim()

    def sim(argv, run_dir, container, tool, timeout):
        if argv[0] == "vvp" and Path(run_dir).name == "reset":
            return 124, "simulation timed out\n"
        return inner(argv, run_dir, container, tool, timeout)

    return sim


def _assert_refused(proj, tmp_path, capsys, missing, extra):
    rc, res, out = base._run_gate(proj, tmp_path, capsys)
    assert rc == 2 and res["verdict"] == "NOT_MEASURED", res
    assert res["pass"] is False and res["functional_verified"] is False
    assert res["rule"] == RULE, res
    assert res["missing_cases"] == missing
    assert res["extra_cases"] == extra
    last = out.rstrip().splitlines()[-1]
    assert last.startswith("INCOMPLETE:"), out
    for name in missing + extra:
        assert name in last, last
    return res


def test_an_errored_case_deleted_from_the_record_is_not_a_pass(
        tmp_path, arith_class, capsys):
    """The review's exact shape: the errored row cut out, every other row
    untouched and still hash-consistent."""
    proj = base._mk_project(tmp_path, die=True)
    rec = _fsf().generate(proj, "ctr", dispatch=_reset_times_out(),
                          model_resolver=base._resolver)
    assert {c["name"]: c["state"] for c in rec["cases"]}["reset"] == "errored"
    rec["cases"] = [c for c in rec["cases"] if c["name"] != "reset"]
    _rewrite(proj, rec)
    _assert_refused(proj, tmp_path, capsys, ["reset"], [])


def test_a_short_case_deleted_from_the_record_is_not_a_pass(
        tmp_path, arith_class, capsys):
    proj = base._mk_project(tmp_path, die=True)
    sim = base._FakeSim(lambda case, nv: (min(nv, 50), min(nv, 50))
                        if case == "random_equivalence" else (nv, nv))
    rec = _fsf().generate(proj, "ctr", dispatch=sim,
                          model_resolver=base._resolver)
    assert rec["verdict"] == "NOT_MEASURED"
    rec["cases"] = [c for c in rec["cases"]
                    if c["name"] != "random_equivalence"]
    _rewrite(proj, rec)
    _assert_refused(proj, tmp_path, capsys, ["random_equivalence"], [])


def test_a_not_run_case_deleted_from_the_record_is_refused(
        tmp_path, arith_class, capsys):
    """Even a row that executed nothing (the excluded coverage figure) is part
    of the declared set: its absence means the record is not the producer's."""
    proj, rec = base._passing_die(tmp_path)
    rec["cases"] = [c for c in rec["cases"]
                    if c["name"] != "toggle_branch_coverage"]
    _rewrite(proj, rec)
    _assert_refused(proj, tmp_path, capsys, ["toggle_branch_coverage"], [])


def test_a_case_the_l10_does_not_declare_is_refused(tmp_path, arith_class,
                                                    capsys):
    """A copied passing row under a name the L10 does not carry: its evidence
    is hash-consistent and re-scores as passed, so without the set check it
    would be counted as a fourth executed case."""
    proj, rec = base._passing_die(tmp_path)
    phantom = dict(next(c for c in rec["cases"]
                        if c["name"] == "random_equivalence"))
    phantom["name"] = "phantom_case"
    rec["cases"].append(phantom)
    _rewrite(proj, rec)
    _assert_refused(proj, tmp_path, capsys, [], ["phantom_case"])


def test_a_duplicated_row_cannot_stand_in_for_a_deleted_one(
        tmp_path, arith_class, capsys):
    proj = base._mk_project(tmp_path, die=True)
    rec = _fsf().generate(proj, "ctr", dispatch=_reset_times_out(),
                          model_resolver=base._resolver)
    dup = dict(next(c for c in rec["cases"] if c["name"] == "corner_operand"))
    rec["cases"] = [c for c in rec["cases"] if c["name"] != "reset"] + [dup]
    _rewrite(proj, rec)
    _assert_refused(proj, tmp_path, capsys, ["reset"], ["corner_operand"])


def test_the_producers_complete_record_still_passes(tmp_path, arith_class,
                                                    capsys):
    """KNOWN-NEGATIVE: the record exactly as the producer wrote it is never
    refused by the set check -- including an L10 case named only by `id` and a
    case whose name is not a legal identifier (the producer names both rows
    the way the gate does)."""
    cases = base.CASES + [
        {"id": "by_id_only", "kind": "functional_vector",
         "stimulus": "blinky.hex", "expected": "GPIO toggles"},
        {"name": "not an identifier", "kind": "functional_vector",
         "stimulus": "blinky.hex", "expected": "GPIO toggles"}]
    proj = base._mk_project(tmp_path, die=True, cases=cases)
    rec = _fsf().generate(proj, "ctr", dispatch=base._FakeSim(),
                          model_resolver=base._resolver)
    names = [c["name"] for c in rec["cases"]]
    assert "by_id_only" in names and "not an identifier" in names
    rc, res, out = base._run_gate(proj, tmp_path, capsys)
    # the set check passes the producer's own record ...
    assert res["missing_cases"] == [] and res["extra_cases"] == [], res
    assert res["rule"] != RULE, res
    assert res["counts"]["executed"] == 3
    # ... and the landed R-0929-STEP5-BAR, not the set check, then judges the
    # two declared rows no oracle executed (they block Step 5 by name)
    assert res["verdict"] == "NOT_MEASURED", res
    assert "by_id_only" in res["rationale"]
    assert "not an identifier" in res["rationale"]


# ===========================================================================
# Rebased onto R-0929-STEP5-BAR: the ISA-credited / data-path rows are bound
# by the same exact-set rule (a credited row is one L10 case, never a stand-in)
# ===========================================================================
import test_r0929_step5_bar_datapath as s5dp  # noqa: E402
from test_r0929_step5_bar_datapath import cpu_env  # noqa: E402,F401


def _credited_pass(tmp_path):
    proj = s5dp._cpu_project(tmp_path)
    rec = s5dp._gen(proj)
    by = {c["name"]: c for c in rec["cases"]}
    assert by["rv32i_all"]["step5_disposition"]["disposition"] == \
        "isa_credited" and rec["verdict"] == "PASS", rec["reason"]
    return proj, rec


def test_an_isa_credited_row_deleted_from_the_record_is_refused(
        tmp_path, cpu_env, capsys):
    proj, rec = _credited_pass(tmp_path)
    rec["cases"] = [c for c in rec["cases"] if c["name"] != "rv32i_all"]
    _fsf().record_path(proj).write_text(json.dumps(rec))
    _assert_refused(proj, tmp_path, capsys, ["rv32i_all"], [])


def test_a_credited_row_cannot_stand_in_for_an_executed_one(
        tmp_path, cpu_env, capsys):
    proj, rec = _credited_pass(tmp_path)
    credited = next(c for c in rec["cases"] if c["name"] == "rv32i_all")
    rec["cases"] = [c for c in rec["cases"] if c["name"] != "boot_fetch"] + [
        dict(credited)]
    _fsf().record_path(proj).write_text(json.dumps(rec))
    _assert_refused(proj, tmp_path, capsys, ["boot_fetch"], ["rv32i_all"])


def test_the_datapath_program_is_not_an_l10_case_row(tmp_path, cpu_env,
                                                     capsys):
    """The flow-built data-path program backs ISA credit; recorded as a CASE
    row it is a case the L10 does not declare."""
    proj, rec = _credited_pass(tmp_path)
    rec["cases"].append(dict(rec["cpu_datapath"], name="cpu_datapath_program"))
    _fsf().record_path(proj).write_text(json.dumps(rec))
    _assert_refused(proj, tmp_path, capsys, [], ["cpu_datapath_program"])
