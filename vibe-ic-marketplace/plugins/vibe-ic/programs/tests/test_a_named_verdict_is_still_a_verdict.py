"""A gate's failing line must survive rendering, even when the program names itself.

MEASURED 2026-09-10. `run_emit` in `tools/gatekeeper-land.sh` renders a failing
gate as (a) the lines matching a FAIL pattern, then (b) `tail -5`. The pattern
anchored the verdict word at the start of the line:

    ^[[:space:]]*(FAIL|ERROR)|\\[FAIL\\]|\\[ERROR\\]|FAILED

`landing_hygiene_ratchet_check` prefixes its verdicts with its own name —

    landing_hygiene_ratchet_check: FAIL — this landing INTRODUCES non-atomic report writes:
      console_tool_termination_check.py:289  .write_text(...)

— so branch (a) matched NOTHING, and the program's 127 lines of output meant the
`tail -5` window held its last two ADVISORY coverage gaps. What a reader saw was
a landing refused for a reason that named no finding.

THE COST, MEASURED, IN ONE NIGHT
    Nine landings refused with an unreadable reason. Every one of the nine named
    a REAL introduced finding once the truncation was undone — zero false
    positives. One held a PR for three and a half hours over a single line. Four
    were a personal username entering this PUBLIC repository inside an added
    test file, on runs where the `NDA — added content/paths` gate itself
    reported PASS.

    And the second-order cost was worse than the first. A reader inferred from
    the display that the GATE was refusing on sub-checks it could not measure,
    and weakened the gate to match — a fail-open hole in the one instrument that
    had just caught four NDA-shaped leaks. The gate was never broken. The
    display was.

WHY A TEST OVER THE PATTERN RATHER THAN OVER THE SCRIPT
    `run_emit` is a bash function inside a 142 KB script that needs a lane, a
    base revision and a subprocess to reach. What actually failed is one regular
    expression, and a regular expression can be read out of the file and applied
    to the exact line that was missed. This pins the thing that broke.

    This is the SECOND repair of this class here: the 2026-07-30 comment above
    the pattern records a failing gate whose reason was hidden by `tail` alone,
    fixed by printing failing lines first. That fix assumed a failing line looks
    like `FAIL ...`. This one drops the assumption.
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[5]
_SCRIPT = _REPO / "tools" / "gatekeeper-land.sh"

#: The exact line the old pattern could not see, from auto_land.log 2026-09-10 03:17.
_REAL_MISSED_LINE = (
    "landing_hygiene_ratchet_check: FAIL — this landing INTRODUCES "
    "non-atomic report writes:"
)

#: Lines that must NOT be dragged in, so the repair cannot become "print everything".
_MUST_NOT_MATCH = [
    "  PASS  this landing introduces no hygiene finding",
    "landing_hygiene_ratchet_check: PASS — [watchdog compliance] introduces nothing",
    "  [NOT_MEASURED] waveform artifact hygiene",
    "REPORT  issues claimed by more than one commit in this landing",
]


def _emit_pattern() -> str:
    """The pattern `run_emit` actually ships, read out of the script."""
    if not _SCRIPT.is_file():
        pytest.skip(f"NOT_MEASURED: {_SCRIPT} is absent from this checkout")
    for line in _SCRIPT.read_text(errors="replace").splitlines():
        s = line.strip()
        if s.startswith("| grep -aE '") and "FAIL" in s:
            return s[len("| grep -aE '"):s.rindex("'")]
    pytest.fail("the failing-line grep in run_emit could not be located")


def _matches(pattern: str, line: str) -> bool:
    # `grep -E` semantics: unanchored search, POSIX classes spelled the same way.
    return re.search(pattern.replace("[[:space:]]", r"[ \t]"), line) is not None


def test_a_verdict_prefixed_with_its_program_name_is_rendered():
    """THE REGRESSION. This exact line was refused and never shown, nine times."""
    assert _matches(_emit_pattern(), _REAL_MISSED_LINE), (
        "run_emit's pattern cannot see a verdict a program prefixes with its own "
        "name, so a real refusal is rendered with no reason: " + _REAL_MISSED_LINE
    )


def test_the_plain_shape_still_matches():
    """THE CONTROL, one direction. The 2026-07-30 repair must not be undone."""
    pattern = _emit_pattern()
    for line in ("  FAIL  repo hygiene gates", "ERROR: the lane died",
                 "  [FAIL] shipped-path portability", "FAILED (rc 1)"):
        assert _matches(pattern, line), line


@pytest.mark.parametrize("line", _MUST_NOT_MATCH)
def test_a_passing_or_advisory_line_is_not_dragged_in(line):
    """THE CONTROL, the other direction. A repair that matched everything would
    pass the test above and restore the original defect — a window full of noise
    with the verdict lost in it."""
    assert not _matches(_emit_pattern(), line), (
        "the pattern is too wide; this line is not a failure: " + line
    )
