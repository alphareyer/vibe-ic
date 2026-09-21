"""A legal placement is not a routable one, and the legalizer never asks.

MEASURED on subservient x gf180mcuD as a DIE, interior-core tree
(lane icsub2, host 8HD-4, 2026-09-21, image 0.3.67 sha256:4e9f54ef): the SDR
candidate was ACCEPTED on router DRC (0 -> 0) with a clean `check_placement`,
and the adopt path then died in its PG re-route:

    [ERROR DRT-1231] Pin u_core/_3245_/ZN does not have access point   (x6)
    [ERROR DRT-0206] checkConnectivity error
    PG_REROUTE_FAILED: DRT-1231  (pnr_sdr_adopt_1.tcl:2979)

so no routed.def was written, DRC and LVS had no input, and the run produced
no post-route STA at all -- which is the number the whole exercise exists for.

`u_core/_3245_` is an original `aoi221_1` (SOURCE NONE -- not a repair
insertion) whose ZN carried exactly ONE access point in the candidate
database, in a row whose only free gaps are two sites wide and none of them
beside it.

Pin access is the ROUTER's question and OpenROAD answers it in the database.
Asking BEFORE adopting turns a whole-run loss into one refused candidate --
the same shape as the `candidate_placement_illegal` refusal beside it.

Both directions: the count is REPORTED on every candidate, a candidate with
zero inaccessible pins is unaffected, PG (special) nets and unconnected
terminals are not counted, and a build that cannot be asked leaves the count
at -1 and judges the candidate exactly as before.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _deck(tmp_path):
    return R._v1_8_100_signoff_drv_repair_tcl(
        str(tmp_path), "BUF_X1", stage="postroute_drv_repair")


def test_the_candidate_is_asked_about_pin_access(tmp_path):
    tcl = _deck(tmp_path)
    assert "SDR_TRANSACTION_CANDIDATE_PIN_ACCESS" in tcl
    assert "getAccessPoints" in tcl


def test_a_pin_with_no_access_refuses_the_candidate_by_name(tmp_path):
    tcl = _deck(tmp_path)
    assert "candidate_pin_access_lost" in tcl
    guard = tcl[:tcl.index("candidate_pin_access_lost")]
    assert "$_sdr_ap > 0" in guard


def test_the_inaccessible_pins_are_named_not_only_counted(tmp_path):
    tcl = _deck(tmp_path)
    assert "SDR_PIN_NO_ACCESS:" in tcl
    assert "[[$_sdr_at getMTerm] getName]" in tcl, "the pin, by name"
    assert "[[$_sdr_ai getMaster] getName]" in tcl, "and its master"


def test_power_nets_are_not_signal_pins(tmp_path):
    """A PG terminal is reached by the rails, not by an access point."""
    tcl = _deck(tmp_path)
    assert "if {[$_sdr_an isSpecial]} { continue }" in tcl


def test_an_unconnected_terminal_is_not_counted(tmp_path):
    tcl = _deck(tmp_path)
    assert 'if {$_sdr_an eq "NULL"} { continue }' in tcl


def test_a_build_that_cannot_be_asked_changes_no_verdict(tmp_path):
    """-1 is NOT MEASURED and must not satisfy the `> 0` refusal."""
    tcl = _deck(tmp_path)
    assert "set _sdr_ap -1" in tcl
    i = tcl.index("set _sdr_ap -1")
    assert tcl.index("SDR_TRANSACTION_CANDIDATE_PIN_ACCESS") > i
    assert "catch {" in tcl[i:i + 200], "the whole sweep is guarded"


def test_the_placement_refusal_still_comes_first(tmp_path):
    """An illegal placement keeps its own name; this is a second question."""
    tcl = _deck(tmp_path)
    assert tcl.index("candidate_placement_illegal") < \
        tcl.index("candidate_pin_access_lost")


def test_the_clean_candidate_path_is_unchanged(tmp_path):
    tcl = _deck(tmp_path)
    assert "router_drc_preserved_clean" in tcl
    assert tcl.index("candidate_pin_access_lost") < \
        tcl.index("router_drc_preserved_clean"), \
        "the pin-access refusal is asked before the clean-preserved accept"


def test_the_deck_is_balanced_tcl(tmp_path):
    tcl = _deck(tmp_path)
    bal = sum(l.count("{") - l.count("}") for l in tcl.splitlines())
    assert bal == 0, f"{bal:+d} braces"
