#!/usr/bin/env python3
"""vibe-ic#2174 — a count is only comparable to a floor over the same population.

`silent_decline_audit.py` subtracts a measured count from a recorded one. That
subtraction is meaningful only when both numbers describe the same population,
and nothing checked that they did.

MEASURED on `main` a7707989e (tree 59e65da55), on files the reporting lane had
NOT touched::

    $ python3 programs/silent_decline_audit.py programs/lec_run.py --ratchet
    files scanned : 1
    silent declines: 2
    [PASS] 15 -> 2; lower the baseline ...                              rc 0

    $ python3 programs/silent_decline_audit.py programs/flow_compliance_check.py
    files scanned : 1
    silent declines: 0
    [PASS] 15 -> 0; lower the baseline ...                              rc 0

Two defects that compound.

THE ARITHMETIC. 15 is a corpus-wide total and the largest single file in the
tree holds 6 of it, so a single file cannot exceed the floor by construction.
Measured over the wired population at that commit: **1395 of 1395 files**
produce the false improvement, the complement empty. "This subset is below the
whole-tree floor" is a property of subsets, not of the tree.

THE INVITATION. `lower the baseline` is an instruction to run the one flag the
campaign forbids, and a gate that asks for `--write-baseline` is a FINDING and
not an instruction. Acting on it would ratchet a whole-tree floor down to a
number derived from one file — a defect converted into a standing waiver, with
every later comparison read against a floor nobody measured.

The tests below take BOTH directions, because either alone is satisfiable by
the wrong program:

  * a subset must refuse (a program that never refuses fails this);
  * the shipped whole-tree invocation must compare exactly as it did, and a
    scan at least as large as the record's population must still FAIL on
    growth (a program that refuses everything fails these — that is the
    "does not blind the gate" half);
  * and the mutation: restoring the subset-versus-whole comparison in the REAL
    shipped source brings the false invitation back, which is what makes the
    guard load-bearing rather than decorative.

Nothing here writes, re-derives or lowers a baseline. The shipped floor of 15
is used only as a number to compare against.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROGRAMS = Path(__file__).resolve().parent.parent
AUDIT = PROGRAMS / "silent_decline_audit.py"
SHIPPED_BASELINE = PROGRAMS / "silent_decline_baseline.json"

# The exact shape #313 §6 defines: a remedy-semantic call whose `is not None`
# guard has no else, so the refusal is recorded nowhere.
_ONE_SILENT_DECLINE = """\
def loosen_die(x):
    return None


def run(x):
    lf = loosen_die(x)
    if lf is not None:
        apply_it(lf)
"""


def _run(*argv: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("VIBE_IC_BENCHMARK_DATA", None)
    env.pop("GATEKEEPER_BENCHMARK_DATA_SHA", None)
    return subprocess.run([sys.executable, *argv], capture_output=True,
                          text=True, check=False, env=env)


def _tree(root: Path, n_files: int, n_declining: int) -> Path:
    """`n_files` modules, the first `n_declining` of them carrying one decline."""
    src = root / "src"
    src.mkdir(parents=True)
    for i in range(n_files):
        body = _ONE_SILENT_DECLINE if i < n_declining else "X = 1\n"
        (src / f"m{i}.py").write_text(body, encoding="utf-8")
    return src


def _record(path: Path, count: int, scanned: int | None) -> Path:
    d: dict = {"count": count}
    if scanned is not None:
        d["scanned"] = scanned
    path.write_text(json.dumps(d), encoding="utf-8")
    return path


# ───────────────────────────────────────────────────────────────────────────
# 1. THE DEFECT. A subset is not judged against a whole-population floor.
# ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ratchet_flag", ([], ["--ratchet"]))
def test_a_subset_is_not_judged_against_a_whole_population_floor(
        tmp_path: Path, ratchet_flag: list) -> None:
    """RED before the fix: rc 0 and `[PASS] 15 -> 1`.

    Parametrised over the flag because the comparison is unconditional
    (vibe-ic#1705); asserting only the flagged form is what let a default run
    keep passing once before.
    """
    src = _tree(tmp_path, n_files=1, n_declining=1)
    bl = _record(tmp_path / "bl.json", count=15, scanned=1091)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), *ratchet_flag)
    transcript = got.stdout + got.stderr

    assert got.returncode == 2, transcript
    assert "NOT COMPARABLE" in transcript, transcript
    assert "[PASS]" not in transcript and "[FAIL]" not in transcript, transcript
    # The populations are both named, so the refusal is auditable.
    assert "1 file(s)" in transcript and "1091 file(s)" in transcript, transcript
    # The findings are still reported. NOT COMPARABLE must not also mean SILENT.
    assert "silent declines: 1" in transcript, transcript
    assert "loosen_die" in transcript, transcript


def test_a_subset_with_no_finding_at_all_is_refused_the_same_way(
        tmp_path: Path) -> None:
    """The loudest form of the defect: a file with ZERO declines read as
    `15 -> 0`, an entire corpus of debt paid off by one clean file."""
    src = _tree(tmp_path, n_files=1, n_declining=0)
    bl = _record(tmp_path / "bl.json", count=15, scanned=1091)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), "--ratchet")
    transcript = got.stdout + got.stderr

    assert got.returncode == 2, transcript
    assert "NOT COMPARABLE" in transcript, transcript
    assert "[PASS]" not in transcript, transcript


def test_write_baseline_refuses_to_overwrite_a_wider_record_from_a_subset(
        tmp_path: Path) -> None:
    """The trapdoor the invitation opened. Removing the words while leaving
    `--write-baseline` able to ratchet a whole-tree floor down to one file's
    number is half a fix, so the write is refused and the record untouched."""
    src = _tree(tmp_path, n_files=1, n_declining=1)
    bl = tmp_path / "bl.json"
    before = _record(bl, count=15, scanned=1091).read_text(encoding="utf-8")

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), "--write-baseline")
    transcript = got.stdout + got.stderr

    assert got.returncode == 2, transcript
    assert "NOT COMPARABLE" in transcript, transcript
    assert bl.read_text(encoding="utf-8") == before, "the record was rewritten"


# ───────────────────────────────────────────────────────────────────────────
# 2. THE FIX DOES NOT BLIND THE GATE. Each of these is satisfiable only by a
#    program that still compares — an unconditional refusal fails all three.
# ───────────────────────────────────────────────────────────────────────────

def test_the_shipped_whole_tree_invocation_still_compares(tmp_path: Path) -> None:
    """The wired gate is `silent_decline_audit.py programs --ratchet`
    (tools/ci/repo_hygiene_gates.sh). It must reach a real verdict, not the
    refusal, and its population must cover the one the record describes."""
    plugin = PROGRAMS.parent
    got = subprocess.run(
        [sys.executable, "programs/silent_decline_audit.py", "programs",
         "--ratchet"],
        cwd=plugin, capture_output=True, text=True, check=False)
    transcript = got.stdout + got.stderr

    assert got.returncode == 0, transcript
    assert "no NEW silent remedy decline" in transcript, transcript
    assert "NOT COMPARABLE" not in transcript, transcript

    recorded = json.loads(SHIPPED_BASELINE.read_text(encoding="utf-8"))
    scanned = int(transcript.split("files scanned : ")[1].split("\n")[0])
    assert scanned >= recorded["scanned"], (
        f"the wired run scans {scanned} files and the record was taken over "
        f"{recorded['scanned']}: the gate would now refuse its own invocation")


@pytest.mark.parametrize("ratchet_flag", ([], ["--ratchet"]))
def test_growth_over_the_recorded_population_is_still_a_fail(
        tmp_path: Path, ratchet_flag: list) -> None:
    src = _tree(tmp_path, n_files=3, n_declining=3)
    bl = _record(tmp_path / "bl.json", count=1, scanned=3)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), *ratchet_flag)
    transcript = got.stdout + got.stderr

    assert got.returncode == 1, transcript
    assert "GREW 1 -> 3" in transcript, transcript
    assert "NOT COMPARABLE" not in transcript, transcript


def test_a_population_larger_than_the_record_still_compares_and_passes(
        tmp_path: Path) -> None:
    """At or below the floor over a LARGER population is a stronger statement
    than the record makes, so it is a genuine PASS and not a refusal."""
    src = _tree(tmp_path, n_files=5, n_declining=1)
    bl = _record(tmp_path / "bl.json", count=2, scanned=3)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), "--ratchet")
    transcript = got.stdout + got.stderr

    assert got.returncode == 0, transcript
    assert "[PASS]" in transcript, transcript
    assert "NOT COMPARABLE" not in transcript, transcript


def test_a_record_that_states_no_population_is_compared_as_before(
        tmp_path: Path) -> None:
    """`{"count": 0}` claims nothing about how many files it was taken over, so
    it cannot be contradicted by one. vibe-ic#1705's teeth are unchanged: the
    first decline against an explicit zero is still NEW."""
    src = _tree(tmp_path, n_files=1, n_declining=1)
    bl = _record(tmp_path / "bl.json", count=0, scanned=None)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), "--ratchet")
    transcript = got.stdout + got.stderr

    assert got.returncode == 1, transcript
    assert "GREW 0 -> 1" in transcript, transcript
    assert "NOT COMPARABLE" not in transcript, transcript


# ───────────────────────────────────────────────────────────────────────────
# 3. THE INVITATION IS GONE, on every branch that can print.
# ───────────────────────────────────────────────────────────────────────────

def _transcripts(tmp_path: Path) -> dict:
    """One transcript per reachable verdict branch of the program."""
    out = {}
    below = _tree(tmp_path / "a", 3, 1)
    out["below"] = _run(str(AUDIT), str(below), "--baseline",
                        str(_record(tmp_path / "a.json", 9, 3)), "--ratchet")
    equal = _tree(tmp_path / "b", 3, 1)
    out["equal"] = _run(str(AUDIT), str(equal), "--baseline",
                        str(_record(tmp_path / "b.json", 1, 3)), "--ratchet")
    grew = _tree(tmp_path / "c", 3, 3)
    out["grew"] = _run(str(AUDIT), str(grew), "--baseline",
                       str(_record(tmp_path / "c.json", 1, 3)), "--ratchet")
    absent = _tree(tmp_path / "d", 1, 1)
    out["not_checked"] = _run(str(AUDIT), str(absent), "--baseline",
                              str(tmp_path / "never-written.json"), "--ratchet")
    subset = _tree(tmp_path / "e", 1, 1)
    out["not_comparable"] = _run(str(AUDIT), str(subset), "--baseline",
                                 str(_record(tmp_path / "e.json", 15, 1091)),
                                 "--ratchet")
    return out


def test_no_branch_of_this_gate_asks_for_a_baseline_to_be_written_or_lowered(
        tmp_path: Path) -> None:
    """A gate reports what it measured. Whether a floor moves is a ruling with
    evidence behind it, not a line of output — and the standing rule is that a
    gate asking for `--write-baseline` is a FINDING, not an instruction."""
    got = _transcripts(tmp_path)
    # Every branch was actually reached, so this is not a vacuous sweep.
    assert got["below"].returncode == 0 and "[PASS]" in got["below"].stdout
    assert got["equal"].returncode == 0 and "[PASS]" in got["equal"].stdout
    assert got["grew"].returncode == 1 and "[FAIL]" in got["grew"].stdout
    assert got["not_checked"].returncode == 2
    assert "NOT CHECKED" in got["not_checked"].stdout
    assert got["not_comparable"].returncode == 2
    assert "NOT COMPARABLE" in got["not_comparable"].stdout

    for name, proc in got.items():
        transcript = proc.stdout + proc.stderr
        assert "lower the baseline" not in transcript, f"{name}: {transcript}"
        assert "--write-baseline" not in transcript, f"{name}: {transcript}"


def test_the_below_floor_branch_still_reports_what_it_measured(
        tmp_path: Path) -> None:
    """Removing the instruction must not remove the information. A ruling
    needs the numbers, so the count, the population and the floor all stay."""
    src = _tree(tmp_path, n_files=3, n_declining=1)
    bl = _record(tmp_path / "bl.json", count=9, scanned=3)

    got = _run(str(AUDIT), str(src), "--baseline", str(bl), "--ratchet")
    transcript = got.stdout + got.stderr

    assert got.returncode == 0, transcript
    assert "[PASS]" in transcript, transcript
    for fact in ("1 silent remedy decline", "3 file(s)", "recorded 9"):
        assert fact in transcript, f"{fact!r} missing: {transcript}"


# ───────────────────────────────────────────────────────────────────────────
# 4. THE MUTATION. The guard is load-bearing, proved on the REAL source.
# ───────────────────────────────────────────────────────────────────────────

_GUARD = 'if _population_shortfall(rep["scanned"], base_pop):'


def test_a_mutation_restoring_the_subset_versus_whole_comparison_goes_red(
        tmp_path: Path) -> None:
    """Mutate the SHIPPED file, not a same-shape copy of it.

    Deleting the population guard restores exactly the pre-fix comparison, and
    the mutant must reproduce the defect verbatim — rc 0 and the false
    improvement. If it does not, this file's first test is passing for some
    reason other than the guard, and the guard is decoration.
    """
    src_text = AUDIT.read_text(encoding="utf-8")
    assert src_text.count(_GUARD) == 1, "the guard's anchor moved"
    mutant = tmp_path / "silent_decline_audit_mutant.py"
    mutant.write_text(src_text.replace(_GUARD, "if False:"), encoding="utf-8")

    tree = _tree(tmp_path, n_files=1, n_declining=1)
    bl = _record(tmp_path / "bl.json", count=15, scanned=1091)

    shipped = _run(str(AUDIT), str(tree), "--baseline", str(bl), "--ratchet")
    mutated = _run(str(mutant), str(tree), "--baseline", str(bl), "--ratchet")

    assert shipped.returncode == 2, shipped.stdout + shipped.stderr
    assert "NOT COMPARABLE" in shipped.stdout

    assert mutated.returncode == 0, mutated.stdout + mutated.stderr
    assert "[PASS]" in mutated.stdout, mutated.stdout
    assert "NOT COMPARABLE" not in mutated.stdout, mutated.stdout


def test_the_mutant_of_the_write_guard_overwrites_the_wider_record(
        tmp_path: Path) -> None:
    """The same proof for the second half of the fix: with its guard removed,
    a one-file scan rewrites a floor measured over 1091."""
    src_text = AUDIT.read_text(encoding="utf-8")
    anchor = ('if _population_shortfall(rep["scanned"], '
              "_load_baseline_population(bl)):")
    assert src_text.count(anchor) == 1, "the write guard's anchor moved"
    mutant = tmp_path / "silent_decline_audit_write_mutant.py"
    mutant.write_text(src_text.replace(anchor, "if False:"), encoding="utf-8")

    tree = _tree(tmp_path, n_files=1, n_declining=1)
    bl = tmp_path / "bl.json"
    before = _record(bl, count=15, scanned=1091).read_text(encoding="utf-8")

    got = _run(str(mutant), str(tree), "--baseline", str(bl),
               "--write-baseline")

    assert got.returncode == 0, got.stdout + got.stderr
    after = json.loads(bl.read_text(encoding="utf-8"))
    assert after["count"] == 1 and after["scanned"] == 1, after
    assert bl.read_text(encoding="utf-8") != before
