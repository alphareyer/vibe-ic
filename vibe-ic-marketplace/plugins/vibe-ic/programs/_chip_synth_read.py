#!/usr/bin/env python3
"""_chip_synth_read.py — THE read of the RTL that produces the chip.

ONE definition, used by BOTH phase-3 synthesis (`phase3_one_shot_runner.step_synth`,
the netlist that goes to place-and-route) and the Step-5 formal proof of the DUT
(R-0915-157), so the two cannot drift:

  * FILE SELECTION — the silicon sources of `rtl/`: every top-level `*.sv` then
    `*.v`, minus FPGA wrappers / test fixtures / assertion files, minus
    include-hub aggregators (`_rtl_include_hub.drop_include_hubs`), packages
    first so `import pkg::*` resolves;
  * DEFINE DECISION — `synth_frontend.decide_macro_aware_sim_define`: `-DSIMULATION`
    unless a real vendor macro is staged for a cell the RTL only instantiates
    with the define absent;
  * FRONTEND — `read_verilog -sv [-DSIMULATION ]<file>` per file;
  * TOP — `effective_top`: `<top>_asic` / `<top>_pad_wrapper` when rtl/ has
    one, else the structural resolver (phase 3 builds THIS module).

THE RECORD (R-0915-157, round 9). Step 5 records the exact read it proved
(`chip_read_record`: every file with its sha256, the define decision with its
verdict and the macro inputs it saw, the top). Phase-3 synthesis writes the
read it BUILT (`write_built_record`) and compares; `chip_read_differences`
names what differs. Any difference makes Step 5's program-closed obligations
STALE: `formal_proof_evidence_check` refuses them, never PASS.

WHY THE PROOF MUST READ THIS AND NOTHING ELSE (R-0915-157, round-7 review). The
proof used to read the DUT with `read_verilog -formal` plus proof-only defines:
FORMAL defined, SIMULATION not. An `ifdef SIMULATION` arm (which the chip
synthesises) or an `ifdef FORMAL` arm (which it never does) then made the
program prove a design that is not the chip, and comparing a signature across
builds leaked in both directions. With ONE DUT read — the chip's — there is
nothing to compare. FORMAL is not defined for the DUT; proof defines apply to
the generated harness only.

chip-AGNOSTIC: generic file-name conventions and the shared macro decision.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from _rtl_include_hub import drop_include_hubs  # noqa: E402
import synth_frontend as _sf  # noqa: E402

#: File-name fragments that are never silicon (phase-3 synth's own list).
NON_SILICON_SUBSTRS = ("assertions", "de10lite_top", "host_emulator", "_tb",
                       "testbench", "stimulus")


def chip_rtl_files(rtl_dir: Path) -> List[Path]:
    """The silicon sources, in read order (packages first)."""
    all_rtl = sorted(Path(rtl_dir).glob("*.sv")) + sorted(Path(rtl_dir).glob("*.v"))
    silicon = [f for f in all_rtl
               if not any(s in f.name.lower() for s in NON_SILICON_SUBSTRS)]
    silicon = drop_include_hubs(silicon)
    pkg_files = [f for f in silicon if "pkg" in f.name.lower()]
    other = [f for f in silicon if "pkg" not in f.name.lower()]
    return pkg_files + other


def chip_sim_define(rtl_files: Sequence[Path],
                    macro_files: Sequence) -> Tuple[str, dict]:
    """(the `-D` prefix the chip's read uses, the decision record)."""
    decision = _sf.decide_macro_aware_sim_define(
        _sf.read_text_blob(list(rtl_files)), list(macro_files))
    return ("-DSIMULATION " if decision["define_sim"] else ""), decision


def chip_read_lines(rtl_files: Sequence[Path], simdef: str,
                    path: Callable[[Path], str] = str) -> List[str]:
    """The yosys read commands, one per file, exactly as synthesis issues them."""
    return [f"read_verilog -sv {simdef}{path(f)}" for f in rtl_files]


def staged_macro_files(project: Path) -> List[str]:
    """The staged hard-macro views phase 3 hands the define decision
    (`_discover_local_macros`: libs + LEFs + Verilog models)."""
    import phase3_one_shot_runner as _p3
    libs, lefs, _gds, mv = _p3._discover_local_macros(Path(project))
    return list(libs) + list(lefs) + list(mv)


def effective_top(project: Path, top_name: str) -> str:
    """The module phase-3 synthesis builds for a requested `top_name` — the
    ONE resolution `phase3_one_shot_runner.main` uses for every phase-3 step:
    `<top>_asic` / `<top>_pad_wrapper` when rtl/ carries one, else the
    structural resolver (unchanged when `top_name` is a real module)."""
    import _path_layout as _pl
    for cand in (f"{top_name}_asic", f"{top_name}_pad_wrapper"):
        if (_pl.rtl_dir(Path(project)) / f"{cand}.sv").is_file():
            return cand
    import phase3_one_shot_runner as _p3
    structural = _p3._resolve_asic_top_structural(
        Path(project), top_name, _p3._l9_top_module_hint(Path(project)))
    return structural or top_name


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def chip_read_record(rtl_files: Sequence[Path], macro_files: Sequence,
                     top: str) -> dict:
    """The exact chip read: what a proof proved, or what synthesis built."""
    _simdef, decision = chip_sim_define(rtl_files, macro_files)
    return {
        "files": [{"name": Path(f).name, "sha256": _sha256(Path(f))}
                  for f in rtl_files],
        "define": {"simulation": bool(decision.get("define_sim")),
                   "verdict": decision.get("verdict"),
                   "macro_cells": list(decision.get("macro_cells") or []),
                   "macro_inputs": sorted(Path(str(m)).name for m in macro_files)},
        "top": top,
    }


def chip_read_differences(proved: Optional[dict], built: Optional[dict]) -> List[str]:
    """What differs between the read a proof proved and the read a chip is
    built from. Empty = the same chip."""
    if not isinstance(proved, dict):
        return ["the proof recorded no chip read"]
    if not isinstance(built, dict):
        return []
    out: List[str] = []
    if proved.get("top") != built.get("top"):
        out.append(f"top: the proof proved {proved.get('top')!r}, the chip "
                   f"is built from {built.get('top')!r}")
    pd, bd = proved.get("define") or {}, built.get("define") or {}
    if (pd.get("simulation"), pd.get("verdict")) != (bd.get("simulation"), bd.get("verdict")):
        out.append(
            f"define: the proof read SIMULATION={'defined' if pd.get('simulation') else 'undefined'} "
            f"({pd.get('verdict')}, macro inputs {pd.get('macro_inputs')}), the chip "
            f"SIMULATION={'defined' if bd.get('simulation') else 'undefined'} "
            f"({bd.get('verdict')}, macro inputs {bd.get('macro_inputs')})")
    pf = {f["name"]: f["sha256"] for f in proved.get("files") or []}
    bf = {f["name"]: f["sha256"] for f in built.get("files") or []}
    if set(pf) != set(bf):
        out.append(f"file set: only proved {sorted(set(pf) - set(bf))}, "
                   f"only built {sorted(set(bf) - set(pf))}")
    changed = sorted(n for n in set(pf) & set(bf) if pf[n] != bf[n])
    if changed:
        out.append(f"file contents (sha256) differ: {changed}")
    return out


def current_chip_read(project: Path, top_name: str) -> dict:
    """The chip read as phase 3 would take it NOW: the file selection, the
    define decision on the macros staged NOW, the effective top."""
    import _path_layout as _pl
    files = chip_rtl_files(_pl.rtl_dir(Path(project)))
    return chip_read_record(files, staged_macro_files(project),
                            effective_top(project, top_name))


#: Where phase-3 synthesis records the read it built.
BUILT_RECORD = "chip_read_built.json"


def built_record_path(project: Path) -> Path:
    import _path_layout as _pl
    return _pl.synth_dir(Path(project)) / BUILT_RECORD


def proved_record(project: Path) -> Optional[dict]:
    """The chip read Step 5's program-closed proof recorded, if any."""
    import _path_layout as _pl
    try:
        res = json.loads((_pl.formal_dir(Path(project)) / "results.json").read_text())
    except (OSError, ValueError):
        return None
    if not res.get("program_discharged_obligations"):
        return None
    return res.get("chip_read") or {}


def write_built_record(project: Path, rtl_files: Sequence[Path],
                       macro_files: Sequence, top: str) -> List[str]:
    """Phase-3 synthesis: record the read it built and COMPARE it against the
    read Step 5 proved. Returns the differences (also written)."""
    rec = chip_read_record(rtl_files, macro_files, top)
    proved = proved_record(project)
    diffs = chip_read_differences(proved, rec) if proved is not None else []
    rec["stale_against_step5"] = diffs
    path = built_record_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2) + "\n")
    return diffs


def bind_built_record_netlist(project: Path, netlist: Path) -> None:
    """Bind the built record to the netlist that read produced (path +
    sha256), once synthesis has written it. A record with no binding, or
    bound to other bytes, describes no netlist a later reader holds."""
    path = built_record_path(project)
    rec = json.loads(path.read_text())
    netlist = Path(netlist)
    try:
        rel = str(netlist.resolve().relative_to(Path(project).resolve()))
    except ValueError:
        rel = str(netlist)
    rec["netlist"] = {"path": rel, "sha256": _sha256(netlist)}
    path.write_text(json.dumps(rec, indent=2) + "\n")


def built_record_for_current_netlist(project: Path
                                     ) -> Tuple[Optional[dict], str]:
    """(the built record, "") when it describes the netlist on disk now,
    else (None, why it cannot be compared). Only direct-mode synthesis
    writes and binds the record; a LibreLane synthesis, a synthesis that
    did not finish, or a later re-synthesis leaves it absent, unbound or
    bound to bytes that are no longer the netlist."""
    project = Path(project)
    path = built_record_path(project)
    if not path.is_file():
        return None, f"no {BUILT_RECORD} (only direct-mode synthesis writes it)"
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"{BUILT_RECORD} is unreadable ({exc})"
    bound = rec.get("netlist") if isinstance(rec, dict) else None
    if not isinstance(bound, dict) or not bound.get("path") or not bound.get("sha256"):
        return None, (f"{BUILT_RECORD} is bound to no netlist (synthesis did "
                      f"not finish, or it predates the binding)")
    netlist = project / str(bound["path"])
    if not netlist.is_file():
        return None, f"{BUILT_RECORD} describes {bound['path']}, which is absent"
    now = _sha256(netlist)
    if now != bound["sha256"]:
        return None, (f"{BUILT_RECORD} describes {bound['path']} sha256 "
                      f"{bound['sha256']}, but that netlist is now {now} "
                      f"(re-synthesised since, e.g. by the LibreLane arm)")
    return rec, ""


def unstaged_analog_blocks(project: Path) -> List[str]:
    """Declared analog blocks whose A8 hard macro is NOT staged yet. While any
    is missing, the chip's define decision cannot be known (A8 stages the
    macro views the decision reads), so a Step-5 program closure is outside
    the class."""
    import _path_layout as _pl
    project = Path(project)
    names: List[str] = []
    for bl in (_pl.analog_dir(project) / "analog_block_list.json",
               project / "phase1/analog/analog_block_list.json"):
        try:
            data = json.loads(bl.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        rows = data.get("blocks") if isinstance(data, dict) else data
        for r in rows or []:
            n = r.get("name") or r.get("block") if isinstance(r, dict) else r
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
    adir = _pl.analog_dir(project)
    if adir.is_dir():
        names += [d.name for d in adir.iterdir()
                  if d.is_dir() and (d / "spec.json").is_file()]
    hm = _pl.hardmacro_dir(project)
    return sorted({n for n in names
                   if not ((hm / n).is_dir() and any((hm / n).glob("*.lef")))})
