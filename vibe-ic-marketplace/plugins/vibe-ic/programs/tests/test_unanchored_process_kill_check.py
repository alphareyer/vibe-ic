"""tests/test_unanchored_process_kill_check.py — the ratchet that keeps a
pattern-based process kill out of the shipped runners, and the identity reap
that replaced it.

MEASURED 2026-08-27. `_docker_watchdog` and `phase3_one_shot_runner` each
reaped a stalled tool with `pkill -TERM/-KILL -f <marker>` — no `-x`, no uid,
no pid, no pgid. `marker` is a path already in the tool's argv, and every run
on a host execs into ONE shared long-lived container, so the pattern matched a
DIFFERENT run's healthy tool and SIGTERMed it. Signature: `rc=143 with ZERO
test failures`, three times in one night (85 s, 17 min, 46 min), no cgroup OOM
in dmesg. Downstream, `lec_run` did not carry 143 in its container-timeout set,
so the stray SIGTERM became a hard FAIL — a healthy design booked as a PROVEN
NON-EQUIVALENCE.

The gate below is verified in BOTH directions: it must go RED on the exact
shipped defect and GREEN on the tree that replaced it. A ratchet only tested on
the good tree cannot tell "detector works" from "detector never fires".
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import unanchored_process_kill_check as GATE  # noqa: E402
import _docker_watchdog as DW  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402

# The two reapers exactly as they shipped at main 40d0e14c. Kept verbatim so
# the gate is exercised against the real defect, not a paraphrase of it.
SHIPPED_DEFECT_DOCKER_WATCHDOG = '''
import shlex, time
def _kill(_proc, reason):
    q = shlex.quote(marker)
    try:
        docker_exec_raw(container, f"pkill -TERM -f {q}", timeout=15)
        time.sleep(min(term_grace_s, 30))
        docker_exec_raw(container, f"pkill -KILL -f {q}", timeout=15)
    except Exception:
        pass
'''

SHIPPED_DEFECT_PHASE3 = '''
import shlex, time
def _kill(_proc, reason):
    q = shlex.quote(marker)
    try:
        _docker_exec_raw(container, f"pkill -TERM -f {q}", timeout=15)
        time.sleep(min(_WATCHDOG_TERM_GRACE_S, 30))
        _docker_exec_raw(container, f"pkill -KILL -f {q}", timeout=15)
    except Exception:
        pass
'''


# ── the ratchet, RED pole ────────────────────────────────────────────────
@pytest.mark.parametrize("src,label", [
    (SHIPPED_DEFECT_DOCKER_WATCHDOG, "_docker_watchdog"),
    (SHIPPED_DEFECT_PHASE3, "phase3_one_shot_runner"),
])
def test_gate_is_red_on_the_shipped_defect(src, label):
    """Both shipped reapers must be caught — fixing one and leaving the other
    is how a duplicated defect survives its own fix."""
    hits = GATE.scan_source(src)
    assert hits, ("%s reaper was NOT flagged — the gate cannot see the very "
                  "defect it exists for" % label)
    assert {r for _l, r, _d in hits} == {"command-string"}


def test_the_x_flag_is_not_an_escape():
    """`-x` matches the whole command line exactly — and the stranger's
    command line IS exactly the same. Stricter pattern, same wrong victim."""
    assert GATE.scan_source('run(f"pkill -x -f {q}")')


def test_argv_form_is_caught():
    """The obvious way around a string scan is an argv list."""
    hits = GATE.scan_source('subprocess.run(["pkill", "-f", pat])')
    assert [r for _l, r, _d in hits] == ["argv-list"]


def test_the_primitive_is_caught_at_any_argv_position():
    """A position-0 rule missed a REAL site: `mcp-eda/test/
    test_exec_timeout_kills_the_tool_in_the_container.py` reaped with

        subprocess.run([_DOCKER, "exec", _CONTAINER, "pkill", "-f", marker])

    argv[0] is `docker`; the pattern kill rides in as argv[3] and lands inside
    the SHARED long-lived container — the same blast radius as the two
    watchdog reapers. An earlier draft of this gate reported that file clean."""
    hits = GATE.scan_source(
        'subprocess.run([D, "exec", C, "pkill", "-f", marker])')
    assert [r for _l, r, _d in hits] == ["argv-list"]


def test_concatenation_form_is_caught():
    hits = GATE.scan_source('subprocess.run("pkill" + " -f " + pat, shell=True)')
    assert any(r == "concatenated" for _l, r, _d in hits)


def test_killall_is_caught_too():
    assert GATE.scan_source('run("killall yosys")')


# ── the ratchet, GREEN pole ──────────────────────────────────────────────
def test_a_mention_in_prose_is_not_an_invocation():
    """A gate that fires on the WORD cannot be written without exempting
    itself, and a gate with an exemption list grows exemptions. This one has
    no exemption list, so it must distinguish naming from doing."""
    assert GATE.scan_source('# we used to pkill -f the marker; never again\n') == []
    assert GATE.scan_source('"""Do not pkill -f a marker."""\n') == []


def test_a_bare_name_in_an_allowlist_is_not_an_invocation():
    """`loop_watchdog_compliance_check.BENIGN_ARGV0` legitimately lists the
    string; listing a command is not running one."""
    assert GATE.scan_source('BENIGN = frozenset({"ps", "pkill", "kill"})') == []


def test_the_gate_passes_its_own_source():
    """It names both primitives in `BANNED`; that must not be an invocation."""
    assert GATE.scan_source(
        (PROG / "unanchored_process_kill_check.py").read_text()) == []


# ── the gate must fail CLOSED ────────────────────────────────────────────
def test_a_class_whose_body_is_only_a_docstring_does_not_blind_the_gate():
    """The exact shape that made an earlier draft of this gate report
    `phase3_one_shot_runner.py` CLEAN while it still held both reapers.

    That draft blanked comments and docstrings out of the SOURCE TEXT and
    re-parsed it; blanking a class whose body was only a docstring left
    `class X:` with no body, the re-parse raised SyntaxError, and the scan
    returned no hits. The analysis now runs on the real AST."""
    src = ('class A:\n    """doc"""\n\n\n'
           'def k(q):\n    run(f"pkill -TERM -f {q}")\n')
    hits = GATE.scan_source(src)
    assert [r for _l, r, _d in hits] == ["command-string"]


def test_an_unparseable_file_is_an_error_not_a_pass(tmp_path):
    """A detector whose failure mode is a PASS is worse than no detector,
    because it is believed."""
    with pytest.raises(GATE.Unscannable):
        GATE.scan_source("def broken(:\n")
    bad = tmp_path / "bad.py"
    bad.write_text('def broken(:\n    "pkill -f x"\n')
    found = GATE.scan_tree(tmp_path)
    assert bad in found
    assert [r for _l, r, _d in found[bad]] == ["unscannable"]


def test_the_cli_reports_an_unparseable_file_as_a_failure(tmp_path):
    (tmp_path / "bad.py").write_text('def broken(:\n    "pkill -f x"\n')
    r = subprocess.run(
        [sys.executable, str(PROG / "unanchored_process_kill_check.py"),
         "--root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "unscannable" in r.stdout


# ── the whole shipped population ─────────────────────────────────────────
def test_no_shipped_program_selects_a_kill_victim_by_pattern():
    found = GATE.scan_tree(PROG)
    assert found == {}, "pattern-based kill reintroduced: %s" % {
        str(p): h for p, h in found.items()}


def test_gate_cli_exits_1_on_a_tree_containing_the_defect(tmp_path):
    """rc must be exactly 1 — a crashing gate also exits non-zero."""
    (tmp_path / "bad.py").write_text(SHIPPED_DEFECT_DOCKER_WATCHDOG)
    r = subprocess.run(
        [sys.executable, str(PROG / "unanchored_process_kill_check.py"),
         "--root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pkill" in r.stdout


def test_gate_cli_exits_0_on_a_clean_tree(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    r = subprocess.run(
        [sys.executable, str(PROG / "unanchored_process_kill_check.py"),
         "--root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── the replacement actually selects by identity ─────────────────────────
def test_reap_command_selects_by_identity_not_by_name():
    cmd = DW.reap_command("/tmp/.vibeic-job-abc.pid", "TERM")
    assert "pkill" not in cmd and "killall" not in cmd
    # reads the stamp, and RE-VALIDATES starttime before signalling anything
    assert "/proc/$1/stat" in cmd
    assert 'VCUR=$(__vic_st "$VPID")' in cmd
    assert '[ "$VCUR" = "$VST" ]' in cmd
    assert "VIBEIC_REAP_SKIP pid_reused" in cmd
    # signals the verified root, its ppid-walked descendants, and its group
    assert 'kill -TERM "$VPID" $VKIDS' in cmd
    assert 'kill -TERM -- "-$VPID"' in cmd


def test_reap_refuses_rather_than_falling_back_to_a_pattern():
    """Every refusal path must END the reap. A fallback to the old selector
    would reinstate the defect on exactly the runs where the stamp failed."""
    cmd = DW.reap_command("/tmp/.vibeic-job-abc.pid", "KILL")
    for reason in ("no_stamp", "unreadable", "bad_pid", "bad_starttime",
                   "already_gone", "pid_reused"):
        assert "VIBEIC_REAP_SKIP %s; exit 0" % reason in cmd, reason


def test_reap_command_rejects_an_arbitrary_signal():
    with pytest.raises(ValueError):
        DW.reap_command("/tmp/x.pid", "HUP; rm -rf /")


def test_the_stamp_records_pid_and_starttime_before_exec():
    pf = DW.new_job_pidfile()
    w = DW.wrap_with_container_timeout("yosys -s x.ys", 3600, pidfile=pf)
    assert w.index("printf") < w.index("exec timeout"), (
        "the identity must be stamped BEFORE the tool replaces the shell")
    assert '"$$"' in w and "__vic_st $$" in w
    assert pf in w


def test_pidfiles_are_unique_per_invocation():
    assert len({DW.new_job_pidfile() for _ in range(200)}) == 200


def test_wrap_without_a_pidfile_is_byte_identical_to_before():
    """Every existing caller keeps its exact command string."""
    assert DW.wrap_with_container_timeout("X", 86400) == (
        "if command -v timeout >/dev/null 2>&1; then "
        "exec timeout --kill-after=5 86395 bash -lc X; "
        "else exec bash -lc X; fi")


def test_reaped_pids_ignores_the_image_login_banner():
    """The vibeic-eda image prints `[INFO] ...` on every login shell, and the
    two runners that reach this code do not set IIC_OSIC_TOOLS_QUIET."""
    assert DW.reaped_pids(
        "[INFO] Final PATH variable: /foo\nVIBEIC_REAP TERM 71 100 \n"
    ) == {71, 100}


# ── the duplicate is gone: ONE implementation, not two ───────────────────
def test_phase3_delegates_the_reap_instead_of_reimplementing_it():
    """ONE producer for the in-container wrap, and NO clock on the supervised
    path. Two byte-equivalent copies of a string is one defect in two files:
    fixing either alone leaves the other live, which is exactly how this one
    survived.

    WHAT THIS TEST USED TO ASSERT, AND WHY IT MOVED (vibe-ic#2099).  It required
    two literals in phase3's source, `_dwd.kill_supervised_job(` and
    `_dwd.wrap_with_container_timeout(`, and then read a `def _kill` closure out
    of that source by index.  All three went stale at once, for two DIFFERENT
    reasons, which is why the red was hard to read:

      * The reap and the closure: v1.18.43 deleted phase3's private `_kill`
        entirely and moved the dispatch to `_docker_watchdog.
        run_docker_supervised`, which does the reap itself.  Delegation became
        MORE complete and the source probe could see LESS of it -- so the
        assertion failed on the tree that satisfied its own docstring best.
      * The wrap: `git show 460a0ffc3` settles where that single call lived --
        the SUPERVISED path, as the outer clock #2051 removed.  So the literal
        went away with the clock, and the assertion was pinning a contract the
        ruling had deliberately retired.

    But the second literal was ALSO, by accident, the only thing in the repo
    standing near a real defect: `_docker_exec_raw` had ALWAYS inlined its own
    copy of that wrap string, byte for byte identical to the helper.  Deleting
    the assertion would have deleted the only pressure on that duplication.  So
    it is not deleted -- it is aimed at the RAW path, where the duplication
    actually was, and paired with the clock property in the place the ruling put
    it.  Nothing here is loosened: the file goes from three source literals to
    one behavioural comparison plus two source guards that each kill a mutation.
    """
    src = (PROG / "phase3_one_shot_runner.py").read_text()

    # (a) ONE PRODUCER, ASKED OF THE RUNNING CODE.  `_docker_exec_raw` must hand
    #     the container the string the shared helper builds -- not a copy of it
    #     that happens to agree today.  Compared against the helper called with
    #     the SAME normalisation the runner applies first, so this is the real
    #     command and not a re-typed guess at it.
    seen = []

    class _CP:
        returncode, stdout, stderr = 0, "", ""

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(R.subprocess, "run",
                   lambda argv, **_kw: (seen.append(list(argv)), _CP())[1])
        mp.setattr(R.shutil, "which", lambda n, *a, **k: None)   # local route:
        mp.setattr(R, "_LOCAL_EXEC_MODE", None, raising=False)   # no host read
        R._docker_exec_raw("cz2099_no_such_container", "yosys -V", timeout=600)
    assert seen, "the raw entry point launched nothing"
    launched = seen[-1][-1]
    assert launched == DW.wrap_with_container_timeout(
        R._tool_status_not_the_log_sinks("yosys -V"), 600), (
        "`_docker_exec_raw` is not building its in-container command through "
        "`_docker_watchdog.wrap_with_container_timeout`: it launched %r"
        % (launched,))

    # (b) AND EXACTLY ONE SHIPPED PROGRAM BUILDS THAT STRING.  (a) passes just
    #     as well against a re-inlined byte-identical copy -- that is precisely
    #     how this duplication survived -- so the PRODUCER SET is asserted, the
    #     same way `test_only_one_module_implements_the_reap` below does it for
    #     the reap.  Keyed on the CONSTRUCTION (`… --kill-after=5 {`, with the
    #     interpolation) and not on the bare literal: `_watchdog.py` and
    #     `watchdog_ceiling_semantics_check.py` both quote a measured `ps` line
    #     containing `timeout --kill-after=5 86395` in their DOCSTRINGS, and a
    #     check that cannot tell prose from code would name them as offenders.
    builders = [f.name for f in sorted(PROG.glob("*.py"))
                if "exec timeout --kill-after=5 {" in f.read_text()]
    assert builders == ["_docker_watchdog.py"], (
        "more than one shipped program builds the container-timeout wrap: %s. "
        "There is one producer, `_docker_watchdog.wrap_with_container_timeout`; "
        "a second copy means the `--kill-after` escalation, the margin and the "
        "no-`timeout`-binary fallback have to be edited twice." % (builders,))
    assert "_dwd.wrap_with_container_timeout(" in src, (
        "phase3 must reach that producer by name")

    # (c) NO CLOCK ON THE SUPERVISED PATH (#2051) -- where the retired literal
    #     used to be, so the property it guarded is kept rather than dropped.
    #     A long tool run is stopped by the progress-stall reap and by nothing
    #     else; the ceiling is a recorded budget.  The RAW path above keeps its
    #     deadline on purpose: a short bounded probe is a different contract.
    dispatched = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(R._dwd, "run_docker_supervised",
                   lambda container, cmd, marker, **kw:
                   (dispatched.append(cmd), (0, "", ""))[1])
        R._docker_exec("cz2099_no_such_container", "openroad -exit r.tcl",
                       timeout=600, marker="r.tcl")
    assert dispatched, "the supervised entry point dispatched nothing"
    assert "timeout" not in dispatched[-1], (
        "an outer clock is back on the supervised path: %r" % (dispatched[-1],))
    assert "--kill-after" not in dispatched[-1], dispatched[-1]

    # (d) AND THE REAP IS STILL NOT REBUILT HERE.  The closure this used to read
    #     by index is gone; what must stay gone is phase3 composing a kill
    #     command of its own.
    assert "pkill" not in src, "phase3 is building its own kill command again"


def test_only_one_module_implements_the_reap():
    impls = [p for p in sorted(PROG.glob("*.py"))
             if "_REAP_TAIL" in p.read_text()]
    assert [p.name for p in impls] == ["_docker_watchdog.py"]


def test_the_mcp_eda_test_tree_is_scanned_and_clean():
    """`mcp-eda/test` is singular, so it is not covered by the skipped `tests`
    name — deliberately. A real pattern kill lived there."""
    mcp = PROG.parent / "mcp-eda"
    if not mcp.is_dir():
        pytest.skip("mcp-eda sub-project not present")
    assert any(p.suffix == ".py" for p in mcp.rglob("*.py")), "nothing scanned"
    assert GATE.scan_tree(mcp) == {}


# ── AND SOMETHING OTHER THAN THIS FILE HAS TO RUN IT ─────────────────────────
#
# MEASURED 2026-08-28. Every assertion above proves the DETECTOR. None of them
# proved that anything points it at the shipped tree, and nothing did:
# `checker_execution_wiring_audit` — itself a blocking hygiene gate — reported
#
#     [FAIL] 1 checker(s) that NOTHING but their own test runs — a fixture the
#            author wrote proves the logic, never the artefacts:
#              unanchored_process_kill_check.py
#
# and exited 1 on `main` at ae5cc4dbf. A checker only its own test runs is the
# weakest runner class there is: the fixtures below can never regress, because
# they are frozen strings in this file, while the tree they are a proxy for
# changes every day. The gate is now dispatched from `repo_hygiene_gates.sh`,
# and these two tests are what keeps that true — deleting the `run` line would
# otherwise only be visible in the audit's own output.
_HYGIENE = PROG.parents[3] / "tools" / "ci" / "repo_hygiene_gates.sh"


def test_the_gate_is_dispatched_from_the_hygiene_set():
    if not _HYGIENE.is_file():
        pytest.skip(f"hygiene gate script not present at {_HYGIENE}")
    src = _HYGIENE.read_text()
    line = [ln for ln in src.splitlines()
            if ln.startswith("run ") and "unanchored_process_kill_check.py" in ln]
    assert len(line) == 1, (
        "unanchored_process_kill_check is not dispatched by a `run` line in "
        "repo_hygiene_gates.sh — it is back to being a checker that only its "
        "own test runs, which is what `checker_execution_wiring_audit` blocks "
        f"on. Matching lines: {line}")
    # `$ROOT`, not `$PLUGIN`: the checker's own docstring records that a real
    # pattern kill lived under `mcp-eda/test`, outside the plugin's programs/.
    assert '--root "$ROOT"' in line[0], (
        f"the gate is wired to a narrower scope than the defect it is for: "
        f"{line[0]}")
    # `run`, not `run_tolerating_uncheckable`: this gate has no input it can
    # fail to resolve — it walks source that is always there — so an rc-2
    # tolerance would only hide a crash.
    assert not line[0].startswith("run_tolerating_uncheckable"), line[0]


def test_the_gate_passes_on_the_tree_it_was_just_wired_to():
    """Wiring a RED gate turns "unverified" into "blocking", which is a
    different repair. This asserts the state that made the wiring safe, and it
    states its denominator: a guard that passes over an empty population
    certifies nothing."""
    root = PROG.parents[3]
    if not (root / "tools" / "ci").is_dir():
        pytest.skip(f"not a full repo checkout at {root}")
    examined = [p for p in GATE.iter_python_files(root)
                if any(b in p.read_text(errors="replace") for b in GATE.BANNED)]
    assert len(examined) >= 5, (
        f"only {len(examined)} file(s) under {root} carry a banned primitive "
        f"at all — the population this gate examines has collapsed, so a PASS "
        f"from it would certify nothing: {[p.name for p in examined]}")
    assert GATE.scan_tree(root) == {}, (
        "the shipped tree trips the gate that is now wired to block on it")
