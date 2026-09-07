#!/usr/bin/env python3
"""E2 of vibe-ic#2092 — `invoked` equals the sum of every bucket, all named.

MEASURED DEFECT
===============
The `opentitan_aes` run of lane icaes (8HD-4, v1.17.38), in its own
`reports/audit/phase23_completion_audit.json`, three rounds on one tree:

    registered_gate_count 246   invoked_gate_count 246
    passed_gate_count     186   failed_gate_count     0
    not_invocable_gate_count 0

186 + 0 + 0 is not 246, and that artefact names no third thing for the other
60 to be — so "246 invoked" could be quoted as coverage and be wrong by 60
gates that made no statement about this design.

WHAT WAS ALREADY CLOSED, AND WHAT WAS NOT. `p0_gate_census` (landed after the
version that wrote the artefact above) publishes the complete partition, so
the buckets are NAMED on this tree. What no code did was CHECK that the four
published counts are that partition, or PRINT the equation with its terms. A
partition nobody checks against the numbers beside it is a second place for
the two to disagree — which is the whole class this issue is about.

DERIVED, NOT ENUMERATED
=======================
Every term comes from `census["by_verdict"]`. A verdict word introduced later
— the way `NOT-MEASURED` was split out of `INCOMPLETE` in #2063 — is in the
equation the day it is introduced, not the day somebody remembers to list it.
`test_a_verdict_word_nobody_has_written_yet_is_already_a_term` is that
promise, run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flow_compliance_check as F  # noqa: E402


def _records(**counts):
    out, n = [], 0
    for verdict, k in counts.items():
        for _ in range(k):
            n += 1
            out.append({"name": f"gate_{n}_check", "verdict": verdict,
                        "message": "", "evidence": {}})
    return out


#: The icaes run's own population: 246 registered, 186 PASS, and 60 gates that
#: answered about something they could not see.
_ICAES = dict(PASS=186, SKIP=36, BLOCKED=11, INCOMPLETE=13)


def _eq(records, **override):
    census = F.p0_gate_census(records)
    counts = dict(
        registered=len(records),
        invoked=sum(1 for r in records
                    if r["verdict"] != F.NOT_INVOCABLE_VERDICT),
        not_invocable=sum(1 for r in records
                          if r["verdict"] == F.NOT_INVOCABLE_VERDICT),
        passed=sum(1 for r in records if r["verdict"] == "PASS"),
    )
    counts.update(override)
    return F.gate_population_equation(census, **counts)


def test_the_icaes_population_closes_and_the_equation_is_printed():
    e = _eq(_records(**_ICAES))
    assert e["holds"] is True, e["breaks"]
    assert e["terms"]["registered_gate_count"] == 246
    assert e["terms"]["invoked_gate_count"] == 246
    assert e["terms"]["by_verdict_total"] == 246
    # EVERY bucket is named, not just the three the old artefact published.
    assert set(e["terms"]["by_verdict"]) == {
        "PASS", "SKIP", "BLOCKED", "INCOMPLETE", "FAIL", "NOT_INVOCABLE"}
    assert "invoked 246 = " in e["printed"]
    for word in ("BLOCKED 11", "INCOMPLETE 13", "PASS 186", "SKIP 36"):
        assert word in e["printed"], word


def test_a_count_that_stops_projecting_the_partition_breaks_by_name():
    """THE POINT OF THE EQUATION. Each of the four counts, moved on its own."""
    recs = _records(**_ICAES)
    for term, wrong, needle in (
            ("invoked", 183, "invoked_gate_count 183"),
            ("passed", 182, "passed_gate_count 182"),
            ("not_invocable", 7, "not_invocable_gate_count 7")):
        e = _eq(recs, **{term: wrong})
        assert e["holds"] is False, term
        assert any(needle in b for b in e["breaks"]), (term, e["breaks"])


def test_not_invocable_is_the_one_bucket_outside_invoked():
    recs = _records(PASS=4, NOT_INVOCABLE=3)
    e = _eq(recs)
    assert e["holds"] is True, e["breaks"]
    assert e["terms"]["invoked_gate_count"] == 4
    assert e["terms"]["not_invocable_gate_count"] == 3
    assert e["terms"]["registered_gate_count"] == 7
    assert F.NOT_INVOCABLE_VERDICT not in e["terms"]["invoked_buckets"]


def test_a_verdict_word_nobody_has_written_yet_is_already_a_term():
    """DERIVED. A tier introduced later must land in the equation without
    this file, or `p0_gate_census`, being edited."""
    recs = _records(PASS=2, A_WORD_FROM_THE_FUTURE=5)
    e = _eq(recs)
    assert e["holds"] is True, e["breaks"]
    assert e["terms"]["by_verdict"]["A_WORD_FROM_THE_FUTURE"] == 5
    assert "A_WORD_FROM_THE_FUTURE 5" in e["printed"]


def test_a_registry_larger_than_the_record_set_is_DISCLOSED_not_asserted():
    """THE FALSE POSITIVE THIS EQUATION FIRST HAD, and the input that measured
    it. `test_flow_compliance_check_gate.py::test_strict_structural_only_
    structural_gates` runs `--phase 2 --strict-structural` on a thin tree: the
    umbrella dispatches 2 of 246 registered gates, and the run says so itself
    ("registered=246 invoked=2 no_verdict=244 — PARTIAL").

    The first draft read `registered == sum(by_verdict)` and turned that green
    run RED. 244 gates leaving no record is not count drift — it is the
    dispatch loop correctly not invoking gates whose inputs this scope does
    not contain, and `invoked_gate_count: 2` is the honest number. The gap is
    DISCLOSED by name; it is never asserted to be zero."""
    e = _eq(_records(PASS=2), registered=246, invoked=2, not_invocable=0)
    assert e["holds"] is True, e["breaks"]
    assert e["registered_gates_with_no_record"] == 244
    assert e["terms"]["registered_gate_count"] == 246
    assert e["terms"]["invoked_gate_count"] == 2


def test_more_records_than_registered_gates_is_incoherent_and_breaks():
    """The one registry statement that is not merely narrow. A record set
    larger than the registry cannot be a projection of it."""
    e = _eq(_records(PASS=10), registered=4, invoked=10, not_invocable=0)
    assert e["holds"] is False
    assert any("more records than there are registered gates" in b
               for b in e["breaks"]), e["breaks"]


def test_counts_that_do_not_partition_the_records_break():
    """What replaced the registry assertion, and the thing that can silently
    stop being true: `invoked` and `not_invocable` must partition the records
    the census counted."""
    e = _eq(_records(PASS=8, NOT_INVOCABLE=2), invoked=8, not_invocable=1)
    assert e["holds"] is False
    assert any("do not partition the records they project" in b
               for b in e["breaks"]), e["breaks"]


def test_no_umbrella_is_not_an_equation_that_holds():
    for census, reg, inv in ((None, 246, 246),
                             ({"by_verdict": {}}, None, None)):
        e = F.gate_population_equation(census, reg, inv, None, None)
        assert e["holds"] is None
        assert "NOT_MEASURED is not a pass" in e["not_measured"]


def test_the_coverage_warning_travels_with_the_equation():
    """`invoked` counts SKIP and BLOCKED too. The sentence saying so is
    carried beside the number, not left in a docstring."""
    e = _eq(_records(**_ICAES))
    assert "is not coverage" not in (e["invoked_is_not_coverage"] or "")
    assert "SKIP and BLOCKED included" in e["invoked_is_not_coverage"]
