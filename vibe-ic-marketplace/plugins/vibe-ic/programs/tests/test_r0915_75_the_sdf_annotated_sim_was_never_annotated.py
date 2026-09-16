"""R-0915-75 — step 29 is called "SDF-annotated" and had never annotated a delay.

MEASURED, sha256 x sky130A, lane icsha2 run15 (main 385445351), front door.
Every one of the eleven gate-level transcripts carries ~31000 lines of

    SDF ERROR: .../sha256.sdf:31014: Unable to match ModPath A -> Y in
               fips1804_sha256_abc.u_dut._08841_

and ZERO `Putting delay` lines.  Not one delay was applied, cell or
interconnect.  Nine cases passed, and what they passed was a ZERO-DELAY re-run
of the L10 suite wearing the name of a timing simulation.

TWO INDEPENDENT CAUSES, both in the flow's own hands, both measured:

 (1) `write_sdf` emits the two-value form `(0.113::0.113)` — min and max with
     the TYP FIELD EMPTY.  Icarus selects typ, finds nothing, and applies no
     delay at all; `+mindelays` / `+typdelays` / `+maxdelays` do not rescue it
     (all three measured).  The pinned image's `write_sdf` takes
     `-include_typ`, which emits `(0.113:0.113:0.113)`.
 (2) Icarus PARSES `specify` by default and does not BUILD module paths from
     it.  `-gspecify` creates them; without them `$sdf_annotate` has nothing to
     attach an IOPATH to.

ISOLATED, one inverter, the flow's exact flags, the PDK's own models, measured
as a DELAY and not as the absence of an error:

    -g2012 -ginterconnect               SDF ERROR, delay   0 ps
    -g2012 -ginterconnect -gspecify     no error,  delay  98 ps  (SDF fall 0.098 ns)
    ... -gspecify -DFUNCTIONAL          SDF ERROR, delay   0 ps  (control: the
                                        FUNCTIONAL cell variant has no specify)

END TO END on run15's own netlist (fips1804_sha256_abc):

    as shipped   (min::max, no -gspecify)   unmatched 31061   delays     0  PASS
    -include_typ (full triple, no -gspecify) unmatched 31061   delays 61976  FAIL 8/12
    + -gspecify                              unmatched     0   delays 61976  FAIL 10/12
    + gate-level read strobe                 unmatched     0   delays 61976  PASS 12/12

The last arm is why the remaining failure is NOT the silicon: with a real
annotation the L10 bus-read task samples `read_data` 100 ps after driving the
address, which only settles in a zero-delay sim; moving the strobe to the
following active edge passes 12 of 12 with ZERO timing-check violations.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import sdf_gate_sim as SG


# --------------------------------------------------------------------------
# the census — pure, and it counts what the simulator said it did
# --------------------------------------------------------------------------
_APPLIED = "SDF INFO: /p/x.sdf:18: Putting delay: 0.001000 for index 1\n"
_UNMATCHED = ("SDF ERROR: /p/x.sdf:31014: Unable to match ModPath A -> Y in "
              "tb.u_dut._08841_\n")


def test_a_run_that_applied_no_delay_is_not_annotated():
    c = SG.sdf_annotation_census(_UNMATCHED * 31061)
    assert c == {"delays_applied": 0, "modpath_unmatched": 31061,
                 "annotated": False}


def test_a_run_that_applied_delays_is_annotated():
    c = SG.sdf_annotation_census(_APPLIED * 61976)
    assert c["delays_applied"] == 61976
    assert c["annotated"] is True


def test_one_delay_is_enough_to_stop_calling_it_unannotated():
    """The boundary, stated: `annotated` is False ONLY at zero."""
    assert SG.sdf_annotation_census(_APPLIED)["annotated"] is True
    assert SG.sdf_annotation_census("")["annotated"] is False


def test_the_two_counts_are_independent():
    """A run can apply interconnect delays and still match no cell arc — that
    is exactly the middle arm above, and collapsing them would hide it."""
    c = SG.sdf_annotation_census(_APPLIED * 61976 + _UNMATCHED * 31061)
    assert c["delays_applied"] == 61976
    assert c["modpath_unmatched"] == 31061
    assert c["annotated"] is True


def test_an_unrelated_sdf_error_is_not_counted_as_an_unmatched_arc():
    """NEGATIVE CONTROL. The header's `Chosen value not defined` lines are SDF
    ERRORs and are not cell arcs."""
    c = SG.sdf_annotation_census(
        "SDF ERROR: /p/x.sdf:9: Chosen value not defined.\n")
    assert c["modpath_unmatched"] == 0


def test_the_census_is_pure():
    src = inspect.getsource(SG.sdf_annotation_census)
    for forbidden in ("open(", "Path(", "subprocess", "_docker"):
        assert forbidden not in src, forbidden


# --------------------------------------------------------------------------
# the sentence
# --------------------------------------------------------------------------
def test_an_unannotated_run_is_named_and_both_causes_are_in_the_sentence():
    msg = SG.name_unannotated_run(
        {"annotated": False, "modpath_unmatched": 31061})
    assert "NOT SDF-ANNOTATED" in msg
    assert "ZERO-DELAY" in msg
    assert "-include_typ" in msg and "-gspecify" in msg
    assert "31061" in msg


def test_an_annotated_run_gets_no_sentence_at_all():
    """NEGATIVE CONTROL. This never speaks about a run that annotated."""
    assert SG.name_unannotated_run(
        {"annotated": True, "modpath_unmatched": 31061}) is None


def test_the_sentence_never_claims_a_verdict():
    msg = SG.name_unannotated_run({"annotated": False, "modpath_unmatched": 0})
    assert "PASS" not in msg.upper()
    assert "FAIL" not in msg.upper()


# --------------------------------------------------------------------------
# the flags
# --------------------------------------------------------------------------
def test_every_iverilog_invocation_builds_specify_paths():
    """One constant, every call site — a second spelling is a second thing to
    keep in step."""
    assert "-gspecify" in SG._IVERILOG_FLAGS
    src = Path(SG.__file__).read_text()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    # `-g2012` is the tell of a hand-built flag string: after this change it
    # may appear in exactly ONE place, the shared constant. A second spelling
    # is a second thing to keep in step, and is how a call site keeps compiling
    # without specify paths while the others stop.
    carriers = [ln for ln in code.splitlines() if "-g2012" in ln]
    assert len(carriers) == 1, carriers
    assert "_IVERILOG_FLAGS" in carriers[0], carriers[0]


def test_the_sdf_writer_asks_for_the_full_triple():
    """`-include_typ`, GUARDED: a tool without the flag must still get an SDF,
    and the run must say which form it got."""
    runner = Path(SG.__file__).parent / "phase3_one_shot_runner.py"
    src = runner.read_text()
    assert "write_sdf -include_typ" in src
    assert "WRITE_SDF_INCLUDE_TYP_UNSUPPORTED" in src
    # the fallback still writes an SDF, and discloses the form
    assert "WRITE_SDF_TWO_VALUE_FORM" in src
    i = src.index("write_sdf -include_typ")
    window = src[i:i + 900]
    assert "WRITE_SDF_FAIL" in window   # the plain path is still reached


# --------------------------------------------------------------------------
# the evidence the step publishes
# --------------------------------------------------------------------------
def _meta(delays, unmatched):
    return {"top": "t", "netlist": "n.v", "pdk_lib": "lib", "sdf": "s.sdf",
            "declared": 2, "compile_flags": SG._IVERILOG_FLAGS,
            "delays_applied": delays, "modpath_unmatched": unmatched}


_ROWS = [{"id": "a", "verdict": "PASS", "passed": 1, "total": 1,
          "marker": "L10_TB_PASS"},
         {"id": "b", "verdict": "PASS", "passed": 1, "total": 1,
          "marker": "L10_TB_PASS"}]


def test_results_log_states_the_annotation_next_to_the_name():
    log = SG.build_l10_results_log(_ROWS, [], _meta(61976, 0))
    assert "sdf annotation: 61976 delay(s) applied, 0 cell arc(s) unmatched" in log
    assert "compile flags : " in log and "-gspecify" in log


def test_a_zero_delay_run_says_so_AFTER_its_per_case_verdicts():
    """Order is the point: the cases said what they said, and this says whether
    any of it was measured with timing. It never replaces a verdict."""
    log = SG.build_l10_results_log(_ROWS, [], _meta(0, 31061))
    assert "VERDICT: PASS" in log or "VERDICT:" in log
    assert "NOT SDF-ANNOTATED" in log
    assert log.index("VERDICT:") < log.index("NOT SDF-ANNOTATED")


def test_an_annotated_run_carries_no_such_paragraph():
    """NEGATIVE CONTROL."""
    log = SG.build_l10_results_log(_ROWS, [], _meta(61976, 0))
    assert "NOT SDF-ANNOTATED" not in log


def test_the_disclosure_changes_no_per_case_verdict():
    """Nothing here gates: the same rows give the same verdict line whether the
    run annotated or not."""
    a = SG.build_l10_results_log(_ROWS, [], _meta(61976, 0))
    b = SG.build_l10_results_log(_ROWS, [], _meta(0, 31061))
    line = lambda t: [x for x in t.splitlines() if x.startswith("VERDICT:")][0]
    assert line(a) == line(b)
