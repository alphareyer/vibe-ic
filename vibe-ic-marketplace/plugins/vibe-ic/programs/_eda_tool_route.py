#!/usr/bin/env python3
"""WHERE an EDA tool runs: decided once, the same way for every program.

THE DEFECT THIS MODULE REMOVES
==============================
Programs asked the HOST first. `shutil.which("yosys")` found a binary, so that
binary ran, and the pinned image was consulted only when the host had nothing
(or, in `step_yosys_synth`, only when the host exec returned 127). The pin said
which toolchain a verdict belongs to; the host decided which toolchain produced
it.

MEASURED 2026-09-28 on 8HD-9 (192.168.1.105), spm through the canonical front
door, runner pinned to `vibeic-eda@sha256:7a01d48e…` (0.3.83) with
`--require-image` satisfied (`reports/container_image.json` PASS):

    Step 1 Spec-to-RTL  FAIL  "Yosys elaboration failed"
      p0_tool_frontend_check ran /usr/bin/yosys 0.9 (the host's):
      "ERROR: No such command: read_slang"
      the SAME script in the pinned image: rc 0

and `step_yosys_synth` ran the host yosys first as well (it failed on
`dffunmap`, which 0.9 does not have, and only then reached the container).
The same design gets a different verdict depending on which host runs it.

THE RULE (chip-, PDK- and design-AGNOSTIC)
==========================================
* When a container route exists, i.e. this process has a docker client, the
  tool runs in the RESOLVED IMAGE:
    - `docker exec` into the run's NAMED container (the `container` argument,
      else `VIBEIC_EDA_CONTAINER`, else `EDA_CONTAINER`), when it is running,
      holds the pinned bytes and can see every path the command names;
    - otherwise `docker run --rm` of the pinned image, with the command's own
      directories bind-mounted at the SAME paths so every path it names still
      resolves;
    - otherwise the call is REFUSED with the reason. There is no silent host
      fallback, because a host fallback is the defect.
* The LOCAL route is taken only when there is no container route at all:
  `_container_exec.no_container_route()`, the one definition, which is true
  inside the image itself. On it the tool's version is RECORDED and checked
  against the minimum the step needs, and the call is refused, with the reason,
  when that minimum is not met. For yosys, the minimum is DERIVED from the
  script: every command the script runs must be one this yosys has
  (`yosys -Q -p help` lists them; `yosys -h <cmd>` is not usable because it
  exits 0 for a command that does not exist).

A REFUSAL IS A `FileNotFoundError`. Every site already has an honest branch for
"the tool is not there" (usually `except (FileNotFoundError, OSError)` around a
bare `subprocess.run([tool, …])`), and a refused route is exactly that fact:
there is no tool this step may use. `ToolRouteRefused` carries the code and the
reason, so the branch can say WHY instead of only THAT.

No tool, PDK or design is chosen by this module; it only decides where the
command the caller built is executed.
"""
from __future__ import annotations

import errno
import atexit
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _container_exec as _ce  # noqa: E402 — the ONE "is there a container route" predicate
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
import _eda_pin as _pin  # noqa: E402 — the ONE place the pinned identity is resolved

__all__ = [
    "EDA_TOOLS", "ROUTE_LOCAL", "ROUTE_CONTAINER", "ROUTE_IMAGE",
    "TOOL_ABSENT", "TOOL_BELOW_MINIMUM", "NO_IMAGE_ROUTE",
    "Requirement", "Route", "ToolRouteRefused",
    "is_eda_tool", "local_route", "resolve", "argv_for", "run", "supervised_run",
    "watchdog_run", "available",
    "why_unavailable",
    "records", "reset_caches",
]

#: The EDA tools the pinned image provides and the flow judges with. A command
#: whose argv[0] is one of these is routed; anything else is refused as not an
#: EDA tool (this module is not a general "run anything in docker" helper).
EDA_TOOLS = frozenset({
    "yosys", "yosys-abc", "iverilog", "vvp", "verilator",
    "verilator_coverage", "sby", "eqy",
    "sta", "openroad", "magic", "klayout", "netgen", "ngspice", "Xyce",
    "sv2v",
})

ROUTE_LOCAL = "local"
ROUTE_CONTAINER = "container"
ROUTE_IMAGE = "image"

#: Refusal codes. Each says a DIFFERENT thing.
TOOL_ABSENT = "TOOL_ABSENT"
TOOL_BELOW_MINIMUM = "TOOL_BELOW_MINIMUM"
NO_IMAGE_ROUTE = "NO_IMAGE_ROUTE"
NOT_AN_EDA_TOOL = "NOT_AN_EDA_TOOL"

#: How a tool reports its own version on the LOCAL route. The banner is
#: recorded verbatim; the first dotted number in it is the comparable version.
_VERSION_ARGS: Dict[str, Tuple[str, ...]] = {
    "yosys": ("-V",), "iverilog": ("-V",), "vvp": ("-V",),
    "verilator": ("--version",), "verilator_coverage": ("--version",),
    "sby": ("--version",), "eqy": ("--version",),
    "sta": ("-version",), "openroad": ("-version",), "magic": ("--version",),
    "klayout": ("-v",), "ngspice": ("-v",), "Xyce": ("-v",),
    "sv2v": ("--version",), "netgen": ("-batch", "quit"),
}

#: Host paths the image route never bind-mounts: the image owns them, and a
#: host copy mounted over the image's would substitute the host's files for the
#: pinned ones — the very substitution this module exists to stop.
#: Of those, the kernel's own interfaces: the container has its own `/dev/null`
#: or `/proc/self`, which mean what the host's do, so naming one is not a
#: host file the tool would miss.
_PSEUDO_ROOTS = ("/dev", "/proc", "/sys")
#: ... and the image's OWN directories: a path under them names the pinned
#: image's toolchain or PDK (`/foss/pdks/...`), whatever the host happens to
#: carry at the same place (8HD-9 has a `/foss/pdks` of its own), so it is
#: neither mounted nor refused.
_IMAGE_NAMESPACE_ROOTS = ("/foss", "/headless", "/dockerstartup")
_IMAGE_OWNED_ROOTS = ("/dev", "/proc", "/sys", "/usr", "/bin", "/sbin",
                      "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/foss",
                      "/opt", "/headless", "/dockerstartup", "/run", "/boot",
                      "/root", "/snap", "/var")

#: An absolute path inside an argv token, including one embedded in a script
#: (`yosys -p "read_verilog /abs/a.v; write_json /abs/o.json"`), glued to an
#: include flag (`-I/abs`), or in a plusarg list (`+incdir+/abs+/abs2`). A `+`
#: ends a path only where the next path begins (`+/`), so `/x/c++` is one path.
_ABS_PATH_RE = re.compile(
    r"(?:^|(?<=[\s=:;,'\"(\[{+])|(?<=-I))"
    r"(/(?:[^\s;,'\"()\[\]{}+]|\+(?!/))+)")

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

#: `yosys -Q -p help` lists every command as four spaces, the name, then the
#: one-line description. Same shape in 0.9 and in the pinned 0.69+ (measured
#: on 8HD-9: 198 names from /usr/bin/yosys 0.9).
_YOSYS_HELP_LINE_RE = re.compile(r"^ {4}([A-Za-z_][\w$.-]*)\s", re.M)

#: Client-side grace over a container-side deadline, so the deadline that runs
#: next to the tool always fires first (same reasoning as `_container_exec`).
_CLIENT_GRACE_S = _ce.CLIENT_GRACE_S
_KILL_GRACE_S = _ce.DEFAULT_KILL_GRACE_S


@dataclass(frozen=True)
class Requirement:
    """What the STEP needs from a local tool.

    `version`: the lowest version the step works with, compared with the first
    dotted number in the tool's own banner. `commands`: yosys commands the step
    runs (added to the ones derived from the script itself). `why`: the step's
    reason, which goes into the refusal so a reader sees what was needed."""
    version: Optional[Tuple[int, ...]] = None
    commands: Tuple[str, ...] = ()
    why: str = ""


class ToolRouteRefused(FileNotFoundError):
    """No route this step may use exists for `tool`; nothing was run."""

    def __init__(self, tool: str, code: str, reason: str,
                 record: Optional[dict] = None):
        self.tool = tool
        self.code = code
        self.reason = reason
        self.record = dict(record or {})
        # errno/strerror are set so a site's existing message, built from
        # `exc.strerror` for a missing binary, carries the refusal's reason.
        super().__init__(errno.ENOENT, f"{code}: {tool}: {reason}")


@dataclass
class Route:
    """Where one command runs, and the facts that decided it."""
    tool: str
    kind: str
    container: Optional[str] = None
    image: Optional[str] = None
    local_path: Optional[str] = None
    version: Optional[str] = None
    requirement: Optional[str] = None
    mounts: Tuple[str, ...] = ()
    notes: List[str] = field(default_factory=list)

    def record(self) -> dict:
        return {"tool": self.tool, "route": self.kind,
                "container": self.container, "image": self.image,
                "local_path": self.local_path, "version": self.version,
                "requirement": self.requirement, "mounts": list(self.mounts),
                "notes": list(self.notes)}

    def where(self) -> str:
        if self.kind == ROUTE_CONTAINER:
            return f"container {self.container} ({self.image or 'pinned image'})"
        if self.kind == ROUTE_IMAGE:
            return f"image {self.image}"
        return f"local {self.local_path} ({self.version or 'version unread'})"


# --------------------------------------------------------------------------
# per-process caches (a tool's banner and an image's contents do not change
# under a running process; a container's state is re-read on every miss)
# --------------------------------------------------------------------------
_LOCAL: Dict[str, dict] = {}
_YOSYS_CMDS: Dict[str, Optional[frozenset]] = {}
_IMAGE_TOOLS: Dict[str, Dict[str, bool]] = {}
_CONTAINER_OK: Dict[str, Tuple[bool, str, List[Tuple[str, str]]]] = {}
_CONTAINER_IMAGE: Dict[str, Optional[str]] = {}
_EXPLICIT_MATCH: Dict[Tuple[str, str], Tuple[bool, str]] = {}
_RECORDS: List[dict] = []
_ANNOUNCED: set = set()


def reset_caches() -> None:
    """Forget every cached probe (tests; a long runner after a container swap).

    Session containers are forgotten for the NEXT call but stay tracked as
    retired, so `close_sessions` still removes them at exit."""
    with _SESSION_LOCK:
        _RETIRED.extend(sess["name"] for sess in _SESSIONS.values())
        _SESSIONS.clear()
    _LOCAL.clear()
    _YOSYS_CMDS.clear()
    _IMAGE_TOOLS.clear()
    _CONTAINER_OK.clear()
    _CONTAINER_IMAGE.clear()
    _EXPLICIT_MATCH.clear()
    _RECORDS.clear()
    _ANNOUNCED.clear()


def records() -> List[dict]:
    """Every route taken by this process, in order (the recorded versions)."""
    return list(_RECORDS)


def is_eda_tool(name: str) -> bool:
    """Is `name` (a bare name or a path) one of `EDA_TOOLS`?"""
    return os.path.basename(str(name or "")) in EDA_TOOLS


def local_route() -> bool:
    """True iff this process has NO container route (the one definition)."""
    return _ce.no_container_route()


# --------------------------------------------------------------------------
# the LOCAL route: version recorded, requirement checked
# --------------------------------------------------------------------------
def _parse_version(banner: str) -> Optional[Tuple[int, ...]]:
    m = _VERSION_RE.search(banner or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def _probe(argv: List[str]) -> subprocess.CompletedProcess:
    """A short self-description of a local tool (`-V`, `help`), supervised by
    forward progress like every other launch of an EDA binary here."""
    import _progress_run as _pr
    return _pr.run(argv, capture_output=True, text=True, errors="replace")


def _probe_local(tool: str) -> dict:
    """The local binary for `tool` and its own version banner, cached per
    resolved PATH entry (a different binary on PATH is a different probe)."""
    path = shutil.which(tool)
    key = f"{tool}\0{path}"
    if key in _LOCAL:
        return _LOCAL[key]
    info = {"path": path, "banner": None, "version": None, "commands": None}
    if path:
        args = _VERSION_ARGS.get(tool)
        if args:
            try:
                cp = _probe([path, *args])
                text = (cp.stdout or "") + (cp.stderr or "")
                line = next((ln.strip() for ln in text.splitlines()
                             if _VERSION_RE.search(ln)), "")
                info["banner"] = line or None
                info["version"] = _parse_version(line)
            except Exception:                                # noqa: BLE001
                pass
    _LOCAL[key] = info
    return info


def _yosys_commands_available(path: str) -> Optional[frozenset]:
    """The command names `path`'s own `yosys -Q -p help` lists (cached per
    binary); None when the listing could not be read or was empty."""
    if path in _YOSYS_CMDS:
        return _YOSYS_CMDS[path]
    try:
        cp = _probe([path, "-Q", "-p", "help"])
    except Exception:                                        # noqa: BLE001
        return None
    names = frozenset(_YOSYS_HELP_LINE_RE.findall(cp.stdout or "")) or None
    _YOSYS_CMDS[path] = names
    return names


def yosys_script_commands(script: str) -> Tuple[str, ...]:
    """The commands a yosys script runs, in order, de-duplicated.

    A statement is split on `;` and newlines; its first word is the command.
    Comments (`#`) and blank statements are skipped. Options of a command are
    not judged here, only that the command exists."""
    seen: List[str] = []
    for raw in re.split(r"[;\n]", script or ""):
        stmt = raw.split("#", 1)[0].strip()
        if not stmt:
            continue
        word = stmt.split()[0]
        if re.fullmatch(r"[A-Za-z_][\w$.-]*", word) and word not in seen:
            seen.append(word)
    return tuple(seen)


def _script_commands_of(argv: Sequence[str]) -> Tuple[str, ...]:
    """Every yosys command the argv's `-p` scripts and `-s` script files run."""
    cmds: List[str] = []
    for i in range(len(argv)):
        tok = str(argv[i])
        if tok == "-p" and i + 1 < len(argv):
            cmds.extend(yosys_script_commands(str(argv[i + 1])))
        elif tok == "-s" and i + 1 < len(argv):
            try:
                cmds.extend(yosys_script_commands(
                    Path(str(argv[i + 1])).read_text(errors="replace")))
            except OSError:
                pass
    out: List[str] = []
    for c in cmds:
        if c not in out:
            out.append(c)
    return tuple(out)


def _local_route(tool: str, argv: Sequence[str],
                 requirement: Optional[Requirement]) -> Route:
    info = _probe_local(tool)
    rec_base = {"tool": tool, "route": ROUTE_LOCAL}
    if not info["path"]:
        raise ToolRouteRefused(
            tool, TOOL_ABSENT,
            "no container route (no docker client on PATH) and no "
            f"`{tool}` on this process's PATH", rec_base)
    req = requirement or Requirement()
    route = Route(tool=tool, kind=ROUTE_LOCAL, local_path=info["path"],
                  version=info["banner"])
    wanted = []
    if req.version is not None:
        have = info["version"]
        wanted.append(f"version >= {'.'.join(map(str, req.version))}")
        if have is None:
            raise ToolRouteRefused(
                tool, TOOL_BELOW_MINIMUM,
                f"{info['path']} did not report a readable version, and the "
                f"step needs {wanted[-1]}" + (f" ({req.why})" if req.why else ""),
                route.record())
        if tuple(have) < tuple(req.version):
            raise ToolRouteRefused(
                tool, TOOL_BELOW_MINIMUM,
                f"{info['path']} is {info['banner']!r}; the step needs "
                f"{wanted[-1]}" + (f" ({req.why})" if req.why else ""),
                route.record())
    if tool == "yosys":
        needed = tuple(dict.fromkeys(
            tuple(req.commands) + _script_commands_of(argv)))
        if needed:
            wanted.append("commands " + ", ".join(needed))
            have_cmds = _yosys_commands_available(info["path"])
            if have_cmds is None:
                raise ToolRouteRefused(
                    tool, TOOL_BELOW_MINIMUM,
                    f"{info['path']} ({info['banner']}) did not list its "
                    f"commands, so the script's commands {list(needed)} "
                    "cannot be confirmed", route.record())
            missing = [c for c in needed if c not in have_cmds]
            if missing:
                raise ToolRouteRefused(
                    tool, TOOL_BELOW_MINIMUM,
                    f"{info['path']} ({info['banner']}) has no "
                    f"{', '.join(missing)}, which the step's script runs"
                    + (f" ({req.why})" if req.why else ""), route.record())
    route.requirement = "; ".join(wanted) if wanted else "none declared"
    key = (ROUTE_LOCAL, tool)
    if key not in _ANNOUNCED:
        _ANNOUNCED.add(key)
        print(f"[eda_tool_route] LOCAL route (no docker client on PATH): "
              f"{tool} = {info['path']} [{info['banner'] or 'version unread'}]"
              f"; requirement: {route.requirement} -> met", file=sys.stderr)
    return route


# --------------------------------------------------------------------------
# the CONTAINER / IMAGE routes
# --------------------------------------------------------------------------
#: Small text files a command reads its inputs FROM (a yosys `-s` script, an
#: iverilog `-c`/`-f` file list, a Tcl deck): the paths inside them must be
#: visible to the tool as well. Read only when named in argv and small.
_SCRIPT_SUFFIXES = (".ys", ".tcl", ".f", ".lst", ".list", ".cmd", ".sby",
                    ".eqy", ".scr", ".txt")
_SCRIPT_MAX_BYTES = 1 << 20


def _paths_of(argv: Sequence[str], cwd: Optional[str],
              program: bool = False) -> List[str]:
    """Absolute host paths the command names, plus its working directory.

    Includes the absolute paths written INSIDE a small script or file list the
    command names (resolved against `cwd` when relative), because a tool reads
    those as surely as it reads its argv. `program=True` (`as_tool`): argv[0]
    is a program a tool BUILT and it is executed in the container, so its own
    path is named too."""
    out: List[str] = []
    base = Path(cwd).resolve() if cwd else Path.cwd()
    if cwd:
        out.append(str(base))
    if program and argv and str(argv[0]).startswith("/"):
        out.append(str(argv[0]))
    for tok in argv[1:]:
        t = str(tok)
        for m in _ABS_PATH_RE.finditer(t):
            out.append(m.group(1))
        if t.lower().endswith(_SCRIPT_SUFFIXES) and not t.startswith("-"):
            f = Path(t) if t.startswith("/") else base / t
            try:
                if f.is_file() and f.stat().st_size <= _SCRIPT_MAX_BYTES:
                    for m in _ABS_PATH_RE.finditer(f.read_text(errors="replace")):
                        out.append(m.group(1))
            except OSError:
                pass
    return out


def _owned_by_image(p: str) -> bool:
    return any(p == r or p.startswith(r + "/") for r in _IMAGE_OWNED_ROOTS)


def _unmountable(d: str) -> bool:
    """`d` (an existing host directory) holds HOST content the image route
    cannot show the tool: it lies strictly inside a root the image owns and is
    not the tmp root the session container always mounts."""
    tmp = _tmp_root()
    if d == tmp or d.startswith(tmp + "/"):
        return False
    return any(d.startswith(r + "/") for r in _IMAGE_OWNED_ROOTS
               if r not in _PSEUDO_ROOTS)


def bind_dirs(argv: Sequence[str], cwd: Optional[str] = None,
              program: bool = False, tool: str = "") -> List[str]:
    """The host directories to bind-mount so every named path resolves.

    Each path's nearest EXISTING directory is mounted (an output that does not
    exist yet is written into its parent), BOTH as named and as resolved when
    a symlink lies on the way: argv keeps the name, and the name only resolves
    in the container if its target is there too. `/` is never mounted, and a
    path that exists only in the image (no host ancestor below `/` or an
    image-owned root) needs nothing. Nested directories fold into their
    ancestor.

    A path whose host directory lies INSIDE a root the image owns (`/opt/work`,
    `/var/...`, `/root/...`) is REFUSED (`NO_IMAGE_ROUTE`, naming it): mounting
    it could shadow the pinned image, and not mounting it would run the tool
    without its input, which the tool reports as a design error."""
    cands = set()
    for p in _paths_of(argv, cwd, program):
        if any(p == r or p.startswith(r + "/")
               for r in _PSEUDO_ROOTS + _IMAGE_NAMESPACE_ROOTS):
            continue
        d = Path(p)
        while not d.is_dir() and d != d.parent:
            d = d.parent
        ds = str(d)
        if ds in ("/", "") or ds in _IMAGE_OWNED_ROOTS:
            continue                       # only the image has it
        forms = {ds}
        try:
            forms.add(str(d.resolve()))
        except OSError:
            pass
        for f in forms:
            if _unmountable(f):
                raise ToolRouteRefused(
                    tool or os.path.basename(str(argv[0]) if argv else "?"),
                    NO_IMAGE_ROUTE,
                    f"{p} lies under {f}, inside a root the pinned image owns; "
                    "the image route cannot show it to the tool without "
                    "mounting over the image (move the work outside "
                    f"{', '.join(r for r in _IMAGE_OWNED_ROOTS if f.startswith(r + '/'))}"
                    ", or name a container that sees it at the same path)",
                    {"tool": tool, "route": None, "path": p})
        cands |= forms
    keep: List[str] = []
    for d in sorted(cands, key=len):
        if any(d == k or d.startswith(k + "/") for k in keep):
            continue
        keep.append(d)
    return keep


def _named_container(container: Optional[str]) -> str:
    for c in (container, os.environ.get(_pin.CONTAINER_NAME_ENV),
              os.environ.get("EDA_CONTAINER")):
        c = (c or "").strip()
        if c and c.lower() != "host":
            return c
    return ""


def _container_state(container: str) -> Tuple[bool, str, List[Tuple[str, str]]]:
    """(usable, why_not, mounts) for a named container."""
    if container in _CONTAINER_OK:
        return _CONTAINER_OK[container]
    try:
        cp = subprocess.run(
            ["docker", "inspect", container, "--format",
             "{{.State.Running}}\n{{range .Mounts}}{{.Source}}|{{.Destination}}\n{{end}}"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        res = (False, f"docker inspect {container} failed: {exc}", [])
        _CONTAINER_OK[container] = res
        return res
    if cp.returncode != 0:
        res = (False, f"no container named {container!r}", [])
        _CONTAINER_OK[container] = res
        return res
    lines = cp.stdout.splitlines()
    if not lines or lines[0].strip() != "true":
        res = (False, f"container {container!r} is not running", [])
        _CONTAINER_OK[container] = res
        return res
    why = _pin.container_attach_refusal(container)
    if why:
        res = (False, f"container {container!r} holds other bytes: {why}", [])
        _CONTAINER_OK[container] = res
        return res
    mounts: List[Tuple[str, str]] = []
    for ln in lines[1:]:
        if "|" in ln:
            s, d = ln.split("|", 1)
            if s and d:
                mounts.append((s.rstrip("/"), d.rstrip("/")))
    mounts.sort(key=lambda t: len(t[0]), reverse=True)
    res = (True, "", mounts)
    _CONTAINER_OK[container] = res
    return res


def _translate(text: str, mounts: List[Tuple[str, str]]) -> str:
    """Rewrite every absolute host path in `text` to its in-container path."""
    def sub(m: "re.Match") -> str:
        p = m.group(1)
        for s, d in mounts:
            if p == s or p.startswith(s + "/"):
                return d + p[len(s):]
        return p
    return _ABS_PATH_RE.sub(sub, text)


def _covered(p: str, mounts: List[Tuple[str, str]]) -> bool:
    """`p` is visible in the container AT THE SAME PATH.

    Identity mounts only: a tool's diagnostics name the paths it was given, and
    callers attribute failures by those paths (which file an error cites). A
    container that sees the file under another name would make the same run
    report different text depending on the route, so it is not used; the image
    route, which mounts every directory at its own path, is."""
    return any((p == s or p.startswith(s + "/")) and s == d for s, d in mounts)


def _image_ref(image: Optional[str]) -> Tuple[Optional[str], str]:
    if image:
        return image, ""
    try:
        ref, why = _pin.pinned_image_present()
    except _pin.ImageNotResolvable as exc:
        return None, str(exc)
    return ref, why


# --------------------------------------------------------------------------
# THE SESSION CONTAINER: one long-lived container of the image per process
# --------------------------------------------------------------------------
#
# MEASURED 2026-09-28 on 8HD-8, the 595-file related selection: 44 min on main,
# 70 min on the first cut of this module, which started a fresh `docker run
# --rm` for EVERY tool call when no container was named. On an idle host a
# `docker run` costs ~360 ms and a `docker exec` ~75 ms (8HD-9, three of each,
# the pinned image); under load the creation cost grows, the exec cost does not.
#
# So the image route starts ONE container per process per image and every call
# after the first is a `docker exec` into it:
#   * OWNERSHIP IS PER PROCESS (each xdist worker owns its own). A container
#     shared across processes would need a cross-process lock AND a refcounted
#     lifetime (who removes it, and when?), and one dying would fail every
#     worker at once. Per-process ownership needs neither, keeps a failure
#     local, and leaves the memory-ceiling semantics exactly what they were:
#     every `docker run` carries the same per-container ceiling
#     (`_docker_memory`), as each per-call container did.
#   * SAME-PATH MOUNTS, BY ROOT. A path's root is the temp directory when it is
#     under it (every `TemporaryDirectory()` lands there, so it is mounted up
#     front), else its first two components (`/home/<user>`, `/mnt/<disk>`).
#     A call naming a path under a NEW root starts a replacement container
#     with the union of roots; the old one is retired, not killed, so a call
#     still running in it finishes. Paths the image owns are never mounted.
#   * LIFETIME: removed at interpreter exit (atexit); labelled with this
#     host, pid and process start time, so a later process reaps a container
#     whose owner is gone (SIGKILL skips atexit); and bounded by its own
#     `sleep` ceiling.
#   * A CONTAINER THAT DIED (OOM, a daemon restart, removed underneath us) is
#     recognised by docker's own daemon error on `exec`, confirmed with
#     `docker inspect`, recreated ONCE, and the call re-run; a second death
#     is refused with the reason. The host PATH is never the fallback.
#   * The pin is verified ONCE, at creation, and the container is registered
#     as owned (`_container_exec.register_owned_container`), so the single
#     `docker exec` builder skips the per-call digest inspect for it.

SESSION_LABEL = "vibeic.eda_tool_route.session"
SESSION_OWNER_LABEL = "vibeic.eda_tool_route.owner"
#: Hard ceiling on a session container's own life, whatever its owner does.
SESSION_MAX_LIFETIME_S = 24 * 3600
SESSION_START_FAILED = "SESSION_START_FAILED"
SESSION_DIED = "SESSION_CONTAINER_DIED"

_SESSIONS: Dict[str, dict] = {}
_RETIRED: List[str] = []
_SESSION_LOCK = threading.RLock()
_SESSION_STATE = {"reaped": False, "atexit": False, "starts": 0, "recreated": 0}


def _tmp_root() -> str:
    return os.path.realpath(tempfile.gettempdir())


def session_root(d: str) -> str:
    """The mount root that covers directory `d` in a session container."""
    tmp = _tmp_root()
    if d == tmp or d.startswith(tmp + "/"):
        return tmp
    parts = Path(d).parts
    return str(Path(*parts[:3])) if len(parts) >= 3 else d


def _proc_start(pid: int) -> str:
    """Field 22 of /proc/<pid>/stat (start time in clock ticks), or ''."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _owner_tag() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{_proc_start(os.getpid())}"


def _owner_alive(tag: str) -> Optional[bool]:
    """True/False for an owner on THIS host; None for another host's."""
    try:
        host, pid_s, start = tag.rsplit(":", 2)
        pid = int(pid_s)
    except ValueError:
        return None
    if host != socket.gethostname():
        return None
    if not Path(f"/proc/{pid}").exists():
        return False
    return (_proc_start(pid) == start) if start else True


def reap_orphan_sessions() -> List[str]:
    """Remove session containers whose owner process on THIS host is gone.

    Selected by this module's own label AND a dead owner (pid absent, or
    reused by a process that started at another time). A live owner's
    container, another host's, and anything unlabelled are left alone."""
    try:
        cp = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label={SESSION_LABEL}=1",
             "--format", '{{.ID}}\t{{.Label "%s"}}' % SESSION_OWNER_LABEL],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if cp.returncode != 0:
        return []
    removed = []
    for line in (cp.stdout or "").splitlines():
        cid, _, owner = line.partition("\t")
        if cid.strip() and _owner_alive(owner.strip()) is False:
            try:
                subprocess.run(["docker", "rm", "-f", cid.strip()],
                               capture_output=True, text=True, timeout=60)
                removed.append(cid.strip())
            except (OSError, subprocess.SubprocessError):
                pass
    return removed


def close_sessions() -> None:
    """Remove every session container this process started (atexit)."""
    with _SESSION_LOCK:
        names = [s["name"] for s in _SESSIONS.values()] + list(_RETIRED)
        _SESSIONS.clear()
        _RETIRED.clear()
    for name in names:
        _ce.unregister_owned_container(name)
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def _start_session(image: str, roots: Iterable[str], tool: str) -> str:
    if not _SESSION_STATE["reaped"]:
        _SESSION_STATE["reaped"] = True
        reap_orphan_sessions()
    name = f"vibeic-route-{os.getpid()}-{secrets.token_hex(4)}"
    argv = ["docker", "run", "-d", "--rm", *_dmem.docker_memory_flags(),
            "--pull", "never", "--network", "none",
            "-u", f"{os.getuid()}:{os.getgid()}",
            "--label", f"{SESSION_LABEL}=1",
            "--label", f"{SESSION_OWNER_LABEL}={_owner_tag()}"]
    for r in sorted(set(roots)):
        argv += ["-v", f"{r}:{r}"]
    argv += ["--name", name, "--entrypoint", "sleep", image,
             str(SESSION_MAX_LIFETIME_S)]
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolRouteRefused(tool, SESSION_START_FAILED,
                               f"could not start a container of {image}: {exc}")
    if cp.returncode != 0:
        raise ToolRouteRefused(
            tool, SESSION_START_FAILED,
            f"`docker run` of {image} exited {cp.returncode}: "
            + ((cp.stderr or cp.stdout or "").strip()[-400:]))
    why = _ce.register_owned_container(name)
    if why:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                       text=True, timeout=60)
        raise ToolRouteRefused(tool, _pin.CONTAINER_IMAGE_MISMATCH, why)
    _SESSION_STATE["starts"] += 1
    if not _SESSION_STATE["atexit"]:
        _SESSION_STATE["atexit"] = True
        atexit.register(close_sessions)
    return name


def session_container(image: str, dirs: Iterable[str] = (),
                      tool: str = "sh") -> str:
    """The name of this process's container of `image` that sees `dirs`
    at their own paths, started (or replaced by one with the union of
    roots) on demand."""
    roots = {session_root(d) for d in dirs} | {_tmp_root()}
    with _SESSION_LOCK:
        cur = _SESSIONS.get(image)
        if cur and roots <= cur["roots"]:
            return cur["name"]
        union = frozenset(roots | (cur["roots"] if cur else set()))
        name = _start_session(image, union, tool)
        if cur:
            _RETIRED.append(cur["name"])
        _SESSIONS[image] = {"name": name, "roots": union}
        return name


def _looks_dead(rc: int, err) -> bool:
    text = err if isinstance(err, str) else (err or b"").decode("utf-8", "replace")
    return (rc != 0 and "Error response from daemon" in text
            and ("No such container" in text or "is not running" in text))


def _container_running(name: str) -> bool:
    try:
        cp = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                             name], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def _replace_dead_session(image: str, name: str, tool: str) -> str:
    """Recreate a session container that died (once per death)."""
    with _SESSION_LOCK:
        cur = _SESSIONS.get(image)
        _ce.unregister_owned_container(name)
        if cur and cur["name"] != name:
            return cur["name"]            # another thread already replaced it
        roots = cur["roots"] if cur else frozenset({_tmp_root()})
        new = _start_session(image, roots, tool)
        _SESSIONS[image] = {"name": new, "roots": roots}
        _SESSION_STATE["recreated"] += 1
        return new


def _image_has(ref: str, tool: str) -> Optional[bool]:
    """Does the image carry `tool` on its default PATH? None = could not ask.

    Asked once per process per image, inside the session container."""
    known = _IMAGE_TOOLS.get(ref)
    if known is None:
        script = "; ".join(
            f"command -v {shlex.quote(t)} >/dev/null 2>&1 && echo {t}"
            for t in sorted(EDA_TOOLS))
        try:
            name = session_container(ref, (), tool)
            cp = subprocess.run(_ce.docker_exec_argv(name, "sh", "-c", script),
                                capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError, ToolRouteRefused,
                _ce.ContainerImageMismatch):
            return None
        if cp.returncode not in (0, 1):
            return None
        present = set(cp.stdout.split())
        known = {t: (t in present) for t in EDA_TOOLS}
        _IMAGE_TOOLS[ref] = known
    return known.get(tool)


def resolve(tool: str, argv: Optional[Sequence[str]] = None, *,
            cwd: Optional[str] = None, container: Optional[str] = None,
            image: Optional[str] = None,
            requirement: Optional[Requirement] = None,
            local: bool = False, program: bool = False) -> Route:
    """The route `argv` (whose argv[0] is `tool`) takes. Raises on refusal.

    An EXPLICIT `image` is what the caller asked to run: a named container is
    used for it only when that container measurably runs the same bytes
    (digest, or image id for a bare id); otherwise the explicit image is run.
    The route records the image the container ACTUALLY runs.

    `local=True` is the CALLER'S deliberate choice of the local route (a
    program run with no container on purpose, e.g. `--container host`). It is
    honoured, and it gets exactly what the no-client local route gets: the
    version recorded and the requirement checked."""
    argv = list(argv or [tool])
    if tool not in EDA_TOOLS:
        raise ToolRouteRefused(tool, NOT_AN_EDA_TOOL,
                               f"{tool!r} is not one of {sorted(EDA_TOOLS)}")
    if local or local_route():
        return _local_route(tool, argv, requirement)
    notes: List[str] = []
    named = _named_container(container)
    ref, why_img = _image_ref(image)
    if named:
        ok, why, mounts = _container_state(named)
        if ok and image:
            ok, why = _container_runs_image(named, image)
        if ok:
            need = bind_dirs(argv, cwd, program, tool)
            unseen = [p for p in need if not _covered(p, mounts)]
            if not unseen:
                return Route(tool=tool, kind=ROUTE_CONTAINER, container=named,
                             image=_container_image(named) or ref,
                             mounts=tuple(need), notes=notes)
            notes.append(f"container {named!r} cannot see {unseen[:3]}")
        else:
            notes.append(why)
    if not ref:
        raise ToolRouteRefused(
            tool, NO_IMAGE_ROUTE,
            "a container route exists (docker client on PATH) but no usable "
            "container and no pinned image: " + "; ".join(notes + [why_img]),
            {"tool": tool, "route": None, "notes": notes})
    return Route(tool=tool, kind=ROUTE_IMAGE, image=ref,
                 mounts=tuple(bind_dirs(argv, cwd, program, tool)), notes=notes)


def _container_image(name: str) -> Optional[str]:
    """The portable reference of the bytes container `name` runs (cached)."""
    if name not in _CONTAINER_IMAGE:
        _CONTAINER_IMAGE[name], _ = _pin.container_image_reference(name)
    return _CONTAINER_IMAGE[name]


def _container_runs_image(name: str, image: str) -> Tuple[bool, str]:
    """(same, why_not): does container `name` run the bytes `image` names?"""
    key = (name, image)
    if key in _EXPLICIT_MATCH:
        return _EXPLICIT_MATCH[key]
    if _pin.is_bare_image_id(image):
        have, why = _pin.container_image_id(name)
        same = bool(have) and (have == image or have.startswith(image))
    else:
        want = {_pin.reference_digest(image)} - {None}
        if not want:
            digests, _w = _pin.local_repo_digests(image)
            want = {_pin.reference_digest(e) for e in digests} - {None}
        have, why = _pin.container_image_digest(name)
        same = bool(have) and have in want
    res = (True, "") if same else (False, (
        f"container {name!r} runs {have or 'an image that could not be read'}"
        f"{' (' + why + ')' if why and not have else ''}, not the explicit "
        f"image {image}"))
    _EXPLICIT_MATCH[key] = res
    return res


def argv_for(argv: Sequence[str], *, cwd: Optional[str] = None,
             container: Optional[str] = None, image: Optional[str] = None,
             requirement: Optional[Requirement] = None,
             deadline_s: Optional[float] = None, stdin: bool = False,
             env: Optional[Dict[str, str]] = None,
             local: bool = False,
             as_tool: Optional[str] = None,
             stamp: Optional[str] = None) -> Tuple[List[str], Route]:
    """(argv to execute, route) for a tool command. Raises on refusal.

    `stamp` is a job pidfile (`_docker_watchdog.new_job_pidfile()`): on the
    container and image routes the tool is launched through the identity
    stamp prelude, so a supervisor that stops watching it can kill it WHERE
    IT LIVES (`_docker_watchdog.kill_supervised_job`). Ignored on the local
    route, where the supervisor's own process-group kill reaches the tool.

    `as_tool` runs a PROGRAM A TOOL PRODUCED (a Verilator `--binary`
    simulator, a compiled vvp image) where that tool runs: the route is
    resolved exactly as for `as_tool`, and argv[0] — a path — is executed on
    it. A binary built in the image links the image's libraries and must not
    be run by the host."""
    argv = [str(a) for a in argv]
    if not argv:
        raise ValueError("empty argv")
    tool = as_tool or os.path.basename(argv[0])
    if cwd is None and not (local or local_route()):
        # A relative path in argv means "relative to where the caller is". The
        # local route inherits that directory; the container and image routes
        # would otherwise start in the image's WORKDIR, so pin it explicitly.
        cwd = os.getcwd()
    route = resolve(tool, argv, cwd=cwd, container=container, image=image,
                    requirement=requirement, local=local,
                    program=bool(as_tool))
    _RECORDS.append(route.record())
    exe = argv[0] if as_tool else tool
    if route.kind == ROUTE_LOCAL:
        # Unchanged: the binary just recorded and checked is the one this
        # PATH resolves for argv[0] (the same `shutil.which` answer).
        return list(argv), route
    inner = [exe, *argv[1:]]
    if deadline_s:
        inner = ["timeout", "-k", str(_KILL_GRACE_S),
                 str(max(1, int(round(deadline_s)))), *inner]
    fwd_env: List[str] = []
    for k, v in sorted((env or {}).items()):
        if k in ("PATH", "HOME", "PWD", "SHELL", "USER", "LOGNAME",
                 "PYTHONPATH", "OLDPWD") or k.startswith("LD_"):
            continue
        if os.environ.get(k) != v:
            fwd_env += ["-e", f"{k}={v}"]
    if route.kind == ROUTE_CONTAINER:
        _ok, _why, mounts = _container_state(route.container or "")
        opts: List[str] = []
        if stdin:
            opts.append("-i")
        if cwd:
            opts += ["-w", _translate(str(Path(cwd).resolve()), mounts)]
        opts += fwd_env
        inner = _stamped([_translate(t, mounts) for t in inner], stamp)
        return _ce.docker_exec_argv(route.container, *inner,
                                    opts=tuple(opts)), route
    # IMAGE route: this process's session container of the image (started on
    # the first call, replaced when a new mount root appears), exec'd into.
    route.container = session_container(route.image, route.mounts, tool)
    opts = []
    if stdin:
        opts.append("-i")
    if cwd:
        opts += ["-w", str(Path(cwd).resolve())]
    opts += fwd_env
    return _ce.docker_exec_argv(route.container, *_stamped(inner, stamp),
                                opts=tuple(opts)), route


def _stamped(inner: List[str], pidfile: Optional[str]) -> List[str]:
    """`inner`, launched so that its (pid, starttime) lands in `pidfile`.

    The stamping shell `exec`s the tool, so the stamped pid IS the tool's pid
    and (a `docker exec` starts its own session) its process-group leader:
    exactly the shape `_container_exec.run_in_container_supervised` uses."""
    if not pidfile:
        return inner
    import _docker_watchdog as _dw  # noqa: PLC0415  (imports _container_exec)
    return ["sh", "-c", _dw.identity_stamp_prelude(pidfile) + 'exec "$@"',
            "sh", *inner]


def _reap_in_container(route: "Route", pidfile: str) -> str:
    """Kill the stamped job inside its container: TERM, grace, KILL, then a
    survivor sweep by identity. The `docker` client a host-side supervisor
    kills does NOT forward SIGKILL to the exec'd process, so without this a
    stalled tool keeps running (and holding its cores) in the container."""
    import _docker_watchdog as _dw  # noqa: PLC0415
    return _dw.kill_supervised_job(route.container or "", pidfile,
                                   docker_exec_raw=_ce._raw_exec)


def _drop_stamp(route: "Route", pidfile: str) -> None:
    import _docker_watchdog as _dw  # noqa: PLC0415
    _dw.cleanup_job_pidfile(route.container or "", pidfile, _ce._raw_exec)


def run(argv: Sequence[str], *, cwd=None, timeout: Optional[float] = None,
        input=None, env: Optional[Dict[str, str]] = None,
        capture_output: bool = False, text: Optional[bool] = None,
        errors: Optional[str] = None, stdout=None, stderr=None,
        check: bool = False, container: Optional[str] = None,
        image: Optional[str] = None,
        requirement: Optional[Requirement] = None,
        local: bool = False, as_tool: Optional[str] = None
        ) -> subprocess.CompletedProcess:
    """`subprocess.run` for an EDA tool command, executed on its route.

    Drop-in for the call it replaces: same keywords, same CompletedProcess,
    `subprocess.TimeoutExpired` when `timeout` expires (the deadline is ALSO
    enforced next to the tool on the container/image routes, so no orphan
    survives it). A refused route raises `ToolRouteRefused`
    (a `FileNotFoundError`) and runs nothing. The route taken is attached to
    the result as `.eda_route` (a `Route.record()` dict)."""
    cwd_s = str(cwd) if cwd is not None else None
    for attempt in (1, 2):
        full, route = argv_for(argv, cwd=cwd_s, container=container,
                               image=image, requirement=requirement,
                               deadline_s=timeout, stdin=input is not None,
                               env=env, local=local, as_tool=as_tool)
        kw = dict(capture_output=capture_output, input=input, check=False)
        if text is not None:
            kw["text"] = text
        if errors is not None:
            kw["errors"] = errors
        if stdout is not None:
            kw["stdout"] = stdout
        if stderr is not None:
            kw["stderr"] = stderr
        if route.kind == ROUTE_LOCAL:
            kw["cwd"] = cwd_s
            kw["env"] = env
            client_timeout = timeout
        else:
            client_timeout = (timeout + _CLIENT_GRACE_S) if timeout else None
        t0 = time.monotonic()
        cp = subprocess.run(full, timeout=client_timeout, **kw)
        if _session_died(route, cp.returncode, cp.stderr, attempt):
            continue
        break
    if (route.kind != ROUTE_LOCAL and timeout and cp.returncode == 124
            and time.monotonic() - t0 >= 0.9 * timeout):
        raise subprocess.TimeoutExpired(full, timeout, output=cp.stdout,
                                        stderr=cp.stderr)
    cp.eda_route = route.record()
    if check and cp.returncode:
        raise subprocess.CalledProcessError(cp.returncode, full,
                                            cp.stdout, cp.stderr)
    return cp


def _session_died(route: "Route", rc: int, err, attempt: int) -> bool:
    """True when the call must be re-run: the SESSION container died under it
    (docker's own daemon error, confirmed by `docker inspect`) and this was
    the first attempt, so it has just been recreated. A second death is
    refused with the reason; any other outcome is the tool's own."""
    if route.kind != ROUTE_IMAGE or not route.container \
            or not _looks_dead(rc, err) or _container_running(route.container):
        return False
    if attempt == 1:
        _replace_dead_session(route.image or "", route.container, route.tool)
        return True
    text = err if isinstance(err, str) else (err or b"").decode("utf-8", "replace")
    raise ToolRouteRefused(
        route.tool, SESSION_DIED,
        f"the session container of {route.image} died, was recreated once, "
        f"and died again: {text.strip()[-300:]}", route.record())


def supervised_run(argv: Sequence[str], *, cwd=None, input=None,
                   env: Optional[Dict[str, str]] = None,
                   capture_output: bool = True, text: bool = True,
                   errors: Optional[str] = None,
                   container: Optional[str] = None,
                   image: Optional[str] = None,
                   requirement: Optional[Requirement] = None,
                   local: bool = False, as_tool: Optional[str] = None,
                   **progress_kwargs
                   ) -> subprocess.CompletedProcess:
    """`_progress_run.run` for an EDA tool command, executed on its route.

    For the call sites that are bounded by NO-PROGRESS rather than by a clock.
    On the container and image routes the progress probe watches the TOOL'S
    processes inside the container (`_container_exec.container_tree_probe`),
    not the `docker` client, whose counters sit still while the tool works —
    the defect `_container_exec` documents for supervised `docker exec`.
    Raises `ToolRouteRefused` exactly as `run` does."""
    import _progress_run as _pr
    import _docker_watchdog as _dw  # noqa: PLC0415
    cwd_s = str(cwd) if cwd is not None else None
    for attempt in (1, 2):
        pidfile = _dw.new_job_pidfile()
        full, route = argv_for(argv, cwd=cwd_s, container=container,
                               image=image, requirement=requirement,
                               stdin=input is not None, env=env, local=local,
                               as_tool=as_tool, stamp=pidfile)
        kw = dict(progress_kwargs)
        kw.update(capture_output=capture_output, text=text, input=input)
        if errors is not None:
            kw["errors"] = errors
        if route.kind == ROUTE_LOCAL:
            kw["cwd"] = cwd_s
            kw["env"] = env
            cp = _pr.run(full, **kw)
        else:
            kw.setdefault("progress_probe",
                          _ce.container_tree_probe(route.container))
            try:
                cp = _pr.run(full, **kw)
            except _pr.Stalled as exc:
                # The supervisor killed the `docker` client; the tool is still
                # running in the container. Reap it there, then report the
                # stall (with the reap evidence) exactly as before.
                reap = _reap_in_container(route, pidfile)
                exc.stderr = ((exc.stderr or "") + "\n" + reap.strip()).strip()
                exc.reap = reap
                raise
            finally:
                _drop_stamp(route, pidfile)
        if _session_died(route, cp.returncode, cp.stderr, attempt):
            continue
        break
    cp.eda_route = route.record()
    return cp


def watchdog_run(argv: Sequence[str], *, cwd=None,
                 container: Optional[str] = None, image: Optional[str] = None,
                 requirement: Optional[Requirement] = None,
                 local: bool = False, as_tool: Optional[str] = None,
                 **supervise_kwargs):
    """`_watchdog.run_host_supervised` for an EDA tool command, on its route.

    Returns the watchdog's own `SupervisedResult` (rc / out / err / outcome /
    elapsed_s), so a converted call site reads it exactly as before. On the
    container and image routes the progress reading is the client's tree PLUS
    the tool's processes inside the container, because the `docker` client
    itself sits idle while the tool works and would otherwise be reaped as
    stalled. A refused route is `outcome == "launch_error"`, rc 127, with the
    refusal as `err`, which is what an absent binary always produced."""
    import _watchdog as _wd
    import _docker_watchdog as _dw  # noqa: PLC0415
    cwd_s = str(cwd) if cwd is not None else None
    for attempt in (1, 2):
        pidfile = _dw.new_job_pidfile()
        try:
            full, route = argv_for(argv, cwd=cwd_s, container=container,
                                   image=image, requirement=requirement,
                                   local=local, as_tool=as_tool, stamp=pidfile)
        except ToolRouteRefused as exc:
            return _refused_result(_wd, exc, supervise_kwargs)
        kw = dict(supervise_kwargs)
        if route.kind == ROUTE_LOCAL:
            kw["cwd"] = cwd_s
            res = _wd.run_host_supervised(full, **kw)
        else:
            inner = _ce.container_tree_probe(route.container)({})

            def _both(proc, _inner=inner):
                vals = [v for v in (_wd.host_cpu_probe(proc), _inner(proc))
                        if v is not None]
                return sum(vals) if vals else None
            kw.setdefault("cpu_probe", _both)
            try:
                res = _wd.run_host_supervised(full, **kw)
                if res.outcome in ("stalled", "aborted"):
                    # The watchdog's kill reached the `docker` client only.
                    reap = _reap_in_container(route, pidfile)
                    tail = "\n" + reap.strip() + "\n"
                    res.err = (res.err + tail.encode()
                               if isinstance(res.err, (bytes, bytearray))
                               else (res.err or "") + tail)
            finally:
                _drop_stamp(route, pidfile)
        try:
            if _session_died(route, res.rc, res.err, attempt):
                continue
        except ToolRouteRefused as exc:
            return _refused_result(_wd, exc, supervise_kwargs)
        break
    try:
        res.scope = dict(res.scope or {}, eda_route=route.record())
    except Exception:                                        # noqa: BLE001
        pass
    return res


def _refused_result(_wd, exc: "ToolRouteRefused", kw: dict):
    """A refused route in `_watchdog`'s own launch-error shape: rc 127,
    outcome `launch_error`, and `COMMAND_NOT_FOUND: <reason>` -- the marker
    `_watchdog` writes for a binary that could not be launched, which the
    #1437 callers key on -- in the caller's stream type (`as_text`)."""
    msg = f"COMMAND_NOT_FOUND: {exc}"
    as_text = kw.get("as_text", True)
    return _wd.SupervisedResult(127, "" if as_text else b"",
                                msg if as_text else msg.encode("utf-8"),
                                "launch_error", 0.0)

def available(tool: str, *, container: Optional[str] = None,
              image: Optional[str] = None,
              requirement: Optional[Requirement] = None,
              local: bool = False) -> bool:
    """Can a command with argv[0] = `tool` run on the route this process has?

    The route-aware replacement for `shutil.which(tool) is not None`: on a
    container route it asks the image (or the named container), never the
    host PATH; on the local route it also applies `requirement`."""
    return why_unavailable(tool, container=container, image=image,
                           requirement=requirement, local=local) == ""


def why_unavailable(tool: str, *, container: Optional[str] = None,
                    image: Optional[str] = None,
                    requirement: Optional[Requirement] = None,
                    local: bool = False) -> str:
    """'' when `tool` can run here; otherwise the reason, in words."""
    try:
        route = resolve(tool, [tool], container=container, image=image,
                        requirement=requirement, local=local)
    except ToolRouteRefused as exc:
        return str(exc)
    if route.kind == ROUTE_LOCAL:
        return ""
    if route.kind == ROUTE_CONTAINER:
        try:
            cp = subprocess.run(
                _ce.docker_exec_argv(route.container or "", "sh", "-c",
                                     f"command -v {shlex.quote(tool)}"),
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError, _ce.ContainerImageMismatch) as exc:
            return f"{TOOL_ABSENT}: {tool}: container probe failed: {exc}"
        return "" if cp.returncode == 0 else (
            f"{TOOL_ABSENT}: {tool}: not on PATH in container {route.container!r}")
    has = _image_has(route.image or "", tool)
    if has is False:
        return f"{TOOL_ABSENT}: {tool}: not on the default PATH of {route.image}"
    if has is None:
        return (f"{NO_IMAGE_ROUTE}: {tool}: could not start {route.image} to "
                "ask whether it carries the tool")
    return ""



def unavailable(*tools: str, container: Optional[str] = None,
                image: Optional[str] = None, local: bool = False) -> str:
    """'' when EVERY one of `tools` can run on this process's route, else the
    first one's refusal in words (NO_IMAGE_ROUTE, TOOL_ABSENT, ...).

    The text a gate puts in its SKIP / NOT_MEASURED reason (review wave 4c):
    `available()` is a bool, and a gate that turned False into "iverilog/vvp
    absent" or "not on PATH" told an operator on a host whose pinned image
    was merely not pulled to install a host tool -- the very substitution
    this module removes."""
    for t in tools:
        why = why_unavailable(t, container=container, image=image, local=local)
        if why:
            return why
    return ""

_ID_PATH_MARK = "__VIBEIC_ROUTE_TOOL_PATH__"
_ID_VER_MARK = "__VIBEIC_ROUTE_TOOL_VERSION__"


def identify(tool: str, *, container: Optional[str] = None,
             image: Optional[str] = None, local: bool = False) -> dict:
    """The MEASURED identity of `tool` on the route a command would take.

    Asked ON that route, never inferred: the path `command -v` gives there,
    the first line of `<tool> -V` there (stdout, else stderr: `iverilog -V`
    writes stdout and `vvp -V` stderr), and, on the container and image
    routes, the image the container is actually running (its registry digest
    and local id, read from docker, not the name that was asked for).
    A refused route is returned with `why` set and nothing probed."""
    out = {"tool": tool, "route": None, "container": None, "image": None,
           "image_digest": None, "image_id": None, "path": None,
           "version": None, "why": ""}
    try:
        route = resolve(tool, [tool], container=container, image=image,
                        local=local)
        out["route"] = route.kind
        out["image"] = route.image
        if route.kind == ROUTE_LOCAL:
            out.update(path=route.local_path, version=route.version)
            return out
        name = (route.container if route.kind == ROUTE_CONTAINER
                else session_container(route.image or "", (), tool))
    except ToolRouteRefused as exc:
        out["why"] = str(exc)
        return out
    t = shlex.quote(tool)
    script = (f'p="$(command -v {t} 2>/dev/null)"; echo "{_ID_PATH_MARK}$p"; '
              f'[ -n "$p" ] || exit 0; '
              f'vo="$("$p" -V 2>/dev/null | head -1)"; '
              f've="$("$p" -V 2>&1 1>/dev/null | head -1)"; '
              f'if [ -n "$vo" ]; then echo "{_ID_VER_MARK}$vo"; '
              f'else echo "{_ID_VER_MARK}$ve"; fi')
    out["container"] = name
    try:
        cp = subprocess.run(_ce.docker_exec_argv(name, "sh", "-c", script),
                            capture_output=True, text=True, errors="replace",
                            timeout=60)
    except (OSError, subprocess.SubprocessError,
            _ce.ContainerImageMismatch) as exc:
        out["why"] = f"identity probe in {name} failed: {exc}"
        return out
    for ln in (cp.stdout or "").splitlines():
        ln = ln.strip()
        if ln.startswith(_ID_PATH_MARK) and out["path"] is None:
            out["path"] = ln[len(_ID_PATH_MARK):].strip() or None
        elif ln.startswith(_ID_VER_MARK) and out["version"] is None:
            out["version"] = ln[len(_ID_VER_MARK):].strip() or None
    if not out["path"]:
        out["why"] = f"{TOOL_ABSENT}: {tool}: not on PATH in {name}"
    out["image_digest"], _ = _pin.container_image_digest(name)
    out["image_id"], _ = _pin.container_image_id(name)
    return out

def main(argv: Optional[List[str]] = None) -> int:
    """`_eda_tool_route.py <tool> [<arg> ...]` prints the route as JSON.

    exit 0 = routed, 69 (EX_ENV_REFUSED) = refused, 2 = usage."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.splitlines()[0])
        print("usage: _eda_tool_route.py <tool> [<arg> ...]")
        return 2
    try:
        full, route = argv_for(args, cwd=os.getcwd())
    except ToolRouteRefused as exc:
        print(json.dumps({"refused": exc.code, "reason": exc.reason,
                          "record": exc.record}, indent=1))
        return _ce.EX_ENV_REFUSED
    print(json.dumps({"route": route.record(), "argv": full}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
