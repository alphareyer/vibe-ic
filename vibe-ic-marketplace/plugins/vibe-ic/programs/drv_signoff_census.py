"""Derive DRV populations and pin classes from fresh OpenSTA census files."""
from __future__ import annotations

import re
from pathlib import Path

from drv_signoff_judge import KINDS, _liberty_limits


def _pins(path: Path) -> dict[str, dict]:
    pins = {}
    for line in path.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) != 14 or not fields[0] or fields[0] in pins:
            raise ValueError("OpenSTA pin census malformed or duplicated")
        (name, kind, direction, driver, instance, cell, cell_pin, net,
         origin, rise, fall, clock, logic, ideal) = fields
        if (kind not in ("pin", "port") or driver not in ("0", "1") or
                clock not in ("0", "1") or logic not in ("0", "1", "X") or
                ideal not in ("0", "1")):
            raise ValueError("OpenSTA pin census type invalid")
        pins[name] = {"kind": kind, "direction": direction,
                      "driver": driver == "1", "instance": instance,
                      "cell": cell, "cell_pin": cell_pin, "net": net,
                      "origin": origin, "clock": clock == "1",
                      "logic": logic, "ideal": ideal == "1",
                      "slew_rise_ns": float(rise), "slew_fall_ns": float(fall)}
    if not pins:
        raise ValueError("OpenSTA pin census empty")
    return pins


def _nets(path: Path) -> dict[str, dict]:
    nets = {}
    for block in re.split(r"(?m)^Net ", path.read_text())[1:]:
        name, _, rest = block.partition("\n")
        cap = re.search(r"(?m)^ Total capacitance:\s*([0-9.eE+-]+)", rest)
        loads = re.search(r"(?ms)^Load pins\n(.*?)(?:\n\n|\Z)", rest)
        count = re.search(r"(?m)^ Number of loads:\s*(\d+)", rest)
        if not cap or not count or name in nets or (loads is None and int(count.group(1))):
            raise ValueError("OpenSTA net census malformed")
        load_names = ([line.strip().split()[0] for line in loads.group(1).splitlines()
                       if line.strip()] if loads is not None else [])
        if len(load_names) != int(count.group(1)):
            raise ValueError("OpenSTA net load count differs from raw report")
        nets[name] = {"cap_pf": float(cap.group(1)), "loads": load_names}
    return nets


def _disabled(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        words = line.split()
        if len(words) >= 4 and words[3] in ("constraint", "constant"):
            for port in words[1:3]:
                result[words[0] + "/" + port] = (
                    "disabled" if words[3] == "constraint" else "constant")
    return result


def derive(scene_dir: Path, linked_liberties: list[dict],
           all_rows: dict[str, list[dict]]) -> dict:
    """Reconcile reported rows with pin and net identities from one STA run."""
    pins = _pins(scene_dir / "pin_census.tsv")
    nets = _nets(scene_dir / "net_census.rpt")
    disabled = _disabled(scene_dir / "disabled_edges.rpt")
    libs = []
    for item in linked_liberties:
        limits = _liberty_limits(Path(item["path"]).read_text())
        libs.append((item["name"], limits))
    metadata = {}
    for name, pin in pins.items():
        if pin["kind"] == "port":
            cell_class = "port"
            liberty = None
        elif pin["direction"] == "internal" and not pin["net"]:
            cell_class = "internal"
            liberty = None
        else:
            matches = [(lib_name, limits) for lib_name, limits in libs
                       if pin["cell"] in limits["cells"] and
                       pin["cell_pin"] in limits["cells"][pin["cell"]]]
            if len(matches) != 1:
                raise ValueError(f"OpenSTA instance {name} lacks unique linked Liberty pin")
            liberty, limits = matches[0]
            cell_class = "IO" if pin["cell"] in limits["pad_cells"] else "std"
        net_class = ("constant" if pin["logic"] in ("0", "1") else
                     "clock" if pin["clock"] else
                     "IO" if cell_class in ("IO", "port") else "data")
        metadata[name] = {"net_class": net_class, "cell_class": cell_class,
                          "cell": pin["cell"] or None,
                          "cell_pin": pin["cell_pin"] or None,
                          "liberty": liberty,
                          "driver_pin": name if pin["driver"] else None,
                          "driver_cell": pin["cell"] or None,
                          "net": pin["net"] or None}
    drivers = {name for name, pin in pins.items() if pin["driver"]}
    all_names = {kind: {row["pin"] for row in all_rows[kind]} for kind in KINDS}
    if any(not names.issubset(pins) for names in all_names.values()):
        raise ValueError("OpenSTA DRV report contains pin outside netlist census")
    excluded = []
    omitted = ((drivers - all_names["max_fanout"]) |
               (drivers - all_names["max_capacitance"]) |
               (set(pins) - all_names["max_slew"]))
    for name in sorted(omitted):
        pin = pins[name]
        reason = ("constant" if pin["logic"] in ("0", "1") else
                  (disabled.get(name) or ("ideal" if pin["ideal"] else None)))
        if reason not in ("constant", "disabled", "ideal"):
            raise ValueError(f"OpenSTA omitted driver {name} without exclusion proof")
        net = nets.get(pin["net"])
        if pin["net"] and net is None:
            raise ValueError(f"OpenSTA net {pin['net']} absent for excluded pin")
        fanout = 0.0
        for load_name in (net or {}).get("loads", []):
            load = pins.get(load_name)
            if load is None:
                raise ValueError(f"OpenSTA load {load_name} absent from pin census")
            if load["kind"] == "port":
                continue
            matches = [limits for _, limits in libs if load["cell"] in limits["cells"]
                       and load["cell_pin"] in limits["cells"][load["cell"]]]
            if len(matches) != 1:
                raise ValueError(f"OpenSTA load {load_name} lacks unique Liberty pin")
            limit = matches[0]
            fanout += (limit["cells"][load["cell"]][load["cell_pin"]]
                       .get("fanout_load") or limit.get("default_fanout_load") or 0.0)
        kinds = [kind for kind in KINDS if name not in all_names[kind] and
                 (kind == "max_slew" or pin["driver"])]
        excluded.append({"pin": name, "reason": reason, "excluded_kinds": kinds,
                         "fanout": fanout,
                         "cap_pf": (net or {}).get("cap_pf", 0.0),
                         "slew_rise_ns": pin["slew_rise_ns"],
                         "slew_fall_ns": pin["slew_fall_ns"]})
    if drivers - {item["pin"] for item in excluded
                  if "max_fanout" in item["excluded_kinds"]} != all_names["max_fanout"]:
        raise ValueError("OpenSTA driver census disagrees with all-limits fanout rows")
    expected_cap = drivers - {item["pin"] for item in excluded
                              if "max_capacitance" in item["excluded_kinds"]}
    if all_names["max_capacitance"] != expected_cap:
        raise ValueError("OpenSTA driver census disagrees with all-limits cap rows")
    slew_pins = set(pins) - {item["pin"] for item in excluded
                             if "max_slew" in item["excluded_kinds"]}
    if all_names["max_slew"] != slew_pins:
        raise ValueError("OpenSTA pin census disagrees with all-limits slew rows")
    return {"pins": metadata, "excluded": excluded,
            "clock_network_pins": sorted(name for name, pin in pins.items() if pin["clock"]),
            "driver_pins": sorted(drivers),
            "population": {"max_slew": len(slew_pins),
                           "max_capacitance": len(expected_cap),
                           "max_fanout": len(expected_cap)}}
