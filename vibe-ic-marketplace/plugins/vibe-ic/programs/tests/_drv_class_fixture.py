"""Synthetic, digest-bound OpenSTA receipt setup for shared class controls."""
import json
from pathlib import Path

from drv_signoff_judge import _sha


def write_witness(project, netlist, sdc, lib, pins, *, scene="typ_nom"):
    """Pins are explicit (name, cell, cell_pin, net, driver) fixture facts.

    This models the existing capture format, not a Verilog connectivity reader.
    Tool execution is measured separately; these fixture receipts are synthetic.
    """
    project = Path(project)
    folder = project / "reports/phase3/sta/drv_capture" / scene
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    nets = {}
    for name, cell, cell_pin, net, driver in pins:
        kind = "pin" if cell else "port"
        inst = name.rpartition("/")[0] if cell else ""
        rows.append("\t".join(map(str, (name, kind, "input" if not driver or not cell else "output",
                    int(driver), inst, cell, cell_pin, net, "input", 0.1, 0.1, 0, "X", 0))) + "\n")
        entry = nets.setdefault(net, {"drivers": [], "loads": []})
        entry["drivers" if driver else "loads"].append(name)
    pin_file = folder / "pin_census.tsv"
    pin_file.write_text("".join(rows))
    text = []
    for net, entry in nets.items():
        text += [f"Net {net}", " Total capacitance: 2.9",
                 f" Number of drivers: {len(entry['drivers'])}",
                 f" Number of loads: {len(entry['loads'])}",
                 f" Number of pins: {len(entry['drivers']) + len(entry['loads'])}", "", "Driver pins"]
        text += [f" {p} input" for p in entry["drivers"]]
        text += ["", "Load pins"] + [f" {p} input" for p in entry["loads"]] + ["", ""]
    net_file = folder / "net_census.rpt"
    net_file.write_text("\n".join(text))
    run = {"run_id": "synthetic-run", "plugin_tree_sha256": "1" * 64}
    run_path = project / "reports/phase3/drv_run_identity.json"
    run_path.write_text(json.dumps(run))
    def ref(p):
        return {"path": str(Path(p).resolve()), "sha256": _sha(Path(p))}
    doc = {"identity": {"project": str(project.resolve()), "run_id": run["run_id"],
                       "tree_sha": run["plugin_tree_sha256"],
                       "opensta_commit": "synthetic-tool", "tool_image_digest": "sha256:" + "2" * 64,
                       "artifacts": {"sta_netlist": ref(netlist)}},
           "current": {"sources": {"signoff_sdc": ref(sdc)}},
           "scenes": [{"name": scene, "fresh_process": True,
                       "linked_liberties": [{"name": "io", **ref(lib)}],
                       "pin_census_report": ref(pin_file), "net_census_report": ref(net_file)}]}
    source = project / "reports/phase3/sta/drv_signoff_bundle.json"
    source.write_text(json.dumps(doc))
    return source
