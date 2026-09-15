"""A power baseline the design bound to ANOTHER technology is not a budget here.

vibe-ic#2277 / OWNER RULING R-0915-22 (2026-09-15), flow step 33. MEASURED on
live main 79506306d (lane icspm4, run1): the design's L7 attributes its ONLY
total-power baseline to a different library than the one the run built against,
so `_ppa.power.resolve_power_requirement` refused it BY NAME
(`baseline_not_attributed_to_this_technology`, `signoff_row_found` true) and
`power_total_vs_budget_check` wrote

  {"verdict":"INCOMPLETE","reason_class":"BLOCKED_BY_UPSTREAM",
   "power_budget_uw":null,
   "missing_authority":"L19_CONSTRAINTS_PDK.json fields.power_budget_uw (unset
     in 1 of 1 published copy/copies); the design's L7 total-power sign-off row
     is NOT_DETERMINED for this run (baseline_not_attributed_to_this_technology)
     ..."}

Refusing to BORROW the number is correct and is unchanged. What R-0915-22
changes is the TIER: BLOCKED_BY_UPSTREAM says an input this gate needs was never
PRODUCED and implies somebody still owes it. Nobody does -- the design's input
states no power budget applicable to this run's technology and never promised
one. The sibling AREA gate has answered NOT_APPLICABLE for exactly this kind of
fact since #2147.

Every conjunct of the new branch is derived from the INPUT, never a flag, and a
design whose L7 / L19 / ppa contract states a budget for THIS technology keeps
the gate live.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(HERE))

import _flow_reason_taxonomy as taxonomy                  # noqa: E402
import flow_compliance_check as F                         # noqa: E402
import power_total_vs_budget_check as power_gate          # noqa: E402
# The measured shape, already fixtured: LIB_A is the technology the design's own
# L7 baseline is attributed to, LIB_B the one the run actually built on.
import test_issue2147_signoff_rows_are_consumed as S      # noqa: E402

_CLAUSE = ("power_total_vs_budget_check . --json "
           "reports/phase2/gates/power_budget.json")


# --------------------------------------------------------------------------- #
# the ruling
# --------------------------------------------------------------------------- #
def test_no_budget_for_this_technology_is_a_design_declared_NA(tmp_path):
    v, rep = power_gate.evaluate(S._project(tmp_path, built_on=S.LIB_B), None)
    assert v == "NOT_APPLICABLE"
    assert rep["reason_class"] == taxonomy.DESIGN_DECLARED_NA
    assert taxonomy.report_reason_class(rep) == taxonomy.DESIGN_DECLARED_NA


def test_the_unusable_row_is_disclosed_BY_NAME_not_dropped(tmp_path):
    """The refusal #2147 landed is the part that must survive: the design DOES
    state a sign-off row, and the report says so and says whose baseline it
    is, rather than reporting silence."""
    _, rep = power_gate.evaluate(S._project(tmp_path, built_on=S.LIB_B), None)
    d = rep["disposition"]
    assert d["signoff_row_reason"] == "baseline_not_attributed_to_this_technology"
    assert d["signoff_row_tier"] == "not_determined"
    assert any(S.LIB_A in str(a) for a in d["attributions_seen"])


def test_the_number_is_still_never_borrowed(tmp_path):
    _, rep = power_gate.evaluate(S._project(tmp_path, built_on=S.LIB_B), None)
    assert rep["power_budget_uw"] is None
    assert rep["requirement"] is None
    assert rep.get("comparison") is None


def test_the_flow_stops_booking_step_33_INCOMPLETE(tmp_path):
    """End of the chain, through the reader's own clause runner."""
    proj = S._project(tmp_path, built_on=S.LIB_B)
    (proj / "reports" / "phase2" / "gates").mkdir(parents=True, exist_ok=True)
    passed, out = F._check_program_exit_zero(proj, _CLAUSE)
    assert passed is True
    assert not out.startswith("INCOMPLETE:")
    assert "reason_class=EXECUTION_ERROR" not in out


def test_the_refusal_still_exits_2_and_never_prints_a_comparison(tmp_path):
    """rc 2 is the honest code: the comparison did not happen, and this gate
    never claims it did. Only the CLASS changed."""
    proj = S._project(tmp_path, built_on=S.LIB_B)
    rc = power_gate.main([str(proj)])
    assert rc == power_gate.RC_NOT_COMPARED


# --------------------------------------------------------------------------- #
# negative controls: a design WITH a budget for this technology stays live
# --------------------------------------------------------------------------- #
def test_a_baseline_on_the_RUNs_own_technology_keeps_the_gate_live(tmp_path):
    v, rep = power_gate.evaluate(S._project(tmp_path, built_on=S.LIB_A), None)
    assert v in ("PASS", "FAIL")
    assert rep.get("reason_class") is None


def test_a_declared_L19_budget_keeps_the_gate_live(tmp_path):
    """The design typed a budget: a higher authority than the L7 row, and the
    branch must not fire over it."""
    v, rep = power_gate.evaluate(
        S._project(tmp_path, built_on=S.LIB_B, power_budget=50_000.0), None)
    assert v in ("PASS", "FAIL")
    assert rep.get("reason_class") is None


def test_an_explicit_caller_budget_keeps_the_gate_live(tmp_path):
    """A caller that states a requirement has taken the authority on itself."""
    v, rep = power_gate.evaluate(S._project(tmp_path, built_on=S.LIB_B),
                                 50_000.0)
    assert v in ("PASS", "FAIL")
    assert rep.get("reason_class") is None


def test_a_design_that_states_NO_signoff_row_at_all_still_REFUSES(tmp_path):
    """The other NOT_DETERMINED shape, and the one this ruling does NOT touch:
    nothing declared a budget anywhere. That is outstanding work and stays
    BLOCKED_BY_UPSTREAM -- the whole reason `signoff_row_found` had to reach
    the consumer."""
    v, rep = power_gate.evaluate(
        S._project(tmp_path, built_on=S.LIB_B, l7=False), None)
    assert v == "INCOMPLETE"
    assert rep["reason_class"] == taxonomy.BLOCKED_BY_UPSTREAM
    assert rep["missing_authority"]


def test_no_power_report_at_all_still_REFUSES(tmp_path):
    """A design with no budget for this technology AND no measured number is
    not an N/A: there is nothing to declare N/A about."""
    proj = S._project(tmp_path, built_on=S.LIB_B)
    (proj / "reports" / "phase3" / "power.rpt").unlink()
    v, rep = power_gate.evaluate(proj, None)
    assert v == "INCOMPLETE"
    assert rep["reason_class"] == taxonomy.BLOCKED_BY_UPSTREAM
