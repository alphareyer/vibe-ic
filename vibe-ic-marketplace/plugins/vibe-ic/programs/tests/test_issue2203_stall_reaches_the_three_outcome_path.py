#!/usr/bin/env python3
"""A stall must reach the stamp as UNDETERMINED — and only a stall.

WHAT WENT WRONG
===============
Lane ``czstamp`` ran the whole lander inside the pinned digest on a host at
load 110.08. ``full:census-freshness`` died at::

    test_flow_matrix_coverage.py:1974
      assert proc.returncode in (0, 1)     WATCHDOG_STALLED, 60.098 s

and the roll-up printed **"the published census does not re-derive on this
tree"** — a sentence about the TREE, produced by a fact about the MACHINE.

``census_freshness_emit`` (``tools/gatekeeper-land.sh``) already had the right
shape: rc 0 PASS, rc 1 FAIL (stale), anything else REPORT — "the census could
not be measured here; NOT a pass". The undetermined path was simply never
reached. The nested driver ``programs/pytest_per_file_junit.py`` returns
``RC_NORECORD`` (2) for a stall, ``_run_one_module_outcome`` refuses that rc
with an AssertionError, and an uncaught AssertionError leaves ``main`` as
interpreter rc **1** — the one code the lander reads as a finding about the
commit. Being killed and being red were indistinguishable in an exit code.

MEASURED, before the fix, on ``origin/main`` at ``0415905c06`` (8HD-8, in the
pinned digest): a module that stops advancing makes the driver print
``WATCHDOG_STALLED`` and ``aggregate INCOMPLETE rc=199``, and that
AssertionError carried into the generator's own ``--check`` exits **1**.

WHAT THIS FILE LOCKS — BOTH DIRECTIONS
======================================
The failure mode of a fix like this is the relabel: turn every refusal into
UNDETERMINED and every board goes green while the gate stops meaning anything.
So the two directions are asserted side by side, from one probe:

1. a STALLED inner driver exits **2**, and says NOT_MEASURED with the host
   condition named (the marker the lander's REPORT branch already greps);
2. a genuinely STALE census still exits **1** and still says so; and a
   NON-stall refusal of the same outcome run still exits **1** as well.

(2) is not incidental. A stale census never travels as an exception at all —
it is the ordinary rc-1 RETURN of a run that finished and compared two blocks
— so the classifier cannot reach it. This file proves that rather than
asserting it.

Run::

    cd .../plugins/vibe-ic && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
      python3 -m pytest \\
      programs/tests/test_issue2203_stall_reaches_the_three_outcome_path.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

from _plugin_tree import repo_path_or_missing

GEN = repo_path_or_missing("tools", "gen_flow_matrix_census.py")

BEGIN = ("<!-- BEGIN GENERATED CENSUS — tools/gen_flow_matrix_census.py — "
         "DO NOT EDIT BY HAND -->")
END = "<!-- END GENERATED CENSUS -->"

#: Bound for one probe launch. Same reasoning as
#: `test_flow_matrix_census_freshness._CLI_TIMEOUT_S`: the harness runs pytest
#: at `--timeout=180`, so any ONE blocking call must stay at or under 180//3.
#: These launches compute a SYNTHETIC census and finish in about a second.
_CLI_TIMEOUT_S = 60

#: The verbatim tail of a driver log for a run its own stall watchdog killed —
#: MEASURED, not composed, by running `_run_one_module_outcome` against a
#: module that stops advancing. The two markers and the `driver rc=2` header
#: are exactly what the classifier keys on, so a change to either side of that
#: contract fails here rather than in a landing an hour later.
_STALL_ASSERTION = (
    "the outcome run for test_hangs.py produced no complete semantic pytest "
    "lifecycle record (driver rc=2, 1-way concurrent). Output and CPU "
    "activity are not progress; only validated collection/test transitions "
    "renew the stall window.\n"
    "=== [aggregate] 1 file(s) in one pytest process\n"
    ".\n"
    "WATCHDOG_STALLED: configured forward-progress signals did not advance "
    "for > 5s — killed as hung, not slow. watched=log "
    "since_last_progress_s=5.001 elapsed_s=6.251\n"
    "AGGREGATE_NORECORD  STALLED after 5 s with no validated pytest lifecycle "
    "progress — cross-file/order semantics are UNKNOWN, not clean\n"
    "  aggregate  INCOMPLETE rc=199 cases=0 red=0\n"
)

#: The SAME refusal with the stall taken out of it: the outcome run failed for
#: one of the other reasons the one assertion covers. It must keep exiting 1.
_NON_STALL_ASSERTION = (
    _STALL_ASSERTION
    .replace("WATCHDOG_STALLED", "NO_MANIFEST_WRITTEN")
    .replace("AGGREGATE_NORECORD  STALLED", "AGGREGATE_NORECORD  other")
)

#: The generator, driven over a SYNTHETIC census through `sys.modules` — the
#: interception `test_flow_matrix_census_freshness._CLI_PROBE` already uses, so
#: a full 504-cell outcome run is not paid for here. `raise` mode makes the
#: coverage seam throw the text handed to it, which is how the inner driver's
#: refusal reaches the generator in the real run.
_PROBE = r'''
import runpy
import sys
import types

gen_path, out_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
payload = sys.argv[4] if len(sys.argv) > 4 else ""


class _Verdict:
    def __init__(self, label):
        self.label = label


def _dims():
    from flow_matrix.cells import DIMENSIONS
    return DIMENSIONS


def enforcement_census():
    if payload:
        raise AssertionError(payload)
    return {(step, dim): _Verdict(label)
            for dim in _dims()
            for step, label in (("1", "ENFORCED"),
                                ("2", "WAIVED"),
                                ("3", "NA"))}


def enforcement_census_with_record():
    return enforcement_census(), ()


def norecord_foreign_red_reason(foreign_reds):
    return "stub NORECORD: %r" % (list(foreign_reds)[:8],)


def substitution_census():
    from flow_matrix import substitution as SUB
    return {("1", dim): SUB.OWN_MECHANISM for dim in _dims()}


stub = types.ModuleType("test_flow_matrix_coverage")
stub.enforcement_census = enforcement_census
stub.enforcement_census_with_record = enforcement_census_with_record
stub.norecord_foreign_red_reason = norecord_foreign_red_reason
stub.substitution_census = substitution_census
sys.modules["test_flow_matrix_coverage"] = stub

sys.argv = ["gen_flow_matrix_census.py"]
if mode == "check":
    sys.argv.append("--check")
sys.argv += ["--out", out_path]
runpy.run_path(gen_path, run_name="__main__")
'''


def _gen_or_skip():
    if not GEN.exists():
        pytest.skip(
            f"generator not present at {GEN} (mirror tree); the three-outcome "
            f"path is enforced in the source-of-truth tree only")
    return GEN


def _generator():
    gen = _gen_or_skip()
    spec = importlib.util.spec_from_file_location(
        "_gen_flow_matrix_census_2203", str(gen))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


def _run(readme, mode, payload=""):
    argv = [sys.executable, "-c", _PROBE, str(_gen_or_skip()), str(readme), mode]
    if payload:
        argv.append(payload)
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=_CLI_TIMEOUT_S)


#: The generator's OWN name for "I cannot enumerate the corpus", raised by
#: `tracked_corpus_files` when `git ls-files` cannot run. It is a CANNOT-LOOK
#: condition, which is the very distinction this file exists to defend, so it
#: is answered with a skip and not with an assertion.
#:
#: MEASURED 2026-09-08 in the pinned digest: a checkout whose git admin dir is
#: not reachable — a worktree whose parent `.git` is unmounted, a tarball
#: export — makes `git ls-files` exit 128, the generator exit 1, and the assert
#: below fire INSIDE the fixture, which pytest reports as an ERROR rather than
#: a failure. Both landing arms then carried 4 identical errors and a lander
#: that refuses on any error refused a branch whose discrimination was clean.
#: Same image, same tree, same commit with the parent `.git` mounted: 6 passed.
#:
#: NARROW ON PURPOSE. Only this marker skips. Any OTHER non-zero exit from the
#: generator still asserts, because a generator that is genuinely broken must
#: not be able to buy silence from this file — that is the blind spot a skip
#: this size would otherwise open.
_CORPUS_UNREADABLE = "CorpusUnreadable"

#: THE GENERATOR'S EXIT CODE CARRIES TWO VERDICTS AND ONLY ONE IS THIS FILE'S.
#: `main` returns the ANCHORED-FIGURE verdict for the whole corpus as well as
#: the census-block verdict, so on a tree where an unrelated landing moved a
#: clause and left an anchor behind, `--check` exits 1 with the census block
#: perfectly fresh. MEASURED on pristine main c733c0e06b: 11 anchored figures
#: stale, every one off by exactly +1, `--check` rc 1.
#:
#: Read through the exit code alone this file would have been wrong in BOTH
#: directions at once: the fixture's `assert rc == 0` turned four tests into
#: ERRORs, and `test_a_genuinely_stale_census_still_exits_one` would have gone
#: GREEN on an rc 1 that had nothing to do with staleness — a guard passing for
#: a reason it never checked, which is the vacuous shape this repo refuses.
#: So the SENTENCE the generator prints about the BLOCK is the predicate here,
#: and the corpus-wide figure verdict is explicitly none of this file's
#: business. `test_flow_matrix_census_freshness` owns that one.
_CENSUS_STALE = "census block is stale"


@pytest.fixture()
def fresh_readme(tmp_path):
    """A README the generator has just written itself, so `--check` is green."""
    readme = tmp_path / "README.md"
    readme.write_text(f"before\n{BEGIN}\nplaceholder\n{END}\nafter\n",
                      encoding="utf-8")
    wrote = _run(readme, "write")
    if wrote.returncode != 0 and _CORPUS_UNREADABLE in (wrote.stdout + wrote.stderr):
        pytest.skip(
            f"the generator cannot enumerate its corpus on this checkout "
            f"({_CORPUS_UNREADABLE}: `git ls-files` cannot run here), so it "
            f"cannot write the block these tests compare against. This is a "
            f"CANNOT-LOOK state, reported as one — the same distinction this "
            f"file exists to defend. The two classifier tests below do not "
            f"need the corpus and still run, so the fix is still falsifiable "
            f"here.")
    body = readme.read_text(encoding="utf-8")
    assert BEGIN in body and END in body and "placeholder" not in body, (
        f"the generator did not replace the marked block (rc="
        f"{wrote.returncode}):\n{body}\n{wrote.stdout}{wrote.stderr}")
    assert _CENSUS_STALE not in (wrote.stdout + wrote.stderr), (
        f"the generator called its own fresh write stale.\n"
        f"{wrote.stdout}{wrote.stderr}")
    green = _run(readme, "check")
    assert _CENSUS_STALE not in (green.stdout + green.stderr), (
        f"`--check` calls stale a block it has just written itself, so neither "
        f"direction below would mean anything.\n{green.stdout}\n{green.stderr}")
    return readme


def test_a_stalled_inner_driver_exits_undetermined_and_names_the_host(
        fresh_readme):
    """rc 2, not rc 1 — and the reason a landing reader will see.

    rc 1 is the code `census_freshness_emit` prints "the published census does
    not re-derive on this tree" for. A run that was KILLED has not re-derived
    anything, so it may not be given that code.
    """
    got = _run(fresh_readme, "check", _STALL_ASSERTION)
    diag = got.stdout + got.stderr
    assert got.returncode == 2, (
        f"a killed inner driver exited {got.returncode}; rc 1 is a finding "
        f"about the commit and no property of the commit changed when the "
        f"host got busy.\n{diag}")
    assert "[UNDETERMINED]" in diag, (
        f"the decline must be ANNOUNCED — a silent rc 2 cannot be told from a "
        f"gate that examined nothing on purpose.\n{diag}")
    assert "NOT_MEASURED" in diag, (
        f"`census_freshness_emit`'s REPORT branch prints the lines matching "
        f"NORECORD|ZERO_DENOMINATOR|CROSS_TREE|NOT_MEASURED. Without one of "
        f"those tokens the stamp says 'could not be measured' and shows the "
        f"operator nothing about WHY.\n{diag}")
    assert "stall watchdog" in diag, (
        f"the host condition must be named, not merely coded.\n{diag}")


def test_undetermined_is_not_a_pass(fresh_readme):
    """rc 2 is refused by the stamp, for the right reason.

    The other half of the relabel hazard: a stall that became rc 0 would be a
    landing bought with a busy host. `census_freshness_emit` PASSes on rc 0
    only, so the assertion here is that the code is neither 0 nor 1.
    """
    got = _run(fresh_readme, "check", _STALL_ASSERTION)
    assert got.returncode not in (0, 1), (
        f"rc {got.returncode}: a stall must be neither a pass nor a verdict "
        f"about the tree.\n{got.stdout}{got.stderr}")


def test_a_genuinely_stale_census_still_exits_one(fresh_readme):
    """The direction that must NOT move. One digit of the total row.

    If this ever reports UNDETERMINED the fix has become the relabel the issue
    forbids, and every board would look like progress.
    """
    body = fresh_readme.read_text(encoding="utf-8")
    marker = None
    for line in body.splitlines():
        if "**total**" in line:
            marker = line
            break
    assert marker, f"no total row to mutate in the generated block:\n{body}"
    import re as _re
    mutated = _re.sub(r"\*\*(\d+)\*\*",
                      lambda m: f"**{int(m.group(1)) + 1}**", marker, count=1)
    assert mutated != marker, marker
    fresh_readme.write_text(body.replace(marker, mutated), encoding="utf-8")

    got = _run(fresh_readme, "check")
    diag = got.stdout + got.stderr
    assert _CENSUS_STALE in diag, (
        f"the census block was mutated and `--check` did not say so. This is "
        f"the direction that must NOT move: a stale census is the finding this "
        f"gate exists for.\n{diag}")
    assert got.returncode == 1, (
        f"a stale census exited {got.returncode}; staleness is a FINDING about "
        f"the tree and rc 1 is the code that says so.\n{diag}")
    assert "[UNDETERMINED]" not in diag, (
        f"a stale census was reported as undetermined — this is the relabel "
        f"vibe-ic#2203 forbids.\n{diag}")


def test_a_non_stall_refusal_of_the_outcome_run_still_exits_one(fresh_readme):
    """The same assertion, without the stall. Still a refusal, still rc 1.

    `_run_one_module_outcome` raises ONE AssertionError for every reason the
    driver could not record a session — a lost manifest, an empty manifest, a
    coverage problem. Only the stall among them is a fact about the host, and
    widening the classifier to the rest would hide real breakage behind
    "the host was busy".
    """
    got = _run(fresh_readme, "check", _NON_STALL_ASSERTION)
    diag = got.stdout + got.stderr
    assert got.returncode == 1, (
        f"a non-stall refusal exited {got.returncode}; only a stall is "
        f"undetermined.\n{diag}")
    assert "[UNDETERMINED]" not in diag, diag


def test_the_classifier_reads_the_drivers_own_two_halves():
    """Unit-level, both halves required, in both directions."""
    gen = _generator()
    assert gen._nested_outcome_stall(AssertionError(_STALL_ASSERTION))
    assert gen._nested_outcome_stall(AssertionError(_NON_STALL_ASSERTION)) is None
    # A marker with no driver header: a test module that merely TALKS about
    # WATCHDOG_STALLED — several in this repo do — may not buy an UNDETERMINED.
    assert gen._nested_outcome_stall(AssertionError(
        "a docstring mentioning WATCHDOG_STALLED and nothing else")) is None
    # The driver header with no marker: rc 2 for one of its other reasons.
    assert gen._nested_outcome_stall(AssertionError(
        "the outcome run for x.py produced no complete semantic pytest "
        "lifecycle record (driver rc=2, 1-way concurrent).")) is None
    # Not an AssertionError at all.
    assert gen._nested_outcome_stall(RuntimeError(_STALL_ASSERTION)) is None


def test_a_real_nested_driver_stall_reaches_undetermined(tmp_path, monkeypatch, capsys):
    """One genuinely stalled item through the real driver; no matrix evaluation."""
    gen = _generator()
    cv, sub, dims, names, questions = gen._load()
    hang = tmp_path / "test_stalled_item.py"
    hang.write_text("import time\ndef test_stalled():\n    time.sleep(60)\n")
    monkeypatch.setattr(cv, "_OUTCOME_PROGRESS_STALL_S", 3.0)

    def one_stalled_module():
        cv._run_one_module_outcome(hang, tmp_path)
        raise AssertionError("the stalled module unexpectedly completed")

    monkeypatch.setattr(cv, "enforcement_census_with_record", one_stalled_module)
    monkeypatch.setattr(gen, "_load", lambda: (cv, sub, dims, names, questions))
    import _progress_run as pr
    try:
        rc = pr.exit_undetermined_on_stall(gen.census_rows_with_record)
    except AssertionError:
        rc = 1  # the interpreter exit that the original generator produced
    captured = capsys.readouterr()
    assert rc == 2, captured.err
    assert "[UNDETERMINED]" in captured.err
    assert "test_stalled_item.py" in captured.err
    assert "stall watchdog" in captured.err


def test_the_stall_type_is_the_one_the_undetermined_wrapper_catches():
    """The contract is the TYPE, not the sentence.

    `exit_undetermined_on_stall` catches `_progress_run.Stalled`. A subclass
    that drifted off that base would compose a perfect message and still exit
    1, which is the defect wearing a better sentence.
    """
    gen = _generator()
    sys.path.insert(0, str(GEN.parent.parent
                           / "vibe-ic-marketplace" / "plugins" / "vibe-ic"
                           / "programs"))
    import _progress_run as pr
    assert issubclass(gen._NestedOutcomeStalled, pr.Stalled)
    exc = gen._NestedOutcomeStalled("NOT_MEASURED: composed", "diag")
    assert pr.exit_undetermined_on_stall(
        lambda: (_ for _ in ()).throw(exc)) == pr.RC_UNDETERMINED
