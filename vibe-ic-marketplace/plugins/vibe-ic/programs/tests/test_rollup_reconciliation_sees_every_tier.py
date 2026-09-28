#!/usr/bin/env python3
"""The roll-up reconciliation must be able to see every tier the producer prints.

WHAT WENT WRONG
===============
``final_report_generate._TALLY_LABEL_TO_BUCKET`` was a hand-typed list of nine
labels. The producer's vocabulary moved on without it, and three tiers ended up
with no key at all: ``STRUCTURE-ONLY``, ``INCOMPLETE`` and
``PASS-VOIDED-BY-DEPENDENCY``.

The blindness is two-sided, which is why nothing caught it:

* ``_parse_audit_tally`` keeps a label only when the map resolves it
  (``if bucket is not None``) — so those three were dropped on the way in;
* ``_reconcile_rollup``'s second loop admits a bucket only when it is
  ``in _TALLY_LABEL_TO_BUCKET.values()`` — so a bucket the roll-up populated and
  the tally never named was filtered right back out.

A disagreement in those tiers was therefore reported as AGREEMENT, and the
"Roll-up reconciliation FAILED" banner could not render in either direction.
``PASS-VOIDED-BY-DEPENDENCY`` is the sharpest of the three: it is the word #671
introduced *precisely* to say "this is NOT a pass", and the reconciliation could
not see it.

``_flow_verdict_tiers.PRODUCER_STATUSES`` is the authoritative vocabulary and
already carries an anti-drift test — "a word added there without a home below is
a test failure, not a silent escape". That protection never reached this map,
because the map was a COPY. The repair derives it instead.

WHAT THIS FILE LOCKS
====================
* every producer status resolves to a bucket, checked against the shared
  vocabulary rather than against a list repeated here
* the deliberate report-side aliases still win
* a disagreement in a previously-blind tier is actually reported
"""
from __future__ import annotations

import sys

import pytest

from _plugin_tree import plugin_path

PROGRAMS = plugin_path() / "programs"
sys.path.insert(0, str(PROGRAMS))

frg = pytest.importorskip("final_report_generate")
# R-0915-85 — `_flow_verdict_tiers` is DELETED, folded into `verdict`. An
# `importorskip` on a module that no longer exists turns a whole file SILENT:
# these seven tests stopped running and nothing said so except the
# zero-test census. Imported outright now, so a missing vocabulary module is
# a collection ERROR and not a quiet skip.
import die_level_deck_rule_attribution as D  # noqa: E402
import verdict as tiers  # noqa: E402


def test_every_producer_status_resolves_to_a_bucket():
    """Totality against the SHARED vocabulary, not against a list typed here.

    This is the assertion that fails on the unfixed module, naming the three
    tiers that had no key.
    """
    unmapped = sorted(
        s for s in tiers.PRODUCER_STATUSES
        if frg._TALLY_LABEL_TO_BUCKET.get(s) is None
    )
    assert not unmapped, (
        f"the producer can emit {unmapped}, and the roll-up reconciliation has "
        f"no bucket for them — a disagreement in those tiers is reported as "
        f"agreement")


def test_the_deliberate_aliases_still_win():
    """Guard the guard. Deriving identity mappings must not clobber the
    report-side renaming, or the tally label would stop folding into its
    bucket and the fix would trade one drift for another.

    R-0915-85 — there is exactly ONE alias left, and that is the point: the
    declared words are their own buckets, and `WAIVED-DEFERRED` is the CONTRACT
    label the audit's tally line prints beside `PASS_WITH_WAIVERS`. `SKIPPED`
    and `SKIPPED-CONDITION` were two spellings of one tier and are one word.
    """
    m = frg._TALLY_LABEL_TO_BUCKET
    assert m["WAIVED-DEFERRED"] == "PASS_WITH_WAIVERS", m.get("WAIVED-DEFERRED")
    assert m["PASS_WITH_WAIVERS"] == "PASS_WITH_WAIVERS"
    assert "SKIPPED" not in m and "SKIPPED-CONDITION" not in m, m


def test_the_nine_original_mappings_are_unchanged():
    """Pinned explicitly: this change ADDS coverage and rewrites nothing.

    If a later change alters one of these, it must be done on purpose — a
    silently different bucket would move published reconciliation results.
    """
    # The declared words each have their own bucket, plus the contract alias.
    # Published reconciliation results DO move, which is why this pin is
    # rewritten rather than deleted -- the new map is stated in full here so a
    # later drift is still a test failure.
    expected = {
        "PASS": "PASS",
        "PASS_WITH_WAIVERS": "PASS_WITH_WAIVERS",
        D.TIER_PASS_WITH_ATTRIBUTION: D.TIER_PASS_WITH_ATTRIBUTION,
        "FAIL": "FAIL",
        "NOT_MEASURED": "NOT_MEASURED",
        "NOT_APPLICABLE": "NOT_APPLICABLE",
        "WAIVED-DEFERRED": "PASS_WITH_WAIVERS",
    }
    assert set(frg._TALLY_LABEL_TO_BUCKET) == set(expected), \
        sorted(frg._TALLY_LABEL_TO_BUCKET)
    for k, v in expected.items():
        assert frg._TALLY_LABEL_TO_BUCKET.get(k) == v, (
            f"{k} used to map to {v}, now maps to "
            f"{frg._TALLY_LABEL_TO_BUCKET.get(k)}")


# R-0915-85 — the three formerly blind tiers are `NOT_MEASURED` now (and a
# PASS carrying a disclosure). What this case measures is that a roll-up count
# in a tier the tally never names is REPORTED rather than filtered out by the
# `values()` guard, so it is asked of a tier that is in the vocabulary and of
# one that is NOT -- the second being the shape a future word would arrive in.
@pytest.mark.parametrize("tier", ["NOT_MEASURED", "NOT_APPLICABLE"])
def test_a_disagreement_in_a_formerly_blind_tier_is_reported(tier):
    """Behaviour, not just the map: the reconciliation must actually report it.

    The roll-up says the tier has 2 steps; the tally never names it. That is a
    disagreement — the checker did not account for those steps — and it was
    being filtered out by the `values()` guard.
    """
    disagreements = frg._reconcile_rollup(
        rollup={"PASS": 5, tier: 2},
        tally={"PASS": 5},
    )
    assert tier in disagreements, (
        f"a roll-up count of 2 in {tier} against a tally that never names it "
        f"was reported as agreement; got {disagreements}")
    assert disagreements[tier] == (2, 0), disagreements[tier]


def test_an_unregistered_tier_cannot_reach_the_rollup_at_all():
    """The `values()` guard still filters a bucket nobody registered, and that
    is now UNREACHABLE rather than a blind spot: `verdict.parse` refuses a
    word outside the declaration at the row that carries it, so no producer can put one in a
    roll-up. Asserted at the refusal rather than left to the filter.
    """
    with pytest.raises(tiers.UnknownVerdictWord):
        tiers.parse("SOME-UNREGISTERED-TIER")
    assert frg._reconcile_rollup(
        rollup={"PASS": 5, "SOME-UNREGISTERED-TIER": 2},
        tally={"PASS": 5}) == {}


def test_an_actual_agreement_is_still_reported_as_agreement():
    """The other arm. A reconciliation that flags everything is as useless as
    one that flags nothing, and would still pass the test above."""
    assert frg._reconcile_rollup(
        rollup={"PASS": 5, "FAIL": 1},
        tally={"PASS": 5, "FAIL": 1},
    ) == {}
