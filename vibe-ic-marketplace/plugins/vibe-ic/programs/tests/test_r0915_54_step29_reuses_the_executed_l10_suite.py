"""R-0915-54 — step 29 re-runs the run's OWN executed L10 suite at gate level.

MEASURED on subservient r20 (the first tree carrying the step-29/30 producers):
step 29 FAILED with basis 'declared-artefact-absent' and the note

    "sdf_gate_sim: no reusable self-checking testbench for subservient and no
     compatible legacy generator ([i_clk, i_rst, i_sram_data, o_gpio, ...])"

while `reports/phase2/sim/l10_execution.json` listed TEN executed self-checking L10
cases, each with its testbench on disk under `phase2/stage1/sim/tb/`. The note was
true of the DISCOVERY — `find_reusable_testbench` accepts a bench only when it carries
one of four marker strings, and only globs *.v/*.sv in three directories — and FALSE
as a statement about the run. A suite the run executed is exactly what step 29 is for:
the same cases, the routed netlist and its SDF in place of the RTL.

The three directions this pins:
  * suite present  -> every executed case re-run at gate level, with a PER-CASE table;
  * a case that fails at gate level -> step 29 FAIL, naming the case (the negative
    control: re-using the suite must be able to REFUSE, or it is not a check);
  * suite genuinely empty -> a NAMED refusal carrying `reason_class`, so the audit
    classifies it rather than recording an absence with no cause.

And the rule underneath all three: a case is never dropped silently. One that ran at
RTL but cannot bind to the gate netlist — renamed port, no module declaration, missing
testbench — is NOT_EXECUTED BY NAME with its reason.
"""
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import sdf_gate_sim as SG  # noqa: E402

TOP = "sha256"


def _tb(text_id: str) -> str:
    # the DUT instantiation must start a line: that is what _tb_dut_instance binds on
    return (f"module tb_{text_id};\n"
            f"  reg clk = 0;\n"
            f"{TOP} dut (.clk(clk));\n"
            f"  initial $finish;\n"
            f"endmodule\n")


def _project(tmp_path: Path, cases) -> Path:
    proj = tmp_path / "p"
    (proj / "reports/phase2/sim").mkdir(parents=True)
    tbdir = proj / "phase2/stage1/sim/tb"
    tbdir.mkdir(parents=True)
    recs = []
    for c in cases:
        rec = {"id": c["id"], "sim_executed": c.get("executed", True),
               "verdict": c.get("verdict", "PASS")}
        if c.get("tb", True):
            p = tbdir / f"{c['id']}.v"
            p.write_text(c.get("text") or _tb(c["id"]))
            rec["tb_file"] = str(p)
        else:
            rec["tb_file"] = str(tbdir / "absent.v")
        if c.get("detail"):
            rec["detail"] = c["detail"]
        recs.append(rec)
    (proj / "reports/phase2/sim/l10_execution.json").write_text(
        json.dumps({"cases": recs}))
    return proj


# --------------------------------------------- direction 1: the suite is used

def test_every_executed_case_becomes_a_step29_subject(tmp_path):
    proj = _project(tmp_path, [{"id": "alpha"}, {"id": "beta"}])
    suite = SG.find_l10_executed_cases(proj, TOP)
    assert [c["id"] for c in suite["cases"]] == ["alpha", "beta"]
    assert suite["skipped"] == []
    assert suite["declared"] == 2


def test_the_suite_is_looked_for_before_the_marker_based_discovery():
    """The run's own executed evidence outranks a four-marker name scan."""
    src = (PROG / "sdf_gate_sim.py").read_text()
    assert src.index("find_l10_executed_cases(project, top)") < \
        src.index("reusable = find_reusable_testbench(project, top)")


# ------------------------------------ no case is ever dropped without a name

@pytest.mark.parametrize("case,disposition", [
    ({"id": "x", "executed": False, "detail": "oracle not executed"},
     "NOT_EXECUTED_AT_RTL"),
    ({"id": "x", "tb": False}, "TB_ABSENT"),
    ({"id": "x", "text": "module tb_x;\n  initial $finish;\nendmodule\n"},
     "NO_GATE_BINDING"),
])
def test_a_case_that_is_not_a_subject_is_named_with_its_reason(
        tmp_path, case, disposition):
    proj = _project(tmp_path, [case])
    suite = SG.find_l10_executed_cases(proj, TOP)
    assert suite["cases"] == []
    assert len(suite["skipped"]) == 1
    assert suite["skipped"][0]["disposition"] == disposition
    assert suite["skipped"][0]["id"] == "x"


def test_every_declared_case_is_accounted_for_exactly_once(tmp_path):
    proj = _project(tmp_path, [{"id": "a"}, {"id": "b", "executed": False},
                               {"id": "c", "tb": False}])
    suite = SG.find_l10_executed_cases(proj, TOP)
    assert suite["declared"] == 3
    assert len(suite["cases"]) + len(suite["skipped"]) == 3


def test_no_l10_record_at_all_falls_through_rather_than_refusing(tmp_path):
    """A project with no L10 suite is not a project whose suite is empty."""
    proj = tmp_path / "bare"
    proj.mkdir()
    assert SG.find_l10_executed_cases(proj, TOP) is None


# ------------------------------- direction 3: an empty suite refuses BY CLASS

def test_an_empty_executed_suite_refuses_with_a_reason_class():
    src = (PROG / "sdf_gate_sim.py").read_text()
    i = src.index("suite = find_l10_executed_cases(project, top)")
    window = src[i:i + 2200]
    assert '"reason_class": ("BLOCKED_BY_UPSTREAM" if declared' in window
    assert '"ZERO_DENOMINATOR"' in window
    # and it names WHICH cases and why, not merely that there were none
    assert "not_a_subject" in window


def test_the_refusal_distinguishes_a_blocked_step_from_an_empty_one():
    """`declared > 0` means upstream produced a suite and did not execute it —
    BLOCKED_BY_UPSTREAM. `declared == 0` is a genuine zero denominator. Folding
    them together is what made the audit say 'absence records no cause'."""
    src = (PROG / "sdf_gate_sim.py").read_text()
    i = src.index("suite = find_l10_executed_cases(project, top)")
    window = src[i:i + 2200]
    assert "BLOCKED_BY_UPSTREAM" in window and "ZERO_DENOMINATOR" in window


# ------------------------ direction 2: the negative control — it can REFUSE

def test_a_failing_case_makes_step29_fail_and_names_it():
    rows = [{"id": "good", "verdict": "PASS", "passed": 1, "total": 1},
            {"id": "bad", "verdict": "FAIL", "passed": 0, "total": 1}]
    log = SG.build_l10_results_log(rows, [], {"top": TOP, "declared": 2})
    assert "VERDICT: FAIL" in log
    assert "bad" in log.split("VERDICT: FAIL")[1]


def test_all_passing_cases_make_step29_pass():
    rows = [{"id": "a", "verdict": "PASS", "passed": 1, "total": 1}]
    log = SG.build_l10_results_log(rows, [], {"top": TOP, "declared": 1})
    assert "VERDICT: PASS" in log


def test_a_case_with_no_verdict_line_is_not_counted_as_a_pass():
    """Positive checks only: an absent marker is NOT_EXECUTED, never a pass."""
    rows = [{"id": "quiet", "verdict": None, "passed": 0, "total": 0}]
    log = SG.build_l10_results_log(rows, [], {"top": TOP, "declared": 1})
    assert "VERDICT: NOT_EXECUTED" in log
    assert "VERDICT: PASS" not in log


def test_the_report_carries_a_per_case_table_and_the_non_subjects():
    rows = [{"id": "a", "verdict": "PASS", "passed": 1, "total": 1,
             "marker": "L10_TB_PASS"}]
    skipped = [{"id": "z", "disposition": "NO_GATE_BINDING", "detail": "port"}]
    log = SG.build_l10_results_log(rows, skipped, {"top": TOP, "declared": 2})
    assert "case" in log and "verdict" in log
    assert "a" in log and "L10_TB_PASS" in log
    assert "z" in log and "NO_GATE_BINDING" in log


# ----------------------------------------------- the per-case verdict parser

def test_a_fail_line_is_a_fail():
    v = SG.parse_l10_case_stdout("[TB alpha] BEGIN\n[TB alpha] FAIL — 2 check(s) failed\n")
    assert v["verdict"] == "FAIL"


def test_a_pass_line_is_a_pass():
    v = SG.parse_l10_case_stdout("[TB alpha] PASS\n")
    assert v["verdict"] == "PASS"


def test_a_transcript_with_neither_marker_yields_no_verdict():
    """The L10 testbenches state their own contract: 'never prints a PASS for a
    check it did not run'. So a quiet transcript is NOT a pass — and an earlier
    defect in this lane read exactly that kind of absence as its opposite."""
    v = SG.parse_l10_case_stdout("[TB alpha] BEGIN\nSUBSTANCE_OK — DUT driven\n")
    assert v["verdict"] is None


def test_the_shared_oracle_marker_still_wins_when_present():
    v = SG.parse_l10_case_stdout("ORACLE_TB_DONE pass=7/7\n")
    assert v["verdict"] == "PASS" and v["total"] == 7


# ------------------------- R-0915-67(2): a killed case is named as killed

def test_the_sim_exit_code_is_kept_not_discarded():
    """It used to be thrown away, and that is why a killed case and a case that
    merely printed nothing were the same finding."""
    src = (PROG / "sdf_gate_sim.py").read_text()
    i = src.index("sr = _docker(container, rr, budget_s=900)")
    # bounded by the END OF THE LOOP, not a byte count and not the first
    # `rows.append` (which belongs to the early `except` branch above).
    window = src[i:src.index("\n    meta = {", i)]
    assert 'r"RC=(\\d+)"' in window or 'RC=(\\d+)' in window
    assert 'row["sim_rc"] = _rc' in window


def test_a_truncated_transcript_is_named_as_read_while_still_being_written():
    """BEHAVIOURAL, not a text scan: an earlier version of this test asserted
    the source contained the words, and it survived setting `_truncated = False`
    — a dead guard. It now calls the namer.

    R-0915-71 CORRECTED WHAT THIS SHAPE MEANS. It used to be named "a killed
    process". Measured on run15, both cases carrying it FINISHED AND PASSED ~100
    minutes after step 29 read them: what stopped was the reader, not the
    simulator."""
    d = SG.name_unverdicted_case(rc=0, size=14241792, truncated=True)
    assert "TRUNCATED" in d
    assert "4096-byte boundary" in d          # 14241792 == 3477 * 4096
    assert "still being written" in d
    assert "$finish long after the reader had moved on" in d
    # the claim that was measured to be wrong must not come back
    assert "killed process" not in d


def test_an_unaligned_truncation_is_still_named_without_the_boundary():
    d = SG.name_unverdicted_case(rc=0, size=14037595, truncated=True)
    assert "TRUNCATED" in d
    assert "4096-byte boundary" not in d


def test_a_reaped_case_is_named_as_a_reap_and_not_as_a_result():
    """R-0915-71. A stall reap has its own sentence: the supervisor stopped, and
    a reap is not a testbench result."""
    import _container_exec as _ce
    d = SG.name_unverdicted_case(rc=_ce.STALLED_RC, size=14241792,
                                 truncated=True)
    assert "REAPED" in d and "no forward progress in the container" in d
    assert "a reap is not a testbench result" in d
    # and it is a DIFFERENT sentence from a plain truncation
    assert d != SG.name_unverdicted_case(rc=0, size=14241792, truncated=True)

def test_four_outcomes_get_four_sentences():
    """Behavioural, and the count is the point: a reap, a truncation, a non-zero
    exit and a silent testbench are four different facts."""
    import _container_exec as _ce
    reaped  = SG.name_unverdicted_case(rc=_ce.STALLED_RC, size=14241792,
                                       truncated=True)
    trunc   = SG.name_unverdicted_case(rc=137, size=14241792, truncated=True)
    errored = SG.name_unverdicted_case(rc=1,   size=500,      truncated=False)
    silent  = SG.name_unverdicted_case(rc=0,   size=500,      truncated=False)
    assert "REAPED" in reaped
    assert "TRUNCATED" in trunc and "REAPED" not in trunc
    assert "exited rc=1" in errored and "TRUNCATED" not in errored
    assert "no verdict line at gate level" in silent and "rc=" not in silent
    assert len({reaped, trunc, errored, silent}) == 4


def test_naming_an_unverdicted_case_never_makes_it_a_pass():
    """Naming WHY it did not finish must never change the verdict.

    THIS GUARD WAS DEAD UNTIL R-0915-71. Two functions in this file carried the
    name `test_none_of_this_turns_a_killed_case_into_a_pass`; the second
    (a source scan) shadowed the first (this loop), so pytest collected one test
    and the behavioural one never ran. Renaming them is what made it run — and
    it immediately failed, on a substring screen (`"PASS" not in d.upper()`) so
    crude that the SHIPPED sentence "a missing marker is not a pass" tripped it.
    A screen that a correct output fails is not a guard, so it is now keyed on
    what a false pass would actually look like: the word PASSED, or the L10
    marker itself, appearing in the reason a case has NO verdict."""
    import _container_exec as _ce
    for rc, size, trunc in ((_ce.STALLED_RC, 14241792, True),
                            (137, 14241792, True), (1, 500, False),
                            (0, 500, False), (None, 0, False)):
        d = SG.name_unverdicted_case(rc=rc, size=size, truncated=trunc)
        assert "PASSED" not in d.upper()
        assert "L10_TB_PASS" not in d
        assert d.strip() != ""


def test_this_file_declares_no_test_name_twice():
    """The shadowing above, as a standing guard: a duplicate `def test_...` in
    one module silently drops every earlier copy, and a dropped test reads as a
    green line."""
    import ast
    import collections
    tree = ast.parse(Path(__file__).read_text())
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    dupes = [n for n, c in collections.Counter(names).items() if c > 1]
    assert not dupes, dupes

def test_none_of_this_turns_a_killed_case_into_a_pass():
    """The whole point of the guard: naming WHY it did not finish must not
    change the verdict, which stays absent."""
    src = (PROG / "sdf_gate_sim.py").read_text()
    i = src.index("sr = _docker(container, rr, budget_s=900)")
    # bounded by the END OF THE LOOP, not a byte count and not the first
    # `rows.append` (which belongs to the early `except` branch above).
    window = src[i:src.index("\n    meta = {", i)]
    # every branch below sits under `if parsed.get("verdict") is None:`
    j = window.index('if parsed.get("verdict") is None:')
    tail = window[j:]
    for bad in ('verdict"] = "PASS"', "verdict'] = 'PASS'"):
        assert bad not in tail
