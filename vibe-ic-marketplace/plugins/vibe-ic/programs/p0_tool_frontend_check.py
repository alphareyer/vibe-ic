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


#: Every tool run is supervised by forward progress.  The old four-hour GNU
#: `timeout` killed healthy elaborations; the same number is now only a
#: recorded budget in `_watchdog`, which never kills a progressing job.
#: Docker carries the shared cgroup memory ceiling and an identity-bound reap;
#: native tools inherit the same memory policy as an RLIMIT_AS on the child.
STALL_ENV = "VIBEIC_P0_FRONTEND_STALL_S"
BUDGET_ENV = "VIBEIC_P0_FRONTEND_BACKSTOP_S"  # legacy spelling, now record-only
DEFAULT_BUDGET_S = 14_400


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default


def _budget_s() -> float:
    """The recorded budget; crossing it cannot stop the tool."""
    return _env_seconds(BUDGET_ENV, DEFAULT_BUDGET_S)


def _stall_s() -> float:
    import _watchdog as _wd
    return _env_seconds(STALL_ENV, _wd.DEFAULT_STALL_GRACE_S)


def _host_memory_ceiling_bytes() -> int | None:
    """Use the same explicit/default memory policy as the container path."""
    limit = _dmem.memory_limit()
    if limit is None:  # an explicit operator opt-out
        return None
    match = re.fullmatch(r"(\d+)([kmgtp]?)(?:b)?", limit.strip().lower())
    if match is None or int(match.group(1)) <= 0:
        raise OSError(f"invalid P0 memory ceiling {limit!r}")
    power = "kmgtp".find(match.group(2)) + 1 if match.group(2) else 0
    return int(match.group(1)) * 1024 ** power


def _limited_host_popen(ceiling: int):
    """Set RLIMIT_AS in the tool child, leaving the supervising host intact."""
    def launch(command, **kwargs):
        import resource

        def cap_address_space():
            _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            cap = min(ceiling, hard) if hard != resource.RLIM_INFINITY else ceiling
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

        kwargs.setdefault("start_new_session", True)
        return subprocess.Popen(command, preexec_fn=cap_address_space, **kwargs)

    return launch


class ToolNotMeasured(RuntimeError):
    """The tool run made no progress for its stall grace; never a finding."""

    def __init__(self, tool: str, how: str):
        super().__init__(f"{tool} {how}")
        self.tool, self.how = tool, how


def _invoke(tool: str, args: list[str], project: Path,
            image: str | None) -> subprocess.CompletedProcess[str]:
    """Run `tool` once under progress supervision, without a clock kill.

    On the docker path the container is NAMED for this invocation, its CPU is
    read from inside it (`_docker_watchdog.ephemeral_container_cpu_probe`), and
    a stall reaps it by that name (`ephemeral_container_reap`) -- killing the
    client alone would leave a `--rm` container holding its cores. Only a
    measured stall raises `ToolNotMeasured`."""
    import _watchdog as _wd
    import _docker_watchdog as _dwd
    kw: dict = {}
    if shutil.which(tool):
        command = [tool, *args]
        ceiling = _host_memory_ceiling_bytes()
        if ceiling is not None:
            kw["popen_factory"] = _limited_host_popen(ceiling)
    elif shutil.which("docker"):
        # Phase 2 needs only the released EDA image's tool binaries. LibreLane
        # CLI capability is neither requested nor assumed here.
        image = image or default_image()
        root = str(project.resolve())
        container = _dwd.ephemeral_container_name(f"vibeic_p0_{tool}")
        command = ["docker", "run", "--rm", "--name", container,
                   *_dmem.docker_memory_flags(),
                   "--network", "none",
                   "-v", f"{root}:{root}:ro", "--entrypoint", tool,
                   image, *args]
        kw = {"kill": _dwd.ephemeral_container_reap(container),
              "cpu_probe": _dwd.ephemeral_container_cpu_probe(container)}
    else:
        raise FileNotFoundError(f"{tool} and docker unavailable")
    res = _wd.run_host_supervised(command, cwd=str(project),
                                  stall_grace_s=_stall_s(),
                                  hard_ceiling_s=_budget_s(), **kw)
    if res.outcome == "launch_error":
        raise FileNotFoundError(f"{tool}: could not be launched")
    if res.outcome == "stalled" or res.rc == _wd.RC_STALLED:
        raise ToolNotMeasured(tool, f"made no progress for {_stall_s():g} s "
                                    f"({STALL_ENV}) and was stopped as STALLED")
    return _wd.completed_process(command, res)


#: FX_P2 — slang's declaration-order opt-out, and the disclosure that names it.
DECL_RELAX_FLAG = "--allow-use-before-declare"
DECL_RELAXED = "DECLARATION_ORDER_RELAXED"


#: slang's own diagnostic grammar: `<file>:<line>:<col>: error: <message>`, and
#: the ONE message the declaration-order retry may answer.
_SLANG_ERROR = re.compile(r"^\S+:\d+:\d+: error: (.*)$", re.M)
_SLANG_ERROR_AT = re.compile(r"^(\S+):\d+:\d+: error: (.*)$", re.M)
_USE_BEFORE_DECLARE = re.compile(
    r"^identifier '[^']+' used before its declaration$")
_BUILD_FAILED = re.compile(r"^Build failed: (\d+) errors?,", re.M)


def only_use_before_declare(log: str) -> bool:
    """True only when slang refused SOLELY on use-before-declare.

    At least one `error:` diagnostic, every one of them is slang's exact
    "identifier '<x>' used before its declaration", and slang's own
    `Build failed: N errors` agrees with that count -- so an error in another
    grammar, a timeout, or a transcript with no diagnostic never qualifies."""
    _calibration.assert_calibrated(
        "p0_tool_frontend_check::only_use_before_declare")
    errors = [m.group(1).strip() for m in _SLANG_ERROR.finditer(log or "")]
    if not errors or not all(_USE_BEFORE_DECLARE.match(e) for e in errors):
        return False
    counts = [int(m.group(1)) for m in _BUILD_FAILED.finditer(log or "")]
    return bool(counts) and counts[-1] == len(errors)


def _is_reused_ip(project: Path) -> bool:
    """The runner's `_is_reused_ip_project`, read through the SAME manifest
    loader (SOURCE_MANIFEST reused_ip:true); False on any error."""
    try:
        import l9_rtl_pin_consistency_check as _l9
        mf = _l9.load_source_manifest(project)
        return bool(isinstance(mf, dict) and mf.get("reused_ip") is True)
    except Exception:                                        # noqa: BLE001
        return False


def _use_before_declare_files(log: str) -> list[str]:
    """The file each slang use-before-declare diagnostic names, in order."""
    return [m.group(1) for m in _SLANG_ERROR_AT.finditer(log or "")
            if _USE_BEFORE_DECLARE.match(m.group(2).strip())]


def _sha256(path: Path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _supplied_ip_sources(project: Path) -> dict[str, str]:
    """`{resolved rtl path: where it came from}` for every RTL file that IS
    the supplied IP, UNEDITED -- byte-identical to what the input delivered.

    Two records say where a reused-IP file came from, and each is checked
    against the bytes, not the name:
      * SOURCE_MANIFEST `staged_from_input` (the design shipped its own RTL):
        the staged file must equal the input file it names;
      * provenance.jsonl `ip_catalog_pull` `outputs` (a catalog pull): the
        file's sha256 must equal the one recorded when it was pulled.
    A wrapper or chip_top the plugin authored appears in neither; a supplied
    file the plugin has since edited no longer matches. Both stay FAILs and go
    to repair."""
    out: dict[str, str] = {}
    try:
        import l9_rtl_pin_consistency_check as _l9
        mf = _l9.load_source_manifest(project) or {}
    except Exception:                                        # noqa: BLE001
        mf = {}
    names: dict[str, list[Path]] = {}
    for path in rtl_source_files(project):
        names.setdefault(path.name, []).append(path)
    for rel in mf.get("staged_from_input") or []:
        if not isinstance(rel, str):
            continue
        src = project / rel
        if not src.is_file():
            continue
        want = _sha256(src)
        # Every RTL file with that leaf whose BYTES are the input's is that
        # input's content; one the plugin edited, or wrote, is not.
        for dst in names.get(Path(rel).name, []):
            if want is not None and _sha256(dst) == want:
                out[str(dst.resolve())] = f"staged_from_input {rel}"
    prov = project / "provenance.jsonl"
    try:
        lines = prov.read_text(errors="replace").splitlines() \
            if prov.is_file() else []
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("event") != "ip_catalog_pull":
            continue
        for rel, recorded in (entry.get("outputs") or {}).items():
            dst = project / str(rel)
            if not dst.is_file() or not isinstance(recorded, str):
                continue
            if recorded.split(":", 1)[-1] == _sha256(dst):
                out[str(dst.resolve())] = (
                    f"ip_catalog_pull {entry.get('ip')} {rel}")
    return out


def _all_in_supplied_ip(project: Path, log: str) -> tuple[bool, list[str]]:
    """(every use-before-declare diagnostic is in an unedited supplied-IP
    file, the files that are not)."""
    files = _use_before_declare_files(log)
    supplied = _supplied_ip_sources(project)
    rtl = [str(p.resolve()) for p in rtl_source_files(project)]
    outside = []
    for name in files:
        key = _diagnostic_file(name, rtl)
        if key not in supplied and name not in outside:
            outside.append(name)
    return bool(files) and not outside, outside


def _diagnostic_file(name: str, rtl: list[str]) -> str | None:
    """The RTL file (resolved) a slang diagnostic names, or None.

    slang prints the path relative to ITS working directory, which is the
    image's, not ours: MEASURED in vibeic-eda 0.3.83 on a project under
    /home/reyerchu, `../../home/reyerchu/<project>/phase2/stage1/rtl/
    serv_state.v:111:52: error: ...`. So a relative path is matched by path
    SUFFIX (its leading `..` segments dropped) against the files slang was
    handed, and only a UNIQUE match counts; an absolute path is resolved."""
    if Path(name).is_absolute():
        try:
            return str(Path(name).resolve())
        except OSError:
            return None
    parts = [part for part in Path(os.path.normpath(name)).parts]
    while parts and parts[0] == "..":
        parts.pop(0)
    if not parts:
        return None
    tail = "/" + "/".join(parts)
    hits = [path for path in rtl if path.endswith(tail)]
    return hits[0] if len(hits) == 1 else None


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

    def _script(extra: str = "") -> str:
        return ("read_slang --single-unit " + extra + selected_top +
                " ".join("-I " + directory for directory in include_dirs) +
                " " + " ".join(names) +
                "; hierarchy -check " + hierarchy +
                "; proc; write_json /dev/null")
    import _eda_pin
    try:
        yosys_image = _route_image("yosys", image)
        yosys = _invoke("yosys", ["-Q", "-T", "-p", _script()], project,
                        yosys_image)
        # Orchestrator ruling (2026-09-28), the D3 review's two conditions:
        # (a) REUSED IP ONLY -- plugin-authored RTL that uses a name before
        #     declaring it stays a FAIL and goes to repair, which can edit it;
        # (b) only when EVERY error slang printed is its use-before-declare
        #     diagnostic (`only_use_before_declare`) -- never on any other
        #     refusal, a mix, or a run that printed none.
        # FX_P2 — the D3 rule at this site. slang, like strict Icarus, refuses
        # a net used before its declaration ("identifier 'x' used before its
        # declaration"); Verilator and read_verilog accept it. MEASURED on
        # subservient (reused serv 1.4.0): serv_state.v uses `trap_pending` at
        # 111/118 and declares it at 223, and this gate FAILed the P0 umbrella
        # over a closure that synthesises and simulates. Retried ONCE with
        # slang's own `--allow-use-before-declare`, which changes only name
        # lookup order and invents no net: only if THAT elaborates is the
        # refusal booked as declaration order, and it is DISCLOSED with the
        # strict output. An undeclared name, a missing module or a syntax
        # error still fails, with the strict output (checked in the image:
        # `use of undeclared identifier` under both).
        # FX_P2 review: condition (a) is PER FILE, not per project. A
        # reused-IP project still carries plugin-authored RTL (the wrapper,
        # chip_top) that repair can edit; only a diagnostic in a file that IS
        # the supplied IP, byte-identical to what the input delivered
        # (`_supplied_ip_sources`), may be relaxed.
        strict_log = yosys.stdout + yosys.stderr
        strict_rc = yosys.returncode
        in_ip, _outside = ((False, [])
                           if not (strict_rc and _is_reused_ip(project)
                                   and only_use_before_declare(strict_log))
                           else _all_in_supplied_ip(project, strict_log))
        if in_ip:
            relaxed = _invoke("yosys", ["-Q", "-T", "-p",
                                        _script(DECL_RELAX_FLAG + " ")],
                              project, yosys_image)
            if relaxed.returncode == 0:
                files = sorted({Path(f).name for f in
                                _use_before_declare_files(strict_log)})
                result["disclosures"] = [
                    f"{DECL_RELAXED}: strict read_slang refused (exit "
                    f"{strict_rc}) and {DECL_RELAX_FLAG} elaborated, so "
                    f"declaration order was the only obstacle -- in supplied "
                    f"IP only ({', '.join(files)}); a portability finding in "
                    f"the RTL, not an elaboration failure"]
                result["strict_refusal"] = strict_log[-4000:]
                result["strict_exit_code"] = strict_rc
                result["relaxed_flags"] = [DECL_RELAX_FLAG]
                yosys = relaxed
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
    except ToolNotMeasured as exc:
        # NOT A FINDING. A front end that stalled measured nothing, so it is
        # NOT_MEASURED(EXECUTION_ERROR), never a FAIL or a relaxed retry.
        result["not_measured"] = {
            "reason_class": "EXECUTION_ERROR",
            "why": f"tool invocation {exc}: nothing was measured"}
        return result
    except (FileNotFoundError, OSError) as exc:
        result["findings"].append(f"tool invocation unavailable: {exc}")
        return result
    result["tools"]["Yosys.JsonHeader"] = {"exit_code": yosys.returncode,
                                               "execution": "host" if shutil.which("yosys") else yosys_image,
                                               "output": (yosys.stdout + yosys.stderr)[-8000:]}
    if "strict_exit_code" in result:
        # The row says which run it shows: the strict refusal's exit code is
        # kept beside the relaxed run's, never overwritten by it.
        result["tools"]["Yosys.JsonHeader"].update(
            strict_exit_code=result["strict_exit_code"],
            relaxed_flags=list(result["relaxed_flags"]))
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
