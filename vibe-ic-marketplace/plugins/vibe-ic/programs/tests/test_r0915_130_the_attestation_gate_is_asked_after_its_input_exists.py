"""R-0915-130 — the attestation gate was asked 10 min before its input existed.

MEASURED on run21, by that run's OWN mtimes:

    reports/phase2/gates/stage2_compliance.json   17:57:12   <- gate asked here
    reports/audit/phase23_completion_audit.json   18:07:33   <- stale FAIL recorded
    reports/final_summary.md                      18:07:34   <- the gate's INPUT

`agent_report_sha256_attestation_check` was a member of
`flow_compliance_check._STRUCTURAL_RTL_GATES`, so EVERY invocation asked it —
including each stage's mid-run compliance call. Its input is written at the END of
a run. It reported "8 attestation gap(s)" against a file written 10 minutes 22
seconds later, and the completion audit published that FAIL ONE SECOND before the
file appeared. On the finished tree the same gate says "PASS: 10 canonical
artefact(s) all attested".

#461 had already found half of this and fixed only the FINAL invocation:
`_prewrite_attestation` writes the fresh table before `_render` runs the internal
audit. The stage-N invocations never had that pre-write and could not — at stage 2
the run has not yet produced the artefacts to attest.

THE RULING IS ORDERING, NOT A CONDITIONAL: a completion-signal escape would add a
second way to say "not yet" for a case ordering removes, and every conditional
refusal is a place a finished-but-unattested run can hide. So the gate moves to
the end, immediately after the attestation table is written and before the audit
roll-up. The GATE ITSELF IS UNTOUCHED and both its refusals stand.

TWO NOTES ON THE PROOF, because the ruling's literal wording is unsatisfiable at
the position it mandates, and saying so is part of the work:

  * "the gate's ledger row timestamp is after final_summary.md's mtime" cannot
    hold for ANY placement: `final_report_generate` writes that file TWICE by
    design — the pre-write, then the gate, then the roll-up, then the full
    rewrite — so the final mtime is always after the gate. What is proved instead
    is the SEQUENCE: pre-write -> gate -> roll-up -> final write.
  * "a finished run whose final_summary.md carries zero sha256 tokens" is
    unreachable AFTER the move, because the pre-write always writes the table
    when artefacts are on disk. That arm existed only in the old position. The
    reachable equivalent — a FINISHED run carrying an artefact that cannot be
    attested — is pinned below, and was measured on a copy of run21: chmod 000 on
    the shipped GDS gives rc=1, `ARTEFACT_UNREADABLE`.
"""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                      # noqa: E402
import agent_report_sha256_attestation_check as GATE      # noqa: E402

GEN = PROGRAMS / "final_report_generate.py"


# ── the gate is no longer asked mid-run ─────────────────────────────────────

def test_the_gate_is_not_a_midrun_structural_member():
    """THE DEFECT. Membership meant every stage's compliance call asked it."""
    assert "agent_report_sha256_attestation_check" not in FCC._STRUCTURAL_RTL_GATES


def test_the_registry_stays_a_set_of_unique_gates():
    """The census is DERIVED, never remembered: no test pins the old 246, so the
    removal is checked by the property the registry must keep, not by a number."""
    reg = FCC._STRUCTURAL_RTL_GATES
    assert len(reg) == len(set(reg)), "duplicate gate in the denominator"
    assert len(reg) > 200, "the structural denominator collapsed"


# ── it IS asked where its input exists ─────────────────────────────────────

#: WHERE THE GATE IS ASKED, as the generator now spells it.
#:
#: RE-PINNED, and the reason is the fix that came after the move: the first tip
#: of this branch CALLED the gate's `audit()` and PRINTED the result, and that
#: dropped #834's record — so the call site became
#: `flow_compliance_check.attestation_gate_record`, which builds the record the
#: umbrella reads. The three anchors below were left on the old import line and
#: `str.index` raised `ValueError: substring not found` — a test that cannot find
#: its subject reports NOTHING about the ordering it exists to hold, which is why
#: they are re-pinned rather than relaxed.
#:
#: THE ASSIGNMENT, NOT THE BARE NAME: `attestation_gate_record` also occurs in
#: the comment that explains the call, one line earlier, so anchoring on the name
#: alone would measure the position of my own prose. Asserted unique below.
_GATE_CALL = "_att_record = _fcc_rec.attestation_gate_record(project)"
_PREWRITE = "_prewrite_attestation(project, canonical)"
_GUARD = "if not args.no_audit:"
_RENDER = "md = _render(project, run_audit="


def test_the_anchor_is_the_call_and_not_the_comment_about_it():
    """The pin's own precondition. If `_GATE_CALL` ever matches twice, every
    ordering claim below silently becomes a claim about whichever came first."""
    src = GEN.read_text()
    assert src.count(_GATE_CALL) == 1, (
        f"{_GATE_CALL!r} occurs {src.count(_GATE_CALL)} times; the ordering "
        f"anchors need exactly one call site")


def _assert_gate_sits_between_its_input_and_the_rollup(src: str) -> None:
    """PURE, so the mutation arm can drive it with a rearranged copy."""
    i_pre = src.index(_PREWRITE)
    i_gate = src.index(_GATE_CALL)
    i_render = src.index(_RENDER)
    assert i_pre < i_gate < i_render, (
        f"order is pre-write={i_pre} gate={i_gate} render={i_render}; the gate "
        f"must sit between its input and the roll-up")


def _assert_gate_sits_under_the_audit_guard(src: str) -> None:
    i_guard = src.index(_GUARD)
    i_gate = src.index(_GATE_CALL)
    i_render = src.index(_RENDER)
    assert i_guard < i_gate < i_render, (
        f"order is guard={i_guard} gate={i_gate} render={i_render}")


def test_the_generator_asks_the_gate_after_the_prewrite():
    """SOURCE-level ordering, because this path only runs inside a real run: the
    call must come AFTER `_prewrite_attestation` (its input) and BEFORE `_render`
    (the audit roll-up that records its verdict)."""
    _assert_gate_sits_between_its_input_and_the_rollup(GEN.read_text())


def test_the_ordering_claim_goes_red_when_the_gate_moves_earlier():
    """MUTATION for the test above. An ordering assertion that cannot be broken
    is a comment; this moves the call site AHEAD of its input in a copy of the
    source and requires the claim to refuse."""
    src = GEN.read_text()
    moved = src.replace(_PREWRITE, _GATE_CALL + "\n        " + _PREWRITE, 1)
    assert moved.index(_GATE_CALL) < moved.index(_PREWRITE)
    with pytest.raises(AssertionError):
        _assert_gate_sits_between_its_input_and_the_rollup(moved)


def test_the_gate_is_not_asked_when_no_audit_will_run():
    """It lives under the same `not args.no_audit` guard as the pre-write: with
    no audit there is no pre-write, so asking would read a stale table — the very
    defect this moves away from."""
    _assert_gate_sits_under_the_audit_guard(GEN.read_text())


def test_the_guard_claim_goes_red_when_the_gate_escapes_it():
    """MUTATION: hoist the call above the `not args.no_audit` guard."""
    src = GEN.read_text()
    hoisted = src.replace(_GUARD, _GATE_CALL + "\n    " + _GUARD, 1)
    assert hoisted.index(_GATE_CALL) < hoisted.index(_GUARD)
    with pytest.raises(AssertionError):
        _assert_gate_sits_under_the_audit_guard(hoisted)


# ── THE REFUSALS ARE UNCHANGED — moving WHERE a question is asked must not
#    move WHAT counts as an answer ──────────────────────────────────────────

def _project(tmp_path: Path, summary: str | None) -> Path:
    if summary is not None:
        p = tmp_path / "reports" / "final_summary.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary)
    return tmp_path


def test_an_absent_report_still_refuses_and_names_the_owner(tmp_path):
    verdict, findings = GATE.audit(_project(tmp_path, None))
    assert verdict == "VACUOUS_PASS", (verdict, findings)


def test_a_report_with_zero_tokens_still_fails(tmp_path):
    """The old negative arm, kept at the GATE level where it is still reachable:
    the gate has not been weakened, only relocated. A finished report carrying no
    attestation table is still a FAIL."""
    proj = _project(tmp_path, "# Final Summary\n\nno attestation table here\n")
    (proj / "phase3" / "stage4" / "gds").mkdir(parents=True, exist_ok=True)
    (proj / "phase3" / "stage4" / "gds" / "top.gds").write_bytes(b"HEADER" * 8)
    verdict, findings = GATE.audit(proj)
    assert verdict == "FAIL", (verdict, findings)
    assert any("ATTESTATION" in str(getattr(f, "rule", f)) for f in findings), findings


def test_a_fail_stays_reachable_in_the_new_position():
    """THE D2 QUESTION, asked of my own change: moving a gate to sit right after a
    writer that generates its input from the same artefacts could make it unable
    to fail. It cannot — the writer SKIPS an artefact it cannot hash
    (`except OSError: continue`) and its own comment says the gate then reports
    ARTEFACT_UNREADABLE. MEASURED on a copy of run21: chmod 000 on the shipped
    GDS -> rc=1, ARTEFACT_UNREADABLE, on an otherwise finished run."""
    src = GEN.read_text()
    i = src.index("def _gather_attestation_rows")
    body = src[i:i + 1400]
    assert "except OSError:" in body and "continue" in body
    assert "ARTEFACT_UNREADABLE" in body, (
        "the writer must keep skipping an unhashable artefact, or the gate's "
        "only reachable FAIL in this position disappears")


def _assert_the_verdict_is_surfaced(src: str) -> None:
    """PURE, for the same reason as the ordering helpers above."""
    i = src.index(_GATE_CALL)
    body = src[i:i + 1600]
    assert "attestation gap(s)" in body
    assert "_att_verdict" in body
    assert "INCOMPLETE" in body, (
        "an unreadable gate must report INCOMPLETE, never imply a pass")


def test_the_verdict_is_surfaced_not_swallowed():
    """A relocated gate whose verdict nothing prints is a gate nobody reads."""
    _assert_the_verdict_is_surfaced(GEN.read_text())


def test_the_surfacing_claim_goes_red_when_the_verdict_is_swallowed():
    """MUTATION: silence the two lines that publish the verdict and require the
    claim to refuse. Without this, a call site that recorded the verdict and told
    no one would satisfy the test above by carrying the words in a comment."""
    src = GEN.read_text()
    i = src.index(_GATE_CALL)
    swallowed = (src[:i]
                 + src[i:i + 1600].replace("attestation gap(s)", "")
                                  .replace("_att_verdict", "_unread")
                 + src[i + 1600:])
    with pytest.raises(AssertionError):
        _assert_the_verdict_is_surfaced(swallowed)
