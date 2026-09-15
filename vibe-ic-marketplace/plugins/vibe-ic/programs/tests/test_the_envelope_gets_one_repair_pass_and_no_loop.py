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
