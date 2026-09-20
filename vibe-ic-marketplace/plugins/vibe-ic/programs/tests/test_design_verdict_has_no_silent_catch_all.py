#!/usr/bin/env python3
"""The phase-2 verdict aggregator has no catch-all — RETIRED ON PURPOSE and rewritten.

WHAT THIS FILE USED TO PIN, and why it is rewritten rather than deleted.

`design_one_shot_runner._aggregate_verdict` classified four FAIL statuses and
`WAIVED`, and returned `PASS` for everything else. Its own comment named the
hazard — "everything this function does not enumerate falls through to the
catch-all `return "PASS"`" — and the hazard bit twice: `BLOCKED` (#544) was
added by hand after the first time, and `SKIP`, the status the runner emitted
MORE than any other, reached the same silent PASS.

This file's job was to make that visible: an unknown status had to be REPORTED
rather than absorbed. And it ended with a deliberate pin,
`test_the_published_verdicts_are_unchanged`, carrying this instruction:

    If a later change promotes SKIP to PASS_WITH_WAIVERS, this test must be
    retired ON PURPOSE, with the published-result restatement acknowledged —
    not quietly adjusted to match new behaviour.

R-0915-85 IS THAT CHANGE, AND THIS IS THAT ACKNOWLEDGEMENT. The ruling reduced
the vocabulary to five words at the producers. `SKIP` no longer exists: each of
its 118 sites in this runner was classified individually into `PASS` (the step
ran and correctly had nothing to change), `NOT_APPLICABLE` (a declaration in the
input says it does not apply) or `NOT_MEASURED` with a reason. The published
phase-2 results ARE restated by that, and the four frozen replays in
`test_r0915_85_the_frozen_replays.py` are where the restatement is measured
rather than asserted.

WHAT IS PINNED NOW, and it is STRICTLY STRONGER than what this file pinned
before. The old property was "an unknown status is visible". The new one is
that an unknown status cannot exist: `verdict.parse` raises
`UnknownVerdictWord` at the row that carries it, which is earlier and louder
than a stderr line nobody greps. The catch-all is gone by construction rather
than by enumeration, so there is no list left to forget to extend — which is
what every one of the three hand-patches above was.
"""
from __future__ import annotations

import sys

import pytest

from _plugin_tree import plugin_path

sys.path.insert(0, str(plugin_path() / "programs"))

import verdict as V  # noqa: E402

RUNNER = plugin_path() / "programs" / "design_one_shot_runner.py"


@pytest.fixture(scope="module")
def agg():
    """The real runner's aggregator and its row type, imported not copied."""
    import design_one_shot_runner as D  # noqa: PLC0415 — heavy, module-scoped
    return D._aggregate_verdict, D.StepResult


def _v(fn, rows):
    return fn(rows)


# ── the catch-all is gone BY CONSTRUCTION ────────────────────────────────

def test_an_unknown_status_cannot_reach_the_aggregator_at_all(agg):
    """The replacement for "an unknown status is reported, not absorbed".

    It is not reported, because it cannot be built: the row refuses it.
    """
    _, SR = agg
    with pytest.raises(V.UnknownVerdictWord):
        SR("b", "SOME_NEW_STATUS")


def test_the_deleted_word_this_file_was_written_about_is_refused(agg):
    """`SKIP` — 118 sites in this runner, and the one that reached the
    catch-all. It is not a status any more."""
    _, SR = agg
    with pytest.raises(V.UnknownVerdictWord):
        SR("lec", "SKIP")


def test_the_aggregator_has_no_return_pass_catch_all_in_its_source():
    """Read off the SHIPPED text, because a re-implementation here would pass
    whatever the runner did — the failure mode this whole file is about."""
    src = RUNNER.read_text(encoding="utf-8")
    start = src.index("def _aggregate_verdict")
    body = src[start:start + 4000]
    assert 'return "PASS"' not in body, (
        "the aggregator has a literal catch-all again; R-0915-85 replaced the "
        "whole function with verdict.run_verdict precisely to delete it")
    assert "_V.run_verdict" in body


# ── the roll-up, in both directions ──────────────────────────────────────

def test_a_run_of_passes_is_a_pass(agg):
    fn, SR = agg
    assert _v(fn, [SR("a", "PASS"), SR("b", "PASS")]) == "PASS"


def test_one_fail_fails_the_run(agg):
    fn, SR = agg
    assert _v(fn, [SR("a", "PASS"), SR("b", "FAIL")]) == "FAIL"


def test_a_waived_step_makes_the_run_pass_with_waivers(agg):
    fn, SR = agg
    assert _v(fn, [SR("a", "PASS_WITH_WAIVERS", detail="a row to close")]) == \
        "PASS_WITH_WAIVERS"


def test_an_unmeasured_step_keeps_the_run_off_pass(agg):
    """THE RESTATEMENT, in one assertion. This is the case that used to be
    `SKIP` and used to return a clean `PASS` — and that phase 3 read as
    PASS_WITH_WAIVERS over the same forty-four steps, which is the
    two-vocabularies defect R-0915-85 deleted."""
    fn, SR = agg
    assert _v(fn, [SR("a", "PASS"),
                   SR("lec", "NOT_MEASURED",
                      reason_class="inconclusive")]) == "NOT_MEASURED"


def test_a_not_applicable_step_costs_the_run_nothing(agg):
    """The other direction: a declaration in the input is not a hole."""
    fn, SR = agg
    assert _v(fn, [SR("a", "PASS"),
                   SR("A3", "NOT_APPLICABLE",
                      declared_by="L5: no analog blocks")]) == "PASS"


def test_a_run_with_no_contributing_step_is_not_a_pass(agg):
    """The empty numerator the old catch-all turned green."""
    fn, SR = agg
    assert _v(fn, []) == "NOT_MEASURED"
