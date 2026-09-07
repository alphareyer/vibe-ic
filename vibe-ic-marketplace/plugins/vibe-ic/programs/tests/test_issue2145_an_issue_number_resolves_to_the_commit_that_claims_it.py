"""vibe-ic#2145 — an issue number names the commit that CLAIMED to fix it.

`fix_surface_classify <issue|sha>` is how the field-agent loop decides whether a
closed fix can be verified artifact-first or has to pay a 40-minute re-run
(`skills/field-agent-loop/SKILL.md`). That decision is only as good as the
commit the issue number resolves to, and `resolve_commit` used to answer it with
"the newest commit whose SUBJECT contains `#N` anywhere, within the last 400
commits". MEASURED on this repo's own history, both halves were wrong:

  * `ebb00b1b4`'s subject reads "... the #599 label probe pinned to a dict's
    LAST key ... (#2111 #2106)". It closes #2111 and #2106 and merely discusses
    #599 — and `resolve_commit("599")` returned it.
  * the real #599 commit, `b81ee38dfecc`, is 3094 commits back, so the honest
    answer was unreachable in the same checkout where the decoy was reachable.

Both are pinned here, on synthetic histories built by this file, so the
assertions do not depend on what this repo's own log happens to contain today.
The two real-git canaries in `test_v0_3_50_issue602_round2_neutral_exclusion.py`
and `test_v0_3_51_issue603_consumer_in_runner_file.py` are the other half of the
pair: they run the same resolver over the LIVE history.

Chip/PDK/tool-AGNOSTIC: nothing here names a design, a PDK or a tool.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import fix_surface_classify as F  # noqa: E402


# `tmp_path` is not used on purpose: under the shipped EDA image the pytest
# tmp-path root has carried a NEWLINE in it, which corrupts every argv built
# from it. `mkdtemp` is the same directory without that hazard.
@pytest.fixture()
def repo():
    d = Path(tempfile.mkdtemp(prefix="i2145_"))
    _git(d, "init", "-q", "-b", "main")
    (d / "seed.txt").write_text("seed\n")
    _git(d, "add", "seed.txt")
    _commit(d, "seed: the history starts here")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _git(root: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, f"git {args}: {cp.stderr}"
    return cp.stdout


def _commit(root: Path, message: str, *, touch: str = "") -> str:
    """One commit whose full message is `message`; `touch` names a file to
    change so the commit carries a real diff."""
    if touch:
        p = root / touch
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text() if p.exists() else "") + message + "\n")
        _git(root, "add", touch)
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


# ── the defect, in its measured shape ───────────────────────────────────────

def test_a_prose_mention_in_a_subject_is_not_a_claim(repo):
    """The exact `ebb00b1b4` shape: a commit that CLOSES two issues and
    DISCUSSES a third must not be returned for the third."""
    real = _commit(repo, "rollup: a verdict word (#599 D1 + step 14) [v1.9.20]",
                   touch="a.py")
    decoy = _commit(
        repo,
        "fix(main-red): four invalid escapes, the #599 label probe pinned to a "
        "dict's LAST key, and the docker stub (#2111 #2106)",
        touch="b.py")

    assert F.resolve_commit("599", repo) == real, (
        "the prose mention in the newer subject won; that is the defect")
    assert F.resolve_commit("599", repo) != decoy
    # and the decoy is still correctly the answer for what it DOES claim
    assert F.resolve_commit("2111", repo) == decoy
    assert F.resolve_commit("2106", repo) == decoy


def test_the_newest_of_several_claims_wins(repo):
    """`resolve_commit`'s contract is the NEWEST claim, and that is unchanged."""
    _commit(repo, "step4-tb: two gates looked in the wrong dir (#599 step 4)")
    newer = _commit(repo, "rollup: no word between PASS and VACUOUS (#599 D1)")
    assert F.resolve_commit("599", repo) == newer


def test_a_closes_trailer_in_the_body_is_a_claim(repo):
    """The other convention this repo writes: the closing trailer."""
    sha = _commit(repo, "fix(programs): the gate read the wrong directory\n"
                        "\nA body paragraph.\n\nCloses #877.\n")
    assert F.resolve_commit("877", repo) == sha


def test_a_trailer_claim_and_a_subject_claim_are_ranked_together(repo):
    """Two claim shapes, ONE ordering. The newest wins whichever shape it is —
    otherwise the answer would depend on which query the resolver ran first."""
    _commit(repo, "older: a subject claim (#901)")
    newer = _commit(repo, "newer: no parenthetical here\n\nCloses #901.\n")
    assert F.resolve_commit("901", repo) == newer

    _commit(repo, "newest: a subject claim again (#901)")
    assert F.resolve_commit("901", repo) != newer


def test_nothing_claims_the_issue_resolves_to_none(repo):
    """DEGRADE LOUDLY. With only a mention in the history the honest answer is
    None — `diff_for` then says "could not resolve", which is the truth. A
    fallback to the newest mention would be the wrong answer, and only the
    wrong answer is actionable."""
    _commit(repo, "chore: mentions #4242 in prose and closes nothing")
    assert F.resolve_commit("4242", repo) is None


def test_the_search_is_not_capped_at_a_recent_window(repo):
    """A claim older than the removed 400-commit cap is still found.

    The cap turned "older than 400 commits" into "not in this history", which is
    "could not read it" reported as "it is not there" — and it is exactly why
    the two real-git #599 canaries had never been exercised."""
    old = _commit(repo, "ancient: the real fix (#606)")
    for i in range(420):
        _commit(repo, f"filler {i}: nothing to see")
    assert F.resolve_commit("606", repo) == old


# ── boundaries ──────────────────────────────────────────────────────────────

def test_an_issue_number_is_not_matched_as_a_prefix(repo):
    """`#5990` is not `#599`."""
    _commit(repo, "unrelated: a different issue entirely (#5990)")
    assert F.resolve_commit("599", repo) is None


def test_the_rightmost_group_is_the_claim_not_the_first(repo):
    """`fix(#2014 D1): ... (#987)` claims 987. The conventional-commit scope in
    the FIRST parentheses is a scope, not an issue list."""
    sha = _commit(repo, "fix(#2014 D1): the hand-off is a stated wait (#987)")
    assert F.resolve_commit("987", repo) == sha
    assert F.resolve_commit("2014", repo) is None


def test_a_sha_argument_is_returned_verbatim(repo):
    """Unchanged behaviour: a hex arg is a sha, not an issue number."""
    assert F.resolve_commit("6a73bad1", repo) == "6a73bad1"
    assert F.resolve_commit("not-a-ref", repo) is None


def test_the_resolved_commit_is_the_one_that_gets_classified(repo):
    """END TO END, through the CLI seam the field agent actually calls.

    `diff_for` composes `resolve_commit` with `git show`, and this is the
    property the whole change exists for: the diff handed to `classify_diff` is
    the CLAIMED commit's, not a mentioner's. The two carry different verdicts
    here, so a regression cannot hide behind a coincidence."""
    _commit(repo, "the real one: a verdict string (#733)",
            touch="programs/foo_check.py")
    _commit(repo, "a later commit that only mentions #733 in prose (#999)",
            touch="programs/phase3_one_shot_runner.py")
    diff, sha = F.diff_for("733", repo)
    assert diff is not None, sha
    assert "programs/foo_check.py" in diff
    assert "phase3_one_shot_runner.py" not in diff
