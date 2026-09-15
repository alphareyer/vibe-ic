"""R-0915-26 — the growth declaration must cover every waiver the machinery
itself creates, and must not cover them with a sentence that is false.

MEASURED on `subservient` x gf180mcuD (lane icsub2, run r13, the run's own
waivers.json):

    waived_steps            ids [39, 6, 36]
    growth_rationale_covers     [39, 6]
    -> waiver_growth_check rc=1

39 and 6 are ENV_UNAVAILABLE deferrals materialised by `waivers_materialize`.
36 is the TAPE-OUT waiver `signoff_audit._emit_tapeout_waiver_entry` appends
when the sign-off reaches its evidence threshold with a DRC/LVS slot credited
via a waiver -- and that emitter wrote the file without re-deriving the growth
declaration, so the document went on describing the population it had before.

THE FIX IS NOT TO PUT 36 UNDER THE EXISTING SENTENCE.  That sentence says
"MACHINERY-SANCTIONED ENV_UNAVAILABLE deferrals, and nothing else"; a sign-off
tier waiver is not one, and covering it there would make the rationale false --
which is worse than a red gate, because the gate at least says something true.
So the declaration is COMPOSED from the kinds the document actually holds, each
clause naming its own basis and its own ids, and an entry neither clause can
justify stays UNCOVERED so the gate still refuses it.
"""
import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import waivers_materialize as WM  # noqa: E402
import signoff_audit as SA  # noqa: E402


def _env(i):
    return {"id": i, "_env_unavailable": True, "auto_synthesized": True,
            "_waiver_condition": "refuse if the step executes",
            "verdict_tier": "ENV_UNAVAILABLE", "ticket": "ORGANIC-env",
            "review_required": True, "approver": "cap-gap-tier"}


def _tier(i):
    return {"id": i, "verdict_tier": "PASS_WITH_WAIVERS",
            "ticket": "ORGANIC-20260613-tapeout-drc-waiver",
            "review_required": True, "approver": "tapeout-review-pending",
            "evidence": ["reports/audit/tapeout_checklist.json"]}


# ---------------------------------------------------------- the direction that must work

def test_the_tapeout_waiver_is_covered():
    d = {"waived_steps": [_env(39), _env(6), _tier(36)]}
    WM.declare_growth(d)
    assert d["growth_rationale_covers"] == [39, 6, 36]


def test_it_is_covered_by_a_clause_that_is_TRUE_of_it():
    """Two kinds, two clauses, each naming its own ids -- the tape-out waiver
    must NOT be folded under the ENV_UNAVAILABLE sentence."""
    d = {"waived_steps": [_env(39), _env(6), _tier(36)]}
    WM.declare_growth(d)
    r = d["growth_rationale"]
    assert r.count("Covers:") == 2
    env_at = r.index("ENV_UNAVAILABLE deferrals")
    tier_at = r.index("SIGN-OFF TIER waiver")
    # 36 is named beside the tier clause, not beside the ENV one
    assert r.index("[36]") > tier_at > env_at
    assert "[39, 6]" in r


def test_a_document_of_one_kind_only_carries_one_clause():
    d = {"waived_steps": [_env(39), _env(6)]}
    WM.declare_growth(d)
    assert d["growth_rationale_covers"] == [39, 6]
    assert d["growth_rationale"].count("Covers:") == 1
    assert "SIGN-OFF TIER" not in d["growth_rationale"]


# ---------------------------------------------------------- the direction that must still refuse

def test_a_hand_added_waiver_is_still_not_covered():
    """THE RATCHET. A waiver of a kind neither clause justifies is left out of
    `covers`, so `waiver_growth_check` still refuses the document -- which is
    the whole reason the mechanism exists."""
    d = {"waived_steps": [_env(39), _env(6), _tier(36),
                          {"id": 21, "reason": "hand added by a human"}]}
    WM.declare_growth(d)
    assert 21 not in d["growth_rationale_covers"]
    assert d["growth_rationale_covers"] == [39, 6, 36]


def test_an_entry_that_only_LOOKS_like_a_tier_waiver_is_not_covered():
    """Classification is by the entry's OWN recorded fields, all of them: a
    tier with no ticket, or no open review, is not a sanctioned tier waiver."""
    for missing in ("ticket", "review_required"):
        e = _tier(36)
        e.pop(missing)
        d = {"waived_steps": [_env(39), e]}
        WM.declare_growth(d)
        assert d["growth_rationale_covers"] == [39], missing


def test_a_document_with_no_classifiable_waiver_declares_nothing():
    """A rationale standing over a population it cannot justify is a blanket."""
    d = {"waived_steps": [{"id": 21, "reason": "hand added"}],
         "growth_rationale": "stale", "growth_rationale_covers": [99]}
    WM.declare_growth(d)
    assert "growth_rationale" not in d
    assert "growth_rationale_covers" not in d


def test_classification_reads_no_step_id():
    """Chip- and flow-AGNOSTIC: the same entry shapes under different ids
    classify the same way, so this is not bound to one flow's numbering."""
    a = {"waived_steps": [_env(39), _tier(36)]}
    b = {"waived_steps": [_env("alpha"), _tier("beta")]}
    WM.declare_growth(a)
    WM.declare_growth(b)
    assert a["growth_rationale_covers"] == [39, 36]
    assert b["growth_rationale_covers"] == ["alpha", "beta"]


# ---------------------------------------------------------- end to end, through the emitter

def test_the_emitter_redeclares_when_it_appends(tmp_path):
    """The defect was in the WRITE path, not in the derivation: prove the
    emitter itself leaves a covered document behind."""
    (tmp_path / "waivers.json").write_text(json.dumps({
        "waived_steps": [_env(39), _env(6)],
        "growth_rationale": "old",
        "growth_rationale_covers": [39, 6],
    }, indent=2))

    class _R:
        summary = {"drc_library_internal_waived": True,
                   "env_unavailable_steps": []}
        findings = []

    SA._emit_tapeout_waiver_entry(tmp_path, _R())
    d = json.loads((tmp_path / "waivers.json").read_text())
    ids = [e["id"] for e in d["waived_steps"]]
    assert SA._TAPEOUT_STEP_ID in ids
    assert set(d["growth_rationale_covers"]) == set(ids)
    assert "SIGN-OFF TIER" in d["growth_rationale"]


def test_the_emitter_is_still_idempotent_and_still_yields_to_a_human(tmp_path):
    """Unchanged contract: a hand-authored waiver for the same step outranks
    the auto entry, and re-running adds nothing."""
    hand = {"id": SA._TAPEOUT_STEP_ID, "reason": "a human wrote this"}
    (tmp_path / "waivers.json").write_text(
        json.dumps({"waived_steps": [hand]}, indent=2))

    class _R:
        summary = {}
        findings = []

    SA._emit_tapeout_waiver_entry(tmp_path, _R())
    d = json.loads((tmp_path / "waivers.json").read_text())
    assert d["waived_steps"] == [hand]
    # and the human's waiver is NOT covered by machinery prose
    assert "growth_rationale_covers" not in d
