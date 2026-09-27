#!/usr/bin/env python3
"""FX_P2 (3c) — the waiver row `validate_step_row` DERIVES from a step's detail
never ends inside a token.

MEASURED on subservient (8HD-4, 2026-09-28): phase 2's final_audit FAILed
`project_outputs_in_tree_check` on a dangling external reference,
`reports/orchestrator/phase2_one_shot.json -> /tmp/vibeic-rtl-step-<id>/run`.
It sat in `steps[rtl_gen].waiver_rows[0].reason`, which `validate_step_row`
derives as `detail[:400]`; the cut fell inside the stage path
`/tmp/vibeic-rtl-step-<id>/run_branch/...`. The step's `detail` carried the
correctly remapped path; the record's remap matches whole paths only, so the
half path could not be remapped and was published. Same class as #2061 R-01.

Driven through the real `validate_step_row`. chip-AGNOSTIC: synthetic detail text.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import verdict as V  # noqa: E402

STAGE = "/tmp/vibeic-rtl-step-abcd1234/run_branch/phase2/stage1/fallback_skill.md"


@dataclass
class _Row:
    name: str
    status: str
    detail: str
    reason_class: str = ""
    declared_by: str = ""
    waiver_rows: List[dict] = field(default_factory=list)
    attribution: str = ""
    disclosures: List[str] = field(default_factory=list)


def _derived_reason(detail: str) -> str:
    row = _Row("rtl_gen", "PASS_WITH_WAIVERS", detail)
    V.validate_step_row(row)
    return row.waiver_rows[0]["reason"]


def test_a_path_across_the_window_is_kept_whole_or_dropped():
    lead = "x" * 360 + " READ THE SKILL AT THIS PATH: `"
    reason = _derived_reason(lead + STAGE + "` and more text after it")
    assert len(reason) <= 400
    # never a PREFIX of the path that is not the whole path
    for n in range(1, len(STAGE)):
        if reason.endswith(STAGE[:n]):
            assert False, f"the reason ends inside the path: {reason[-60:]!r}"


def test_a_detail_inside_the_window_is_unchanged():
    detail = "short detail naming " + STAGE
    assert _derived_reason(detail) == detail


def test_a_single_overlong_token_is_kept_not_blanked():
    token = "y" * 500
    assert _derived_reason(token) == token
