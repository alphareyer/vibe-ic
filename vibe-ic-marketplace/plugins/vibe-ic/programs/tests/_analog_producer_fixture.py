"""Shared SYNTHETIC fixture builder for the A1-A3 producer tests.

Every name here is invented (`vreg_alpha`, `blk_alpha`, `keeper_x`,
`widget_q`, `doc_alpha.md`). No chip, PDK SKU, vendor or part number appears
in this file or in anything it writes.

The producers are driven as SUBPROCESSES and every assertion is about the
ARTEFACTS on disk and about the rc of the SHIPPED gates. No test reaches into
a producer's internals, so a test can only fail on a wrong artefact or a wrong
absence — never on a renamed helper.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import _plugin_tree  # noqa: F401  — puts programs/ on sys.path
import _watchdog

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))

A1 = PROGRAMS / "analog_a1_spec_emit.py"
A2 = PROGRAMS / "analog_a2_topology_emit.py"
A3 = PROGRAMS / "analog_a3_netlist_emit.py"

GATE_A1 = PROGRAMS / "analog_a1_spec_extract_check.py"
GATE_A2 = PROGRAMS / "analog_a2_topology_select_check.py"
GATE_A3 = PROGRAMS / "analog_a3_netlist_gen_check.py"
NETLIST_CHECKERS = (
    PROGRAMS / "analog_netlist_pdk_check.py",
    PROGRAMS / "analog_netlist_connectivity_check.py",
    PROGRAMS / "analog_netlist_include_order_check.py",
    PROGRAMS / "analog_netlist_path_lint.py",
)


def block(name: str, btype: str, specs: Optional[List[Dict[str, Any]]] = None,
          low_confidence: bool = False) -> Dict[str, Any]:
    """One synthetic `analog_blocks[]` entry. `specs=None` models the honest
    Phase-1 outcome this whole round is about: the documents mention the block
    and attribute no number to it."""
    return {
        "name": name,
        "type": btype,
        "low_confidence": low_confidence,
        "evidence": "doc_alpha.md (keyword)",
        "evidence_paragraph": f"The alpha subsystem contains a {btype}.",
        "spec": ({"specs": specs} if specs is not None else None),
    }


def make_project(root: Path, blocks: List[Dict[str, Any]],
                 unattributed_rows: int = 0) -> Path:
    """`unattributed_rows` models the real Phase-1 shape the producers refuse
    to bind: electrical rows that carry NO block key, so deciding which block
    each belongs to is judgment."""
    (root / "phase1/generated_docs").mkdir(parents=True, exist_ok=True)
    (root / "phase3/analog").mkdir(parents=True, exist_ok=True)
    (root / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": blocks}, indent=2), encoding="utf-8")
    l5: Dict[str, Any] = {"analog_blocks": blocks}
    if unattributed_rows:
        l5["electrical_specs"] = [
            {"param": f"row_{i}", "value": 1.0 + i, "unit": "V",
             "source": "doc_alpha.md"}
            for i in range(unattributed_rows)]
    (root / "phase1/generated_docs/L5_ADI_SPEC.json").write_text(
        json.dumps(l5, indent=2), encoding="utf-8")
    return root


def stage_custom_pdk(root: Path, family: str) -> Path:
    """Stage a rung-1 project-custom PDK for `family` and declare it in L19.

    WHY A TEST THAT IS NOT ABOUT PDKs NEEDS THIS (vibe-ic#2139). A producer
    driven with `--pdk <family>` on a host where that family is neither staged
    nor installed used to be handed ANOTHER family's model library under the
    requested name, and the roles that library does not cover were filled from
    the requested family's registry entry. The deck that came out loaded one
    process and instantiated another's devices; ngspice refused it with
    `unknown subckt`, and every test that only read the artefact was green.

    `analog_a3_netlist_emit.resolve_pdk_context` now refuses that binding
    instead of emitting it, so a test that wants a RENDERED netlist for a
    family has to give the producer a context that names one PDK. This is the
    cheapest honest way to do that with no container and no PDK tree: one
    sectioned model lib on disk under the project's own `input/pdk/`, which is
    rung 1 of `analog_pdk_availability.resolve_pdk` — the same shape a project
    staging its own process assets uses.

    The device names are synthetic and carry only the STRUCTURAL role tokens
    `analog_pdk_deck_context._ROLE_TOKENS` assigns by, so nothing here names a
    foundry device. Roles this lib does not carry still resolve from the
    family's own registry entry, which is the SAME family, so the context
    still names one PDK.
    """
    # TERMINAL COUNT IS PART OF THE CONTRACT, not decoration: the deck
    # emitter refuses an IR whose instance line has fewer nodes than the
    # resolved subckt declares ("Too few parameters for subcircuit"), so the
    # staged devices carry the terminal counts the topology IR writes — four
    # for the MOS pair, three for a resistor, two for a capacitor.
    devices = [("stg_nmos_dev", "d g s b"), ("stg_pmos_dev", "d g s b"),
               ("stg_res_x_dev", "p1 p2 b"), ("stg_cap_x_dev", "p1 p2")]
    body = "\n".join(f".subckt {name} {nodes} w=1 l=1\n.ends"
                      for name, nodes in devices)
    text = "* staged model lib\n"
    for section in ("ss", "tt", "ff"):
        text += f".lib {section}\n{body}\n.endl\n"
    lib = root / "input" / "pdk" / "spice" / "staged_models.lib"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(text, encoding="utf-8")
    gen = root / "phase1" / "generated_docs"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "L19_CONSTRAINTS_PDK.json").write_text(
        json.dumps({"fields": {"pdk_target": family}}, indent=2),
        encoding="utf-8")
    return lib


def run_prog(prog: Path, project: Path, *args: str,
             stall_grace_s: Optional[float] = None
             ) -> subprocess.CompletedProcess:
    """Drive a producer as a subprocess, bounded by NO-PROGRESS, not by a clock.

    This used to be `subprocess.run(..., timeout=60)`. 60 s is not a property
    of any producer here — it is a guess about a HOST, and it was wrong: the
    owner hit a `TimeoutExpired` on `analog_a3_netlist_emit`, a module nobody
    had touched, on a loaded machine. The failing test named a defect in the
    producer; the producer was fine and the machine was busy. A wall-clock
    budget cannot tell those apart, because "how long has it been" is a
    different question from "is it working".

    So nothing here bounds RUNTIME. `run_host_supervised` watches the job's
    /proc tree and kills only a job that is not PROGRESSING — no CPU, no I/O,
    no output across the grace window. A producer that is merely slow, on a
    loaded host or a slower one, now runs to completion however long it takes;
    a producer that genuinely hangs still dies, and arrives as rc RC_STALLED
    with WATCHDOG_STALLED on stderr rather than as whatever rc the assertion
    happened to be looking for. `stall_grace_s` is IDLE time, never a runtime
    estimate, so it needs no per-host tuning; a test that wants to prove the
    stall path passes a small one."""
    cmd = [sys.executable, str(prog), str(project), *args]
    res = _watchdog.run_host_supervised(
        cmd, stall_grace_s=(_watchdog.DEFAULT_STALL_GRACE_S
                            if stall_grace_s is None else stall_grace_s))
    return _watchdog.completed_process(cmd, res)


def bdir(project: Path, name: str) -> Path:
    return project / "phase3/analog" / name


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def all_sp_files(project: Path) -> List[Path]:
    """Every `.sp` anywhere under the project — the negative control asserts
    on the whole tree, not on one expected path, so a netlist smuggled into a
    different directory would still be caught."""
    return sorted(project.rglob("*.sp"))
