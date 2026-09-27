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
    _T + "test_flow_compliance_check_gate.py"
         "::test_unclassified_rc2_is_incomplete":
        "wrote and unlinked programs/_pytest_rc2_helper.py",
    _T + "test_flow_compliance_check_gate.py"
         "::test_check_program_exit_zero_rc1_still_fails":
        "wrote and unlinked programs/_pytest_rc1_helper.py",
    _T + "test_flow_compliance_check_gate.py"
         "::test_crash_is_flagged_as_a_crash_at_any_checkout_depth":
        "wrote and unlinked programs/_pytest_crash_helper.py, twice",
    _T + "test_flow_compliance_check_gate.py"
         "::test_a_real_verdict_is_not_mistaken_for_a_crash":
        "wrote and unlinked programs/_pytest_{verdict,quoted_tb,"
        "indented_err}_helper.py",
    _T + "test_flow_compliance_check_gate.py"
         "::test_rc0_and_rc2_are_not_misread_as_crashes":
        "wrote and unlinked programs/_pytest_noisy_{pass,skip}_helper.py",
    _T + "test_issue1446_scratch_root_guard.py"
         "::test_every_line_of_this_cost_table_fires":
        "created and removed a vibeic1446-probe-* directory in programs/, "
        "the plugin root, plugins/, the marketplace dir and the repo root "
        "(a mkdir probe asked of candidates already refused as in-tree)",
}
#: NOT watched here, and why. `test_issue1129_gatekeeper_prepare_landing.py::
#: test_the_real_program_runs_against_this_repo_and_honours_its_boundary` ran
#: the real landing preparation on the SHARED checkout (version bump across
#: plugin.json, both marketplace manifests and the READMEs; INDEX.md; then
#: `git checkout --`). It now runs from a private `--shared` clone and asserts
#: that the program it ran lives there. It costs ~460 s (the census writer) and
#: skips on any dirty tree, so re-running it in this child would double that
#: cost and read as "did not PASS" whenever the suite runs on local edits.


def test_the_audit_sees_a_write_and_only_a_write(tmp_path):
    """Calibration. The instrument must record a planted-and-removed file,
    and must NOT record the three things that fooled its first version: a tmp
    symlink farm pointing INTO the tree being deleted (that deletes links, not
    the tree), `shutil.rmtree` of a tmp dir run from a cwd inside the tree (it
    removes entries by bare name relative to a directory fd), an anonymous
    `O_TMPFILE` opened on the tree's directory (it creates no entry), and a
    bare-name `os.open` relative to a directory fd outside the tree (the
    event does not carry the fd), and `mkdir(exist_ok=True)` on a directory
    that is already there. A bare-name builtin `open()` IS a write to the cwd,
    and a NEW directory is a write, and both must still be seen."""
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
        fd = os.open(str(scratch), os.O_RDONLY)
        os.close(os.open("fd_relative.tmp", os.O_CREAT | os.O_WRONLY, 0o600,
                         dir_fd=fd))
        os.close(fd)
        with open("cwd_relative.txt", "w") as fh:
            fh.write("x")
        os.remove("cwd_relative.txt")
        (root / "programs").mkdir(exist_ok=True)
        (root / "programs" / "new_dir").mkdir()
        (root / "programs" / "new_dir").rmdir()
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
    assert ("open", "cwd_relative.txt") in seen, events
    assert ("os.mkdir", "programs/new_dir") in seen, events
    assert {e["path"] for e in events} == {"programs/probe.py",
                                          "cwd_relative.txt",
                                          "programs/new_dir"}, (
        "the audit reported a write the child never made under the tree: "
        f"{sorted(seen)}")
    assert (root / "programs" / "real.py").read_text() == "x\n"


def test_no_former_writer_plants_anything_in_the_shipped_tree():
    """ONE child session for every former writer, and ONE test, on purpose: a
    module-scoped fixture is instantiated once per xdist WORKER, so spreading
    per-node cases across workers re-ran the whole child session for each.

    Two failure classes, both named per node. A node that did not PASS proves
    nothing about what it writes (renamed, deleted, or red for another
    reason). A node that wrote under the checkout is the defect. An import-time
    write carries no test id, so collection-time events are reported on their
    own: both corpus modules wrote while they were being COLLECTED."""
    audit = TWA.run_nodes(sorted(FORMER_WRITERS), plugin_root=_PLUGIN,
                          root=_ROOT)
    assert audit.outcomes, (
        f"the audited child reported no test at all (rc={audit.rc}); an "
        f"empty session proves nothing:\n{audit.output[-3000:]}")
    problems = []
    for nodeid in sorted(FORMER_WRITERS):
        got = audit.outcomes.get(nodeid)
        if got != "passed":
            problems.append(
                f"{nodeid} did not PASS under the audit ({got or 'not run'})")
        wrote = sorted({f"{e['event']} {e['path']}" for e in audit.events
                        if e["test"].split(" ")[0] == nodeid})
        if wrote:
            problems.append(
                f"{nodeid} wrote into the checkout it tests: "
                f"{'; '.join(wrote)} (on origin/main 76a277544 it "
                f"{FORMER_WRITERS[nodeid]})")
    collected = sorted({f"{e['event']} {e['path']}" for e in audit.events
                        if not e["test"]})
    if collected:
        problems.append("collecting the former writers wrote into the "
                        "checkout: " + "; ".join(collected))
    assert not problems, (
        "every concurrent worker listing these paths races the writer:\n  "
        + "\n  ".join(problems)
        + (f"\nchild rc={audit.rc}\n{audit.output[-3000:]}"
           if any("did not PASS" in p for p in problems) else ""))
