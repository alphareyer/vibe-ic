"""The tap-ripup rung succeeds on COVERAGE, never on a tie count. R-0915-104.

The first version of the rung required the well-tie COUNT not to go backwards.
That compares two different insertion policies -- the design's own
`tapcell -distance` is FIXED-PITCH across every row, while the recovery is
COVERAGE-DRIVEN and inserts only where an anchor is uncovered -- so the count
almost always drops. MEASURED, the same design and the same recovery giving
opposite verdicts from a number that was never the rule:

    r46   117 core rows   ties 1965 -> 2003   count rose -> rung claimed OK
    m3    135 core rows   ties 2624 -> 2395   count fell -> rung refused

and in the m3 arm the placement itself was legal after the ripup (OpenROAD
`detailed_placement`: 5817/5817 cells, Total Placement Failures 0, total
displacement 0.0 u), while the coverage repair left `unplaceable=10`. Ten
uncovered anchors is a latch-up / DRC risk and is NOT tradeable for timing, so
the m3 verdict stays REFUSED -- what changes is that the refusal now measures
the thing the rule is about.

Both directions: a count that FALLS with coverage complete is SUCCESS, a count
that RISES with coverage incomplete is REFUSED, and a repair that never ran is
refused as NOT MEASURED rather than read as "nothing was uncovered".
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _pdk():
    return R.PdkConfig(
        name="fixture_pdk", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fixture_fd_sc__filltie")


def _rung(marker="SDR_DPL", tag="_sdrl"):
    return R._build_escalating_legalize_tcl(
        marker, tag, tie_recover_tcl=R._build_welltie_coverage_repair_tcl(_pdk()))


# ── the rung's criterion ──────────────────────────────────────────────────
def test_success_requires_zero_uncovered_anchors():
    tcl = _rung()
    guard = tcl[:tcl.index("disp=tap-ripup")]
    assert "$_tu_sdrl == 0" in guard
    assert "check_placement" in guard, "legality is still a conjunct"


def test_the_tie_count_is_no_longer_a_criterion():
    tcl = _rung()
    assert "$_ta_sdrl >= $_tb_sdrl" not in tcl
    # it is still REPORTED -- a reader wants to see it, it just does not decide
    assert "ties=${_tb_sdrl}->${_ta_sdrl}" in tcl


def test_an_uncovered_anchor_is_not_tradeable_for_timing():
    tcl = _rung()
    assert "not tradeable for timing" in tcl
    assert "_TAP_RIPUP_NOT_RECOVERED" in tcl
    assert tcl.count("SDR_DPL_LEGALIZE_FAILED") == 1, \
        "the honest failure still follows the rung"


def test_a_repair_that_never_ran_is_not_a_clean_sheet():
    """-1 is NOT MEASURED. It must not satisfy `== 0`."""
    tcl = _rung()
    assert "set _tu_sdrl -1" in tcl
    assert "-1 = the coverage repair did not run" in tcl


def test_the_rung_reads_the_repairs_own_measurement():
    """One producer of the number, not a second re-derivation."""
    tcl = _rung()
    assert "$::_vibeic_welltie_uncovered" in tcl
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert "set ::_vibeic_welltie_uncovered $_wtfail" in deck
    assert "set ::_vibeic_welltie_uncovered -1" in deck, \
        "seeded NOT MEASURED before the repair runs"


def test_the_seed_precedes_the_measurement():
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert deck.index("set ::_vibeic_welltie_uncovered -1") < \
        deck.index("set ::_vibeic_welltie_uncovered $_wtfail")


# ── the coverage repair itself ────────────────────────────────────────────
def test_the_window_search_runs_both_ways_to_the_pdk_tap_distance():
    """The coverage rule is a WINDOW around the anchor, not its own site."""
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert "foreach _wtsgn {1 -1}" in deck, "both directions"
    assert "set _wtmaxk [expr {$_wtd / $_wtsw}]" in deck, \
        "the window is the PDK tap distance in sites"
    assert "if {abs($_wtx - $_wtcx) > $_wtd} { continue }" in deck


def test_a_site_already_occupied_is_skipped_not_overwritten():
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert "set _wtfree 0; break" in deck
    assert "if {!$_wtfree} { continue }" in deck


def test_coverage_is_measured_centre_to_centre():
    """`_wttie` holds each tie's xMin and `_wtcx` is the anchor's CENTRE."""
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert "abs([expr {$_wtt + $_wttw / 2}] - $_wtcx) <= $_wtd" in deck
    assert "if {abs($_wtt - $_wtcx) <= $_wtd}" not in deck, \
        "the old edge-against-centre comparison is gone"


def test_an_unplaceable_anchor_says_why_not_only_where():
    deck = R._build_welltie_coverage_repair_tcl(_pdk())
    assert "row_edge_clips_the_window" in deck
    assert '"occupied"' in deck
    assert 'row=$_wty x=$_wtcx ($_wtwhy)' in deck


def test_a_pdk_with_no_tapcell_master_emits_no_repair():
    pdk = R.PdkConfig(
        name="p", liberty="/l", tech_lef="/t", cell_lef="/c", cell_gds=None,
        site="S", drc_deck=None, metal_prefix="M")
    deck = R._build_welltie_coverage_repair_tcl(pdk)
    assert "WELLTIE_COVERAGE_REPAIR_SKIPPED" in deck
    assert "dbInst_create" not in deck


def test_without_a_recovery_deck_there_is_no_rung_at_all():
    tcl = R._build_escalating_legalize_tcl("SDR_DPL", "_sdrl")
    assert "tapcell_ripup" not in tcl
    assert "_vibeic_welltie_uncovered" not in tcl
