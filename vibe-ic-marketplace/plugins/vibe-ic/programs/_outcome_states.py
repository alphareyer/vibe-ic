"""_outcome_states.py — one run is the verdict; only a plain FAIL is red.

THE OWNER'S RULING (2026-09-27)
===============================
    "量測型的，還有工具或環境不足的，這些都不算紅。這部分用另外一種型態表示"
    "WE DONT WANT TO RE-RUN FOR SUCH RED"
    "need to report the reason and may need to other machine run or install
     tool to re-run"
    "記帳-type red (just statistic value not matched), no need to stop landing.
     other types if cannot run, provide reason and may need to re-run after
     fix issues."

So a test reports its own state IN THE RUN THAT PRODUCED IT, and nobody
re-runs a red to find out what kind of red it was. Four states:

    FAIL           a plain failure. The only red.
    NOT_VERIFIED   the failure is the HOST's: a tool binary it verifies with is
                   absent (checked on the session's own PATH at failure time),
                   or there is no docker / no resolvable EDA image, or the EDA
                   container runs other bytes than the pinned image
                   (CONTAINER_IMAGE_MISMATCH, confirmed by a live pin check). The reason
                   names the host, the missing tool or resource, and the remedy.
    NOT_MEASURED   a test marked ``@pytest.mark.measures`` failed its
                   measurement while the run's own conditions make that
                   measurement untrustworthy: xdist worker count > 1, and/or the
                   1-min load per core above LOAD_PER_CORE_LIMIT. The reason
                   names the condition and the measured numbers. A quiet serial
                   run keeps the same failure a FAIL, so an O(N) -> O(N^2)
                   regression is still caught where it can be measured.
    BOOKKEEPING    a test marked ``@pytest.mark.bookkeeping`` — one whose ONLY
                   subject is that a stated statistic (a published count in a
                   README / INVENTORY / INDEX / census block) matches reality —
                   failed. The reason carries the stated-vs-actual message and
                   the regenerator. ``match=`` narrows it to the assertion that
                   IS the count, so a property assertion in the same test stays
                   a FAIL.

THE MECHANISM
=============
A ``pytest_runtest_makereport`` hookwrapper turns such a failure into a SKIPPED
outcome whose reason starts with the state name. NOT_VERIFIED reuses
`not_verified_tier` — its SENTINEL, its `not_verified_reason`, its summary block
and, unchanged, its ``VIBEIC_REQUIRE_EDA_VERIFICATION=1`` refusal: on a host
that sets it, a converted NOT_VERIFIED still makes the session red.

Nothing here changes which tests RUN: the two markers select nothing and
deselect nothing (the `consistency` tier's deselection is a different, broader
class: order, registers and member pins as well as numbers).

WHAT IS NEVER CONVERTED
=======================
* A failure while the tool IS present. Presence is asked of the PATH the session
  started with, not the test's (a test that monkeypatches PATH to empty to
  exercise its own missing-tool branch is testing, not missing, the tool).
* An environment exception RAISED BY TEST CODE — a planted ImageNotResolvable
  or FileNotFoundError is a fixture, and a regression that lets it escape is
  exactly what such a test exists to catch.
* xfail/xpass reports, and teardown failures.
* A test marked ``outcome_state_exempt(reason)``: its subject IS a tool's
  absence (or this tier), so the text it asserts on names the missing tool and
  its own regression would otherwise read as the host's.

THE SUMMARY
===========
One line per non-FAIL outcome, then one count line, with stable prefixes::

    OUTCOME_STATE NOT_VERIFIED <nodeid> :: <reason>
    OUTCOME_STATES FAIL=<n> NOT_VERIFIED=<n> NOT_MEASURED=<n> BOOKKEEPING=<n>

chip-AGNOSTIC / PDK-AGNOSTIC: pytest reporting only; no design is read.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import not_verified_tier as _nv  # noqa: E402

#: Shared with `not_verified_tier`: the same sentinel, so one reader and one
#: VIBEIC_REQUIRE_EDA_VERIFICATION refusal cover both spellings.
NOT_VERIFIED = _nv.SENTINEL.rstrip(":")
NOT_MEASURED = "NOT_MEASURED"
BOOKKEEPING = "BOOKKEEPING"
FAIL = "FAIL"
STATES = (NOT_VERIFIED, NOT_MEASURED, BOOKKEEPING)

MEASURES_MARK = "measures"
BOOKKEEPING_MARK = "bookkeeping"
#: A test whose SUBJECT is a tool's absence (or this tier itself) asserts on text
#: that names a missing tool; on a host that lacks the tool, the text reader
#: above would turn that test's own regression into NOT_VERIFIED. Such a test
#: declares this mark, with a reason, and always reports its raw outcome.
EXEMPT_MARK = "outcome_state_exempt"

#: 1-min load average per online CPU above which a wall-clock measurement taken
#: in this run is not trusted. At 1.0 every core already has a runnable task
#: queued behind it; half that is where a single-threaded timing loop starts
#: sharing its core with a neighbour most of the time. Stated, not tuned: a
#: serial run on a quiet host sits well under it, so it keeps its FAIL.
LOAD_PER_CORE_LIMIT = 0.5

#: Tool binaries whose absence makes a verification impossible on this host.
#: A name here is only ever CONVERTED when `shutil.which` also says it is absent
#: on the session's PATH, so listing a tool can never hide a real failure.
KNOWN_TOOLS = frozenset((
    "yosys", "yosys-abc", "yosys-config", "sby", "eqy", "yices", "yices-smt2",
    "z3", "boolector", "bitwuzla", "iverilog", "vvp", "verilator", "magic",
    "netgen", "netgen-lvs", "klayout", "openroad", "sta", "ngspice", "xyce",
    "Xyce", "docker", "openlane", "librelane", "cocotb-config", "gtkwave",
))

#: The remedy for a tool the HOST lacks. The harness is the repo's own answer to
#: "the tool lives in the image", so it is named rather than paraphrased.
IMAGE_HARNESS = "tools/ci/run_suite_in_eda_image.sh"

_SESSION_PATH_KEY = "_outcome_states_session_path"
_REPORTED_KEY = "_outcome_states_reported"
_SUMMARY_PREFIX = "OUTCOME_STATE"
_COUNT_PREFIX = "OUTCOME_STATES"

#: How a tool's absence is SPELLED in an exception's text. Each captures the
#: name the failure itself gives; the name then has to be in KNOWN_TOOLS and
#: absent from PATH before anything is converted.
_ABSENT_TOOL_PATTERNS = (
    re.compile(r"No such file or directory: ['\"]([^'\"]+)['\"]"),
    re.compile(r"(?:^|[\s'\"`(])([A-Za-z0-9_.+/-]+): (?:command )?not found"),
    re.compile(r"`?([A-Za-z0-9_.+-]+)`? (?:is )?not (?:found )?on PATH"),
)

_SESSION_PATH: Optional[str] = None
_SESSION_ENV: Optional[Dict[str, str]] = None

#: A container attach refused because the container runs bytes other than the
#: pinned ones (`_eda_pin.container_pin_state` -> MISMATCH, raised as
#: `_container_exec.ContainerImageMismatch` or carried as text in an ENV_REFUSED
#: / pytest.fail message). The name is taken from the refusal's own sentence.
_MISMATCH_RE = re.compile(r"CONTAINER_IMAGE_MISMATCH: container (\S+?),? runs ")

#: The repo's own recycle command for the default container.
RESTART_EDA = "tools/vibeic-eda/restart-eda.sh"


# ── classification (pure; the hook below only wires it) ──────────────────────
def host() -> str:
    """The hostname; inside a container that name is a container id, so say so."""
    try:
        name = socket.gethostname() or "<unknown host>"
    except OSError:                                        # pragma: no cover
        name = "<unknown host>"
    return f"{name} (a container)" if os.path.exists("/.dockerenv") else name


def _chain(exc: Optional[BaseException]) -> Iterator[BaseException]:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _raised_in_test_code(exc: BaseException) -> bool:
    """True when the innermost frame that raised *exc* is a test file's own code."""
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return False
    fname = Path(last.tb_frame.f_code.co_filename)
    return fname.name.startswith("test_") or fname.name == "conftest.py"


def tool_absent(tool: str, path: Optional[str] = None) -> bool:
    """True when *tool* is not an executable on *path* (default: session PATH)."""
    if path is None:
        path = _SESSION_PATH if _SESSION_PATH is not None else os.environ.get("PATH", "")
    return shutil.which(tool, path=path) is None


def _named_tools(text: str) -> List[str]:
    out: List[str] = []
    for pat in _ABSENT_TOOL_PATTERNS:
        for m in pat.finditer(text):
            name = os.path.basename(m.group(1).strip())
            if name in KNOWN_TOOLS and name not in out:
                out.append(name)
    return out


def classify_not_verified(exc: Optional[BaseException],
                          path: Optional[str] = None) -> Optional[str]:
    """The NOT_VERIFIED reason for this failure, or None when it is a FAIL."""
    for e in _chain(exc):
        name = type(e).__name__
        if name == "ImageNotResolvable" or (
                isinstance(e, RuntimeError) and "IMAGE_NOT_RESOLVABLE" in str(e)):
            if _raised_in_test_code(e):
                return None
            docker_state = ("docker is absent" if tool_absent("docker", path)
                            else "docker is present but no image identity resolved")
            return _nv.not_verified_reason(
                f"host {host()}: no resolvable vibeic-eda image ({docker_state}): "
                f"{str(e)[:240]}",
                f"run on a host with docker and the pinned vibeic-eda image "
                f"(the suite's `_eda_pin` tests belong on the host, not inside "
                f"the image), or pull the image here")
        if isinstance(e, FileNotFoundError) and getattr(e, "filename", None):
            tool = os.path.basename(str(e.filename))
            if tool in KNOWN_TOOLS and tool_absent(tool, path):
                if _raised_in_test_code(e):
                    return None
                return _tool_reason(tool)
    for e in _chain(exc):
        text = str(e)
        if "docker unusable" in text and tool_absent("docker", path):
            return _tool_reason("docker")
        for tool in _named_tools(text):
            if tool_absent(tool, path):
                return _tool_reason(tool)
    return None


_PIN_PROBE = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import _eda_pin\n"
    "name = sys.argv[2] or _eda_pin.default_container_name()\n"
    "state, detail = _eda_pin.container_pin_state(name)\n"
    "print(json.dumps([state, detail, name]))\n")


def live_pin_state(container: str) -> Tuple[str, str, str]:
    """`_eda_pin.container_pin_state(container)`, asked NOW, in a fresh process.

    Returns ``(state, detail, container)``; an empty *container* asks about the
    plugin's default container (`_eda_pin.default_container_name`).

    A fresh interpreter under the SESSION's environment, because at report time
    the test's monkeypatches are still in force: a test that planted a fake
    mismatch would otherwise answer this question with its own fake.
    """
    import json
    import subprocess
    env = dict(_SESSION_ENV if _SESSION_ENV is not None else os.environ)
    try:
        cp = subprocess.run(
            [sys.executable, "-c", _PIN_PROBE, str(_HERE), container],
            capture_output=True, text=True, env=env, timeout=120)
        state, detail, name = json.loads(cp.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return "UNREADABLE", f"the live pin check could not run: {exc}", container
    return str(state), str(detail), str(name)


def classify_container_mismatch(exc: Optional[BaseException],
                                probe=live_pin_state) -> Optional[str]:
    """NOT_VERIFIED when the refusal names a container that, asked NOW, still
    runs other bytes than the pinned ones. A container that matches (or cannot
    be read) keeps the FAIL: then the refusal is not a fact about the host."""
    for e in _chain(exc):
        text = str(e)
        if "CONTAINER_IMAGE_MISMATCH" not in text:
            continue
        m = _MISMATCH_RE.search(text)
        # A repr can truncate the name ("container vibeic-ed...db36dd"); then
        # the plugin's DEFAULT container is the one asked, and it still has to
        # be a live, confirmed MISMATCH before anything is converted.
        container = m.group(1).strip("`'\"") if m and "..." not in m.group(1) else ""
        state, detail, container = probe(container)
        if state != "MISMATCH":
            return None
        return _nv.not_verified_reason(
            f"host {host()}: container `{container}` runs other bytes than the "
            f"image the plugin resolved, so the attach was refused and nothing "
            f"was verified: {_one_line(detail, 320)}",
            f"recycle `{container}` to the resolved image ({RESTART_EDA}, or "
            f"`docker rm -f {container}` and re-create it from the pinned "
            f"reference), or re-run once the container and the pin agree")
    return None


def _tool_reason(tool: str) -> str:
    if tool == "docker":
        remedy = (f"run on a host with a usable docker engine, or inside "
                  f"{IMAGE_HARNESS}, which binds the host engine in")
    else:
        remedy = (f"run inside the EDA image with {IMAGE_HARNESS}, or on a host "
                  f"where `{tool}` is on PATH")
    return _nv.not_verified_reason(
        f"host {host()}: `{tool}` is not on PATH, so this verification could "
        f"not run", remedy)


def run_conditions(environ=None) -> Dict[str, float]:
    env = os.environ if environ is None else environ
    try:
        workers = int(env.get("PYTEST_XDIST_WORKER_COUNT", "") or 1)
    except ValueError:
        workers = 1
    cores = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except OSError:                                        # pragma: no cover
        load1 = 0.0
    return {"workers": workers, "load1": load1, "cores": cores,
            "load_per_core": load1 / cores}


def classify_not_measured(cond: Dict[str, float], message: str) -> Optional[str]:
    """NOT_MEASURED reason when the run's conditions void a measurement."""
    why = []
    if cond["workers"] > 1:
        why.append(f"xdist workers={int(cond['workers'])} > 1")
    if cond["load_per_core"] > LOAD_PER_CORE_LIMIT:
        why.append(f"1-min load per core {cond['load_per_core']:.2f} > "
                   f"{LOAD_PER_CORE_LIMIT}")
    if not why:
        return None
    return (f"{NOT_MEASURED}: host {host()}: {' and '.join(why)} "
            f"(load1={cond['load1']:.2f} over {int(cond['cores'])} cores); "
            f"measured: {_one_line(message, 300)} — remedy: this measurement "
            f"is only trusted in a serial run on a quiet host")


def classify_bookkeeping(mark, message: str) -> Optional[str]:
    match = mark.kwargs.get("match")
    if match and not re.search(match, message):
        return None
    regen = mark.kwargs.get("regenerate") or "(no regenerator: a hand edit)"
    return (f"{BOOKKEEPING}: stated vs actual: {_one_line(message, 400)} — "
            f"regenerate: {regen}")


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."


# ── pytest wiring ─────────────────────────────────────────────────────────────
def pytest_configure(config):
    global _SESSION_PATH, _SESSION_ENV
    if _SESSION_PATH is None:
        _SESSION_PATH = os.environ.get("PATH", "")
    if _SESSION_ENV is None:
        _SESSION_ENV = dict(os.environ)
    config.addinivalue_line(
        "markers",
        f"{MEASURES_MARK}: the test MEASURES wall-clock (ratios, budgets, stall "
        f"windows); a failure under -n>1 or load/core>{LOAD_PER_CORE_LIMIT} is "
        f"reported {NOT_MEASURED}, not red (_outcome_states.py).")
    config.addinivalue_line(
        "markers",
        f"{BOOKKEEPING_MARK}(regenerate=..., match=...): the test's only subject "
        f"is a stated statistic; a failure (matching `match`) is reported "
        f"{BOOKKEEPING}, not red (_outcome_states.py).")
    config.addinivalue_line(
        "markers",
        f"{EXEMPT_MARK}(reason): the test's subject is a tool's absence or this "
        f"tier itself, so its failures are always reported raw (_outcome_states.py).")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if not rep.failed or rep.when not in ("setup", "call") or hasattr(rep, "wasxfail"):
        return
    exc = call.excinfo.value if call.excinfo is not None else None
    if exc is None or item.get_closest_marker(EXEMPT_MARK) is not None:
        return
    reason = classify_not_verified(exc) or classify_container_mismatch(exc)
    if reason is None and rep.when == "call":
        message = str(exc)
        if item.get_closest_marker(MEASURES_MARK) is not None:
            reason = classify_not_measured(run_conditions(), message)
        if reason is None:
            mark = item.get_closest_marker(BOOKKEEPING_MARK)
            if mark is not None:
                reason = classify_bookkeeping(mark, message)
    if reason is None:
        return
    original = rep.longreprtext
    path, lineno, _ = item.reportinfo()
    rep.outcome = "skipped"
    rep.longrepr = (str(path), (lineno or 0) + 1, reason)
    rep.sections.append(("outcome-state: the failure this replaced", original[-4000:]))


def _state_of(rep) -> Tuple[Optional[str], str]:
    longrepr = getattr(rep, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr or "")
    if reason.startswith("Skipped: "):
        reason = reason[len("Skipped: "):]
    for state in STATES:
        if reason.startswith(f"{state}:"):
            return state, reason
    return None, reason


def outcome_lines(stats) -> List[str]:
    """The parseable block: one line per non-FAIL state, then the counts."""
    counts = {s: 0 for s in STATES}
    lines: List[str] = []
    for rep in stats.get("skipped", []):
        state, reason = _state_of(rep)
        if state is None:
            continue
        counts[state] += 1
        lines.append(f"{_SUMMARY_PREFIX} {state} {rep.nodeid} :: "
                     f"{_one_line(reason, 600)}")
    # FAIL is everything red: pytest's `failed` and its `error` (a setup or
    # teardown failure), which it counts separately and which are just as red.
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    lines.append(f"{_COUNT_PREFIX} {FAIL}={failed} "
                 + " ".join(f"{s}={counts[s]}" for s in STATES))
    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    if getattr(config, _REPORTED_KEY, False):
        return
    setattr(config, _REPORTED_KEY, True)
    lines = outcome_lines(terminalreporter.stats)
    terminalreporter.write_sep("-", "outcome states (only FAIL is red)")
    for line in lines:
        terminalreporter.write_line(line)
