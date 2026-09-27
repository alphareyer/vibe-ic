"""A test that plants a probe in the shipped tree races every test that reads it.

THE DEFECT (8HD-8 stacked census, 2026-09-28). `test_issue559`'s growth-hole
test wrote `programs/brand_new_hand_rolled_check.py`, measured, and unlinked it
in a `finally`. Under xdist, `test_upstream_mirror_is_pinned::
test_the_shipped_tree_is_clean` listed programs/*.py while the probe existed and
read it after the unlink: `FileNotFoundError`, a red on a test that is correct.
Lane rfrace fixed twelve writers of this shape on 2026-09-27; this one was
missed because its write sat inside a helper whose parameter is named
`tmp_path`, and the caller passed the SHIPPED programs dir as that argument.

WHY A NEW TEST IS NEEDED AT ALL. `suite_write_guard` compares `git status`
snapshots and states its own bound: "a write made AND reverted inside a single
test is invisible to a snapshot taken after it". A probe writer that cleans up
is exactly that write, so the guard printed `[PASS] ... wrote nothing` over
every one of these on origin/main. The race is the only symptom, and a race
is a symptom nobody reproduces on demand.

WHAT THIS DOES. It re-runs each former writer in a child pytest under
`_tree_write_audit`, which records the WRITE itself (an audit hook in every
Python process of the child), and requires both that the node PASSED -- a node
that did not run proves nothing about what it writes -- and that it wrote
nothing under the checkout. Moving any of them back into the shipped tree is
then a named red here instead of an intermittent one somewhere else.

Each entry names what the node wrote on origin/main 76a277544, measured with
the same hook.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_PLUGIN = _TESTS.parents[1]
#: The checkout the plugin sits in (the monorepo root), or the plugin itself
#: where there is no enclosing checkout (the installed cache).
_ROOT = next((p for p in _PLUGIN.parents if (p / ".git").exists()), _PLUGIN)
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
import _progress_run as _pr  # noqa: E402
import _tree_write_audit as TWA  # noqa: E402

_T = "programs/tests/"
FORMER_WRITERS = {
    _T + "test_issue559_drift_check_rule_b_blindspot.py"
         "::test_a_newly_registered_hand_rolled_gate_fails_the_ratchet":
        "wrote, chmod'ed and unlinked programs/brand_new_hand_rolled_check.py",
    _T + "test_issue559_drift_check_rule_b_blindspot.py"
         "::test_a_rule_b_gate_is_not_filed_as_mechanical_wiring":
        "wrote, chmod'ed and unlinked programs/brand_new_semantic_check.py",
    _T + "test_unwired_by_decision_proof_is_re_derived.py"
         "::test_a_probe_that_raises_does_not_buy_the_exclusion":
        "wrote and unlinked programs/_tmp_raising_probe.py",
    _T + "test_progress_run.py"
         "::test_the_outer_window_is_RESOLVED_from_its_owner_not_copied_here":
        "REWROTE the tracked programs/pytest_per_file_junit.py in place "
        "(DEFAULT_STALL_AFTER 300 -> 999) and wrote it back",
    _T + "test_protocol_detector_no_misfire.py"
         "::test_every_detector_fires_on_its_own_benchmark":
        "materialised programs/tests/fixtures/synthetic_benchmark_phase1/ "
        "at IMPORT time (collection), in every worker that collected it",
    _T + "test_protocol_detector_no_misfire_matrix.py"
         "::test_no_gold_cross_contamination":
        "the same import-time corpus write, through its import of "
        "test_protocol_detector_no_misfire",
}


@pytest.fixture(scope="module")
def audit():
    return TWA.run_nodes(sorted(FORMER_WRITERS), plugin_root=_PLUGIN,
                         root=_ROOT)


def test_the_audit_sees_a_write_and_only_a_write(tmp_path):
    """Calibration. The instrument must record a planted-and-removed file,
    and must NOT record the three things that fooled its first version: a tmp
    symlink farm pointing INTO the tree being deleted (that deletes links, not
    the tree), `shutil.rmtree` of a tmp dir run from a cwd inside the tree (it
    removes entries by bare name relative to a directory fd), and an anonymous
    `O_TMPFILE` opened on the tree's directory (it creates no entry)."""
    root = tmp_path / "tree"
    (root / "programs").mkdir(parents=True)
    (root / "programs" / "real.py").write_text("x\n")
    farm = tmp_path / "farm"
    farm.mkdir()
    (farm / "real.py").symlink_to(root / "programs" / "real.py")
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent("""
        import shutil, sys
        from pathlib import Path
        root, farm, scratch = map(Path, sys.argv[1:4])
        probe = root / "programs" / "probe.py"
        probe.write_text("planted\\n")
        probe.unlink()
        shutil.rmtree(farm)
        (scratch / "d" / "e").mkdir(parents=True)
        (scratch / "d" / "e" / "f").write_text("x")
        shutil.rmtree(scratch / "d")
        import os
        if hasattr(os, "O_TMPFILE"):
            os.close(os.open(str(root / "programs"),
                             os.O_TMPFILE | os.O_RDWR, 0o600))
    """))
    work = tmp_path / "work"
    work.mkdir()
    r = _pr.run(
        [sys.executable, str(child), str(root), str(farm),
         str(tmp_path / "scratch")],
        cwd=str(root), env=TWA.audit_env(root, work),
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    events = TWA.read_events(work)
    seen = {(e["event"], e["path"]) for e in events}
    assert ("open", "programs/probe.py") in seen, events
    assert ("os.remove", "programs/probe.py") in seen, events
    assert {e["path"] for e in events} == {"programs/probe.py"}, (
        "the audit reported a write the child never made under the tree: "
        f"{sorted(seen)}")
    assert (root / "programs" / "real.py").read_text() == "x\n"


@pytest.mark.parametrize("nodeid", sorted(FORMER_WRITERS))
def test_a_former_writer_plants_nothing_in_the_shipped_tree(audit, nodeid):
    got = audit.outcomes.get(nodeid)
    assert got == "passed", (
        f"{nodeid} did not PASS under the audit ({got or 'not run'}); a node "
        f"that did not run proves nothing about what it writes.\n"
        f"child rc={audit.rc}\n{audit.output[-3000:]}")
    wrote = sorted({f"{e['event']} {e['path']}" for e in audit.events
                    if e["test"].split(" ")[0] == nodeid})
    assert not wrote, (
        f"{nodeid} wrote into the checkout it tests: {'; '.join(wrote)}. "
        f"On origin/main 76a277544 it {FORMER_WRITERS[nodeid]}; every "
        f"concurrent worker listing that directory races it.")


def test_collecting_the_former_writers_plants_nothing(audit):
    """An import-time write has no test id to carry. Both corpus modules wrote
    during COLLECTION, so this is where they would show."""
    assert audit.outcomes, (
        f"the audited child reported no test at all (rc={audit.rc}); an "
        f"empty session proves nothing:\n{audit.output[-3000:]}")
    wrote = sorted({f"{e['event']} {e['path']}" for e in audit.events
                    if not e["test"]})
    assert not wrote, (
        "collecting the former writers wrote into the checkout: "
        + "; ".join(wrote))
