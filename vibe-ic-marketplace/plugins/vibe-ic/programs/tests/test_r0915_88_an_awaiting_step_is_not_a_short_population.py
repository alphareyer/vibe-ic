"""R-0915-88 — `AWAITING_AGENT_PASS` was written into the taxonomy, never reached.

THE INPUT, on run21, `flow_compliance_check --stage-id stage_phase1 --strict`
(rc=1, the one failing ledger row of two):

    [UNCLASSIFIED] Step D1: Phase 1 Doc Extraction   (NOT_MEASURED)
        reason_class : partial_population
        observed     : INCOMPLETE: the gate reports its input was applicable
                       and was NOT examined: phase1_expert_parse_track . --check-report
        why          : it ran without measuring design-bound content, which
                       names no cause to act on

Every one of that step's 33 gates returned a decisive verdict, and the gate's
own report says `execution.disposition = "AWAITING"`,
`observed_ai_status = "HANDOFF_EMITTED"`, `observed_ai_consumed = 0`. Nothing
about the population is short: the flow is waiting on a second pass a PROGRAM
cannot make. Both sentences above are false, and no structural sub-gate failed —
the structural umbrella was not even asked to run in that invocation.

FOUR LINKS, each measured, each pinned below:

  1. the PRODUCER never printed the token (0 occurrences in its source), though
     its `--check-report` branch had just proved the awaiting triple;
  2. the rc=4 REWRITE names `AWAITING_AGENT_PASS` mid-line after `INCOMPLETE: `,
     and the token is only read where it STARTS a line — so the reader's own
     synthesised message was unreadable by the reader's own test;
  3. the REQUIRED `program_exit_zero` branch never looked for the token, while
     the OPTIONAL branch does — the asymmetry the vacuous comment ten lines
     above it describes, for the other token;
  4. the `all_of` forwarding WHITELIST has no branch for the awaiting hint, so
     it was appended and dropped one level below the line meant to carry it.
     That whitelist's own comments record the same failure mode three times.

WHAT MUST NOT MOVE, and the controls are here for it: an INCOMPLETE that is
genuinely a short population keeps `partial_population`, and the awaiting tier
stays UNCLASSIFIED — it is not MISSING_CAPABILITY, which this repo defines as a
named TOOL, BOARD or PDK being absent. Only the basis and the sentence change.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
FCC = PROGRAMS / "flow_compliance_check.py"
sys.path.insert(0, str(PROGRAMS))


def _fcc(name: str):
    """A fresh module per test, so a patched runner cannot leak between cases."""
    spec = importlib.util.spec_from_file_location(name, FCC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Res:
    outcome = "ok"


def _stub_gate(mod, rc: int, stdout: str) -> None:
    """Make every gate invocation return (rc, stdout).

    Patched at `_watchdog`, which is the seam the module actually calls: a
    `subprocess.run` stub is blinded by the supervised-run recorder in between.
    """
    class _CP:
        returncode = rc
        stdout = ""
        stderr = ""

    cp = _CP()
    cp.stdout = stdout
    mod._watchdog.run_host_supervised = (
        lambda argv, **kw: _Res())
    mod._watchdog.completed_process = lambda argv, res: cp
    mod._resolve_program_cmd = lambda cmd_str, cwd=None: ["/bin/true"]


_AWAITING_STDOUT = (
    "INCOMPLETE: phase1_expert_parse_track: INCOMPLETE — read-only report check\n"
    "AWAITING_AGENT_PASS: HANDOFF_EMITTED — 0 of 1 required agent answer(s) "
    "consumed\n")

#: The SAME first line, and NOTHING else. This is what the producer printed
#: before this change, and what any other gate in the family still prints.
_BARE_INCOMPLETE_STDOUT = (
    "INCOMPLETE: phase1_expert_parse_track: INCOMPLETE — read-only report check\n")

_ALL_OF_GATE = {"all_of": [
    {"program_exit_zero": "phase1_expert_parse_track . --check-report"}]}


def _awaiting_hints(mod, reasons):
    return [r for r in reasons if r.startswith(mod._AWAITING_HINT_PREFIX)]


def _incomplete_hints(mod, reasons):
    return [r for r in reasons if r.startswith(mod._INCOMPLETE_HINT_PREFIX)]


# ── the chain, end to end through the required clause in an all_of ────────────

def test_an_awaiting_gate_inside_all_of_reaches_the_awaiting_hint(tmp_path):
    """LINKS 3 AND 4 TOGETHER. Before them the hint was either never created
    (required branch) or created and dropped (all_of whitelist)."""
    mod = _fcc("fcc_awaiting_1")
    _stub_gate(mod, 4, _AWAITING_STDOUT)
    passed, reasons = mod._evaluate_gate(tmp_path, _ALL_OF_GATE)
    assert passed is True, "the awaiting tier passes the clause; it is not a FAIL"
    assert _awaiting_hints(mod, reasons), (
        "the awaiting hint did not survive to the step: "
        f"{[r[:70] for r in reasons]}")
    assert _incomplete_hints(mod, reasons), (
        "the INCOMPLETE tier must still be raised — awaiting REFINES it, and a "
        "step that loses it would be reported as a bare PASS")


def test_the_reader_does_not_depend_on_the_producer_saying_the_word(tmp_path):
    """LINK 2, AND THE MEASUREMENT THAT MATTERS MOST. With the rc=4 message
    self-sufficient, a gate whose stdout carries NO sentinel still reaches the
    tier — so this is fixed for every gate in the family, not only the one
    taught to print it. MEASURED on run21 by reverting the producer alone:
    reason_class stayed `awaiting_agent_pass`."""
    mod = _fcc("fcc_awaiting_2")
    _stub_gate(mod, 4, _BARE_INCOMPLETE_STDOUT)
    _passed, reasons = mod._evaluate_gate(tmp_path, _ALL_OF_GATE)
    assert _awaiting_hints(mod, reasons), (
        "with rc=4 and the INCOMPLETE sentinel the tier is established by the "
        "RETURN CODE; requiring the producer's own word made it unreachable")


def test_the_awaiting_token_starts_a_line_of_the_readers_own_message(tmp_path):
    """LINK 2, stated as the property that was wrong. `_stdout_signals_token`
    believes a token only where it begins a line, and the rc=4 message named it
    mid-sentence after `INCOMPLETE: `."""
    mod = _fcc("fcc_awaiting_3")
    _stub_gate(mod, 4, _BARE_INCOMPLETE_STDOUT)
    outcome = mod._check_program_exit_zero(
        tmp_path, "phase1_expert_parse_track . --check-report")
    out = outcome[1] if isinstance(outcome, tuple) else outcome.output
    assert mod._stdout_signals_token(out, mod._AWAITING_STDOUT_TOKEN), (
        "the reader's own message must carry the token where its own test can "
        f"see it; got {out[:200]!r}")


# ── THE TEETH: a short population is still a short population ────────────────

def test_a_bare_incomplete_without_rc4_is_not_promoted_to_awaiting(tmp_path):
    """THE CONTROL THIS WHOLE CHANGE MUST NOT BREAK. A gate that exits 0 and
    prints `INCOMPLETE:` is disclosing a partially examined population — the
    original and correct meaning of the tier. It must NOT acquire the awaiting
    class, or the two states are one word again in the other direction."""
    mod = _fcc("fcc_awaiting_4")
    _stub_gate(mod, 0, _BARE_INCOMPLETE_STDOUT)
    _passed, reasons = mod._evaluate_gate(tmp_path, _ALL_OF_GATE)
    assert _incomplete_hints(mod, reasons), "the INCOMPLETE tier still applies"
    assert not _awaiting_hints(mod, reasons), (
        "a short population was relabelled as an awaiting hand-off: "
        f"{[r[:70] for r in reasons]}")


def test_a_bare_rc4_without_the_sentinel_stays_a_failure(tmp_path):
    """The `_WAIVER_EXIT_CODE` shape, unchanged: rc=4 alone must not inherit the
    tier, or a stray 4 from an unrelated program buys a pass."""
    mod = _fcc("fcc_awaiting_5")
    _stub_gate(mod, 4, "some unrelated program output\n")
    passed, reasons = mod._evaluate_gate(tmp_path, _ALL_OF_GATE)
    assert passed is False, "a bare rc=4 is a FAIL"
    assert not _awaiting_hints(mod, reasons)


# ── the blocker's basis and its sentence ─────────────────────────────────────

def _classify(reason_class: str):
    import _blocker_classification as bc
    return bc.classify({
        "id": "D1", "status": "NOT_MEASURED", "reason_class": reason_class,
        "reasons": [], "disclosures": [],
    })


def test_the_awaiting_blocker_names_a_cause_to_act_on():
    """The class does NOT move -- correctly. What moves is the false sentence."""
    classification, basis, why = _classify("awaiting_agent_pass")
    assert classification == "UNCLASSIFIED", (
        "awaiting is not MISSING_CAPABILITY: that word is defined as a named "
        "tool, board or PDK being absent, and none is")
    assert basis == "awaiting-agent-pass"
    assert "names no cause to act on" not in why
    assert "hand-off" in why and "re-run" in why


def test_a_short_population_keeps_the_disclosure_tier_basis():
    """CONTROL. The new rule must fire on the awaiting class ALONE."""
    classification, basis, why = _classify("partial_population")
    assert (classification, basis) == ("UNCLASSIFIED", "disclosure-tier")
    assert "names no cause to act on" in why


# ── the producer's own stdout ────────────────────────────────────────────────

def _staged_report(tmp_path: Path, *, disposition: str, ai_status: str,
                   verdict: str) -> Path:
    # One L-doc, so the project HAS a Phase-1 root, and the recorded root is
    # DERIVED from it by the program's own function rather than typed here: the
    # checker refuses a report whose root digest does not match the tree, and a
    # hand-written digest would be a fixture asserting against itself.
    import phase1_expert_parse_track as _t
    doc = tmp_path / "phase1/generated_docs/L1_DATASHEET.json"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(json.dumps({"layer": "L1", "fields": {}}) + "\n")
    root = _t.phase1_root_identity(tmp_path)
    assert root.get("status") == "OK", root
    rel = Path("reports/audit/phase1/expert_parse_track.json")
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "program": "phase1_expert_parse_track", "verdict": verdict,
        "ai_subtrack": {"status": ai_status},
        "execution": {"complete": disposition != "AWAITING",
                      "disposition": disposition,
                      "required_ai_status": "CONSUMED",
                      "required_ai_consumed_min": 1,
                      "observed_ai_status": ai_status,
                      "observed_ai_consumed": 0 if disposition == "AWAITING" else 1,
                      "derivation": None,
                      "deterministic_examined": 0},
        "phase1_root": root,
        "producer": {"invoked_by": "phase1_one_shot_runner",
                     "invocation_id": "00000000-0000-0000-0000-000000000000",
                     "returncode": 4 if disposition == "AWAITING" else 0},
    }, indent=2) + "\n")
    return p


def _check_report(tmp_path: Path):
    return subprocess.run(
        [sys.executable, str(PROGRAMS / "phase1_expert_parse_track.py"),
         str(tmp_path), "--check-report"],
        capture_output=True, text=True)


def test_the_producer_says_the_word_for_the_state_it_proved(tmp_path):
    """LINK 1. The branch accepts rc=4 only WITH verdict INCOMPLETE and
    `AI_HANDOFF_EMITTED` — the awaiting triple — and then printed only
    "INCOMPLETE". The token appeared nowhere in that file."""
    _staged_report(tmp_path, disposition="AWAITING",
                   ai_status="HANDOFF_EMITTED", verdict="INCOMPLETE")
    r = _check_report(tmp_path)
    assert any(line.lstrip().startswith("AWAITING_AGENT_PASS")
               for line in r.stdout.splitlines()), (
        "the sentinel must START a line to be read at all; got "
        f"{r.stdout[:300]!r}")
    assert r.stdout.splitlines()[0].startswith("INCOMPLETE: "), (
        "and the INCOMPLETE headline must stay FIRST: the consumer keeps a "
        "300-char head, which is what makes it survive a long project path")


def test_the_producer_is_silent_about_awaiting_when_it_is_not_awaiting(tmp_path):
    """CONTROL. A consumed track must not print the sentinel, or every completed
    run would claim to be waiting."""
    _staged_report(tmp_path, disposition="COMPLETE",
                   ai_status="CONSUMED", verdict="PASS")
    r = _check_report(tmp_path)
    assert "AWAITING_AGENT_PASS" not in r.stdout
