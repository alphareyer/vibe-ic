"""The sign-off summary publishes what the run measured, after it exists (icspm2).

TWO MEASURED FAILURES, ONE PROGRAM
==================================

1. THE SUMMARY WAS WRITTEN BEFORE ITS EVIDENCE. On a FIRST run of `spm` x
   gf180mcuD in a fresh project directory:

       phase3/final/metrics.json          05:12:48   <- the summary
       reports/phase3/drc_router.rpt      05:12:47   <- runner-written, in time
       reports/phase3/drc_signoff.rpt     05:11:47   <- runner-written, in time
       reports/phase3/drc_router.json     05:15:37   <- THREE MINUTES LATER
       reports/phase3/drc_signoff.json    05:15:39   <- THREE MINUTES LATER

   The two `.json` are written by `drc_report_check`, and the only thing that
   runs it is the FLOW's own gate clause inside the completion audit. So the
   summary recorded `route__drc_errors` / `klayout__drc_error__count` as
   NOT_MEASURED ("producers found: none"), the audit created the reports, and
   its own `--check` then reported the record stale. It passed on the
   predecessor lane only because that lane re-used ONE project directory, so a
   previous attempt's reports were already on disk.

2. FIVE AXES THE RUN MEASURED AND NEVER PUBLISHED.
   `every_required_metric_key_has_a_producer` reported

       axis 'drv' / 'antenna' / 'ir' / 'em' / 'equivalence' / 'eco_readiness'
       IS NOT PROVEN BY ANY RUN IN THIS CORPUS

   on a run that had measured every one of them — `SIGNOFF_DRV_CENSUS
   max_fanout violators=0`, `antenna.json net_violations 0 / pin_violations 0`,
   `ir_drop.json worst_ir_uv 1630.0`, `em_current_authority.json jmax_screen
   offender_count 0 / worst_utilization 0.449207`, `lec_post_layout.json
   verdict PROVEN_EQUIVALENT`, `spare_preservation.json survived 6`. Every
   record was on disk under a name the canonical vocabulary did not carry.

   And `_drv` returned `text.count("VIOLATED")` — the whole report's violated
   row count — for EVERY check, so max-slew and max-cap were the same number
   under two names.
"""
import json
import re
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import signoff_metrics_aggregate as sma   # noqa: E402
import _metric_vocabulary as mv           # noqa: E402

STA_RPT = """\
SIGNOFF_CHECK_TYPES_REPORTED recovery removal max_slew min_pulse_width max_capacitance max_fanout
SIGNOFF_DRV_CENSUS_BEGIN the tool's own violator count for every check type requested above
SIGNOFF_DRV_CENSUS max_slew violators=3
SIGNOFF_DRV_CENSUS max_fanout violators=0
SIGNOFF_DRV_CENSUS max_capacitance violators=1
some row VIOLATED
another row VIOLATED
a third row VIOLATED
a fourth row VIOLATED
"""


def _proj(tmp_path, **files):
    p = tmp_path / "proj"
    for rel, payload in files.items():
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(payload if isinstance(payload, str)
                     else json.dumps(payload))
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 2a. the DRV census — per check, not one total under three names
# ---------------------------------------------------------------------------
def test_each_drv_check_gets_its_own_count(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": STA_RPT})
    assert sma._drv(p, "max_slew").value == 3
    assert sma._drv(p, "max_capacitance").value == 1
    assert sma._drv(p, "max_fanout").value == 0


def test_the_three_counts_are_not_the_same_number(tmp_path):
    """The defect, stated directly: before the fix all three were 4 — the
    report's whole VIOLATED row count."""
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": STA_RPT})
    vals = {c: sma._drv(p, c).value
            for c in ("max_slew", "max_capacitance", "max_fanout")}
    assert len(set(vals.values())) == 3, vals


def test_an_unavailable_counter_is_not_a_zero(tmp_path):
    rpt = STA_RPT.replace("max_fanout violators=0",
                          "max_fanout violators=UNAVAILABLE")
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": rpt})
    cell = sma._drv(p, "max_fanout")
    assert not cell.measured
    assert "UNAVAILABLE" in cell.reason


def test_a_check_the_census_omits_is_not_a_zero(tmp_path):
    rpt = STA_RPT.replace("SIGNOFF_DRV_CENSUS max_fanout violators=0\n", "")
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": rpt})
    assert not sma._drv(p, "max_fanout").measured


def test_a_report_with_no_census_falls_back_and_says_so(tmp_path):
    """Every already-published run has no census; it must still answer, and the
    basis must disclose that the figure is not per-check."""
    rpt = "\n".join(l for l in STA_RPT.splitlines()
                    if "SIGNOFF_DRV_CENSUS" not in l) + "\n"
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": rpt})
    cell = sma._drv(p, "max_slew")
    assert cell.value == 4
    assert "NOT specific to max_slew" in cell.basis


def test_a_check_the_run_never_requested_is_still_refused(tmp_path):
    rpt = STA_RPT.replace(" max_fanout\n", "\n", 1)
    p = _proj(tmp_path, **{"reports/phase3/sta_spef_based.rpt": rpt})
    assert not sma._drv(p, "max_fanout").measured


# ---------------------------------------------------------------------------
# 2b. the five axes — each measured, each refusing honestly
# ---------------------------------------------------------------------------
def test_ir_worst_drop_is_volts_from_microvolts(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/ir_drop.json": {
        "worst_ir_uv": 1630.0, "supply_measured": True,
        "unmeasured_reason": None}})
    assert sma._ir_worst_drop_v(p).value == pytest.approx(0.00163)


@pytest.mark.parametrize("doc", [
    {"worst_ir_uv": 1630.0, "supply_measured": False},
    {"worst_ir_uv": 1630.0, "unmeasured_reason": "PSM found no source"},
    {"supply_measured": True},
])
def test_an_ir_number_the_tool_did_not_establish_is_refused(tmp_path, doc):
    p = _proj(tmp_path, **{"reports/phase3/ir_drop.json": doc})
    assert not sma._ir_worst_drop_v(p).measured


def test_em_violations_and_ratio_come_from_the_jmax_screen(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/em_current_authority.json": {
        "jmax_screen": {"skip_reason": None, "offender_count": 0,
                        "summary": {"segments_screened": 6949,
                                    "worst_utilization": 0.449207}}}})
    assert sma._em(p, "violations").value == 0
    assert sma._em(p, "worst_ratio").value == pytest.approx(0.449207)


def test_a_skipped_or_empty_em_screen_is_not_a_zero(tmp_path):
    for screen in ({"skip_reason": "no Jmax reference", "offender_count": 0,
                    "summary": {"segments_screened": 0}},
                   {"skip_reason": None, "offender_count": 0,
                    "summary": {"segments_screened": 0}}):
        p = _proj(tmp_path / str(id(screen)),
                  **{"reports/phase3/em_current_authority.json":
                     {"jmax_screen": screen}})
        assert not sma._em(p, "violations").measured
        assert not sma._em(p, "worst_ratio").measured


def test_the_equivalence_verdict_is_the_checkers_own(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/lec_post_layout.json": {
        "verdict": "PROVEN_EQUIVALENT", "equivalent": True,
        "proven_points": 12, "total_points": 12, "skipped": False}})
    assert sma._equivalence(p).value == "PROVEN_EQUIVALENT"


def test_a_skipped_lec_proves_nothing(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/lec_post_layout.json": {
        "verdict": "SKIPPED", "skipped": True}})
    assert not sma._equivalence(p).measured


def test_spares_counts_what_survived_not_what_was_inserted(tmp_path):
    p = _proj(tmp_path, **{"reports/spare_preservation.json": {
        "inserted": 6, "survived": 4, "verdict": "FAIL"}})
    cell = sma._spares(p)
    assert cell.value == 4, "an inserted spare a later pass removed reaches nothing"
    assert "4 of 6" in cell.basis


def test_a_clean_antenna_result_answers_the_total(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/antenna.json": {
        "net_violations": 0, "pin_violations": 0, "routing_incomplete": False}})
    assert sma._antenna_total(p).value == 0


@pytest.mark.parametrize("nets,pins", [(1, 0), (0, 2), (3, 5)])
def test_a_dirty_antenna_result_refuses_to_invent_a_total(tmp_path, nets, pins):
    """The two populations OVERLAP — a net may violate at several pins — so
    their sum is not the total, and a dirty design is never certified here."""
    p = _proj(tmp_path / f"{nets}_{pins}", **{"reports/phase3/antenna.json": {
        "net_violations": nets, "pin_violations": pins,
        "routing_incomplete": False}})
    cell = sma._antenna_total(p)
    assert not cell.measured
    assert "do not sum" in cell.reason or "not the total" in cell.reason


def test_an_incomplete_route_is_not_antenna_signoff_evidence(tmp_path):
    p = _proj(tmp_path, **{"reports/phase3/antenna.json": {
        "net_violations": 0, "pin_violations": 0, "routing_incomplete": True}})
    assert not sma._antenna_total(p).measured


def test_every_new_key_is_a_RULE_and_a_vocabulary_entry(tmp_path):
    """A producer with no vocabulary entry is invisible to the axis gate, and a
    vocabulary entry with no producer is a promise nothing keeps."""
    emitted = {k for k, _label, _fn in sma.RULES}
    for canonical, spelling in (
            ("timing.drv.max_fanout_violations",
             "design__max_fanout_violation__count"),
            ("power.ir.worst_drop_v", "power__ir__worst_drop_v"),
            ("reliability.em.violations", "reliability__em__violation__count"),
            ("reliability.em.worst_ratio", "reliability__em__worst_ratio"),
            ("equivalence.verdict", "equivalence__verdict"),
            ("design_for_eco.spares.count", "design_for_eco__spares__count"),
            ("physical.antenna.violations", "antenna__violation__count")):
        assert spelling in emitted, f"{spelling} is in no RULE"
        entries = mv.SYNONYMS.get(canonical, ())
        assert any(s == spelling and rel == mv.SAME_FACT
                   for s, rel, _why in entries), (canonical, entries)


# ---------------------------------------------------------------------------
# 1. the ordering — the evidence exists before the summary reads it
# ---------------------------------------------------------------------------
def test_the_runner_emits_the_drc_attribution_reports_before_aggregating(
        tmp_path):
    import phase3_one_shot_runner as r
    fn = getattr(r, "_emit_drc_attribution_reports", None)
    if fn is None:
        pytest.skip("pre-fix tree has no _emit_drc_attribution_reports")
    p = _proj(tmp_path, **{
        "reports/phase3/drc_router.rpt": "[INFO DRT-0199] Number of violations = 0\n",
        "reports/phase3/drc_signoff.rpt": "total DRC errors: 0\n"})
    rows = fn(p)
    assert {row["json"] for row in rows} == {
        "reports/phase3/drc_router.json", "reports/phase3/drc_signoff.json"}
    for row in rows:
        assert row["status"] == "PRODUCED", row
        assert (p / row["json"]).is_file(), row


def test_an_absent_rpt_produces_nothing_and_says_why(tmp_path):
    import phase3_one_shot_runner as r
    fn = getattr(r, "_emit_drc_attribution_reports", None)
    if fn is None:
        pytest.skip("pre-fix tree has no _emit_drc_attribution_reports")
    p = _proj(tmp_path, **{"reports/keep": "x"})
    rows = fn(p)
    assert all(row["status"] == "NOT_APPLICABLE" for row in rows), rows
    assert all(not (p / row["json"]).is_file() for row in rows)
    assert all(row.get("reason") for row in rows)


def test_the_argv_is_the_one_the_flow_declares():
    """Two spellings of one invocation is two places to get wrong. If the flow
    clause moves, this test is where it is noticed."""
    import phase3_one_shot_runner as r
    jobs = getattr(r, "_DRC_ATTRIBUTION_JOBS", None)
    if jobs is None:
        pytest.skip("pre-fix tree has no _DRC_ATTRIBUTION_JOBS")
    yaml = (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text()
    for rel_json, _rel_rpt, argv in jobs:
        want = "drc_report_check . " + " ".join(argv) + f" --json {rel_json}"
        assert want in yaml, (
            f"the runner's invocation is not the flow's:\n  runner: {want}")


def test_the_verdict_of_the_spawned_gate_is_recorded_not_discarded(tmp_path):
    """`drc_report_check` is a GATE; this call site wants only the attribution
    record. The status is bound and written down, so the decision is on the
    record rather than inferred from silence."""
    import phase3_one_shot_runner as r
    fn = getattr(r, "_emit_drc_attribution_reports", None)
    if fn is None:
        pytest.skip("pre-fix tree has no _emit_drc_attribution_reports")
    p = _proj(tmp_path, **{
        "reports/phase3/drc_signoff.rpt": "total DRC errors: 0\n"})
    rows = fn(p)
    produced = [row for row in rows if row["status"] == "PRODUCED"]
    assert produced, rows
    for row in produced:
        assert "rc" in row
        assert row["blocking_here"] is False
        assert row["why_advisory_here"]
