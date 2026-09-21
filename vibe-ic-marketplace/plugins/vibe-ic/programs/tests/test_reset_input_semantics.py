"""`_reset_input_semantics` -- the first test this shipped module has ever had.

RC16: it is one of two modules under `programs/` that no test named. A shipped
reader with no test is a reader whose doctrine lives only in its docstring, and
this one's doctrine is sharp and load-bearing:

    Historical comparisons are not declarations. Conflicting explicit values
    remain unknown; no corpus-global name/value join or generated-artifact
    inference.

Every expectation below was MEASURED against the module as it ships, not derived
from reading it -- so this file records what it DOES, and the cases are chosen to
be the ones whose answer would change if the doctrine were quietly relaxed.

ASCII-ONLY SOURCE, on purpose. The module carries CJK alternates in its own
patterns and that path has to be covered, so the CJK inputs below are written as
`\\u` escapes rather than as literal characters.
"""
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _reset_input_semantics as R            # noqa: E402


# -- a declaration is read, and only from the named port's own line -----------
def test_a_plain_declaration_is_read():
    assert R.reset_semantics("rst_n", "rst_n is an active-low synchronous reset.") == {
        "sync": "synchronous", "polarity": "active_low"}


def test_another_ports_declaration_is_never_borrowed():
    """The `no corpus-global name/value join` half: `por_n`'s semantics are not
    `rst_n`'s, however close they sit."""
    assert R.reset_semantics("rst_n", "por_n is active-low synchronous.") == {}


def test_the_evidence_names_its_own_source_line():
    rows = list(R.declarations("noise\nrst_n is active-low.\n", "rst_n"))
    assert [r["line"] for r in rows] == [2]
    assert rows[0]["evidence"] == "rst_n is active-low."


# -- history is not a declaration, in each shape the module handles -----------
def test_a_historical_line_declares_nothing():
    assert R.reset_semantics(
        "rst_n", "Previously rst_n was active-high asynchronous.") == {}


def test_a_comparison_clause_declares_nothing():
    """MEASURED: the text AFTER a comparison marker is dropped with it, so this
    whole line declares nothing. That is the conservative direction -- the cost
    is a missed declaration, never an invented one -- and it is pinned here so
    the trade is a decision on the record rather than a surprise."""
    assert R.reset_semantics(
        "rst_n", "Unlike the prior part, rst_n is active-low synchronous.") == {}


def test_a_parenthetical_history_is_dropped_and_the_rest_survives():
    """The parenthesis is discarded, not the sentence: the current declaration
    is still read."""
    assert R.reset_semantics(
        "rst_n", "rst_n is active-low (formerly active-high).") == {
        "polarity": "active_low"}


# -- a conflict is `unknown`, never a winner ----------------------------------
def test_two_explicit_polarities_are_unknown_not_first_wins():
    assert R.reset_semantics(
        "rst_n", "rst_n is active-low.\nrst_n is active-high.") == {
        "polarity": "unknown"}


def test_two_explicit_syncs_are_unknown_not_first_wins():
    assert R.reset_semantics(
        "rst_n", "rst_n is synchronous.\nrst_n is asynchronous.") == {
        "sync": "unknown"}


# -- the CJK alternates the module ships are exercised ------------------------
def test_the_cjk_asynchronous_alternate_is_read():
    text = "rst_n \u70ba\u975e\u540c\u6b65 active-low reset."   # "is asynchronous"
    assert R.reset_semantics("rst_n", text) == {
        "sync": "asynchronous", "polarity": "active_low"}


def test_the_cjk_history_alternate_declares_nothing():
    text = "\u5148\u524d rst_n \u70ba active-high."             # "previously ..."
    assert R.reset_semantics("rst_n", text) == {}


# -- current_clause is the seam both of those go through ----------------------
def test_current_clause_keeps_a_declaration_and_drops_a_history():
    assert R.current_clause("rst_n is active-low.") == "rst_n is active-low."
    assert R.current_clause("formerly active-high") == ""
