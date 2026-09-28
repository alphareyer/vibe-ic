#!/usr/bin/env python3
"""_klayout_launch.py — shared locator/launcher for KLayout batch scripts.

The KLayout-fork sign-off engines (GDS-geometry antenna deck, per-layer density
metal fill) are ordinary KLayout batch scripts driven by environment variables.
They must run wherever KLayout actually lives, which in this project is one of
two places:

  * on the HOST PATH (``strmrun``, or ``klayout -b -r``) — a developer box with a
    local KLayout build;
  * inside the EDA CONTAINER (``$VIBEIC_EDA_CONTAINER``, default ``vibeic-eda``) —
    the normal flow environment, where the host has no KLayout at all.

A naive host-only ``shutil.which("klayout")`` therefore reports "no KLayout" on
every real flow run, which would turn every geometry gate into a permanent skip.
This module resolves the runner for both cases and, for the container case,
translates host paths to their in-container equivalents via the bind mounts
(``docker inspect``) so the script, its inputs and its outputs all resolve.

chip/PDK-AGNOSTIC: no design, vendor or PDK literal appears here.

    runner = find_runner()
    if runner is None:
        ...            # caller emits a NAMED, DISCLOSED skip — never a bare PASS
    rc, out, err = runner.run(script, {"ANT_GDS": gds, ...},
                              path_keys=("ANT_GDS", "ANT_CONFIG", "ANT_OUT"))
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _container_exec as _ce  # noqa: E402 — the ONE guarded docker-exec argv
import _eda_tool_route as _tool_route  # noqa: E402 — WHERE an EDA tool runs, decided once (FX-N1)
import _progress_run as _pr  # noqa: E402
import _eda_pin as _pin  # noqa: E402 — the ONE place the pin is stated

__all__ = ["KLayoutRunner", "HostRunner", "ContainerRunner", "find_runner"]

#: `_eda_pin.default_container_name()` IS this expression, plus the part
#: that was missing: the default half derives from the pinned digest
#: instead of being the shared literal `vibeic-eda`.  MEASURED 2026-09-07
#: on 8hd-3 -- the container holding that shared name was running 0.3.46
#: while the pin demanded 0.3.47, and a run that attached to it recorded
#: image provenance PASS about the wrong image.  `VIBEIC_EDA_CONTAINER` is
#: read exactly as before and still wins.
DEFAULT_CONTAINER = _pin.default_container_name()

_MOUNT_CACHE: Dict[str, List[Tuple[str, str]]] = {}


def _container_mounts(container: str) -> List[Tuple[str, str]]:
    """(host_src, container_dst) bind mounts of `container`, longest first.

    Mirrors phase3_one_shot_runner._container_mounts; duplicated here so a
    single gate program does not have to import the multi-thousand-line runner.
    """
    if container in _MOUNT_CACHE:
        return _MOUNT_CACHE[container]
    out: List[Tuple[str, str]] = []
    try:
        cp = subprocess.run(
            ["docker", "inspect", container, "--format",
             "{{range .Mounts}}{{.Source}}|{{.Destination}}\n{{end}}"],
            capture_output=True, text=True, timeout=15,
        )
        if cp.returncode == 0:
            for line in cp.stdout.splitlines():
                line = line.strip()
                if not line or "|" not in line:
                    continue
                src, dst = line.split("|", 1)
                if src and dst:
                    out.append((src.rstrip("/"), dst.rstrip("/")))
    except Exception:                                        # noqa: BLE001
        pass
    out.sort(key=lambda t: len(t[0]), reverse=True)
    _MOUNT_CACHE[container] = out
    return out


class KLayoutRunner:
    """A resolved way to execute a KLayout batch script."""

    kind = "none"
    detail = ""

    def cpath(self, host_path) -> str:
        return str(host_path)

    def covers(self, host_path) -> bool:
        """True when `host_path` is reachable by this runner."""
        return True

    def run(self, script, env: Dict[str, str], *,
            path_keys: Iterable[str] = (),
            timeout: int = 1800) -> Tuple[int, str, str]:
        raise NotImplementedError

    # ── Running something that is NOT a KLayout batch script ────────────────
    # A PDK ships its seal-ring / filler generators as ordinary `python3 <script>
    # --opt ...` CLIs that `import pya` (this is how LibreLane invokes them:
    # `KLayout.SealRing.run_generic` builds exactly such an argv). They must run
    # in the SAME environment KLayout lives in, which is what this class already
    # resolves — but not through `-r`, because their argv is their own.
    #
    # Paths are NOT translated here. The caller decides which arguments are
    # project paths (translate with `cpath`) and which are already environment-
    # native — a PDK under /foss/pdks inside the container has no host
    # counterpart, so translating it would corrupt a perfectly valid path.
    def run_argv(self, argv: Sequence[str], env: Dict[str, str],
                 *, timeout: int = 1800) -> Tuple[int, str, str]:
        raise NotImplementedError

    def exists(self, path) -> bool:
        """True when `path` is a readable file IN THIS RUNNER'S environment."""
        return Path(str(path)).is_file()

    def klayout_bin(self) -> str:
        """The KLayout GUI-class binary, for callers that need its own CLI
        (`-n <tech>`, `-rd k=v`). NOT `self._bin`: a host runner may have
        resolved `strmrun`, which is a script runner and accepts neither."""
        if _ce.no_container_route():
            try:
                return _tool_route.resolve("klayout", local=True).local_path \
                    or "klayout"
            except _tool_route.ToolRouteRefused:
                return "klayout"
        return "klayout"


class HostRunner(KLayoutRunner):
    kind = "host"

    def __init__(self, binary: str, flags: Sequence[str]):
        self._bin = binary
        self._flags = list(flags)
        self.detail = f"{binary} {' '.join(flags)}".strip()

    def run(self, script, env, *, path_keys=(), timeout=1800):
        full = dict(os.environ)
        full.setdefault("QT_QPA_PLATFORM", "offscreen")
        full.update({k: str(v) for k, v in env.items()})
        try:
            cp = subprocess.run([self._bin, *self._flags, str(script)],
                                env=full, capture_output=True, text=True,
                                timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"klayout timed out after {timeout}s"
        except OSError as exc:
            return 127, "", f"klayout launch failed: {exc}"
        return cp.returncode, cp.stdout or "", cp.stderr or ""

    def run_argv(self, argv, env, *, timeout=1800):
        full = dict(os.environ)
        full.setdefault("QT_QPA_PLATFORM", "offscreen")
        full.update({k: str(v) for k, v in env.items()})
        try:
            cp = subprocess.run([str(a) for a in argv], env=full,
                                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"command timed out after {timeout}s"
        except OSError as exc:
            return 127, "", f"launch failed: {exc}"
        return cp.returncode, cp.stdout or "", cp.stderr or ""


class ContainerRunner(KLayoutRunner):
    kind = "container"

    def __init__(self, container: str, binary: str = "klayout",
                 flags: Sequence[str] = ("-zz", "-b", "-r")):
        self._c = container
        self._bin = binary
        self._flags = list(flags)
        self.detail = f"{container}:{binary}"

    def cpath(self, host_path) -> str:
        p = str(host_path)
        if not p:
            return p
        for src, dst in _container_mounts(self._c):
            if p == src:
                return dst
            if p.startswith(src + "/"):
                return dst + p[len(src):]
        return p

    def covers(self, host_path) -> bool:
        p = str(host_path)
        if not p:
            return False
        return any(p == src or p.startswith(src + "/")
                   for src, _ in _container_mounts(self._c))

    def run(self, script, env, *, path_keys=(), timeout=1800):
        pk = set(path_keys)
        exports = " ".join(
            f"{k}={shlex.quote(self.cpath(v) if k in pk else str(v))}"
            for k, v in env.items())
        script_c = self.cpath(script)
        cmd = "export QT_QPA_PLATFORM=offscreen && "
        if exports:
            cmd += f"export {exports} && "
        cmd += f"{self._bin} {' '.join(self._flags)} {shlex.quote(script_c)}"
        try:
            cp = subprocess.run(_ce.docker_exec_argv(self._c, "bash", "-lc", cmd),
                                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"klayout (container) timed out after {timeout}s"
        except OSError as exc:
            return 127, "", f"docker exec failed: {exc}"
        return cp.returncode, cp.stdout or "", cp.stderr or ""

    def run_argv(self, argv, env, *, timeout=1800):
        exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
        cmd = "export QT_QPA_PLATFORM=offscreen && "
        if exports:
            cmd += f"export {exports} && "
        cmd += " ".join(shlex.quote(str(a)) for a in argv)
        try:
            cp = subprocess.run(_ce.docker_exec_argv(self._c, "bash", "-lc", cmd),
                                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"command (container) timed out after {timeout}s"
        except OSError as exc:
            return 127, "", f"docker exec failed: {exc}"
        return cp.returncode, cp.stdout or "", cp.stderr or ""

    def klayout_bin(self) -> str:
        return "klayout"

    def exists(self, path):
        try:
            cp = _pr.run_best_effort(
                _ce.docker_exec_argv(self._c, "test", "-f", str(path)),
                capture_output=True, text=True)
            return cp.returncode == 0
        except Exception:                                    # noqa: BLE001
            return False


def _container_has_klayout(container: str) -> bool:
    try:
        cp = _pr.run_best_effort(
            _ce.docker_exec_argv(container, "bash", "-lc", "command -v klayout >/dev/null 2>&1"),
            capture_output=True, text=True)
        return cp.returncode == 0
    except Exception:                                        # noqa: BLE001
        return False


#: The run's OWN receipt of where its tools ran. `container_image_provenance`
#: writes it and records the container NAME under "container".
CONTAINER_IMAGE_REL = "reports/container_image.json"


def container_the_run_recorded(project) -> Optional[str]:
    """The container THIS RUN used, from its own receipt, or None.

    THE ONE RESOLVER, and it exists because two invocations of one gate answered
    differently on one host seconds apart. MEASURED on the SLT53D S arm: the
    phase-3 runner dispatched `gds_xor_check --container real-ic-arm-eda` and the
    XOR ran -- runner {"kind": "container", "detail": "real-ic-arm-eda:klayout"},
    verdict PASS, 0 design-layer differences across 46 layers. Seconds later the
    completion audit evaluated the SAME gate through the flow's static clause,
    which carries no `--container` because a per-host container name cannot be
    written into the yaml; `find_runner(None)` fell through to
    `DEFAULT_CONTAINER` -- a DIFFERENT container that does not mount that project
    -- and the gate said, correctly for what it was given, "no KLayout runner
    reaches this project". The audit then published that answer over the
    producer's measurement.

    The durable identity is the run's own record, not the pinned default name:
    `reports/container_image.json` is written by the run that used the container
    and names it. So a caller with no explicit `--container` asks the RUN which
    container it used before falling back to a name that is merely conventional.
    """
    try:
        doc = json.loads(
            (Path(project) / CONTAINER_IMAGE_REL).read_text(errors="replace"))
    except (OSError, ValueError, TypeError):
        return None
    name = doc.get("container") if isinstance(doc, dict) else None
    return str(name).strip() or None if isinstance(name, str) else None


def find_runner(container: Optional[str] = None,
                project=None) -> Optional[KLayoutRunner]:
    """Resolve a KLayout batch runner: the container whenever a container
    route exists, this filesystem's KLayout only when there is none.

    FX-N1: this used to be "host first then container", so a host KLayout build
    ran the sign-off geometry decks of a run pinned to the image's KLayout, and
    the same GDS got a verdict that depended on the machine. With a docker
    client on PATH the run's container (argument, receipt, shared name) is the
    only candidate; `strmrun`/`klayout` on PATH are used only on the LOCAL
    route (no docker client, e.g. inside the image), where `_eda_tool_route`
    records KLayout's version.

    Returns None when neither is available — the caller MUST then emit a named,
    disclosed skip. Never silently succeed on a missing checker.

    `project`, when given, lets the run's OWN container receipt answer before the
    pinned default name does — see :func:`container_the_run_recorded` for the
    measurement that made this necessary. `container` still wins: an explicit
    argument is the caller stating which container it means.
    """
    if os.environ.get("VIBEIC_KLAYOUT_FORCE_ABSENT"):
        # Test hook: proves the honest-degrade path without uninstalling
        # KLayout. Only ever set by the gate's own regression tests.
        return None
    if _ce.no_container_route():
        for cand, flags in (("strmrun", ()), ("klayout", ("-zz", "-b", "-r"))):
            found = shutil.which(cand)
            if found:
                try:
                    _tool_route.resolve("klayout", local=True)   # version recorded
                except _tool_route.ToolRouteRefused:
                    pass            # strmrun without a klayout binary is still a runner
                return HostRunner(found, flags)
        return None
    recorded = container_the_run_recorded(project) if project else None
    for name in (container, recorded, DEFAULT_CONTAINER):
        if name and _container_has_klayout(name):
            return ContainerRunner(name)
    return None


def why_no_runner(container: Optional[str] = None, project=None) -> str:
    """Why :func:`find_runner` returned None, naming ONLY the places it looks.

    Review wave 4c: every caller blamed the PATH and $VIBEIC_EDA_CONTAINER,
    including on a host with a docker client, where the PATH is never asked
    -- an operator following it would install a host KLayout, which is
    exactly what must not be used."""
    if os.environ.get("VIBEIC_KLAYOUT_FORCE_ABSENT"):
        return "KLayout forced absent (VIBEIC_KLAYOUT_FORCE_ABSENT)"
    if _ce.no_container_route():
        return ("no container route (no docker client here), and neither "
                "strmrun nor klayout is on this process's PATH")
    recorded = container_the_run_recorded(project) if project else None
    looked = []
    for name in dict.fromkeys(n for n in (container, recorded, DEFAULT_CONTAINER) if n):
        refusal = _pin.container_attach_refusal(name)
        looked.append(f"{name!r}: {refusal}" if refusal else
                      f"{name!r}: not running, or no klayout on its PATH")
    return ("a container route exists (docker client on PATH), so KLayout runs "
            "only in the run's container and the host PATH is not asked; "
            "looked in " + ("; ".join(looked) or "no container"))


#: (root, subdir, name) triples already reported as "the override carries no
#: such engine", so a program that resolves the same engine repeatedly says it
#: once instead of once per call.
_ENV_MISS_REPORTED: set = set()


def _subdir_spellings(subdir: str) -> List[str]:
    """`subdir` plus the same directory name under the other separator style.

    The plugin names its program directories with UNDERSCORES (``metal_fill/``,
    ``gds_antenna/``) because they sit next to importable Python; the KLayout
    fork names the very same engines with HYPHENS (``metal-fill/``,
    ``gds-antenna/``, and likewise ``mp-color/``, ``perc-latchup/``,
    ``cmp-gradient/`` …). Neither convention is wrong and neither repository
    owns the other's layout, so the override accepts both rather than forcing a
    rename on one side. What the caller asked for is always tried first.
    """
    out = [subdir]
    for alt in (subdir.replace("_", "-"), subdir.replace("-", "_")):
        if alt not in out:
            out.append(alt)
    return out


def find_engine(subdir: str, name: str) -> Optional[Path]:
    """Locate a KLayout-fork engine script.

    Resolution order (first hit wins):
      1. ``$VIBEIC_KLAYOUT_TOOLS/<subdir>/<name>`` — a fork checkout / a newer
         engine baked into the container image, which OVERRIDES the vendored copy.
         ``<subdir>`` is tried in both separator spellings (see
         :func:`_subdir_spellings`), because the fork's own directory names use
         hyphens and every caller here passes the underscored plugin spelling;
      2. ``<programs>/<subdir>/<name>`` — the copy vendored into the plugin, so a
         clean install can reach the capability with no extra setup.

    When the override IS set but carries no such engine, falling back to the
    vendored copy is correct — doing it *silently* is not. A silent fall-through
    is indistinguishable from the override having worked, which is precisely how
    the spelling mismatch above survived unnoticed, so the miss is named on
    stderr (once per root/engine) and the fallback still happens.
    """
    env = os.environ.get("VIBEIC_KLAYOUT_TOOLS")
    env_cands: List[Path] = []
    if env:
        env_cands = [Path(env) / s / name for s in _subdir_spellings(subdir)]
    for c in env_cands:
        if c.is_file():
            return c
    if env_cands:
        key = (str(env), subdir, name)
        if key not in _ENV_MISS_REPORTED:
            _ENV_MISS_REPORTED.add(key)
            tried = ", ".join(str(c) for c in env_cands)
            print(f"[klayout-engine] VIBEIC_KLAYOUT_TOOLS={env} carries no "
                  f"{subdir}/{name} (tried: {tried}) — falling back to the "
                  f"vendored copy; the override is NOT in effect for this engine",
                  file=sys.stderr)
    vendored = Path(__file__).resolve().parent / subdir / name
    return vendored if vendored.is_file() else None
