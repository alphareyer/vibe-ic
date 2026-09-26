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


def _invoke(tool: str, args: list[str], project: Path,
            image: str | None) -> subprocess.CompletedProcess[str]:
    if shutil.which(tool):
        command = [tool, *args]
    elif shutil.which("docker"):
        # Phase 2 needs only the released EDA image's tool binaries. LibreLane
        # CLI capability is neither requested nor assumed here.
        image = image or default_image()
        root = str(project.resolve())
        command = ["docker", "run", "--rm", *_dmem.docker_memory_flags(),
                   "--network", "none",
                   "-v", f"{root}:{root}:ro", "--entrypoint", tool,
                   image, *args]
    else:
        raise FileNotFoundError(f"{tool} and docker unavailable")
    return subprocess.run(command, cwd=project, capture_output=True,
                          text=True, check=False)


def check(project: Path, image: str | None = None) -> dict:
    """`image` is the declared one (`--image`); None resolves it only if a
    tool actually has to run in docker."""
    files = rtl_source_files(project)
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
    try:
        yosys_image = _route_image("yosys", image)
        yosys = _invoke("yosys", ["-Q", "-T", "-p", script], project, yosys_image)
        verilator_args = ["--lint-only", "--Wall", "-Wno-fatal"]
        if top:
            verilator_args += ["--top-module", top]
        verilator_image = _route_image("verilator", image)
        verilator = _invoke("verilator", [*verilator_args,
                                          *("-I" + directory for directory in include_dirs),
                                          *names], project, verilator_image)
    except _eda_pin.ImageNotResolvable as exc:
        result["findings"].append(f"tool invocation refused: {exc}")
        return result
    except (FileNotFoundError, OSError) as exc:
        result["findings"].append(f"tool invocation unavailable: {exc}")
        return result
    result["tools"]["Yosys.JsonHeader"] = {"exit_code": yosys.returncode,
                                               "execution": "host" if shutil.which("yosys") else yosys_image,
                                               "output": (yosys.stdout + yosys.stderr)[-8000:]}
    lint_log = verilator.stdout + verilator.stderr
    try:
        diagnostics = _diagnostic_codes(lint_log)
    except _calibration.Uncalibrated as exc:
        result["findings"].append(f"tool diagnostic reader uncalibrated: {exc}")
        return result
    result["tools"]["Verilator.Lint"] = {"exit_code": verilator.returncode,
                                            "execution": "host" if shutil.which("verilator") else verilator_image,
                                            "diagnostics": diagnostics,
                                            "output": lint_log[-8000:]}
    if yosys.returncode:
        result["findings"].append("Yosys elaboration failed")
    if verilator.returncode:
        result["findings"].append("Verilator lint failed")
    for diagnostic in diagnostics:
        if diagnostic["code"] in BLOCKING_CODES:
            result["findings"].append(
                f"Verilator {diagnostic['severity']}-{diagnostic['code']}")
    result["passed"] = not result["findings"]
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
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
