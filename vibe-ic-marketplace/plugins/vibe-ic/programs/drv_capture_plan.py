#!/usr/bin/env python3
"""Build a DRV capture request from the routed run's own STA state.

The plan is an input inventory, never a sign-off assertion.  Missing inputs
raise an error.  Implementation-stage evidence is read from the receipts each
stage recorded when it ran (`STAGE_RECEIPT_DIR`); a stage without one stays
absent, which the judge reports as FAIL (DRV standard section 1: a stage whose
applied value cannot be extracted is FAIL, never N/A or NOT_MEASURED).  Identity
the run cannot hold before stream-out (GDS / LVS netlist) is named in
``identity["bound_at"]`` as bound at the final post-stream capture
(R-0929-DRV-IDENTITY); run and specification identity come from the record
taken at run start (`drv_run_identity`) and stay None, i.e. NOT_MEASURED,
when absent.  The final STAPostPNR state is the
source of scene identity.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_text
from drv_signoff_judge import (_NUM, _REQUIRED_STAGES, _SCENE_PROFILES,
                               _integrator_value, _liberty_header, _sdc_values,
                               _sha)

#: Where an implementation stage records its DRV-constraint receipt, one JSON
#: document per stage named ``<stage>.json`` (``synth``, ``placement_repair``,
#: ``cts``, ``post_grt_repair``, ``signoff_sta`` and, when step 32 adopted a
#: candidate, ``postroute_repair``).  Each document carries ``name``, ``ran``
#: and ``{"path", "sha256"}`` references written when the stage ran:
#: ``behavior_report`` (the declared-value DRV census, including a
#: ``sta::max_fanout_check_limit <value>`` line for an OpenROAD stage),
#: ``abc_script`` (synth) or ``sdc_snapshot`` (the ``write_sdc`` taken in the
#: same process on the line before the stage command).  The applied values are
#: derived here from those hashed bytes, never taken from the receipt's prose.
STAGE_RECEIPT_DIR = Path("reports/phase3/drv_stages")

#: Identity an in-flow capture cannot bind (R-0929-DRV-IDENTITY): it exists
#: only after stream-out, so it is "bound at" the final post-stream capture --
#: neither missing evidence nor NOT_MEASURED here.
_BOUND_AT_POST_STREAM = {
    "lvs_netlist": "the final post-stream DRV sign-off capture (after LVS)",
    "gds_netlist": "the final post-stream DRV sign-off capture (after stream-out)",
}


def _run_identity(project: Path) -> dict:
    """The run and specification identity an in-flow capture binds: the run
    id and code identity recorded at run start (`drv_run_identity.record`)
    and the Phase-1 document digest.  An absent record stays None, which the
    judge reports NOT_MEASURED; nothing is invented."""
    import drv_run_identity
    record = drv_run_identity.load(project)
    return {"run_id": record.get("run_id"),
            "tree_sha": record.get("plugin_tree_sha256"),
            "code_identity": {k: record.get(k) for k in (
                "plugin_source_commit", "plugin_tree_sha256", "recorded_at")},
            "spec_version": drv_run_identity.spec_version(project),
            "capture_point": "in_flow",
            "bound_at": dict(_BOUND_AT_POST_STREAM)}


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


def _state(project: Path, final_state: dict | None = None) -> tuple[dict, Path]:
    if final_state is not None:
        final = final_state.get("final") or {}
        path = _run_path(project, str(final.get("sta_state") or ""))
        if not path.is_file() or _sha(path) != final.get("sta_state_sha256"):
            raise ValueError("final STAPostPNR state absent or differs from step 32")
        return json.loads(path.read_text()), path
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


def _recorded_ref(project: Path, item: object) -> dict | None:
    """Keep the hash a stage recorded; the judge re-reads the bytes."""
    if not isinstance(item, dict) or not item.get("path"):
        return None
    return {"path": str(_run_path(project, str(item["path"]))),
            "sha256": item.get("sha256")}


def _recorded_text(ref: dict | None) -> str:
    """Bytes a stage recorded, or nothing when they changed since."""
    if not ref or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256") or "")):
        return ""
    path = Path(ref["path"])
    if not path.is_file() or _sha(path) != ref["sha256"]:
        return ""
    return path.read_text(errors="replace")


def _one(values: list[float]) -> float | None:
    return values[0] if values and len(set(values)) == 1 else None


def _stages(project: Path, postroute_repair_ran: bool) -> tuple[list[dict], dict]:
    """Derive each required stage row from the receipt the stage recorded."""
    names = [*_REQUIRED_STAGES, *(("postroute_repair",) if postroute_repair_ran else ())]
    rows: list[dict] = []
    inventory: dict[str, dict] = {}
    for name in names:
        path = project / STAGE_RECEIPT_DIR / f"{name}.json"
        entry = {"receipt": str(path)}
        inventory[name] = entry
        if not path.is_file():
            entry["status"] = "absent"
            continue
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or doc.get("name") != name:
            entry["status"] = "unreadable or names another stage"
            continue
        entry.update(status="recorded", sha256=_sha(path))
        row: dict = {"name": name, "ran": doc.get("ran") is True}
        for field in ("behavior_report", "abc_script", "sdc_snapshot"):
            ref = _recorded_ref(project, doc.get(field))
            if ref is not None:
                row[field] = ref
        if name == "synth":
            script = _recorded_text(row.get("abc_script"))
            row["applied"] = {"fanout": _one([float(v) for v in re.findall(
                rf"\bbuffer\s+-N\s+({_NUM})\b", script)])}
            row["synth_abc_buffering"] = doc.get("synth_abc_buffering") is True
            row["ideal_clock_excluded"] = doc.get("ideal_clock_excluded") is True
        else:
            snapshot = _recorded_text(row.get("sdc_snapshot"))
            row["applied"] = {field: _one(_sdc_values(snapshot, command))
                              for field, command in (
                                  ("fanout", "set_max_fanout"),
                                  ("slew_ns", "set_max_transition"),
                                  ("cap_pf", "set_max_capacitance"))}
            behavior = _recorded_text(row.get("behavior_report"))
            row["fanout_check_limit"] = _one([float(v) for v in re.findall(
                rf"(?m)^\s*sta::max_fanout_check_limit\s+({_NUM})\s*$", behavior)])
            if name == "cts":
                row["cts_parameters"] = doc.get("cts_parameters")
                row["clock_driver_fanout"] = doc.get("clock_driver_fanout")
        rows.append(row)
    return rows, inventory


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


def _clock_io_values(sdc: str) -> tuple[float | None, float | None]:
    """Read applied numeric clock and IO constraints from the run's SDC."""
    numbers = r"[0-9]+(?:\.[0-9]+)?"
    periods = re.findall(rf"(?m)^\s*create_clock\b[^\n;]*?-period\s+({numbers})(?=\s|$)", sdc)
    delays = re.findall(rf"(?m)^\s*set_(?:input|output)_delay\s+({numbers})(?=\s|$)", sdc)
    period = float(periods[0]) if len(set(periods)) == 1 and periods else None
    io_delay = float(delays[0]) if len(set(delays)) == 1 and delays else None
    return period, io_delay


def _scene_files(env: dict, root: Path, pdk: str, pvt: str) -> tuple[list[dict], list[dict]]:
    libs = []
    lefs = []
    for key in ("CELL_LIBS", "PAD_LIBS"):
        paths = re.findall(r"/[^\s\"\\]+\.lib\b", env.get(key, ""))
        selected = [_pdk_path(value, root, pdk) for value in paths if pvt in value]
        for path in selected:
            libs.append({"name": path.stem, **_ref(path)})
    for key in ("CELL_LEFS", "PAD_LEFS", "MACRO_LEFS"):
        for value in re.findall(r"/[^\s\"\\]+\.lef\b", env.get(key, "")):
            path = _pdk_path(value, root, pdk)
            lefs.append(_ref(path))
    if not libs or not lefs:
        raise ValueError("linked scene Liberty or LEF inventory absent")
    return libs, lefs


def _direct_script(path: Path) -> dict:
    """Read only literal inputs from the post-route STA deck that actually ran."""
    if not path.is_file():
        raise ValueError(f"direct post-route STA deck absent: {path}")
    result: dict[str, object] = {"liberties": []}
    commands = {"read_verilog": "netlist", "read_sdc": "sdc",
                "read_spef": "spef", "link_design": "top"}
    for line in path.read_text().splitlines():
        words = line.strip().split()
        if not words:
            continue
        if words[0] == "read_liberty" and len(words) == 2:
            result["liberties"].append(words[1])
        elif words[0] in commands and len(words) == 2:
            key = commands[words[0]]
            if key in result:
                raise ValueError(f"direct STA deck repeats {words[0]}")
            result[key] = words[1]
    if any(not result.get(key) for key in ("netlist", "sdc", "spef", "top", "liberties")):
        raise ValueError("direct STA deck lacks a frozen input")
    return result


def _build_direct(project: Path) -> dict:
    """Capture the scenes a direct OCV deck really used, leaving gaps visible."""
    import _eda_image
    import librelane_contract
    sta_dir = project / "phase3/stage3/sta"
    decks = [_direct_script(sta_dir / f"sta_mcorner_ocv_{kind}.tcl")
             for kind in ("setup", "hold")]
    if any(decks[0][key] != decks[1][key] for key in ("netlist", "sdc", "top")):
        raise ValueError("direct setup and hold decks use different routed identities")
    library_path = Path(decks[0]["liberties"][0])
    parts = library_path.parts
    if "libs.ref" not in parts:
        raise ValueError("direct linked Liberty lacks installed PDK identity")
    i = parts.index("libs.ref")
    pdk, library = parts[i - 1], parts[i + 1]
    image = _eda_image.resolve()
    resolved_root = librelane_contract.resolve_pdk_root(project, pdk, image=image)
    if not resolved_root:
        raise ValueError("direct STA PDK root unavailable")
    root = Path(resolved_root)
    profile = next((v for k, v in json.loads(_SCENE_PROFILES.read_text()).items()
                    if pdk.lower().startswith(k.lower())), None)
    if profile is None:
        raise ValueError("PDK has no declared DRV scene profile")
    expected = [f"{pvt}_{rc}" for pvt in profile["pvt"]
                for rc in profile["rc_corners"]]
    def host_lib(value: str) -> Path:
        components = Path(value).parts
        marker = components.index(pdk)
        return root.joinpath(*components[marker:])
    netlist = _run_path(project, str(decks[0]["netlist"]))
    routed = project / "phase3/stage3/pnr/routed.def"
    odb = project / "phase3/stage3/pnr/routed.odb"
    artifacts = {"sta_netlist": _ref(netlist), "lvs_netlist": {},
                 "gds_netlist": {}, "odb": _ref(odb) if odb.is_file() else {},
                 "def": _ref(routed)}
    sdc = _run_path(project, str(decks[0]["sdc"]))
    config = root / pdk / "libs.tech/librelane" / library / "config.tcl"
    sources = {"signoff_sdc": _ref(sdc), "pdk_config": _ref(config)}
    for layer in ("L7", "L9"):
        paths = [p for folder in ("input/docs", "phase1/generated_docs")
                 for p in sorted((project / folder).glob(layer + "*")) if p.is_file()]
        if not paths:
            raise ValueError(f"{layer} declaration absent")
        sources[layer.lower()] = _ref(paths[0])
    linked_all = {}
    scenes = []
    scene_libs = {}
    pvt_headers = {}
    for deck in decks:
        linked = []
        lefs = []
        for value in deck["liberties"]:
            path = host_lib(value)
            item = {"name": path.stem, **_ref(path)}
            linked.append(item)
            linked_all[item["name"]] = item
            for lef in sorted((path.parent.parent / "lef").glob("*.lef")):
                lefs.append(_ref(lef))
        header = _liberty_header(Path(linked[0]["path"]).read_text())
        matches = [key for key, spec in profile["pvt"].items()
                   if all(header.get(field) == value for field, value in spec.items())]
        spef = _run_path(project, str(deck["spef"]))
        rc_matches = [rc for rc in profile["rc_corners"]
                      if re.search(rf"(?:^|\.){re.escape(rc)}\.spef$", spef.name)]
        if len(matches) != 1 or len(rc_matches) != 1:
            raise ValueError("direct STA deck PVT/RC scene cannot be identified")
        name = f"{matches[0]}_{rc_matches[0]}"
        if name in scene_libs:
            raise ValueError("direct STA decks repeat one scene")
        scene_libs[name] = [item["name"] for item in linked]
        pvt_headers[name] = header
        scenes.append({"name": name, "mode": "functional", "rc_corner": rc_matches[0],
                       "spef": _ref(spef), "spef_layout_sha256": artifacts["def"]["sha256"],
                       "liberty": linked[0]["name"], "linked_liberties": linked,
                       "linked_lefs": lefs,
                       "positive_control_limits": {"max_fanout": 1,
                                                   "max_slew": 1e-6,
                                                   "max_capacitance": 1e-6}})
    pdk_text = config.read_text()
    from declared_knob_applied_parity import collect_declared
    declared = collect_declared(project, pdk=pdk, library=library)
    fanout = _integrator_value(pdk_text, "MAX_FANOUT_CONSTRAINT", "SYNTH_MAX_FANOUT")
    sdc_text = sdc.read_text()
    period, io_delay = _clock_io_values(sdc_text)
    values = {"fanout": (declared.get("SYNTH_MAX_FANOUT") or (fanout,))[0],
              "slew_ns": _integrator_value(pdk_text, "MAX_TRANSITION_CONSTRAINT", "MAX_SLEW_CONSTRAINT"),
              "cap_pf": _integrator_value(pdk_text, "MAX_CAPACITANCE_CONSTRAINT", "MAX_CAP_CONSTRAINT"),
              "default_fanout_ceiling": fanout,
              "period_ns": period, "io_delay_ns": io_delay}
    frozen = {"sources": {k: v["sha256"] for k, v in sources.items()},
              "liberties": {k: v["sha256"] for k, v in linked_all.items()},
              "values": values, "scope": "whole final netlist", "scenes": expected,
              "scene_profile_sha256": _sha(_SCENE_PROFILES),
              "scene_liberties": scene_libs, "rc_corners": profile["rc_corners"],
              "pvt": pvt_headers}
    current = {"sources": sources, "liberties": list(linked_all.values()),
               "values": values, "scope": frozen["scope"], "scenes": expected,
               "scene_liberties": scene_libs}
    identity = {"project": str(project), **_run_identity(project), "pdk": pdk,
                "library": library, "artifacts": artifacts,
                "source_tool_image": image,
                "sta_netlist": artifacts["sta_netlist"]["sha256"],
                "lvs_netlist": None, "gds_netlist": None}
    stages, stage_receipts = _stages(project, False)
    return {"top": decks[0]["top"], "identity": identity,
            "frozen": frozen, "current": current, "stages": stages,
            "stage_receipts": stage_receipts, "pins": {},
            "scenes": scenes, "postroute_repair_ran": False}


def build(project: Path, *, final_state: dict | None = None) -> dict:
    project = project.resolve()
    if (final_state is None and
            not (project / "reports/phase3/librelane_postroute_repair.json").is_file() and
            not list((project / "phase3/librelane").glob(
                "**/04-openroad-stapostpnr/state_out.json"))):
        return _build_direct(project)
    state, state_path = _state(project, final_state)
    provenance = json.loads((project / "phase3/librelane_pdk_root.provenance.json").read_text())
    pdk = provenance["derivation"]["pdk"]
    image = provenance["derivation"]["image"]
    # A copied run retains the producer host's cache path. Recreate the same
    # image's PDK on this host without changing the run's provenance record.
    from librelane_contract import pdk_root_resolution
    resolution = pdk_root_resolution(None, pdk, image=image)
    if resolution["derivation"]["image_id"] != provenance["derivation"]["image_id"]:
        raise ValueError("final STA PDK image differs from run provenance")
    root = Path(resolution["path"])
    netlist = _run_path(project, state["nl"])
    top = netlist.name.removesuffix(".nl.v")
    state_def = _ref(_run_path(project, state["def"]))
    if final_state is None:
        # After handoff the canonical routed DEF must be the layout the final
        # STA state measured.
        layout = _ref(project / "phase3/stage3/pnr/routed.def")
        if state_def["sha256"] != layout["sha256"]:
            raise ValueError("final STA DEF differs from routed DEF")
    else:
        # Step 32 captures its candidate before handoff writes routed.def.
        # Bind the candidate's own DEF; the pre-stream capture repeats the
        # routed.def comparison once the handoff has happened.
        layout = state_def
    artifacts = {"sta_netlist": _ref(netlist),
                 "lvs_netlist": {}, "gds_netlist": {},
                 "odb": _ref(_run_path(project, state["odb"])),
                 "def": layout}
    scene_root = state_path.parent
    profile = next((v for k, v in json.loads(_SCENE_PROFILES.read_text()).items()
                    if pdk.lower().startswith(k.lower())), None)
    if profile is None:
        raise ValueError("PDK has no declared DRV scene profile")
    expected_order = [f"{pvt}_{rc}" for pvt in profile["pvt"]
                      for rc in profile["rc_corners"]]
    expected = set(expected_order)
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
        # state["lib"] is STAPostPNR's generated top Liberty output. It was
        # not linked as an input by this scene; only the environment's CELL_LIBS
        # and PAD_LIBS were read into STA.
        generated_liberty = _run_path(project, state["lib"][actual_name])
        linked, lefs = _scene_files(env, root, pdk, pvt)
        for item in linked:
            existing = liberties.setdefault(item["name"], item)
            if existing != item:
                raise ValueError("linked Liberty identity collision")
        scene_libs[name] = [item["name"] for item in linked]
        spef = _run_path(project, state["spef"][rc + "_*"])
        scenes.append({"name": name, "mode": "functional", "rc_corner": rc,
                       "spef": _ref(spef), "spef_layout_sha256": artifacts["def"]["sha256"],
                       "generated_top_liberty": _ref(generated_liberty),
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
    from declared_knob_applied_parity import collect_declared
    declared = collect_declared(project, pdk=pdk, library=env["STD_CELL_LIBRARY"])
    integrator_fanout = _integrator_value(pdk_text, "MAX_FANOUT_CONSTRAINT", "SYNTH_MAX_FANOUT")
    period, io_delay = _clock_io_values(sdc_text)
    values = {"fanout": (declared.get("SYNTH_MAX_FANOUT") or (integrator_fanout,))[0],
              "slew_ns": _integrator_value(pdk_text, "MAX_TRANSITION_CONSTRAINT", "MAX_SLEW_CONSTRAINT"),
              "cap_pf": _integrator_value(pdk_text, "MAX_CAPACITANCE_CONSTRAINT", "MAX_CAP_CONSTRAINT"),
              "default_fanout_ceiling": integrator_fanout,
              "period_ns": period, "io_delay_ns": io_delay}
    observed = {}
    for field, command in (("fanout", "set_max_fanout"),
                           ("slew_ns", "set_max_transition"),
                           ("cap_pf", "set_max_capacitance")):
        observed[field] = _sdc_values(sdc_text, command)
    frozen = {"sources": {k: v["sha256"] for k, v in sources.items()},
              "liberties": {k: v["sha256"] for k, v in liberties.items()},
              "values": values, "scope": "whole final netlist",
              "scenes": expected_order, "scene_profile_sha256": _sha(_SCENE_PROFILES),
              "scene_liberties": scene_libs, "rc_corners": profile["rc_corners"],
              "pvt": {name: _liberty_header(Path(next(item["path"] for item in scene["linked_liberties"]
                                                     if item["name"] == scene["liberty"])).read_text())
                      for name in sorted(expected)
                      for scene in scenes if scene["name"] == name}}
    current = {"sources": sources, "liberties": list(liberties.values()),
               "values": values, "scope": frozen["scope"],
               "scenes": frozen["scenes"], "scene_liberties": scene_libs,
               "applied_sdc": observed}
    identity = {"project": str(project), **_run_identity(project),
                "pdk": pdk, "library": env["STD_CELL_LIBRARY"],
                "source_tool_image": image,
                "source_tool_image_id": provenance["derivation"]["image_id"],
                "artifacts": artifacts,
                **{key: artifacts[key].get("sha256") for key in
                   ("sta_netlist", "lvs_netlist", "gds_netlist")}}
    # The judge requires post-route repair constraints exactly when step 32
    # adopted a candidate; use the same adoption record.
    if final_state is not None:
        postroute_repair_ran = bool(final_state.get("adopted"))
    else:
        repair = project / "reports/phase3/librelane_postroute_repair.json"
        postroute_repair_ran = bool(repair.is_file() and
                                    json.loads(repair.read_text()).get("adopted"))
    stages, stage_receipts = _stages(project, postroute_repair_ran)
    return {"top": top, "identity": identity, "frozen": frozen,
            "current": current, "stages": stages,
            "stage_receipts": stage_receipts, "pins": {}, "scenes": scenes,
            "postroute_repair_ran": postroute_repair_ran}


#: What step 31 records about the layout and netlist it compared, and what
#: the GDS admission records about the stream (both written by the flow).
LVS_INPUTS = Path("reports/phase3/lvs_inputs.json")
LVS_VERDICT = Path("reports/phase3/lvs_verdict.json")
GDS_ADMISSION = Path("reports/phase3/gds_admission.json")


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def post_stream_identity(project: Path, plan: dict) -> dict:
    """R-0929-DRV-IDENTITY: the FINAL capture binds the streamed GDS and the
    LVS netlist and records how they derive from the judged DEF / netlist.

    * GDS: `gds_admission.json` names the admitted GDS, its sha256 and the
      routed DEF it was streamed from (`basis_inputs`).
    * LVS: `lvs_inputs.json` names the DEF step 31 extracted the layout from
      and the gate netlist it compared against; `lvs_verdict.json` says
      whether the compare matched.
    The judge re-hashes every file and checks each link; this only gathers
    the records.  A missing record leaves its identity absent (NOT_MEASURED).
    """
    identity = dict(plan["identity"])
    artifacts = dict(identity.get("artifacts") or {})
    identity.pop("bound_at", None)
    identity["capture_point"] = "post_stream"
    derivation: dict = {}
    admission = _read_json(project / GDS_ADMISSION)
    gds_rel = admission.get("gds_relpath")
    if gds_rel and (project / gds_rel).is_file():
        basis = admission.get("basis_inputs") or {}
        derivation["gds"] = {
            "path": str((project / gds_rel).resolve()),
            "sha256": admission.get("gds_sha256"),
            "streamed_from_def_sha256": basis.get("phase3/stage3/pnr/routed.def"),
            "record": _ref(project / GDS_ADMISSION)}
    lvs = _read_json(project / LVS_INPUTS)
    verdict = _read_json(project / LVS_VERDICT)
    schematic = (lvs.get("schematic_netlist") or {}).get("path")
    if schematic and Path(schematic).is_file() and (project / LVS_VERDICT).is_file():
        derivation["lvs"] = {
            "layout_def_sha256": (lvs.get("layout_def") or {}).get("sha256"),
            "schematic_netlist": _ref(Path(schematic)),
            "verdict": verdict.get("status"),
            "compare_performed": verdict.get("compare_performed"),
            "records": [_ref(project / LVS_INPUTS), _ref(project / LVS_VERDICT)]}
        artifacts["lvs_netlist"] = derivation["lvs"]["schematic_netlist"]
        identity["lvs_netlist"] = artifacts["lvs_netlist"]["sha256"]
    if "gds" in derivation:
        # The netlist a streamed layout carries is the netlist of the DEF it
        # was streamed from; the judge accepts it only when that DEF is the
        # judged DEF (the plan binds DEF and STA netlist from one state).
        artifacts["gds_netlist"] = dict(artifacts["sta_netlist"])
        identity["gds_netlist"] = artifacts["gds_netlist"]["sha256"]
    identity["artifacts"] = artifacts
    identity["derivation"] = derivation
    return {**plan, "identity": identity}


def publish(project: Path, *, final_state: dict | None = None,
            capture_point: str = "in_flow") -> Path:
    output = project / "reports/phase3/sta/drv_capture_plan.json"
    plan = build(project, final_state=final_state)
    if capture_point == "post_stream":
        plan = post_stream_identity(project, plan)
    write_text(output, json.dumps(plan, indent=2) + "\n")
    return output


def capture_and_publish(project: Path, *, final_state: dict | None = None,
                        capture_point: str = "in_flow") -> Path:
    """Measure the current routed state and publish the judge's only input.

    `capture_point="post_stream"` is the FINAL capture (after stream-out and
    LVS); only its verdict counts toward IC PASS (R-0929-DRV-IDENTITY)."""
    import drv_signoff_capture
    output = project / "reports/phase3/sta/drv_signoff_bundle.json"
    output.unlink(missing_ok=True)
    plan_path = publish(project, final_state=final_state, capture_point=capture_point)
    plan = json.loads(plan_path.read_text())
    bundle = drv_signoff_capture.capture(
        plan, project / "reports/phase3/sta/drv_capture",
        image=plan["identity"]["source_tool_image"])
    write_text(output, json.dumps(bundle, indent=2) + "\n")
    return output
