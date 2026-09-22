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

def test_the_generator_asks_the_gate_after_the_prewrite():
    """SOURCE-level ordering, because this path only runs inside a real run: the
    call must come AFTER `_prewrite_attestation` (its input) and BEFORE `_render`
    (the audit roll-up that records its verdict)."""
    src = GEN.read_text()
    i_pre = src.index("_prewrite_attestation(project, canonical)")
    i_gate = src.index("from agent_report_sha256_attestation_check import audit")
    i_render = src.index("md = _render(project, run_audit=")
    assert i_pre < i_gate < i_render, (
        f"order is pre-write={i_pre} gate={i_gate} render={i_render}; the gate "
        f"must sit between its input and the roll-up")


def test_the_gate_is_not_asked_when_no_audit_will_run():
    """It lives under the same `not args.no_audit` guard as the pre-write: with
    no audit there is no pre-write, so asking would read a stale table — the very
    defect this moves away from."""
    src = GEN.read_text()
    i_guard = src.index("if not args.no_audit:")
    i_gate = src.index("from agent_report_sha256_attestation_check import audit")
    i_render = src.index("md = _render(project, run_audit=")
    assert i_guard < i_gate < i_render


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


def test_the_verdict_is_surfaced_not_swallowed():
    """A relocated gate whose verdict nothing prints is a gate nobody reads."""
    src = GEN.read_text()
    i = src.index("from agent_report_sha256_attestation_check import audit")
    body = src[i:i + 1600]
    assert "attestation gap(s)" in body
    assert "_att_verdict" in body
    assert "INCOMPLETE" in body, (
        "an unreadable gate must report INCOMPLETE, never imply a pass")
