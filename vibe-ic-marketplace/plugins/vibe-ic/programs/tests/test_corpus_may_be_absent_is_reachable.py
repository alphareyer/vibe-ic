"""`--corpus-may-be-absent` must be reachable when `--tree` is also named.

MEASURED 2026-09-09. `gatekeeper-land.sh:552` runs the structure gate as

    benchmark_evidence_structure_check.py --tree benchmark-data --corpus-may-be-absent [...]

on EVERY landing. `benchmark-data/` moved to its own repository, so the explicit-subject
guard returned 2 before the permission was ever consulted, the cheap tier read rc != 0 as
FAIL, and THE LANDING GATE REFUSED EVERY CHANGE. Fourteen gates passed and this one blocked
the repo for a tree it is not required to carry.

The guard itself is right and is kept: an environment pointer must never SUBSTITUTE for an
explicitly named subject (czcorpus, 2026-09-08 — 28 tests that built a fixture corpus in
tmp_path were silently redirected onto the real corpus and judged the wrong subject).

But "may not be substituted" and "may not be absent" are different claims. These tests pin
all three cases so the next reader cannot collapse them again.
"""
import os
import subprocess
import sys
from pathlib import Path

CHECK = (Path(__file__).resolve().parents[1]
         / "benchmark_evidence_structure_check.py")
ENV = "VIBE_IC_BENCHMARK_DATA"


def _run(args, cwd, env_tree=None):
    env = dict(os.environ)
    env.pop(ENV, None)
    if env_tree is not None:
        env[ENV] = env_tree
    return subprocess.run([sys.executable, str(CHECK), *args],
                          cwd=str(cwd), env=env,
                          capture_output=True, text=True)


def test_an_absent_tree_the_caller_permitted_is_NO_CORPUS_not_a_refusal(tmp_path):
    """The landing's exact invocation. Absent + permitted + no pointer -> rc 0."""
    cp = _run(["--tree", "benchmark-data", "--corpus-may-be-absent"], tmp_path)
    assert cp.returncode == 0, (cp.returncode, cp.stdout, cp.stderr)
    assert "NO_CORPUS" in cp.stderr + cp.stdout, cp.stderr


def test_the_permission_survives_changed_since(tmp_path):
    """The lander adds --changed-since whenever it has a base. Same verdict, or the
    permission is reachable only in the shape nobody actually uses."""
    cp = _run(["--tree", "benchmark-data", "--corpus-may-be-absent",
               "--changed-since", "HEAD"], tmp_path)
    assert cp.returncode == 0, (cp.returncode, cp.stdout, cp.stderr)


def test_an_absent_tree_WITHOUT_the_permission_still_refuses(tmp_path):
    """The guard is not weakened: absence alone is still UNDETERMINED. If this ever
    passes, the repair became a licence."""
    cp = _run(["--tree", "benchmark-data"], tmp_path)
    assert cp.returncode == 2, (cp.returncode, cp.stdout, cp.stderr)
    assert "not a directory" in cp.stderr, cp.stderr


def test_a_pointer_that_is_SET_still_refuses_even_with_the_permission(tmp_path):
    """The czcorpus rule, intact. A caller naming two subjects is still said out loud —
    the permission excuses an ABSENCE, never a SUBSTITUTION."""
    decoy = tmp_path / "elsewhere"
    decoy.mkdir()
    cp = _run(["--tree", "benchmark-data", "--corpus-may-be-absent"],
              tmp_path, env_tree=str(decoy))
    assert cp.returncode == 2, (cp.returncode, cp.stdout, cp.stderr)
    assert "explicit" in cp.stderr.lower(), cp.stderr


def test_a_PRESENT_tree_is_still_scanned_and_can_still_fail(tmp_path):
    """The positive control that matters: the repair must not turn the gate into a
    no-op for repos that DO carry a corpus. A malformed evidence tree still refuses."""
    tree = tmp_path / "benchmark-data"
    (tree / "ic" / "not_a_real_ic").mkdir(parents=True)
    (tree / "ic" / "not_a_real_ic" / "stray.txt").write_text("not evidence\n")
    cp = _run(["--tree", "benchmark-data", "--corpus-may-be-absent"], tmp_path)
    assert cp.returncode != 0 or "NO_CORPUS" not in cp.stderr, (
        "a PRESENT tree must be scanned, not excused by the absence permission:\n"
        + cp.stdout + cp.stderr)
