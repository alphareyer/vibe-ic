#!/usr/bin/env python3
"""odbpy body of LibreLane step `Vibeic.InsertSpareCells` (flow step 18).

Runs inside OpenROAD's Python (`openroad -python`) on the DetailedPlacement
ODB, through LibreLane's own `reader.click_odb` harness. What it does, in the
order the direct deck (`phase3_one_shot_runner._build_spare_*_tcl`) proved it
must happen:

1. PLAN. `VIBEIC_SPARE_PLAN` (the caller's plan) or, when unset, the same
   `_spare_plan.build_spare_cells_plan` run here on the placed database:
   logic-cell count, core box and the library's own master names.
2. INSERT each spare PLACED at its plan position, then legalize ONLY the
   spares (every other instance is LOCKED for the call and restored), so the
   tool's placement is not re-legalized around them.
3. TIE-OFF. One tie-low driver PER SPARE, placed at that spare, on net
   `spare_tielo_<spare>` (one driver for the whole pool is an RC tree: 12
   antenna diodes and 54 max_slew rows, #563 r4). Connected BEFORE the spare
   becomes dont_touch (odb refuses a dont_touch iterm, ODB-0369). The drivers
   are then legalized on their own.
4. SUPPLY. `global_connect` skips dont_touch instances (ODB-0383, 236 floating
   spare PG pins, R-0915-24), so every spare and tie driver has its POWER/
   GROUND pins connected here, explicitly, by the block's own global-connect
   rules -- or, when the ODB carries none, by the config's SCL power/ground
   pin names and VDD/GND nets, the rules LibreLane itself applies.
5. PROTECT: spares dont_touch + FIRM; tie nets dont_touch.
6. MEASURE: `check_placement`'s own count, printed in the runner's marker
   grammar so the existing Step-17/18 readers consume it unchanged.

chip-AGNOSTIC: every name comes from the plan, the library or the config.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from reader import click, click_odb, odb  # LibreLane's odbpy harness

sys.path.append(str(Path(__file__).resolve().parents[2]))
import _spare_plan as sp  # noqa: E402  (vibe-ic programs/, mounted read-only)

#: Master classes that are logic (what the direct flow's netlist count sees).
_LOGIC_TYPES = {"CORE", "CORE_TIEHIGH", "CORE_TIELOW", "BLOCK"}


def say(line: str) -> None:
    print(line, flush=True)


def _um(block, dbu: int) -> float:
    return dbu / block.getDbUnitsPerMicron()


def _dbu(block, um) -> int:
    return int(round(float(um) * block.getDbUnitsPerMicron()))


def plan_from_block(block, db, density: float) -> dict:
    """`_spare_plan` on the PLACED database: the direct flow's plan inputs,
    measured instead of estimated from the netlist."""
    logic = [i for i in block.getInsts()
             if i.getMaster().getType() in _LOGIC_TYPES]
    core = block.getCoreArea()
    box = (int(_um(block, core.xMin())), int(_um(block, core.yMin())),
           int(_um(block, core.xMax())), int(_um(block, core.yMax())))
    names = [m.getName() for lib in db.getLibs() for m in lib.getMasters()
             if m.getType() == "CORE"]
    used = {i.getMaster().getName() for i in logic}
    has_pads = any(i.getMaster().getType().startswith("PAD")
                   for i in block.getInsts())
    return sp.build_spare_cells_plan(
        len(logic), density, box, sp.discover_spare_cells(names, used),
        has_pad_ring=has_pads, used_cells=used)


def _legalize_only(block, dpl, movable, config) -> str:
    """Detailed placement of `movable` alone; everything else is LOCKED for
    the call and restored after (the `Odb.InsertECOBuffers` pattern)."""
    keep = {i.getName() for i in movable}
    restore = []
    for inst in block.getInsts():
        if inst.getName() in keep:
            continue
        status = inst.getPlacementStatus()
        if status != "LOCKED":
            restore.append((inst, status))
            inst.setPlacementStatus("LOCKED")
    try:
        site = block.getRows()[0].getSite()
        pad = int(config.get("DPL_CELL_PADDING") or 0) // 2
        dpl.setPaddingGlobal(pad, pad)
        dpl.detailedPlacement(
            int(_dbu(block, config.get("PL_MAX_DISPLACEMENT_X") or 500) / site.getWidth()),
            int(_dbu(block, config.get("PL_MAX_DISPLACEMENT_Y") or 100) / site.getHeight()))
        return "ok"
    except Exception as exc:  # the tool's refusal, recorded; checked below
        return f"{exc}"
    finally:
        for inst, status in restore:
            inst.setPlacementStatus(status)


def _pg_rules(block, config) -> list:
    """(net, instance-regex, pin-regex) supply rules, and where they came from."""
    rules = [(g.getNet(), g.getInstPattern(), g.getPinPattern())
             for g in block.getGlobalConnects()]
    if rules:
        return rules
    for pins_key, nets_key in (("SCL_POWER_PINS", "VDD_NETS"),
                               ("SCL_GROUND_PINS", "GND_NETS")):
        nets = config.get(nets_key) or []
        net = block.findNet(nets[0]) if nets else None
        for pin in config.get(pins_key) or []:
            if net is not None:
                rules.append((net, ".*", f"^{re.escape(pin)}$"))
    return rules


def connect_supply(block, insts, rules) -> tuple:
    """Connect every POWER/GROUND iterm of `insts` by the first matching rule."""
    connected, missing = 0, []
    for inst in insts:
        for it in inst.getITerms():
            if it.getSigType() not in ("POWER", "GROUND"):
                continue
            if it.getNet() is not None:
                connected += 1
                continue
            pin = it.getMTerm().getName()
            for net, inst_rx, pin_rx in rules:
                if (net.getSigType() == it.getSigType()
                        and re.search(inst_rx, inst.getName())
                        and re.search(pin_rx, pin)):
                    it.connect(net)
                    connected += 1
                    break
            else:
                missing.append(f"{inst.getName()}/{pin}")
    return connected, missing


def insert(reader, config: dict, plan: dict) -> dict:
    block, db = reader.block, reader.db
    dpl = reader.design.getOpendp()
    measured = {"inserted": 0, "tieoff_candidates": 0, "tieoff_connected": 0,
                "tieoff_drivers": 0, "pg_connected": 0, "pg_unconnected": 0,
                "pg_unconnected_pins": [], "firm_locked": 0}
    spares = []
    for rec in plan.get("instances") or []:
        name, cell = rec.get("name"), rec.get("cell")
        master = db.findMaster(cell) if cell else None
        if master is None:
            say(f"SPARE_INSERT_NONFATAL {name}: master {cell!r} not in the database")
            continue
        inst = block.findInst(name) or odb.dbInst.create(block, master, name)
        inst.setOrient("R0")
        inst.setLocation(_dbu(block, rec.get("llx", 0)), _dbu(block, rec.get("lly", 0)))
        inst.setPlacementStatus("PLACED")
        spares.append(inst)
    measured["inserted"] = len(spares)
    if spares:
        say(f"SPARE_LEGALIZE: {_legalize_only(block, dpl, spares, config)}")

    tie = str(config.get("VIBEIC_SPARE_TIELO_CELL") or config.get("SYNTH_TIELO_CELL") or "")
    tie_cell, _, tie_pin = tie.partition("/")
    tie_master = db.findMaster(tie_cell) if tie_cell else None
    drivers, tie_nets = [], []
    if spares and (tie_master is None or not tie_pin):
        say(f"SPARE_TIEOFF_SKIPPED: no tie-low cell/pin resolved from {tie!r} "
            "-- spare inputs remain floating")
    for inst in spares if tie_master is not None and tie_pin else []:
        floating = [it for it in inst.getITerms()
                    if it.getSigType() == "SIGNAL" and it.isInputSignal()
                    and it.getNet() is None]
        if not floating:
            continue
        measured["tieoff_candidates"] += len(floating)
        net_name = f"spare_tielo_{inst.getName()}"
        drv = (block.findInst(net_name + "_drv")
               or odb.dbInst.create(block, tie_master, net_name + "_drv"))
        drv.setOrient("R0")
        drv.setLocation(*inst.getLocation())
        drv.setPlacementStatus("PLACED")
        out = drv.findITerm(tie_pin)
        if out is None:
            say(f"SPARE_TIEOFF_SKIPPED: tie cell has no {tie_pin} pin")
            break
        net = block.findNet(net_name) or odb.dbNet.create(block, net_name)
        out.connect(net)
        for it in floating:
            it.connect(net)
            measured["tieoff_connected"] += 1
        drivers.append(drv)
        tie_nets.append(net)
    measured["tieoff_drivers"] = len(drivers)
    say(f"SPARE_TIEOFF_CONNECTED {measured['tieoff_connected']} of "
        f"{measured['tieoff_candidates']}")
    say(f"SPARE_TIEOFF_DRIVERS {len(drivers)}")
    say("SPARE_TIEOFF_DONE: nets " + " ".join(n.getName() for n in tie_nets))
    if drivers:
        result = _legalize_only(block, dpl, drivers, config)
        say("SPARE_TIEOFF_LEGALIZED: detailed_placement ok" if result == "ok"
            else f"SPARE_TIEOFF_LEGALIZE_NONFATAL: {result}")

    connected, missing = connect_supply(block, spares + drivers,
                                        _pg_rules(block, config))
    measured.update(pg_connected=connected, pg_unconnected=len(missing),
                    pg_unconnected_pins=missing[:50])
    say(f"SPARE_PG_CONNECTED {connected} of {connected + len(missing)}")

    for net in tie_nets:
        net.setDoNotTouch(True)
        say(f"SPARE_TIE_NET_DONT_TOUCH: {net.getName()}")
    for inst in spares:
        inst.setDoNotTouch(True)
        inst.setPlacementStatus("FIRM")
    measured["firm_locked"] = len(spares)
    say(f"SPARE_FIRM_LOCKED: {len(spares)} instances")

    count = reader.design.evalTclString("check_placement -no_abort")
    try:
        measured["check_placement_violations"] = int(str(count).strip())
        say(f"SPARE_CHECK_PLACEMENT_VIOLATIONS {measured['check_placement_violations']}")
    except ValueError:
        measured["check_placement_violations"] = None
        say(f"SPARE_CHECK_PLACEMENT_UNAVAILABLE: non-numeric result '{count}'")
    placed = {i.getName(): i.getLocation() for i in spares}
    for rec in plan.get("instances") or []:
        if rec.get("name") in placed:
            x, y = placed[rec["name"]]
            rec["planned_llx"], rec["planned_lly"] = rec.get("llx"), rec.get("lly")
            rec["llx"], rec["lly"] = round(_um(block, x), 4), round(_um(block, y), 4)
    return measured


@click.command()
@click.option("--report", "report_path", required=True)
@click.option("--output-nl", "output_nl", required=True)
@click.option("--output-pnl", "output_pnl", required=True)
@click_odb
def cli(reader, report_path, output_nl, output_pnl):
    config = reader.config or {}
    if config.get("VIBEIC_SPARE_PLAN"):
        plan = json.loads(Path(config["VIBEIC_SPARE_PLAN"]).read_text())
        source = f"VIBEIC_SPARE_PLAN {config['VIBEIC_SPARE_PLAN']}"
    else:
        density, warn = sp.compute_spare_density(config.get("VIBEIC_SPARE_DENSITY"))
        if warn:
            say(f"SPARE_DENSITY_NOTE: {warn}")
        plan = plan_from_block(reader.block, reader.db, density)
        source = "built in-step by _spare_plan from the placed database"
    plan = dict(plan)
    measured = insert(reader, config, plan)
    plan["plan_source"] = source
    plan["measured"] = measured
    plan["tied_off"] = bool(measured["tieoff_candidates"] == measured["tieoff_connected"]
                            and plan.get("instances"))
    reader.design.evalTclString(f"write_verilog {{{output_nl}}}")
    reader.design.evalTclString(f"write_verilog -include_pwr_gnd {{{output_pnl}}}")
    tmp = report_path + ".tmp"
    with open(tmp, "w", encoding="utf8") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    os.replace(tmp, report_path)


if __name__ == "__main__":
    cli()
