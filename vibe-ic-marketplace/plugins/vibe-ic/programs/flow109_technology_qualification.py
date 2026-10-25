#!/usr/bin/env python3
"""D03 native technology selection. BLOCKING on native config/pin refusal.

LibreLane resolves the design's configuration and validates its selected files;
ciel identifies the installed PDK revision. This is technology-use qualification,
not execution/signoff of every downstream tool, corner or physical deck.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
from typing import Mapping

STEP = "D03"
NATIVE_STEP = "flow109.D03"
SCHEMA = "vibeic.flow109.technology-qualification.v1"


class D03Refusal(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _record(path: Path) -> dict:
    return {"path": str(path.resolve(strict=True)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _resolve(config_path: Path, pdk_root: Path):
    import ciel
    from librelane.flows import Flow

    # Constructing a native Flow resolves/validates configuration only. No
    # physical or synthesis step is launched, and no existing run is resumed.
    meta = json.loads(config_path.read_text()).get("meta", {})
    name = meta.get("flow", "Classic")
    if not isinstance(name, str) or Flow.factory.get(name) is None:
        raise D03Refusal("D03_NATIVE_FLOW_UNRESOLVED", str(name))
    target = Flow.factory.get(name)
    if meta.get("substituting_steps"):
        target = target.Substitute(meta["substituting_steps"])
    flow = target(str(config_path), pdk_root=str(pdk_root))
    selected = flow.config
    pdk = selected["PDK"]
    family = next((f for f in ciel.Family.by_name.values() if pdk in f.variants), None)
    if family is None:
        raise D03Refusal("D03_CIEL_FAMILY_UNRESOLVED", pdk)
    installed = ciel.Version.get_all_installed(str(pdk_root.parents[3]), family.name) \
        if pdk_root.parent.name == "versions" else []
    pins = [v for v in installed if Path(v.get_dir(str(pdk_root.parents[3]))).resolve() == pdk_root]
    if len(pins) != 1 or not (pdk_root / pdk).is_dir():
        raise D03Refusal("D03_CIEL_PIN_UNRESOLVED", str(pdk_root))
    return selected, {"family": family.name, "variant": pdk,
                      "revision": pins[0].name, "root": str(pdk_root)}


def execute(project: Path, context: Mapping) -> dict:
    project = Path(project).resolve(strict=True)
    checks, inputs, metrics = [], [], {}
    native_started = False
    status, reason = "NOT_MEASURED", "D03_INPUT_UNRESOLVED"
    try:
        row = context.get("resolved_row") or {}
        if (context.get("requested_step") != STEP or row.get("id") != STEP
                or not context.get("context_sha256")):
            raise D03Refusal("D03_CONTEXT_INVALID", "current D03 context required")
        image = os.environ.get("VIBEIC_EDA_IMAGE", "")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
            raise D03Refusal("D03_RUNTIME_IMAGE_UNRESOLVED", "resolve image on launch host")
        config_path = project / "phase3/librelane/config.json"
        inputs.append(_record(config_path))
        raw_root = os.environ.get("VIBEIC_LIBRELANE_PDK_ROOT", "")
        if not raw_root:
            raise D03Refusal("D03_PDK_ROOT_UNSTATED", "VIBEIC_LIBRELANE_PDK_ROOT")
        root = Path(raw_root).resolve(strict=True)
        native_started = True
        selected, pin = _resolve(config_path, root)
        resolved_path = project / "reports/flow109/D03/resolved.json"
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(selected.dumps())
        inputs.append(_record(resolved_path))
        # Native Path values, including input RTL/SDC, Liberty, LEF and decks,
        # are validated by LibreLane, then hashed without a second file policy.
        from librelane.common import Path as NativePath
        def files(value):
            if isinstance(value, NativePath):
                p = Path(str(value))
                if p.is_file():
                    yield p
            elif isinstance(value, Mapping):
                for v in value.values():
                    yield from files(v)
            elif isinstance(value, (tuple, list)):
                for v in value:
                    yield from files(v)
        inputs.extend(_record(p) for p in sorted(set(files(selected))))
        tools = {name: version(name) for name in ("librelane", "ciel")}
        inputs.append(_record(Path(__file__)))
        metrics = {"required_capability_qualifications": {
            "librelane_config_resolution": "PASS", "ciel_installed_pin": "PASS"},
            "tool_deck_corner_binding": {"image": image, "tools": tools,
                "pdk": pin, "standard_cell_library": selected["STD_CELL_LIBRARY"],
                "default_corner": selected["DEFAULT_CORNER"],
                "resolved_config": inputs[1]}}
        checks = [{"check": key, "status": value} for key, value in
                  metrics["required_capability_qualifications"].items()]
        status, reason = "PASS", "D03_NATIVE_CONFIG_AND_CIEL_PIN_PASS"
    except D03Refusal as exc:
        reason = exc.code
        checks.append({"check": "native_qualification", "status": status, "detail": exc.detail})
    except Exception as exc:
        status = "FAIL" if native_started else "NOT_MEASURED"
        reason = "D03_NATIVE_RESOLUTION_FAILED" if native_started else "D03_INPUT_UNRESOLVED"
        checks.append({"check": "native_qualification", "status": status,
                       "detail": str(exc)})
    return {"schema": SCHEMA, "step_id": STEP,
        "program": "flow109_technology_qualification",
        "context_sha256": context.get("context_sha256"),
        "component_status": status, "default_row_status": status,
        "completed": status == "PASS", "reason": reason,
        "checks": checks, "inputs": inputs, "metrics": metrics,
        "native_started": native_started, "metric_acceptance_status": "IMPLEMENTED",
        "flow109_acceptance": status, "whole_flow_acceptance": "NOT_MEASURED",
        "delivery_accepted": False,
        "scope": "native LibreLane configuration resolution and ciel installed PDK pin",
        "unsupported_scope": "downstream synthesis, simulation, timing and physical signoff"}
