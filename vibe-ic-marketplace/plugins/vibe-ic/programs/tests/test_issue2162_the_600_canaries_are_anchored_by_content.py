"""vibe-ic#2162 — the two #600 canaries are anchored BY CONTENT, both directions.

The canaries themselves run against THIS repo's real history, where the
population is whatever it happens to be. These arms run against SYNTHETIC
histories built by this file, so each direction of the predicate is exercised on
a history whose answer is known by construction rather than by what the repo
currently contains.

WHAT WENT WRONG, AND WHAT THE DIRECTIONS ARE:
  * a SHA anchor died with the pre-v1.0.0 squash, and the canaries skipped
    silently -- so "a commit that produces the artefact IS selected" has to be
    provable, and "the canary cannot go silent" has to be provable too;
  * an ISSUE-NUMBER anchor reaches a CONSUMER_ONLY commit (measured, #2145) --
    so "a commit that merely MENTIONS the artefact is NOT selected" is the other
    direction, and an empty population must be a NAMED refusal, never a skip.

Chip/PDK/tool-AGNOSTIC: nothing here names a design, a PDK or a tool.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(_HERE))
import _issue600_producing_commits as P  # noqa: E402


def _git(root: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, f"git {args}: {cp.stderr}"
    return cp.stdout


#: A stand-in runner: long enough, and shaped so git's own Python funcname
#: detection puts the enclosing `def` into the `@@ … @@` context — which is the
#: signal the predicate reads.
def _runner_source(tail: str = "    return 0\n") -> str:
    body = "".join(f"    x{i} = {i}\n" for i in range(30))
    return (
        '"""a stand-in for the Phase-3 backend runner."""\n\n\n'
        "def _unrelated_helper():\n" + body + "    return None\n\n\n"
        "def step_pnr(project, top):\n" + body + tail
    )


@pytest.fixture()
def repo():
    # `mkdtemp`, not `tmp_path`: under the shipped EDA image the pytest tmp-path
    # root has carried a NEWLINE, which corrupts every argv built from it.
    d = Path(tempfile.mkdtemp(prefix="i2162_"))
    _git(d, "init", "-q", "-b", "main")
    runner = d / P.RUNNER
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(_runner_source())
    (d / "docs").mkdir(exist_ok=True)
    (d / "docs" / "ARCH.md").write_text("prose\n")
    (d / "other_runtime.py").write_text("VALUE = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "seed: the history starts here")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


# ── DIRECTION 1 — a commit that PRODUCES the artefact is selected ───────────

def test_a_commit_that_changes_the_producer_is_selected(repo):
    (repo / P.RUNNER).write_text(_runner_source("    write_def(top)\n"))
    sha = _commit(repo, "pnr: write the DEF")
    pop, why = P.producing_commits(repo)
    assert [s for s, _ in pop] == [sha], (why, pop)
    assert "diff --git" in pop[0][1], "the FULL diff is what the caller gets"


def test_a_neutral_file_alongside_does_not_disqualify(repo):
    """Every real commit here bumps a manifest, regenerates INDEX.md and ships
    tests. If those disqualified, the population would be empty on a live
    history and the canary would be a permanent NOT_MEASURED."""
    (repo / P.RUNNER).write_text(_runner_source("    write_def(top)\n"))
    (repo / "docs" / "ARCH.md").write_text("prose, revised\n")
    (repo / ".claude-plugin").mkdir(exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text('{"v": "0.1"}\n')
    tests = repo / "vibe-ic-marketplace/plugins/vibe-ic/programs/tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_new.py").write_text("def test_x():\n    assert True\n")
    sha = _commit(repo, "pnr: write the DEF, with the mandatory bumps")
    pop, why = P.producing_commits(repo)
    assert [s for s, _ in pop] == [sha], (why, pop)


# ── DIRECTION 2 — a commit that only MENTIONS it is not selected ────────────

def test_a_commit_that_only_mentions_the_artefact_is_not_selected(repo):
    """The #2145 failure mode, in this predicate's terms. Prose about
    `step_pnr` and the DEF is a reference; a reference is not a change to the
    producer, and the refusal must be NAMED."""
    (repo / "docs" / "ARCH.md").write_text(
        "step_pnr writes routed.def and _gds_grid_snap snaps the GDS.\n")
    _commit(repo, "docs: describe step_pnr and the stream-out (#600)")
    pop, why = P.producing_commits(repo)
    assert pop == []
    assert why.startswith("NOT_MEASURED:"), why
    assert "step_pnr" in why and P.RUNNER in why, (
        "the refusal must NAME the predicate it could not satisfy")


def test_a_hunk_outside_any_producer_is_not_selected(repo):
    """A real change to the runner, in a function that produces nothing."""
    src = _runner_source()
    (repo / P.RUNNER).write_text(src.replace("    x0 = 0\n", "    x0 = 99\n", 1))
    _commit(repo, "runner: an unrelated helper")
    pop, why = P.producing_commits(repo)
    assert pop == [] and why.startswith("NOT_MEASURED:"), (pop, why)


def test_a_commit_that_also_changes_another_runtime_file_is_not_selected(repo):
    """CONDITION (1), and the reason the file list is read WITHOUT the pathspec.

    With `-- <RUNNER>` on the read that produces the file list, git restricts
    each diff to that path, this condition can never fail, and the population
    silently triples. MEASURED on the live history while writing this: 108 with
    the pathspec, 37 without it. A commit that also rewrites another runtime
    surface is a different subject and belongs to that subject's own canary."""
    (repo / P.RUNNER).write_text(_runner_source("    write_def(top)\n"))
    (repo / "other_runtime.py").write_text("VALUE = 2\n")
    _commit(repo, "pnr + an unrelated runtime file")
    pop, why = P.producing_commits(repo)
    assert pop == [] and why.startswith("NOT_MEASURED:"), (pop, why)


# ── THE REFUSAL IS NAMED, AND IT IS NOT A SKIP ─────────────────────────────

def test_an_empty_population_is_a_named_NOT_MEASURED(repo):
    pop, why = P.producing_commits(repo)
    assert pop == []
    assert why == P.NOT_MEASURED
    for name in P.PRODUCER_STEPS:
        assert name in why, f"the refusal does not name {name}"
    assert "not a pass" in why, (
        "the refusal must say what it is NOT, or a reader scores it as green")


def test_the_canaries_report_the_refusal_as_a_RED_not_a_skip():
    """`assert pop, why` — not `pytest.skip(why)`. Read off the source of both
    canaries, because the difference between the two spellings is exactly the
    difference between a canary that stopped watching loudly and one that
    stopped watching silently."""
    for fname, test in (
            ("test_v0_3_50_issue602_round2_neutral_exclusion.py",
             "test_real_issue600_commit_is_producer"),
            ("test_v0_3_51_issue603_consumer_in_runner_file.py",
             "test_real_issue600_still_producer")):
        tree = ast.parse((_HERE / fname).read_text())
        fn = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == test]
        assert len(fn) == 1, f"{fname}: {test} is not there once"
        src = ast.unparse(fn[0])
        assert "producing_commits" in src, f"{fname}: not anchored by content"
        assert "assert_population_is_safe_and_producer_is_reachable" in src, (
            f"{fname}: {test} no longer asserts the population's two facts. "
            "MEASURED: with those assertions written inline, deleting one "
            "turned nothing red — the canary runs on the real history, where "
            "no arm can inject a population. The call is what makes them "
            "reachable by a test.")
        assert "pytest.skip" not in src.replace(
            "pytest.skip('not in a git checkout')", ""), (
            f"{fname}: {test} can still skip on the population")
        assert not re.search(r"['\"][0-9a-f]{7,40}['\"]", src), (
            f"{fname}: {test} names a commit by sha again — the anchor that "
            "died with the history rewrite")


# ── THE INSTRUMENT ITSELF ──────────────────────────────────────────────────

def test_every_named_producer_is_a_function_in_the_runner():
    """The set cannot rot into names that match nothing.

    A `PRODUCER_STEPS` naming functions that no longer exist would select an
    empty population while still LOOKING like a population — the shape where a
    never-firing instrument reports clean."""
    tree = ast.parse((_PROGRAMS / "phase3_one_shot_runner.py").read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in P.PRODUCER_STEPS if n not in defined]
    assert not missing, (
        f"{missing} are named as producers but are not functions in "
        "phase3_one_shot_runner.py")
    assert P.PRODUCER_STEPS, "the producer set is empty"


def test_the_selector_never_consults_the_classifier():
    """NON-CIRCULARITY, as a property of the module rather than a promise in its
    docstring. Selecting the population with `classify_diff` and then asserting
    `classify_diff`'s answer is a tautology that passes however the classifier
    is broken."""
    tree = ast.parse((_HERE / "_issue600_producing_commits.py").read_text())
    imported = set()
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add(n.module or "")
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    assert "fix_surface_classify" not in imported, imported
    # NAMES IN THE CODE, not text in the file: this module DESCRIBES the
    # circularity it avoids, at length, and a substring search over the source
    # would fire on its own explanation.
    for token in ("classify_diff", "classify_hunk", "PRODUCER_PATTERNS",
                  "CONSUMER_PATTERNS"):
        assert token not in names, f"the selector consults {token}"


def test_the_record_separator_does_not_occur_in_the_diffs_it_splits():
    """The split is by 0x1e. If that byte appeared in a diff this module reads,
    one commit's diff would be cut in half and the halves scored as two
    commits. Measured against the real history rather than assumed."""
    root = _PROGRAMS.parent.parent.parent.parent
    if not (root / ".git").exists():
        pytest.skip("not in a git checkout")
    pop, why = P.producing_commits(root)
    assert pop, why
    for sha, diff in pop:
        assert P._SEP not in diff, f"{sha} carries the record separator"
        assert re.fullmatch(r"[0-9a-f]{40}", sha), sha


# ── THE CANARIES' OWN ASSERTION, DRIVEN BOTH WAYS ──────────────────────────
#
# vibe-ic#2162, found by mutation: with these two facts asserted INLINE in each
# canary, deleting the CONSUMER_ONLY half turned NOTHING red. The canaries run
# against this repo's real history, so no arm could hand them a population that
# violates it. Moving the assertion into a function makes it drivable.

def test_a_consumer_only_verdict_in_the_population_is_refused():
    with pytest.raises(AssertionError) as exc:
        P.assert_population_is_safe_and_producer_is_reachable(
            {"aa": "PRODUCER", "bb": "CONSUMER_ONLY", "cc": "MIXED"})
    assert "CONSUMER_ONLY" in str(exc.value) and "bb" in str(exc.value)


def test_a_population_with_no_PRODUCER_at_all_is_refused():
    """The degeneration guard: MIXED everywhere satisfies the first fact."""
    with pytest.raises(AssertionError) as exc:
        P.assert_population_is_safe_and_producer_is_reachable(
            {"aa": "MIXED", "bb": "MIXED"})
    assert "unreachable" in str(exc.value)


def test_a_safe_population_with_a_producer_passes():
    """The other direction — or the two arms above would pass on a function
    that refused everything."""
    P.assert_population_is_safe_and_producer_is_reachable(
        {"aa": "MIXED", "bb": "PRODUCER"})
    unsafe, producers = P.population_findings(
        {"aa": "MIXED", "bb": "PRODUCER", "cc": "CONSUMER_ONLY"})
    assert (unsafe, producers) == (["cc"], ["bb"])


def test_the_dead_helper_is_gone():
    """`_qualifies` duplicated the selection and was called by nothing. It was
    found by MUTATING it and watching no arm change colour — the shape where a
    reader believes a rule is enforced by code that never runs."""
    assert not hasattr(P, "_qualifies"), (
        "_issue600_producing_commits._qualifies is back and unreachable; the "
        "selection lives in producing_commits")
