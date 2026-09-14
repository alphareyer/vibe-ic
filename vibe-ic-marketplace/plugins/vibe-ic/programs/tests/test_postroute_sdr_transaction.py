"""The post-route SDR pass is a CHECKPOINT-AND-CHILD transaction (#2253).

These are Tcl-level tests because the implementation is emitted OpenROAD Tcl.
The tool actions are mocked, but the immutable checkpoint, the child
invocation, the child receipt ingest, the strict DRC comparison, the
placement requirement and the adopt handoff all execute.

WHAT CHANGED, AND WHY THE OLD CONSEQUENCE WAS THE DEFECT.  MEASURED on
sha256 x sky130A over four runs on four trees (lane icsha, F041/F044): the
detailed route closed clean every time, the OPTIONAL end-of-flow DRV repair
then mutated the design inside the SAME session, and the transaction refused
the candidate -- CORRECTLY, it was worse (router DRC 0 -> 1, 0 -> 2).  Each
refusal `error`ed out of the script, so `routed.def` was never written and six
downstream sign-off gates were blocked behind one absent file.  The decision
was right and the consequence was catastrophic.

So the candidate is now built in a CHILD session seeded from the checkpoint,
and the shipping session is never mutated at all.  A refusal costs the run
nothing: the clean route it still holds is the one that ships.  An acceptance
cannot be pulled back into the live database (ODB-0251), so it HANDS OFF --
the session names the candidate and exits without shipping anything, and the
Python side finishes the tail from that candidate.
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

STAGE = "postroute_drv_repair"
TXN = R._SDR_TXN_DIRS[STAGE]


def _drc(n: int) -> str:
    return "".join("violation type: spacing\n" for _ in range(n))


def _run(tmp_path: Path, *, before: int, after: int,
         connectivity_ok: bool = True, nonfatal: bool = False,
         route_ok: bool = True, candidate_report: str = "count",
         placement_violations: int | None = 0,
         child_dies: bool = False, child_receipt: str = "full"):
    """Drive begin -> parent child-call -> finish with a SIMULATED child.

    ``candidate_report``: ``count`` writes ``after`` violations into the
    CANDIDATE's report; ``absent`` writes none; ``garbage`` writes bytes that
    are not the tool's record grammar.  The last two are what a -1 in the
    receipt is supposed to mean.
    ``child_dies``: the child process itself fails and writes nothing.
    ``child_receipt``: ``full`` | ``short`` (a truncated row) | ``absent``.
    """
    out = tmp_path / "pnr"
    out.mkdir()
    txn = out / TXN
    # The SHIPPING session's own router report, describing the route this
    # session holds.  Nothing in the transaction may overwrite it.
    report = out / R.ROUTER_DRC_REPORT_NAME
    report.write_text(_drc(before))
    cand_rpt = txn / R._SDR_CANDIDATE_DRC_NAME
    cand_def = txn / R._SDR_CANDIDATE_DEF_NAME
    receipt = txn / R._SDR_CHILD_RECEIPT_NAME
    pv = "NA" if placement_violations is None else str(placement_violations)
    child_writes = [
        f'  set f [open {{{cand_def}}} w]; puts -nonewline $f CANDIDATE; close $f',
    ]
    if candidate_report == "count":
        child_writes.append(
            f'  set f [open {{{cand_rpt}}} w]; '
            f'puts -nonewline $f {{{_drc(after)}}}; close $f')
    elif candidate_report == "garbage":
        child_writes.append(
            f'  set f [open {{{cand_rpt}}} w]; '
            f'puts -nonewline $f {{not the router grammar}}; close $f')
    if child_receipt == "full":
        child_writes.append(
            f'  set f [open {{{receipt}}} w]; '
            f'puts $f "mutated\\terror\\troute_ok\\tplacement_violations"; '
            f'puts $f "1\\t{1 if nonfatal else 0}\\t{1 if route_ok else 0}\\t{pv}"; '
            f'close $f')
    elif child_receipt == "short":
        child_writes.append(
            f'  set f [open {{{receipt}}} w]; '
            f'puts $f "mutated\\terror\\troute_ok\\tplacement_violations"; '
            f'puts $f "1\\t0"; close $f')
    child_body = ('  error "child openroad died"' if child_dies
                  else "\n".join(child_writes))
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
        # THE CHILD, simulated.  The parent runs it with `exec`; nothing it
        # does touches `::db`, which is the whole point of the re-plumb.
        "proc exec {args} {\n"
        + child_body + "\n"
        "}\n"
        + R._postroute_sdr_transaction_begin_tcl(str(out), STAGE)
        + R._postroute_sdr_parent_child_call_tcl(str(out), STAGE)
        + R._postroute_sdr_transaction_finish_tcl(STAGE)
        + '# Only a REJECTED pass reaches here: an acceptance exits above.\n'
        + 'write_def ' + str(out / "shipped.def") + '\n'
        + 'puts "FINAL:$::db ABORT:$::_vic_postroute_transaction_failed"\n')
    return subprocess.run([tclsh, str(script)], text=True,
                          capture_output=True), out


def _receipt(out: Path) -> str:
    return (out / TXN / "receipt.tsv").read_text()


# ---------------------------------------------------------------- acceptance


@needs_tclsh
def test_accepts_only_connected_strictly_improved_candidate(tmp_path):
    run, out = _run(tmp_path, before=4, after=2)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc=4 -> 2" in run.stdout
    assert "ACCEPTED\tstrict_router_drc_improvement\t4\t2" in _receipt(out)


@needs_tclsh
def test_accepts_drv_candidate_when_clean_router_drc_is_preserved(tmp_path):
    """DRV repair can change cells while a DRC-clean route correctly stays 0."""
    run, out = _run(tmp_path, before=0, after=0)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc clean-preserved (0 -> 0)" in run.stdout
    assert "ACCEPTED\trouter_drc_preserved_clean\t0\t0" in _receipt(out)


@needs_tclsh
def test_an_accepted_pass_ships_the_candidate_and_names_its_measurement(
        tmp_path):
    """#2253 — acceptance HANDS OFF, and says exactly what it accepted.

    The live database cannot take the candidate back (ODB-0251), so the
    session must not ship its own stale one.  It names the candidate file and
    exits WITHOUT writing any sign-off artifact, and the receipt carries the
    numbers the decision was made on.  An accepted pass is therefore never a
    candidate nobody measured.
    """
    run, out = _run(tmp_path, before=4, after=2)
    assert run.returncode == 0, run.stderr
    assert R._SDR_ADOPT_MARKER in run.stdout
    assert f"stage={STAGE}" in run.stdout
    assert str(out / TXN / R._SDR_CANDIDATE_DEF_NAME) in run.stdout
    assert "SDR_TRANSACTION_ADOPT_HANDOFF" in run.stdout
    # it exited BEFORE shipping anything from this database
    assert not (out / "shipped.def").exists()
    assert "FINAL:" not in run.stdout
    assert "ACCEPTED\tstrict_router_drc_improvement\t4\t2" in _receipt(out)
    req = R._sdr_adopt_request(run.stdout)
    assert req is not None and req["stage"] == STAGE


# ----------------------------------------------------------------- refusal


@needs_tclsh
def test_rejects_when_router_drc_does_not_strictly_improve(tmp_path):
    run, out = _run(tmp_path, before=4, after=4)
    assert run.returncode == 0, run.stderr
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=router_drc_not_strictly_improved") in run.stdout
    assert "ODB-0251" not in run.stderr
    tx = out / TXN
    assert (tx / "pre_repair.def").read_text() == "BASE"
    assert "REJECTED_CANDIDATE_DISCARDED\trouter_drc_not_strictly_improved\t4\t4" in (
        tx / "receipt.tsv").read_text()


@needs_tclsh
def test_a_rejected_pass_ships_the_clean_route_and_the_flow_continues(tmp_path):
    """THE DEFECT #2253 FIXES, as a test.

    MEASURED four times on sha256 x sky130A: a correct refusal `error`ed out
    of pnr.tcl, `routed.def` was never written, and drc / lvs / sta_signoff /
    em / hardmacro were all BLOCKED behind that one absent file.  Now the
    refusal discards a file the shipping session never held, the live database
    is still the clean route, the abort flag is CLEAR so the antenna / fill /
    min-area tail runs, and the sign-off is written.
    """
    run, out = _run(tmp_path, before=0, after=2)
    assert run.returncode == 0, run.stderr
    assert "FINAL:BASE ABORT:0" in run.stdout
    assert (out / "shipped.def").read_text() == "BASE"
    assert "the checkpointed route is UNCHANGED in this session" in run.stdout


@needs_tclsh
def test_the_checkpoint_is_byte_identical_to_what_a_rejected_pass_ships(
        tmp_path):
    """The claim the whole re-plumb rests on, measured rather than asserted."""
    run, out = _run(tmp_path, before=0, after=2)
    assert run.returncode == 0, run.stderr
    checkpoint = (out / TXN / "pre_repair.def").read_bytes()
    shipped = (out / "shipped.def").read_bytes()
    assert checkpoint == shipped
    # and it is NOT the candidate the child built
    assert (out / TXN / R._SDR_CANDIDATE_DEF_NAME).read_bytes() != shipped


@needs_tclsh
def test_the_shipping_sessions_own_route_report_is_not_overwritten(tmp_path):
    """The candidate's numbers never get written over the shipped route's.

    The in-session pass asked the router for its report on the SAME path the
    base route used, so a candidate that was then thrown away still left the
    shipped route described by ITS numbers.  The child writes its own.
    """
    run, out = _run(tmp_path, before=3, after=9)
    assert run.returncode == 0, run.stderr
    assert (out / R.ROUTER_DRC_REPORT_NAME).read_text() == _drc(3)
    assert (out / TXN / R._SDR_CANDIDATE_DRC_NAME).read_text() == _drc(9)


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
    assert "REJECTED_CANDIDATE_DISCARDED" not in run.stdout
    assert "ODB-0251" not in run.stderr
    assert ("ACCEPTED_WITH_ADVISORY\tnonfatal_disclosed_router_drc"
            "_and_placement_measured_clean\t4\t1") in _receipt(out)
    assert (out / TXN / "accepted_router.drc.rpt").exists()


@needs_tclsh
def test_nonfatal_advisory_over_a_measured_clean_zero_candidate_is_disclosed(tmp_path):
    """`after == 0` is clean even when the base was clean too (0 -> 0)."""
    run, out = _run(tmp_path, before=0, after=0, nonfatal=True)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED_WITH_ADVISORY" in run.stdout
    assert ("ACCEPTED_WITH_ADVISORY\tnonfatal_disclosed_router_drc"
            "_and_placement_measured_clean\t0\t0") in _receipt(out)


@needs_tclsh
def test_nonfatal_advisory_over_a_WORSE_measured_candidate_is_still_refused(tmp_path):
    """MEASURED and not better is still a refusal -- #2240's honesty is kept.

    MEASURED (sha256/sky130A, lane icsha, run2): the branch fired with error=1,
    route_ok=1, and the router answered 0 -> 1 (one li1 metal-spacing violation
    the pass had introduced).  That candidate must NOT be committed, and the
    receipt must say WHY in numbers instead of a blanket -1.  What it must NOT
    do any more is take the run down with it.
    """
    run, out = _run(tmp_path, before=0, after=1, nonfatal=True)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC: before=0 after=1" in run.stdout
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=nonfatal_or_route_error") in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t0\t1" in _receipt(out)
    # the base route the run falls back to is still the one on disk AND the
    # one in the live database
    assert (out / TXN / "pre_repair.def").read_text() == "BASE"
    assert "FINAL:BASE ABORT:0" in run.stdout


@needs_tclsh
def test_nonfatal_advisory_over_an_equal_measured_candidate_is_still_refused(tmp_path):
    """`after == before` and non-zero is not an improvement, advisory or not."""
    run, out = _run(tmp_path, before=4, after=4, nonfatal=True)
    assert run.returncode == 0, run.stderr
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=nonfatal_or_route_error") in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t4\t4" in _receipt(out)


@needs_tclsh
@pytest.mark.parametrize("candidate_report", ["absent", "garbage"])
def test_nonfatal_receipt_keeps_minus_one_only_for_an_unreadable_report(
        tmp_path, candidate_report):
    """-1 keeps exactly ONE meaning: the candidate report could not be read."""
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    candidate_report=candidate_report)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC_UNREADABLE" in run.stdout
    assert "SDR_TRANSACTION_CANDIDATE_ROUTER_DRC: " not in run.stdout
    # UNMEASURED is never disclosed-and-committed: nothing is known about the
    # geometry, so the candidate is refused (the #2240 arm).
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=nonfatal_or_route_error") in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t4\t-1" in _receipt(out)
    assert "FINAL:BASE ABORT:0" in run.stdout


@needs_tclsh
@pytest.mark.parametrize("candidate_report", ["absent", "garbage"])
def test_a_candidate_with_an_unreadable_report_is_rejected_on_the_clean_arm(
        tmp_path, candidate_report):
    """Same refusal with NO advisory: an unreadable report is never a count."""
    run, out = _run(tmp_path, before=4, after=1,
                    candidate_report=candidate_report)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ROUTER_DRC_UNREADABLE" in run.stdout
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=unreadable_candidate_router_drc") in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tunreadable_candidate_router_drc\t4\t-1" in _receipt(out)
    assert "FINAL:BASE ABORT:0" in run.stdout


@needs_tclsh
def test_reject_cause_separates_a_nonfatal_from_a_failed_candidate_route(tmp_path):
    """One reason string carried two independent facts; the cause line splits them."""
    run, _ = _run(tmp_path, before=4, after=4, nonfatal=False, route_ok=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_REJECT_CAUSE: nonfatal_during_repair=0 candidate_route_failed=1" in run.stdout
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=nonfatal_or_route_error") in run.stdout


@needs_tclsh
def test_a_failed_candidate_route_is_never_committed_however_clean_the_report_reads(
        tmp_path):
    """route_ok == 0 means there is no routed geometry to accept."""
    run, out = _run(tmp_path, before=0, after=0, nonfatal=False, route_ok=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED" not in run.stdout
    assert ("SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: "
            "reason=nonfatal_or_route_error") in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t0\t0" in _receipt(out)


@needs_tclsh
def test_does_not_call_unavailable_openroad_connectivity_command(tmp_path):
    """Pinned OpenROAD has no ``check_connectivity`` command; LVS owns that proof."""
    run, out = _run(tmp_path, before=0, after=0, connectivity_ok=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc clean-preserved (0 -> 0)" in run.stdout
    assert "check_connectivity" not in run.stdout
    assert "ACCEPTED\trouter_drc_preserved_clean\t0\t0" in _receipt(out)


@needs_tclsh
def test_disclosure_needs_the_PLACEMENT_measurement_too_not_router_drc_alone(tmp_path):
    """Router DRC cannot see the failure #2240 guards.

    A partial ``repair_design`` leaves UN-LEGALISED swaps under the old
    routing; MEASURED on another design as 4 overlapping instances that
    streamed out as 15 FEOL sign-off violations and 2 LVS extraction
    overlaps.  None of those are ROUTING rules, so a clean router report would
    certify them.  The disclosure therefore needs BOTH measurements, and the
    child receipt is what carries the placement one across the process
    boundary.
    """
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    placement_violations=3)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_CANDIDATE_PLACEMENT_VIOLATIONS: 3" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t4\t1" in _receipt(out)


@needs_tclsh
def test_disclosure_refuses_when_the_pass_never_reached_check_placement(tmp_path):
    """No ``_sdr_pv`` at all is UNMEASURED, which refuses like every other.

    Across the process boundary that is the literal ``NA`` the child writes --
    never a 0, which would read as "measured legal".
    """
    run, out = _run(tmp_path, before=4, after=1, nonfatal=True,
                    placement_violations=None)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_PLACEMENT_UNMEASURED" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error\t4\t1" in _receipt(out)


# ------------------------------------------------- the child cannot answer


@needs_tclsh
def test_a_child_session_that_dies_is_a_refusal_not_a_pass(tmp_path):
    run, out = _run(tmp_path, before=0, after=0, child_dies=True)
    assert run.returncode == 0, run.stderr
    assert "SDR_CHILD_SESSION_NONFATAL" in run.stdout
    assert "SDR_CHILD_RECEIPT_UNREADABLE" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "REJECTED_CANDIDATE_DISCARDED\tnonfatal_or_route_error" in _receipt(out)
    assert "FINAL:BASE ABORT:0" in run.stdout


@needs_tclsh
@pytest.mark.parametrize("child_receipt", ["absent", "short"])
def test_a_child_that_cannot_say_what_it_did_is_a_refusal(tmp_path,
                                                          child_receipt):
    """A candidate nobody measured is refused through the SAME decision."""
    run, out = _run(tmp_path, before=0, after=0, child_receipt=child_receipt)
    assert run.returncode == 0, run.stderr
    assert "SDR_CHILD_RECEIPT_UNREADABLE" in run.stdout
    assert "SDR_TRANSACTION_PLACEMENT_UNMEASURED" in run.stdout
    assert "ACCEPTED" not in run.stdout
    assert "FINAL:BASE ABORT:0" in run.stdout


# ------------------------------------------------------ the emitted shapes


def test_the_parent_branch_never_mutates_the_shipping_session():
    """The invariant in the emitted text, not only in the run.

    The parent half must contain no resize, no repair and no route command --
    if it did, a refusal would once again have something to roll back.
    """
    parent = R._postroute_sdr_parent_child_call_tcl("/o", STAGE)
    for forbidden in ("repair_design", "repair_timing", "detailed_placement",
                      "global_route", "detailed_route", "write_def"):
        assert forbidden not in parent, forbidden


def test_both_sdr_sites_own_a_distinct_transaction_directory():
    """`begin` starts by deleting its directory, so sharing one destroyed the
    first site's receipt and checkpoint before the run was over."""
    assert len(set(R._SDR_TXN_DIRS.values())) == len(R._SDR_TXN_DIRS) == 2
    a = R._postroute_sdr_transaction_begin_tcl("/o", "postroute_drv_repair")
    b = R._postroute_sdr_transaction_begin_tcl("/o", "postroute_drv_reconverge")
    assert "/o/sdr_transaction}" in a
    assert "/o/sdr_transaction_reconverge}" in b


def test_the_emitted_block_carries_both_roles_of_one_transaction():
    t = R._v1_8_100_signoff_drv_repair_tcl("/o", "BUF", stage=STAGE)
    assert 'if {[info exists ::_vic_sdr_role] && $::_vic_sdr_role eq "child"}' in t
    # the repair recipe exists ONCE, on the child side of that branch: the
    # first call plus its two bounded retries (EST-0104, RSZ-0074).
    assert t.count("repair_design -max_wire_length") == 3
    child_half, _, parent_half = t.partition("# ===== PARENT ROLE")
    assert "repair_design" in child_half
    assert "repair_design" not in parent_half
    assert "exec openroad" in parent_half
    assert "exec openroad" not in child_half


def test_sdr_adopt_request_reads_the_last_marker_or_none():
    assert R._sdr_adopt_request("nothing here\n") is None
    log = ("noise\n"
           f"{R._SDR_ADOPT_MARKER} stage=postroute_drv_repair def=/a/c.def "
           "report=/a/r.rpt txn=sdr_transaction\n"
           "more noise\n")
    req = R._sdr_adopt_request(log)
    assert req == {"stage": "postroute_drv_repair", "def": "/a/c.def",
                   "report": "/a/r.rpt", "txn": "sdr_transaction"}
