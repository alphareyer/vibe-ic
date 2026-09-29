"""The container's login profile may not write into a tool's STDOUT.

`ContainerRunner` enters the EDA container through a LOGIN shell, because that
is where the image puts its tools: PATH, PYTHONPATH, LD_LIBRARY_PATH and the
PDK variables come from the login profile.  The same profile PRINTS.  MEASURED
on vibeic-eda 0.3.85 (`docker run --rm <image> bash -lc 'echo MARK'`):

    [INFO] Final PATH variable: /headless/.local/bin:/foss/tools/bin:...
    [INFO] Final PYTHONPATH variable: /headless/.local/lib/python3.12/...
    MARK

Every `run` / `run_argv` / `run_argv_supervised` caller that parses stdout
then reads the profile's words as its tool's data.  That is how byte-identical
PDK antenna decks were reported as a "deck digest mismatch", how a `*.lyt`
listing grew two phantom technology files, and why at least nine callers each
carry their own `[INFO]`-line filter.  The fix belongs at the one place the
login shell is started, so no caller has to remember to filter.

These tests run the runner's REAL login-shell command on this machine's
`bash`: only `docker exec <container>` is peeled off the argv, everything after
it executes as built.  The fixture profile does what the image's does — puts a
tool on PATH, exports a variable, prints a banner — so each test pins BOTH
halves of the contract at once:

  * stdout is exactly what the tool wrote (a banner byte there is the bug);
  * the tool was found and saw the profile's variable (dropping the login
    shell to get a quiet stdout would break every tool the profile provides).

chip-AGNOSTIC: no design, tool, PDK or image literal is involved; the banner
text is the fixture's own.
"""
import stat
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _klayout_launch as launch  # noqa: E402

TOOL = "vibeic-fixture-login-tool"
PROFILE_VAR = "VIBEIC_FIXTURE_FROM_LOGIN_PROFILE"
BANNER = ("[INFO] Final PATH variable: /fixture/bin\n"
          "[INFO] Final PYTHONPATH variable: /fixture/lib\n")


@pytest.fixture
def login_host(tmp_path, monkeypatch):
    """A 'container' that is this host's login shell under a fixture HOME.

    `bash -l` reads `~/.bash_profile`, so the fixture profile below is what
    the runner's login shell sources.  The tool lives ONLY on the PATH that
    profile exports."""
    home = tmp_path / "home"
    tools = tmp_path / "profile_tools"
    home.mkdir()
    tools.mkdir()
    tool = tools / TOOL
    tool.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s|%s\\n" "$*" "${{{PROFILE_VAR}:-}}" "${{FIXTURE_ENV:-}}"\n'
        'echo "tool-stderr" >&2\n'
        "exit 3\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (home / ".bash_profile").write_text(
        f'export PATH="{tools}:$PATH"\n'
        f"export {PROFILE_VAR}=from-the-login-profile\n"
        + "".join(f"echo {line!r}\n" for line in BANNER.splitlines())
        + "printf 'and a last line with no newline'\n"
        + "echo 'profile-stderr' >&2\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(PROFILE_VAR, raising=False)
    monkeypatch.delenv("BASH_ENV", raising=False)
    monkeypatch.setattr(launch._ce, "docker_exec_argv",
                        lambda container, *rest, opts=(): list(rest))
    monkeypatch.setattr(launch, "_container_mounts", lambda container: [])
    return tmp_path


def _clean(out: str) -> None:
    assert "[INFO]" not in out and "last line with no newline" not in out, (
        "the login profile's own output reached the tool's stdout:\n" + out)


def test_run_argv_stdout_is_the_tools_alone(login_host):
    rc, out, err = launch.ContainerRunner("fixture").run_argv(
        [TOOL, "one arg"], {"FIXTURE_ENV": "a value"}, timeout=60)
    _clean(out)
    assert out == "one arg|from-the-login-profile|a value\n", out
    assert rc == 3, (rc, out, err)
    assert "tool-stderr" in err, err


def test_run_klayout_script_stdout_is_the_tools_alone(login_host):
    script = login_host / "probe.py"
    script.write_text("# fixture\n")
    runner = launch.ContainerRunner("fixture", binary=TOOL, flags=("-b", "-r"))
    rc, out, err = runner.run(script, {"FIXTURE_ENV": "k"}, timeout=60)
    _clean(out)
    assert out == f"-b -r {script}|from-the-login-profile|k\n", out
    assert rc == 3, (rc, out, err)


def test_supervised_run_stdout_is_the_tools_alone(login_host):
    """The supervised route stamps the job's identity in one login shell and
    `exec`s a second one; neither may print into the tool's stdout, and the
    tool must still be the process the stamp names (rc reaches us)."""
    result = launch.ContainerRunner("fixture").run_argv_supervised(
        [TOOL, "supervised"], {"FIXTURE_ENV": "s"},
        stall_grace_s=30, memory_limit_mb=4096)
    _clean(result.out)
    assert result.out == "supervised|from-the-login-profile|s\n", result
    assert result.rc == 3, result
    assert result.outcome == "natural", result


def test_the_profiles_stderr_is_left_where_it_was(login_host):
    """Only stdout is the command's DATA channel.  A profile that complains
    is still heard: its stderr is not redirected, so a broken image profile
    stays diagnosable instead of being silenced along with its banner."""
    _rc, _out, err = launch.ContainerRunner("fixture").run_argv(
        [TOOL], {}, timeout=60)
    assert "profile-stderr" in err, err


def test_the_tool_does_not_inherit_the_parked_descriptor(login_host):
    """The command's stdout is parked on descriptor 3 while the profile runs.
    It must be handed back AND closed before the tool starts: a tool that
    inherited a second copy of the pipe would keep it open behind its own
    stdout, and a tool that uses fd 3 itself would find it taken."""
    rc, out, _err = launch.ContainerRunner("fixture").run_argv(
        ["sh", "-c",
         "if (true >&3) 2>/dev/null; then echo fd3-open; else echo fd3-closed; fi"],
        {}, timeout=60)
    _clean(out)
    assert (rc, out) == (0, "fd3-closed\n"), (rc, out)
