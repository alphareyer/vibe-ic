"""R-0915-51: the report card is the one the flow produces.

MEASURED 2026-09-16 on the SPM verdict run (run30 / `spm25`, main d5fe608e3).
The completion audit named ONE cause for the whole run's FAIL::

    step 36 FAIL <- step_internal_fail_bubble_up_check
      [STEP_FAIL_NOT_BUBBLED] reports/phase2/gates/agent_report_presence.json
      verdict=FAIL  ("AGENT_REPORT.md does not exist at project root")
    steps 37, 37.4, 37.5ip, 38: PASS_VOIDED_BY_DEPENDENCY on step 36

and the runner's own docstring already admitted the contradiction: "NOTHING in
the shipped flow writes AGENT_REPORT.md. The canonical report card moved to
reports/final_summary.md in v1.6.32 ... this gate was never re-pointed."

TWO HALVES, BOTH TESTED HERE.

(1) `agent_report_presence_check` resolves the card through the SIBLING's own
register, `agent_report_sha256_attestation_check._REPORT_CANDIDATE_REL_PATHS`
(generated card first, hand-authored back-compat second), and verifies the
sections THAT card declares. A generated card is held to what its generator
GUARANTEES — `final_report_generate.MANDATORY_SECTIONS`, imported, never
re-typed — and a hand-authored one to the five this gate has always demanded.
It still FAILS with no card and with a declared section missing; both are
below. NOBODY HAND-WRITES A VERDICT CARD: accepting the generated one is what
keeps it that way.

(2) The step that wires a gate ADVISORY (`"blocking": False` in its ledger
row) now writes `"verdict_mode": "ADVISES"` into that GATE's own JSON, because
that is where `step_internal_fail_bubble_up_check` reads it ("verdict_mode:
ADVISES IS AN ANSWER, NOT A SILENCE"). The decision was written in the ledger
and the consumer that needed it could not see it. This is CONSISTENCY, NOT A
LEVER: a blocking gate's report never receives the key, and no gate changes
tier.

chip-AGNOSTIC: synthetic projects in tmp_path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import agent_report_presence_check as A  # noqa: E402
import agent_report_sha256_attestation_check as S  # noqa: E402
import design_one_shot_runner as D  # noqa: E402
import final_report_generate as G  # noqa: E402

PRESENCE = PROGRAMS / "agent_report_presence_check.py"
BUBBLE = PROGRAMS / "step_internal_fail_bubble_up_check.py"


#: Read with `getattr` so this file COLLECTS against a tree that does not have
#: the constant yet. A control arm that errors at collection proves only that
#: a name is new; one that RUNS proves the old code answers wrongly.
GUARANTEED = tuple(getattr(G, "MANDATORY_SECTIONS", ()))


def _card(project, rel, sections):
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "# Report\n\n" + "\n\n".join(f"## {s}\n\nbody\n" for s in sections)
    p.write_text(body)
    return p


def _generated_sections():
    return [heading for _n, heading, _s in GUARANTEED]


def _run_presence(project):
    r = subprocess.run([sys.executable, str(PRESENCE), str(project)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── (1) the card is resolved through the sibling's register ──────────────

def test_the_register_is_the_siblings_not_a_copy():
    candidates = getattr(A, "_candidate_rel_paths", lambda: ())()
    assert candidates == tuple(S._REPORT_CANDIDATE_REL_PATHS)
    assert candidates[0] == "reports/final_summary.md"


def test_the_generated_card_passes(tmp_path):
    _card(tmp_path, "reports/final_summary.md", _generated_sections())
    rc, out = _run_presence(tmp_path)
    assert rc == 0, out
    assert "reports/final_summary.md" in out


def test_the_section_list_is_derived_from_the_generator(tmp_path):
    """Not hand-typed: rename a heading in the generator and this moves with
    it, because the checker imports the generator's own guarantee."""
    required = getattr(A, "_sections_for", lambda _r: ())(
        "reports/final_summary.md")
    assert GUARANTEED, "the generator must declare what it guarantees"
    assert [n for n, _syn in required] == [n for n, _h, _s in GUARANTEED]


@pytest.mark.parametrize("name,heading,synonyms", GUARANTEED)
def test_every_guaranteed_heading_matches_its_own_synonyms(name, heading,
                                                           synonyms):
    """THE DRIFT LOOP, closed. The generator emits `heading`; the checker
    accepts `synonyms`. If someone renames the heading without moving the
    synonyms, the card the flow produces stops satisfying the gate that reads
    it -- silently, on every run. This fails first instead."""
    emitted = getattr(G, "section_heading", lambda n: None)(name)
    assert emitted == f"## {heading}"
    low = heading.lower()
    assert any(s in low for s in synonyms), (name, heading, synonyms)


# ── (1) the two refusals it must keep ────────────────────────────────────

def test_no_card_at_all_still_FAILS(tmp_path):
    """Re-pointing a presence check must not cost it its presence check."""
    rc, out = _run_presence(tmp_path)
    assert rc == 1, out
    assert "no report card exists" in out
    for rel in getattr(A, "_candidate_rel_paths", lambda: ())():
        assert rel in out


@pytest.mark.parametrize("drop", [n for n, _h, _s in GUARANTEED])
def test_a_generated_card_missing_a_declared_section_FAILS(tmp_path, drop):
    keep = [h for n, h, _s in GUARANTEED if n != drop]
    _card(tmp_path, "reports/final_summary.md", keep)
    rc, out = _run_presence(tmp_path)
    assert rc == 1, out
    assert drop in out


def test_an_empty_card_FAILS(tmp_path):
    p = tmp_path / "reports" / "final_summary.md"
    p.parent.mkdir(parents=True)
    p.write_text("   \n")
    rc, out = _run_presence(tmp_path)
    assert rc == 1, out


# ── (1) back-compat: a hand-authored card keeps the old five ─────────────

LEGACY = ["Verdict", "Acceptance evidence", "Waivers list", "Discoveries",
          "Iteration log"]


def test_a_hand_authored_card_is_held_to_the_legacy_five(tmp_path):
    _card(tmp_path, "AGENT_REPORT.md", LEGACY)
    rc, out = _run_presence(tmp_path)
    assert rc == 0, out
    assert "AGENT_REPORT.md" in out


def test_a_hand_authored_card_missing_one_of_the_five_FAILS(tmp_path):
    _card(tmp_path, "AGENT_REPORT.md", LEGACY[:-1])
    rc, out = _run_presence(tmp_path)
    assert rc == 1, out
    assert "Iteration log" in out


def test_the_generated_card_wins_when_both_exist(tmp_path):
    _card(tmp_path, "reports/final_summary.md", _generated_sections())
    _card(tmp_path, "AGENT_REPORT.md", ["Nothing useful"])
    rc, out = _run_presence(tmp_path)
    assert rc == 0, out
    assert "reports/final_summary.md" in out


# ── (2) the advisory decision is written where its consumer reads it ─────

def test_an_advisory_gate_report_receives_ADVISES(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"gate": "x", "verdict": "FAIL"}))
    stamp = getattr(D, "stamp_verdict_mode", lambda *_a, **_k: None)
    assert stamp(p, blocking=False) == "ADVISES"
    payload = json.loads(p.read_text())
    assert payload["verdict_mode"] == "ADVISES"
    assert payload["verdict"] == "FAIL", "the gate's own verdict is untouched"
    assert payload["verdict_mode_source"]


def test_a_BLOCKING_gate_report_NEVER_receives_it(tmp_path):
    """THE CONTROL THE RULING NAMES. This is consistency, not a lever: no gate
    changes tier, and a blocking gate's report cannot acquire the key here."""
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"gate": "x", "verdict": "FAIL"}))
    stamp = getattr(D, "stamp_verdict_mode", None)
    assert stamp is not None, "the stamp must exist to be constrained"
    assert stamp(p, blocking=True) is None
    assert "verdict_mode" not in json.loads(p.read_text())


@pytest.mark.parametrize("payload", ["{not json", "[]", ""])
def test_an_unreadable_report_is_left_alone(tmp_path, payload):
    p = tmp_path / "g.json"
    p.write_text(payload)
    stamp = getattr(D, "stamp_verdict_mode", None)
    assert stamp is not None
    assert stamp(p, blocking=False) is None
    assert p.read_text() == payload


def test_a_missing_report_is_not_created(tmp_path):
    p = tmp_path / "nope.json"
    stamp = getattr(D, "stamp_verdict_mode", None)
    assert stamp is not None
    assert stamp(p, blocking=False) is None
    assert not p.exists()


def test_the_stamp_is_what_the_bubble_up_gate_reads(tmp_path):
    """END TO END, and the reason part (2) exists: the same FAIL report is
    flagged without the key and acknowledged with it."""
    rel = "reports/phase2/gates/agent_report_presence.json"
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"gate": "agent_report_presence_check",
                             "verdict": "FAIL"}))

    def _bubble():
        r = subprocess.run([sys.executable, str(BUBBLE), str(tmp_path)],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    rc_before, out_before = _bubble()
    assert rc_before == 1, out_before
    assert "STEP_FAIL_NOT_BUBBLED" in out_before

    stamp = getattr(D, "stamp_verdict_mode", lambda *_a, **_k: None)
    assert stamp(p, blocking=False) == "ADVISES"
    rc_after, out_after = _bubble()
    assert rc_after == 0, out_after
    assert "acknowledged" in out_after
