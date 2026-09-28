"""Reconcile OpenSTA's unannotated names with Liberty, LEF and route evidence."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drv_signoff_judge import _liberty_limits


def _lef_uses(paths: list[dict]) -> dict[tuple[str, str], str]:
    uses: dict[tuple[str, str], str] = {}
    for item in paths:
        macro = pin = None
        for raw in Path(item["path"]).read_text().splitlines():
            line = raw.strip()
            match = re.fullmatch(r"MACRO\s+(\S+)", line, re.I)
            if match:
                macro, pin = match.group(1), None
                continue
            match = re.fullmatch(r"PIN\s+(\S+)", line, re.I)
            if match and macro:
                pin = match.group(1)
                continue
            match = re.fullmatch(r"USE\s+(POWER|GROUND|SIGNAL|CLOCK|ANALOG|SCAN)\s*;", line, re.I)
            if match and macro and pin:
                key = (macro, pin)
                use = match.group(1).upper()
                if key in uses and uses[key] != use:
                    raise ValueError(f"LEF USE conflicts for {macro}/{pin}")
                uses[key] = use
                continue
            match = re.fullmatch(r"END\s+(\S+)", line, re.I)
            if match:
                if pin and match.group(1) == pin:
                    pin = None
                elif macro and match.group(1) == macro:
                    macro, pin = None, None
    if not uses:
        raise ValueError("linked LEF pin USE census empty")
    return uses


def _unannotated(body: str) -> list[str]:
    found = []
    pattern = re.compile(r"(?m)^Found\s+(\d+)\s+((?:partially\s+)?unannotated)\s+drivers\.\s*$")
    matches = list(pattern.finditer(body))
    if not matches:
        raise ValueError("OpenSTA unannotated driver grammar absent")
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        names = [line.strip() for line in body[match.end():end].splitlines()
                 if line.strip() and not line.startswith("=")]
        if len(names) != int(match.group(1)):
            raise ValueError("OpenSTA unannotated names differ from count")
        found.extend(names)
    if len(set(found)) != len(found):
        raise ValueError("OpenSTA unannotated driver repeated")
    return sorted(found)


def _spef_nets(path: Path) -> set[str]:
    names = {}
    result = set()
    in_map = False
    for line in path.read_text(errors="replace").splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "*NAME_MAP":
            in_map = True
        elif in_map and words[0].startswith("*") and words[0][1:].isdigit() and len(words) >= 2:
            names[words[0]] = words[1]
        elif words[0] == "*D_NET" and len(words) >= 3:
            in_map = False
            result.add(names.get(words[1], words[1]))
    return result


def _def_segments(path: Path) -> dict[str, int]:
    body = path.read_text(errors="replace")
    match = re.search(r"(?ms)^NETS\s+\d+\s*;\s*(.*?)^END NETS\b", body)
    if not match:
        raise ValueError("routed DEF NETS section absent")
    result = {}
    for item in re.finditer(r"(?ms)^\s*-\s+(\S+)\s+(.*?);", match.group(1)):
        name, record = item.groups()
        if name in result:
            raise ValueError("routed DEF net repeated")
        result[name] = len(re.findall(r"\+\s*(?:ROUTED|NEW|FIXED)\b", record))
    return result


def derive(scene_dir: Path, pins: dict, liberties: list[dict], lefs: list[dict],
           routed_def: Path, spef: Path) -> dict:
    """Only LEF PG pins without any linked timing arc leave the population."""
    uses = _lef_uses(lefs)
    lib_pins = {}
    for item in liberties:
        cells = _liberty_limits(Path(item["path"]).read_text())["cells"]
        for cell, cell_pins in cells.items():
            for pin, props in cell_pins.items():
                key = (cell, pin)
                if key in lib_pins and lib_pins[key] != props:
                    raise ValueError(f"linked Liberty pin conflicts for {cell}/{pin}")
                lib_pins[key] = props
    pg = []
    unconnected = []
    for name, pin in sorted(pins.items()):
        if not pin["net"]:
            unconnected.append(name)
        key = (pin["cell"], pin["cell_pin"])
        if uses.get(key) in ("POWER", "GROUND") and key in lib_pins and not lib_pins[key]["has_timing_arc"]:
            pg.append({"pin": name, "lef_use": uses[key],
                       "liberty_timing_arc": False, "net": pin["net"]})
    names = _unannotated((scene_dir / "annotation.rpt").read_text())
    pg_names = {item["pin"] for item in pg}
    by_net = {}
    for name, pin in pins.items():
        if pin["net"]:
            by_net.setdefault(pin["net"], []).append(name)
    spef_nets = _spef_nets(spef)
    segments = _def_segments(routed_def)
    resolved = []
    unresolved = []
    for name in names:
        pin = pins.get(name)
        if pin is None:
            unresolved.append({"pin": name, "reason": "not_in_pin_census"})
        elif name in pg_names:
            resolved.append({"pin": name, "reason": "lef_pg_no_liberty_arc"})
        elif not pin["net"]:
            resolved.append({"pin": name, "reason": "unconnected_no_net"})
        else:
            net = pin["net"]
            members = by_net.get(net, [])
            port_pad = (any(pins[m]["kind"] == "port" for m in members) and
                        any(pins[m]["cell_class"] == "IO" for m in members))
            if port_pad and net in spef_nets:
                resolved.append({"pin": name, "net": net,
                                 "reason": "port_pad_spef"})
            elif port_pad and segments.get(net) == 0:
                resolved.append({"pin": name, "net": net,
                                 "reason": "port_pad_zero_routed_segments"})
            else:
                unresolved.append({"pin": name, "net": net,
                                   "reason": "missing_parasitic_or_geometry_proof"})
    return {"unannotated_names": names, "pg_exclusions": pg,
            "unconnected_pins": unconnected, "resolved": resolved,
            "unresolved": unresolved}
