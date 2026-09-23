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
  * FRONTEND — `read_verilog -sv [-DSIMULATION ]<file>` per file.

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

import sys
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

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
