#!/usr/bin/env python3
"""vibe-ic#2103 — the lesson-consumption gate had no caller and no reader.

WHAT WAS MEASURED ON THE PRE-FIX TIP
====================================
`lesson_consumption_check` shipped complete: it scores the staged digest
against the design's own spec, it can REFUSE a run whose denominator is not the
one the scorer scored (#2086), and `--strict` can fail an unacknowledged strong
match. The runner imported it — and used it as a LIBRARY only. Grepped over the
whole tree at 94617408759e, the ONLY sites naming `lessons_ack.json` were the
two lines in `design_one_shot_runner` that BUILD the path in order to print it
to the author:

    programs/design_one_shot_runner.py:6281   ack_path = str(... / "lessons_ack.json")
    programs/design_one_shot_runner.py:6376   extras["lessons_ack_path"] = ack_path

Nothing opened it. So an author who acknowledged every named section and an
author who wrote no acknowledgement at all produced runs whose published report
was identical, and the gate that exists to tell those two apart was reachable
only by a human who read the handoff text and chose to type the command.

WHAT THE FIX IS
===============
`design_one_shot_runner.step_lesson_consumption` runs the gate with the argv
the handoff prints (`--project`, the staged digest, the ack, the scoring record
when present, `--strict`) at the first site that sees authored RTL, and writes
its verdict into the run ledger. The status is always `ADVISORY`, which
`_aggregate_verdict` classifies as green, so the row cannot move the run verdict
in either direction — the flow contract keeps this consumer out of a gate clause
(flow Step 1 `required_outputs`: unconditional declarations versus a digest that
is staged only at an authoring WAIVE).

BOTH DIRECTIONS, PER TEST
=========================
Every test below drives the REAL step over a REAL fixture project; nothing is
monkeypatched and no report is hand-written. The pair that proves the ack is
READ rather than merely NAMED is
`test_an_unacknowledged_strong_section_is_a_FINDING` /
`test_acknowledging_the_same_section_flips_the_verdict_to_PASS`: one fixture,
one file added, two different verdicts. On the pre-fix tree the function does
not exist and both are errors; with the file's only read of the ack deleted,
both report the same verdict, which is the defect.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as DOSR        # noqa: E402
import lesson_consumption_check as LCC       # noqa: E402

#: The literal dispatch. A function that exists and is never called is the
#: state #2103 describes, so the call site is asserted by name.
_DISPATCH = "plan.append(step_lesson_consumption(project))"

#: A spec whose distinctive vocabulary lands in exactly one digest section.
#: Deliberately generic hardware prose — no chip, no vendor, no PDK.
_SPEC = (
    "Design a thermometer-code encoder driving a barrel shifter. The "
    "thermometer output must be monotonic, and the barrel rotation is "
    "supplemental to the thermometer decode path. Thermometer encoding and "
    "barrel shifting dominate this design.\n"
)

#: The section the spec above matches, and nine that it does not. Ten sections
#: is over `lesson_consumption_check._MIN_SECTIONS_FOR_STRICT`, so `--strict`
#: is not downgraded to a low-confidence NOTICE and the fixture measures the
#: strict path the handoff's own command uses.
_MATCHING_SECTION = "thermometer barrel supplemental encoding"


def _digest_text() -> str:
    secs = [f"### Skill: {_MATCHING_SECTION}\n\n"
            "**When to apply**: thermometer barrel designs.\n"
            "Thermometer monotonic barrel rotation supplemental decode path.\n"]
    for i in range(9):
        secs.append(f"### Skill: unrelated topic {i}\n\n"
                    f"**When to apply**: nothing here.\n"
                    f"Vocabulary{i} lorem ipsum dolor sit amet consectetur "
                    f"adipiscing elit gizmo{i} widget{i} sprocket{i}.\n")
    return "# Lessons\n\n" + "\n".join(secs)


def _authoring_project(root: Path) -> Path:
    """A project in the state the WAIVE handoff leaves behind.

    Spec prose under `phase1/input_prompt/`, the digest and the scoring record
    under `phase2/stage1/` — written by the SAME functions the staging site
    calls (`lesson_consumption_check.build_scoring_record` over
    `parse_digest` / `match_sections`), never hand-shaped, so a change to the
    record's schema breaks this fixture instead of being papered over by it.
    """
    p = root / "proj"
    (p / "phase1/input_prompt").mkdir(parents=True)
    (p / "phase2/stage1").mkdir(parents=True)
    (p / "phase1/input_prompt/prompt.md").write_text(_SPEC)
    digest = p / "phase2/stage1/lessons.md"
    digest.write_text(_digest_text())
    sections = LCC.parse_digest(digest.read_text())
    spec_text = DOSR._gather_spec_text(p)
    matches = LCC.match_sections(spec_text, sections)
    rec = LCC.build_scoring_record(p, str(digest), sections, matches, spec_text)
    assert rec["strong_sections"] == [_MATCHING_SECTION], (
        f"the fixture no longer produces exactly one strong match; the "
        f"scorer named {rec['strong_sections']}. Re-shape the fixture — do "
        f"not relax the assertion, the single-match denominator is what makes "
        f"the acknowledged/unacknowledged pair readable.")
    (p / "phase2/stage1" / LCC.SCORING_RECORD_NAME).write_text(
        json.dumps(rec, indent=2))
    return p


def _acknowledge(project: Path, sections: List[str]) -> None:
    (project / "phase2/stage1/lessons_ack.json").write_text(json.dumps(
        {"lessons_applied": [{"section": s, "applied": True,
                              "note": "applied in the RTL"} for s in sections]}))


def _row(res) -> Dict:
    assert res.extras and "lesson_consumption" in res.extras, (
        f"the step produced no ledger row: extras={res.extras!r}")
    return res.extras["lesson_consumption"]


# ---------------------------------------------------------------------------
# THE WIRING ITSELF
# ---------------------------------------------------------------------------
def test_the_runner_dispatches_the_gate_at_a_real_call_site() -> None:
    """A gate nothing calls produces no verdict. #2080's invocation.v1 rule.

    Asserted on the SOURCE, not on an import: `step_lesson_consumption` existing
    is not the fix — the fix is that `main()` calls it on every run. Deleting the
    one line this asserts is the mutation arm, and it turns this test red while
    every behavioural test below still passes, which is exactly the state the
    pre-fix tree was in.
    """
    src = Path(DOSR.__file__).read_text(errors="replace")
    assert _DISPATCH in src, (
        f"design_one_shot_runner no longer dispatches the lesson-consumption "
        f"gate ({_DISPATCH!r} absent). A checker with no call site produces no "
        f"verdict and the tree looks the same either way — vibe-ic#2103.")


def test_the_dispatch_sits_where_authored_RTL_is_first_judged() -> None:
    """After the rtl_validate span, before the remaining Step-2 checks.

    Order is load-bearing and not cosmetic: the digest and the scoring record
    are written by the PREVIOUS invocation's staging site and the ack is written
    by the author between the two, so the gate must run on the re-invocation and
    not at the WAIVE. Dispatched before `step_crosslayer_rewrite_fidelity` puts
    the acknowledgement question at the same point the RTL starts being judged.
    """
    src = Path(DOSR.__file__).read_text(errors="replace")
    here = src.index(_DISPATCH)
    determinism = src.index("step_determinism_gates, project, args.top_name")
    crosslayer = src.index("plan.append(step_crosslayer_rewrite_fidelity(project))")
    assert determinism < here < crosslayer, (
        "the lesson-consumption dispatch moved out of the Step-2 RTL-validation "
        "window (after the rtl_validate span, before the cross-layer fidelity "
        "judge). Its inputs are written one invocation earlier; re-establish the "
        "ordering rather than deleting this assertion.")


def test_the_step_can_never_move_the_run_verdict() -> None:
    """ADVISORY by contract, and the contract is `_aggregate_verdict`'s own list.

    Read out of the shipped source rather than restated here, so a future edit
    that drops ADVISORY from the green statuses fails this test instead of
    silently promoting the row to a blocking one.
    """
    src = Path(DOSR.__file__).read_text(errors="replace")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_aggregate_verdict")
    green = next(
        ast.literal_eval(t.value) for t in ast.walk(fn)
        if isinstance(t, ast.Assign) and len(t.targets) == 1
        and isinstance(t.targets[0], ast.Name)
        and t.targets[0].id == "_GREEN_STATUSES")
    assert "ADVISORY" in green
    step = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "step_lesson_consumption")
    statuses = {c.args[1].value for c in ast.walk(step)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "StepResult" and len(c.args) >= 2
                and isinstance(c.args[1], ast.Constant)}
    assert statuses == {"ADVISORY"}, (
        f"step_lesson_consumption returns {sorted(statuses)}; every return must "
        f"be ADVISORY. A blocking verdict from a natural-language overlap score "
        f"is how a gate gets turned off.")


# ---------------------------------------------------------------------------
# THE ACK IS READ — THE PAIR
# ---------------------------------------------------------------------------
def test_an_unacknowledged_strong_section_is_a_FINDING(tmp_path: Path) -> None:
    p = _authoring_project(tmp_path)
    assert not (p / "phase2/stage1/lessons_ack.json").exists()
    row = _row(DOSR.step_lesson_consumption(p))
    assert row["verdict"] == "FINDING", row
    assert row["ack_present"] is False
    assert row["strong_matches"] == 1 and row["unacknowledged"] == 1, row


def test_acknowledging_the_same_section_flips_the_verdict_to_PASS(
        tmp_path: Path) -> None:
    """The other direction, one file apart. This is the whole of #2103.

    Same project, same digest, same spec, same scoring record. The ONLY change
    is that `lessons_ack.json` now exists and names the section. On the pre-fix
    tree nothing opened that file, so this and the test above were the same run.
    """
    p = _authoring_project(tmp_path)
    before = _row(DOSR.step_lesson_consumption(p))
    _acknowledge(p, [_MATCHING_SECTION])
    after = _row(DOSR.step_lesson_consumption(p))
    assert (before["verdict"], after["verdict"]) == ("FINDING", "PASS"), (
        before, after)
    assert after["ack_present"] is True and after["unacknowledged"] == 0, after


# ---------------------------------------------------------------------------
# THE ROW IS WRITTEN ON EVERY RUN, AND NEVER LAUNDERED INTO A SKIP
# ---------------------------------------------------------------------------
def test_a_run_that_staged_no_digest_records_NOT_APPLICABLE(
        tmp_path: Path) -> None:
    """A row that appears only on a finding cannot tell "ran and found nothing"
    from "was never wired" — the defect this file measures. A design whose RTL
    came from a deterministic generator stages no digest and owes no
    acknowledgement, and it says so."""
    p = tmp_path / "generated"
    (p / "phase2/stage1").mkdir(parents=True)
    res = DOSR.step_lesson_consumption(p)
    assert res.status == "ADVISORY"
    row = _row(res)
    assert row["verdict"] == "NOT_APPLICABLE" and row["digest_present"] is False


def test_a_refusing_gate_is_NOT_MEASURED_never_a_SKIP_or_a_PASS(
        tmp_path: Path) -> None:
    """"Could not read it" is not "read it and it was empty".

    An unreadable acknowledgement record makes the gate exit 2. The step must
    carry that word through: a SKIP would launder a tool error, and a PASS over
    a file it could not parse is the vacuous pass the gate exists to refuse.
    """
    p = _authoring_project(tmp_path)
    (p / "phase2/stage1/lessons_ack.json").write_text("{ not json at all")
    res = DOSR.step_lesson_consumption(p)
    assert res.status == "ADVISORY"
    row = _row(res)
    assert row["verdict"] == "NOT_MEASURED", row
    assert row["rc"] == 2 and "unreadable" in row["reason"], row


def test_a_scoring_record_from_another_spec_is_refused_not_passed(
        tmp_path: Path) -> None:
    """#2086's refusal, reached THROUGH the wiring rather than by hand.

    The record pins the spec that was scored. Move the spec under it and the
    gate must refuse: a verification of a different denominator is not a
    verification. Without the `--scoring-record` argument this run would print a
    clean verdict over the wrong subject.
    """
    p = _authoring_project(tmp_path)
    (p / "phase1/input_prompt/prompt.md").write_text(
        "A completely different design: an asynchronous FIFO with gray-coded "
        "pointers and two independent clock domains.\n")
    row = _row(DOSR.step_lesson_consumption(p))
    assert row["verdict"] == "NOT_MEASURED", row
    assert row["rc"] == 2


def test_the_verdict_report_is_published_under_reports_phase2_gates(
        tmp_path: Path) -> None:
    """The evidence, not only the row: a later reader with no access to this
    process must be able to see what was scored."""
    p = _authoring_project(tmp_path)
    res = DOSR.step_lesson_consumption(p)
    out = p / "reports/phase2/gates/lesson_consumption.json"
    assert out.is_file(), sorted(str(x) for x in p.rglob("*.json"))
    assert res.output_files == [str(out)]
    doc = json.loads(out.read_text())
    assert doc["gate"] == "lesson_consumption_check"
    assert doc["spec_source"] == "project:" + str(p)
