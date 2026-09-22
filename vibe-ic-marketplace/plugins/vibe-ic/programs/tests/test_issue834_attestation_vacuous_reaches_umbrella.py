#!/usr/bin/env python3
"""Regression for #834 — `agent_report_sha256_attestation_check` returned rc 0
for `VACUOUS_PASS`, and its umbrella keys the vacuous tier on rc 2.

THE TWO HALVES THAT DISAGREED
=============================
`flow_compliance_check` registers this gate in `_STRUCTURAL_RTL_GATES`. The
driver for that tuple, `_run_structural_rtl_gates`, reads the gate's EXIT CODE
and nothing else:

    rc 0 -> a PASS gate record  (counted by `_p0_passed_count`)
    rc 1 -> FAIL
    rc 2 -> a SKIP record, `skip_kind: input-missing`

The gate computed the verdict `VACUOUS_PASS`, printed it, and returned 0. So
"there was no report file" and "there were no canonical artefacts on disk"
both arrived at the umbrella as an ordinary executed PASS — the same record a
project gets when every SOF / GDS / netlist on disk is attested and every hash
matches.

MEASURED BEFORE THE FIX, through the real umbrella on a project holding one
trivial RTL file and a `reports/final_summary.md` with no hashes:

    {"name": "agent_report_sha256_attestation_check",
     "verdict": "PASS", "evidence": {"exit_code": 0}}

AFTER: verdict `SKIP`, `exit_code 2`, `skip_kind input-missing` — out of the
executed-PASS numerator, which is the point.

WHAT IS AND IS NOT ASSERTED HERE
================================
The two rc-2 cases are the fix. The rc-0 and rc-1 cases are CONTROLS and pass
identically before and after: a change that moved a real PASS or a real FAIL
would be a different bug, not this one. The stdout-sentinel test is a
no-regression pin — the second (weaker) channel `_stdout_signals_vacuous`
reads must survive, because `_check_program_exit_zero` on the per-step path
still uses it.

chip-AGNOSTIC: generic synthetic fixtures; no vendor / SKU / IC literal.
"""
from __future__ import annotations

import hashlib
import subprocess
import pathlib
import pytest
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _vacuous_exit as _vx  # noqa: E402
import flow_compliance_check as F  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

_GATE = _PROGRAMS / "agent_report_sha256_attestation_check.py"
_GATE_NAME = "agent_report_sha256_attestation_check"

def _run_gate(project: Path) -> subprocess.CompletedProcess:
    return _pr.run([sys.executable, str(_GATE), str(project)],
                          capture_output=True, text=True)


def _report(project: Path, body: str) -> None:
    (project / "reports").mkdir(parents=True, exist_ok=True)
    (project / "reports" / "final_summary.md").write_text(body,
                                                          encoding="utf-8")


def _artefact(project: Path, rel: str, body: bytes) -> str:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# The fix: both VACUOUS branches must reach the umbrella's vacuous channel.
# ---------------------------------------------------------------------------
def test_no_report_file_exits_with_the_vacuous_rc(tmp_path):
    """No canonical report anywhere — the gate opened nothing."""
    proj = tmp_path / "proj"
    proj.mkdir()
    r = _run_gate(proj)
    assert r.returncode == _vx.RC_VACUOUS, (
        f"a gate that examined nothing must exit {_vx.RC_VACUOUS}, not "
        f"{r.returncode} — the umbrella reads the exit code and credits "
        f"rc 0 as an executed PASS. stdout={r.stdout!r}")


def test_no_canonical_artefacts_exits_with_the_vacuous_rc(tmp_path):
    """A report exists, but the project has produced no artefact to attest."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _report(proj, "# summary\n\nNo canonical artefacts yet.\n")
    r = _run_gate(proj)
    assert r.returncode == _vx.RC_VACUOUS, (
        f"pre-output project: examined nothing, must exit "
        f"{_vx.RC_VACUOUS}, got {r.returncode}. stdout={r.stdout!r}")


def _assert_no_executed_pass_for_a_vacuous_run(rec):
    """#834's property, in ONE place so the mutation arm can drive the same code.

    Factored because the arm below must demonstrate this going RED. An arm that
    re-states the assertions inline can drift from the test it claims to guard,
    and an arm that merely describes the mutant proves nothing at all.
    """
    assert rec["verdict"] != "PASS", (
        "a gate that examined nothing must not hold a PASS record — that is "
        f"what puts it in the executed-PASS numerator. record={rec}")
    assert rec["verdict"] == "BLOCKED", rec
    assert rec["reason_class"] == "BLOCKED_BY_UPSTREAM", rec
    assert rec["evidence"].get("exit_code") == _vx.RC_VACUOUS, rec
    assert rec["evidence"].get("skip_kind") == "input-missing", rec
    assert rec["verdict"] != "NOT_INVOCABLE", rec


def test_the_vacuous_rc_is_the_one_the_umbrella_reads():
    """Pins the two sides of the contract to ONE constant, not to a literal.

    The defect was two literals in two files drifting apart. Reading the
    consumer's own convention constant is what stops a third one appearing.
    """
    assert _vx.RC_VACUOUS == 2
    # RE-PINNED TO THE NEW CONSUMER (R-0915-130), assertion unchanged in kind.
    # The gate no longer sits in `_STRUCTURAL_RTL_GATES` — membership meant every
    # mid-run invocation asked it ten minutes before `reports/final_summary.md`
    # existed — so the thing to assert is that the consumer which DOES ask it
    # exists and is reached. The property #834 defends is unchanged: the vacuous
    # rc must arrive at a record, not merely be printed.
    assert _GATE_NAME not in F._STRUCTURAL_RTL_GATES, (
        "the gate was moved out of the umbrella by R-0915-130; if it is back in "
        "the registry it is being asked before its input exists again")
    assert hasattr(F, "attestation_gate_record"), (
        "the relocated gate has no consumer, so nothing records its rc")
    import final_report_generate as _frg
    _src = pathlib.Path(_frg.__file__).read_text()
    # THE CALL, not the NAME. A first draft asserted the bare identifier and was
    # satisfied by the explanatory COMMENT beside the call — so replacing the call
    # with a literal dict left this green. Assert the invocation, and strip
    # comments first so no future comment can satisfy it either.
    _code = "\n".join(
        line.split("#", 1)[0] for line in _src.splitlines())
    assert "attestation_gate_record(project)" in _code, (
        "the late consumer must CALL the record builder, not merely mention it: "
        "a consumer that prints a verdict without recording it is the pre-#834 "
        "state, where a vacuous run is counted nowhere")


# ---------------------------------------------------------------------------
# CONTROLS — these pass identically before and after the fix.
# ---------------------------------------------------------------------------
def test_attested_artefact_still_exits_zero(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    h = _artefact(proj, "phase2/stage2/synth/netlist.v",
                  b"module top(); endmodule\n")
    _report(proj, f"# summary\n\n| netlist | `sha256:{h}` |\n")
    r = _run_gate(proj)
    assert r.returncode == 0, (
        f"a real, fully-attested artefact set is a real PASS. "
        f"stdout={r.stdout!r} stderr={r.stderr!r}")


def test_unattested_artefact_still_exits_one(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _artefact(proj, "phase2/stage2/synth/netlist.v",
              b"module top(); endmodule\n")
    _report(proj, "# summary\n\n| netlist | `sha256:"
                  + "0" * 64 + "` |\n")
    r = _run_gate(proj)
    assert r.returncode == 1, (
        f"an artefact on disk whose hash is not declared is a real FAIL. "
        f"stdout={r.stdout!r} stderr={r.stderr!r}")


def test_stdout_vacuous_sentinel_channel_is_preserved(tmp_path):
    """The weaker channel must survive the fix, not be traded for the rc.

    `_check_program_exit_zero` (the per-step path) reads this line; the token
    is matched at LINE START, which is why `[VACUOUS_PASS]` would not do.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    r = _run_gate(proj)
    assert F._stdout_signals_vacuous(r.stdout), (
        f"the line-start VACUOUS_PASS sentinel must still be emitted; "
        f"stdout={r.stdout!r}")


# ---------------------------------------------------------------------------
# The consequence, measured through the REAL umbrella (not re-derived).
# ---------------------------------------------------------------------------
def test_umbrella_no_longer_records_an_executed_pass_for_a_vacuous_run(
        tmp_path):
    """Drives `_run_structural_rtl_gates` and reads the gate's own record.

    This is the assertion the issue is about: the gate's conclusion has to
    arrive at the machine that publishes the X-of-Y figure, not merely be
    printed. Before the fix this record read
    `verdict=PASS, exit_code=0` on a project with no artefacts at all.
    """
    proj = tmp_path / "proj"
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text(
        "module top(input clk, output reg q);\n"
        "  always @(posedge clk) q <= ~q;\n"
        "endmodule\n")
    _report(proj, "# summary\n\nNo canonical artefacts yet.\n")

    # RE-PINNED TO THE NEW CONSUMER. Same record, same four assertions below;
    # only the machine that produces it moved. `attestation_gate_record` reuses the
    # umbrella's own `_p0_gate_record` and `infer_nonverdict_reason`, so the rc
    # mapping still has ONE definition — which is what this file's sibling test
    # ("two literals in two files drifting apart") exists to prevent.
    rec = F.attestation_gate_record(proj)
    assert rec["name"] == _GATE_NAME, rec

    # The four assertions live in `_assert_no_executed_pass_for_a_vacuous_run`
    # so the mutation arm at the end of this file drives the SAME code and can
    # show it going red. The last one is the point that it must NOT be misread as
    # the caller's own invocation defect: the gate DID return a verdict.
    _assert_no_executed_pass_for_a_vacuous_run(rec)


def test_a_consumer_that_swallows_the_vacuous_rc_is_red(tmp_path, monkeypatch):
    """THE MUTATION ARM the move owes, and it must go RED, not describe a mutant.

    A relocated gate is only as good as the record it produces. If the consumer
    maps a vacuous run onto a PASS — or drops the exit code — the run re-enters the
    executed-PASS numerator silently, which is the pre-#834 state. So: patch the
    consumer to swallow the rc, then drive the SAME property helper the test above
    uses and require it to RAISE. A first draft of this arm merely asserted the
    mutant's own claim and passed; an arm that cannot fail guards nothing.
    """
    proj = tmp_path / "proj"
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text("module top(); endmodule\n")
    _report(proj, "# summary\n\nNo canonical artefacts yet.\n")

    # the honest consumer satisfies the property
    _assert_no_executed_pass_for_a_vacuous_run(F.attestation_gate_record(proj))

    # the swallowing consumer must not
    monkeypatch.setattr(
        F, "attestation_gate_record",
        lambda project, gate_name=_GATE_NAME: {
            "name": gate_name, "verdict": "PASS", "reason_class": "",
            "message": "", "evidence": {"exit_code": 0}})
    with pytest.raises(AssertionError):
        _assert_no_executed_pass_for_a_vacuous_run(
            F.attestation_gate_record(proj))
