"""vibe-ic#2124 — the two halves of one grammar defect.

PART 1 — the P0 waiver reason names the branch that granted the waiver.
``_compose_p0_reasons`` rendered the literal ``thin-input`` for all three
waiver branches, beside a ticket that named the real one. The shipped line
``WAIVED-DEFERRED: l6_… — thin-input (ticket=ENFORCEMENT:advisory, …)`` names
two different branches in one sentence. No verdict ever moved on the label,
which is why it survived: only a human reads it, and the human is the person
deciding whether the waiver is legitimate.

The fix is one definition per branch (``_P0_WAIVER_BRANCHES``) that the ticket
constants are READ OUT OF, so the label and the ticket in one line are two
spellings of one row rather than two facts that can disagree.

PART 2 — ``NON_WAIVERABLE:`` is wired, and the wiring is written down.
``VACUOUS_PASS:`` has a place a reader goes to learn who writes the token and
who acts on it. ``NON_WAIVERABLE:`` (landed by #2098) had one emitter, two
consumers and no entry naming either. The register added here follows the SAME
contract, deliberately: emission stays OPEN (any gate may declare a finding
non-waiverable and it is honoured on the spot — the alternative is
``flow_compliance_check`` deciding from a gate NAME something only the gate
knows about the finding it just produced), and what the register adds is the
direction the vacuous contract also holds — the wiring is audited and may not
rot. Both directions are tested below, and neither changes what a flow run
honours.
"""
from __future__ import annotations

import ast
import importlib
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

F = importlib.import_module("flow_compliance_check")


# ---------------------------------------------------------------------------
# PART 1 — one label per branch, and it is the branch that granted the waiver
# ---------------------------------------------------------------------------


def _waiver_record(gate: str, ticket: str, first_line: str = "why"):
    return F._p0_waiver_record({
        "gate": gate, "review_required": True, "ticket": ticket,
        "evidence": "detail", "reason": "the branch's own reason",
        "first_line": first_line})


def _rendered(gate: str, ticket: str) -> str:
    lines = F._compose_p0_reasons_from_records(
        [_waiver_record(gate, ticket)], True)
    assert len(lines) == 1, lines
    return lines[0]


def test_the_registered_branches_are_the_three_that_exist():
    """MEMBERSHIP, not a count: the register holds exactly the three branches
    the umbrella can take, and the labels are distinct."""
    labels = [label for label, _ in F._P0_WAIVER_BRANCHES]
    tickets = [ticket for _, ticket in F._P0_WAIVER_BRANCHES]
    assert set(labels) == {"thin-input", "reused-IP", "two-source-advisory"}
    assert len(set(labels)) == len(labels) == len(set(tickets))


def test_the_ticket_constants_are_read_out_of_the_register():
    """The register is not a second table beside the tickets — the tickets come
    from it. If someone re-types a ticket constant, these stop being one fact
    and this test is what says so."""
    assert (F._THIN_INPUT_WAIVER_TICKET
            is F._P0_WAIVER_TICKET_BY_LABEL["thin-input"])
    assert (F._REUSED_IP_RTL_ONLY_FSM_CAP_TICKET
            is F._P0_WAIVER_TICKET_BY_LABEL["reused-IP"])


@pytest.mark.parametrize("label,ticket", list(F._P0_WAIVER_BRANCHES))
def test_the_rendered_reason_names_its_own_branch_and_no_other(label, ticket):
    """THE ISSUE, one test per branch: the label names the branch that granted
    the waiver, and never another branch's."""
    line = _rendered("alpha_check", ticket)
    assert line == (f"WAIVED-DEFERRED: alpha_check — {label} "
                    f"(ticket={ticket}, review_required=true): why"), line
    others = [o for o, _ in F._P0_WAIVER_BRANCHES if o != label]
    for other in others:
        assert other not in line, (
            f"the reason for the {label} branch names {other}: {line}")


def test_the_advisory_line_from_the_issue_is_no_longer_self_contradictory():
    """The exact shape #2098 quoted, which is what made this findable."""
    line = _rendered("l6_fsm_scaffold_actionable_check",
                     "ENFORCEMENT:advisory")
    assert "— two-source-advisory (ticket=ENFORCEMENT:advisory" in line, line
    assert "thin-input" not in line, line


def test_a_ticket_no_branch_registers_degrades_loudly():
    """It does NOT fall back to a branch name. Guessing a label is the defect
    itself; an unrecognised ticket says so, and prints the ticket beside it."""
    line = _rendered("beta_check", "T-9")
    assert F._P0_UNREGISTERED_BRANCH_LABEL in line, line
    assert "(ticket=T-9, review_required=true)" in line, line
    for label, _ in F._P0_WAIVER_BRANCHES:
        assert label not in line, line


def test_the_label_helper_is_the_only_place_that_answers():
    """Direction 2 for the helper itself: a registered ticket maps to its own
    label, an unregistered one to the refusal, and nothing else."""
    for label, ticket in F._P0_WAIVER_BRANCHES:
        assert F._p0_waiver_branch_label(ticket) == label
    for absent in ("", None, "thin-input", "ENFORCEMENT:advisory-ish"):
        assert (F._p0_waiver_branch_label(absent)
                == F._P0_UNREGISTERED_BRANCH_LABEL), absent


def test_the_three_authoring_sites_carry_registered_tickets():
    """The renderer can only be right if the WAIVER carries a registered
    ticket. Parsed from the source of the three demotion branches, so a fourth
    branch authored with a fresh literal is caught here rather than by a
    reader noticing a strange label in a report."""
    src = Path(F.__file__).read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_eval_gate_worker")
    literals: List[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "ticket"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)):
                    literals.append(v.value)
    assert literals == [], (
        "a P0 waiver ticket is authored as a bare literal instead of being "
        f"read out of _P0_WAIVER_BRANCHES: {literals}")


# ---------------------------------------------------------------------------
# PART 2 — the NON_WAIVERABLE: wiring register, audited in both directions
# ---------------------------------------------------------------------------

_TOKEN = "NON_WAIVERABLE:"


def _programs_emitting_token(programs_dir: Path) -> List[str]:
    """Every program whose SOURCE writes a line-start `NON_WAIVERABLE:`.

    Parsed, never grepped — for the reason the token itself exists: a module
    that DISCUSSES the convention in prose (`flow_compliance_check` does, at
    length) must not be counted as declaring one. Only a string literal passed
    to `print` that STARTS with the token counts.
    """
    found: List[str] = []
    for path in sorted(programs_dir.glob("*.py")):
        try:
            with warnings.catch_warnings():
                # a few shipped programs carry invalid escapes in prose; that
                # is not this audit's subject and its warnings are not this
                # suite's noise.
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:                      # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print" and node.args):
                continue
            first = node.args[0]
            head = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                head = first.value
            elif isinstance(first, ast.JoinedStr) and first.values:
                lead = first.values[0]
                if isinstance(lead, ast.Constant) and isinstance(lead.value, str):
                    head = lead.value
            if head is not None and head.lstrip().startswith(_TOKEN):
                found.append(path.stem)
                break
    return found


def _emitter_refusals(register: Sequence[str],
                      in_tree: Sequence[str]) -> List[str]:
    """The register's refusals, BY NAME, in both directions."""
    out = [f"emits {_TOKEN} but no register entry names it: {name}"
           for name in sorted(set(in_tree) - set(register))]
    out += [f"registered as an emitter of {_TOKEN} but emits none: {name}"
            for name in sorted(set(register) - set(in_tree))]
    return out


def test_the_token_constant_is_the_one_the_register_is_about():
    assert F._NON_WAIVERABLE_TOKEN == _TOKEN


def test_every_emitter_in_the_tree_is_registered_and_every_entry_is_live():
    """Direction 1 and its converse on the real tree. An unregistered emitter
    is named; an entry that outlived its emitter is named too — the rule
    `checker_execution_wiring_audit` already states for its own registers."""
    refusals = _emitter_refusals(F._NON_WAIVERABLE_EMITTERS,
                                _programs_emitting_token(_PROGRAMS))
    assert refusals == [], refusals


def test_the_audit_refuses_an_unregistered_emitter_by_name():
    """The negative control for direction 1, run against the SAME function the
    tree test uses — an audit that cannot refuse is not an audit."""
    refusals = _emitter_refusals(("l6_fsm_scaffold_actionable_check",),
                                 ("l6_fsm_scaffold_actionable_check",
                                  "some_other_check"))
    assert refusals == [
        f"emits {_TOKEN} but no register entry names it: some_other_check"]


def test_the_audit_refuses_a_stale_register_entry_by_name():
    """The negative control for the converse."""
    refusals = _emitter_refusals(("gone_check",), ())
    assert refusals == [
        f"registered as an emitter of {_TOKEN} but emits none: gone_check"]


def test_the_registered_consumers_exist_and_read_the_token():
    """The two demotion sites and the reader, named in the register and proved
    against the code: each is a real function in `flow_compliance_check`, and
    each mentions the reader or the token in its own body."""
    src = Path(F.__file__).read_text()
    tree = ast.parse(src)
    by_name: Dict[str, ast.FunctionDef] = {
        n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    missing, deaf = [], []
    for entry in F._NON_WAIVERABLE_CONSUMERS:
        module, _, func = entry.partition(".")
        assert module == "flow_compliance_check", entry
        node = by_name.get(func)
        if node is None:
            missing.append(entry)
            continue
        body = ast.get_source_segment(src, node) or ""
        if not ("_output_declares_non_waiverable" in body
                or "_NON_WAIVERABLE_TOKEN" in body):
            deaf.append(entry)
    assert missing == [], f"registered consumer does not exist: {missing}"
    assert deaf == [], f"registered consumer never reads the token: {deaf}"


def test_the_two_demotion_sites_are_both_registered():
    """MEMBERSHIP: the register names the reader and BOTH demotion sites. #2098
    fixed two sites; a register naming one of them would read as a complete
    disclosure."""
    assert set(F._NON_WAIVERABLE_CONSUMERS) == {
        "flow_compliance_check._output_declares_non_waiverable",
        "flow_compliance_check._eval_gate_worker",
        "flow_compliance_check._evaluate_gate",
    }


def test_emission_stays_open_which_is_the_vacuous_contract():
    """NOT a second convention: the register does not gate what is honoured at
    run time. A gate NOT in the register that declares the token is still read
    — `VACUOUS_PASS:` is honoured from any gate for the same reason, and making
    this one an allowlist would put the decision back where only the gate has
    the facts."""
    assert F._output_declares_non_waiverable(
        "", "NON_WAIVERABLE: a gate nobody registered said so") == (
            "a gate nobody registered said so")
