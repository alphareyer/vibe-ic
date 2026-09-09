#!/usr/bin/env python3
"""Two landed contracts answered one command line differently. This pins the seam.

THE COLLISION, MEASURED ON MAIN 72bd2679b
=========================================
`benchmark_evidence_structure_check.py --tree benchmark-data --corpus-may-be-absent`
with $VIBE_IC_BENCHMARK_DATA set had two shipped answers:

  #1710 (v1.10.51) — follow the pointer and ANNOUNCE it. The corpus left this
      repository in v1.10.56 and the pointer is the only way to say where it went.
      `test_issue1710_evidence_gate_finds_the_moved_corpus` rows 4 and 5.
  czcorpus (v1.19.92) — refuse: an environment variable must never SUBSTITUTE for
      a subject the caller named. 28 tests had been judged against the wrong tree.

Both are right about different inputs, and the difference is WHO CHOSE THE NAME.
`benchmark-data` as a REPOSITORY-RELATIVE path is not a subject a caller picked;
it is this repository's own former name for the tree that moved out of it,
spelled once as `_corpus_location.CANONICAL_CORPUS_NAME` and hardcoded into both
shipped call sites. An absolute path, or any other relative name, IS a subject
the caller picked, and czcorpus governs it unchanged.

WHAT THE UNRESOLVED COLLISION COST, MEASURED
============================================
With a real 21-cell corpus on this host, on 72bd2679b:

    pointer unset -> rc 0  NO_CORPUS      nothing scanned, by permission
    pointer set   -> rc 2  UNDETERMINED   refused before discovery

`tools/gatekeeper-land.sh` drops `--changed-since` exactly when the pointer IS
set, and `tools/ci/hermetic_candidate_runner.py` sets the pointer in BOTH its
land and test process envs. So configuring the corpus — the one supported way to
make this gate check something — turned it into a landing blocker, and there was
no configuration under which the gate scanned a corpus at all.

It also left a FALSE GREEN.
`test_issue1710_...::test_a_pointed_at_corpus_that_is_malformed_still_fails`
asserts `rc != 0` over a malformed pointed-at corpus. On 72bd2679b it got rc 2
because the gate never opened that corpus; the same corpus named directly gives
rc 1 with the offending entry named. Row 3 below is that test's subject, asserted
on the verdict rather than on "not zero".

chip-AGNOSTIC: generic IC/PDK tokens only, every fixture under tmp_path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
PROG = PROGRAMS / "benchmark_evidence_structure_check.py"
ENV = "VIBE_IC_BENCHMARK_DATA"
CANONICAL = "benchmark-data"


def _run(*args: str, pointer=None, cwd=None):
    env = dict(os.environ)
    env.pop(ENV, None)
    if pointer is not None:
        env[ENV] = str(pointer)
    r = subprocess.run([sys.executable, str(PROG), *args], env=env,
                       cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, timeout=900)
    return r.returncode, r.stdout + r.stderr


def _corpus(root: Path, cell: str = "v1.9.96_openpdkx") -> Path:
    d = root / "ic" / "unitx" / cell
    d.mkdir(parents=True)
    (d / "RESULT.md").write_text("# result\nverdict: PASS\n")
    return root


# ------------------------------------------------------------- falsifying
# RED on 72bd2679b (all three answer UNDETERMINED, having scanned nothing).

def test_the_canonical_relative_name_lets_the_pointer_supply_the_corpus(tmp_path):
    corpus = _corpus(tmp_path / "clone")
    rc, out = _run("--tree", CANONICAL, "--corpus-may-be-absent", pointer=corpus)
    assert f"{ENV} overrides --tree" in out, out
    assert str(corpus) in out, "the tree actually scanned must be named:\n" + out
    assert "UNDETERMINED" not in out and "NO_CORPUS" not in out, out
    assert "v1.9.96_openpdkx" in out or "conformant" in out, (
        "the gate produced no evidence it opened the corpus:\n" + out)


def test_a_subpath_under_the_canonical_name_maps_into_the_clone(tmp_path):
    """CI names `benchmark-data/ic`; the clone carries `ic/` at its top."""
    corpus = _corpus(tmp_path / "clone")
    rc, out = _run("--tree", f"{CANONICAL}/ic", "--corpus-may-be-absent",
                   pointer=corpus)
    assert f"overrides --tree {CANONICAL}/ic -> {corpus / 'ic'}" in out, out
    assert "UNDETERMINED" not in out, out


def test_a_malformed_pointed_at_corpus_is_refused_on_its_CONTENT(tmp_path):
    """The de-vacuuming of #1710 row 6: rc 1 because the corpus was opened and
    judged, not rc 2 because it never was."""
    corpus = tmp_path / "clone"
    bad = corpus / "ic" / "unitx" / "clean_run_whatever"
    bad.mkdir(parents=True)
    (bad / "RESULT.md").write_text("# result\n")
    rc, out = _run("--tree", CANONICAL, "--corpus-may-be-absent", pointer=corpus)
    assert rc == 1, (
        "a malformed pointed-at corpus must be refused on its CONTENT; rc 2 "
        "would mean the gate never opened it:\n" + out)
    assert "clean_run_whatever" in out, (
        "the refusal must name what it found:\n" + out)


# ---------------------------------------------------------------- controls
# Green in BOTH arms. czcorpus is not weakened: the permission to substitute
# belongs to ONE name, and these are the ways of not being that name.

def test_an_absolute_path_that_merely_ENDS_in_the_canonical_name_is_not_it(
        tmp_path):
    corpus = _corpus(tmp_path / "clone")
    absolute = tmp_path / CANONICAL          # absolute, absent, same basename
    rc, out = _run("--tree", str(absolute), "--corpus-may-be-absent",
                   pointer=corpus)
    assert rc == 2 and "UNDETERMINED" in out, out
    assert "v1.9.96_openpdkx" not in out, (
        "a subject the caller named was replaced by the pointer's tree:\n" + out)


def test_any_other_relative_name_is_still_never_substituted(tmp_path):
    corpus = _corpus(tmp_path / "clone")
    rc, out = _run("--tree", "some-other-relative-name", "--corpus-may-be-absent",
                   pointer=corpus, cwd=tmp_path)
    assert rc == 2 and "UNDETERMINED" in out, out
    assert "v1.9.96_openpdkx" not in out, out


def test_with_no_pointer_the_canonical_name_is_still_NO_CORPUS(tmp_path):
    """v1.20.1, untouched: the permission still answers the unconfigured repo."""
    rc, out = _run("--tree", CANONICAL, "--corpus-may-be-absent", cwd=tmp_path)
    assert rc == 0 and "NO_CORPUS" in out, out


def test_a_broken_pointer_is_still_undetermined_under_the_canonical_name(
        tmp_path):
    """#1710's first outcome: set-and-wrong is never laundered as absent."""
    rc, out = _run("--tree", CANONICAL, "--corpus-may-be-absent",
                   pointer=tmp_path / "nowhere", cwd=tmp_path)
    assert rc == 2 and "UNDETERMINED" in out, out
    assert ENV in out and "NO_CORPUS" not in out, out
