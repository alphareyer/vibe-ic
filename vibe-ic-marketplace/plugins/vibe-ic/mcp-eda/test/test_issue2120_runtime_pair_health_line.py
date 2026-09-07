"""`eda_doctor` states the runtime PAIR on its own line, and it is HARD (#2120).

A newly started MCP reported `16/16 checks passed` against an upgraded shared
container. Every one of those sixteen was true, and every one was about TOOL
AVAILABILITY. Whether the general runner would select that container, and
whether it would accept its digest, was asked by nothing — and the answer was
no:

    the installed pin required             sha256:8c5694ab…
    a fresh pull of the published :latest  sha256:1463dac5…
    …whose own version label ALSO read     0.3.48

The dispatcher fanned out, provenance was recorded about a container the pin
does not name, and the batch had to be stopped and excluded. A human reading two
`0.3.48`s next to "16/16 checks passed" had nothing to see.

HARD, deliberately, unlike the memory ceiling and the stale-container line
beside it. Those are conditions an operator may knowingly accept while every
flow still runs correctly. This one says the runner will REFUSE this runtime,
and `allOk` is the field a reader turns into "I can run" — a soft line here
would reproduce the report that was already misread once.

The helper is EXECUTED here, not grepped for: these tests stub the subprocess
and run `_runtimePairIdentity` under node, so they assert what it DOES.
"""
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
INDEX_JS = HERE.parent / "src" / "index.js"

sys.path.insert(0, str(HERE.parents[1] / "programs"))
import _watchdog                                              # noqa: E402

_STALL_GRACE_S = 60
_PIN = "sha256:" + "8c" * 32
_OTHER = "sha256:" + "14" * 32


def _helper_source() -> str:
    """The shipped helper itself. A copy in this file would pass while the
    server did something else, which is the failure this test exists to catch."""
    src = INDEX_JS.read_text(encoding="utf-8")
    start = src.index("const _RUNTIME_PAIR_PY =")
    end = src.index("// ─── Tool: eda_doctor", start)
    body = src[start:end]
    assert "_runtimePairIdentity" in body, body[:200]
    return body


def _run(status: int, stdout: str, stderr: str = "") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available; the helper under test is JavaScript")
    script = textwrap.dedent(f"""
        const VIBE_IC_PROGRAMS_DIR = "/nonexistent-programs-dir";
        const _spawnSync = () => ({json.dumps({"status": status,
                                               "stdout": stdout,
                                               "stderr": stderr})});
        {_helper_source()}
        console.log(JSON.stringify(_runtimePairIdentity()));
    """)
    argv = [node, "-e", script]
    res = _watchdog.run_host_supervised(argv, stall_grace_s=_STALL_GRACE_S)
    assert res.outcome not in ("stalled", "ceiling"), (
        f"the node child made NO forward progress for {_STALL_GRACE_S}s\n"
        f"{res.out}{res.err}")
    run = _watchdog.completed_process(argv, res)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def _answer(ok: bool) -> str:
    line = (f"{'RUNTIME_PAIR_MATCH' if ok else 'RUNTIME_PAIR_MISMATCH'} "
            f"container=vibeic-eda-8c5694abdf5c required={_PIN} "
            f"found={_PIN if ok else _OTHER}")
    if not ok:
        line += " disagreed=container_matches_pin"
    return json.dumps({"ok": ok, "line": line,
                       "disagreed": None if ok else "container_matches_pin"})


# ── the case this exists for ───────────────────────────────────────────────

def test_a_same_version_different_digest_pair_is_reported_as_a_mismatch():
    out = _run(0, _answer(False) + "\n")
    assert out["ok"] is False
    assert "RUNTIME_PAIR_MISMATCH" in out["detail"]


def test_the_line_names_both_digests_and_the_container():
    """A reader told only that something mismatched cannot tell a stale
    container from a mis-set repository, and will re-run it."""
    d = _run(0, _answer(False) + "\n")["detail"]
    assert _PIN in d and _OTHER in d
    assert "vibeic-eda-8c5694abdf5c" in d
    assert "disagreed=container_matches_pin" in d


def test_the_line_says_tool_availability_did_not_answer_this():
    """The exact substitution #2120 reports: sixteen working tools read as
    execution-ready for a runtime the runner refuses."""
    d = _run(0, _answer(False) + "\n")["detail"]
    assert "NOT" in d and "execution-ready" in d
    assert "do not weaken the pin" in d


def test_no_registry_address_travels_in_the_health_line():
    """Deployment configuration does not belong in a report an operator pastes."""
    d = _run(0, _answer(False) + "\n")["detail"]
    assert "://" not in d and "ghcr" not in d


# ── the control: green in the other direction ──────────────────────────────

def test_CONTROL_a_matching_pair_passes_and_says_so():
    """Without this the check is satisfied by code that calls every pair a
    mismatch, which would fail `eda_doctor` on every healthy deployment."""
    out = _run(0, _answer(True) + "\n")
    assert out["ok"] is True
    assert out["detail"].startswith("RUNTIME_PAIR_MATCH")
    assert "MISMATCH" not in out["detail"]


# ── cannot-ask is never a match ────────────────────────────────────────────

def test_a_preflight_that_could_not_run_is_NOT_a_match():
    out = _run(1, "", "ModuleNotFoundError: no module named _runtime_pair_preflight")
    assert out["ok"] is False
    assert "RUNTIME_PAIR_NOT_MEASURED" in out["detail"]
    assert "ModuleNotFoundError" in out["detail"], (
        "name what could not be read; a bare refusal cannot be acted on")


def test_an_unparseable_answer_is_NOT_a_match():
    out = _run(0, "not json\n")
    assert out["ok"] is False
    assert "RUNTIME_PAIR_NOT_MEASURED" in out["detail"]


# ── registration shape: separate line, HARD, before the tool rows ──────────

def test_the_pair_line_is_its_own_check_and_is_not_soft():
    src = INDEX_JS.read_text(encoding="utf-8")
    i = src.index('check: "runtime_pair_match"')
    # THE PUSH ITSELF, not a window around it: a ±N-character slice reaches the
    # `soft:` on the neighbouring image-identity row and would report this one
    # soft while it is hard, or the reverse.
    start = src.rindex("checks.push({", 0, i)
    block = src[start:src.index("});", i) + 3]
    assert "_runtimePairIdentity()" in src[start - 200:start]
    assert "soft" not in block, (
        "a soft pair line reproduces the '16/16 checks passed' misreading")
    assert "if (!pair.ok) allOk = false;" in src[start:start + len(block) + 200]
    assert src.index('check: "docker_reachable"') < i < src.index(
        "// 2. Per-tool binaries"), (
        "the pair must be stated BEFORE the tool rows it must not be confused "
        "with")
