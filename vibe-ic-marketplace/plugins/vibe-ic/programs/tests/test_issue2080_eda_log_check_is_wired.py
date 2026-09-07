#!/usr/bin/env python3
"""vibe-ic#2080 — `eda_log_check` was credited by a closing parenthesis.

WHAT WAS MEASURED ON THE PRE-FIX TIP (94617408759e)
===================================================
`checker_execution_wiring_baseline.json` recorded the whole of it under #1347:

    "Never wired; held up solely by the fragment `eda_log_check)` at the end of
     a sentence in `openroad_tcl_deprecation_check`'s deprecation table."

    programs/openroad_tcl_deprecation_check.py:123
        "eda_log_check)"

One string fragment, and both wiring registers read it as an execution path.
Its own triage note names the home this commit uses: "Takes --log-file, so it is
per-design and belongs ... at the step that produces the log it should read."

WHAT THE ROW BUYS
=================
`step_yosys_synth` writes `phase2/stage2/synth/yosys.log` unconditionally, and
NOTHING in the tree asked whether that file exists, is non-empty, or carries the
`stat` table the step dropped `-q` (v1.6.193) in order to capture. The step's
own sibling comment names the failure mode it does not cover —
`_ystat.emit_stats_json` refuses to write accounting because "the docker-fallback
path can return rc=0 with an empty stdout capture" — but that guard reads the
IN-MEMORY capture, and the FILE is the artefact every later reader has. A
zero-byte `yosys.log` beside a netlist has read exactly like a complete one.

BOTH DIRECTIONS
===============
`test_a_log_carrying_the_tools_own_stat_table_is_a_PASS` and the three FINDING
cases beside it are one fixture apart: same project, same synth verdict, the
log's CONTENT the only variable. And the discriminator that must not be got
wrong is measured on its own:
`test_a_design_that_never_synthesised_is_NOT_APPLICABLE_not_a_FINDING` — a
pure-analog design has no digital RTL, `step_yosys_synth` answers SKIP by
design, and reading the log's absence directly would accuse every such design.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as DOSR                # noqa: E402
import step_preflight as _spf                        # noqa: E402

_DISPATCH = "plan.append(step_synth_log_audit(project, plan[-1]))"
_GATE = "eda_log_check"

#: SPELLED HERE, and pinned to the runner's constants by
#: `test_this_file_measures_the_same_log_and_the_same_skip_set`.
#:
#: A module-level `DOSR._SYNTH_LOG_REL` / `DOSR._SYNTH_NOT_ATTEMPTED` would be
#: read at COLLECTION time, so on the pre-fix tree this file aborted collection
#: with one AttributeError instead of failing seventeen tests by name — and a
#: collection abort scrapes as zero failures, which is the weakest possible
#: negative control. Literals here, membership asserted below.
_SYNTH_LOG_REL = "phase2/stage2/synth/yosys.log"
_NOT_ATTEMPTED = ("BLOCKED", "SKIP", "SKIPPED-BY-ENTRY", "SKIPPED-BY-EXIT",
                  "SKIPPED-CONDITION")

_REAL_STAT_LOG = (
    "\n=== chip_top ===\n"
    "\n   Number of wires:                 12\n"
    "   Number of cells:                   42\n"
    "     $_AND_                            8\n"
)


def _project(tmp: Path, log: str | None = None) -> Path:
    p = tmp / "proj"
    (p / _SYNTH_LOG_REL).parent.mkdir(parents=True)
    if log is not None:
        (p / _SYNTH_LOG_REL).write_text(log)
    return p


def _synth(status: str):
    return DOSR.StepResult("yosys_synth", status, 0.0, "")


def _row(res):
    assert res.extras and "synth_log_audit" in res.extras, res.extras
    return res.extras["synth_log_audit"]


# ---------------------------------------------------------------------------
# THE WIRING
# ---------------------------------------------------------------------------
def test_the_runner_dispatches_the_gate_at_a_real_call_site() -> None:
    src = Path(DOSR.__file__).read_text(errors="replace")
    assert _DISPATCH in src, (
        f"design_one_shot_runner no longer dispatches {_GATE} ({_DISPATCH!r} "
        f"absent). Its only other credit in this tree is the fragment "
        f"'eda_log_check)' inside a string — vibe-ic#2080.")


def test_the_dispatch_follows_the_step_that_writes_the_log() -> None:
    """The gate reads a file; it must run after the step that writes it, and it
    must be handed THAT step's verdict rather than a later one."""
    src = Path(DOSR.__file__).read_text(errors="replace")
    synth = src.index("step_yosys_synth, project, args.top_name, args.container")
    here = src.index(_DISPATCH)
    assert synth < here, (
        "the synth-log audit moved ahead of the step that writes the log; it "
        "would then judge the PREVIOUS run's file, or none.")
    qsf = src.index("plan.append(step_qsf_gen(project, args.top_name, ic_class))")
    assert here < qsf, (
        "the synth-log audit no longer sits immediately after the synth "
        "dispatch, so `plan[-1]` is some other step's verdict and the "
        "NOT_APPLICABLE discriminator is reading the wrong row.")


def test_the_gate_left_both_unwired_registers() -> None:
    wired = json.loads((_PROGRAMS / "gate_is_wired_baseline.json").read_text())
    assert _GATE not in wired["unwired"], (
        "gate_is_wired_baseline.json still records this gate as unwired.")
    checkers = json.loads(
        (_PROGRAMS / "checker_execution_wiring_baseline.json").read_text())
    assert _GATE + ".py" not in checkers["known"], (
        "checker_execution_wiring_baseline.json still records this checker as "
        "test-only; its audit FAILs on a recorded checker that gained a runner.")
    assert _GATE + ".py" not in (checkers.get("triage") or {}), (
        "the triage note outlived its `known` row.")


def test_the_step_can_never_move_the_run_verdict() -> None:
    src = Path(DOSR.__file__).read_text(errors="replace")
    step = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef)
                and n.name == "step_synth_log_audit")
    statuses = {c.args[1].value for c in ast.walk(step)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "StepResult" and len(c.args) >= 2
                and isinstance(c.args[1], ast.Constant)}
    assert statuses == {"ADVISORY"}, (
        f"step_synth_log_audit returns {sorted(statuses)}; vibe-ic#2080 wires a "
        f"recorded-unwired gate ADVISORY unless its docstring declares it "
        f"BLOCKING, and this gate's does not.")


# ---------------------------------------------------------------------------
# THE VERDICT MOVES ON THE LOG'S CONTENT — BOTH DIRECTIONS
# ---------------------------------------------------------------------------
def test_a_log_carrying_the_tools_own_stat_table_is_a_PASS(
        tmp_path: Path) -> None:
    p = _project(tmp_path, _REAL_STAT_LOG)
    row = _row(DOSR.step_synth_log_audit(p, _synth("PASS")))
    assert row["verdict"] == "PASS", row
    assert row["expect_matched"], row


def test_an_empty_log_beside_a_passing_synth_is_a_FINDING(
        tmp_path: Path) -> None:
    """The exact shape `step_yosys_synth`'s own comment names: the
    docker-fallback path "can return rc=0 with an empty stdout capture". The
    guard beside it refuses to write ACCOUNTING for that; nothing said anything
    about the LOG, which is what a later reader has."""
    p = _project(tmp_path, "")
    row = _row(DOSR.step_synth_log_audit(p, _synth("PASS")))
    assert row["verdict"] == "FINDING", row
    assert row["categories"] == ["EMPTY_LOG"], row


def test_an_absent_log_after_a_synth_that_ran_is_a_FINDING(
        tmp_path: Path) -> None:
    p = _project(tmp_path, None)
    row = _row(DOSR.step_synth_log_audit(p, _synth("PASS")))
    assert row["verdict"] == "FINDING", row
    assert row["categories"] == ["LOG_MISSING"], row


def test_a_log_without_the_tools_accounting_is_a_FINDING(
        tmp_path: Path) -> None:
    """Non-empty is not the same as complete. A log that stops before `stat`
    is a run that did not finish saying what it produced."""
    p = _project(tmp_path, "Yosys 0.33\n-- Running command `read_verilog` --\n")
    row = _row(DOSR.step_synth_log_audit(p, _synth("PASS")))
    assert row["verdict"] == "FINDING", row
    assert row["categories"] == ["EXPECTED_NOT_FOUND"], row


# ---------------------------------------------------------------------------
# THE DISCRIMINATOR THAT MUST NOT BE GOT WRONG
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", _NOT_ATTEMPTED)
def test_a_design_that_never_synthesised_is_NOT_APPLICABLE_not_a_FINDING(
        tmp_path: Path, status: str) -> None:
    """A pure-analog design has no digital RTL and `step_yosys_synth` answers
    SKIP by design. Judging the log's ABSENCE would accuse every one of them;
    judging it as a skip would launder a real missing artefact. The synth
    step's own verdict is the only thing that can tell the two apart, and it is
    passed in rather than re-derived."""
    p = _project(tmp_path, None)
    row = _row(DOSR.step_synth_log_audit(p, _synth(status)))
    assert row["verdict"] == "NOT_APPLICABLE", row
    assert row["synth_status"] == status


def test_this_file_measures_the_same_log_and_the_same_skip_set() -> None:
    """MEMBERSHIP, both ways, against the shipped constants.

    A literal here that drifted from the runner's would leave this file green
    while measuring a file the runner never writes and a skip set it does not
    use. `_spf.REFUSAL_STATUS` is compared rather than spelled twice: a BLOCKED
    step never ran, so it never owed a log, and a rename there must not
    silently start accusing every refused run.
    """
    assert _SYNTH_LOG_REL == DOSR._SYNTH_LOG_REL
    assert set(_NOT_ATTEMPTED) == set(DOSR._SYNTH_NOT_ATTEMPTED)
    assert _spf.REFUSAL_STATUS in DOSR._SYNTH_NOT_ATTEMPTED


def test_a_synth_that_FAILED_still_owes_its_log(tmp_path: Path) -> None:
    """FAIL means the tool ran and the step judged the result. The log is the
    evidence for that judgement and must still be there — this is the direction
    a "skip when anything went wrong" rule would silently lose."""
    p = _project(tmp_path, None)
    row = _row(DOSR.step_synth_log_audit(p, _synth("FAIL")))
    assert row["verdict"] == "FINDING", row


def test_no_reject_pattern_is_passed(tmp_path: Path) -> None:
    """Deliberate, and stated in the row rather than left to inference.

    This step has a slang fallback frontend whose diagnostics legitimately
    contain the word `error`; deciding which strings in a synthesis log are
    fatal is a contract about the tool and the design, and inventing one here
    would manufacture findings on correct runs.
    """
    p = _project(tmp_path, _REAL_STAT_LOG + "ERROR: harmless downstream note\n")
    row = _row(DOSR.step_synth_log_audit(p, _synth("PASS")))
    assert row["reject_pattern"] is None
    assert row["verdict"] == "PASS", row


def test_the_verdict_report_is_published_under_reports_phase2_gates(
        tmp_path: Path) -> None:
    p = _project(tmp_path, _REAL_STAT_LOG)
    res = DOSR.step_synth_log_audit(p, _synth("PASS"))
    out = p / "reports/phase2/gates/synth_log_audit.json"
    assert out.is_file(), sorted(str(x) for x in p.rglob("*.json"))
    assert res.output_files == [str(out)]
    assert json.loads(out.read_text())["program"] == _GATE
