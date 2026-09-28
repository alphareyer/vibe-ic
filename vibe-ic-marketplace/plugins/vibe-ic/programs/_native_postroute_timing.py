"""Post-route timing measured with the direct signoff RCX recipe.

LibreLane's RCX uses ``-lef_res``.  The direct Phase-3 signoff extracts with
``-corner_cnt 1 -max_res 50 -coupling_threshold 0.1``; on a routed candidate
those recipes can disagree at a zero-slack boundary.  This module measures the
same routed ODB at every declared RC/process scene before a controller accepts
setup or hold closure.  It never changes a route or a constraint.
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _docker_memory as _dmem

_SLACK = re.compile(r"^worst slack (max|min)\s+([-+]?\d+(?:\.\d+)?)\s*$", re.M)


def _one(mapping: dict[str, Any], corner: str, what: str) -> Any:
    values = [value for pattern, value in mapping.items() if fnmatch.fnmatch(corner, pattern)]
    if len(values) != 1:
        raise ValueError(f"{what}: {corner} matches {len(values)} entries")
    return values[0]


def _q(path: Any) -> str:
    """Quote a resolved, generated Tcl path without evaluating Tcl syntax."""
    value = str(path)
    if any(ch in value for ch in "{}\n\r"):
        raise ValueError(f"unsafe Tcl path: {value!r}")
    return "{" + value + "}"


def _run(ctx: dict[str, Any], script: Path, output: Path) -> None:
    import librelane_contract as ll
    project = Path(ctx["project"]).resolve()
    volumes = ["-v", f"{project}:{project}"]
    for host, guest in ctx["mounts"]:
        volumes.extend(["-v", f"{Path(host).resolve()}:{guest}:ro"])
    argv = ["docker", "run", *_dmem.docker_memory_flags(), "--rm", *volumes,
            "--entrypoint", "openroad", ctx["image"], "-no_init", "-exit", str(script)]
    output.unlink(missing_ok=True)
    result = ll.run_container(argv, supervised=True, log=script.with_suffix(".log"))
    script.with_suffix(".log").write_text((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise ll.Refusal("NATIVE_POSTROUTE_STA_FAILED",
                         f"{script}: rc={result.returncode}; output={output.is_file()}")


def measure(ctx: dict[str, Any], repair_state: Path, out_dir: Path) -> dict[str, Any]:
    """Return WNS for all declared scenes, refusing any missing/ambiguous view."""
    import librelane_contract as ll
    state = json.loads(repair_state.read_text())
    odb = Path(state["odb"])
    sdc = Path(state["sdc"])
    if not odb.is_file() or not sdc.is_file():
        raise ll.Refusal("NATIVE_POSTROUTE_INPUT_MISSING", f"{odb}; {sdc}")
    cfg = json.loads(Path(ctx["configs"]["OpenROAD.RCX"]).read_text())
    corners = ctx["corners"]
    rulesets = cfg["RCX_RULESETS"]
    cell_libs = cfg["CELL_LIBS"]
    pad_libs = cfg.get("PAD_LIBS") or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    default_libs = _one(cell_libs, cfg["DEFAULT_CORNER"], "default cell liberty")
    # Reuse one SPEF per RC ruleset.  The direct signoff's three parasitic
    # scenes (nom/min/max) can each serve every matching process library.
    parasitics: dict[str, Path] = {}
    setup: dict[str, float] = {}
    hold: dict[str, float] = {}
    for corner in corners:
        rules = _one(rulesets, corner, "RCX ruleset")
        if rules not in parasitics:
            index = len(parasitics)
            spef = out_dir / f"rc_{index}.spef"
            script = out_dir / f"extract_{index}.tcl"
            lines = [f"read_db {_q(odb)}"]
            lines += [f"read_liberty {_q(lib)}" for lib in default_libs]
            lines += ["if {[catch {set_wire_rc -signal -layer Metal1} _s]} { catch {set_wire_rc -layer Metal1} }",
                      "catch {set_wire_rc -clock -layer Metal5}",
                      "catch {define_process_corner -ext_model_index 0 X}",
                      f"extract_parasitics -ext_model_file {_q(rules)} -corner_cnt 1 -max_res 50 -coupling_threshold 0.1",
                      f"write_spef {_q(spef)}", "exit"]
            script.write_text("\n".join(lines) + "\n")
            _run(ctx, script, spef)
            parasitics[rules] = spef
        report = out_dir / f"sta_{corner}.rpt"
        script = out_dir / f"sta_{corner}.tcl"
        libs = _one(cell_libs, corner, "cell liberty")
        pads = _one(pad_libs, corner, "pad liberty") if pad_libs else []
        lines = [f"read_db {_q(odb)}"]
        lines += [f"read_liberty {_q(lib)}" for lib in [*libs, *pads]]
        lines += [f"read_sdc {_q(sdc)}", f"read_spef {_q(parasitics[rules])}",
                  "set_propagated_clock [all_clocks]",
                  f"report_worst_slack -max -digits 6 > {_q(report)}",
                  f"report_worst_slack -min -digits 6 >> {_q(report)}", "exit"]
        script.write_text("\n".join(lines) + "\n")
        _run(ctx, script, report)
        slacks = {kind: float(value) for kind, value in _SLACK.findall(report.read_text())}
        if set(slacks) != {"max", "min"}:
            raise ll.Refusal("NATIVE_POSTROUTE_SLACK_MISSING", f"{corner}: {report}")
        setup[corner] = slacks["max"]
        hold[corner] = slacks["min"]
    if not setup or set(setup) != set(corners) or set(hold) != set(corners):
        raise ll.Refusal("NATIVE_POSTROUTE_SCENE_MISSING", str(out_dir))
    return {"setup_ws": setup, "hold_ws": hold,
            "setup_ws_min": min(setup.values()), "hold_ws_min": min(hold.values()),
            "native_timing_dir": str(out_dir),
            "native_odb_sha256": ll.digest(odb), "native_sdc_sha256": ll.digest(sdc),
            "native_spef_sha256": {rules: ll.digest(path) for rules, path in parasitics.items()},
            "measurement_basis": "direct_phase3_rcx_recipe+post_route_spef+flat_ocv"}
