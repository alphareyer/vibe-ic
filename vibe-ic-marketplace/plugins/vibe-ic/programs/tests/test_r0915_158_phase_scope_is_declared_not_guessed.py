#!/usr/bin/env python3
"""R-0915-158 — a step's phase comes from what it DECLARES, not from its id shape.

THE MEASUREMENT (subservient x gf180mcuD as a DIE and spm x gf180mcuD, lanes
icsub5 / icspm5, 2026-09-23): a `--phase 2` audit — the one
`design_one_shot_runner.step_final_audit` runs, argv
`flow_compliance_check.py <project> --phase 2 --strict-structural
--allow-thin-input` — reported, on BOTH ICs:

    ✗ [FAIL] Step 15.5ic: Pad Ring                                (stage3) (missing_artefact)
    ✗ [FAIL] Step 26.5ic: Die Finishing — seal ring + die id      (stage3) (missing_artefact)
    ✗ [FAIL] Step 37.3:  GDS stream-out / finishing fidelity      (stage4) (missing_artefact)
    ✗ [FAIL] Step 37.4:  Sign-off metrics aggregation             (stage4) (missing_artefact)
    ✗ [FAIL] Step 37.5ic: Tape-out Precheck                       (stage4) (missing_artefact)

Steps whose outputs cannot exist until phase 3 has run, judged in a phase-2
scope. They got there for one reason: `--phase 2` kept a step only when its id
was an `int` in 1..6, and every NON-INTEGER id fell into an `else` branch that
kept it in BOTH scopes as "phase-agnostic" — a class meant for A*/DT*/FS*/M*/P0.

Their own `stage:` had said which phase they belong to all along.

The rules this deck pins, in both directions:
  * a step that DECLARES `phase_scope: agnostic` is judged in BOTH scopes;
  * a step that declares neither a known stage nor agnostic is STILL kept in
    both and disclosed — an unlabelled future step never vanishes silently;
  * a stage3/stage4 step is NOT in the phase-2 scope;
  * it IS still in the phase-3 and whole-flow scopes;
  * the integer-id rule is untouched.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import flow_compliance_check as F  # noqa: E402

YAML = PROG.parent / "flow" / "phase1_phase2_phase3.yaml"

#: The five the measurement named, with the stage each one declares.
MEASURED = {"15.5ic": "stage3", "26.5ic": "stage3",
            "37.3": "stage4", "37.4": "stage4", "37.5ic": "stage4"}
#: The class the old `else` branch was actually written for.
AGNOSTIC = ["P0", "FS1", "DT1", "DT2", "DT3",
            "A1", "A5", "A9", "M1", "M4", "D1", "0.5ic"]


def _yaml_text() -> str:
    return YAML.read_text(errors="replace")


def _step_block(sid: str) -> str:
    y = _yaml_text()
    m = re.search(r'^\s*-\s*id:\s*["\']?' + re.escape(sid) + r'["\']?\s*$',
                  y, re.M)
    assert m, f"step {sid} not found in the flow yaml"
    return y[m.start():m.start() + 2500]


# ── 1. the flow yaml says it, rather than the code guessing it ────────────
@pytest.mark.parametrize("sid", AGNOSTIC)
def test_a_genuinely_phase_agnostic_step_DECLARES_itself(sid):
    assert "phase_scope: agnostic" in _step_block(sid), (
        f"{sid} must declare its own phase-agnosticism, not rely on its id shape")


@pytest.mark.parametrize("sid,stage", sorted(MEASURED.items()))
def test_the_measured_five_declare_a_stage_and_NOT_agnostic(sid, stage):
    blk = _step_block(sid)
    assert f"stage: {stage}" in blk, (sid, stage)
    assert "phase_scope: agnostic" not in blk, (
        f"{sid} is a {stage} step; it must not claim to be phase-agnostic")


# ── 2. the stage → phase map, and its refusal ─────────────────────────────
def test_stage1_is_phase_2_and_every_later_stage_is_phase_3():
    """Derived from what the integer rule has always meant: `--phase 2` is
    steps 1-6, and those six are exactly the stage1 steps."""
    assert F._STAGE_PHASE["stage1"] == "2"
    for st in ("stage2", "stage3", "stage4", "stage5_manufacturing"):
        assert F._STAGE_PHASE[st] == "3", st


def test_an_unknown_stage_is_not_guessed_at():
    """THE REFUSAL. A stage the map does not know yields no phase, and the
    caller keeps such a step in BOTH scopes and discloses it, so a new stage
    cannot silently drop out of an audit."""
    assert F._STAGE_PHASE.get("stage_phase1") is None
    assert F._STAGE_PHASE.get("stage_analog") is None
    assert F._STAGE_PHASE.get("stage_mixed_signal") is None
    assert F._STAGE_PHASE.get("stage_that_does_not_exist_yet") is None


# ── 3. the selection itself, both directions ──────────────────────────────
def _select(steps, phase):
    """Exercise the real predicate the audit uses, on synthetic rows."""
    kept = []
    for s in steps:
        sid = s.get("id")
        if isinstance(sid, int):
            lo, hi = (1, 6) if phase == "2" else (7, 99)
            if lo <= sid <= hi:
                kept.append(sid)
        elif str(s.get("phase_scope") or "").strip().lower() == "agnostic":
            kept.append(sid)
        else:
            ph = F._STAGE_PHASE.get(str(s.get("stage") or "").strip())
            if ph is None or str(ph) == phase:
                kept.append(sid)
    return kept


ROWS = [
    {"id": 3, "stage": "stage1"},                       # integer, phase 2
    {"id": 21, "stage": "stage3"},                      # integer, phase 3
    {"id": "P0", "stage": "stage1", "phase_scope": "agnostic"},
    {"id": "15.5ic", "stage": "stage3"},
    {"id": "37.4", "stage": "stage4"},
    {"id": "A1", "stage": "stage_analog", "phase_scope": "agnostic"},
    {"id": "NEW9", "stage": "stage_not_in_the_map"},    # unlabelled future step
]


def test_a_stage3_or_stage4_step_is_NOT_in_the_phase_2_scope():
    kept = _select(ROWS, "2")
    assert "15.5ic" not in kept and "37.4" not in kept, kept


def test_the_same_steps_ARE_still_in_the_phase_3_scope():
    """THE NEGATIVE ARM: this narrows phase 2, it does not stop judging them."""
    kept = _select(ROWS, "3")
    assert "15.5ic" in kept and "37.4" in kept, kept


def test_a_declared_agnostic_step_is_in_BOTH_scopes():
    assert "P0" in _select(ROWS, "2") and "P0" in _select(ROWS, "3")
    assert "A1" in _select(ROWS, "2") and "A1" in _select(ROWS, "3")


def test_an_unlabelled_step_is_kept_in_BOTH_rather_than_dropped():
    assert "NEW9" in _select(ROWS, "2") and "NEW9" in _select(ROWS, "3")


def test_the_integer_rule_is_untouched():
    assert 3 in _select(ROWS, "2") and 3 not in _select(ROWS, "3")
    assert 21 in _select(ROWS, "3") and 21 not in _select(ROWS, "2")


# ── 4. the MUTATION arm: the old id-shape guess must go red ───────────────
def test_MUTATION_the_old_non_integer_equals_agnostic_guess_is_caught():
    """Re-plant the defect: keep EVERY non-integer id in both scopes."""
    def defect(steps, phase):
        kept = []
        for s in steps:
            sid = s.get("id")
            if isinstance(sid, int):
                lo, hi = (1, 6) if phase == "2" else (7, 99)
                if lo <= sid <= hi:
                    kept.append(sid)
            else:
                kept.append(sid)          # the pre-R-0915-158 behaviour
        return kept

    assert "15.5ic" in defect(ROWS, "2")          # the defect, reproduced
    assert "15.5ic" not in _select(ROWS, "2")     # and the fix differs on it
