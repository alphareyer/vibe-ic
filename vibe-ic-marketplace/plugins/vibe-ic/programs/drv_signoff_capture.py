#!/usr/bin/env python3
"""Capture raw post-route DRV evidence in fresh pinned OpenSTA processes.

The plan names actual routed inputs and stage receipts; this program never
invents a missing stage, pin class, owner approval, or threshold.  It writes a
bundle for drv_signoff_judge, which remains the sole verdict authority.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_text  # noqa: E402
import _eda_image  # noqa: E402
from drv_signoff_judge import KINDS, _COMMAND, _sha  # noqa: E402

_COUNTER = re.compile(
    r"(?m)^DRV_COUNTER\s+(max_slew|max_capacitance|max_fanout)\s+(\d+)\s*$")


def _ref(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"capture input absent: {path}")
    return {"path": str(path.resolve()), "sha256": _sha(path)}


def _tcl(path: Path) -> str:
    value = str(path.resolve())
    if any(c in value for c in "{}\\\n\r"):
        raise ValueError("unsafe Tcl path")
    return "{" + value + "}"


def _run_fresh(script: Path, roots: set[Path], *, image: str) -> str:
    mounts = []
    for root in sorted(roots):
        root = root.resolve()
        mounts.extend(("-v", f"{root}:{root}"))
    proc = subprocess.run(
        ["docker", "run", "--rm", *mounts, image, "--skip", "bash", "-c",
         f"sta -no_init -exit {shlex.quote(str(script.resolve()))}"],
        capture_output=True, text=True, check=False)
    body = proc.stdout + "\n" + proc.stderr
    if proc.returncode or re.search(r"(?m)^Error(?:\s|:)", body):
        raise RuntimeError(f"fresh OpenSTA process failed rc={proc.returncode}: "
                           + body[-1000:])
    return body


def _script(plan: dict, scene: dict, out: Path, *, control: bool,
            max_count: int) -> str:
    libs = [Path(item["path"]) for item in scene["linked_liberties"]]
    for path in libs:
        _ref(path)
    netlist = Path(plan["identity"]["artifacts"]["sta_netlist"]["path"])
    sdc = Path(plan["current"]["sources"]["signoff_sdc"]["path"])
    spef = Path(scene["spef"]["path"])
    for path in (netlist, sdc, spef):
        _ref(path)
    prefix = "".join(f"read_liberty {_tcl(path)}\n" for path in libs)
    prefix += (f"read_verilog {_tcl(netlist)}\n"
               f"link_design {{{plan['top']}}}\n"
               f"read_sdc {_tcl(sdc)}\n"
               f"read_spef {_tcl(spef)}\n"
               "set_propagated_clock [all_clocks]\n")
    if control:
        limits = scene["positive_control_limits"]
        prefix += ("set_max_fanout 1 [current_design]\n"
                   f"set_max_transition {limits['max_slew']} [current_design]\n"
                   f"set_max_capacitance {limits['max_capacitance']} [current_design]\n")
        return (prefix + f"{_COMMAND} -digits 6 -max_count {max_count} > {_tcl(out / 'positive_control.rpt')}\n"
                f"set _f [open {_tcl(out / 'positive_control_counters.log')} w]\n"
                + "".join(
                    f'puts $_f "DRV_COUNTER {kind} [sta::{kind}_violation_count]"\n'
                    for kind in KINDS) + "close $_f\n")
    return (prefix
            + f"report_parasitic_annotation -report_unannotated > {_tcl(out / 'annotation.rpt')}\n"
            + f"report_clock_properties [all_clocks] > {_tcl(out / 'clocks.rpt')}\n"
            + f"{_COMMAND} -digits 6 -max_count {max_count} > {_tcl(out / 'violators.rpt')}\n"
            + f"report_check_types -max_slew -max_capacitance -max_fanout -verbose -digits 6 -max_count {max_count} > {_tcl(out / 'all_limits.rpt')}\n"
            + f"set _f [open {_tcl(out / 'counters.log')} w]\n"
            + "".join(
                f'puts $_f "DRV_COUNTER {kind} [sta::{kind}_violation_count]"\n'
                for kind in KINDS) + "close $_f\n")


def capture(plan: dict, out_dir: Path, *, image: str | None = None) -> dict:
    """Run each scene and its planted control; return a content-addressed bundle."""
    import instrument_calibration
    instrument_calibration.assert_calibrated("drv_signoff_capture::capture")
    image = image or _eda_image.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {k: plan[k] for k in ("identity", "frozen", "current", "stages",
                                  "pins")}
    bundle["identity"] = dict(bundle["identity"])
    image_info = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"],
        capture_output=True, text=True, check=False)
    try:
        image_doc = json.loads(image_info.stdout)
        digest = image_doc["Id"]
        version = (image_doc.get("Config") or {}).get("Labels", {}).get(
            "org.opencontainers.image.version")
    except (ValueError, TypeError, KeyError, AttributeError):
        digest, version = None, None
    if image_info.returncode or not str(digest).startswith("sha256:"):
        raise ValueError("pinned OpenSTA image identity unavailable")
    bundle["identity"]["tool_image"] = image
    bundle["identity"]["tool_image_digest"] = digest
    bundle["identity"]["tool_image_oci_version"] = version
    bundle["postroute_repair_ran"] = bool(plan.get("postroute_repair_ran"))
    bundle["scenes"] = []
    roots = {out_dir}
    for scene in plan["scenes"]:
        for item in (*scene["linked_liberties"], scene["spef"],
                     plan["identity"]["artifacts"]["sta_netlist"],
                     plan["current"]["sources"]["signoff_sdc"]):
            roots.add(Path(item["path"]).resolve().parent)
        scene_dir = out_dir / scene["name"]
        scene_dir.mkdir(parents=True, exist_ok=True)
        population = scene["population"]
        max_count = max(population.values())
        if max_count < 1:
            raise ValueError("empty all-limits population")
        for control in (False, True):
            script = scene_dir / ("positive_control.tcl" if control else "measure.tcl")
            write_text(script, _script(plan, scene, scene_dir,
                                       control=control, max_count=max_count))
            tool_output = _run_fresh(script, roots, image=image)
            commit = re.search(r"OpenSTA\s+\S+\s+([0-9a-f]{10,40})", tool_output)
            if not commit:
                raise ValueError("fresh process did not report OpenSTA commit")
            bundle["identity"]["opensta_commit"] = commit.group(1)
        counts = dict((kind, int(value)) for kind, value in _COUNTER.findall(
            (scene_dir / "counters.log").read_text()))
        control_counts = dict((kind, int(value)) for kind, value in _COUNTER.findall(
            (scene_dir / "positive_control_counters.log").read_text()))
        if set(counts) != set(KINDS) or set(control_counts) != set(KINDS):
            raise ValueError("OpenSTA did not emit every DRV counter")
        annotation = (scene_dir / "annotation.rpt").read_text()
        unannotated = re.findall(
            r"Found\s+(\d+)\s+(?:partially\s+)?unannotated\s+(?:drivers|nets)",
            annotation)
        row = dict(scene)
        row.update(fresh_process=True, postroute=True, propagated_clocks=True,
                   command=_COMMAND, all_limits_max_count=max_count,
                   positive_control_fresh_process=True,
                   counters=counts, positive_control_counters=control_counts,
                   unannotated_nets=(sum(map(int, unannotated))
                                     if unannotated else None),
                   report=_ref(scene_dir / "violators.rpt"),
                   all_limits_report=_ref(scene_dir / "all_limits.rpt"),
                   counter_report=_ref(scene_dir / "counters.log"),
                   positive_control_report=_ref(scene_dir / "positive_control.rpt"),
                   parasitic_annotation_report=_ref(scene_dir / "annotation.rpt"),
                   clock_properties=_ref(scene_dir / "clocks.rpt"),
                   tool_scripts=[_ref(scene_dir / "measure.tcl"),
                                 _ref(scene_dir / "positive_control.tcl")])
        bundle["scenes"].append(row)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text())
        bundle = capture(plan, args.out.parent / "drv_capture")
        write_text(args.out, json.dumps(bundle, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"DRV capture NOT_MEASURED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
