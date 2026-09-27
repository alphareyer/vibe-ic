"""programs/tests/_tree_write_audit.py -- run test nodes and record every write
they make under a root, including writes they undo before they finish.

WHY THIS EXISTS
===============
`suite_write_guard` compares two `git status` snapshots, and says so: "a write
made AND reverted inside a single test is invisible to a snapshot taken after
it". That is exactly the shape of a probe writer -- plant a file in the shipped
`programs/`, measure, unlink it in a `finally`. Nothing is left for a snapshot
to see, but every concurrent xdist worker that lists the tree in between sees
the file appear and vanish, and one of them reads it after the unlink
(`FileNotFoundError` in `test_upstream_mirror_is_pinned`, 8HD-8, 2026-09-28).

So this records the WRITE itself. A `sitecustomize` placed first on the child's
`PYTHONPATH` installs `sys.addaudithook` in the pytest child and in every Python
process it starts (the same mechanism as `step_input_scope.install_guard`).
CPython raises the `open` event from C for `open()`, `Path.write_text` and
`os.open` alike, and `os.remove`/`os.rename`/`os.mkdir`/`os.chmod`/
`shutil.rmtree`/... for the rest, so a write cannot get past it by the route it
takes. Each event is tagged with `PYTEST_CURRENT_TEST` ('' = collection).

WHAT IT DOES NOT SEE, stated so it is not read as a guarantee: a write made by a
non-Python process (a shell `>`, a compiled tool); a Python child started with
`-I`/`-S` or a scrubbed environment; a write under `__pycache__`/
`.pytest_cache` or to a `*.pyc` (bytecode churn, excluded on purpose).

PATH RESOLUTION -- three calibrations, all measured:
  * `shutil.rmtree` removes entries by bare NAME relative to a directory fd;
    reading that name against the cwd invented deletions under the plugin root.
    The name is joined onto `/proc/self/fd/<dir_fd>` instead.
  * removing or renaming a SYMLINK acts on the entry, not its target. Resolving
    the whole path reported a tmp symlink farm's cleanup as the deletion of
    every file it pointed at. Entry-level events resolve only the parent.
  * an `O_TMPFILE` open names a directory and creates no entry in it, so it is
    not a write a reader can see (`tempfile.TemporaryFile()` with the cwd as
    its temp dir did this 45 times in one sweep).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

import _progress_run as _pr

_SITECUSTOMIZE = r'''
import os
import sys

_ROOT = os.environ.get("VIBEIC_TREE_WRITE_AUDIT_ROOT", "")
_LOG = os.environ.get("VIBEIC_TREE_WRITE_AUDIT_LOG", "")
_W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# An O_TMPFILE open names a DIRECTORY and creates no entry in it: nothing a
# reader can list ever appears. Measured: `tempfile.TemporaryFile()` in a
# `python -c` child whose temp dir resolved to the cwd.
_TMPFILE = getattr(os, "O_TMPFILE", 0)
# event -> ((path arg, dir_fd arg or None, acts on the symlink TARGET), ...)
_EV = {
    "os.remove": ((0, 1, False),), "os.rename": ((0, 2, False), (1, 3, False)),
    "os.rmdir": ((0, 1, False),), "os.mkdir": ((0, 2, False),),
    "os.symlink": ((1, 2, False),), "os.link": ((1, 3, False),),
    "os.truncate": ((0, None, True),), "os.chmod": ((0, 2, True),),
    "os.chown": ((0, 3, True),), "os.utime": ((0, 3, True),),
    "shutil.copyfile": ((1, None, True),),
    "shutil.copytree": ((1, None, False),), "shutil.rmtree": ((0, None, False),),
    "shutil.move": ((0, None, False), (1, None, False)),
    "os.mkfifo": ((0, 2, False),), "os.mknod": ((0, 3, False),),
}
_busy = [False]


def _rel(p, dir_fd=None, follow=True):
    if isinstance(p, bytes):
        try:
            p = p.decode()
        except Exception:
            return None
    if not isinstance(p, (str, os.PathLike)):
        return None
    p = os.fspath(p)
    if isinstance(dir_fd, int) and not os.path.isabs(p):
        try:
            p = os.path.join(os.readlink("/proc/self/fd/%d" % dir_fd), p)
        except OSError:
            return None
    ap = os.path.abspath(p)
    if follow:
        rp = os.path.realpath(ap)
    else:
        rp = os.path.join(os.path.realpath(os.path.dirname(ap)),
                          os.path.basename(ap))
    root = _ROOT.rstrip(os.sep) + os.sep
    for q in (ap, rp):
        if q.startswith(root):
            rel = os.path.relpath(q, _ROOT)
            parts = rel.split(os.sep)
            if "__pycache__" in parts or ".pytest_cache" in parts \
                    or rel.endswith((".pyc", ".pyo")):
                return None
            return rel.replace(os.sep, "/")
    return None


def _log(ev, rel):
    import json
    fd = os.open(_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (json.dumps({
            "event": ev, "path": rel,
            "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
            "pid": os.getpid()}) + "\n").encode())
    finally:
        os.close(fd)


def _hook(ev, args):
    if _busy[0]:
        return
    try:
        _busy[0] = True
        if ev == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if isinstance(path, int):
                return
            if _TMPFILE and isinstance(flags, int) \
                    and flags & _TMPFILE == _TMPFILE:
                return
            if not ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                    or (isinstance(flags, int) and flags & _W)):
                return
            rel = _rel(path)
            if rel is not None:
                _log(ev, rel)
            return
        for i, fdi, follow in _EV.get(ev, ()):
            if i < len(args) and not isinstance(args[i], int):
                dfd = args[fdi] if fdi is not None and fdi < len(args) else None
                rel = _rel(args[i], dfd, follow)
                if rel is not None:
                    _log(ev, rel)
    except Exception:
        pass
    finally:
        _busy[0] = False


if _ROOT and _LOG:
    sys.addaudithook(_hook)
'''


@dataclass
class Audit:
    """What one audited child session did."""
    rc: int
    output: str
    #: nodeid -> "passed" | "failed" | "error" | "skipped", from the JUnit report
    outcomes: Dict[str, str] = field(default_factory=dict)
    #: one dict per write-class event under the root: event, path, test, pid
    events: List[dict] = field(default_factory=list)

    def events_for(self, nodeid: str) -> List[dict]:
        """Events attributed to `nodeid`, plus every COLLECTION-time event
        ('' test id): an import-time write belongs to whoever is imported."""
        return [e for e in self.events
                if not e["test"] or e["test"].split(" ")[0] == nodeid]


def audit_env(root: Path, workdir: Path) -> Dict[str, str]:
    """An environment whose Python processes record writes under `root`."""
    hook = workdir / "hook"
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    # The child is a separate session: nothing of the PARENT session's pytest
    # plumbing may reach it -- its xdist identity, its current-test tag (which
    # would attribute the child's writes to the parent's node), its addopts, or
    # its progress channel (the child would otherwise write events into the
    # landing supervisor's stream under the parent's nonce).
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_XDIST", "PYTEST_CURRENT_TEST",
                                "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                                "VIBEIC_PYTEST_PROGRESS_",
                                "VIBEIC_PYTEST_RUNTIME_IDENTITY"))}
    # vibe-ic#1047: a broken third-party `pytest11` entry point on the host
    # (web3 on the landing host) must not decide what this child reports.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(hook)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["VIBEIC_TREE_WRITE_AUDIT_ROOT"] = str(Path(root).resolve())
    env["VIBEIC_TREE_WRITE_AUDIT_LOG"] = str(workdir / "events.jsonl")
    return env


def read_events(workdir: Path) -> List[dict]:
    log = workdir / "events.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def _junit_outcomes(xml_path: Path, plugin_root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not xml_path.is_file():
        return out
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        # classname is the dotted file path (+ class); rebuild the node id.
        parts = classname.split(".")
        for cut in range(len(parts), 0, -1):
            f = Path(*parts[:cut]).with_suffix(".py")
            if (plugin_root / f).is_file():
                nodeid = "::".join([f.as_posix()] + parts[cut:] + [name])
                break
        else:
            nodeid = f"{classname}::{name}"
        kinds = {child.tag for child in case}
        out[nodeid] = ("failed" if "failure" in kinds else
                       "error" if "error" in kinds else
                       "skipped" if "skipped" in kinds else "passed")
    return out


def run_nodes(nodeids: Sequence[str], *, plugin_root: Path,
              root: Path) -> Audit:
    """Run `nodeids` (relative to `plugin_root`) in ONE child pytest session
    under the write audit, and return what it did.

    Bounded by PROGRESS (`_progress_run.run`), not by a wall clock: a child
    that stops making forward progress is killed and reported as not run, so
    every node reads "not run" and the caller's assertions go red by name.
    `--write-guard=off`: the child's own snapshot guard would compare `git
    status` while the parent session's other workers are writing, and its
    verdict is not what this measures."""
    with tempfile.TemporaryDirectory(prefix="tree_write_audit_") as tmp:
        work = Path(tmp)
        junit = work / "junit.xml"
        env = audit_env(root, work)
        try:
            r = _pr.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "--write-guard=off", f"--junitxml={junit}", *nodeids],
                cwd=str(plugin_root), env=env, capture_output=True, text=True)
            rc, output = r.returncode, (r.stdout or "") + (r.stderr or "")
        except _pr.Stalled as exc:
            rc, output = -1, f"STALLED, killed: {exc}"
        return Audit(rc=rc, output=output,
                     outcomes=_junit_outcomes(junit, plugin_root),
                     events=read_events(work))
