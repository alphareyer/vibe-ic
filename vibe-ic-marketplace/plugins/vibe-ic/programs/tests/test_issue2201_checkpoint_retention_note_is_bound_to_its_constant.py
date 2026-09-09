"""tests/test_issue2201_checkpoint_retention_note_is_bound_to_its_constant.py
— vibe-ic#2201

`_cvg_prune_checkpoints` keeps every post-route convergence pass's routed DEF
and prunes them at the decision. Its note explains what that retention costs,
and #2201's complaint was that the cost was ASSERTED: the sentence read "on a
large design eight routed DEFs is real disk", and nothing in the repo made that
`eight` the same eight as `_SHIP_POSTROUTE_CVG_MAX_PASSES`.

That is the whole failure mode, and it is not about prose. The peak the note
describes is not an independent fact — it is `_SHIP_POSTROUTE_CVG_MAX_PASSES` x
one checkpoint, and every figure the note derives from it (the peak in bytes,
its share of `pnr/`, the ceiling on the largest routed DEF in the archive) is
arithmetic that starts there. Change the bound to four and not one character of
the note goes red: the arithmetic quietly becomes false, and the next reader
redoes a retention decision from numbers that no longer describe the code. This
repo has paid for that shape before — a claim in prose going stale silently
while every test around it stays green.

So these tests bind the note to the constant it is derived from, in the same
doc/code-agreement idiom as
`test_multi_corner_sta_basis::test_docstring_no_longer_claims_routed_unconditionally`
over this same module. Four bindings, and the last is what keeps the others from
being decoration:

  * the note NAMES the constant, so its numbers are traceable to code rather
    than being literals a reader has to trust;
  * the note states a MEASURED size for one checkpoint and for the peak — the
    number #2201 asked for — rather than an adjective;
  * the stated peak IS the bound times one checkpoint, so moving the bound
    reddens the note that was derived from it;
  * every count of held checkpoints the note states, in digits or spelled out,
    is the value the module actually holds.

The negative control moves the bound underneath the same note and requires the
arithmetic check to redden, and requires the extractor to still be reading the
same figures when it does. A guard that goes quiet instead of red is the defect,
not the guard.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402


# Two checkpoint DEFs from the same series are not byte-identical, so the note's
# peak cannot be an exact multiple and the check cannot demand one. MEASURED on
# the note's own figures: the true multiplier is 0.03% off, and the nearest
# WRONG multiplier (bound +/- 1) is 12.5% off. 2% admits the first and rejects
# the second by a factor of six.
_TOLERANCE = 0.02

# The shapes in which the note states a count of held checkpoints. Each is
# arithmetic a reader is meant to be able to redo, so each must start from the
# bound the code holds.
_DIGIT_COUNT = (
    re.compile(r"(\d+)\s+held\b"),               # "peak, 8 held at once"
    re.compile(r"(\d+)\s*[x×]\s*one\b"),         # "8 x one fill-free routed DEF"
    re.compile(r"bound is already\s+(\d+)"),     # "The bound is already 8"
)
# The sentence #2201 was actually filed about spells its count as a word.
_WORD_COUNT = re.compile(r"(\w+)\s+routed DEFs\b")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16}

# Byte figures. The note writes them with spaces as thousand separators, the way
# it prints them for a reader.
_ONE_BYTES = re.compile(r"one checkpoint\s+([\d ]+?)\s*B\b")
_PEAK_BYTES = re.compile(r"peak,\s*\d+\s+held at once\s+([\d ]+?)\s*B\b")


def _note() -> str:
    return R._cvg_prune_checkpoints.__doc__ or ""


def _flat(doc: str) -> str:
    return " ".join((doc or "").split())


def _counts(doc: str):
    """Every count of held checkpoints the note states, first-seen order.

    Membership, never count: the same bound is stated in several places on
    purpose and each one is a separate promise to the reader.

    An unrecognised spelled-out number comes back as its raw token rather than
    being dropped. A parser that silently skips what it cannot read reports a
    clean note for one it never checked — the exact asymmetry these tests exist
    to close.
    """
    flat = _flat(doc)
    out = []
    for pat in _DIGIT_COUNT:
        out.extend(int(n) for n in pat.findall(flat))
    for tok in _WORD_COUNT.findall(flat):
        out.append(_WORDS.get(tok.lower(), tok))
    return out


def _bytes_figures(doc: str):
    """(one checkpoint, peak) in bytes, or None for either the note omits."""
    flat = _flat(doc)
    one = _ONE_BYTES.search(flat)
    peak = _PEAK_BYTES.search(flat)
    to_i = lambda m: int(m.group(1).replace(" ", ""))   # noqa: E731
    return (to_i(one) if one else None, to_i(peak) if peak else None)


def test_the_note_names_the_constant_its_arithmetic_starts_from():
    assert "_SHIP_POSTROUTE_CVG_MAX_PASSES" in _note(), (
        "the retention note states a peak in held checkpoints; it must name the "
        "constant that fixes that peak, or the number is a literal the reader "
        "has no way to re-derive")


def test_the_note_states_what_one_checkpoint_and_the_peak_actually_cost():
    """#2201 asked for a number. An adjective ('real disk') is not one, and the
    two checks below are over these figures — a note that stated neither would
    pass them by giving them nothing to read."""
    one, peak = _bytes_figures(_note())
    missing = [n for n, v in (("one checkpoint", one), ("the peak", peak))
               if v is None]
    assert not missing, (
        f"the retention note states no measured size for {', '.join(missing)}, "
        f"so the cost it argues from is asserted rather than measured")
    assert one > 0 and peak > 0


def test_the_stated_peak_is_the_bound_times_one_checkpoint():
    bound = R._SHIP_POSTROUTE_CVG_MAX_PASSES
    one, peak = _bytes_figures(_note())
    assert one and peak, "no figures to check"
    dev = abs(peak - bound * one) / peak
    assert dev <= _TOLERANCE, (
        f"the note's peak is {peak} B and one checkpoint is {one} B, a "
        f"multiplier of {peak / one:.2f}, but _SHIP_POSTROUTE_CVG_MAX_PASSES is "
        f"{bound} ({dev:.1%} off). Every figure the note derives from that peak "
        f"— its share of pnr/, the ceiling on the largest archived DEF — is "
        f"computed from the wrong count")


def test_every_count_of_held_checkpoints_is_the_bound_the_code_holds():
    bound = R._SHIP_POSTROUTE_CVG_MAX_PASSES
    counts = _counts(_note())
    assert counts, (
        "the note states no count of held checkpoints, so this check would pass "
        "by finding nothing")
    wrong = [c for c in counts if c != bound]
    assert not wrong, (
        f"the note states {wrong} held checkpoint(s) where "
        f"_SHIP_POSTROUTE_CVG_MAX_PASSES is {bound}")


def test_the_arithmetic_check_reddens_when_the_bound_moves_under_the_note():
    """The negative control. Without it a green above could be a green that could
    not fail — the note and the constant could have drifted apart and this file
    would still be reporting agreement."""
    bound = R._SHIP_POSTROUTE_CVG_MAX_PASSES
    one, peak = _bytes_figures(_note())
    assert one and peak, "nothing to control against"

    moved = bound + 1          # the same note, a code base that moved beneath it
    dev = abs(peak - moved * one) / peak
    assert dev > _TOLERANCE, (
        f"moving the bound to {moved} left the note's own arithmetic inside the "
        f"{_TOLERANCE:.0%} tolerance ({dev:.1%}), so the check cannot tell a "
        f"note that tracks the code from one that does not")
    # and it must redden by DISAGREEING, not by going blind: the extractor still
    # reads the same figures it read a moment ago.
    assert _bytes_figures(_note()) == (one, peak), (
        "the extractor stopped finding figures it had already found; a check "
        "that goes quiet instead of red is the defect, not the guard")
