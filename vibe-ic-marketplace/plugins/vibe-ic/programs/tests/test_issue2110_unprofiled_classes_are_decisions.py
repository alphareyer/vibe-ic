#!/usr/bin/env python3
"""#2110 — the classes that are STILL `null`, each skipped for a MEASURED
reason, and pinned so the skip stays a decision.

#2094 made every registered ic_class appear in `registered_class_profiles`,
`null` meaning a DECLARED gap. Lanes have since profiled several of them one at
a time. What has never existed is the other half of that record: WHY the
remaining ones are still null. "Nobody got to it yet" and "this class cannot be
profiled from what the DB carries" are different facts, and a map of nulls
cannot tell them apart — which is how the second one quietly becomes the first
and then becomes nobody's problem.

Every reason below was MEASURED against the shipped DB, not asserted, and each
is re-measured here rather than quoted:

  pure_analog                        the DB carries NO craft for a design with
                                     no digital datapath. Measured: zero entries
                                     whose text names an LDO, a bandgap, an
                                     amplifier, a charge pump or a regulator.
                                     Its two analog-adjacent entries are the
                                     `data_converter` class's craft.
  aid_class_half_duplex_single_wire  measured: zero entries whose text names a
                                     half-duplex, single-wire or open-drain
                                     bidirectional line — the whole substance of
                                     the class.
  mixed_signal_otp                   a two-conjunct class where ONE conjunct has
                                     craft (`has_otp` -> the single fuse-array
                                     entry) and the other, the analog half, has
                                     none. Half a derivation is not one.
  digital_arithmetic_primitive       the classifier's TERMINAL CATCH-ALL. The
                                     registry says in its own words that designs
                                     landing there "are UNCLASSIFIED, not
                                     classified"; profiling it would score the
                                     classifier's uncertainty as the design's
                                     deficiency.
  unknown_protocol_class             the registry says it means "Detection
                                     failed" — the same category, and there is
                                     nothing to select FOR.
  bare_fpga                          not a design family at all: the registry
                                     defines it by having NO silicon target, and
                                     the classifier reaches it when there are no
                                     L docs yet. A context pack has no subject.
  digital_cmd_driven                 the one remaining class that HAS craft in
                                     the DB and no readable corpus design: seven
                                     classified doc-sets, zero with an input the
                                     retrieval can read. Its decision is a
                                     conjunction one of whose conjuncts is
                                     itself a disjunction, so "the features
                                     alone determine it" — the condition under
                                     which a no-corpus class may be profiled —
                                     is a judgement, not a reading. REFERRED UP,
                                     not decided here.

chip-AGNOSTIC: the checks read the shipped DB and registry only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import ic_expert_backup_pack as PACK  # noqa: E402
import ic_expert_db_query as Q        # noqa: E402

_DB = _PROGRAMS.parent / "agents" / "ic_expert_db" / "ic_expert_db.json"
_REGISTRY = _PROGRAMS / "ic_class_registry.json"

# Every class this lane deliberately left unprofiled, with the ONE-LINE reason.
_SKIPPED = {
    "pure_analog": "no craft in the DB for a design with no digital datapath",
    "aid_class_half_duplex_single_wire": "no craft in the DB for a single-wire "
                                         "half-duplex line",
    "mixed_signal_otp": "only one of its two conjuncts has craft",
    "digital_arithmetic_primitive": "the classifier's terminal catch-all",
    "unknown_protocol_class": "means 'detection failed'; nothing to select for",
    "bare_fpga": "not a design family; defined by having no silicon target",
    "digital_cmd_driven": "no readable corpus design; the features-alone "
                          "condition is a judgement, referred up",
}


def _db() -> dict:
    return json.loads(_DB.read_text())


def _entry_text_matches(rx: str) -> list:
    pat = re.compile(rx, re.I)
    return [e["ic_class"] for e in _db()["entries"]
            if pat.search(e["ic_class"] + " " + " ".join(e.get("lessons", [])))]


# ── the skips are declared gaps, not silence ──────────────────────────────

@pytest.mark.parametrize("cls", sorted(_SKIPPED))
def test_each_skipped_class_is_registered_and_still_a_DECLARED_gap(cls):
    """Registered, present in the map, mapped to null, and reported to a
    consumer as NOT_PROFILED rather than as "no such class". Those last two are
    different facts and collapsing them is how a gap goes silent."""
    registered = {c["name"] for c in json.loads(_REGISTRY.read_text())["classes"]}
    assert cls in registered
    prof = _db()["registered_class_profiles"]
    assert cls in prof, f"{cls} is not even named in the map — that is silence"
    assert prof[cls] is None, f"{cls} is profiled; this file is out of date"
    d = PACK.class_first_disposition(cls)
    assert d["registered"] is True and d["profile"] == "NOT_PROFILED"
    assert d["db_class_selection"] == "LEXICAL_UNCONFINED"


# A class that is null HERE only because the branch that profiles it has not
# landed yet. This is a HAND-OFF, not a decision, and it names the lane that
# owns it — so a reader can tell "somebody is doing this" apart from "somebody
# decided not to", which is the whole point of the file.
# EMPTY, and kept rather than deleted. It held `processor_cpu` while lane
# czprofile1's branch was in flight; that landed, so the class left the null set
# and the entry left this dict. The dict stays because the DISTINCTION is what
# matters and the next hand-off will need it — and because an empty one makes
# the accounting below strict again, which is the state it should be in
# whenever nothing is actually in flight.
_PENDING_ELSEWHERE: dict = {}


def test_every_null_class_is_accounted_for():
    """MEMBERSHIP, not count, and by set UNION rather than by addition: every
    class still mapped to null is either a recorded SKIP with a measured reason
    or a recorded HAND-OFF that names its lane. A class in neither is one nobody
    has considered, and that is the state this file exists to make impossible.

    Stated as containment in both directions rather than as one equality,
    because the two populations move for different reasons: a hand-off leaves
    `nulls` the moment its lane lands, while a skip must stay in it."""
    nulls = {k for k, v in _db()["registered_class_profiles"].items()
             if not k.startswith("_") and v is None}
    assert nulls - set(_SKIPPED) <= set(_PENDING_ELSEWHERE), (
        f"null and unaccounted for: "
        f"{sorted(nulls - set(_SKIPPED) - set(_PENDING_ELSEWHERE))}")
    assert set(_SKIPPED) <= nulls, (
        f"recorded as skipped but no longer null: {sorted(set(_SKIPPED) - nulls)}")
    assert not (set(_SKIPPED) & set(_PENDING_ELSEWHERE)), (
        "a class cannot be both a decision and a hand-off")


# ── the reasons, RE-MEASURED rather than quoted ───────────────────────────

def test_pure_analog_has_no_craft_in_the_db():
    assert _entry_text_matches(
        r"\bLDO\b|bandgap|\bamplifier\b|\bop-?amp\b|charge\s+pump|"
        r"\bvoltage\s+regulator\b") == []


def test_the_single_wire_half_duplex_class_has_no_craft_in_the_db():
    assert _entry_text_matches(
        r"half[-\s]duplex|single[-\s]wire|one[-\s]wire|open[-\s]drain") == []


def test_mixed_signal_otp_has_craft_for_only_one_of_its_two_conjuncts():
    """The OTP conjunct has exactly one entry; the analog conjunct has none.
    Both halves are asserted, because "there is some craft" would have been
    enough to profile it and is not the same claim."""
    otp = _entry_text_matches(r"\bOTP\b|\bfuse\b|antifuse")
    assert otp, "the OTP conjunct has no craft either; the reason has changed"
    assert len(otp) == 1, f"the OTP craft has grown to {otp}; re-take the decision"
    assert _entry_text_matches(
        r"\bLDO\b|bandgap|\bamplifier\b|charge\s+pump") == []


def test_the_two_catch_alls_say_so_in_the_registry_itself():
    """The reason for these two is the registry's own words, so it is read from
    the registry rather than restated here — a quotation that drifts from its
    source is worse than no quotation."""
    reg = {c["name"]: c for c in json.loads(_REGISTRY.read_text())["classes"]}
    basis = reg["digital_arithmetic_primitive"].get("class_tree_node_basis", "")
    assert "UNCLASSIFIED, not classified" in basis
    assert "CATCH-ALL" in basis.upper()
    assert "Detection failed" in reg["unknown_protocol_class"].get("description", "")


def test_bare_fpga_is_defined_by_having_no_silicon_target():
    reg = {c["name"]: c for c in json.loads(_REGISTRY.read_text())["classes"]}
    assert "no silicon target" in reg["bare_fpga"].get("description", "")


def test_digital_cmd_driven_does_have_craft_which_is_why_it_is_referred_up():
    """The one skip that is NOT "there is nothing to select". Asserting the
    craft EXISTS is what keeps this an open question rather than a closed one:
    if it were empty, the reason would be the same as pure_analog's and no
    ruling would be needed."""
    assert _entry_text_matches(r"command-driven|command\s+protocol|opcode")


# ── and the skipped classes really are left alone ─────────────────────────

@pytest.mark.parametrize("cls", sorted(_SKIPPED))
def test_a_skipped_class_retrieves_exactly_what_no_class_retrieves(cls):
    """The declared-gap contract: passing an unprofiled class must be
    byte-identical to passing no class at all. This is what makes "we left it
    null" a statement about the map and not about the design."""
    prompt = ("A command-driven digital block with an opcode dispatcher, a "
              "trimmed analog reference and a single-wire host interface.")
    assert Q.query(prompt, k=5) == Q.query(prompt, k=5, ic_class=cls)
