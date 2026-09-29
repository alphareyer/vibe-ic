"""Class a sign-off DRV violator row the way the DRV standard judges it.

OpenSTA holds one design-wide `set_max_capacitance` / `set_max_transition`
margin for every driver, top-level ports and IO-cell pins included: its checker
reads the top-cell, port and Liberty limits only (a pin-scoped limit is never
read) and a port can only be tightened below the design limit. On a pad-ring
(DIE) top that puts the std-cell margin on nets no std cell drives:

* a bond-pad PORT whose net reaches only IO-cell pins (the port-to-PAD net).
  Its "load" is the pad's own PAD-pin capacitance (2.9-3.1 pF on gf180) and its
  "slew" is the off-chip drive. R-0928-DRV-IC: such a net is judged by the IO
  Liberty T1 on the IO-cell pin (that row stays in the table under its own name)
  and by the declared or PDK set_load -- never by the std-cell margin;
* an IO-cell pin whose tool limit is the std-cell margin while its measured
  value is within the IO Liberty limit (DRV standard section 4: listed apart,
  not a gate, pending the owner). Above the IO Liberty limit it stays T1.

Every other row counts, including every IO-cell row at its Liberty limit (the
IO library's default_max_fanout 1 is fix-only). A row is taken out of the count
only on proof read from the netlist and the IO Liberty the sign-off STA linked;
anything unprovable counts, and an unreadable input raises `Unavailable`.

chip-AGNOSTIC: an IO cell is a cell the run's own PAD_LIBS Liberty marks
`pad_cell : true` -- the DRV judge's definition, read by its own reader.
"""
from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

OFFCHIP_PORT = "OFFCHIP_PORT_TO_PAD_NET"
IO_MARGIN = "IO_STD_CELL_MARGIN_DISCLOSURE"
COUNTED = "DRV"


class Unavailable(RuntimeError):
    """The netlist or an IO Liberty the classes need could not be read."""


def _name(token: str) -> str:
    token = token.strip()
    return token[1:].strip() if token.startswith("\\") else token


def netlist_nets(text: str) -> Dict[str, List[Tuple[str, str, str]]]:
    """{net: [(instance, master, pin)]} from a flat or hierarchical-name
    structural Verilog netlist (named port connections only)."""
    nets: Dict[str, List[Tuple[str, str, str]]] = {}
    body = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    body = re.sub(r"//[^\n]*", " ", body)
    for m in re.finditer(r"(?m)^\s*(\\?[A-Za-z_][\w$]*)\s+(\\\S+\s|[A-Za-z_][\w$\[\]]*)\s*"
                         r"\((.*?)\)\s*;", body, re.S):
        master, inst = m.group(1), _name(m.group(2))
        if master in ("module", "input", "output", "inout", "wire", "assign", "reg"):
            continue
        for c in re.finditer(r"\.(\w+)\s*\(\s*(\\\S+\s|[^()\s]+)\s*\)", m.group(3)):
            nets.setdefault(_name(c.group(2)), []).append((inst, master, c.group(1)))
    return nets


class Classifier:
    """Classes of (corner, kind, pin, limit, value) rows for one sign-off run."""

    def __init__(self, netlist: Path, pad_libs: Mapping[str, Sequence[Path]]):
        try:
            self._nets = netlist_nets(Path(netlist).read_text(errors="replace"))
        except OSError as exc:
            raise Unavailable(f"netlist {netlist} unreadable: {exc}") from exc
        if not self._nets:
            raise Unavailable(f"netlist {netlist} names no instance connection")
        self._inst_master = {inst: master for conns in self._nets.values()
                             for inst, master, _ in conns}
        from drv_signoff_judge import _liberty_limits
        self._libs: Dict[str, List[dict]] = {}
        for corner, paths in pad_libs.items():
            parsed = []
            for path in paths:
                try:
                    parsed.append(_liberty_limits(Path(path).read_text(errors="replace")))
                except (OSError, ValueError) as exc:
                    raise Unavailable(f"IO Liberty {path} unreadable: {exc}") from exc
            self._libs[corner] = parsed

    def _io(self, corner: str) -> List[dict]:
        if corner not in self._libs:
            raise Unavailable(f"no IO Liberty for corner {corner}")
        return self._libs[corner]

    def _pad(self, corner: str, master: Optional[str]) -> Optional[dict]:
        """The IO Liberty that declares `master` a pad cell (the judge's IO
        class: `pad_cell : true`), or None."""
        for lib in self._io(corner):
            if master in lib["pad_cells"]:
                return lib
        return None

    def io_limit(self, corner: str, master: str, pin: str, kind: str) -> Optional[float]:
        """The tightest IO Liberty limit over every linked library that
        declares `master` a pad cell (one per corner in a scene; all of them
        when a report spans corners)."""
        limits = []
        for lib in self._io(corner):
            if master not in lib["pad_cells"]:
                continue
            value = (lib["cells"].get(master, {}).get(pin) or {}).get(kind)
            value = value if value is not None else lib["defaults"].get(kind)
            if value is not None:
                limits.append(value)
        return min(limits) if limits else None

    def classify(self, corner: str, kind: str, pin: str,
                 limit: Optional[float], value: Optional[float]) -> str:
        if "/" not in pin:
            conns = self._nets.get(pin) or []
            if conns and all(self._pad(corner, master) is not None
                             for _, master, _ in conns):
                return OFFCHIP_PORT
            return COUNTED
        inst, _, lib_pin = pin.rpartition("/")
        master = self._inst_master.get(inst)
        if self._pad(corner, master) is None or limit is None or value is None:
            return COUNTED
        io = self.io_limit(corner, master, lib_pin, kind)
        tol = 1e-6
        if io is not None and limit < io - tol and value <= io + tol:
            return IO_MARGIN
        return COUNTED


def pad_libs_by_corner(pad_libs: object, corners: Iterable[str]) -> Dict[str, List[str]]:
    """LibreLane PAD_LIBS (a corner-glob map, or one list for every corner)."""
    out: Dict[str, List[str]] = {}
    for corner in corners:
        if isinstance(pad_libs, dict):
            hits = [p for glob, paths in pad_libs.items()
                    if fnmatch.fnmatch(corner, glob)
                    for p in ([paths] if isinstance(paths, str) else paths)]
        else:
            hits = [pad_libs] if isinstance(pad_libs, str) else list(pad_libs or [])
        if hits:
            out[corner] = hits
    return out
