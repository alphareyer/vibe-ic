"""A stamp the consumer never parses is not a disclosure.

MEASURED on `origin/main` before this branch. `phase3_one_shot_runner.py`
writes seven `STA_*` stamps into the reports it emits. `_ppa/backends/opensta.py`
-- the only thing that reads those reports for the PPA record layer -- parsed
four of them::

    STA_BASIS                  parsed
    STA_BASIS_LIBERTY          parsed
    STA_SIGNOFF_CORNER         parsed
    STA_SIGNOFF_CORNER_COUNT   parsed
    STA_BASIS_SPEF             NOT PARSED   <- four emitters write it, a test
                                               asserts one of them writes it,
                                               and nothing ever read it
    STA_BASIS_NETLIST          NOT PARSED
    STA_BASIS_NOTE             NOT PARSED

`STA_BASIS_SPEF` is the one that cost something. The runner disclosed which
parasitics a report timed; the PPA layer discarded it; the records it then wrote
say `rc_corner: null`; and `ppa_head_to_head_check` refuses those records
`SCOPE_SENTINEL` for a field the artefact chain had been stating all along. A
stamp written, tested, and consumed by nothing is the "declared but inert"
class -- a thing that looks like evidence from the producer's side and is not
evidence from the reader's.

THIS TEST DOES NOT DEMAND THAT EVERY STAMP BE PARSED. Some are genuinely not
comparability data. What it refuses is SILENCE: a stamp the consumer does not
read must be listed below with a reason, so that adding another one nothing
reads is a decision somebody makes on purpose rather than a thing that happens.

Chip-, PDK- and vendor-AGNOSTIC: no foundry, node or SKU appears here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import gate_is_wired_check as _wiring          # noqa: E402

RUNNER = _PROGRAMS / "phase3_one_shot_runner.py"
BACKEND = _PROGRAMS / "_ppa" / "backends" / "opensta.py"

#: Stamps the PPA reader deliberately does NOT parse, each with the reason.
#: An entry here is a declaration, not a waiver -- it says "we looked, and
#: decided", and it is the only way a stamp is allowed to go unread.
NOT_READ_ON_PURPOSE = {
    "STA_BASIS_NOTE":
        "free prose the emitter attaches for a human. It is not a field, has "
        "no grammar, and nothing could compare two of them.",
    "STA_BASIS_NETLIST":
        "A REAL GAP, recorded rather than quietly parsed. Which netlist was "
        "timed IS comparability data -- two slacks from two different netlists "
        "are not the same measurement -- but no consumer wants it yet, and "
        "adding an unread field to the Report is the same disease this file "
        "exists to name. It is listed so the next reader finds it as a "
        "decision instead of as a silence.",
}


#: A stamp is a token the runner WRITES INTO A REPORT. Two things wear the same
#: spelling and are not that, and both were measured on this tree (czmainred9,
#: v1.19.60) as false accusations this file made against honest landings:
#:
#:   PROSE.       `c281a9ed4` put a worked example of an audit finding line into
#:                a comment -- `#   sta_signoff  PASS  STA_SINGLE_CORNER_ONLY:
#:                no multi-corner POST_ROUTE STA`. Nothing emits it. It is an
#:                `eda_report_audit` FINDING RULE quoted for a reader, and the
#:                raw-text scan read the comment as an emitter.
#:   AN IDENTIFIER TAIL. `_STA_SINGLE_CORNER_RULE` is a module constant, and
#:                `if ... == _STA_SINGLE_CORNER_RULE:` ends in the colon that
#:                closes an `if`. The lookahead matched that colon and the
#:                unanchored `STA_` matched mid-identifier, so a Python
#:                statement was read as a stamp named `STA_SINGLE_CORNER_RULE`.
#:
#: Neither is reachable by a reader of a report, so demanding that the PPA
#: backend parse them -- or that somebody declare them unread -- asks for a
#: disclosure about text that is not disclosed. The population is narrowed to
#: what the rule was always about; `test_the_runner_really_does_stamp_things`
#: is the guard against narrowing it to nothing, and it reads 8 here.
#:
#: The prose half is decided by `gate_is_wired_check.executable_text`, the
#: tree's ONE answer to "which bytes of this file can do anything" -- not a
#: second copy of that rule, for the reason vibe-ic#2169 gives.
_STAMP_RE = re.compile(r"(?<![A-Za-z0-9_])STA_[A-Z_]+(?=\s*:)")


def _stamps_written_by_the_runner():
    """Every `STA_*` token the runner stamps into a report."""
    return set(_STAMP_RE.findall(
        _wiring.executable_text(RUNNER, RUNNER.read_text())))


def _stamps_parsed_by_the_reader():
    text = BACKEND.read_text()
    return {s for s in _stamps_written_by_the_runner() if s in text}


def test_the_runner_really_does_stamp_things():
    """The premise. If this ever finds nothing, the rule below is vacuous."""
    written = _stamps_written_by_the_runner()
    assert len(written) >= 4, (
        "no STA_* stamps were found in the runner, so this whole file would "
        "pass by having nothing to check: %r" % (sorted(written),))


def test_every_stamp_is_either_parsed_or_declared_unread():
    written = _stamps_written_by_the_runner()
    parsed = _stamps_parsed_by_the_reader()
    orphaned = sorted(written - parsed - set(NOT_READ_ON_PURPOSE))
    assert not orphaned, (
        "these stamps are written into reports by the runner and read by "
        "nothing in the PPA reader: %s\n\n"
        "A stamp nothing parses is not a disclosure -- it looks like evidence "
        "from the emitter's side and is absent from the reader's. Either parse "
        "it in _ppa/backends/opensta.py, or add it to NOT_READ_ON_PURPOSE in "
        "this file, in the same commit, with the reason." % (orphaned,))


def test_the_spef_stamp_is_now_actually_read():
    """The specific one that cost something, pinned by name.

    Kept separate from the rule above so that removing the parse is a red with
    an unambiguous subject rather than one entry in a list.
    """
    assert "STA_BASIS_SPEF" in BACKEND.read_text(), (
        "`STA_BASIS_SPEF` is stamped by the runner and is no longer parsed by "
        "the PPA reader. Four emitters write it; when nothing read it, every "
        "timing row taken from a bannerless report carried `rc_corner: null` "
        "and the head-to-head gate refused those records for a field the "
        "report had already stated.")


def test_the_ledger_may_not_hold_a_stamp_that_is_actually_parsed():
    """The ledger must not rot into a list of things that are fine.

    An entry that is ALSO parsed is stale, and a stale ledger teaches the next
    reader that entries here mean nothing.
    """
    parsed = _stamps_parsed_by_the_reader()
    stale = sorted(set(NOT_READ_ON_PURPOSE) & parsed)
    assert not stale, (
        "these stamps are listed as deliberately unread and ARE parsed: %s. "
        "Remove them from NOT_READ_ON_PURPOSE." % (stale,))


def test_every_ledger_entry_states_a_reason():
    for name, why in NOT_READ_ON_PURPOSE.items():
        assert isinstance(why, str) and len(why.strip()) > 40, (
            "%s is listed as unread with no real reason. The entry is the "
            "declaration; without it this is a silence with extra steps."
            % (name,))


# ── the emitter half ───────────────────────────────────────────────────────
# Parsing a stamp nobody writes is the same defect facing the other way, and
# the report whose records were actually refused is the single-corner one.
# Anchored on that deck's OWN sentence, which appears exactly once in the
# runner, so this cannot silently start measuring a different emitter.
# NOTE the deliberate stopping point: in the runner this sentence is split
# across two f-string lines ("...ONE process " + "corner; ..."), so an anchor
# that reads past `process` matches ZERO times in the SOURCE while looking
# perfectly correct in the emitted report.
_SINGLE_CORNER_ANCHOR = "STA_SIGNOFF_CORNER_SEMANTICS this report times ONE process"


def _single_corner_stamp_block() -> str:
    """The stamp block of the deck that writes the single-corner SPEF report."""
    text = RUNNER.read_text()
    assert text.count(_SINGLE_CORNER_ANCHOR) == 1, (
        "the anchor identifying the single-corner STA emitter now matches "
        "%d times, so this test can no longer say which emitter it is "
        "measuring." % (text.count(_SINGLE_CORNER_ANCHOR),))
    end = text.index(_SINGLE_CORNER_ANCHOR)
    start = text.rindex("read_spef", 0, end)
    return text[start:end]


def test_the_single_corner_deck_stamps_the_parasitics_it_reads():
    """It read a SPEF and disclosed four other facts about the corner.

    MEASURED on the campaign's own run: `sta_spef_based.rpt` carried
    STA_BASIS, STA_SIGNOFF_CORNER, STA_BASIS_LIBERTY and
    STA_SIGNOFF_CORNER_COUNT, and no parasitics. That report has NO banner, so
    the whole-file stamp is the only place its RC condition could ever be
    stated, and both published end-to-end records that read it are undecidable
    on the RC axis as a direct result.
    """
    block = _single_corner_stamp_block()
    assert "read_spef" in block, block
    assert "STA_BASIS_SPEF" in block, (
        "the deck that writes the single-corner SPEF report reads a SPEF and "
        "does not stamp it, while stamping the corner, the liberty and the "
        "corner count. The report it produces can then name no RC condition "
        "at all:\n" + block)


# ── the population's own two directions (czmainred9) ──────────────────────
#
# The rule above was narrowed, and a narrowing is only honest if it can be
# shown to have dropped the two things it names AND NOTHING ELSE. These drive
# the extractor on source built for the purpose, so they answer without waiting
# for the shipped runner to happen to carry an instance.

_SYNTHETIC_RUNNER = '''\
"""A docstring that mentions STA_DOCSTRING: and must not count."""
# a comment that mentions STA_COMMENTED: and must not count
_STA_ALIASED_RULE = "STA_REAL_EMITTED"


def emit(f, basis):
    if f.get("rule") == _STA_ALIASED_RULE:      # an `if`, not a stamp
        f.write(f"STA_REAL_EMITTED: {basis}\\n")
    f.write("# STA_COMMENT_LINE_IN_THE_REPORT: yes\\n")
'''


def test_the_extractor_reads_emitted_stamps_and_only_those(tmp_path):
    """MUTATION, both directions, on source that carries every shape at once.

    A stamp emitted into the report is FOUND -- including one emitted as a
    comment line OF THE REPORT, which is a real disclosure and must survive.
    A stamp appearing only in this file's own prose, or only as the tail of a
    longer Python identifier, is NOT. Delete either clause of `_STAMP_RE`/
    `executable_text` and one of these four goes the wrong way.
    """
    p = tmp_path / "synthetic_runner.py"
    p.write_text(_SYNTHETIC_RUNNER)
    found = set(_STAMP_RE.findall(
        _wiring.executable_text(p, p.read_text())))

    assert "STA_REAL_EMITTED" in found, found          # emitted -> counted
    assert "STA_COMMENT_LINE_IN_THE_REPORT" in found, found   # emitted as a
    #        report comment: still a disclosure the reader can see
    assert "STA_DOCSTRING" not in found, found        # this file's prose
    assert "STA_COMMENTED" not in found, found        # this file's prose
    assert "STA_ALIASED_RULE" not in found, found     # an identifier tail


def test_a_genuinely_unread_new_stamp_still_fails_the_rule():
    """THE POWER OF THE NARROWED POPULATION.

    Everything the fix removed was prose; the rule itself must be unchanged.
    Adding a real emitter for a stamp the PPA backend does not parse must
    still be refused, or the narrowing bought silence instead of accuracy.
    """
    planted = "STA_PLANTED_BY_THIS_TEST"
    text = _wiring.executable_text(RUNNER, RUNNER.read_text())
    text += '\n    f.write(f"%s: {x}\\n")\n' % planted

    written = set(_STAMP_RE.findall(text))
    assert planted in written, "the plant did not even reach the population"
    parsed = {s for s in written if s in BACKEND.read_text()}
    orphaned = sorted(written - parsed - set(NOT_READ_ON_PURPOSE))
    assert orphaned == [planted], orphaned
