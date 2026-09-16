#!/usr/bin/env python3
"""A hygiene gate silent on BOTH arms under a live exemption refused every landing.

MEASURED on 30772a457 (v1.20.93), running
`programs/tests/test_landing_merge_verdict.py` on a host where the hermetic arms
can actually start -- inside the pinned image they skip as NOT_MEASURED, so 24
ids never run and the file reads green from there:

    test_end_to_end_post_bootstrap_equal_corpus_uses_ordinary_delta   FAILED
      REFUSE INCOMPLETE EVIDENCE — HYGIENE_NO_VERDICT_EITHER_SIDE:DRC PASS is
                                   not vacuous (tiny/openpdkx)
      ... and the same for three sibling gates ...

THE DEFECT. `hygiene_finding_delta` reports every gate that reached no verdict
on both arms in one list, `no_verdict_either_side`, and `landing_merge_verdict`
(since v1.19.88, which made incomplete evidence blocking) turns each entry into
a refusal. That list holds two different things:

  * a gate NOT_CHECKED on both arms under a DATED, REASONED, UNEXPIRED
    `uncheckable_until` -- a disclosure this repository sells on purpose, and
  * a gate that is simply silent, with nothing bought.

Only the second is missing evidence. The first is an unchanged inherited state,
identical on both arms, that NO BRANCH CAN CLEAR -- so the landing was refused
for a reason no candidate could act on.

THE PROOF THAT THIS IS AN INCONSISTENCY AND NOT A POLICY. The same file already
answers this exact question on the EMPTY-base bootstrap path: `_bounded_not_
checked` -- "a dated, reasoned, unexpired disclosure" -- decides which unknowns
may stand in for a measured base, and `test_end_to_end_trusted_verifier_supplies
_the_one_bootstrap_evidence` asserts those FOUR `tiny/openpdkx` labels are
admitted as `bounded_not_checked` and LANDS. The bootstrap arm sees the gates
declared on ONE side; the ordinary arm sees them declared on BOTH, in the same
state, with the same live exemption -- and refused. Strictly more evidence
produced a strictly worse verdict.

NOTHING IS RELAXED, and that is what the controls below are for. The exemption
test is the one the bootstrap path already trusts. An unexempted silence, an
EXPIRED one, and a silence of a kind no exemption speaks to (`OUT_OF_SCOPE`,
`LISTED`, `OTHER_SHARD`) are each still outside the bought subset and still
refuse. A gate silent on only ONE arm still refuses, untouched.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import gate_process_attestation as A      # noqa: E402
import hygiene_finding_delta as H         # noqa: E402
import landing_merge_verdict as V         # noqa: E402

#: The day both records are measured on. `delta` refuses arms measured on
#: different days, so one constant serves both.
TODAY = "2026-08-15"

#: A live bound and its reason, in the shape `repo_hygiene_gates.sh` writes for
#: `uncheckable_until <date> <why>`.
LIVE_UNTIL = "2027-02-28"
LIVE_WHY = "fixture has no macro LEF"

LABEL = "macro OBS not crossed (tiny/openpdkx)"


def _gate(label, state, *, until=None, why=None, expired=False):
    """One gate row as `--summary-json` writes it. Every key always present."""
    return {"label": label, "state": state, "seconds": 1,
            "exempt_until": until, "exempt_reason": why, "corpus": None,
            "exemption_expired": expired, "scope": None}


def _attestation(row):
    rc = {"PASS": 0, "FAIL": 1, "NOT_MEASURED": 2, "WROTE_CORPUS": 0}[row["state"]]
    verdict = ("[PASS] checked" if rc == 0 else
               "[NOT_CHECKED] unavailable" if rc == 2 else "[FAIL] named finding")
    return A.process_attestation(row["label"], verdict + "\n", rc,
                                 ["python3", "checker.py", row["label"]])


def _record(gates):
    counts = {s: sum(g["state"] == s for g in gates) for s in H.TERMINAL_STATES}
    return {
        "listed_only": False, "declared": len(gates),
        "ran": sum(counts[s] for s in H.PROCESS_STATES),
        "decided": counts["PASS"] + counts["FAIL"],
        "passed": counts["PASS"], "failed": counts["FAIL"],
        "not_checked": counts["NOT_MEASURED"],
        "wrote_corpus": counts["WROTE_CORPUS"], "deferred": counts["LISTED"],
        "other_shard": counts["OTHER_SHARD"], "out_of_scope": counts["OUT_OF_SCOPE"],
        "not_checked_unexempted": [g["label"] for g in gates
                                   if g["state"] == "NOT_MEASURED"
                                   and not g.get("exempt_until")],
        "exemptions_expired": [g["label"] for g in gates
                               if g.get("exemption_expired")],
        "wiring_errors": [], "corpora": [], "shard": None, "today": TODAY,
        "gates": gates,
        "process_attestations": [_attestation(g) for g in gates
                                 if g["state"] in H.PROCESS_STATES
                                 and not H._legacy_structural_empty(g)],
    }


def _both_arms(gate):
    """The SAME gate row on base and candidate — the symmetric case."""
    rec = _record([_gate("a gate that passed", "PASS"), gate])
    return H.delta(copy.deepcopy(rec), copy.deepcopy(rec))


# ------------------------------------------------------------------------
# 1. WHAT THE DELTA MUST NOW SAY
# ------------------------------------------------------------------------

def test_a_live_dated_reasoned_exemption_on_both_arms_is_named_as_bought():
    """The subset must be STATED. Before this, a consumer had one list and no
    way to tell a bought silence from a bare one."""
    d = _both_arms(_gate(LABEL, "NOT_MEASURED", until=LIVE_UNTIL, why=LIVE_WHY))
    assert d["no_verdict_either_side"] == [LABEL], d
    assert d["no_verdict_either_side_bounded"] == [LABEL], (
        "a NOT_CHECKED bought on both arms with a live dated reason is exactly "
        "what `_bounded_not_checked` recognises on the bootstrap path", d)


@pytest.mark.parametrize("label,gate", [
    ("unexempted", _gate(LABEL, "NOT_MEASURED")),
    ("expired", _gate(LABEL, "NOT_MEASURED", until="2026-01-01", why="stale",
                      expired=True)),
    ("out of scope", _gate(LABEL, "OUT_OF_SCOPE")),
])
def test_a_silence_nobody_bought_is_reported_but_never_in_the_bought_subset(
        label, gate):
    """THE CONTROLS THAT PROVE NOTHING WAS RELAXED, one per way of being silent.

    Each is still in `no_verdict_either_side` and still refuses; only the live
    dated exemption above is excused. `OUT_OF_SCOPE` is here because no
    exemption speaks to it at all -- an exemption buys a gate that COULD NOT
    LOOK, not one that had nothing to look at.
    """
    d = _both_arms(gate)
    assert d["no_verdict_either_side"] == [LABEL], (label, d)
    assert d["no_verdict_either_side_bounded"] == [], (label, d)


def test_an_expired_exemption_is_still_a_finding_in_its_own_right():
    """Its own blocking route is untouched: leaving the bought subset must not
    have moved it out of `findings` as well."""
    d = _both_arms(_gate(LABEL, "NOT_MEASURED", until="2026-01-01", why="stale",
                         expired=True))
    assert ["EXEMPTION_EXPIRED", LABEL, ""] in d["carried"], d


def test_a_gate_silent_on_only_one_arm_still_refuses_outright():
    """The ONE-sided case is a different question and this change must not have
    reached it: it is refused before any subset is computed."""
    base = _record([_gate("a gate that passed", "PASS"), _gate(LABEL, "PASS")])
    cand = _record([_gate("a gate that passed", "PASS"),
                    _gate(LABEL, "NOT_MEASURED", until=LIVE_UNTIL, why=LIVE_WHY)])
    with pytest.raises(H.Refusal) as exc:
        H.delta(base, cand)
    assert "reached a verdict on one side and none on the other" in str(exc.value)


# ------------------------------------------------------------------------
# 2. WHAT THE VERDICT MUST NOW DO WITH IT
# ------------------------------------------------------------------------

def _decide(**over):
    """The sibling file's LAND-OK baseline, so this asserts against the REAL
    decision and not a re-typed copy of it that could drift into agreeing."""
    spec = importlib.util.spec_from_file_location(
        "sibling_landing_merge_verdict_tests",
        HERE / "test_landing_merge_verdict.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert hasattr(module, "_decide"), (
        "the sibling test file no longer exposes the LAND-OK baseline `_decide`")
    return module._decide(**over)


def _hygiene(**over):
    rec = {"status": "CLEAN", "introduced": [], "carried": [], "cleared": [],
           "candidate_findings": 0, "base_findings": 0, "declared": 2}
    rec.update(over)
    return rec


def _reasons_naming_no_verdict(verdict):
    return [r for r in verdict.reasons if "NO_VERDICT_EITHER_SIDE" in r]


def test_a_bought_silence_does_not_refuse_the_landing():
    """THE CASE THIS EXISTS FOR. Identical on both arms, bought, and therefore
    not something this branch did or could undo."""
    v = _decide(hygiene=_hygiene(
        no_verdict_either_side=[LABEL],
        no_verdict_either_side_bounded=[LABEL]))
    assert _reasons_naming_no_verdict(v) == [], v.reasons
    assert v.ok is True, v.reasons


def test_an_unbought_silence_still_refuses_the_landing():
    """THE PAIRED CONTROL, and the one that would catch this being turned into
    a blanket excuse: same record, empty subset, still refused."""
    v = _decide(hygiene=_hygiene(no_verdict_either_side=[LABEL],
                                 no_verdict_either_side_bounded=[]))
    assert _reasons_naming_no_verdict(v) == [
        "INCOMPLETE EVIDENCE — HYGIENE_NO_VERDICT_EITHER_SIDE:" + LABEL], v.reasons
    assert v.ok is False


def test_a_record_that_does_not_state_the_subset_keeps_todays_refusal():
    """A helper too old to state the subset must not be READ as having stated
    an empty-of-blocking one. Absence is the strict direction."""
    v = _decide(hygiene=_hygiene(no_verdict_either_side=[LABEL]))
    assert _reasons_naming_no_verdict(v) == [
        "INCOMPLETE EVIDENCE — HYGIENE_NO_VERDICT_EITHER_SIDE:" + LABEL], v.reasons
    assert v.ok is False


def test_the_subset_cannot_excuse_a_label_it_does_not_hold():
    """The bought subset is the one list here that BUYS something, so it is the
    one a wrong record could use. It must be a subset of the list it qualifies;
    otherwise a record naming any string at all could silence a reason."""
    v = _decide(hygiene=_hygiene(no_verdict_either_side=[],
                                 no_verdict_either_side_bounded=["a label "
                                                                "nothing is silent about"]))
    assert any("HYGIENE_RESULT_INVALID" in r for r in v.reasons), v.reasons
    assert v.ok is False


def test_an_excused_silence_is_still_reported_rather_than_disappearing():
    """Excused is not invisible. It must remain in the record a reader gets,
    or this trades a false refusal for a silent one."""
    v = _decide(hygiene=_hygiene(
        no_verdict_either_side=[LABEL],
        no_verdict_either_side_bounded=[LABEL]))
    assert v.debt.get("hygiene_no_verdict_bought") == [LABEL], v.debt
    assert any(LABEL in n and "uncheckable exemption" in n for n in v.notes), (
        "the note must say WHY it did not block", v.notes)
