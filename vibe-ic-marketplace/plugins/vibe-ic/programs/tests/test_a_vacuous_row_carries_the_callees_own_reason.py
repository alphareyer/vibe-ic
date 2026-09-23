"""R-0915-135 — a vacuous row names the instrument's reason, not the clause string.

THE DEFECT. A step whose every gate clause went vacuous published:

    vacuous: gate program signalled VACUOUS_PASS (input not applicable), and it is
    1 of 1 gate clause(s) that ran here: gds_xor_check . --check reports/phase3/gds_xor.json

while the CALLEE had said, on its own first line:

    NOT_MEASURED [CAPABILITY_ABSENT]: the run's own receipt records verdict
    NOT_DETERMINED — no KLayout runner reaches this project, so the comparison was
    not performed; an unrun XOR is not a zero

and the row's class read `no_population` -- "this step has no population" -- for a
tool that was simply not present. A row whose reason names the CLAUSE and not the
instrument's reason is the "refusal that discloses nothing" shape.

AND THE CLASSIFIER HAD BOTH IN HAND. The site that builds the hint knew the
callee's `reason_class` and its message and dropped them, saying so:
"Downstream aggregation treats everything after the prefix as the command
identity. The diagnostic suffix was needed here for classification, not in that
marker payload." Its own sibling branch two cases above already carried them.

WHAT THE PAYLOAD IS NOW: `<clause>` on line 1, `reason_class=<CLASS>; <message>`
after it. THE FIRST LINE IS STILL THE COMMAND IDENTITY, and that is load-bearing:
`all_vacuous_cmds` is a SET whose length decides `unanimous` and therefore the
step's TIER. Counting clause-plus-diagnostic would make two invocations of one
clause with different diagnostics read as two clauses -- already reachable before
this change, because the rc==2 producer has appended `\\n<snippet>` to that payload
all along. `split_vacuous_payload` closes that too.

ONE VOCABULARY PER CHANNEL. The callee states a `_flow_reason_taxonomy` class
(CAPABILITY_ABSENT); a row states a `_T.ReasonClass` one (`tool_absent`). The first
cut of this change injected the callee's spelling straight into the row, which put
two vocabularies in one field -- measured, and fixed by a short mapping table.
Declining is the conservative direction: a class with no entry leaves the row at
`no_population`, exactly what it said before.

MEASURED, main 93b2d10fa vs this tip, THREE UNRELATED CLAUSES on a run21 copy:

  step 37.3  gds_xor_check . --check reports/phase3/gds_xor.json
             rc no_population -> tool_absent, and the row now carries
             "reason_class=CAPABILITY_ABSENT; NOT_MEASURED [CAPABILITY_ABSENT]: …"
  step D1    l21_macro_supply_rail_declared_check .
             + "reason_class=DESIGN_DECLARED_NA; [SKIP] … no macro L…"
  step D1    l6_fsm_scaffold_actionable_check .
             + "reason_class=DESIGN_DECLARED_NA; [SKIP] … the design's own L…"
  step D1    l9_floorplan_contract_check . --json …/l9_floorplan_contract.json
             + "reason_class=DESIGN_DECLARED_NA; the desig…"
The three D1 clauses are PARTIALLY-VACUOUS, a PASS row: the CLASS election is
deliberately confined to the NOT_MEASURED branch, because a PASS row has no
reason_class to elect -- but the callee's line was dropped there for the same
reason and is carried there too.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402
import _flow_reason_taxonomy as TAX                          # noqa: E402

_T = FCC._T


# ── the payload: the clause stays the identity ─────────────────────────────

def test_the_clause_is_the_first_line_and_the_rest_is_diagnostic():
    clause, diag = FCC.split_vacuous_payload(
        "gate . --json x.json\nreason_class=CAPABILITY_ABSENT; no runner here")
    assert clause == "gate . --json x.json"
    assert diag == "reason_class=CAPABILITY_ABSENT; no runner here"


def test_a_payload_with_no_diagnostic_still_yields_the_clause():
    assert FCC.split_vacuous_payload("gate . --json x.json") == (
        "gate . --json x.json", "")


def test_two_invocations_of_one_clause_count_as_one():
    """THE TIER DEPENDS ON THIS COUNT. `all_vacuous_cmds` is a set and its length
    decides `unanimous`; counting the diagnostic would split one clause in two.
    Reachable before this change too, via the rc==2 producer's snippet."""
    payloads = ["gate . --json x.json\nreason_class=CAPABILITY_ABSENT; a",
                "gate . --json x.json\nsome other snippet entirely"]
    assert len({FCC.split_vacuous_payload(p)[0] for p in payloads}) == 1


# ── the class: unanimity or nothing, and one vocabulary ────────────────────

def test_a_unanimous_stated_class_is_elected_and_mapped_to_the_row_word():
    got = FCC.elected_vacuous_class(
        ["reason_class=CAPABILITY_ABSENT; no runner",
         "reason_class=CAPABILITY_ABSENT; still no runner"])
    assert got == _T.ReasonClass.TOOL_ABSENT.value, got
    assert got != TAX.CAPABILITY_ABSENT, (
        "the row must not carry the callee's vocabulary — one vocabulary per "
        "channel, and the first cut of this change got it wrong")


def test_two_different_stated_classes_elect_nothing():
    """Two clauses vacuous for DIFFERENT reasons do not license either as the
    step's class; `no_population` is then the honest word."""
    assert FCC.elected_vacuous_class(
        ["reason_class=CAPABILITY_ABSENT; a",
         "reason_class=EXECUTION_ERROR; b"]) is None


def test_one_silent_clause_declines_the_election():
    assert FCC.elected_vacuous_class(
        ["reason_class=CAPABILITY_ABSENT; a", "a bare snippet"]) is None
    assert FCC.elected_vacuous_class([]) is None


def test_a_class_with_no_row_word_declines_rather_than_inventing_one():
    """DESIGN_DECLARED_NA is a real taxonomy class with no `_T.ReasonClass`
    counterpart, so the row keeps `no_population`. Declining is the conservative
    direction: it leaves the row exactly as it read before."""
    assert FCC.elected_vacuous_class(
        ["reason_class=DESIGN_DECLARED_NA; the design declared none",
         "reason_class=DESIGN_DECLARED_NA; likewise"]) is None


def test_an_unreadable_diagnostic_states_no_class():
    for bad in ("", "   ", "no class here", "reason_class=; empty"):
        assert FCC.vacuous_stated_class(bad) is None, bad


def test_every_mapped_class_is_a_real_member_of_both_vocabularies():
    """A mapping table is two places to drift. Both ends are checked against the
    modules that own them."""
    for src, dst in FCC._VACUOUS_CLASS_TO_ROW_CLASS.items():
        assert TAX.normalise(src) == src, src
        assert dst in {m.value for m in _T.ReasonClass}, dst


# ── the producer carries what the consumer reads ───────────────────────────

def test_the_classifier_puts_the_class_and_message_in_the_payload():
    """The half that made the row blind: the classifier had both and dropped
    them. Asserted at source, because the alternative is to run a whole audit."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index('verdict = "VACUOUS_PASS"')
    window = src[i:i + 1800]
    assert "_VACUOUS_HINT_PREFIX}{cmd_str}" in window
    assert "reason_class={reason_class}" in window, window[-400:]
    assert "report_message or legacy_message" in window


def test_both_disclosure_branches_carry_the_diagnostic():
    """The NOT_MEASURED row is what the ruling names; the PARTIALLY-VACUOUS row
    drops the callee's line for the same reason, so both carry it."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    assert src.count('+ (f" — {_diag}" if _diag else "")') == 2, (
        "expected the diagnostic on both the unanimous and the partial branch")
