#!/usr/bin/env python3
"""Regression: the vibeic-eda image must resolve EDA tools on a *non-login*
`docker exec` PATH (v0.2.12).

Root cause of the escape: stock iic-osic-tools only puts `/foss/tools/*` on
PATH via `/etc/profile.d/iic-osic-tools-setup.sh`, which runs for LOGIN shells
only. A bare `docker exec <c> <tool>` (and `docker exec <c> bash -c '<tool>'`)
is non-login, so `yosys`/`openroad`/... were "executable file not found in
$PATH" — exactly what a user copy-pasting the INSTALL_GUIDE / README probe hit.
The eda-tools MCP dodged it by prefixing every command with
`export PATH=${TOOLS}/...:$PATH`, but a human running the documented command
could not. The fix bakes the tool dirs into a global `ENV PATH` in the
Dockerfile runtime stage so tools resolve WITHOUT a login shell.

The STATIC tests lock the Dockerfile invariant when its separate tool-image
source is available. LIVE probes on the host retain the historical docker-exec
transport. Inside the EDA test image, they execute native tools with PID 1's
initial environment: the image's global environment before the normal startup
entrypoint adds tool paths. This measures the same non-login PATH contract
without attaching to another owner's container. JUnit names the transport;
a native image-environment result is not a historical docker-exec measurement.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[2] / "tools" / "vibeic-eda" / "Dockerfile"
# The plugin tree ships under vibe-ic-marketplace/plugins/vibe-ic/; the Dockerfile
# lives at repo-root tools/vibeic-eda/. Resolve both layouts.
if not DOCKERFILE.exists():
    # marketplace layout: .../plugins/vibe-ic/mcp-eda/test/ -> up to repo root
    for up in Path(__file__).resolve().parents:
        cand = up / "tools" / "vibeic-eda" / "Dockerfile"
        if cand.exists():
            DOCKERFILE = cand
            break

TOOL_DIRS = ["/foss/tools/bin", "/foss/tools/sak"]
CONTAINER = os.environ.get("EDA_CONTAINER", "vibeic-eda")
_IN_CONTAINER = Path("/.dockerenv").is_file()


def _env_path_line():
    if not DOCKERFILE.exists():
        pytest.skip(f"Dockerfile not found at {DOCKERFILE}")
    for line in DOCKERFILE.read_text().splitlines():
        s = line.strip()
        if s.startswith("ENV PATH=") and "/foss/tools/bin" in s:
            return s
    return None


def test_dockerfile_bakes_tool_dirs_into_global_env_path():
    """A global `ENV PATH=` must carry the tool dirs so non-login exec resolves them."""
    line = _env_path_line()
    assert line is not None, (
        "Dockerfile must set a global `ENV PATH=` containing /foss/tools/bin so a "
        "non-login `docker exec <c> <tool>` resolves EDA tools without a login shell"
    )
    for d in TOOL_DIRS:
        assert d in line, f"global ENV PATH must include {d}: {line!r}"
    # must preserve the inherited PATH, never clobber it
    assert "${PATH}" in line or "$PATH" in line, (
        "ENV PATH must append to the inherited ${PATH}, not replace it"
    )


def test_env_path_is_in_runtime_stage_after_user():
    """The ENV must live in the final runtime stage (after the `USER 1000`
    restore), not in a throwaway builder stage."""
    if not DOCKERFILE.exists():
        pytest.skip(f"Dockerfile not found at {DOCKERFILE}")
    text = DOCKERFILE.read_text()
    idx_env = text.find("ENV PATH=")
    assert idx_env != -1, "no `ENV PATH=` in Dockerfile"
    idx_user = text.rfind("USER 1000", 0, idx_env)
    assert idx_user != -1, "global ENV PATH must come after the runtime `USER 1000` restore"


def _docker_ok():
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["docker", "ps", "--filter", f"name={CONTAINER}", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    return r.returncode == 0 and CONTAINER in r.stdout


def _run_nonlogin(probe, record_property):
    if not _IN_CONTAINER:
        record_property("runtime_transport", "historical-docker-exec")
        return subprocess.run(["docker", "exec", CONTAINER] + probe,
                              capture_output=True, text=True)
    # Without an init frame an entrypoint can patch PATH and exec the test as
    # PID 1; that process's environ is no longer the original OCI environment.
    # A container marker alone must not upgrade that environment to evidence.
    init_name = Path("/proc/1/comm").read_text().strip()
    assert init_name in {"docker-init", "tini"}, (
        "native image-environment probe requires docker --init; "
        f"PID 1 is {init_name!r}, not a preserved init frame"
    )
    record_property("runtime_transport", "native-image-initial-env")
    # The init frame inherits OCI's environment before starting the entrypoint.
    initial = dict(item.split("=", 1) for item in
                   Path("/proc/1/environ").read_bytes().decode().split("\0")
                   if "=" in item)
    assert "PATH" in initial, "container initial environment has no PATH"
    try:
        return subprocess.run(probe, env=initial, capture_output=True, text=True)
    except OSError as exc:
        return subprocess.CompletedProcess(probe, 127, "", str(exc))


@pytest.mark.skipif(not (_IN_CONTAINER or _docker_ok()), reason="docker/container not available")
@pytest.mark.parametrize("probe", [
    ["yosys", "--version"],
    ["openroad", "-version"],
])
def test_bare_docker_exec_resolves_tool(probe, record_property):
    """Resolve the original tool argv with the non-login image environment."""
    r = _run_nonlogin(probe, record_property)
    assert r.returncode == 0, (
        f"non-login {' '.join(probe)} failed "
        f"(rc={r.returncode}): {r.stderr.strip() or r.stdout.strip()}"
    )


@pytest.mark.skipif(not (_IN_CONTAINER or _docker_ok()), reason="docker/container not available")
def test_nonlogin_path_contains_tool_dir(record_property, monkeypatch):
    """Non-login PATH (what `docker exec ... bash -c` sees) must carry /foss/tools/bin."""
    r = _run_nonlogin(["bash", "--noprofile", "--norc", "-c", "echo $PATH"],
                      record_property)
    assert r.returncode == 0
    assert "/foss/tools/bin" in r.stdout, f"non-login PATH lacks /foss/tools/bin: {r.stdout!r}"
    if _IN_CONTAINER:
        original_read = Path.read_text
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "read_text", lambda path, *args, **kwargs:
                           "python3\n" if str(path) == "/proc/1/comm" else
                           original_read(path, *args, **kwargs))
            with pytest.raises(AssertionError, match="requires docker --init"):
                _run_nonlogin(["bash", "-c", "echo $PATH"], record_property)
