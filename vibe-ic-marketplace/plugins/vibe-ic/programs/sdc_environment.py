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


def _sdc_environment_prefix(values: Dict[str, Tuple[str, str]],
                            unread: Sequence[str]) -> str:
    """Emit the four non-DRV R8 lines, each immediately preceded by its source."""
    lines = [f"# {item}" for item in unread]
    for name in ("set_clock_uncertainty", "set_clock_transition",
                 "set_driving_cell", "set_load"):
        if name not in values:
            continue
        raw, source = values[name]
        if name.startswith("set_clock_"):
            command = f"{name} {raw} [all_clocks]"
        elif name == "set_driving_cell":
            cell, pin = raw.split("/", 1)
            command = f"set_driving_cell -lib_cell {cell} -pin {pin} [all_inputs]"
        else:
            # LibreLane OUTPUT_CAP_LOAD is fF; OpenSTA set_load takes pF.
            command = f"set_load {float(raw) / 1000:g} [all_outputs]"
        lines.extend((f"# R8 source: {source}", command))
    return "\n".join(lines) + ("\n" if lines else "")
