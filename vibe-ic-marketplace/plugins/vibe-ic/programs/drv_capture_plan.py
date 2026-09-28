#!/usr/bin/env python3
"""Build a DRV capture request from the routed run's own STA state.

The plan is an input inventory, never a sign-off assertion.  Missing inputs
raise an error; absent implementation-stage receipts remain absent for the
judge to report.  The final STAPostPNR state is the source of scene identity.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_text
from drv_signoff_judge import (_SCENE_PROFILES, _integrator_value,
                               _liberty_header, _sdc_values, _sha)


def _ref(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"run input absent: {path}")
    return {"path": str(path.resolve()), "sha256": _sha(path)}


def _run_path(project: Path, value: str) -> Path:
    """Rebase an immutable run-state path when evaluating a copied project."""
    path = Path(value)
    if path.is_absolute():
        parts = path.parts
        for marker in ("phase3", "phase1", "input", "reports"):
            if marker in parts:
                return project.joinpath(*parts[parts.index(marker):])
    return project / path if not path.is_absolute() else path


def _state(project: Path) -> tuple[dict, Path]:
    repair = project / "reports/phase3/librelane_postroute_repair.json"
    if repair.is_file():
        receipt = json.loads(repair.read_text())
        final = receipt.get("final") or {}
        path = _run_path(project, str(final.get("sta_state") or ""))
        if not path.is_file() or _sha(path) != final.get("sta_state_sha256"):
            raise ValueError("final STAPostPNR state absent or differs from repair receipt")
        return json.loads(path.read_text()), path
    candidates = sorted((project / "phase3/librelane").glob("**/04-openroad-stapostpnr/state_out.json"))
    if len(candidates) != 1:
        raise ValueError("final STAPostPNR state is not uniquely identified")
    return json.loads(candidates[0].read_text()), candidates[0]


def _env(scene_dir: Path) -> dict[str, str]:
    files = list(scene_dir.glob("_env_*.tcl"))
    if len(files) != 1:
        raise ValueError(f"scene environment not unique: {scene_dir}")
    result = {}
    for line in files[0].read_text().splitlines():
        match = re.match(r"set ::env\(([^)]+)\) (.*)$", line)
        if match:
            result[match.group(1)] = match.group(2).strip('"')
    return result


def _pdk_path(value: str, root: Path, pdk: str) -> Path:
    if value.startswith("/pdk/" + pdk + "/"):
        return root / value[len("/pdk/"):]
    return Path(value)


def _scene_files(env: dict, root: Path, pdk: str, pvt: str,
                 macro_lib: Path) -> tuple[list[dict], list[dict]]:
    libs = []
    lefs = []
    for key in ("CELL_LIBS", "PAD_LIBS"):
        paths = re.findall(r"/[^\s\"\\]+\.lib\b", env.get(key, ""))
        selected = [_pdk_path(value, root, pdk) for value in paths if pvt in value]
        for path in selected:
            libs.append({"name": path.stem, **_ref(path)})
    if macro_lib.is_file():
        libs.append({"name": macro_lib.stem, **_ref(macro_lib)})
    for key in ("CELL_LEFS", "PAD_LEFS", "MACRO_LEFS"):
        for value in re.findall(r"/[^\s\"\\]+\.lef\b", env.get(key, "")):
            path = _pdk_path(value, root, pdk)
            lefs.append(_ref(path))
    if not libs or not lefs:
        raise ValueError("linked scene Liberty or LEF inventory absent")
    return libs, lefs


def build(project: Path) -> dict:
    project = project.resolve()
    state, state_path = _state(project)
    provenance = json.loads((project / "phase3/librelane_pdk_root.provenance.json").read_text())
    root = Path(provenance["path"])
    pdk = provenance["derivation"]["pdk"]
    image = provenance["derivation"]["image"]
    netlist = _run_path(project, state["nl"])
    top = netlist.name.removesuffix(".nl.v")
    routed = project / "phase3/stage3/pnr/routed.def"
    artifacts = {"sta_netlist": _ref(netlist),
                 "lvs_netlist": _ref(netlist), "gds_netlist": _ref(netlist),
                 "odb": _ref(_run_path(project, state["odb"])),
                 "def": _ref(routed)}
    if _sha(_run_path(project, state["def"])) != artifacts["def"]["sha256"]:
        raise ValueError("final STA DEF differs from routed DEF")
    scene_root = state_path.parent
    profile = next((v for k, v in json.loads(_SCENE_PROFILES.read_text()).items()
                    if pdk.lower().startswith(k.lower())), None)
    if profile is None:
        raise ValueError("PDK has no declared DRV scene profile")
    expected = {f"{pvt}_{rc}" for pvt in profile["pvt"]
                for rc in profile["rc_corners"]}
    actual_scenes = {f"{rc}_{pvt}" for pvt in profile["pvt"]
                     for rc in profile["rc_corners"]}
    if set(state.get("lib") or {}) != actual_scenes:
        raise ValueError("final STAPostPNR state lacks required scenes")
    scene_libs = {}
    liberties = {}
    scenes = []
    signoff_sdc = None
    for name in sorted(expected):
        pvt, _, rc = name.rpartition("_")
        actual_name = f"{rc}_{pvt}"
        folder = scene_root / actual_name
        env = _env(folder)
        sdc = _run_path(project, env["SIGNOFF_SDC_FILE"])
        if signoff_sdc is not None and sdc != signoff_sdc:
            raise ValueError("scene sign-off SDC differs")
        signoff_sdc = sdc
        macro = _run_path(project, state["lib"][actual_name])
        linked, lefs = _scene_files(env, root, pdk, pvt, macro)
        for item in linked:
            existing = liberties.setdefault(item["name"], item)
            if existing != item:
                raise ValueError("linked Liberty identity collision")
        scene_libs[name] = [item["name"] for item in linked]
        spef = _run_path(project, state["spef"][rc + "_*"])
        scenes.append({"name": name, "mode": "functional", "rc_corner": rc,
                       "spef": _ref(spef), "spef_layout_sha256": artifacts["def"]["sha256"],
                       "liberty": linked[0]["name"], "linked_liberties": linked,
                       "linked_lefs": lefs,
                       "positive_control_limits": {"max_fanout": 1,
                                                   "max_slew": 1e-6,
                                                   "max_capacitance": 1e-6}})
    if signoff_sdc is None:
        raise ValueError("sign-off SDC absent")
    pdk_config = root / pdk / "libs.tech/librelane" / env["STD_CELL_LIBRARY"] / "config.tcl"
    sources = {"pdk_config": _ref(pdk_config), "signoff_sdc": _ref(signoff_sdc)}
    for layer in ("L7", "L9"):
        paths = [p for directory in ("input/docs", "phase1/generated_docs")
                 for p in sorted((project / directory).glob(layer + "*")) if p.is_file()]
        if not paths:
            raise ValueError(f"{layer} declaration absent")
        sources[layer.lower()] = _ref(paths[0])
    sdc_text = signoff_sdc.read_text()
    pdk_text = pdk_config.read_text()
    from declared_knob_applied_parity_check import collect_declared
    declared = collect_declared(project, pdk=pdk, library=env["STD_CELL_LIBRARY"])
    integrator_fanout = _integrator_value(pdk_text, "MAX_FANOUT_CONSTRAINT", "SYNTH_MAX_FANOUT")
    values = {"fanout": (declared.get("SYNTH_MAX_FANOUT") or (integrator_fanout,))[0],
              "slew_ns": _integrator_value(pdk_text, "MAX_TRANSITION_CONSTRAINT", "MAX_SLEW_CONSTRAINT"),
              "cap_pf": _integrator_value(pdk_text, "MAX_CAPACITANCE_CONSTRAINT", "MAX_CAP_CONSTRAINT"),
              "default_fanout_ceiling": integrator_fanout,
              "period_ns": None, "io_delay_ns": None}
    for field, command in (("fanout", "set_max_fanout"),
                           ("slew_ns", "set_max_transition"),
                           ("cap_pf", "set_max_capacitance")):
        values[field + "_sdc_observed"] = _sdc_values(sdc_text, command)
    frozen = {"sources": {k: v["sha256"] for k, v in sources.items()},
              "liberties": {k: v["sha256"] for k, v in liberties.items()},
              "values": values, "scope": "whole final netlist",
              "scenes": sorted(expected), "scene_profile_sha256": _sha(_SCENE_PROFILES),
              "scene_liberties": scene_libs, "rc_corners": profile["rc_corners"],
              "pvt": {name: _liberty_header(Path(next(item["path"] for item in scene["linked_liberties"]
                                                     if item["name"] == scene["liberty"])).read_text())
                      for name in sorted(expected)
                      for scene in scenes if scene["name"] == name}}
    current = {"sources": sources, "liberties": list(liberties.values()),
               "values": values, "scope": frozen["scope"],
               "scenes": frozen["scenes"], "scene_liberties": scene_libs}
    identity = {"project": str(project), "run_id": project.name,
                "tree_sha": None, "spec_version": None,
                "pdk": pdk, "library": env["STD_CELL_LIBRARY"],
                "artifacts": artifacts,
                **{key: artifacts[key]["sha256"] for key in
                   ("sta_netlist", "lvs_netlist", "gds_netlist")}}
    return {"top": top, "identity": identity, "frozen": frozen,
            "current": current, "stages": [], "pins": {}, "scenes": scenes,
            "postroute_repair_ran": bool((project / "reports/phase3/librelane_postroute_repair.json").is_file())}


def publish(project: Path) -> Path:
    output = project / "reports/phase3/sta/drv_capture_plan.json"
    plan = build(project)
    write_text(output, json.dumps(plan, indent=2) + "\n")
    return output
