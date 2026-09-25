"""#1412 CONTINUED — a WAIVED PnR must still reach the stream-out.

`step_pnr` returns WAIVED (not FAIL) when every residual DRT marker is below
its own layer's LEF MINWIDTH in both dimensions, on the explicit reasoning that
"the sign-off DRC deck runs on the streamed GDS, evaluates merged polygons and
keeps its own verdict". The phase-3 chain test that gates the stream-out was
written when `step_pnr` was binary, and still read `status == "PASS"`, so a
WAIVED PnR was rejected exactly like a failed one and `step_gds` was NEVER
CALLED -- emitting no `gds` row at all, not even a SKIP.

MEASURED, spm x gf180mcuD, plugin 1.17.38, EDA image 0.3.6, host 8HD-9:
  phase3 steps ... pnr WAIVED -> drc SKIP ...   (no `gds` row anywhere)
  drc               SKIP  "GDS missing: phase3/stage3/pnr/spm.gds"
  tapeout_precheck  FAIL  NOT_DETERMINED at KLayout.ReadLayout,
                          refuses_on "the layout file cannot be read as GDSII"
  overall           FAIL  halted_at phase3
while the published v1.14.88 cell for the same IC and PDK recorded
`gds PASS 41.33s (streamout=magic, 2,131,574 bytes)` then `drc PASS
violations=0`.

The gating status is NOT the assertion here. What is asserted is that the
predicate keys off the ARTEFACT evidence `pnr_signoff_writes_complete` -- set by
`step_pnr` on the same branch that sets WAIVED, because "the writes below it all
ran" -- so a PnR that died mid-tcl still stops the chain, and a future status
added without those writes is not silently admitted.
"""
import inspect
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402


#: R-0915-85 — the fields each word REQUIRES, supplied here so a fixture can
#: name a status without also having to remember its obligations. A
#: NOT_MEASURED with no reason and a NOT_APPLICABLE with no declaration are
#: refused at the row, which is the point: they are not sayable.
_OBLIGATION = {
    "NOT_MEASURED": {"reason_class": "not_executed"},
    "NOT_APPLICABLE": {"declared_by": "the input declares no PnR"},
    "PASS_WITH_WAIVERS": {"attribution": "the sign-off engineer"},
}


def _row(status, **extras):
    return R.StepResult("pnr", status, 1.0, "detail", [], dict(extras),
                        **_OBLIGATION.get(status, {}))


# ── the predicate ────────────────────────────────────────────────────────────

def test_passing_pnr_continues_the_chain():
    assert R._pnr_chain_continues(_row("PASS")) is True


def test_waived_pnr_with_completed_signoff_writes_continues_the_chain():
    """The #1412 shape. This is the case that was silently dropping the GDS."""
    assert R._pnr_chain_continues(
        _row("PASS_WITH_WAIVERS", pnr_signoff_writes_complete=True,
             route_residual_waiver={"ticket": "vibe-ic#1412"})) is True


def test_waived_pnr_without_completed_writes_does_NOT_continue():
    """A PnR that died mid-tcl must still stop the chain: WAIVED is not a
    password, the completed writes are."""
    assert R._pnr_chain_continues(_row("PASS_WITH_WAIVERS")) is False
    assert R._pnr_chain_continues(
        _row("PASS_WITH_WAIVERS", pnr_signoff_writes_complete=False)) is False


def test_failed_blocked_and_absent_pnr_do_not_continue():
    # R-0915-85 — BLOCKED and ENV_UNAVAILABLE are both NOT_MEASURED, and
    # SKIP is NOT_APPLICABLE. Three words, three rows, no duplicates.
    for st in ("FAIL", "NOT_MEASURED", "NOT_APPLICABLE"):
        assert R._pnr_chain_continues(_row(st)) is False, st
    # even with the flag: a FAILed PnR is not admitted by carrying the key
    assert R._pnr_chain_continues(
        _row("FAIL", pnr_signoff_writes_complete=True)) is False
    assert R._pnr_chain_continues(None) is False


# ── the wiring: the predicate must actually gate the stream-out ──────────────

def test_the_chain_gate_is_wired_to_the_predicate():
    """A predicate nothing calls is not a fix."""
    src = inspect.getsource(R.main)
    assert "_pnr_step_passed = _pnr_chain_continues(_pnr_row)" in src, (
        "main() no longer derives stream-out from the measured PnR row")
    waived = _row("PASS_WITH_WAIVERS", pnr_signoff_writes_complete=True,
                  route_residual_waiver={"ticket": "vibe-ic#1412"})
    assert R._ppa_power._pdn_em_resize_chain_continues(
        waived, "PASS", R._pnr_chain_continues) is True
    assert R._ppa_power._pdn_em_resize_chain_continues(
        waived, "FAIL", R._pnr_chain_continues) is False
    assert R._ppa_power._pdn_em_resize_chain_continues(
        _row("FAIL"), "PASS", R._pnr_chain_continues) is False
    assert '_pnr_row.status == "PASS"' not in src, (
        "main() still tests the PnR row's status literally against PASS -- "
        "that is the exact test that dropped the WAIVED stream-out")


def test_step_gds_is_dispatched_under_the_chain_gate():
    """The `gds` dispatch must sit behind `_chain_ok`, which is what
    `_pnr_chain_continues` feeds. If `step_gds` is ever called unconditionally
    this test should be rewritten, not deleted -- but it must not silently
    become vacuous."""
    src = inspect.getsource(R.main)
    from _phase3_main_dispatch import gds_chain_dispatch_line
    assert gds_chain_dispatch_line()
    assert "_chain_ok = _pnr_step_passed" in src, (
        "_chain_ok no longer derives from the PnR chain predicate")


def test_step_pnr_sets_the_evidence_this_predicate_reads():
    """Both halves in one place: the producer must still write the key the
    consumer reads. This is the producer/consumer contract that, broken
    elsewhere in this same file family, is why LVS sign-off metrics are
    permanently NOT_MEASURED."""
    src = inspect.getsource(R.step_pnr)
    # R-0915-85 — BOTH ENDS OF #1412 IN ONE ASSERTION. The producer wrote
    # `WAIVED` and `_pnr_chain_continues` tested for it; migrating one and not
    # the other would have left the chain broken in exactly the way this file
    # exists to stop, so the word is pinned at the producer and the predicate
    # is driven above.
    assert '_status = "PASS_WITH_WAIVERS"' in src
    assert '"pnr_signoff_writes_complete"' in src, (
        "step_pnr no longer records pnr_signoff_writes_complete, so "
        "_pnr_chain_continues can never admit a WAIVED PnR")
