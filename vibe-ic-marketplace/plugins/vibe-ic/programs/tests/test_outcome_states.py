"""Outcome states: one run is the verdict, and only a plain FAIL is red (owner, 2026-09-27).

Every claim is proved by a one-file pytest session run in a child process with
the real plugin loaded (``-p _outcome_states``), and every conversion has its
paired control: the same failure under the conditions that make it a real
failure must stay FAIL. A converter without that control could be a converter
that paints every red grey, which is the one thing this tier must never do.

    1. a missing tool on the host         -> NOT_VERIFIED (host, tool, remedy)
       the tool present, a real assert    -> FAIL
    2. ImageNotResolvable from _eda_pin   -> NOT_VERIFIED
       the same exception planted by test -> FAIL
    3. a `measures` test on a loaded host -> NOT_MEASURED (with the numbers)
       the same failure serial and quiet  -> FAIL
       the same failure -n 16 but quiet   -> FAIL (workers are never a cause)
       an UNMARKED test under -n 16       -> FAIL
    4. a `bookkeeping` stale count        -> BOOKKEEPING (stated vs actual)
       a property assert in that test     -> FAIL
    5. VIBEIC_REQUIRE_EDA_VERIFICATION=1  -> the NOT_VERIFIED case is red
"""
from __future__ import annotations

import os
import re
import socket
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _outcome_states as OS  # noqa: E402
import not_verified_tier as NV  # noqa: E402

import _progress_run as _pr  # noqa: E402

import pytest  # noqa: E402

#: This file's assertions quote the child's "`yosys` is not on PATH"; on a host
#: without yosys, a regression here would otherwise be converted into the very
#: state it is testing. Measured: the drop-the-presence-check mutation read
#: "17 passed, 1 skipped" until this mark was added.
pytestmark = pytest.mark.outcome_state_exempt(
    "the subject is the outcome-state tier itself")

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"

#: A conftest for the child that pins the host's load reading, so the "quiet"
#: and "loaded" arms do not depend on what else this machine is doing.
_LOAD_CONFTEST = "import os\nos.getloadavg = lambda: ({load}, {load}, {load})\n"


def _session(tmp_path, body, *, path=None, env_extra=None, load=None):
    """Run one probe file under the real plugin; return (rc, output)."""
    test = tmp_path / "test_probe.py"
    test.write_text("import os, subprocess, sys\nimport pytest\n"
                    f"sys.path.insert(0, {str(PROGRAMS)!r})\n" + body)
    if load is not None:
        (tmp_path / "conftest.py").write_text(_LOAD_CONFTEST.format(load=load))
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROGRAMS)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    for key in (NV.REQUIRE_ENV, "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_WORKER",
                "VIBEIC_EDA_IMAGE", "IIC_EDA_IMAGE"):
        env.pop(key, None)
    if path is not None:
        env["PATH"] = path
    env.update(env_extra or {})
    res = _pr.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
         "-p", "not_verified_tier", "-p", "_outcome_states", str(test)],
        capture_output=True, text=True, cwd=str(tmp_path), env=env)
    return res.returncode, res.stdout + res.stderr


def _counts(out):
    m = re.search(r"^OUTCOME_STATES FAIL=(\d+) NOT_VERIFIED=(\d+) "
                  r"NOT_MEASURED=(\d+) BOOKKEEPING=(\d+)$", out, re.M)
    assert m, f"no parseable count line in the session output:\n{out}"
    return dict(zip(("FAIL", "NOT_VERIFIED", "NOT_MEASURED", "BOOKKEEPING"),
                    map(int, m.groups())))


def _state_lines(out, state):
    return [l for l in out.splitlines() if l.startswith(f"OUTCOME_STATE {state} ")]


def _empty_bin(tmp_path, *tools):
    """A PATH directory holding exactly *tools* (each a shim that exits 0)."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for tool in tools:
        shim = d / tool
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return str(d)


# ---------------------------------------------------------------------------
# 1. a missing tool
# ---------------------------------------------------------------------------
_RUNS_YOSYS = ("def test_synth():\n"
               "    subprocess.run(['yosys', '-V'], check=True)\n"
               "    assert 1 == 2, 'the synthesised netlist is wrong'\n")


def test_a_missing_tool_is_not_verified_naming_host_tool_and_remedy(tmp_path):
    rc, out = _session(tmp_path, _RUNS_YOSYS, path=_empty_bin(tmp_path))
    assert _counts(out) == {"FAIL": 0, "NOT_VERIFIED": 1, "NOT_MEASURED": 0,
                            "BOOKKEEPING": 0}, out
    [line] = _state_lines(out, "NOT_VERIFIED")
    assert f"host {socket.gethostname()}" in line, line
    assert "`yosys` is not on PATH" in line, line
    assert "remedy: run inside the EDA image with tools/ci/run_suite_in_eda_image.sh" in line
    assert rc == 0, out


def test_the_tool_present_and_a_real_failure_stays_fail(tmp_path):
    rc, out = _session(tmp_path, _RUNS_YOSYS, path=_empty_bin(tmp_path, "yosys"))
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_VERIFIED"] == 0, out
    assert rc == 1, out


def test_a_failure_that_merely_mentions_the_tool_stays_fail_when_it_is_present(tmp_path):
    """The text names yosys as missing, but the session's PATH has it: FAIL."""
    body = ("def test_x():\n"
            "    raise FileNotFoundError(2, 'No such file or directory', 'yosys')\n")
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path, "yosys"))
    assert _counts(out)["FAIL"] == 1, out
    assert rc == 1, out


def test_a_test_that_empties_path_itself_is_not_the_host(tmp_path):
    """Presence is asked of the SESSION's PATH: a test that monkeypatches PATH
    to exercise its own missing-tool branch, and fails, is a FAIL."""
    body = ("def test_x(monkeypatch):\n"
            "    monkeypatch.setenv('PATH', '/nonexistent')\n"
            "    subprocess.run(['yosys', '-V'], check=True)\n")
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path, "yosys"))
    assert _counts(out)["FAIL"] == 1, out


def test_a_shell_command_not_found_names_the_tool(tmp_path):
    body = ("def test_x():\n"
            "    p = subprocess.run(['sh', '-c', 'iverilog -V'], capture_output=True, text=True)\n"
            "    assert p.returncode == 0, p.stderr\n")
    # PATH holds `sh` and nothing else: a directory with one link to the real
    # shell. `dirname(realpath('/bin/sh'))` is /usr/bin, and on a host with
    # the Ubuntu `iverilog` package that directory holds the very tool this
    # test needs absent (MEASURED: 8HD-8, stack39 census) -- the inner test
    # then passes and nothing is classified.
    sh_only = tmp_path / "sh-only"
    sh_only.mkdir()
    (sh_only / "sh").symlink_to(os.path.realpath("/bin/sh"))
    rc, out = _session(tmp_path, body, path=f"{_empty_bin(tmp_path)}:{sh_only}")
    assert _counts(out)["NOT_VERIFIED"] == 1, out
    assert "`iverilog` is not on PATH" in _state_lines(out, "NOT_VERIFIED")[0]


# ---------------------------------------------------------------------------
# 2. no resolvable image
# ---------------------------------------------------------------------------
def test_image_not_resolvable_from_the_program_is_not_verified(tmp_path):
    body = ("def test_x():\n"
            "    import _eda_pin\n"
            "    _eda_pin.resolved_image_digest({})\n")
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path))
    assert _counts(out)["NOT_VERIFIED"] == 1, out
    [line] = _state_lines(out, "NOT_VERIFIED")
    assert "no resolvable vibeic-eda image (docker is absent)" in line, line
    assert "IMAGE_NOT_RESOLVABLE" in line and "remedy:" in line, line
    assert rc == 0, out


def test_image_not_resolvable_planted_by_the_test_stays_fail(tmp_path):
    """A test that raises it ITSELF is exercising a fixture, not the host."""
    body = ("def test_x():\n"
            "    import _eda_pin\n"
            "    raise _eda_pin.ImageNotResolvable(['planted'])\n")
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path))
    assert _counts(out)["FAIL"] == 1, out
    assert rc == 1, out


# ---------------------------------------------------------------------------
# 3. measurements
# ---------------------------------------------------------------------------
_MEASURES = ("@pytest.mark.measures\n"
             "def test_linear():\n"
             "    assert 40.0 < 15.0, 'cost 40.0x for a 6x netlist'\n")
_UNMARKED = ("def test_linear():\n"
             "    assert 40.0 < 15.0, 'cost 40.0x for a 6x netlist'\n")


def test_a_marked_measurement_under_many_workers_on_a_quiet_host_stays_fail(tmp_path):
    """R-0929-ENV-AT-RUNTIME: the worker count is never a cause on its own.
    This test used to assert the opposite (-n 16 at load 0 -> NOT_MEASURED);
    that rule let two reverse mutations read non-red at -n 2 on a quiet host."""
    rc, out = _session(tmp_path, _MEASURES, load=0.0,
                       env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert "cost 40.0x for a 6x netlist" in out, out
    assert rc == 1, out


def test_a_marked_measurement_under_many_workers_and_load_is_not_measured(tmp_path):
    load = 64.0 * (os.cpu_count() or 1)
    rc, out = _session(tmp_path, _MEASURES, load=load,
                       env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["NOT_MEASURED"] == 1 and _counts(out)["FAIL"] == 0, out
    [line] = _state_lines(out, "NOT_MEASURED")
    assert "1-min load per core 64.00 > 0.5" in line, line
    assert "xdist workers=16" in line, line
    assert "xdist workers=16 > 1" not in line, line
    assert "cost 40.0x for a 6x netlist" in line, line
    assert rc == 0, out


def test_a_marked_measurement_under_load_is_not_measured(tmp_path):
    load = 64.0 * (os.cpu_count() or 1)
    rc, out = _session(tmp_path, _MEASURES, load=load)
    [line] = _state_lines(out, "NOT_MEASURED")
    assert "1-min load per core 64.00 > 0.5" in line, line


def test_the_same_failure_serial_and_quiet_stays_fail(tmp_path):
    rc, out = _session(tmp_path, _MEASURES, load=0.0)
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert rc == 1, out


def test_an_unmarked_failure_under_many_workers_stays_fail(tmp_path):
    rc, out = _session(tmp_path, _UNMARKED, load=0.0,
                       env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert rc == 1, out


# ---------------------------------------------------------------------------
# 4. bookkeeping
# ---------------------------------------------------------------------------
_BOOK = ("@pytest.mark.bookkeeping(regenerate='python3 gen_counts.py',\n"
         "                         match=r'is stale')\n"
         "def test_count():\n"
         "    assert {cond}, {msg!r}\n")


def test_a_stale_count_is_bookkeeping_with_stated_vs_actual(tmp_path):
    rc, out = _session(tmp_path, _BOOK.format(
        cond="12 == 13", msg="README.md is stale: states 12 programs, the tree has 13"))
    assert _counts(out)["BOOKKEEPING"] == 1 and _counts(out)["FAIL"] == 0, out
    [line] = _state_lines(out, "BOOKKEEPING")
    assert "stated vs actual: README.md is stale: states 12 programs, the tree has 13" in line
    assert "regenerate: python3 gen_counts.py" in line, line
    assert rc == 0, out


def test_a_property_failure_in_a_bookkeeping_test_stays_fail(tmp_path):
    """`match` confines the state to the assertion that IS the stated count."""
    rc, out = _session(tmp_path, _BOOK.format(
        cond="False", msg="the gate did NOT refuse a stale index"))
    assert _counts(out)["FAIL"] == 1 and _counts(out)["BOOKKEEPING"] == 0, out
    assert rc == 1, out


def test_an_unmarked_stale_count_stays_fail(tmp_path):
    rc, out = _session(tmp_path, "def test_count():\n"
                                 "    assert 12 == 13, 'README.md is stale'\n")
    assert _counts(out)["FAIL"] == 1, out


# ---------------------------------------------------------------------------
# 5. the landing contract is unchanged
# ---------------------------------------------------------------------------
def test_require_verification_makes_the_not_verified_case_red(tmp_path):
    rc, out = _session(tmp_path, _RUNS_YOSYS, path=_empty_bin(tmp_path),
                       env_extra={NV.REQUIRE_ENV: "1"})
    assert _counts(out)["NOT_VERIFIED"] == 1, out
    assert rc != 0 and "REFUSES to be green" in out, out


def test_a_declared_skip_not_verified_is_counted_in_the_same_block(tmp_path):
    rc, out = _session(tmp_path, "from not_verified_tier import skip_not_verified\n"
                                 "def test_x():\n"
                                 "    skip_not_verified('image out of reach', 'pull it')\n")
    assert _counts(out)["NOT_VERIFIED"] == 1, out


# ---------------------------------------------------------------------------
# the markers are declared on the tests the brief names
# ---------------------------------------------------------------------------
def _marked(path, func, mark):
    import ast
    tree = ast.parse((Path(__file__).parent / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return [d for d in node.decorator_list
                    if mark in ast.unparse(d)]
    raise AssertionError(f"{path}::{func} not found")


def test_the_named_measuring_and_bookkeeping_tests_carry_their_marks():
    observed = {
        (p, f, m): bool(_marked(p, f, f"mark.{m}")) for p, f, m in (
            ("test_v0_3_37_issue548_pnr_timeouts.py",
             "test_reset_dependency_check_pattern_b_is_linear_not_quadratic", "measures"),
            ("test_pytest_per_file_junit.py",
             "test_silent_pytest_boundaries_keep_a_long_session_alive", "measures"),
            ("test_program_inventory_no_drift.py",
             "test_the_writer_is_idempotent_and_touches_nothing_that_agrees", "bookkeeping"),
            ("test_issue538_merge_gate_covers_ci_hygiene.py",
             "test_a_new_program_without_a_regenerated_index_is_refused", "bookkeeping"),
            ("test_flow_matrix_census_freshness.py",
             "test_the_census_block_is_fresh", "bookkeeping"),
            ("test_flow_matrix_census_freshness.py",
             "test_the_published_total_equals_the_live_census", "bookkeeping"),
        )}
    assert [k for k, v in observed.items() if not v] == [], observed


def test_a_setup_error_counts_as_fail(tmp_path):
    rc, out = _session(tmp_path, "@pytest.fixture\n"
                                 "def broken():\n"
                                 "    raise RuntimeError('fixture broke')\n"
                                 "def test_x(broken):\n"
                                 "    pass\n")
    assert _counts(out)["FAIL"] == 1, out
    assert rc == 1, out


def test_an_exempt_test_reports_its_raw_failure(tmp_path):
    body = ("@pytest.mark.outcome_state_exempt('subject: yosys absence')\n" + _RUNS_YOSYS)
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path))
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_VERIFIED"] == 0, out


# ---------------------------------------------------------------------------
# 2b. the container runs other bytes than the pin (fleet upgraded mid-run)
# ---------------------------------------------------------------------------
_MISMATCH_TEXT = (
    "ENV_REFUSED: analog_a3_netlist_emit: CONTAINER_IMAGE_MISMATCH: container "
    "vibeic-eda runs sha256:7a01d48e, but the pinned runtime is "
    "ghcr.io/vibeic/vibeic-eda@sha256:93d88e9e; required sha256:93d88e9e, "
    "found sha256:7a01d48e")


def test_a_confirmed_container_mismatch_is_not_verified_naming_both_images():
    asked = []

    def probe(container):
        asked.append(container)
        return "MISMATCH", _MISMATCH_TEXT.split("emit: ", 1)[1], container

    reason = OS.classify_container_mismatch(AssertionError(_MISMATCH_TEXT), probe)
    assert asked == ["vibeic-eda"], asked
    assert reason.startswith("NOT_VERIFIED: "), reason
    for part in ("container `vibeic-eda`", "runs sha256:7a01d48e",
                 "@sha256:93d88e9e", "remedy: recycle `vibeic-eda` to the resolved image",
                 "re-run once the container and the pin agree"):
        assert part in reason, (part, reason)


def test_a_container_that_matches_now_keeps_the_fail():
    for state in ("MATCH", "UNREADABLE"):
        assert OS.classify_container_mismatch(
            AssertionError(_MISMATCH_TEXT), lambda c, s=state: (s, "", c)) is None, state


def test_a_refusal_naming_a_container_that_is_not_there_stays_fail(tmp_path):
    """End to end through the LIVE pin check: nothing named that exists, so the
    refusal is not a fact about this host and the FAIL stands."""
    body = ("def test_x():\n"
            "    import _container_exec\n"
            "    raise _container_exec.ContainerImageMismatch(\n"
            "        'CONTAINER_IMAGE_MISMATCH: container t129-no-such-container '\n"
            "        'runs sha256:00, but the pinned runtime is x@sha256:11')\n")
    rc, out = _session(tmp_path, body)
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_VERIFIED"] == 0, out
    assert rc == 1, out


def test_a_truncated_container_name_asks_the_default_container():
    """A repr cut the name ("vibeic-ed..."): the DEFAULT container is asked, and
    only its confirmed MISMATCH converts."""
    text = ("('A3_netlist_gen', 'NOT_MEASURED', 'ENV_REFUSED: CONTAINER_IMAGE_MISMATCH: "
            "container vibeic-ed...db36dd; required sha256:7a01d48eb')")
    asked = []

    def probe(container):
        asked.append(container)
        return "MISMATCH", "container vibeic-eda runs sha256:93d8", "vibeic-eda"

    reason = OS.classify_container_mismatch(AssertionError(text), probe)
    assert asked == [""] and "container `vibeic-eda`" in reason, (asked, reason)
    assert OS.classify_container_mismatch(
        AssertionError(text), lambda c: ("MATCH", "", "vibeic-eda")) is None


def test_text_without_the_refusal_code_never_asks_docker():
    asked = []
    OS.classify_container_mismatch(AssertionError("container vibeic-eda runs fine"),
                                   lambda c: asked.append(c) or ("MISMATCH", "", c))
    assert asked == [], asked


# ---------------------------------------------------------------------------
# 6. at the site: require_tools / skip_if_not_measurable (owner 2026-09-29)
# ---------------------------------------------------------------------------
_REQUIRES = ("import _outcome_states as S\n"
             "def test_x():\n"
             "    S.require_tools({tools})\n"
             "    assert 1 == 2, 'the tool half ran and found a real defect'\n")


def test_require_tools_absent_is_not_verified_naming_host_and_tool(tmp_path):
    rc, out = _session(tmp_path, _REQUIRES.format(tools="'yosys'"),
                       path=_empty_bin(tmp_path))
    assert _counts(out) == {"FAIL": 0, "NOT_VERIFIED": 1, "NOT_MEASURED": 0,
                            "BOOKKEEPING": 0}, out
    [line] = _state_lines(out, "NOT_VERIFIED")
    assert f"host {socket.gethostname()}" in line, line
    assert "`yosys` is not on PATH" in line, line
    assert "remedy: run inside the EDA image with tools/ci/run_suite_in_eda_image.sh" in line
    assert rc == 0, out


def test_require_tools_present_runs_the_test_and_its_failure_stays_fail(tmp_path):
    rc, out = _session(tmp_path, _REQUIRES.format(tools="'yosys'"),
                       path=_empty_bin(tmp_path, "yosys"))
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_VERIFIED"] == 0, out
    assert "the tool half ran and found a real defect" in out, out
    assert rc == 1, out


def test_require_tools_names_only_the_absent_ones(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    body = _REQUIRES.format(tools="'iverilog', 'vvp'")
    rc, out = _session(one, body, path=_empty_bin(one, "iverilog"))
    [line] = _state_lines(out, "NOT_VERIFIED")
    assert "`vvp` is not on PATH" in line and "`iverilog`" not in line, line
    rc, out = _session(two, body, path=_empty_bin(two))
    [line] = _state_lines(out, "NOT_VERIFIED")
    assert "`iverilog`, `vvp` are not on PATH" in line, line


def test_require_tools_asks_the_search_path_the_site_names(tmp_path):
    """A program that prepends its own directories is asked about THAT path:
    the tool found there runs, so its failure is a FAIL, not NOT_VERIFIED."""
    extra = tmp_path / "extra"
    extra.mkdir()
    body = ("import _outcome_states as S\n"
            "def test_x():\n"
            "    S.require_tools('yosys', path=os.environ['EXTRA'] + os.pathsep\n"
            "                    + os.environ['PATH'])\n"
            "    assert 1 == 2, 'ran with the prepended yosys'\n")
    rc, out = _session(tmp_path, body, path=_empty_bin(tmp_path),
                       env_extra={"EXTRA": _empty_bin(extra, "yosys")})
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_VERIFIED"] == 0, out


_CEILING = ("import _outcome_states as S\n"
            "def test_x():\n"
            "    S.skip_if_not_measurable('still running at the 55s ceiling',\n"
            "                             progress=S.{progress})\n"
            "    pytest.fail('still running at the 55s ceiling')\n")
_LOADED = 64.0 * (os.cpu_count() or 1)


def test_a_ceiling_branch_under_many_workers_on_a_quiet_host_stays_fail(tmp_path):
    """The M9b / M10 shape: -n 2 (here 16) on a quiet host is a FAIL. The worker
    count alone used to make it NOT_MEASURED (review_wave58 ENVGUARDS)."""
    rc, out = _session(tmp_path, _CEILING.format(progress="PROGRESS_UNOBSERVABLE"),
                       load=0.0, env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert rc == 1, out


def test_a_ceiling_branch_under_load_is_not_measured(tmp_path):
    rc, out = _session(tmp_path, _CEILING.format(progress="PROGRESS_UNOBSERVABLE"),
                       load=_LOADED)
    [line] = _state_lines(out, "NOT_MEASURED")
    assert "1-min load per core 64.00 > 0.5" in line, line
    assert "slow and stuck cannot be told apart at this branch" in line, line
    assert "still running at the 55s ceiling" in line, line
    assert rc == 0, out


def test_a_ceiling_branch_that_saw_slow_progress_under_load_is_not_measured(tmp_path):
    rc, out = _session(tmp_path, _CEILING.format(progress="PROGRESS_SLOW"),
                       load=_LOADED)
    [line] = _state_lines(out, "NOT_MEASURED")
    assert "the subject was seen advancing, just not finishing" in line, line


def test_a_ceiling_branch_that_saw_no_progress_stays_fail_under_any_load(tmp_path):
    """A subject SEEN doing nothing is a hang: load slows work, it does not stop
    it. Loaded host and many workers together still leave it a FAIL."""
    rc, out = _session(tmp_path, _CEILING.format(progress="PROGRESS_NONE"),
                       load=_LOADED, env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert rc == 1, out


def test_a_ceiling_branch_serial_and_quiet_stays_fail(tmp_path):
    rc, out = _session(tmp_path, _CEILING.format(progress="PROGRESS_UNOBSERVABLE"),
                       load=0.0)
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out
    assert rc == 1, out


def test_the_conditions_sampled_at_the_ceiling_are_the_ones_judged(tmp_path):
    """A site samples the load AT its ceiling, before it kills anything; the
    helper judges that sample, not whatever the host reads a moment later."""
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    body = ("import _outcome_states as S\n"
            "def test_x():\n"
            "    S.skip_if_not_measurable('ceiling', progress=S.PROGRESS_UNOBSERVABLE,\n"
            "        conditions={{'workers': 1, 'load1': {l}, 'cores': 1,\n"
            "                    'load_per_core': {l}}})\n"
            "    pytest.fail('ceiling')\n")
    rc, out = _session(one, body.format(l=9.0), load=0.0)
    assert _counts(out)["NOT_MEASURED"] == 1, out
    rc, out = _session(two, body.format(l=0.1), load=_LOADED)
    assert _counts(out)["FAIL"] == 1 and _counts(out)["NOT_MEASURED"] == 0, out


def test_a_site_must_say_what_progress_it_observed():
    """No default: a site that forgets to state its evidence does not get the
    permissive reading; a misspelt kind is refused rather than read as 'slow'."""
    with pytest.raises(TypeError):
        OS.skip_if_not_measurable("ceiling")  # type: ignore[call-arg]
    cond = {"workers": 1, "load1": 64.0, "cores": 1, "load_per_core": 64.0}
    with pytest.raises(ValueError):
        OS.classify_not_measured(cond, "ceiling", progress="stuck")
    assert OS.classify_not_measured(cond, "c", progress=OS.PROGRESS_NONE) is None
    quiet = dict(cond, workers=16, load1=0.0, load_per_core=0.0)
    assert OS.classify_not_measured(quiet, "c") is None


def _calls(path, func, callee):
    import ast
    tree = ast.parse((Path(__file__).parent / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return any(isinstance(c, ast.Call)
                       and getattr(c.func, "attr", getattr(c.func, "id", "")) == callee
                       for c in ast.walk(node))
    raise AssertionError(f"{path}::{func} not found")


def test_the_named_sites_report_their_state_during_the_run():
    """The sites the 2026-09-29 census found failing (or silently passing) for
    a missing tool or a load-bound ceiling each call the site helper."""
    sites = (
        ("test_formal_die_chiptop_core_binding.py", "_require_yosys", "require_tools"),
        ("test_organic_20260704_rcvar_whitebox_flat.py",
         "test_flat_result_compiles_and_whitebox_signal_visible", "require_tools"),
        ("test_v1_11_78_alias_wrapper_hidden_from_verilator_lint.py",
         "test_iverilog_still_binds_the_alias_top", "require_tools"),
        ("test_v1_11_78_alias_wrapper_hidden_from_verilator_lint.py",
         "test_verilator_lint_is_clean_with_the_guard", "require_tools"),
        ("test_v1_2_47_alias_param_forward.py",
         "test_param_aliased_completion_compiles", "require_tools"),
        ("test_v1_2_47_alias_param_forward.py",
         "test_nonparam_aliased_completion_still_compiles", "require_tools"),
        ("test_v1_2_48_alias_json_unwrap.py",
         "test_json_wrapped_completion_compiles_under_iverilog", "require_tools"),
        ("test_v1_2_harness_toplevel_alias.py",
         "test_aliased_completion_compiles", "require_tools"),
        ("test_v1_3_88_issue119_chip_top_reemit_pull_restore.py",
         "test_reemit_restores_pull_and_design_resets", "require_tools"),
        ("test_cvdp_gate_alias_compliance.py",
         "test_wrapper_correctness_from_prompt_name", "require_tools"),
        ("test_deterministic_rtl_dispatcher.py",
         "test_cli_generates_and_compiles", "require_tools"),
        ("test_v1_3_85_chip_top_vl_tri_outermost.py",
         "test_autoemit_moves_pull_to_outermost_face_end_to_end", "require_tools"),
        ("test_die_density_keepout_census_runs_in_openroad.py", "_route",
         "probe_skip_reason"),
        ("test_landing_merge_verdict.py",
         "_assert_interruption_cleans_every_parallel_arm", "skip_if_not_measurable"),
        ("test_issue1129_gatekeeper_prepare_landing.py",
         "test_the_real_program_runs_against_this_repo_and_honours_its_boundary",
         "skip_if_not_measurable"),
    )
    missing = [s for s in sites if not _calls(*s)]
    assert missing == [], missing


def _keywords_of(path, func, callee):
    import ast
    tree = ast.parse((Path(__file__).parent / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return [sorted(k.arg for k in c.keywords) for c in ast.walk(node)
                    if isinstance(c, ast.Call)
                    and getattr(c.func, "attr", getattr(c.func, "id", "")) == callee]
    raise AssertionError(f"{path}::{func} not found")


def test_every_ceiling_site_states_its_progress_evidence_and_its_ceiling_sample():
    """R-0929-ENV-AT-RUNTIME at the sites: each ceiling/stall call passes what
    it observed (`progress=`) and the load it read AT the ceiling
    (`conditions=`), and the evidence each one has is the evidence it uses:
    issue1129's Stalled maps to PROGRESS_NONE, and the landing helper FAILs a
    cleanup that STARTED, and a verifier that created worktrees after the
    signal, before it can reach the ceiling helper."""
    sites = (("test_landing_merge_verdict.py",
              "_assert_interruption_cleans_every_parallel_arm", 2),
             ("test_issue1129_gatekeeper_prepare_landing.py",
              "test_the_real_program_runs_against_this_repo_and_honours_its_boundary", 1))
    for path, func, n in sites:
        kws = _keywords_of(path, func, "skip_if_not_measurable")
        assert kws == [["conditions", "progress"]] * n, (path, kws)
    src = (Path(__file__).parent / "test_issue1129_gatekeeper_prepare_landing.py").read_text()
    assert "states.PROGRESS_NONE if seen" in src
    land = (Path(__file__).parent / "test_landing_merge_verdict.py").read_text()
    body = land[land.index("def _assert_interruption_cleans_every_parallel_arm"):]
    body = body[:body.index("\ndef ")]
    last_skip = body.rindex("states.skip_if_not_measurable(")
    # A cleanup that STARTED and a verifier that went on AFTER the signal
    # (later worktrees) are observed behaviour: both FAIL before the helper.
    for evidence in ("if still_running and started:",
                     "if still_running and len(after_signal) >= 2:"):
        assert body.index(evidence) < last_skip, evidence
