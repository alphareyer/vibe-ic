#!/usr/bin/env python3
"""vibe-ic#2158 (half 1, PRODUCER) — a persistent report may not record the
ephemeral scratch directory the step happened to run in.

`step_rtl_gen` copies the project into
`tempfile.TemporaryDirectory(prefix="vibeic-rtl-step-")`, runs the generator
there, and remaps every stage pathname in the result back to the LIVE project
before it is written into `reports/orchestrator/phase2_one_shot.json` — a
report that outlives the run by design.

MEASURED (lane rbsub6, 8HD-9, 2026-09-07, project `d/subservient`): the report
carried BOTH forms in one `steps[3].detail`, produced by a single f-string —

    --project /tmp/lane.rbsub6/vibeic-rtl-step-6k_pkx29/subservient \
    --digest  /home/reyerchu/_lane_rbsub6/d/subservient/phase2/stage1/lessons.md

The remap replaced `<stage>` only when the string continued `<stage>/` (or was
exactly `<stage>`), so the argument that named the stage ROOT — with a SPACE
after it, not a separator — survived into the persistent artefact. The command
the report tells its reader to run therefore points at a directory the run
itself deleted.

DIRECTION OF THE PROOF. `test_the_pre_fix_shape_really_did_leak` re-implements
the pre-fix remap and asserts the SAME input leaks under it. Without that arm
these tests would pass against code that never had the defect, and would prove
nothing (`flow-change-acceptance` §2: a test that cannot fail against the
pre-fix code is not a control).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as dosr  # noqa: E402


# The exact scratch shape the runner creates, and a live project root that is
# NOT under it. Neither has to exist: the remap is a pure string operation.
STAGE = Path("/tmp/lane.neutral/vibeic-rtl-step-6k_pkx29/subservient")
LIVE = Path("/home/reyerchu/runroot/subservient")

# The measured detail, reduced to the two arguments that disagreed.
MEASURED_DETAIL = (
    "Then VERIFY, rather than asserting it:\n"
    f"    python3 plugins/vibe-ic/programs/lesson_consumption_check.py "
    f"--project {STAGE} --digest {STAGE}/phase2/stage1/lessons.md \\\n"
    f"        --scoring-record {STAGE}/phase2/stage1/rec.json --strict\n"
)


def _pre_fix_remap(value: str, stage: Path, project: Path) -> str:
    """The remap exactly as it stood before #2158 — separator-only."""
    import os
    stage_text = str(stage)
    if value == stage_text or value.startswith(stage_text + os.sep):
        return str(project) + value[len(stage_text):]
    return value.replace(stage_text + os.sep, str(project) + os.sep)


def test_the_pre_fix_shape_really_did_leak():
    """NEGATIVE CONTROL — the defect is reproducible, so the tests below bite."""
    leaked = _pre_fix_remap(MEASURED_DETAIL, STAGE, LIVE)
    assert str(STAGE) in leaked, (
        "the pre-fix remap must leak the stage root, otherwise every "
        "assertion in this file is vacuous")
    # And it leaked ONLY the child-less occurrence: the two that continued
    # with a separator were remapped even before the fix.
    assert leaked.count(str(STAGE)) == 1, leaked


def test_the_planted_scratch_path_does_not_reach_the_report():
    """PRODUCER — plant the scratch path, assert the emitted value drops it."""
    out = dosr._phase1_remap_stage_value(MEASURED_DETAIL, STAGE, LIVE)
    assert str(STAGE) not in out, out
    # Not merely deleted — every argument now names a path under the run root.
    assert f"--project {LIVE} " in out, out
    assert f"--digest {LIVE}/phase2/stage1/lessons.md" in out, out
    assert f"--scoring-record {LIVE}/phase2/stage1/rec.json" in out, out


@pytest.mark.parametrize("tail,remapped", [
    ("", True),                 # exactly the stage root, end of string
    ("/phase2/x.json", True),   # a child — the only shape the old code caught
    (" --digest z", True),      # the MEASURED shape: a space
    ("\n", True),               # end of line
    ('"', True),                # quoted, as JSON prose writes it
    (".", True),                # end of an English sentence
    ("_other/x", False),        # a DIFFERENT directory sharing the prefix
    (".json", False),           # a DIFFERENT file sharing the prefix
])
def test_every_boundary_shape(tail, remapped):
    value = f"see {STAGE}{tail}"
    out = dosr._phase1_remap_stage_value(value, STAGE, LIVE)
    if remapped:
        assert out == f"see {LIVE}{tail}", out
        assert str(STAGE) not in out, out
    else:
        assert out == value, out


def test_the_whole_emitted_step_record_is_clean():
    """The report is JSON: assert over the SERIALIZED bytes, not one field."""
    step = dosr.StepResult(
        name="rtl_gen",
        status="WAIVED",
        duration_s=2.05,
        detail=MEASURED_DETAIL,
        output_files=[str(STAGE / "phase2" / "stage1" / "rtl" / "top.sv"),
                      str(STAGE)],
        extras={"lessons_digest": str(STAGE / "phase2/stage1/lessons.md"),
                "project_root": str(STAGE),
                "nested": {"ack": [str(STAGE / "a.json"), str(STAGE)]},
                "lessons_count": 212},
    )
    step.detail = dosr._phase1_remap_stage_value(step.detail, STAGE, LIVE)
    step.output_files = dosr._phase1_remap_stage_value(
        step.output_files, STAGE, LIVE)
    step.extras = dosr._phase1_remap_stage_value(step.extras, STAGE, LIVE)

    emitted = json.dumps({
        "name": step.name, "status": step.status,
        "duration_s": step.duration_s, "detail": step.detail,
        "output_files": step.output_files, "extras": step.extras,
    })
    assert str(STAGE) not in emitted, emitted
    # MEMBERSHIP, not a count: nothing was dropped on the way through.
    assert step.extras["lessons_count"] == 212
    assert set(step.extras) == {"lessons_digest", "project_root", "nested",
                                "lessons_count"}
    assert step.extras["project_root"] == str(LIVE)
    assert step.extras["nested"]["ack"] == [str(LIVE / "a.json"), str(LIVE)]
    assert step.output_files[-1] == str(LIVE)


def test_a_value_with_no_stage_reference_is_returned_unchanged():
    v = "no scratch path here at all; /home/x/y is a live path"
    assert dosr._phase1_remap_stage_value(v, STAGE, LIVE) is v
