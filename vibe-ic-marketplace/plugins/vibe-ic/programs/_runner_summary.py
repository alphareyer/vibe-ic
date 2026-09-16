#!/usr/bin/env python3
"""_runner_summary.py — the one-line-per-step rollup a one-shot runner prints.

THE DEFECT THIS CLOSES (vibe-ic#2081)
-------------------------------------
Every one-shot runner ended with a per-step rollup written as

    print(f"  {s.status:6} {s.name:8} {s.detail[:120]}")

`detail[:120]` is a SILENT fixed-width cut. It has no marker, so a reader
cannot tell a short reason from a long one that was amputated, and the cut
lands wherever 120 characters happen to end.

MEASURED on the published sha256 run (lane rbsha6, `run2.log:152`, the run
vibe-ic#2081 was filed from). `drv_promotion_corroboration` had computed and
recorded the whole finding — its JSON carries
`signoff_drv_violations: 368, claimed_drv_after: 176` and the sentence that
explains them. What the rollup printed was

      FAIL   drv_promotion_corroboration the promotion claimed it ended at 176
      DRV violation(s) from its own session, but the sign-off report the
      acceptance gate

— 157 characters, cut mid-clause. `368` sits at offset 133 of the detail, THIRTEEN
characters past the cut, so the one number the gate exists to corroborate was
the part that got dropped. #2081 reports that step as "reports FAIL with no
detail at all". It reports plenty of detail; the rollup deleted the half that
carried the number, which is indistinguishable from a gate that never had one.

TWO PROPERTIES, BOTH LOAD-BEARING
---------------------------------
1. A ROW THAT KEEPS THE RUN FROM BEING GREEN PRINTS ITS REASON IN FULL.
   `verdict.NON_GREEN` owns that classification and is reused
   rather than re-spelled here — a literal set of status words in a renderer
   is exactly the drift that module was created to delete. For every other
   row the bound stays, because the rollup is a rollup.
2. A CUT IS MARKED, AND SAYS WHAT IT DROPPED. `… (+N chars)` — so a bounded
   line can never again be read as a complete one. Silence about a truncation
   is what turned a recorded measurement into an apparently empty one.

AND THE ROLLUP IS ONE PHYSICAL LINE, ALWAYS. The detail is whitespace-collapsed
first. MEASURED on the same log: the `em_signoff` row's detail is a JSON
fragment, so the rollup printed

      PASS   em_signoff  "path",
        "items": [
          {
            "path": "reports/phase3/em.rpt",

— four rows of raw JSON inside a per-step summary, and the step's own word was
the only part on its line. A rollup whose row count is not the step count
cannot be read by eye or by grep.

NOT A WIDENING. The bound is unchanged for every row that does not carry a
finding, and nothing here relaxes, relabels or re-tiers any verdict: the status
word each row prints is the word the runner already computed.

chip-AGNOSTIC: status words and string layout only. No design, PDK or vendor
literal appears here.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as _tiers  # noqa: E402

#: The width the four runners already used for a bounded row. Kept as the
#: default so this landing changes no PASS row's rendering.
DEFAULT_WIDTH = 120

_WS = re.compile(r"\s+")


def one_line(detail: object) -> str:
    """The detail as ONE line: every run of whitespace becomes one space.

    Applied to EVERY row, whatever its status, because a multi-line detail
    breaks the rollup's shape rather than its content — a `PASS` row can spill
    JSON just as easily as a `FAIL` one, and it did.
    """
    return _WS.sub(" ", str(detail or "")).strip()


def summary_detail(detail: object, status: Optional[str] = None,
                   width: int = DEFAULT_WIDTH) -> str:
    """The rollup rendering of one step's detail.

    A NON_GREEN row is returned in full: its reason is the finding, and the
    rollup is where a reader looks for it. Any other row is bounded at
    `width`, and a bounded row that was actually cut says so and says by how
    much — never a silent amputation.

    `width <= 0` means "do not bound", which keeps the helper honest for a
    caller that wants the whole string.
    """
    text = one_line(detail)
    # `status` is OPTIONAL and its default is "not stated", which is not a
    # verdict word and must not be parsed as one: `verdict.parse` refuses
    # `None` by design, and most callers here pass only a detail and a width.
    # An unstated status bounds the text, which is the conservative half —
    # the full reason is only ever returned for a row that SAYS it is
    # non-green.
    if status is not None and _tiers.is_non_green(status):
        return text
    if width <= 0 or len(text) <= width:
        return text
    return f"{text[:width]}… (+{len(text) - width} chars)"
