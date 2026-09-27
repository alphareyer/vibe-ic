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
    3. a `measures` test under -n 16      -> NOT_MEASURED (with the numbers)
       the same failure serial and quiet  -> FAIL
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
    sh_dir = os.path.dirname(os.path.realpath("/bin/sh"))
    rc, out = _session(tmp_path, body, path=f"{_empty_bin(tmp_path)}:{sh_dir}")
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


def test_a_marked_measurement_under_many_workers_is_not_measured(tmp_path):
    rc, out = _session(tmp_path, _MEASURES, load=0.0,
                       env_extra={"PYTEST_XDIST_WORKER_COUNT": "16"})
    assert _counts(out)["NOT_MEASURED"] == 1 and _counts(out)["FAIL"] == 0, out
    [line] = _state_lines(out, "NOT_MEASURED")
    assert "xdist workers=16 > 1" in line and "load1=0.00" in line, line
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
