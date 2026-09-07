"""vibe-ic#2162 — the population the two #600 canaries are about, BY CONTENT.

WHY THIS EXISTS. `test_v0_3_50_issue602_round2_neutral_exclusion.py::
test_real_issue600_commit_is_producer` and its twin in the #603 file both
HARDCODED the sha `6a73bad1`. That sha is gone — the pre-`v1.0.0` history was
squashed into one "initial public release" commit — so both canaries had been
SKIPPING on every checkout, silently, for as long as that squash has existed.
A canary that stopped watching and said nothing is the failure this module
removes ("not in the FAILED list" is not "green").

THE OBVIOUS REPAIR IS WRONG, AND IT WAS MEASURED (vibe-ic#2145, #2162):
resolving `"600"` through `fix_surface_classify.resolve_commit` reaches
`311f8fc81bcf` ("cascade: a documented KNOWN GAP was hidden behind an unrelated
waiver (#600)"), whose verdict is CONSUMER_ONLY. Rewiring the canaries to the
ISSUE NUMBER would turn a silent skip into a red that says nothing about the
classifier. An issue number is no more durable than a sha here; both name a
commit, and the canary is not about a commit — it is about a KIND of commit.

THE PREDICATE, therefore, is over the DIFF, so a history rewrite cannot
invalidate it and a prose mention cannot satisfy it. A commit is in the
population when BOTH hold:

  (1) the only RUNTIME file it changes is the Phase-3 backend runner. Version
      manifests, INDEX.md, prose `.md` and `programs/tests/**` are not runtime
      surfaces and are ignored — a commit that also rewrites another producer
      is a different subject and is left to that subject's own canary.
  (2) at least one of its hunks IN THAT FILE is enclosed by one of the
      geometry / stream-out producer functions named in `PRODUCER_STEPS` —
      the functions that WRITE the DEF and the GDS.

(2) is what "produces, not mentions" means operationally: a hunk's enclosing
symbol comes from git's own `@@ … @@` context, i.e. the function the changed
lines are INSIDE, which prose about the artefact can never be.

NOT CIRCULAR, DELIBERATELY. `PRODUCER_STEPS` is spelled HERE and is NOT imported
from `fix_surface_classify.PRODUCER_PATTERNS`, and nothing in the selection
consults `classify_diff`, its consumer vocabulary or its verdict. Selecting with
the classifier and then asserting the classifier's answer would be a tautology
that passes however the classifier is broken.

DEGRADES LOUDLY. An empty population is `NOT_MEASURED` naming the predicate, and
the callers report it as a RED — never a skip. A zero denominator is not a pass
(the rule this repo already spells as `gate_zero_denominator_refuses_check`).

Chip/PDK/tool-AGNOSTIC: nothing here names a design, a PDK or a tool.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

#: The Phase-3 backend runner, relative to the repo root.
RUNNER = ("vibe-ic-marketplace/plugins/vibe-ic/programs/"
          "phase3_one_shot_runner.py")

#: The geometry / stream-out producers, DERIVED FROM THE TREE rather than
#: invented: every name here is asserted to be a real `def` in `RUNNER` by
#: `test_issue2162_...::test_every_named_producer_is_a_function_in_the_runner`.
#: A hand-written list is how this set would quietly come to name nothing and
#: shrink the population to zero while still reporting a population.
PRODUCER_STEPS: Tuple[str, ...] = (
    "step_pnr", "step_gds", "_build_pnr_tcl_text", "_gds_grid_snap",
    "_magic_def_to_gds", "_klayout_merge_layers",
)

#: A file that carries no runtime surface: version manifests, the generated
#: index, prose, and the tests themselves (a test is the author's EVIDENCE, not
#: a surface — the same rule #602 established for the classifier).
_NEUTRAL_RE = re.compile(r"(\.md$|\.json$|/tests/)")

_FILE_RE = re.compile(r"^\+\+\+ b/(.*)$")
_HUNK_RE = re.compile(r"^@@[^@]*@@\s*(.*)$")
#: RECORD SEPARATOR. Not NUL: a NUL cannot be passed in an argv at all
#: (`ValueError: embedded null byte`), and 0x1e does not occur in the
#: source this history carries — `test_issue2162_…::
#: test_the_record_separator_does_not_occur_in_the_diffs_it_splits`
#: measures that rather than assuming it.
_SEP = "\x1e"

NOT_MEASURED = (
    "NOT_MEASURED: no commit in this history satisfies the predicate "
    "\"changes ONLY %s among runtime files AND has a hunk in it enclosed by "
    "one of %s\". This is not a pass: the canary could not be exercised, and "
    "the reason is named rather than skipped over. A shallow clone is the "
    "usual cause — this canary needs the runner's real history."
) % (RUNNER, ", ".join(PRODUCER_STEPS))


def _hunk_is_enclosed_by_a_producer(context: str) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", context)
               for name in PRODUCER_STEPS)


def _split_log(text: str) -> List[Tuple[str, str]]:
    """`[(sha, diff), …]`, newest first, from one `git log --format=%x00%H -p`."""
    out: List[Tuple[str, str]] = []
    for chunk in text.split(_SEP):
        if not chunk.strip():
            continue
        head, _, body = chunk.partition("\n")
        sha = head.strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            out.append((sha, body))
    return out


def _git(repo_root: Path, *args: str) -> Tuple[int, str, str]:
    """`errors="replace"` because a FULL history diff is not valid UTF-8: this
    repo's own history carries bytes that are not (measured: 0xd3 at offset
    74643985 of one such read). A decode crash there would be an unreadable
    population reported as an exception rather than as a verdict."""
    try:
        cp = subprocess.run(["git", "-C", str(repo_root), *args],
                            capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", f"git could not be run: {type(exc).__name__}: {exc}"
    dec = lambda b: (b or b"").decode("utf-8", errors="replace")  # noqa: E731
    return cp.returncode, dec(cp.stdout), dec(cp.stderr)


def _files_and_hunks(repo_root: Path, shas: List[str]
                     ) -> Tuple[dict, dict, str]:
    """`({sha: [paths]}, {sha: [hunk contexts in RUNNER]}, why_not)`.

    TWO SMALL READS RATHER THAN ONE HUGE ONE. The full diffs of 406 commits are
    hundreds of megabytes and only two facts are needed to SELECT: which files
    each commit touched, and what encloses its hunks in the runner. The second
    read carries the `-- <RUNNER>` pathspec ON PURPOSE — restricting to the
    runner is exactly right for reading ITS hunk contexts, and exactly wrong for
    the file list, which is why they are two reads. MEASURED while writing this:
    using one path-restricted read for BOTH makes condition (1) unfalsifiable
    and the population goes 37 -> 108."""
    rc, out, err = _git(repo_root, "log", "--no-walk", f"--format={_SEP}%H",
                        "--name-only", "--no-color", *shas)
    if rc != 0:
        return {}, {}, f"NOT_MEASURED: git log --name-only failed: {err.strip()[:200]}"
    files = {sha: [ln.strip() for ln in body.splitlines() if ln.strip()]
             for sha, body in _split_log(out)}
    rc, out, err = _git(repo_root, "log", "--no-walk", f"--format={_SEP}%H",
                        "-p", "--no-color", *shas, "--", RUNNER)
    if rc != 0:
        return {}, {}, f"NOT_MEASURED: git log -p failed: {err.strip()[:200]}"
    hunks = {}
    for sha, body in _split_log(out):
        hunks[sha] = [m.group(1) for m in
                      (_HUNK_RE.match(ln) for ln in body.splitlines()) if m]
    return files, hunks, ""


def producing_commits(repo_root: Path) -> Tuple[List[Tuple[str, str]], str]:
    """`(population, why_not)` — every commit satisfying the predicate, newest
    first, each as `(sha, FULL diff)`.

    `--no-merges` is EXPLICIT AND INERT, and both halves are stated because a
    comment implying a guard that nothing can redden is worse than no comment.
    Inert: a merge's `-p` output is empty by default, so a merge could never
    qualify whether or not the flag is there — MEASURED, by removing the flag
    and finding no arm of this file's test suite changes colour. Explicit
    anyway: this repo lands by merge (411 commits touch the runner here, 406
    non-merges and 5 merges), each merge's content is carried by the non-merge
    commit it brought in, and the day someone adds `-m` or `--first-parent` to
    this read the flag is what keeps merges from being counted twice.

    The whole history is read — no recent-N window, because a window turns
    "older than N" into "not in this history", the substitution vibe-ic#2145
    was about. The FULL diff is fetched only for the commits that SURVIVE
    selection (tens, not hundreds), so nothing here holds a whole history's
    diffs in memory.
    """
    rc, out, err = _git(repo_root, "log", "--format=%H", "--no-merges",
                        "--", RUNNER)
    if rc != 0:
        return [], f"NOT_MEASURED: git log failed: {err.strip()[:200]}"
    shas = out.split()
    if not shas:
        return [], NOT_MEASURED
    files, hunks, why = _files_and_hunks(repo_root, shas)
    if why:
        return [], why
    pop: List[Tuple[str, str]] = []
    for sha in shas:                      # newest first, git log's own order
        runtime = {f for f in files.get(sha, ()) if not _NEUTRAL_RE.search(f)}
        if runtime != {RUNNER}:
            continue
        if not any(_hunk_is_enclosed_by_a_producer(c)
                   for c in hunks.get(sha, ())):
            continue
        rc, diff, err = _git(repo_root, "show", sha, "--format=", "--unified=3")
        if rc != 0 or not diff.strip():
            continue
        pop.append((sha, diff))
    return (pop, "") if pop else ([], NOT_MEASURED)


# ---------------------------------------------------------------------------
# WHAT THE CANARIES ASSERT, in one place so it can be DRIVEN
# ---------------------------------------------------------------------------
#
# MEASURED (vibe-ic#2162): with these two assertions written inline in each
# canary, deleting the CONSUMER_ONLY half from one of them turned NOTHING red —
# the canaries run against this repo's real history, where no synthetic
# population can be injected, so no arm could reach them. Here they take the
# verdict map as an argument, so `test_issue2162_…` drives both directions on a
# population it builds itself, and the AST guard in that file asserts both
# canaries still CALL this.


def population_findings(verdicts: dict) -> Tuple[List[str], List[str]]:
    """`(unsafe, producers)` — the two facts the #600 canaries rest on.

    `verdicts` is `{sha: verdict}`. `unsafe` is every commit classified
    CONSUMER_ONLY: that verdict means "artifact-first, no re-run", and saying it
    about a commit that moved the geometry is the one answer that ships an
    unverified producer fix. MIXED and PRODUCER both re-run and are both safe,
    which is why the property is "none is CONSUMER_ONLY" and not
    "== PRODUCER" — the latter is simply FALSE of this history's newest
    qualifying commit, which is MIXED because it also edits a verdict string.
    """
    unsafe = sorted(s for s, v in verdicts.items() if v == "CONSUMER_ONLY")
    producers = sorted(s for s, v in verdicts.items() if v == "PRODUCER")
    return unsafe, producers


def assert_population_is_safe_and_producer_is_reachable(verdicts: dict) -> None:
    """Raise `AssertionError` unless BOTH hold. See `population_findings`.

    The second half is not decoration: without it a classifier that answered
    MIXED to everything would satisfy the first while being useless — the
    degeneration this repo has already measured once ("over the 200 most recent
    origin/main commits ... PRODUCER 0 times", skills/field-agent-loop/SKILL.md).
    """
    unsafe, producers = population_findings(verdicts)
    assert not unsafe, (
        f"{len(unsafe)} of {len(verdicts)} commits that change the geometry / "
        f"stream-out producer classify CONSUMER_ONLY, i.e. would be verified "
        f"artifact-first with no re-run: {unsafe[:5]}")
    assert producers, (
        f"not one of the {len(verdicts)} commits that change the geometry / "
        f"stream-out producer classifies PRODUCER; the verdict is unreachable "
        f"on this history: {sorted(set(verdicts.values()))}")
