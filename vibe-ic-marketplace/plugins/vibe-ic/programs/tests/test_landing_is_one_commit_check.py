"""The batch arm must accept the message shape the lander actually writes.

MEASURED 2026-09-10 over the last 300 commits of `main`:

    [v1.2.3]   matched   0
    (v1.2.3)   matched  81

`_VERSION_RE` demanded the bracket form. `land_seq.sh` appends ` (vX.Y.Z)` to
every landing subject, so the batch arm was asking for a format this repository
abandoned. A legitimate two-commit batch whose tip read `... (v1.20.64)` was
refused with `batch of 2 carries 0 version-tagged commit(s) []`, and re-wording
the same tip to `[v1.20.64]` made it pass.

A rule no lane can satisfy is not a standard, it is a wall — and this one was
invisible for months because a one-commit landing never reaches the batch arm at
all. The same blindness reached history mode: `--limit 600` reported "every
landing is a single squashed commit" over a window that contained a manifest-only
commit sitting on an unversioned one.

The repair WIDENS, it does not move: the historical bracket form keeps working,
and a subject carrying no version at all is still refused.
"""
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import landing_is_one_commit_check as C  # noqa: E402


def test_the_tip_form_the_lander_actually_writes_is_accepted():
    """THE REGRESSION. Every landing this repo has made in months looks like this."""
    assert C._VERSION_RE.search(
        "fix(landing): a verdict prefixed with its own name is still a verdict (v1.20.68)")


def test_the_historical_bracket_form_still_works():
    """THE CONTROL, one direction. This widens the rule; it must not move it."""
    assert C._VERSION_RE.search("release: land frozen red closure as [v1.17.18]")


def test_a_subject_with_no_version_is_still_refused():
    """THE CONTROL, the other direction. A repair that matched everything would
    pass the two tests above and delete the arm's meaning — which is the shape
    the original defect already had, from the opposite side."""
    assert not C._VERSION_RE.search(
        "prepare(ci): authorise a move of one protected path")
    assert not C._VERSION_RE.search("fix: a version-shaped thing v1.2.3 with no brackets")
