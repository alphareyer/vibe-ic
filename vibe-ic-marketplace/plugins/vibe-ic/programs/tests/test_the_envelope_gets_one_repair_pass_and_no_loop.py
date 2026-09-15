"""R-0915-41 — the MCF crosstalk-delay envelope gets ONE repair pass.

`si_mcf_sta` folds each coupling into its victim's grounded cap at the corner's
Miller factor, re-runs OpenSTA, and REPORTS. Nothing closed it. MEASURED on
`subservient` x gf180mcuD (lane icsub2, the same answer in r13, r14, r15 and
r16 — four trees):

    si_mcf_sta_nominal.rpt     worst slack max  +2.4975
    si_mcf_sta_mcf_setup.rpt   worst slack max  -0.2660     <- the FAIL
    si_mcf_sta_mcf_hold.rpt    worst slack max  +4.7640

and that FAIL makes step 27 FAIL, voids 28/31/32 and cascades to 29/30. The bar
is not relaxed; the flow closes the envelope instead of only reporting it.

Four refusals are pinned here because each one is a way this could go wrong:
  * a design whose envelope already CLOSES runs no repair at all;
  * a candidate that makes the ROUTE worse is rejected (the SDR children's rule);
  * a candidate that buys envelope margin with NOMINAL margin is rejected;
  * one pass, and the residual is reported BY NAME rather than iterated.
"""
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import si_mcf_repair as R  # noqa: E402


def _before(**kw):
    d = {"router_drc": 0, "nominal_setup_ns": 0.03, "mcf_setup_ns": -0.266}
    d.update(kw)
    return d


# ------------------------------------------------------- when it runs at all

def test_a_closed_envelope_runs_no_repair(tmp_path, monkeypatch):
    """THE FIRST NEGATIVE CONTROL. Only si_mcf_sta's own FAIL opens this door."""
    for verdict in ("PASS", "ADVISORY", "ERROR", None):
        assert R.envelope_is_open({"verdict": verdict} if verdict else {}) is False
    assert R.envelope_is_open({"verdict": "FAIL"}) is True


def test_advisory_and_error_are_not_a_licence_to_repair():
    """ADVISORY means no slack was produced and ERROR means the tool failed;
    repairing on either would be acting on an absence."""
    assert not R.envelope_is_open({"verdict": "ADVISORY"})
    assert not R.envelope_is_open({"verdict": "ERROR"})


# ------------------------------------------------------- what it targets

def test_the_targets_come_from_the_runs_own_reports():
    victims = R.dominant_victims(
        {"coupling_dominated_nets": [
            {"net": "n_hot", "coupling_ratio": 0.97},
            {"net": "n_mild", "coupling_ratio": 0.40}]},
        {"corners": {"setup": {"worst_victim": {"net": "n_worst"}},
                     "hold": {"worst_victim": {"net": "n_hot"}}}})
    assert victims == ["n_hot", "n_worst"], victims


def test_a_net_the_envelope_did_not_complain_about_is_not_a_target():
    victims = R.dominant_victims(
        {"coupling_dominated_nets": [{"net": "n_mild", "coupling_ratio": 0.5}]},
        {"corners": {}})
    assert victims == []


def test_no_victim_means_no_deck_at_all():
    """A deck that runs a repair over an empty set can still move a cell."""
    assert R.repair_tcl(folded_spef_c="/c/x.spef", victims=[],
                        sdc_c="/c/x.sdc") == ""


# ------------------------------------------------------- the deck itself

def test_the_pass_reads_the_MCF_BOUNDED_spef_and_touches_only_the_victims():
    tcl = R.repair_tcl(folded_spef_c="/c/mcf_setup.spef",
                       victims=["n_hot", "n_worst"], sdc_c="/c/x.sdc")
    assert "read_spef /c/mcf_setup.spef" in tcl
    assert "set _si_victims {n_hot n_worst}" in tcl
    assert "SI_MCF_REPAIR_TARGETS" in tcl


def test_it_is_one_pass_and_the_deck_cannot_loop():
    """No `while`, no `for`, and the repair itself is capped at one pass: an
    envelope a repair can also WIDEN must never be iterated."""
    tcl = R.repair_tcl(folded_spef_c="/c/s.spef", victims=["n"], sdc_c="/c/x.sdc")
    assert "-max_passes 1" in tcl
    for forbidden in ("while ", "for {", "repair_design", "detailed_route"):
        assert forbidden not in tcl, forbidden


# ------------------------------------------------------- the judgement

def test_a_worse_route_is_rejected_by_name():
    ok, why = R.accepts(_before(), {"router_drc": 3, "mcf_setup_ns": 0.1})
    assert not ok
    assert "router_drc 0 -> 3" in why and "closed nothing" in why


def test_buying_envelope_margin_with_nominal_margin_is_rejected():
    ok, why = R.accepts(_before(),
                        {"router_drc": 0, "nominal_setup_ns": 0.01,
                         "mcf_setup_ns": 0.10})
    assert not ok
    assert "nominal setup" in why and "REAL margin" in why


def test_a_pass_that_did_not_improve_the_envelope_is_rejected():
    ok, why = R.accepts(_before(),
                        {"router_drc": 0, "nominal_setup_ns": 0.03,
                         "mcf_setup_ns": -0.30})
    assert not ok
    assert "did not improve the envelope" in why


def test_a_good_candidate_is_accepted():
    ok, why = R.accepts(_before(),
                        {"router_drc": 0, "nominal_setup_ns": 0.03,
                         "mcf_setup_ns": 0.05})
    assert ok, why


# ------------------------------------------------------- the residual

def test_a_still_open_envelope_is_reported_by_name_not_iterated():
    msg = R.residual({"mcf_setup_ns": -0.11})
    assert msg is not None
    assert "STILL OPEN" in msg and "-0.11 ns" in msg
    assert "ONE pass by design" in msg
    assert "reported rather than chased" in msg


def test_a_closed_envelope_has_no_residual():
    assert R.residual({"mcf_setup_ns": 0.05}) is None
    assert R.residual({"mcf_setup_ns": 0.0}) is None


# ------------------------------------------------------- the recorded trajectory

def _proj(tmp_path, si_mcf, si_x=None):
    import json as _j
    p = tmp_path / "proj"
    (p / "reports" / "phase3").mkdir(parents=True)
    (p / "reports" / "phase3" / "si_mcf_sta.json").write_text(_j.dumps(si_mcf))
    if si_x is not None:
        (p / "reports" / "phase3" / "si_crosstalk.json").write_text(_j.dumps(si_x))
    return p


_OPEN = {"verdict": "FAIL",
         "nominal": {"worst_setup_slack_ns": 0.03},
         "corners": {"setup": {"worst_slack_after_ns": -0.266,
                               "worst_victim": {"net": "n_worst"}},
                     "hold": {"worst_slack_after_ns": 4.764}}}
_CLOSED = {"verdict": "PASS",
           "nominal": {"worst_setup_slack_ns": 0.03},
           "corners": {"setup": {"worst_slack_after_ns": 0.5},
                       "hold": {"worst_slack_after_ns": 4.7}}}


def test_a_closed_envelope_records_NOT_RUN_and_touches_nothing(tmp_path):
    rec = R.run_once(_proj(tmp_path, _CLOSED))
    assert rec["decision"] == "NOT_RUN"
    assert "does not report FAIL" in rec["reason"]
    assert rec["after"] is None


def test_an_absent_execution_seam_says_so_rather_than_reading_as_a_no_op(tmp_path):
    """"the pass ran and changed nothing" and "there was no way to run it" are
    different answers and the record must not merge them."""
    rec = R.run_once(_proj(tmp_path, _OPEN, {"coupling_dominated_nets": []}))
    assert rec["decision"] == "NOT_EXECUTED"
    assert "applied nothing" in rec["reason"]
    assert rec["before"]["mcf_setup_ns"] == -0.266


def test_the_trajectory_carries_all_three_numbers_before(tmp_path):
    rec = R.run_once(_proj(tmp_path, _OPEN))
    assert rec["before"] == {"nominal_setup_ns": 0.03,
                             "mcf_setup_ns": -0.266,
                             "mcf_hold_ns": 4.764}


def test_a_rejected_candidate_is_recorded_as_discarded(tmp_path):
    def _runner(project, container="", victims=()):
        return {"router_drc_before": 0, "router_drc": 4,
                "nominal_setup_ns": 0.03, "mcf_setup_ns": 0.1}
    rec = R.run_once(_proj(tmp_path, _OPEN), runner=_runner)
    assert rec["decision"] == "REJECTED_CANDIDATE_DISCARDED"
    assert "makes the ROUTE worse" in rec["reason"]
    assert rec["residual"] is None


def test_an_adopted_candidate_records_its_residual(tmp_path):
    def _runner(project, container="", victims=()):
        return {"router_drc_before": 0, "router_drc": 0,
                "nominal_setup_ns": 0.03, "mcf_setup_ns": -0.05}
    rec = R.run_once(_proj(tmp_path, _OPEN), runner=_runner)
    assert rec["decision"] == "ADOPTED"
    assert "STILL OPEN" in rec["residual"]
