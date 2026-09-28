#!/usr/bin/env python3
"""P0 structural front ends: Yosys elaboration and Verilator diagnostic codes.

The lint policy names only structural diagnostics previously checked by P0.
Other warnings remain visible, but do not turn design-style opinions into a
new P0 failure. Both tools must actually run; an absent tool is never PASS.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specrtl_common import rtl_source_files  # noqa: E402
import instrument_calibration as _calibration  # noqa: E402
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling


# The retired bit-range and port regex gates blocked range errors and
# unknown port names. Their warning-only width/unconnected reports remain
# visible in diagnostics without becoming a stricter new P0 policy.
BLOCKING_CODES = frozenset({"SELRANGE", "PINNOTFOUND", "MULTIDRIVEN"})

# RETIRED BY THIS FRONT END, and what blocks each one's finding now:
#   bitwidth_consistency_check  bitselect-out-of-range -> SELRANGE     (program deleted)
#   module_port_audit           port-name MISMATCH     -> PINNOTFOUND  (kept as a port
#                               parser for _staged_top_module and phase3_one_shot_runner)
# The record is `RETIRED_REGEX_GATES` in tests/test_p0_tool_frontend_migration.py,
# and it is kept out of this file on purpose: `gate_is_wired_check` credits a
# gate name written as a table literal in a program that can spawn as a
# dispatch, so a retirement record here would read as the retired gate's caller.


def default_image() -> str:
    """The image this check runs, resolved and never remembered.

    The pinned DIGEST (`_eda_pin.resolved_image_digest`, which honours an
    explicit override), run under whatever LOCAL reference holds those bytes
    (`_eda_pin.local_references_for_digest`, no network), and the composed
    pinned reference only when nothing local holds them. Local first is
    load-bearing: `resolved_image_digest` exports `<repo>@<digest>` into the
    environment, and on a host whose `VIBEIC_EDA_IMAGE_REPO` names a registry
    mirror that reference is not local, so handing it to `docker run` would
    fetch the image instead of running it.

    It used to be a module constant pinned to a tag (0.3.76), which went stale
    the moment the image moved; `test_the_eda_image_is_resolved_not_remembered`
    refuses that shape."""
    import _eda_pin
    try:
        digest = _eda_pin.resolved_image_digest()
    except _eda_pin.ImageNotResolvable:
        return _eda_pin.image_reference()
    refs, _why = _eda_pin.local_references_for_digest(digest)
    return refs[0] if refs else _eda_pin.image_reference()


_DIAGNOSTIC = re.compile(r"^%(Warning|Error)-([A-Z][A-Z0-9_]*):", re.M)


def _diagnostic_codes(log: str) -> list[dict[str, str]]:
    _calibration.assert_calibrated("p0_tool_frontend_check::_diagnostic_codes")
    return [{"severity": match.group(1), "code": match.group(2)}
            for match in _DIAGNOSTIC.finditer(log)]


def _top(project: Path) -> str | None:
    for name in ("L9_INTEGRATION_SPEC.json", "L9_INTEGRATION.json"):
        path = project / "phase1/generated_docs" / name
        if path.is_file():
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                return None
            top = value.get("top_module") if isinstance(value, dict) else None
            if isinstance(top, str) and re.fullmatch(r"[a-zA-Z_$][\w$]*", top):
                return top
    return None


def _route_image(tool: str, image: str | None) -> str | None:
    """The image `tool` will run in, or None when no image runs.

    RESOLVED ONLY ON THE DOCKER PATH, at the moment it is needed. A tool on
    PATH (natively, or inside the image itself) runs with nothing resolved; so
    does the no-docker case, which `_invoke` refuses as unavailable. Otherwise
    the declared `image` is used as is, else `default_image()` resolves it now
    and raises `_eda_pin.ImageNotResolvable` rather than guess."""
    if shutil.which(tool) or not shutil.which("docker"):
        return None
    return image or default_image()


#: How many of a failing tool's own lines a record carries inline. The whole
#: transcript is kept beside the record and cited by sha256.
ERROR_EXCERPT_LINES = 20


class ToolStalled(RuntimeError):
    """The tool made no forward progress across the stall grace (#2051): it
    was stopped, and nothing it would have said was measured. Elapsed time
    alone never raises this."""


def _invoke(tool: str, args: list[str], project: Path,
            image: str | None) -> subprocess.CompletedProcess[str]:
    """Run `tool` to completion however long it legitimately takes; stop it
    only on a progress STALL (#2051). On the docker path the container gets
    a name that is this invocation's own, its CPU is read inside it, and a
    stall reaps it BY THAT NAME (killing the client alone orphans the tool)."""
    if shutil.which(tool):
        import _progress_run
        try:
            return _progress_run.run([tool, *args], cwd=project,
                                     capture_output=True, text=True)
        except _progress_run.Stalled as exc:
            raise ToolStalled(f"{tool} stalled: {exc}") from exc
    if not shutil.which("docker"):
        raise FileNotFoundError(f"{tool} and docker unavailable")
    import _docker_watchdog as _dwd
    import _watchdog as _wd
    # Phase 2 needs only the released EDA image's tool binaries. LibreLane
    # CLI capability is neither requested nor assumed here.
    image = image or default_image()
    root = str(project.resolve())
    name = _dwd.ephemeral_container_name("vibeic_p0")
    command = ["docker", "run", "--rm", "--name", name,
               *_dmem.docker_memory_flags(), "--network", "none",
               "-v", f"{root}:{root}:ro", "--entrypoint", tool,
               image, *args]
    res = _wd.run_host_supervised(
        command, cwd=project,
        kill=_dwd.ephemeral_container_reap(name),
        cpu_probe=_dwd.ephemeral_container_cpu_probe(name))
    if res.outcome == "launch_error":
        raise FileNotFoundError(f"docker could not launch {tool}")
    if res.outcome == "stalled":
        raise ToolStalled(f"{tool} (container {name}) stalled: no forward "
                          f"progress across the stall grace; reaped by name")
    return subprocess.CompletedProcess(command, res.rc, res.out, res.err)


#: slang's own diagnostic grammar, `path:line:col: error: ...` (Yosys's
#: `ERROR:` line is only the summary trailer).
_SLANG_ERROR = re.compile(r"^\S+:\d+:\d+: error:")


def _error_lines(log: str) -> list[str]:
    """The tool's OWN lines behind a refusal, verbatim, first
    `ERROR_EXCERPT_LINES`: Verilator's ``%Error`` lines and the ``%Warning-``
    lines of the codes P0 blocks on, slang's ``: error:`` diagnostics, and
    Yosys's ``ERROR:`` trailer. A disclosure, not a verdict: the verdict is
    the exit code and the calibrated codes."""
    blocking = tuple(f"%Warning-{code}" for code in sorted(BLOCKING_CODES))
    return [line for line in log.splitlines()
            if line.startswith(("%Error", "ERROR:") + blocking)
            or _SLANG_ERROR.match(line)][:ERROR_EXCERPT_LINES]


def check(project: Path, image: str | None = None) -> dict:
    """`image` is the declared one (`--image`); None resolves it only if a
    tool actually has to run in docker. Each tool's WHOLE transcript is
    returned under the private ``_transcripts`` key for a caller that keeps
    it (the rtl content check writes and cites it); `main` does not print it."""
    # READ ORDER: packages in dependency order ahead of the RTL that uses
    # them. Verilator elaborates in ONE pass, so a file that names a package
    # type before the package is parsed fails "Reference to <type> before
    # declaration" (IEEE 1800-2023 6.18) however correct the design is;
    # read_slang --single-unit is order-tolerant, which is why Yosys passed
    # the same set. The shared rule, not a second one (#682).
    from rtl_transitive_cone import topological_package_first
    files = topological_package_first(rtl_source_files(project))
    result = {"program": "p0_tool_frontend_check", "passed": False,
              "sources": [str(p.relative_to(project)) for p in files],
              "tools": {}, "findings": []}
    if not files:
        result["findings"].append("no authored RTL source")
        return result
    top = _top(project)
    names = [str(p.resolve()) for p in files]
    if any(not re.fullmatch(r"[A-Za-z0-9_./-]+", name) for name in names):
        result["findings"].append("RTL path requires unsafe Yosys command escaping")
        return result
    include_dirs = sorted({str(p.parent.resolve()) for p in files})
    # read_slang is the released Yosys SV front end. hierarchy -check and
    # write_json are the JsonHeader elaboration checks, without Phase-3 config.
    selected_top = f"--top {top} " if top else ""
    hierarchy = f"-top {top}" if top else "-auto-top"
    script = ("read_slang --single-unit " + selected_top +
              " ".join("-I " + directory for directory in include_dirs) +
              " " + " ".join(names) +
              "; hierarchy -check " + hierarchy + "; proc; write_json /dev/null")
    import _eda_pin
    verilator_args = ["--lint-only", "--Wall", "-Wno-fatal"]
    if top:
        verilator_args += ["--top-module", top]
    runs = (("Yosys.JsonHeader", "yosys", ["-Q", "-T", "-p", script]),
            ("Verilator.Lint", "verilator",
             [*verilator_args, *("-I" + d for d in include_dirs), *names]))
    # Each tool runs and is recorded ON ITS OWN: a stall of one never
    # discards what the other measured, and a measured FAIL stays a FAIL.
    result["_transcripts"] = {}
    for label, tool, args in runs:
        try:
            tool_image = _route_image(tool, image)
            cp = _invoke(tool, args, project, tool_image)
        except _eda_pin.ImageNotResolvable as exc:
            result["findings"].append(f"tool invocation refused: {exc}")
            return result
        except (FileNotFoundError, OSError) as exc:
            result["findings"].append(f"tool invocation unavailable: {exc}")
            return result
        except ToolStalled as exc:
            result.setdefault("not_measured", {})[label] = str(exc)
            continue
        log = cp.stdout + cp.stderr
        row = {"exit_code": cp.returncode,
               "execution": "host" if shutil.which(tool) else tool_image,
               "errors": _error_lines(log),
               "output": log[-8000:]}
        if label == "Verilator.Lint":
            try:
                row["diagnostics"] = _diagnostic_codes(log)
            except _calibration.Uncalibrated as exc:
                result["findings"].append(f"tool diagnostic reader uncalibrated: {exc}")
                return result
        result["tools"][label] = row
        result["_transcripts"][label] = log
    yosys_row = result["tools"].get("Yosys.JsonHeader")
    lint_row = result["tools"].get("Verilator.Lint")
    if yosys_row and yosys_row["exit_code"]:
        result["findings"].append("Yosys elaboration failed")
    if lint_row and lint_row["exit_code"]:
        result["findings"].append("Verilator lint failed")
    for diagnostic in (lint_row or {}).get("diagnostics", []):
        if diagnostic["code"] in BLOCKING_CODES:
            result["findings"].append(
                f"Verilator {diagnostic['severity']}-{diagnostic['code']}")
    result["passed"] = not result["findings"] and not result.get("not_measured")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--image", default=None)
    args = parser.parse_args()
    if not args.project.is_dir():
        print("FAIL: project directory absent")
        return 2
    result = check(args.project.resolve(), args.image)
    result.pop("_transcripts", None)
    print(json.dumps(result, sort_keys=True))
    if result["findings"]:
        return 1            # a measured FAIL outranks another tool's stall
    return 2 if result.get("not_measured") else 0


if __name__ == "__main__":
    raise SystemExit(main())
