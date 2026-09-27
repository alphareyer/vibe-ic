"""The landing runtime preflight names what its probe did (FX_PREFLIGHT_UNDER_LOAD).

THE FINDING. In a census shard on a busy host (8HD-4, load 60-85), two tests
of `test_landing_pytest_runtime_preflight.py` failed with
`{"ok": false, "probe_returncode": 2, "reason": "the trusted entry could not
execute and report one synthetic test"}` and nothing else: the result kept
none of the child's output, and the reason is the same sentence for every
outcome but a pass. What the child did could not be read back.

The investigation's hypothesis -- the probe killed as a stall under load --
does not fit that record (a stall never forms a result: it escapes
`preflight()` as `_progress_run.Stalled`), and it did not reproduce: the same
two tests pass under a 1 % cgroup CPU quota (173 s). What the record DOES
show is two disclosure defects, both fixed here:

* the reason and the JSON carry no evidence of which failure it was, so the
  next occurrence names its own cause (the entry's refusal line, the
  runner's last line, a signal, or a stall with the host's load);
* a real stall exited through `exit_undetermined_on_stall` with rc 2 -- the
  REFUSAL's rc -- so a busy host read as a broken runtime. It is now
  NOT_MEASURED, rc 3, with the load.

A genuinely broken entry still refuses with rc 2.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _progress_run as _pr  # noqa: E402
import landing_pytest_runtime_preflight as program  # noqa: E402

PROGRAM = PROGRAMS / "landing_pytest_runtime_preflight.py"


def _fake_programs(tmp_path: Path, body: str) -> Path:
    fake = tmp_path / "programs"
    fake.mkdir()
    (fake / "trusted_pytest_entry.py").write_text(body, encoding="utf-8")
    return fake


@pytest.fixture
def lane(monkeypatch):
    # A lane is set so the probe runs whatever this interpreter can import.
    monkeypatch.setenv(program.HOST_LANE_ENV, "auto")


def test_a_frozen_probe_is_not_measured_with_the_load_not_a_refusal(
        tmp_path, monkeypatch, lane):
    """Starvation taken to its end: a probe child that makes NO progress at
    all (stopped), under a short supervision window. It is not a runtime that
    cannot report, and it must not be called one."""
    monkeypatch.setattr(_pr, "_outer_cache", 2.0)   # a ~1 s stall grace
    fake = _fake_programs(tmp_path, "import os, signal\n"
                                    "os.kill(os.getpid(), signal.SIGSTOP)\n")
    result = program.preflight(programs=fake, python=sys.executable)
    assert result["ok"] is False
    assert result["verdict"] == "NOT_MEASURED", result
    assert "STALLED" in result["reason"] and "load1" in result["reason"]
    assert "could not execute" not in result["reason"]


def test_the_entrys_own_refusal_is_named(tmp_path, lane):
    fake = _fake_programs(tmp_path, "import sys\nsys.stderr.write("
                          "'[NORECORD] trusted pytest entry: pytest resolved "
                          "inside the subject checkout\\n')\nraise SystemExit(2)\n")
    result = program.preflight(programs=fake, python=sys.executable)
    assert ("the entry refused: [NORECORD] trusted pytest entry: pytest "
            "resolved inside the subject checkout") in result["reason"]
    assert result["verdict"] == "REFUSE" and result["probe_returncode"] == 2
    assert "could not execute and report" in result["reason"]
    assert result["probe_tail"]["stderr"]


def test_an_interrupted_runner_is_named_by_its_last_line(tmp_path, lane):
    fake = _fake_programs(tmp_path, "import sys\nprint('!!!! Interrupted: 1 "
                          "error during collection !!!!')\nraise SystemExit(2)\n")
    result = program.preflight(programs=fake, python=sys.executable)
    assert "Interrupted: 1 error during collection" in result["reason"]
    assert result["verdict"] == "REFUSE"


def test_a_probe_killed_from_outside_is_not_measured(tmp_path, lane):
    fake = _fake_programs(tmp_path, "import os, signal\n"
                                    "os.kill(os.getpid(), signal.SIGTERM)\n")
    result = program.preflight(programs=fake, python=sys.executable)
    assert "killed by signal 15" in result["reason"]
    assert result["verdict"] == "NOT_MEASURED"


def test_the_cli_says_not_measured_with_its_own_exit_code(tmp_path):
    """rc 3 for a probe that could not finish looking; rc 2 stays the
    refusal's. Driven through the real CLI with a real stall."""
    fake = _fake_programs(tmp_path, "import os, signal\n"
                                    "os.kill(os.getpid(), signal.SIGSTOP)\n")
    shim = tmp_path / "run.py"
    shim.write_text(
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(PROGRAMS)!r})\n"
        "import _progress_run as pr\npr._outer_cache = 2.0\n"
        f"sys.argv = [{str(PROGRAM)!r}, '--programs', {str(fake)!r}, '--json']\n"
        f"runpy.run_path({str(PROGRAM)!r}, run_name='__main__')\n")
    env = dict(__import__("os").environ, **{program.HOST_LANE_ENV: "auto"})
    cp = subprocess.run([sys.executable, str(shim)], env=env,
                        stdin=subprocess.DEVNULL, capture_output=True,
                        text=True, timeout=300)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["verdict"] == "NOT_MEASURED"


def test_a_broken_entry_still_refuses_with_rc_two(tmp_path):
    fake = _fake_programs(tmp_path, "import sys\nsys.stderr.write("
                          "'[NORECORD] trusted pytest entry: broken\\n')\n"
                          "raise SystemExit(2)\n")
    env = dict(__import__("os").environ, **{program.HOST_LANE_ENV: "auto"})
    cp = subprocess.run([sys.executable, str(PROGRAM), "--programs", str(fake),
                         "--json"], env=env, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, timeout=300)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    payload = json.loads(cp.stdout)
    assert "the entry refused: [NORECORD] trusted pytest entry: broken" in payload["reason"]
    assert payload["verdict"] == "REFUSE"
