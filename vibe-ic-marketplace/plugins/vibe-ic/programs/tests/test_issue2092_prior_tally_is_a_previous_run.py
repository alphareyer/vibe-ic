#!/usr/bin/env python3
"""E3 of vibe-ic#2092 — `tally_delta`'s prior is the previous RUN, or ABSENT.

MEASURED DEFECT
===============
The `opentitan_aes` run of lane icaes (8HD-4, written by v1.17.38), in its
own `reports/audit/phase23_completion_audit.json`, publishes:

    tally_delta.classification  MEASUREMENT_CHANGE
    tally_delta.statement       "The tally moved while the design inputs
                                 stayed BYTE-IDENTICAL. What changed is the
                                 ruler, not the design."
    tally_delta.prior.run_at    2026-09-06T23:53:21.940686+00:00
    tally_delta.current.run_at  2026-09-06T23:53:24.101597+00:00
    prior.tally.step_counts     sums to 13 steps
    current.tally.step_counts   sums to 69 steps

Every word of that statement is true and the record is still wrong. 69 minus
13 is not a movement in a tally: the two numbers count DIFFERENT STEP
POPULATIONS, because the prior audit at that path was a narrower-scope
invocation 2.16 s earlier in the same run. Their difference is not a delta,
and the artefact recorded no ruler scope for the prior, so no reader could see
that.

WHAT IS ENFORCED
================
The prior is a previous run of THIS measurement, or it is ABSENT and the
reason is named. Two rejections, both measurable from the two artefacts:
a `POPULATION_FLAGS` value that differs, and a step population that differs
under one and the SAME flow definition.

WHAT IS DELIBERATELY NOT A REJECTION — each has its own control below:
a newer plugin version, a changed flow definition, a changed strict flag, an
identical `run_at`. Those are the ruler moving over ONE population, which is
the case `MEASUREMENT_CHANGE` exists for and keeps.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import design_input_digest as did  # noqa: E402

_SCOPE = {"flow": "phase1_phase2_phase3", "phase": "all", "stage": None,
          "stage_id": None, "exclude_step": []}


def _audit(*, run_at, counts, design="D", meas="M", flow_def="F", scope=None):
    return {
        "run_at": run_at,
        "verdict": "FAIL",
        "step_counts": dict(counts),
        "passed_gate_count": 186,
        "failed_gate_count": 0,
        "design_input_digest": {"sha256": design},
        "measurement": {"id": meas, "flow_def_sha256": flow_def,
                        "ruler_flags": dict(scope or _SCOPE)},
    }


#: The two tallies exactly as the icaes artefact carries them.
_PRIOR_COUNTS = {"PASS": 1, "FAIL": 2, "MISSING": 2, "WAIVED": 1,
                 "INCOMPLETE": 4, "PASS_VOIDED_BY_DEPENDENCY": 3}
_CURRENT_COUNTS = {"PASS": 3, "FAIL": 12, "MISSING": 16, "WAIVED": 3,
                   "SKIPPED-CONDITION": 22, "PARTIALLY-VACUOUS": 1,
                   "INCOMPLETE": 5, "PASS_VOIDED_BY_DEPENDENCY": 7}


def test_the_measured_icaes_pair_is_refused_and_publishes_no_prior():
    prior = _audit(run_at="2026-09-06T23:53:21.940686+00:00",
                   counts=_PRIOR_COUNTS, meas="f79149",
                   scope=dict(_SCOPE, phase="2"))
    current = _audit(run_at="2026-09-06T23:53:24.101597+00:00",
                     counts=_CURRENT_COUNTS, meas="d678cb")
    r = did.classify(prior, current)
    assert r["classification"] == "NOT_COMPARABLE"
    assert r["prior"] is None, "an incomparable prior must not be published"
    assert r["prior_eligibility"]["comparable"] is False
    assert "phase" in r["prior_eligibility"]["population_flags_differing"]
    assert r["prior_eligibility"]["prior_step_population"] == 13
    assert r["prior_eligibility"]["current_step_population"] == 69
    # The sentence the old record made must be gone, not merely qualified.
    assert "BYTE-IDENTICAL" not in r["statement"]


def test_a_differing_population_flag_is_refused_on_its_own():
    """Scope alone, with the two populations the SAME size."""
    for flag, other in (("phase", "2"), ("stage", 3), ("stage_id", "stage1"),
                        ("exclude_step", ["7"]), ("flow", "other_flow")):
        prior = _audit(run_at="t1", counts={"PASS": 4},
                       scope=dict(_SCOPE, **{flag: other}))
        current = _audit(run_at="t2", counts={"PASS": 4})
        r = did.classify(prior, current)
        assert r["classification"] == "NOT_COMPARABLE", flag
        assert flag in r["prior_eligibility"]["population_flags_differing"]


def test_a_population_that_changed_size_under_one_flow_def_is_refused():
    """Size alone, with every scope flag identical."""
    prior = _audit(run_at="t1", counts={"PASS": 4, "FAIL": 9})
    current = _audit(run_at="t2", counts={"PASS": 4})
    r = did.classify(prior, current)
    assert r["classification"] == "NOT_COMPARABLE"
    assert not r["prior_eligibility"]["population_flags_differing"]
    assert "one and the same flow definition" in r["statement"]


# ── the controls: what must STILL be compared ────────────────────────────


def test_a_newer_ruler_over_one_population_is_still_a_measurement_change():
    """THE CASE THIS MUST NOT EAT. Same tree, same scope, same flow def, a
    newer plugin — the finding `tally_delta` was built for."""
    prior = _audit(run_at="t1", counts={"PASS": 22, "FAIL": 5}, meas="M-old")
    current = _audit(run_at="t2", counts={"PASS": 6, "FAIL": 21},
                     meas="M-new")
    r = did.classify(prior, current)
    assert r["classification"] == "MEASUREMENT_CHANGE"
    assert r["prior"] is not None
    assert r["prior_eligibility"]["comparable"] is True


def test_a_flow_definition_that_gained_a_step_is_not_an_incomparable_pair():
    """A flow def that grows legitimately changes the population size. The
    size rejection is guarded on a SHARED flow_def_sha256 for exactly this."""
    prior = _audit(run_at="t1", counts={"PASS": 4}, flow_def="F1")
    current = _audit(run_at="t2", counts={"PASS": 4, "MISSING": 1},
                     flow_def="F2", meas="M2")
    r = did.classify(prior, current)
    assert r["classification"] == "MEASUREMENT_CHANGE"
    assert r["prior"] is not None


def test_a_design_change_over_one_population_still_reads_as_the_design():
    prior = _audit(run_at="t1", counts={"PASS": 4, "FAIL": 1}, design="D1")
    current = _audit(run_at="t2", counts={"PASS": 5, "FAIL": 0}, design="D2")
    r = did.classify(prior, current)
    assert r["classification"] == "DESIGN_CHANGE"
    assert r["attributable_to_design"] is True


def test_an_identical_run_at_is_not_by_itself_a_rejection():
    """`run_at` is minted per invocation from the clock, so two audits that
    share one are a fixture, not a producer state — and `UNEXPLAINED_TALLY_
    MOVE` already owns that pair. Rejecting on it would have deleted that
    classification's only input."""
    prior = _audit(run_at="t", counts={"PASS": 4, "FAIL": 1})
    current = _audit(run_at="t", counts={"PASS": 5, "FAIL": 0})
    r = did.classify(prior, current)
    assert r["classification"] == "UNEXPLAINED_TALLY_MOVE"


def test_an_unverifiable_pair_says_not_measured_and_does_not_pass():
    """No ruler flags on either side and no shared flow definition: whether
    the two judged one population was NOT MEASURED, and the record says so
    rather than defaulting to comparable."""
    prior = {"run_at": "t1", "step_counts": {"PASS": 1},
             "design_input_digest": {"sha256": "D"}, "verdict": "FAIL"}
    current = {"run_at": "t2", "step_counts": {"PASS": 2},
               "design_input_digest": {"sha256": "D"}, "verdict": "FAIL"}
    r = did.classify(prior, current)
    assert r["prior_eligibility"]["comparable"] is None
    assert "NOT_MEASURED is not a pass" in \
        r["prior_eligibility"]["not_measured"]
    # NOT a rejection: the classification path is unchanged.
    assert r["classification"] == "UNEXPLAINED_TALLY_MOVE"


def test_every_population_flag_is_also_a_ruler_flag():
    """POPULATION_FLAGS is a SUBSET of RULER_FLAGS. A name in one and not the
    other would mean two runs over different populations could share a
    `measurement.id`."""
    assert set(did.POPULATION_FLAGS) <= set(did.RULER_FLAGS)
