#!/usr/bin/env python3
"""A stricter guard on the contract landed at cf296de04 (v1.20.1).

WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT REPEAT
========================================================
`test_corpus_may_be_absent_is_reachable.py` (landed with the fix) pins the three
rows the repair was about: absent + permitted -> rc 0, absent + permitted +
`--changed-since` -> rc 0, absent WITHOUT the permission -> rc 2. Those are not
re-asserted here.

This file pins the FIVE properties that survive the same contract but are not
observable from those three, plus the reach of "not a directory". Each one is a
way the repair could have been right in the shape it was tested and wrong in the
shape the lander actually runs:

  1. A refusal that never certifies the pointer's tree. `..._is_reachable`'s
     pointer row asserts `rc == 2` and the word "explicit". A gate that refused
     AND ALSO walked the decoy would satisfy both — and walking a tree nobody
     named, while reporting confidently, IS the czcorpus defect (2026-09-08, 28
     tests judged the wrong subject). The subject scanned is asserted here, not
     only the exit code.
  2. The no-permission refusal under `--changed-since`. Scope is where this
     defect lived; a rule pinned in one scope is pinned in half the callers.
  3. Scope-invariance as a PROPERTY, not a value: the plain and the scoped run
     must agree. A future change that splits them again goes red here even if it
     picks a verdict both of the value-assertions above would accept.
  4. A malformed corpus refused with rc == 1 and the failing rules NAMED.
     `..._is_reachable`'s positive control is `rc != 0 or "NO_CORPUS" not in
     stderr` — a disjunction a gate that scanned nothing and printed nothing
     with rc 0 also satisfies, because the second half is then true.
  5. THE LANDER'S OWN SHAPE: a malformed cell that THIS CHANGE published, under
     `--changed-since`, in a real git fixture. `tools/gatekeeper-land.sh` adds
     `--changed-since` whenever the pointer is unset, so the grandfathering path
     is the one that runs on every landing and no test reached it.
  6. The permission covers "not a directory", not "not readable as a corpus":
     a plain file and a dangling symlink take the same NO_CORPUS row and say
     which path they looked at.

THE ARM, AND WHY IT IS A MUTATION AND NOT TWO TREES
==================================================
A tests-only change has no second immutable tree to compare against: both arms
would carry identical sources and the differential would be empty. The evidence
is a MUTATION arm instead — one tree, one hunk reverted.

    GREEN     live main 6883a9c93 (v1.20.7), checker blob b904779af, untouched
              -> 7 passed
    MUTATION  the same tree with cf296de04's hunk reverted: `_absence_is_permitted`
              deleted and `if args.tree is not None and (not args.tree or not
              Path(args.tree).is_dir()):` put back. Blob fa7cbc1fd; it is the ONLY
              difference between the two arms.
              -> 2 failed, 5 passed

The mutation is proven to bite before the tests are read: the landing's own
invocation (`--tree benchmark-data --corpus-may-be-absent --changed-since`) is
rc 0 on the green arm and rc 2 on the mutant. `control_substance_check --junit`
grades the mutation arm "2 of 2 reported failures observed a VALUE".

The two rows that die are 3 and 6. Row 6 is not covered by
`test_corpus_may_be_absent_is_reachable.py` at all, so it is reach this file adds
rather than reach it repeats. Rows 1, 2, 4 and 5 are green in BOTH arms on
purpose: they do not prove the contract, they refuse the wrong ways to satisfy
it. They do not prove the repair; they refuse the wrong ways to
have obtained its number.

chip-AGNOSTIC: generic IC/PDK tokens only, every fixture under tmp_path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CHECK = (Path(__file__).resolve().parents[1]
         / "benchmark_evidence_structure_check.py")
ENV = "VIBE_IC_BENCHMARK_DATA"

_GOOD_MANIFEST = ("top.gds 1180456B sha256:"
                  + "2915355c69e0162887e4c3e3e60855a0710a8bccb0e02f1b08191989ef392c8f")
_RESULT_PASS = "# RESULT\n\n## VERDICT\n\n**PASS_WITH_WAIVERS.** re-derived.\n"

SCOPES = ([], ["--changed-since", "HEAD"])


def _run(args, pointer=None, cwd=None):
    env = dict(os.environ)
    env.pop(ENV, None)
    if pointer is not None:
        env[ENV] = str(pointer)
    return subprocess.run([sys.executable, str(CHECK), *args], env=env,
                          cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=900)


def _git(root: Path, *cmd: str):
    return subprocess.run(["git", "-C", str(root), *cmd], check=True,
                          capture_output=True, text=True, timeout=120)


def _cell(ic: Path, name: str, conformant: bool = True) -> Path:
    d = ic / name
    if conformant:
        (d / "phase1" / "generated_docs").mkdir(parents=True)
        (d / "phase1" / "generated_docs" / "L1.json").write_text("{}")
        (d / "phase2" / "stage1" / "rtl").mkdir(parents=True)
        (d / "phase2" / "stage1" / "rtl" / "top.v").write_text(
            "module top; endmodule\n")
        (d / "reports" / "phase3").mkdir(parents=True)
        (d / "reports" / "phase3" / "drc.json").write_text("{}")
        (d / "phase3" / "stage4" / "gds").mkdir(parents=True)
        (d / "phase3" / "stage4" / "gds" / "GDS_MANIFEST.txt").write_text(
            _GOOD_MANIFEST + "\n")
    else:
        (d / "phase1").mkdir(parents=True)
    (d / "RESULT.md").write_text(_RESULT_PASS)
    return d


def _corpus(base: Path, name: str, cell_name: str, conformant: bool) -> Path:
    root = base / name
    _cell(root / "ic" / f"unit_{name}", cell_name, conformant)
    return root


# --------------------------------------------------------------------- 1, 2
# Green in BOTH arms. The czcorpus rule, asserted on the SUBJECT and in both
# scopes rather than on the exit code in one.

def test_the_refusal_with_a_set_pointer_never_certifies_the_pointers_tree(
        tmp_path):
    decoy = _corpus(tmp_path, "decoy", "v7.7.7_openpdkx", True)
    missing = tmp_path / "benchmark-data"
    for scope in SCOPES:
        r = _run(["--tree", str(missing), "--corpus-may-be-absent", *scope],
                 pointer=decoy)
        assert r.returncode == 2, (scope, r.stdout, r.stderr)
        assert "v7.7.7_openpdkx" not in r.stdout, (
            "a refused explicit subject reported on the pointer's tree — that "
            "is the czcorpus defect wearing a correct exit code:\n" + r.stdout)


def test_without_the_permission_an_absent_root_refuses_in_every_scope(tmp_path):
    missing = tmp_path / "benchmark-data"
    for scope in SCOPES:
        r = _run(["--tree", str(missing), *scope])
        assert r.returncode == 2, (scope, r.stdout, r.stderr)
        assert "UNDETERMINED" in r.stderr, (scope, r.stderr)


# ------------------------------------------------------------------------ 3
# FALSIFYING on 610cae2cc (2 == 2 there, 0 == 0 here — the assertion is that the
# two AGREE, and it is the pair that moved).

def test_the_two_scopes_agree_over_an_absent_permitted_subject(tmp_path):
    missing = tmp_path / "benchmark-data"
    plain = _run(["--tree", str(missing), "--corpus-may-be-absent"])
    scoped = _run(["--tree", str(missing), "--corpus-may-be-absent",
                   "--changed-since", "HEAD"])
    assert plain.returncode == scoped.returncode == 0, (
        "one absent subject, one verdict: %s without --changed-since, %s with\n%s%s"
        % (plain.returncode, scoped.returncode, plain.stderr, scoped.stderr))
    assert "NO_CORPUS" in plain.stderr and "NO_CORPUS" in scoped.stderr, (
        plain.stderr, scoped.stderr)


# ------------------------------------------------------------------------ 4
# Green in BOTH arms. The permission must not shortcut a tree that IS there.

def test_a_present_corpus_is_scanned_not_excused_by_the_permission(tmp_path):
    named = _corpus(tmp_path, "present", "v1.2.3_openpdkx", True)
    decoy = _corpus(tmp_path, "decoy", "v7.7.7_openpdkx", True)
    r = _run(["--tree", str(named), "--corpus-may-be-absent"], pointer=decoy)
    assert "NO_CORPUS" not in r.stderr, (
        "a corpus that is present was excused as absent:\n" + r.stderr)
    assert "v1.2.3_openpdkx" in r.stdout, r.stdout + r.stderr
    assert "v7.7.7_openpdkx" not in r.stdout, r.stdout


# ------------------------------------------------------- 5, POSITIVE CONTROL
# Green in BOTH arms. If a future repair makes the gate green by disarming it,
# these are what go red.

def test_a_malformed_corpus_is_refused_and_its_failing_rules_are_named(
        tmp_path):
    named = _corpus(tmp_path, "malformed", "v1.2.3_openpdkx", False)
    r = _run(["--tree", str(named), "--corpus-may-be-absent"])
    assert r.returncode == 1, (
        "a present, malformed corpus was not refused:\n" + r.stdout + r.stderr)
    assert "nonconformant" in r.stdout, r.stdout
    for rule in ("PHASE1_DOCS", "PHASE2", "PHASE3_REPORTS", "GDS_MANIFEST"):
        assert rule in r.stdout, (rule, r.stdout)


def test_a_malformed_cell_this_change_published_is_refused_under_scope(
        tmp_path):
    """The lander's own shape: no pointer, a ROOT that IS there, the permission
    AND `--changed-since`. Grandfathering must not excuse what this change
    added."""
    named = _corpus(tmp_path, "landing", "v1.2.3_openpdkx", False)
    _git(named, "init", "-q")
    _git(named, "config", "user.email", "t@t")
    _git(named, "config", "user.name", "t")
    (named / "README.md").write_text("base\n")
    _git(named, "add", "README.md")
    _git(named, "commit", "-qm", "base")
    base = _git(named, "rev-parse", "HEAD").stdout.strip()
    _git(named, "add", "-Af")
    _git(named, "commit", "-qm", "publish the malformed cell")
    r = _run(["--tree", str(named), "--corpus-may-be-absent",
              "--changed-since", base])
    assert r.returncode == 1, (
        "a malformed cell ADDED by the change under measurement was excused:\n"
        + r.stdout + r.stderr)
    assert "v1.2.3_openpdkx" in r.stdout, r.stdout


# ------------------------------------------------------------------------ 6
# FALSIFYING on 610cae2cc. States how far the permission reaches: it answers
# "not a directory", and it names the path it looked at either way.

def test_the_permission_covers_every_shape_of_not_a_directory(tmp_path):
    a_file = tmp_path / "benchmark-data"
    a_file.write_text("this is a file, not a corpus root\n")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "never-created")
    for subject in (a_file, dangling):
        for scope in SCOPES:
            r = _run(["--tree", str(subject), "--corpus-may-be-absent", *scope])
            assert r.returncode == 0, (str(subject), scope, r.stdout, r.stderr)
            assert "NO_CORPUS" in r.stderr, (str(subject), scope, r.stderr)
            assert str(subject) in r.stderr, (
                "the statement must name the path it looked at: " + r.stderr)
