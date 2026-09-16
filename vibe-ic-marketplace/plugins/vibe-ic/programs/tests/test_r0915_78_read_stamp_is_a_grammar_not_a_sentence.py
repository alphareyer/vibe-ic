"""`analog_incremental_decimator.read_stamp` reads a FORMAL GRAMMAR, never a
sentence — measured, so the `_NOT_PROSE` entry beside it is evidence and not an
assertion.

WHY THIS FILE EXISTS. `prose_polarity_consulted_check --ratchet` named
`analog_incremental_decimator::read_stamp` the first time it was pushed: the
function regex-matches over a text argument and writes the captured values into
a declaration, which is the #706/#711 shape — a value taken out of a sentence
and published as fact, with nothing asking whether the sentence DENIES it.

THE ARGUMENT. The line this reads is not a sentence and cannot be one. It is
written by exactly one producer — `stamp()`, four functions above it in the
same file — and it is a closed key=value grammar:

    * analog_incremental_decimator: mode=<word> window_clocks=<int>
      order=<int> coeff=<float> feedback_delay_clocks=<int>

Every one of the five fields must be PRESENT and must CAST to its declared
type, and a field that does not is a refusal BY NAME (`BAD_STAMP`, listing
which), never a default. THE GRAMMAR HAS NO NEGATION FORM: there is no way to
write "this deck is NOT decoded incrementally" in it — a decode that is not
declared is simply ABSENT, and absence is already how this function answers
(`NO_STAMP`). So the two things a denial could do — remove a declaration, or
reverse one — are respectively already handled and not expressible.

BOTH DIRECTIONS, ON THE SAME TRIALS:
  * every denial token of `_prose_polarity`'s own vocabulary — both tiers, the
    five CJK spellings included — each carrying a DECLARATION-SHAPED payload
    that mints a RIVAL decode, in each of the positions a sentence can
    physically occupy around the stamp, moves 0 published answers;
  * the same tokens spliced INSIDE the stamp's own payload also move 0, which
    is the grammar working: a bare word is not a term in it;
  * NEGATIVE CONTROL: change a VALUE — `window_clocks=256` to `512` — and the
    published answer moves. So the fixture could have moved, and the zero is a
    statement about the grammar rather than about a fixture that could never
    have answered differently.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _prose_polarity as P                          # noqa: E402
import analog_incremental_decimator as D             # noqa: E402

# The vocabulary, from `_prose_polarity` itself — never a second copy.
TOKENS = [t.replace(r"\b", "").replace(r"\w*", "").replace("-?", "")
          for t in (P._DENIAL_CORE + "|" + P._DENIAL_RETIRED).split("|")]
TOKENS = [t for t in (t.strip() for t in TOKENS) if t]

STAMP = D.stamp(D.MODE_INCREMENTAL, 256, 2, 0.2499, 1)
TRUTH = {"mode": "incremental", "window_clocks": 256, "order": 2,
         "coeff": 0.2499, "feedback_delay_clocks": 1}

#: A payload shaped like a DECLARATION of a rival decode — the thing a denial
#: would have to carry for the polarity question to have a referent at all.
RIVAL = ("mode=free_running window_clocks=512 order=3 coeff=0.05 "
         "feedback_delay_clocks=0")


def _deck(*lines: str) -> str:
    return "\n".join(("* a testbench", *lines, ".end")) + "\n"


def test_the_vocabulary_is_read_from_prose_polarity_and_is_not_empty():
    """A zero measured over an empty vocabulary is not a measurement."""
    assert len(TOKENS) >= 15, TOKENS
    assert any(not t.isascii() for t in TOKENS), "the CJK tier is missing"


def test_the_stamp_reads_as_written():
    got, detail = D.read_stamp(_deck(STAMP))
    assert got == TRUTH, detail


def test_no_denial_in_any_neighbouring_position_moves_the_answer():
    """Every token, carrying a rival DECLARATION, in each position a sentence
    can physically occupy around the stamp."""
    moved = []
    for tok in TOKENS:
        sentence = f"* the decode is {tok} {RIVAL}"
        for position in ("above", "below", "both"):
            if position == "above":
                deck = _deck(sentence, STAMP)
            elif position == "below":
                deck = _deck(STAMP, sentence)
            else:
                deck = _deck(sentence, STAMP, sentence)
            got, _ = D.read_stamp(deck)
            if got != TRUTH:
                moved.append((tok, position, got))
    assert not moved, moved


def test_no_denial_inside_the_payload_moves_the_answer():
    """The grammar working: a bare word is not a term in it, so a denial
    spliced INTO the stamp's own payload changes nothing it publishes."""
    moved = []
    for tok in TOKENS:
        for deck in (_deck(STAMP.replace(": ", f": {tok} ")),
                     _deck(STAMP + f" {tok}"),
                     _deck(STAMP.replace("order=", f"{tok} order="))):
            got, _ = D.read_stamp(deck)
            if got != TRUTH:
                moved.append((tok, got))
    assert not moved, moved


def test_the_negative_control_a_VALUE_moves_the_published_answer():
    """So the fixture could have moved. Without this the zeros above would be
    a property of a fixture that can never answer differently."""
    got, _ = D.read_stamp(_deck(STAMP.replace("window_clocks=256",
                                              "window_clocks=512")))
    assert got is not None
    assert got["window_clocks"] == 512
    assert got != TRUTH


def test_a_denial_that_removes_the_grammar_is_ALREADY_a_refusal_by_name():
    """The only thing a denial can actually do to this reader: leave the
    declaration absent. That is refused by name and never defaulted."""
    for tok in TOKENS[:6]:
        got, detail = D.read_stamp(
            _deck(f"* analog_incremental_decimator: the decode is {tok} here"))
        assert got is None
        assert detail["reason"] == D.BAD_STAMP
        assert set(detail["missing_or_unreadable"]) == set(D.STAMP_FIELDS)
    got, detail = D.read_stamp(_deck("* nothing about a decode at all"))
    assert got is None
    assert detail["reason"] == D.NO_STAMP


def test_the_grammar_has_no_negation_form_and_stamp_cannot_write_one():
    """The producer's own output is checked, so the claim is about the pair
    and not about the reader alone: every field `stamp` writes is a
    `name=value`, and there is no term in it that could carry a polarity."""
    line = D.stamp(D.MODE_INCREMENTAL, 256, 2, 0.2499, 1)
    body = line.split(":", 1)[1]
    terms = [t for t in body.split() if t]
    assert terms and all("=" in t for t in terms), terms
    assert {t.split("=", 1)[0] for t in terms} == set(D.STAMP_FIELDS)
