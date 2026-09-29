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


#: The pad-ring producer's record; it names every IO Liberty it linked.
IO_PAD_RECORD = "reports/phase3/io_pad_chip_top.json"
_LIB_TIME_UNIT_NS = {"1ps": 1e-3, "10ps": 1e-2, "100ps": 1e-1, "1ns": 1.0,
                     "10ns": 10.0, "100ns": 100.0, "1us": 1e3}


def _liberty_blocks(text: str, kind: str) -> List[Tuple[str, str]]:
    """Every ``kind ("name") { ... }`` group directly inside ``text``, with its
    body, found by brace matching (Liberty groups nest; a regex cannot)."""
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r"\b%s\s*\(\s*\"?([^\"()]*?)\"?\s*\)\s*\{" % kind, text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append((m.group(1).strip(), text[m.end():i - 1]))
    return out


def _own_attrs(body: str) -> str:
    """A group's own simple attributes: its body with every nested group cut."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _io_pad_pin_transitions(text: str) -> Tuple[List[Tuple[str, float]], int]:
    """(cell/pin, max_transition in ns) for every bond-pad pin that receives
    an off-chip signal (``is_pad : true``, direction input or inout), and the
    number of such pins that document no bound at all (pin nor library)."""
    unit = re.search(r"\btime_unit\s*:\s*\"?\s*([0-9]+\s*[pnu]s)\"?", text)
    scale = _LIB_TIME_UNIT_NS.get((unit.group(1) if unit else "1ns").replace(" ", ""))
    if scale is None:
        return [], 0
    lib_default = re.search(r"\bdefault_max_transition\s*:\s*([0-9.eE+-]+)",
                            text)
    found: List[Tuple[str, float]] = []
    undocumented = 0
    for cell, cell_body in _liberty_blocks(text, "cell"):
        for pin, pin_body in _liberty_blocks(cell_body, "pin"):
            own = _own_attrs(pin_body)
            if not re.search(r"\bis_pad\s*:\s*true\b", own):
                continue
            direction = re.search(r"\bdirection\s*:\s*\"?(\w+)", own)
            if not direction or direction.group(1) not in ("input", "inout"):
                continue
            bound = (re.search(r"\bmax_transition\s*:\s*([0-9.eE+-]+)", own)
                     or lib_default)
            try:
                value = float(bound.group(1)) * scale if bound else None
            except ValueError:
                value = None
            if value is None or not math.isfinite(value) or value <= 0:
                undocumented += 1
                continue
            found.append((f"{cell}/{pin}", value))
    return found, undocumented


def _read_pdk_file(path: str, container: str,
                   to_container_path: Optional[Callable[[str, str], str]]
                   ) -> Optional[str]:
    """A pinned-PDK file's text: from the container when one is named (the
    record's paths are container paths), else from this host."""
    if container:
        path_c = (to_container_path(path, container)
                  if to_container_path is not None else path)
        try:
            run = subprocess.run(_cex.docker_exec_argv(container, "cat", path_c),
                                 capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired, _cex.ContainerImageMismatch):
            return None
        return run.stdout if run.returncode == 0 else None
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def _pdk_io_input_transition(project: Path, container: str = "",
                             to_container_path: Optional[Callable[[str, str], str]] = None
                             ) -> Tuple[Optional[Tuple[str, str]], Dict[str, object]]:
    """The PDK IO-tier off-chip input transition (ns), with its source.

    The IO library documents, on every bond-pad pin that receives an off-chip
    signal (``is_pad : true``, direction input/inout), the ``max_transition``
    it is characterised to accept (its PAD->core delay tables end there). That
    is the slowest edge the pad may legally see, so applying it to the pads is
    the conservative PDK IO-tier model: never the ideal edge, never an
    extrapolation past the IO tables. The IO libraries are the ones the
    pad-ring producer linked (its record, ``io_library_liberty``); the value is
    the maximum over every such pin of every linked view, bound by sha256.
    Returns (None, why) when no linked IO view documents one.
    """
    import hashlib
    import json
    basis: Dict[str, object] = {"record": IO_PAD_RECORD}
    try:
        libs = json.loads((Path(project) / IO_PAD_RECORD).read_text()
                          ).get("io_library_liberty")
    except (OSError, ValueError, AttributeError):
        libs = None
    if not isinstance(libs, list) or not any(isinstance(x, str) for x in libs):
        basis["why"] = (f"no IO Liberty recorded by the pad-ring producer "
                        f"({IO_PAD_RECORD} absent or without io_library_liberty)")
        return None, basis
    views: List[Dict[str, object]] = []
    unread: List[str] = []
    best: Optional[Tuple[float, str, str]] = None
    for lib in sorted(str(x) for x in libs if isinstance(x, str)):
        text = _read_pdk_file(lib, container, to_container_path)
        if text is None:
            unread.append(lib)
            continue
        pins, undocumented = _io_pad_pin_transitions(text)
        views.append({"liberty": lib,
                      "sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "pad_input_pins": len(pins),
                      "pad_input_pins_without_bound": undocumented,
                      "max_transition_ns": max((v for _, v in pins), default=None)})
        for pin, value in pins:
            if best is None or value > best[0]:
                best = (value, lib, pin)
    basis.update(views=views, unread=unread)
    if unread:
        # A view that could not be read may document a slower edge; a partial
        # maximum is not the conservative bound.
        basis["why"] = f"IO Liberty NOT_READ: {', '.join(unread)}"
        return None, basis
    if best is None:
        basis["why"] = ("no linked IO Liberty documents a max_transition on a "
                        "bond-pad input pin")
        return None, basis
    value, lib, pin = best
    sha = next(v["sha256"] for v in views if v["liberty"] == lib)
    return (f"{value:g}", f"PDK IO tier {lib}:{pin}:max_transition "
                          f"(sha256 {sha})"), basis


def _pad_input_drive(project: Path, values: Dict[str, Tuple[str, str]],
                     container: str = "",
                     to_container_path: Optional[Callable[[str, str], str]] = None
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
    found = _design_input_transition(project)
    if found:
        out["set_input_transition"] = found
        record.update(verdict="DECLARED", model="set_input_transition",
                      value=found[0], source=found[1])
        return out, record
    found, io_basis = _pdk_io_input_transition(project, container,
                                               to_container_path)
    record["pdk_io_tier"] = io_basis
    if found:
        out["set_input_transition"] = found
        record.update(verdict="PDK_IO_TIER", model="set_input_transition",
                      value=found[0], source=found[1],
                      basis=("the slowest edge the linked IO library is "
                             "characterised to accept on a bond-pad input "
                             "pin (conservative bound, not a board "
                             "measurement)"))
        return out, record
    record.update(verdict="NOT_MEASURED", reason=(
        "no design-declared off-chip input driver/transition and no PDK "
        f"IO-tier value ({io_basis.get('why')}); the core synthesis driving "
        "cell never drives a bond pad (R-0929-PAD-INPUT-DRIVE), so "
        "input-launched and clock-latency timing is NOT_MEASURED"))
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
