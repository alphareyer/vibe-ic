"""vibe-ic#2160 — a POLICY exit of the post-reroute convergence loop must publish
the transition its claim rests on, and must not claim for the DESIGN what only
the loop's own threshold established.

THE DEFECT, measured twice on real production logs (subservient x gf180mcuD):

  * the #2160 fix arm, image 0.3.48 `sha256:1463dac58116…`, from its own
    `phase3/stage3/pnr/signoff_spef_repair.log`::

        SHIP_WNS_CVG_PASS0..4: -2.0617 -1.3118 -0.4644 -0.3191 -0.2794
        SHIP_CVG_PLATEAU            (5 of 8 passes used; last gain +0.0397 ns)

  * the live tip e2b3c08170b5, image 0.3.49 `sha256:89a8fd72…`, lane cz2160 on
    8HD-6, from ITS own log::

        SHIP_WNS_CVG_PASS0..3: -1.8036 -0.9816 -0.5692 -0.4975
        SHIP_CVG_PLATEAU            (4 of 8 passes used; last gain +0.0717 ns)

In BOTH, `reports/phase3/ship_convergence_exhaustion.json` published verdict
PASS, `still_converging_at_exit: null`, and the sentence "any residual violation
is the design's, and raising the pass bound cannot change it" — showing neither
the gain nor the unused passes. The SAME program, on its OTHER exit (bound
reached), computes exactly that transition, prints the gain, and REFUSES to let
the number be triaged as a design floor ("it stopped COUNTING, not CONVERGING").

Two exits, the same evidence shape, opposite conclusions, and only one of them
shows a number. That asymmetry is what these tests pin. The verdict is
deliberately NOT changed: this gate gates nothing, and declaring it blocking
needs a proven-by-run demonstration that does not exist.
"""
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import ship_postroute_convergence_exhaustion_check as C  # noqa: E402


# The two MEASURED transcripts, verbatim from the runs named in the docstring.
TIP_PLATEAU = """\
SHIP_WNS_CVG_PASS0: -1.8036044279692278
SHIP_DRV_CVG_PASS0: 0
SHIP_WNS_CVG_PASS1: -0.9816485679954445
SHIP_DRV_CVG_PASS1: 0
SHIP_WNS_CVG_PASS2: -0.5691980381470422
SHIP_DRV_CVG_PASS2: 0
SHIP_WNS_CVG_PASS3: -0.49750960315187887
SHIP_DRV_CVG_PASS3: 0
SHIP_CVG_PLATEAU
SHIP_WNS_POSTROUTE: -0.49750960315187887
"""

ARM_PLATEAU = """\
SHIP_WNS_CVG_PASS0: -2.061733953030237
SHIP_DRV_CVG_PASS0: 0
SHIP_WNS_CVG_PASS1: -1.311802259503759
SHIP_DRV_CVG_PASS1: 0
SHIP_WNS_CVG_PASS2: -0.4643503490929866
SHIP_DRV_CVG_PASS2: 0
SHIP_WNS_CVG_PASS3: -0.31905679001873943
SHIP_DRV_CVG_PASS3: 0
SHIP_WNS_CVG_PASS4: -0.2793516608215273
SHIP_DRV_CVG_PASS4: 0
SHIP_CVG_PLATEAU
SHIP_WNS_POSTROUTE: -0.2793516608215273
"""


def _detail(findings):
    return " ".join(f.detail for f in findings
                    if f.label == "loop_ended_on_own_policy")


def test_a_plateau_exit_publishes_the_gain_it_rests_on():
    """The tip run's own transcript: the exit fired on a transition that GAINED
    +0.0717 ns. That number must be in the record, not only in the log."""
    _, findings, summary = C.audit(TIP_PLATEAU, bound=8)
    assert summary["last_transition_gain_ns"] is not None
    assert abs(summary["last_transition_gain_ns"] - 0.07168843499516331) < 1e-12
    assert "+0.0717" in _detail(findings)


def test_a_plateau_exit_publishes_how_many_passes_it_left_unused():
    """4 of 8 on the tip, 5 of 8 on the #2160 fix arm. A reader cannot judge a
    policy exit without knowing how much headroom it declined to spend."""
    _, findings, summary = C.audit(TIP_PLATEAU, bound=8)
    assert summary["passes_unused"] == 4
    assert "4 unused" in _detail(findings)
    _, _, summary_arm = C.audit(ARM_PLATEAU, bound=8)
    assert summary_arm["passes_unused"] == 3


def test_a_plateau_exit_no_longer_attributes_the_residual_to_the_design():
    """SHIP_CVG_PLATEAU fires on `wns_now <= wns_prev + threshold`, so a
    strictly POSITIVE gain below the threshold ends the loop. That establishes
    the loop's policy, never the design's floor."""
    for raw in (TIP_PLATEAU, ARM_PLATEAU):
        _, findings, _ = C.audit(raw, bound=8)
        detail = _detail(findings)
        assert "residual violation is the design's" not in detail
        assert "NOT established by this exit" in detail


def test_still_converging_at_exit_is_populated_on_a_policy_exit():
    """The field existed and was left None on this whole exit path, so a reader
    could not tell 'measured and flat' from 'never computed'."""
    _, _, summary = C.audit(TIP_PLATEAU, bound=8)
    assert summary["still_converging_at_exit"] is False


def test_a_closed_exit_says_there_is_no_residual_to_attribute():
    """SHIP_CVG_CLOSED is the ONE marker that leaves nothing to attribute — the
    loop met its own non-negative-setup + zero-DRV test. It must say that, and
    it must not borrow the plateau's sentence."""
    raw = ("SHIP_WNS_CVG_PASS0: -1.5\nSHIP_DRV_CVG_PASS0: 3\n"
           "SHIP_WNS_CVG_PASS1: 0.02\nSHIP_DRV_CVG_PASS1: 0\n"
           "SHIP_CVG_CLOSED\nSHIP_WNS_POSTROUTE: 0.02\n")
    verdict, findings, _ = C.audit(raw, bound=8)
    assert verdict == "PASS"
    detail = _detail(findings)
    assert "no residual for this exit to attribute" in detail
    assert "residual violation is the design's" not in detail


def test_a_nonnumeric_exit_says_it_measured_nothing():
    """The loop breaks on SHIP_CVG_NONNUMERIC when its OWN worst-slack probe
    returns something that is not a number. 'Could not read it' is not 'read it
    and the design was at its floor'."""
    raw = ("SHIP_WNS_CVG_PASS0: -1.5\nSHIP_DRV_CVG_PASS0: 3\n"
           "SHIP_WNS_CVG_PASS1: -1.4\nSHIP_DRV_CVG_PASS1: 3\n"
           "SHIP_CVG_NONNUMERIC\nSHIP_WNS_POSTROUTE: -1.4\n")
    _, findings, _ = C.audit(raw, bound=8)
    detail = _detail(findings)
    assert "MEASURED NOTHING about the design" in detail
    assert "residual violation is the design's" not in detail


def test_a_single_pass_policy_exit_reports_no_transition_not_a_gain_of_zero():
    """One pass is UNMEASURED, and unmeasured is never zero. The gain field must
    stay None rather than becoming a 0.0 a reader would take for 'flat'."""
    raw = ("SHIP_WNS_CVG_PASS0: 0.5\nSHIP_DRV_CVG_PASS0: 0\n"
           "SHIP_CVG_CLOSED\nSHIP_WNS_POSTROUTE: 0.5\n")
    _, findings, summary = C.audit(raw, bound=8)
    assert summary["last_transition_gain_ns"] is None
    assert summary["still_converging_at_exit"] is None
    assert "no transition to judge" in _detail(findings)


def test_the_bound_exhausted_while_converging_fail_is_untouched():
    """NEGATIVE CONTROL — green BEFORE and AFTER. The other exit's refusal is
    the reason this program exists; publishing the plateau's numbers must not
    soften it. The transcript is the emitter's own measured bound-3 capture."""
    raw = ("SHIP_WNS_CVG_PASS0: -4.4438\nSHIP_DRV_CVG_PASS0: 190\n"
           "SHIP_WNS_CVG_PASS1: -3.0167\nSHIP_DRV_CVG_PASS1: 179\n"
           "SHIP_WNS_CVG_PASS2: -1.3748\nSHIP_DRV_CVG_PASS2: 78\n"
           "SHIP_WNS_POSTROUTE: -1.6742\n")
    verdict, findings, summary = C.audit(raw, bound=3)
    assert verdict == "FAIL"
    assert any(f.label == "bound_exhausted_while_converging" for f in findings)
    assert summary["bound_exhausted"] is True
    assert summary["still_converging_at_exit"] is True


def test_both_exits_publish_the_same_fields_from_the_same_helper():
    """The two exits must not be able to drift again. The bound-exhausted exit
    publishes the identical fields, computed by the identical helper — so a
    reader gets the same evidence whichever way the loop ended, and a future
    edit to one path cannot quietly leave the other one silent."""
    plateau = C.audit(TIP_PLATEAU, bound=8)[2]
    bound_hit = C.audit(
        "SHIP_WNS_CVG_PASS0: -4.4438\nSHIP_DRV_CVG_PASS0: 190\n"
        "SHIP_WNS_CVG_PASS1: -3.0167\nSHIP_DRV_CVG_PASS1: 179\n"
        "SHIP_WNS_CVG_PASS2: -1.3748\nSHIP_DRV_CVG_PASS2: 78\n"
        "SHIP_WNS_POSTROUTE: -1.6742\n", bound=3)[2]
    fields = ("still_converging_at_exit", "last_transition_gain_ns",
              "last_transition_reason", "passes_unused")
    for f in fields:
        assert plateau[f] is not None, f"plateau exit left {f} unpublished"
        assert bound_hit[f] is not None, f"bound exit left {f} unpublished"
    assert abs(bound_hit["last_transition_gain_ns"] - 1.6419) < 1e-9
    assert bound_hit["passes_unused"] == 0
