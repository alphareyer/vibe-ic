#!/usr/bin/env python3
"""R8 SDC environment derivation from design, pinned PDK, and Liberty."""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _container_exec as _cex
import _path_layout as _pl
_SDC_ENV_KEYS = {
    "set_clock_uncertainty": "CLOCK_UNCERTAINTY_CONSTRAINT",
    "set_clock_transition": "CLOCK_TRANSITION_CONSTRAINT",
    "set_driving_cell": "SYNTH_DRIVING_CELL",
    "set_load": "OUTPUT_CAP_LOAD",
    "set_max_transition": "MAX_TRANSITION_CONSTRAINT",
    "set_max_capacitance": "MAX_CAPACITANCE_CONSTRAINT",
}
_SDC_ENV_NUMERIC = frozenset(_SDC_ENV_KEYS) - {"set_driving_cell"}


def _sdc_environment_design_values(project: Path) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
    """Read strict L9 key/value rows; prose and unrelated tables are ignored."""
    values: Dict[str, Tuple[str, str]] = {}
    unread: List[str] = []
    aliases = {name: name for name in _SDC_ENV_KEYS}
    aliases.update({key: name for name, key in _SDC_ENV_KEYS.items()})
    for root in (project / "input" / "docs", _pl.generated_docs_dir(project)):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("L9*")):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                unread.append(f"NOT_READ: design doc {path}")
                continue
            for lineno, line in enumerate(lines, 1):
                match = re.match(r"\s*\|\s*`?([A-Za-z_]+)`?\s*\|\s*\*{0,2}\s*([^|]+?)\s*\*{0,2}\s*\|", line)
                if not match:
                    continue
                name = aliases.get(match.group(1))
                if name is None or name in values:
                    continue
                raw = match.group(2).strip().strip("*` ")
                if name in _SDC_ENV_NUMERIC:
                    try:
                        number = float(raw)
                    except ValueError:
                        continue
                    if not math.isfinite(number) or number <= 0:
                        continue
                    # A literal SDC `set_load` is pF; the flow key
                    # OUTPUT_CAP_LOAD is fF. Store both internally as fF.
                    if name == "set_load" and match.group(1) == "set_load":
                        number *= 1000.0
                    raw = f"{number:g}"
                elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*/[A-Za-z_][A-Za-z0-9_]*", raw):
                    continue
                values[name] = (raw, f"design doc {path}:{lineno}")
    return values, unread


def _sdc_environment_pdk_values(liberty_path: str, container: str,
                                to_container_path: Optional[Callable[[str, str], str]] = None
                                ) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
    """Read the active PDK and cell-library LibreLane configs in the pinned image."""
    values: Dict[str, Tuple[str, str]] = {}
    unread: List[str] = []
    path = str(liberty_path or "")
    match = re.search(r"^(.*)/libs\.ref/([^/]+)/lib/", path)
    if not match or not container:
        return values, ["NOT_READ: pinned PDK SDC config path or container identity"]
    pdk_root, library = match.groups()
    root = f"{pdk_root}/libs.tech/librelane"
    for config in (f"{root}/config.tcl", f"{root}/{library}/config.tcl"):
        config_c = (to_container_path(config, container)
                    if to_container_path is not None else config)
        try:
            run = subprocess.run(_cex.docker_exec_argv(container, "cat", config_c),
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired,
                _cex.ContainerImageMismatch):
            unread.append(f"NOT_READ: pinned PDK config {config_c}")
            continue
        if run.returncode != 0:
            unread.append(f"NOT_READ: pinned PDK config {config_c}")
            continue
        for line in run.stdout.splitlines():
            found = re.match(r"\s*set\s+::env\(([A-Z_]+)\)\s+(\"[^\"]*\"|\S+)", line)
            if not found:
                continue
            key, raw = found.groups()
            name = next((name for name, var in _SDC_ENV_KEYS.items() if var == key), None)
            if name is None:
                continue
            raw = raw.strip('"').replace("$::env(STD_CELL_LIBRARY)", library)
            if name in _SDC_ENV_NUMERIC:
                try:
                    number = float(raw)
                except ValueError:
                    continue
                if not math.isfinite(number) or number <= 0:
                    continue
                raw = f"{number:g}"
            elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*/[A-Za-z_][A-Za-z0-9_]*", raw):
                continue
            values[name] = (raw, f"pinned PDK default {config_c}:{key}")
    return values, unread


def _sdc_environment_values(project: Path, liberty_path: str, container: str,
                            liberty_slew: Optional[float],
                            liberty_cap: Optional[float],
                            to_container_path: Optional[Callable[[str, str], str]] = None
                            ) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
    """Resolve each R8 command: design > pinned PDK > Liberty default."""
    design, unread_design = _sdc_environment_design_values(project)
    pdk, unread_pdk = _sdc_environment_pdk_values(
        liberty_path, container, to_container_path)
    liberty: Dict[str, Tuple[str, str]] = {}
    if liberty_slew is not None:
        liberty["set_max_transition"] = (f"{liberty_slew:g}",
                                         f"liberty default {liberty_path}:default_max_transition")
    if liberty_cap is not None:
        liberty["set_max_capacitance"] = (f"{liberty_cap:g}",
                                          f"liberty default {liberty_path}:default_max_capacitance")
    return {name: next((tier[name] for tier in (design, pdk, liberty) if name in tier))
            for name in _SDC_ENV_KEYS if any(name in tier for tier in (design, pdk, liberty))}, \
        unread_design + unread_pdk


#: R-0929-PAD-INPUT-DRIVE. The record of how a DIE top's bond-pad inputs are
#: driven, read by the Step-23 STA verdict (a NOT_MEASURED drive cannot PASS).
PAD_INPUT_DRIVE_REPORT = "reports/phase3/pad_input_drive.json"
PAD_INPUT_DRIVE_SCHEMA = "vibeic.pad_input_drive.v1"
_DESIGN_TIER = "design doc "


def _design_input_transition(project: Path) -> Optional[Tuple[str, str]]:
    """A design-declared off-chip input transition (ns): a strict L9 key/value
    row `set_input_transition | <ns>`, the same grammar as every R8 key."""
    for root in (project / "input" / "docs", _pl.generated_docs_dir(project)):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("L9*")):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                m = re.match(r"\s*\|\s*`?set_input_transition`?\s*\|"
                             r"\s*\*{0,2}\s*([^|]+?)\s*\*{0,2}\s*\|", line)
                if not m:
                    continue
                try:
                    number = float(m.group(1).strip().strip("*` "))
                except ValueError:
                    continue
                if math.isfinite(number) and number > 0:
                    return f"{number:g}", f"{_DESIGN_TIER}{path}:{lineno}"
    return None


def _pdk_io_input_transition(project: Path) -> Optional[Tuple[str, str]]:
    """The PDK IO-tier off-chip input transition, with its source.

    No PDK this plugin supports documents one today: the IO Liberty carries
    only table axes (`input_transition_time` index values, which are a
    characterisation range, not a board driver) and the pinned LibreLane
    config's only driving cells are the CORE library's synthesis cells. A PDK
    that documents an IO-tier value is read here, with its file and key."""
    return None


def _pad_input_drive(project: Path, values: Dict[str, Tuple[str, str]]
                     ) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, object]]:
    """R-0929-PAD-INPUT-DRIVE: resolve the off-chip drive of a DIE's inputs.

    On a pad-ring (DIE) top every input is a bond-pad port. The core library's
    synthesis driving cell (a tiny inverter from the pinned PDK's LibreLane
    config) "driving" a pad's input capacitance is not a model of anything:
    measured on a routed gf180 die it put 50.8 ns on the clock port and turned
    9/9 positive scenes into -55.9 ns. The ladder is the design's declared
    driver or input transition, then a PDK IO-tier value; with neither the
    drive is NOT_MEASURED, and the STA verdict consumer must not read the
    ideal edge the deck is then left with as a sign-off PASS. HARDMACRO / core
    runs keep the synthesis driving cell (their inputs ARE driven on chip).
    """
    try:
        import _tapeout_declaration as _td
        die = bool(_td.requests_pad_ring(project))
    except Exception as exc:  # noqa: BLE001 — an unreadable route is not a core route
        return values, {"schema": PAD_INPUT_DRIVE_SCHEMA, "applies": None,
                        "verdict": "NOT_MEASURED",
                        "reason": f"route undeterminable: {exc!r}"}
    if not die:
        return values, {"schema": PAD_INPUT_DRIVE_SCHEMA, "applies": False,
                        "verdict": "NOT_APPLICABLE",
                        "reason": "not a pad-ring (DIE) top; the synthesis "
                                  "driving cell models on-chip drive"}
    out = dict(values)
    cell = out.get("set_driving_cell")
    record: Dict[str, object] = {"schema": PAD_INPUT_DRIVE_SCHEMA,
                                 "applies": True}
    if cell and cell[1].startswith(_DESIGN_TIER):
        record.update(verdict="DECLARED", model="set_driving_cell",
                      value=cell[0], source=cell[1])
        return out, record
    if cell:
        record["refused_core_driving_cell"] = {"value": cell[0],
                                               "source": cell[1]}
        out.pop("set_driving_cell")
    for tier, found in (("DECLARED", _design_input_transition(project)),
                        ("PDK_IO_TIER", _pdk_io_input_transition(project))):
        if found:
            out["set_input_transition"] = found
            record.update(verdict=tier, model="set_input_transition",
                          value=found[0], source=found[1])
            return out, record
    record.update(verdict="NOT_MEASURED", reason=(
        "no design-declared off-chip input driver/transition and no PDK "
        "IO-tier value; the core synthesis driving cell never drives a bond "
        "pad (R-0929-PAD-INPUT-DRIVE), so input-launched and clock-latency "
        "timing is NOT_MEASURED"))
    return out, record


def write_pad_input_drive_record(project: Path, record: Dict[str, object]) -> Path:
    import _atomic_artefact as _aa
    import json
    return _aa.write_json(Path(project) / PAD_INPUT_DRIVE_REPORT, record)


def pad_input_drive_not_measured(project: Path) -> Optional[str]:
    """The reason a sign-off STA verdict cannot PASS, or None."""
    import json
    try:
        doc = json.loads((Path(project) / PAD_INPUT_DRIVE_REPORT).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and doc.get("verdict") == "NOT_MEASURED":
        return str(doc.get("reason") or "pad input drive NOT_MEASURED")
    return None


def _sdc_environment_prefix(values: Dict[str, Tuple[str, str]],
                            unread: Sequence[str], time_scale: float = 1.0,
                            pad_drive: Optional[Dict[str, object]] = None) -> str:
    """Emit sourced commands and name every command that could not be emitted.

    Time-valued declarations are in ns; OpenSTA reads the active Liberty unit.
    ``NOT_READ`` names an inaccessible tier; ``NOT_MEASURED`` names each
    command it leaves unresolved. Readable tiers with no declaration use
    ``UNDECLARED`` instead.
    """
    lines = [f"# {item}" for item in unread]
    pad = pad_drive if isinstance(pad_drive, dict) and pad_drive.get("applies") else None
    if pad is not None:
        refused = pad.get("refused_core_driving_cell")
        if isinstance(refused, dict):
            lines.append(f"# R-0929-PAD-INPUT-DRIVE: core synthesis driving cell "
                         f"{refused.get('value')} ({refused.get('source')}) NOT "
                         f"applied: every input of this DIE top is a bond pad")
        if pad.get("verdict") == "NOT_MEASURED":
            lines.append(f"# NOT_MEASURED: OFFCHIP_INPUT_DRIVE; {pad.get('reason')}")
    for name in _SDC_ENV_KEYS:
        if pad is not None and name == "set_driving_cell":
            continue
        if name not in values:
            status = "NOT_MEASURED" if unread else "UNDECLARED"
            reason = ("at least one source tier NOT_READ" if unread else
                      "no value in design, pinned PDK, or Liberty")
            lines.append(f"# {status}: {_SDC_ENV_KEYS[name]}; {reason}")
    for name in ("set_clock_uncertainty", "set_clock_transition",
                 "set_driving_cell", "set_input_transition", "set_load"):
        if name not in values:
            continue
        raw, source = values[name]
        if name.startswith("set_clock_"):
            command = f"{name} {float(raw) * time_scale:g} [all_clocks]"
        elif name == "set_driving_cell":
            cell, pin = raw.split("/", 1)
            command = f"set_driving_cell -lib_cell {cell} -pin {pin} [all_inputs]"
        elif name == "set_input_transition":
            command = f"set_input_transition {float(raw) * time_scale:g} [all_inputs]"
        else:
            # LibreLane OUTPUT_CAP_LOAD is fF; OpenSTA set_load takes pF.
            command = f"set_load {float(raw) / 1000:g} [all_outputs]"
        lines.extend((f"# R8 source: {source}", command))
    return "\n".join(lines) + ("\n" if lines else "")
