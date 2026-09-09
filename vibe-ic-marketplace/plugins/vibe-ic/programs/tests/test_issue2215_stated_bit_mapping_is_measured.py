#!/usr/bin/env python3
"""vibe-ic#2215 — a bit obligation the prose STATES and no extractor reads is a
MISSED FACT, not silence, and the COMPLETE verdict must measure that.

MEASURED ON main 4245ea72e, AFTER the #2215 bit-mapping grammar landed:

    'Convert a 12-bit width to a larger width of 20-bit. When shift is one,
     result[11:4] takes sample[7:0] rotated left by three.'

        spec_numeric_pack_extract.extract  -> 0 bit items
        spec_complete_extract.assess_spec  -> COMPLETE, gaps = 0,
                'every port placed ...; stated structures captured'

A control-conditioned per-bit obligation, stated in plain words, read by
nothing, and graded COMPLETE with zero gaps by the verdict a consumer reads to
decide whether a spec is safe for deterministic generation.

THE CAUSE IS NOT THE GRAMMAR'S WIDTH. `_verdict`'s claim that stated structures
were captured is ASSERTED, never MEASURED: its `gaps` list is built solely by
`_place_interface`, which classifies PORT WIDTHS, so no structural extractor can
contribute a "missed structure". Widening the grammar moves the blind spot to
the next prose form; measuring the claim is what removes it.

THE ANTI-TAUTOLOGY TEST BELOW IS THE LOAD-BEARING ONE. The presence predicate
must not be the extraction grammar, or the gap can never fire.
"""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import spec_numeric_pack_extract as NP  # noqa: E402
import spec_complete_extract as SCE  # noqa: E402

BASE = "Convert a 12-bit width to a larger width of 20-bit. "

#: Stated plainly, and outside the landed grammar. This is the measured case.
UNREAD = BASE + ("When shift is one, result[11:4] takes sample[7:0] rotated "
                 "left by three.")
#: Stated and inside the landed grammar.
READ = BASE + "Bits result[10:0] always equal sample[10:0]."
#: The issue's third fixture — genuinely absent, must stay absent.
UNSPECIFIED = ("Implement a configurable data format block; detailed mapping "
               "is not yet supplied.")

_BIT_KINDS = ("bit_mapping", "bit_preserve")


def _bits(prompt):
    return [i for i in NP.extract(prompt) if i.get("kind") in _BIT_KINDS]


def _gap(spec):
    return [g for g in (spec.get("gaps") or [])
            if g.get("type") == "bit_mapping_not_extracted"]


# --------------------------------------------------------------------------
# The anti-tautology guard.
# --------------------------------------------------------------------------
def test_presence_is_not_the_extraction_grammar():
    """There must EXIST prose the predicate calls stated and the grammar cannot
    read. Without this the gap is unreachable and the guard is decoration."""
    assert NP.states_bit_mapping(UNREAD) is True
    assert _bits(UNREAD) == [], (
        "the grammar grew to cover this case — pick a new UNREAD fixture; the "
        "point of this test is that SOME stated obligation is still unread")


def test_a_stated_but_unread_bit_mapping_is_a_missed_fact_not_silence():
    spec = SCE.assess_spec(UNREAD, ["sample", "shift"], ["result"])
    gaps = _gap(spec)
    assert len(gaps) == 1, spec.get("gaps")
    assert gaps[0]["kind"] == "INCOMPLETE_EXTRACTION_GAP"
    assert spec["completeness"] == "INCOMPLETE_EXTRACTION_GAP"


def test_a_stated_and_read_bit_mapping_leaves_no_gap():
    """The other half of the same guard: a gap that fires on everything is as
    useless as one that fires on nothing."""
    assert _bits(READ), "fixture no longer exercises the read path"
    assert _gap(SCE.assess_spec(READ, ["sample"], ["result"])) == []


def test_a_prompt_with_no_bit_claim_raises_no_structural_gap():
    assert NP.states_bit_mapping(UNSPECIFIED) is False
    assert _gap(SCE.assess_spec(UNSPECIFIED, ["sample"], ["result"])) == []


def test_a_bare_mention_of_a_mapping_is_not_a_bit_obligation():
    assert NP.states_bit_mapping(
        "The mapping between input and output is documented elsewhere.") is False


# --------------------------------------------------------------------------
# A PORT DECLARATION IS A WIDTH, NOT A BIT OBLIGATION.
# This is the false positive that turned an 8-to-3 priority encoder from
# COMPLETE into EXTRACTION_GAP on the first cut of the predicate.
# --------------------------------------------------------------------------
ENCODER = """Design the module named `encoder8` - an 8-to-3 priority encoder.

### Inputs:
- in [7:0]: the request bits.

### Outputs:
- out [2:0]: the index of the highest set bit.
"""


def test_a_port_declaration_is_not_a_bit_obligation():
    assert NP.states_bit_mapping(ENCODER) is False
    assert _gap(SCE.assess_spec(ENCODER, ["in"], ["out"])) == []


@pytest.mark.parametrize("line", [
    "- data [31:0]: the payload.",
    "- addr [11:0]: the address, one entry per set.",
    "* q [7:0]: the output register.",
])
def test_no_width_declaration_line_is_read_as_a_bit_claim(line):
    assert NP.states_bit_mapping(line) is False


# --------------------------------------------------------------------------
# The predicate fires on the shapes a bit obligation actually takes.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prose", [
    "result[3] is the majority of sample[3], sample[2] and sample[1].",
    "When mode is one, out[7:0] is sample[7:0] reversed.",
    "Do not invert any source bit.",
    "Preserve all twelve source bits.",
])
def test_the_predicate_sees_a_stated_bit_obligation(prose):
    assert NP.states_bit_mapping(prose) is True
