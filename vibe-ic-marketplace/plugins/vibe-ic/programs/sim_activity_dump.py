#!/usr/bin/env python3
"""sim_activity_dump.py — a DUT-scoped VCD for vector power, with its scope.

Step 4 simulates the design; Step 33 wants the switching activity of that
simulation. OpenSTA's `read_vcd` needs to be told WHICH VCD scope is the linked
top: MEASURED in the released image (OpenSTA 3.1.0) on a routed netlist with a
testbench VCD, `read_vcd tb.vcd` prints `Annotated 0 pin activities.`,
`read_vcd -scope <tb>/<wrong>` prints the same, and `read_vcd -scope <tb>/<dut>`
annotates the DUT's pins. The deprecated `read_power_activities -vcd` that the
power deck used raises `default is not a mode object` and annotates nothing at
all. So the scope is part of the artefact, not a guess made later: this program
writes the VCD and a manifest naming the scope it verified in the VCD header.

It adds a dump module as a second simulation top (`$dumpvars(0, <tb>.<inst>)`),
so the testbench source is never edited. The dump file is named relative to
the run directory, so the same deck works when the simulator runs in a
container that mounts the tree elsewhere. The testbench is the first one that
instantiates the design top; its instance name comes from the testbench text.

Manifest `activity.json`: vcd, scope (`<tb_top>/<inst>`), dut_module, tb,
vcd_sha256, scope_verified. Exit 0 written; 1 simulated but the VCD is empty or
does not carry the scope; 2 nothing to dump (no testbench instantiates the top).
chip-AGNOSTIC: module/instance names come from the design's own sources.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
import _eda_tool_route as _tool_route  # noqa: E402 — WHERE an EDA tool runs, decided once (FX-N1)

MANIFEST = "activity.json"
DUMP_TOP = "vibeic_vcd_dump"
_MODULE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)", re.M)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def dut_instance(tb_text: str, dut_module: str) -> Optional[Tuple[str, str]]:
    """(tb_top, instance) when the testbench's first module instantiates the DUT."""
    text = _COMMENTS.sub("", tb_text)
    top = _MODULE.search(text)
    if not top:
        return None
    inst = re.search(r"\b" + re.escape(dut_module)
                     + r"\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
                     r"([A-Za-z_][A-Za-z0-9_$]*)\s*\(", text[top.end():])
    return (top.group(1), inst.group(1)) if inst else None


def vcd_scopes(vcd: Path) -> List[str]:
    """Every `a/b/c` scope path declared in the VCD header."""
    stack: List[str] = []
    found: List[str] = []
    with vcd.open("r", errors="replace") as fh:
        for line in fh:
            if line.startswith("$enddefinitions"):
                break
            words = line.split()
            if len(words) >= 3 and words[0] == "$scope":
                stack.append(words[2])
                found.append("/".join(stack))
            elif words[:1] == ["$upscope"] and stack:
                stack.pop()
    return found


def _local(argv: Sequence[str], cwd: str) -> Tuple[int, str, str]:
    """The default exec: the tool on its `_eda_tool_route` route (FX-N1 — the
    pinned image whenever a container route exists, never the host PATH). A
    refused route is rc 127 with the reason."""
    try:
        r = _tool_route.run([str(a) for a in argv], cwd=cwd,
                            capture_output=True, text=True)
    except _tool_route.ToolRouteRefused as exc:
        return 127, "", str(exc)
    return r.returncode, r.stdout, r.stderr


def dump(rtl: Sequence[str], tbs: Sequence[str], dut_module: str,
         out_dir: Path,
         exec_fn: Optional[Callable[[Sequence[str], str],
                                    Tuple[int, str, str]]] = None
         ) -> Dict[str, Any]:
    """Simulate the first testbench that instantiates `dut_module`, dumping it."""
    exec_fn = exec_fn or _local
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen = None
    for tb in tbs:
        try:
            found = dut_instance(Path(tb).read_text(errors="replace"), dut_module)
        except OSError:
            continue
        if found:
            chosen = (Path(tb), *found)
            break
    if chosen is None:
        return {"status": "NOT_APPLICABLE",
                "reason": f"no testbench instantiates {dut_module}"}
    tb, tb_top, inst = chosen
    vcd = out_dir / f"{tb.stem}.vcd"
    vcd.unlink(missing_ok=True)
    dumper = out_dir / f"{DUMP_TOP}.v"
    dumper.write_text(f"module {DUMP_TOP};\n  initial begin\n"
                      f"    $dumpfile(\"{vcd.name}\");\n"
                      f"    $dumpvars(0, {tb_top}.{inst});\n  end\nendmodule\n")
    vvp = out_dir / f"{tb.stem}.vvp"
    dirs: List[str] = []
    for d in [tb.parent] + [Path(f).parent for f in rtl]:
        if str(d) not in dirs:
            dirs.append(str(d))
    rc, out, err = exec_fn(["iverilog", "-g2012", "-o", str(vvp),
                            "-s", tb_top, "-s", DUMP_TOP,
                            *[f"-I{d}" for d in dirs],
                            str(tb), *[str(f) for f in rtl], str(dumper)],
                           str(out_dir))
    if rc != 0:
        return {"status": "FAIL", "reason": f"iverilog rc={rc}: {err[-400:]}"}
    rc, out, err = exec_fn(["vvp", "-n", str(vvp)], str(out_dir))
    scope = f"{tb_top}/{inst}"
    if not vcd.is_file() or vcd.stat().st_size == 0:
        return {"status": "FAIL", "reason": f"vvp rc={rc} wrote no VCD at {vcd}"}
    verified = scope in vcd_scopes(vcd)
    manifest = {"status": "PASS" if verified else "FAIL",
                "vcd": str(vcd), "scope": scope, "dut_module": dut_module,
                "tb": str(tb), "vvp_rc": rc, "scope_verified": verified,
                "vcd_sha256": hashlib.sha256(vcd.read_bytes()).hexdigest()}
    if not verified:
        manifest["reason"] = f"the VCD header declares no scope {scope}"
    write_json(out_dir / MANIFEST, manifest)
    return manifest


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rtl", nargs="+", required=True)
    ap.add_argument("--tb", nargs="+", required=True)
    ap.add_argument("--top", required=True, help="design top module")
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    result = dump(args.rtl, args.tb, args.top, args.out_dir)
    print(f"[sim_activity_dump] {result['status']}: "
          + str(result.get("reason") or result.get("scope")))
    return {"PASS": 0, "NOT_APPLICABLE": 2}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
