"""Transactional post-route SDR repair must either prove an improved route or restore.

These are Tcl-level tests because the implementation is emitted OpenROAD Tcl.
The tool actions are mocked, but the immutable checkpoint, receipt, strict DRC
comparison, connectivity refusal, and database restoration all execute.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402


tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")


def _drc(n: int) -> str:
    return "".join("violation type: spacing\n" for _ in range(n))


def _run(tmp_path: Path, *, before: int, after: int,
         connectivity_ok: bool = True, nonfatal: bool = False,
         route_ok: bool = True, candidate_report: str = "count",
         placement_violations: int | None = 0):
    """``candidate_report``: ``count`` writes ``after`` violations; ``absent``
    deletes the report; ``garbage`` writes bytes that are not the tool's record
    grammar.  The last two are what a -1 in the receipt is supposed to mean."""
    out = tmp_path / "pnr"
    out.mkdir()
    report = out / R.ROUTER_DRC_REPORT_NAME
    report.write_text(_drc(before))
    begin = R._postroute_sdr_transaction_begin_tcl(str(out))
    finish = R._postroute_sdr_transaction_finish_tcl()
    connectivity = "return" if connectivity_ok else 'error "disconnected"'
    script = tmp_path / "trial.tcl"
    script.write_text(
        "set ::db BASE\n"
        "proc write_def {path} {\n"
        "  set f [open $path w]; puts -nonewline $f $::db; close $f\n"
        "}\n"
        "proc read_def {path} {\n"
        "  set f [open $path r]; set ::db [read $f]; close $f\n"
        "}\n"
        f"proc check_connectivity {{}} {{ {connectivity} }}\n"
        + begin
        + "set ::db CANDIDATE\n"
        + "set _sdr_tx_mutated 1\n"
        + "set _sdr_tx_route_ok 1\n"
        + ("set _sdr_tx_error 1\n" if nonfatal else "")
        + ("" if route_ok else "set _sdr_tx_route_ok 0\n")
        + ("" if placement_violations is None
           else f"set _sdr_pv {placement_violations}\n")
        + {
            "count": f"set f [open {{{report}}} w]; "
                     f"puts -nonewline $f {{{_drc(after)}}}; close $f\n",
            "absent": f"file delete -force {{{report}}}\n",
            "garbage": f"set f [open {{{report}}} w]; "
                       f"puts -nonewline $f {{not the router grammar}}; close $f\n",
        }[candidate_report]
        + finish
        + 'puts "FINAL:$::db ABORT:$::_vic_postroute_transaction_failed"\n')
    return subprocess.run([tclsh, str(script)], text=True, capture_output=True), out


@needs_tclsh
def test_accepts_only_connected_strictly_improved_candidate(tmp_path):
    run, out = _run(tmp_path, before=4, after=2)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc=4 -> 2" in run.stdout
    assert "FINAL:CANDIDATE ABORT:0" in run.stdout
    receipt = (out / "sdr_transaction" / "receipt.tsv").read_text()
    assert "ACCEPTED\tstrict_router_drc_improvement\t4\t2" in receipt


@needs_tclsh
def test_accepts_drv_candidate_when_clean_router_drc_is_preserved(tmp_path):
    """DRV repair can change cells while a DRC-clean route correctly stays 0."""
    run, out = _run(tmp_path, before=0, after=0)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc clean-preserved (0 -> 0)" in run.stdout
    assert "FINAL:CANDIDATE ABORT:0" in run.stdout
    receipt = (out / "sdr_transaction" / "receipt.tsv").read_text()
    assert "ACCEPTED\trouter_drc_preserved_clean\t0\t0" in receipt


@needs_tclsh
def test_rejects_when_router_drc_does_not_strictly_improve(tmp_path):
    run, out = _run(tmp_path, before=4, after=4)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=router_drc_not_strictly_improved" in run.stdout
    assert "ODB-0251" not in run.stderr
    tx = out / "sdr_transaction"
    assert (tx / "pre_repair.def").read_text() == "BASE"
    assert "REJECTED_UNRESTORABLE\trouter_drc_not_strictly_improved\t4\t4" in (
        tx / "receipt.tsv").read_text()


@needs_tclsh
def test_nonfatal_advisory_over_a_measured_clean_candidate_is_disclosed_not_refused(
        tmp_path):
    """A nonfatal is a DISCLOSURE about the pass, not a fact about the geometry.

    The branch used to read the advisory FLAG and never ask the router.  Here
    the router IS asked and answers 1 against a base of 4 -- strictly better,
    the very rule the no-advisory path applies -- so the candidate is committed
    and the advisory is RECORDED beside it, under its own status so no reader
    mistakes it for an un-advised acceptance.
    """
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_REJECT_CAUSE: nonfatal_during_repair=1 candidate_route_failed=0" in run.stdout
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC: before=4 after=1" in run.stdout
    assert "SDR_TRANSACTION_ACCEPTED_WITH_ADVISORY" in run.stdout
    assert "REJECTED_UNRESTORABLE" not in run.stdout
    assert "FINAL:CANDIDATE ABORT:0" in run.stdout
    assert "ODB-0251" not in run.stderr
    assert ("ACCEPTED_WITH_ADVISORY\tnonfatal_disclosed_router_drc"
            "_and_placement_measured_clean\t4\t1") in (out / "sdr_transaction" / "receipt.tsv").read_text()
    assert (out / "sdr_transaction" / "accepted_router.drc.rpt").exists()


@needs_tclsh
def test_nonfatal_advisory_over_a_measured_clean_zero_candidate_is_disclosed(tmp_path):
    """`after == 0` is clean even when the base was clean too (0 -> 0)."""
    run, out = _run(tmp_path, before=0, after=0, nonfatal=True)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED_WITH_ADVISORY" in run.stdout
    assert ("ACCEPTED_WITH_ADVISORY\tnonfatal_disclosed_router_drc"
            "_and_placement_measured_clean\t0\t0") in (out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
def test_nonfatal_advisory_over_a_WORSE_measured_candidate_is_still_refused(tmp_path):
    """MEASURED and not better is still a refusal -- #2240's honesty is kept.

    MEASURED (sha256/sky130A, lane icsha, run2): the branch fired with error=1,
    route_ok=1, and the router answered 0 -> 1 (one li1 metal-spacing violation
    the pass had introduced).  That candidate must NOT be committed, and the
    receipt must now say WHY in numbers instead of a blanket -1.
    """
    run, out = _run(tmp_path, before=0, after=1, nonfatal=True)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC: before=0 after=1" in run.stdout
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=nonfatal_or_route_error" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t0\t1" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()
    # the base route the run falls back to is still the one on disk
    assert (out / "sdr_transaction" / "pre_repair.def").read_text() == "BASE"


@needs_tclsh
def test_nonfatal_advisory_over_an_equal_measured_candidate_is_still_refused(tmp_path):
    """`after == before` and non-zero is not an improvement, advisory or not."""
    run, out = _run(tmp_path, before=4, after=4, nonfatal=True)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=nonfatal_or_route_error" in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t4\t4" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
@pytest.mark.parametrize("candidate_report", ["absent", "garbage"])
def test_nonfatal_receipt_keeps_minus_one_only_for_an_unreadable_report(
        tmp_path, candidate_report):
    """-1 keeps exactly ONE meaning: the candidate report could not be read."""
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    candidate_report=candidate_report)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC_UNREADABLE" in run.stdout
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC: " not in run.stdout
    # UNMEASURED is never disclosed-and-committed: nothing is known about the
    # geometry, so the session still stops (the #2240 arm).
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=nonfatal_or_route_error" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t4\t-1" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
def test_reject_cause_separates_a_nonfatal_from_a_failed_candidate_route(tmp_path):
    """One reason string carried two independent facts; the cause line splits them."""
    run, _ = _run(tmp_path, before=4, after=4, nonfatal=False, route_ok=False)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_REJECT_CAUSE: nonfatal_during_repair=0 candidate_route_failed=1" in run.stdout
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=nonfatal_or_route_error" in run.stdout


@needs_tclsh
def test_a_failed_candidate_route_is_never_committed_however_clean_the_report_reads(
        tmp_path):
    """route_ok == 0 means there is no routed geometry to accept.

    The stale report on disk can read 0 -- it is the BASE route's report, not
    the candidate's -- and that must not be mistaken for a clean candidate.
    """
    run, out = _run(tmp_path, before=0, after=0, nonfatal=False, route_ok=False)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_ACCEPTED" not in run.stdout
    assert "SDR_TRANSACTION_REJECTED_UNRESTORABLE: reason=nonfatal_or_route_error" in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t0\t0" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
def test_does_not_call_unavailable_openroad_connectivity_command(tmp_path):
    """Pinned OpenROAD has no ``check_connectivity`` command; LVS owns that proof."""
    run, out = _run(tmp_path, before=0, after=0, connectivity_ok=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc clean-preserved (0 -> 0)" in run.stdout
    assert "FINAL:CANDIDATE ABORT:0" in run.stdout
    assert "check_connectivity" not in run.stdout
    assert "ACCEPTED\trouter_drc_preserved_clean\t0\t0" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
def test_disclosure_needs_the_PLACEMENT_measurement_too_not_router_drc_alone(tmp_path):
    """Router DRC cannot see the failure #2240 guards.

    A partial ``repair_design`` leaves UN-LEGALISED swaps under the old
    routing; MEASURED on another design as 4 overlapping instances that
    streamed out as 15 FEOL sign-off violations and 2 LVS extraction
    overlaps.  None of those are ROUTING rules, so a clean router report would
    certify them.  The disclosure therefore needs BOTH measurements.
    """
    # router measured clean (4 -> 1), placement measured ILLEGAL
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    placement_violations=3)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_CANDIDATE_PLACEMENT_VIOLATIONS: 3" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t4\t1" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()


@needs_tclsh
def test_disclosure_refuses_when_the_pass_never_reached_check_placement(tmp_path):
    """No ``_sdr_pv`` at all is UNMEASURED, which refuses like every other."""
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    placement_violations=None)
    assert run.returncode != 0
    assert "SDR_TRANSACTION_PLACEMENT_UNMEASURED" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_UNRESTORABLE\tnonfatal_or_route_error\t4\t1" in (
        out / "sdr_transaction" / "receipt.tsv").read_text()
