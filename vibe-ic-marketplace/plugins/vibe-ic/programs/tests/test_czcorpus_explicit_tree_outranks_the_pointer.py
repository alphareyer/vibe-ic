#!/usr/bin/env python3
"""An explicit `--tree` names the subject; the corpus pointer must not replace it.

MEASURED (czcorpus lane, 2026-09-08, main 8862fd371): 28 tests that build a
synthetic corpus in ``tmp_path`` and pass ``--tree <tmp_path>`` were silently
redirected onto ``$VIBE_IC_BENCHMARK_DATA`` and judged a tree the caller never
named. The verdict was complete and confident about the wrong subject.

THE RULE THIS PINS, IN BOTH DIRECTIONS
======================================
* A readable explicit ``--tree`` WINS over the pointer.  (falsifying: red on
  unfixed sources)
* The pointer still supplies the corpus when NO ``--tree`` is given.  (control:
  green in both arms — it exists so that a future "fix" cannot make the
  environment default inert and call that a win)
* An explicit missing ``--tree`` is refused without scanning the pointer's
  subject, including when absence of a default corpus would be allowed.
  The original fallback control's node ID is retained for exact-ID comparison;
  its old behavior was the remaining defect, not a valid default.
* A caller that passes BOTH a readable root and a pointer at a DIFFERENT tree
  has named two subjects. That is said out loud, not silently resolved.
  (falsifying)

chip-AGNOSTIC: generic IC/PDK tokens only, all fixtures under tmp_path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "benchmark_evidence_structure_check.py"

_GOOD_MANIFEST = ("top.gds 1180456B sha256:"
                  + "2915355c69e0162887e4c3e3e60855a0710a8bccb0e02f1b08191989ef392c8f")
_RESULT_PASS = "# RESULT\n\n## VERDICT\n\n**PASS_WITH_WAIVERS.** re-derived.\n"


def _run(args, pointer=None):
    env = dict(os.environ)
    env.pop("VIBE_IC_BENCHMARK_DATA", None)
    if pointer is not None:
        env["VIBE_IC_BENCHMARK_DATA"] = str(pointer)
    return subprocess.run([sys.executable, str(PROG)] + args,
                          capture_output=True, text=True, env=env)


def _cell(base: Path, name: str = "v9.9.9_openpdkx") -> Path:
    d = base / name
    (d / "phase1" / "generated_docs").mkdir(parents=True)
    (d / "phase1" / "generated_docs" / "L1.json").write_text("{}")
    (d / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    (d / "phase2" / "stage1" / "rtl" / "top.v").write_text("module top; endmodule\n")
    (d / "reports" / "phase3").mkdir(parents=True)
    (d / "reports" / "phase3" / "drc.json").write_text("{}")
    (d / "phase3" / "stage4" / "gds").mkdir(parents=True)
    (d / "phase3" / "stage4" / "gds" / "GDS_MANIFEST.txt").write_text(_GOOD_MANIFEST + "\n")
    (d / "RESULT.md").write_text(_RESULT_PASS)
    return d


def _two_trees(tmp_path):
    """A named tree the caller asks about, and a decoy the pointer names."""
    named = tmp_path / "named"
    decoy = tmp_path / "decoy"
    named.mkdir()
    decoy.mkdir()
    _cell(named, "v1.2.3_openpdkx")
    _cell(decoy, "v7.7.7_openpdkx")
    return named, decoy


# ---------------------------------------------------------------- falsifying

def test_a_readable_explicit_tree_is_the_subject_not_the_pointers_tree(tmp_path):
    named, decoy = _two_trees(tmp_path)
    r = _run(["--tree", str(named)], pointer=decoy)
    # The RC is not the point (the synthetic cells are deliberately minimal and
    # the gate reports findings on them); WHICH TREE it reported on is.
    assert "v1.2.3_openpdkx" in r.stdout, (
        "the gate was handed --tree %s and did not report on it:\n%s%s"
        % (named, r.stdout, r.stderr))
    assert "v7.7.7_openpdkx" not in r.stdout, (
        "the gate walked the pointer's tree instead of the one it was handed:\n%s%s"
        % (r.stdout, r.stderr))


def test_the_disagreement_between_the_two_is_announced_not_silently_resolved(tmp_path):
    named, decoy = _two_trees(tmp_path)
    r = _run(["--tree", str(named)], pointer=decoy)
    err = r.stderr
    assert "outranks" in err and str(named) in err and str(decoy) in err, (
        "a caller that names two subjects must be told which one was scanned; "
        "stderr was:\n%s" % err)


def test_no_false_conflict_when_the_two_name_the_same_tree(tmp_path):
    named = tmp_path / "named"
    named.mkdir()
    _cell(named, "v1.2.3_openpdkx")
    r = _run(["--tree", str(named)], pointer=named)
    assert "DISAGREE" not in r.stderr, r.stderr


# ------------------------------------------------------------------ controls
# Green in BOTH arms on purpose. They do not prove the fix; they refuse the
# obvious wrong way to get the fix's number, which is to make the pointer inert.

def test_with_no_tree_at_all_the_pointer_still_supplies_the_corpus(tmp_path):
    _, decoy = _two_trees(tmp_path)
    r = _run([], pointer=decoy)
    assert "v7.7.7_openpdkx" in r.stdout, (
        "the environment default must keep working when nothing is named:\n%s%s"
        % (r.stdout, r.stderr))
    assert "scanning" in r.stderr


def test_an_explicit_tree_that_is_not_there_still_falls_back_to_the_pointer(tmp_path):
    """Legacy node ID names the defect: explicit missing subjects must NOT fall back."""
    _, decoy = _two_trees(tmp_path)
    missing = tmp_path / "benchmark-data-that-is-gone"
    for optional in ([], ["--corpus-may-be-absent"]):
        r = _run(["--tree", str(missing), *optional], pointer=decoy)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "v7.7.7_openpdkx" not in r.stdout, (
            "a refused explicit subject must not certify the decoy:\n%s%s"
            % (r.stdout, r.stderr))
        assert str(missing) in r.stderr and "is not a directory" in r.stderr
        assert "UNDETERMINED" in r.stderr
