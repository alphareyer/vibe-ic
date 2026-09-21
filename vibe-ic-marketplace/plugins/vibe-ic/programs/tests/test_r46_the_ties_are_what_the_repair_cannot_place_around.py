"""The post-route DRV repair's legalization has a rung for the real blocker.

MEASURED on subservient x gf180mcuD as a DIE (lane icsub2, host 8HD-4, run r46,
OpenROAD 26Q3-2599-g697a63f1ad, EDA image 0.3.67), against the repair's own
transaction database `phase3/stage3/pnr/sdr_transaction/candidate.odb`:

    check_placement                                   8 violations
    detailed_placement (default window)  -> check    10 violations
    tapcell_ripup (1965 CORE_WELLTAP out)
      detailed_placement                 -> check     0 violations
      well-tie coverage repair (2003 ties back,
        17 anchors disclosed unplaceable)
      detailed_placement                 -> check     0 violations

and the geometry that explains it: the largest contiguous free run anywhere in
the 117 core rows is 50 sites with the ties in place and 780 with them out,
while the instances the legalizer named (`u_core/fanout3953`, `fanout3954`,
`fanout4066` -- `buf_12`, 38 sites -- and `u_core/rebuffer3944` -- `buf_16`,
50 sites) need a run that long. Rows able to host a `buf_16` go 0 -> 73 of 117.
That is why widening `-max_displacement` to the whole die (+/-3503 sites,
+/-500 rows) failed on the SAME four instances as the +/-500 x +/-100 rung:
the window was never the constraint.

Both directions: with no tie-recovery deck the emitted Tcl is byte-for-byte
what it was, and the rung's success is conditional on BOTH ending legal AND not
losing tie coverage.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _pdk() -> "R.PdkConfig":
    return R.PdkConfig(
        name="fixture_pdk",
        liberty="/pdk/lib.lib", tech_lef="/pdk/tech.lef",
        cell_lef="/pdk/cells.lef", cell_gds=None,
        site="unithd", drc_deck=None, metal_prefix="met",
        tapcell_master="fixture_fd_sc__filltie",
    )


def test_without_a_tie_recovery_deck_the_ladder_is_unchanged():
    """No deck, no rung — every existing call site keeps its exact bytes."""
    tcl = R._build_escalating_legalize_tcl("SDR_DPL", "_sdrl")
    assert "tapcell_ripup" not in tcl
    assert "tap-ripup" not in tcl
    assert "SDR_DPL_LEGALIZE_FAILED" in tcl


def test_the_ladder_rips_the_ties_up_when_displacement_has_failed():
    tcl = R._build_escalating_legalize_tcl(
        "SDR_DPL", "_sdrl", tie_recover_tcl="puts TIE_RECOVER_DECK")
    assert "tapcell_ripup" in tcl
    assert "TIE_RECOVER_DECK" in tcl, "the PDK's own recovery deck is inlined"
    # the fixed-pitch inserter is NOT what recovers coverage: re-inserting on a
    # 14 um pitch lands on top of the cells just legalized (measured: 10 back).
    assert "tapcell -distance" not in tcl


def test_the_rung_runs_only_after_every_displacement_rung_failed():
    tcl = R._build_escalating_legalize_tcl(
        "SDR_DPL", "_sdrl", tie_recover_tcl="puts TIE_RECOVER_DECK")
    rung = tcl[tcl.index("tapcell_ripup"):]
    head = tcl[:tcl.index("tapcell_ripup")]
    # the rung is inside a `$_dplok_sdrl == 0` guard opened before it
    assert head.rstrip().endswith("{") or "$_dplok_sdrl == 0" in head[-400:]
    assert "$_dplok_sdrl == 0" in head[-400:]
    assert "_LEGALIZE_FAILED" in rung, "the honest failure still follows it"


def test_the_rung_claims_nothing_unless_the_ties_come_back():
    """Legal-but-untied is not legalized. The success test is a conjunction.

    The conjunction is unchanged; WHAT IT MEASURES was corrected by owner
    ruling R-0915-104. This used to require the tie COUNT not to go backwards,
    which compares a FIXED-PITCH original against a COVERAGE-DRIVEN recovery
    and gave opposite verdicts on the same design (r46 1965 -> 2003 passed it,
    the m3 arm 2624 -> 2395 failed it). The rule is COVERAGE, and the rung is
    no weaker for it: an uncovered anchor still refuses.
    """
    tcl = R._build_escalating_legalize_tcl(
        "SDR_DPL", "_sdrl", tie_recover_tcl="puts TIE_RECOVER_DECK")
    ok = [ln for ln in tcl.splitlines() if "disp=tap-ripup" in ln]
    assert len(ok) == 1
    guard = tcl[:tcl.index("disp=tap-ripup")]
    assert "$_tu_sdrl == 0" in guard, "every anchor must be covered"
    assert "check_placement" in guard
    assert "_TAP_RIPUP_NOT_RECOVERED" in tcl


def test_a_design_with_no_ties_never_enters_the_rung():
    tcl = R._build_escalating_legalize_tcl(
        "SDR_DPL", "_sdrl", tie_recover_tcl="puts TIE_RECOVER_DECK")
    assert "$_tb_sdrl > 0 && ![catch {tapcell_ripup}" in tcl


def test_the_rung_reports_when_the_ties_were_not_the_blocker():
    tcl = R._build_escalating_legalize_tcl(
        "SDR_DPL", "_sdrl", tie_recover_tcl="puts TIE_RECOVER_DECK")
    assert "_TAP_RIPUP_DID_NOT_LEGALIZE" in tcl


def test_the_sdr_deck_carries_the_rung_when_its_pdk_is_known(tmp_path):
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        str(tmp_path), "BUF_X1", stage="postroute_drv_repair", pdk=_pdk())
    assert "tapcell_ripup" in deck
    assert "fixture_fd_sc__filltie" in deck, \
        "the recovery deck is built from THIS run's tapcell master"


def test_the_sdr_deck_without_a_pdk_is_what_it_was(tmp_path):
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        str(tmp_path), "BUF_X1", stage="postroute_drv_repair")
    assert "tapcell_ripup" not in deck


def test_the_clock_tree_is_still_not_traded_for_legality(tmp_path):
    """r42 measured that trade at -12.27 ns; the tap rung does not reopen it."""
    deck = R._v1_8_100_signoff_drv_repair_tcl(
        str(tmp_path), "BUF_X1", stage="postroute_drv_repair", pdk=_pdk())
    assert "SDR_DPL_CLKBUF_DOWNSIZE" not in deck
