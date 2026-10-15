#!/usr/bin/env python3
"""formal_structural_check.py — Step 5: discharge STRUCTURAL clock/reset
declarations with a deterministic structural check (R-0915-144).

WHY THIS EXISTS
===============
Step 5 turns every L8 clock/reset declaration into a formal obligation. Some of
those declarations state a TEMPORAL behaviour (the reset is active-high; every
register is zero one cycle after it is asserted) and a property can prove or
refute them. Others state a STRUCTURAL fact about the RTL:

    clocks.N.edge   = posedge          which edge every flop samples on
    resets.N.name   = rst              which port the reset branch tests
    resets.N.sync   = synchronous      whether any flop resets asynchronously

MEASURED on spm run22 (lane icspm5, 2026-09-23): no temporal property can
falsify these in the cycle-based model the flow proves in. There is no "between
edges" in which an async reset could show itself, and a property that "proves"
a port name can only be one that cannot fail. They were handed to the
`formal-verify` expert and stayed EXPERT_FALLBACK_OUTSTANDING.

A structural fact gets a structural answer. This program reads the design
through the flow's OWN yosys (`proc` + `flatten` + `opt_dff`, so a synchronous
reset is folded into `$sdff`/`$sdffe`/`$sdffce` with its SRST net and an
asynchronous one is `$adff`/`$adffe`/`$dffsr`/`$aldff`) and answers each claim
from the netlist:

    clock_edge    every flop clocked by the declared clock port has the declared
                  CLK_POLARITY, at least one is, and NO flop is clocked through
                  logic or by an internal net (its edge cannot be stated).
    reset_port    the declared port exists as a 1-bit input and its
                  combinational cone reaches some flop's reset control.
    reset_sync    "synchronous": the reset's WHOLE combinational cone reaches
                  flops only through a sync reset (SRST) and never an async
                  control (ARST / SET / CLR / AD / ALOAD), a clock pin, a latch
                  or a memory.  "asynchronous": the reverse. Review M4: a reset
                  ORed into an $adff ARST used to be invisible.
    state_covered every flop output bit in the design belongs to one of the
                  registers the generated "all internal state is zero"
                  properties observe. This is what makes "ALL" true of the
                  property set: a register the RTL scan missed is named here,
                  and the obligation stays open instead of being claimed.

Each claim ends PASS, REFUTED (the netlist contradicts the declaration, and the
finding names the cells), NOT_DISCHARGED (the netlist does not show the fact
either way: a reset yosys could not fold, a latch, a memory, a cell this
program cannot classify) or NOT_APPLICABLE (the declared signal is not a 1-bit
input — e.g. an OUTPUT reset `o_rst_n` — so it is not this rule's subject).
Only PASS discharges; the others keep the obligation open and SAY WHY.

The answer is recorded as INVOKED_BY_PROGRAM with the netlist and log as
evidence. It is never recorded as an AI answer (R-0915-125(a)).

chip-AGNOSTIC: generic yosys cell types and the design's own declared names.
There is no vendor, SKU, IC or PDK literal anywhere.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import _atomic_artefact as _aa  # noqa: E402  (vibe-ic#1082 — atomic writes)

VERSION = "3.0.0"

STRUCTURAL_RULES = ("clock_edge", "reset_port", "reset_sync", "reset_polarity",
                    "state_covered")

PASS = "PASS"
REFUTED = "REFUTED"
NOT_DISCHARGED = "NOT_DISCHARGED"
#: The claim's subject is not a 1-bit input of the design (an OUTPUT reset,
#: a clock that is not a port). Not a refutation, and not a discharge: the
#: obligation goes to the expert with the reason (review H5).
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Flop cell types and the control pins that make them asynchronous.
_SYNC_RESET_FFS = {"$sdff", "$sdffe", "$sdffce"}
_ASYNC_CONTROLS = {
    "$adff": ("ARST",), "$adffe": ("ARST",),
    "$dffsr": ("SET", "CLR"), "$dffsre": ("SET", "CLR"),
    "$aldff": ("AD", "ALOAD"), "$aldffe": ("AD", "ALOAD"),
}
_FF_TYPES = ({"$dff", "$dffe"} | _SYNC_RESET_FFS | set(_ASYNC_CONTROLS))
#: State the flop checks do not model. Present → a claim about "all state" or
#: about how a reset/clock reaches state is NOT_DISCHARGED, never PASS
#: (review H2): a latch or a memory is state no generated property observes.
_LATCH_TYPES = {"$dlatch", "$adlatch", "$dlatchsr", "$sr", "$ff"}
_MEMORY_PREFIX = ("$mem",)
#: Combinational word-level cells: the only cells a cone is traced THROUGH.
#: Anything that is neither this nor a known state cell is UNCLASSIFIED and
#: makes the claims below NOT_DISCHARGED — never PASS on what was not seen.
_COMB_TYPES = {
    "$not", "$pos", "$neg", "$and", "$or", "$xor", "$xnor",
    "$reduce_and", "$reduce_or", "$reduce_xor", "$reduce_xnor", "$reduce_bool",
    "$logic_not", "$logic_and", "$logic_or", "$shl", "$shr", "$sshl", "$sshr",
    "$shift", "$shiftx", "$lt", "$le", "$eq", "$ne", "$eqx", "$nex", "$ge",
    "$gt", "$add", "$sub", "$mul", "$div", "$mod", "$divfloor", "$modfloor",
    "$pow", "$mux", "$pmux", "$bmux", "$demux", "$bwmux", "$bweqx", "$lut",
    "$sop", "$concat", "$slice", "$tribuf", "$alu", "$lcu", "$macc", "$fa",
}
_ASYNC_PINS = ("ARST", "SET", "CLR", "AD", "ALOAD")
#: Cells that carry no signal at all. `flatten` leaves one `$scopeinfo` per
#: flattened instance (hierarchy metadata, no ports that drive anything); it is
#: neither state nor logic.
_METADATA_TYPES = {"$scopeinfo",
                   # formal / debug cells: they observe, they hold no state
                   "$assert", "$assume", "$cover", "$live", "$fair", "$check",
                   "$print"}

#: The netlist the checks read. `opt_dff` is what folds a synchronous reset mux
#: into `$sdff` with its SRST net (and absorbs an `if (!rst_n)` inverter into
#: SRST_POLARITY); without it a sync reset is an anonymous `$mux` select.
YOSYS_PASSES = ("proc; flatten; opt_expr; opt_clean; opt_dff; opt_clean")


def yosys_script(rtl_files: List[str], top: str, netlist: str,
                 simdef: str = "", passes: str = "") -> str:
    """The DUT read EXACTLY as the chip's synthesis reads it (R-0915-157):
    `_chip_synth_read.chip_read_lines` — `read_verilog -sv [-DSIMULATION ]` per
    file. No `-formal`, no proof define: the facts are about the chip."""
    import _chip_synth_read as _csr
    reads = "; ".join(_csr.chip_read_lines([Path(f) for f in rtl_files], simdef))
    return (f"{reads}; hierarchy -top {top}; "
            f"{passes or YOSYS_PASSES}; write_json {netlist}")


def _param_bit(value) -> Optional[int]:
    """A yosys JSON parameter as an int (binary string or int)."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s and set(s) <= {"0", "1"}:
        return int(s, 2)
    try:
        return int(s)
    except ValueError:
        return None


def _top_module(netlist: dict, top: str) -> Optional[Tuple[str, dict]]:
    mods = netlist.get("modules") or {}
    for name, mod in mods.items():
        attrs = mod.get("attributes") or {}
        if _param_bit(attrs.get("top", 0)) == 1:
            return name, mod
    if top in mods:
        return top, mods[top]
    return None


def _port_bit(mod: dict, name: str) -> Tuple[Optional[int], str]:
    """(bit id, "") for a 1-bit input port, else (None, why)."""
    port = (mod.get("ports") or {}).get(name)
    if port is None:
        return None, f"no port named {name!r} on the module"
    if port.get("direction") != "input":
        return None, f"port {name!r} is {port.get('direction')!r}, not an input"
    bits = port.get("bits") or []
    if len(bits) != 1 or not isinstance(bits[0], int):
        return None, f"port {name!r} is {len(bits)} bits wide, not 1"
    return bits[0], ""


def _kind(ctype: str) -> str:
    """ff | latch | memory | comb | metadata | unclassified."""
    if ctype in _FF_TYPES:
        return "ff"
    if ctype in _LATCH_TYPES:
        return "latch"
    if ctype.startswith(_MEMORY_PREFIX):
        return "memory"
    if ctype in _COMB_TYPES:
        return "comb"
    if ctype in _METADATA_TYPES:
        return "metadata"
    if ctype.startswith("$_") and not any(
            k in ctype for k in ("DFF", "DLATCH", "SR", "FF_", "ALDFF")):
        return "comb"
    return "unclassified"


def _bits(cell: dict, pin: str) -> List[int]:
    return [b for b in ((cell.get("connections") or {}).get(pin) or [])
            if isinstance(b, int)]


def _cells(mod: dict) -> List[Tuple[str, dict, str]]:
    return [(n, c, _kind(str(c.get("type"))))
            for n, c in (mod.get("cells") or {}).items()]


def _cone(mod: dict, start: int) -> set:
    """Every bit reachable from `start` through COMBINATIONAL cells only."""
    cone = {start}
    comb = [(c, c.get("port_directions") or {}) for _, c, k in _cells(mod)
            if k == "comb"]
    changed = True
    while changed:
        changed = False
        for c, dirs in comb:
            if c.get("type") == "$mux":
                # BIT-PRECISE for a mux: Y[i] depends on A[i], B[i], and on S
                # only when A[i] and B[i] are not the same constant. A reset
                # folded into a mux whose both arms are 0 at some bit (the
                # constant MSB of a shifted register) does not reach that bit.
                conn = c.get("connections") or {}
                a, b, sel, y = (conn.get("A") or [], conn.get("B") or [],
                                conn.get("S") or [], conn.get("Y") or [])
                s_in = bool(cone.intersection(x for x in sel if isinstance(x, int)))
                for i, yb in enumerate(y):
                    if not isinstance(yb, int) or yb in cone:
                        continue
                    ai = a[i] if i < len(a) else None
                    bi = b[i] if i < len(b) else None
                    same_const = (isinstance(ai, str) and ai == bi)
                    if ai in cone or bi in cone or (s_in and not same_const):
                        cone.add(yb)
                        changed = True
                continue
            ins = [b for p, d in dirs.items() if d == "input" for b in _bits(c, p)]
            if not cone.intersection(ins):
                continue
            for p, d in dirs.items():
                if d == "output":
                    for b in _bits(c, p):
                        if b not in cone:
                            cone.add(b)
                            changed = True
    return cone


def _drivers(mod: dict) -> Dict[int, dict]:
    """bit -> the cell that drives it."""
    out: Dict[int, dict] = {}
    for _, c, _k in _cells(mod):
        for p, d in (c.get("port_directions") or {}).items():
            if d == "output":
                for b in _bits(c, p):
                    out[b] = c
    return out


_INVERTERS = {"$not", "$logic_not", "$_NOT_"}
_BUFFERS = {"$pos", "$_BUF_"}


def _parity(drivers: Dict[int, dict], bit: int, port_bit: int) -> Optional[int]:
    """Inversions between `port_bit` and `bit` along a chain of 1-bit
    inverters/buffers, or None when the path is anything else (a gate, a mux,
    a second input) — then the polarity is not a netlist fact."""
    par, seen = 0, set()
    while bit != port_bit:
        if bit in seen:
            return None
        seen.add(bit)
        c = drivers.get(bit)
        if c is None:
            return None
        a = _bits(c, "A")
        if len(a) != 1:
            return None
        if c.get("type") in _INVERTERS:
            par ^= 1
        elif c.get("type") not in _BUFFERS:
            return None
        bit = a[0]
    return par


def _relevant_bits(mod: dict) -> set:
    """Bits that can influence a DESIGN OUTPUT. Formal and debug cells are
    sinks that do not count, so a flop that only feeds an `assert` (an
    `ifdef FORMAL` helper) is verification-only, not design state."""
    rel = {b for p in (mod.get("ports") or {}).values()
           if p.get("direction") == "output"
           for b in p.get("bits") or [] if isinstance(b, int)}
    live = [(c, c.get("port_directions") or {}) for _, c, k in _cells(mod)
            if k not in ("metadata",)]
    changed = True
    while changed:
        changed = False
        for c, dirs in live:
            outs = {b for p, d in dirs.items() if d == "output" for b in _bits(c, p)}
            if not outs & rel:
                continue
            for p, d in dirs.items():
                if d == "input":
                    for b in _bits(c, p):
                        if b not in rel:
                            rel.add(b)
                            changed = True
    return rel


def _reset_map(mod: dict, port_bit: int) -> Dict[str, list]:
    """How the port reaches state, by what it FORCES (review round 4, H3):
      sync    SRST reached — the reset forces the flop's value on the clock
      async   ARST reached — forced between clocks
      async_x SET/CLR/AD/ALOAD reached — asynchronous, polarity not a fact
      data    D reached (never SRST/ARST) — could be a reset to a non-constant
              value or plain data use: NOT classified
      clk     a clock pin
      opaque  a latch, memory or unclassified cell
    A reset that only reaches an ENABLE gates a load; it forces nothing and
    is not a reset of that flop."""
    cone = _cone(mod, port_bit)
    drivers = _drivers(mod)
    rel = _relevant_bits(mod)
    out = {k: [] for k in ("sync", "async", "async_x", "data", "clk", "opaque")}
    for n, c, k in _cells(mod):
        if k in ("comb", "metadata"):
            continue
        if k == "ff" and not rel.intersection(_bits(c, "Q")):
            # a verification-only helper is not the design (the SAME
            # relevance netlist_facts and the class gate use)
            continue
        dirs = c.get("port_directions") or {}
        hit = [p for p, d in dirs.items() if d == "input" and cone.intersection(_bits(c, p))]
        if not hit:
            continue
        params = c.get("parameters") or {}
        if k != "ff":
            out["opaque"].append({"cell": n, "type": c.get("type")})
        elif "SRST" in hit or "ARST" in hit:
            pin = "SRST" if "SRST" in hit else "ARST"
            par = _parity(drivers, _bits(c, pin)[0], port_bit) if _bits(c, pin) else None
            pol = _param_bit(params.get(f"{pin}_POLARITY"))
            eff = None if (par is None or pol is None) else (pol ^ par)
            if eff is None:
                # a GATED reset pin (e.g. `rst_n && clear`) is not a reset the
                # port forces — unclassified, never counted as forced (round 5)
                out["opaque"].append({"cell": n, "type": c.get("type")})
                continue
            out["sync" if pin == "SRST" else "async"].append({
                "cell": n, "type": c.get("type"),
                "polarity": (None if eff is None else
                             "active_high" if eff == 1 else "active_low"),
                "q": _bits(c, "Q")})
        elif any(p in _ASYNC_PINS for p in hit):
            out["async_x"].append({"cell": n, "type": c.get("type"), "polarity": None})
        elif "CLK" in hit:
            out["clk"].append({"cell": n, "type": c.get("type")})
        elif "D" in hit:
            out["data"].append({"cell": n, "type": c.get("type")})
        # EN only: a gated load, not a reset
    return out


def netlist_facts(netlist: dict, top: str) -> dict:
    """The design's STATE, read from the netlist (review round 4, M4): every
    flop that can influence an output, covered by public net names, each
    with its width in bits. `ok` is False — with the reason — when that
    cannot be stated: a latch, memory or unclassified cell, or a flop bit no
    public name holds wholly."""
    tm = _top_module(netlist, top)
    if tm is None:
        return {"ok": False, "why": f"top module {top!r} is not in the netlist"}
    _, mod = tm
    census = _state_census(mod, netlist)
    other = census["latch"] + census["memory"] + census["unclassified"]
    if other:
        return {"ok": False, "why": "state that is not a flop: " + ", ".join(other)}
    rel = _relevant_bits(mod)
    flops = [(n, c) for n, c, k in _cells(mod) if k == "ff"]
    state_bits = {b for _, c in flops for b in _bits(c, "Q") if b in rel}
    verif_only = sorted(n for n, c in flops if not rel.intersection(_bits(c, "Q")))
    names = []
    for nname, net in (mod.get("netnames") or {}).items():
        if _param_bit(net.get("hide_name", 0)) == 1:
            continue
        bits = [b for b in net.get("bits") or []]
        if bits and all(isinstance(b, int) and b in state_bits for b in bits):
            names.append((nname, bits))
    names.sort(key=lambda t: ("." in t[0], len(t[0]), t[0]))
    chosen, covered = [], set()
    for nname, bits in names:
        if set(bits) - covered:
            chosen.append({"name": nname, "width": len(bits)})
            covered |= set(bits)
    if state_bits - covered:
        return {"ok": False, "why": (f"{len(state_bits - covered)} flop bit(s) "
                                     f"are not wholly held by any public net")}
    if not chosen:
        return {"ok": False, "why": "the design has no state"}
    return {"ok": True, "why": "", "registers": chosen,
            "verification_only": verif_only}


def _state_census(mod: dict, netlist: dict) -> Dict[str, List[str]]:
    census: Dict[str, List[str]] = {"ff": [], "latch": [], "memory": [],
                                    "unclassified": []}
    for n, c, k in _cells(mod):
        if k in census:
            census[k].append(f"{n} ({c.get('type')})")
    for m in (mod.get("memories") or {}):
        census["memory"].append(f"{m} (memory)")
    return census


def sync_state_facts(netlist: dict, top: str, clock: str) -> dict:
    """Full raw Q census for the global-timestep SYNC constructor only.

    Async flops remain in the observation set: they are a counterexample to
    prove, never a reason to silently choose a smaller synchronous subset.
    Reset/value claims continue to use their own applicability conditions.
    """
    tm = _top_module(netlist, top) if isinstance(netlist, dict) else None
    if tm is None:
        return {"ok": False, "why": "the current raw netlist is unavailable"}
    _, mod = tm
    census = _state_census(mod, netlist)
    other = census["latch"] + census["memory"] + census["unclassified"]
    if other:
        return {"ok": False, "why": "unsupported storage: " + ", ".join(other)}
    cbit, why = _port_bit(mod, clock)
    if cbit is None:
        return {"ok": False, "why": "unknown clock: " + why}
    drivers = _drivers(mod)
    flops = [(n, c) for n, c, k in _cells(mod) if k == "ff"]
    identities, state = [], set()
    for name, cell in flops:
        q = _bits(cell, "Q")
        clk = _bits(cell, "CLK")
        edge = _param_bit((cell.get("parameters") or {}).get("CLK_POLARITY"))
        source = (_chain_source(drivers, clk[0], False)[0]
                  if len(clk) == 1 else None)
        if not q or edge is None or source != cbit:
            return {"ok": False, "why": f"unknown Q/clock/edge for {name}"}
        if state.intersection(q):
            return {"ok": False, "why": f"multiply driven state at {name}"}
        identities.append({"cell": name, "type": cell["type"], "q": q,
                           "clock_edge": "posedge" if edge else "negedge"})
        state.update(q)
    if not state:
        return {"ok": False, "why": "the design has no state"}
    if len({x["clock_edge"] for x in identities}) != 1:
        return {"ok": False, "why": "state samples on different clock edges"}
    names = []
    for name, net in (mod.get("netnames") or {}).items():
        bits = net.get("bits") or []
        # Exactly the dotted grammar the existing default runner binds.
        if (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name)
                and bits and all(isinstance(b, int) and b in state for b in bits)):
            names.append((name, bits))
    names.sort(key=lambda x: ("." in x[0], len(x[0]), x[0]))
    covered, registers = set(), []
    for name, bits in names:
        if set(bits) - covered:
            registers.append({"name": name, "width": len(bits), "q": bits})
            covered.update(bits)
    if covered != state:
        return {"ok": False, "why": f"missing exact alias for {len(state-covered)} Q bit(s)"}
    return {"ok": True, "why": "", "clock": clock,
            "clock_edge": identities[0]["clock_edge"],
            "identities": identities, "q_bits": sorted(state),
            "registers": registers}


def _transition_pair(mod: dict, reset_bit: int) -> Tuple[dict, List[dict]]:
    """Two native combinational next-state models sharing every Q/data bit.

    Only the selected top reset differs (0 versus 1). Original combinational
    cells and literal undef bits are retained. The coarse FF priority below
    follows Yosys simlib: sdffe resets before enable; sdffce enables before
    reset; asynchronous controls precede enable; CLR precedes SET.
    """
    import copy
    flops = [(n, c) for n, c, k in _cells(mod) if k == "ff"]
    qbits = {b for _, c in flops for b in _bits(c, "Q")}
    free = qbits | {b for p in (mod.get("ports") or {}).values()
                    if p.get("direction") == "input" for b in p["bits"]
                    if isinstance(b, int) and b != reset_bit}
    nxt, maps, shared, cells, ports = 2, [{}, {}], {}, {}, {}

    def allocate():
        nonlocal nxt
        b = nxt
        nxt += 1
        return b

    for bit in sorted(free):
        shared[bit] = allocate()
        ports[f"input_{bit}"] = {"direction": "input", "bits": [shared[bit]]}

    def remap(bit, arm):
        if not isinstance(bit, int):
            return bit
        if bit == reset_bit:
            return str(arm)
        if bit in shared:
            return shared[bit]
        if bit not in maps[arm]:
            maps[arm][bit] = allocate()
        return maps[arm][bit]

    def gate(kind, a, b=None, select=None):
        y = allocate()
        conn = {"A": [a], "Y": [y]}
        params = {"A_SIGNED": 0, "A_WIDTH": 1, "Y_WIDTH": 1}
        if b is not None:
            conn["B"] = [b]
            params.update(B_SIGNED=0, B_WIDTH=1)
        if select is not None:
            conn["S"] = [select]
            params = {"WIDTH": 1}
        cells[f"transition_{y}"] = {"type": kind, "parameters": params,
            "port_directions": {p: "output" if p == "Y" else "input" for p in conn},
            "connections": conn}
        return y

    def mux(a, b, s):
        return gate("$mux", a, b, s)

    drivers = _drivers(mod)
    cone = _cone(mod, reset_bit)
    for arm in (0, 1):
        for i, (_name, cell, kind) in enumerate(_cells(mod)):
            if kind == "comb":
                copied = copy.deepcopy(cell)
                copied["connections"] = {p: [remap(b, arm) for b in bits]
                                          for p, bits in cell["connections"].items()}
                cells[f"comb_{arm}_{i}"] = copied

    def both(a, b):
        return gate("$and", a, b)

    def either(a, b):
        return gate("$or", a, b)

    def inverse(a):
        return gate("$not", a)

    role_cache, role_provenance = {}, []

    def input_bit(cell, pin, index):
        values = cell["connections"].get(pin) or []
        if index < len(values):
            return values[index]
        signed = _param_bit(cell.get("parameters", {}).get(pin + "_SIGNED"))
        return values[-1] if signed and values else "0"

    def d_roles(bit, arm, q, seen=frozenset()):
        """Conditional source roles, without solving for convenient data values.

        The three predicates mean force-zero, force-one, and retain-Q. A mux
        keeps its original branch selection. A Boolean mask uses its original
        reset-dependent operand as the control: AND's zero and OR's one are
        dominating values, independent of the other (unconstrained) data
        operand. Two reset-dependent operands are composed from their roles;
        neither arbitrary SAT cubes nor a data value chosen by a witness become
        a new control. Every resulting force/hold implication is checked by
        native SAT against the original next-state value below.
        """
        key = (bit, arm, q)
        if key in role_cache:
            return role_cache[key]
        empty = ("0", "0", "0")
        if bit in ("0", "1"):
            return ("1", "0", "0") if bit == "0" else ("0", "1", "0")
        if bit == q:
            return ("0", "0", "1")
        if not isinstance(bit, int) or bit in qbits or bit in seen:
            return empty
        cell = drivers.get(bit) or {}
        conn, kind = cell.get("connections") or {}, cell.get("type")
        try:
            i = conn["Y"].index(bit)
            seen = seen | {bit}
            if kind in {"$pos", "$not"}:
                zero, one, held = d_roles(input_bit(cell, "A", i), arm, q, seen)
                answer = (one, zero, "0") if kind == "$not" else (zero, one, held)
            elif kind in {"$mux", "$bwmux"}:
                select = conn["S"][i if kind == "$bwmux" else 0]
                a = d_roles(input_bit(cell, "A", i), arm, q, seen)
                b = d_roles(input_bit(cell, "B", i), arm, q, seen)
                answer = tuple(mux(x, y, remap(select, arm)) for x, y in zip(a, b))
                role_provenance.append({"output_bit": bit, "kind": kind,
                    "control_bits": [select], "branch_roles": "original A/B selection"})
            elif kind in {"$and", "$or", "$xor", "$xnor"}:
                left, right = input_bit(cell, "A", i), input_bit(cell, "B", i)
                a, b = d_roles(left, arm, q, seen), d_roles(right, arm, q, seen)
                controls = [v for v in (left, right) if isinstance(v, int) and v in cone]
                if len(controls) == 1 and kind in {"$and", "$or"}:
                    control = controls[0]
                    data = b if control == left else a
                    select = remap(control, arm)
                    # Annihilator versus transparent branch, not a cube over
                    # the other operand. The data branch stays free/shared.
                    answer = ((mux("1", data[0], select),
                               mux("0", data[1], select),
                               mux("0", data[2], select)) if kind == "$and" else
                              (mux(data[0], "0", select),
                               mux(data[1], "1", select),
                               mux(data[2], "0", select)))
                    role_provenance.append({"output_bit": bit, "kind": kind,
                        "control_bits": [control],
                        "branch_roles": "dominating Boolean mask / unchanged data"})
                elif kind == "$and":
                    answer = (either(a[0], b[0]), both(a[1], b[1]),
                              either(both(a[2], b[1]), both(b[2], a[1])))
                elif kind == "$or":
                    answer = (both(a[0], b[0]), either(a[1], b[1]),
                              either(both(a[2], b[0]), both(b[2], a[0])))
                else:
                    zero = either(both(a[0], b[0]), both(a[1], b[1]))
                    one = either(both(a[0], b[1]), both(a[1], b[0]))
                    held = either(both(a[2], b[0]), both(b[2], a[0]))
                    answer = ((one, zero, "0") if kind == "$xnor" else (zero, one, held))
            elif kind in {"$slice", "$concat"}:
                if kind == "$slice":
                    offset = cell.get("parameters", {}).get("OFFSET", 0)
                    offset = int(offset, 2) if isinstance(offset, str) else int(offset)
                    source = input_bit(cell, "A", i + offset)
                else:
                    width = len(conn.get("A") or [])
                    source = input_bit(cell, "A", i) if i < width else input_bit(cell, "B", i - width)
                answer = d_roles(source, arm, q, seen)
            else:
                answer = empty
        except (IndexError, KeyError, ValueError):
            answer = empty
        role_cache[key] = answer
        return answer

    rows = []
    for name, cell in flops:
        kind = cell["type"]
        conn, params = cell.get("connections") or {}, cell.get("parameters") or {}
        hit = [p for p, bits in conn.items() if p != "Q" and cone.intersection(
            b for b in bits if isinstance(b, int))]
        clk = _bits(cell, "CLK")
        source = _chain_source(drivers, clk[0], False)[0] if len(clk) == 1 else None
        input_clocks = {b for p in (mod.get("ports") or {}).values()
                        if p.get("direction") == "input" and len(p["bits"]) == 1
                        for b in p["bits"]}
        for i, q in enumerate(conn.get("Q") or []):
            row = {"cell": name, "type": kind, "q": q, "bit_index": i,
                   "clock": conn.get("CLK"), "clock_polarity": params.get("CLK_POLARITY"),
                   "controls": {p: conn[p] for p in ("D", "EN", "SRST", "ARST", "SET", "CLR") if p in conn},
                   "parameters": params, "reset_reaches": hit}
            rows.append(row)
            if (kind not in {"$dff", "$dffe", "$sdff", "$sdffe", "$sdffce", "$adff", "$adffe", "$dffsr", "$dffsre"}
                    or not isinstance(q, int) or i >= len(conn.get("D") or [])
                    or source not in input_clocks or "CLK" in hit
                    or _param_bit(params.get("CLK_POLARITY")) is None):
                row["unsupported"] = "unmodelled storage, clock or D/Q identity"
                continue

            def active(pin, arm, index=0):
                values = conn.get(pin) or []
                polarity = _param_bit(params.get(pin + "_POLARITY"))
                if index >= len(values) or polarity is None:
                    raise ValueError("unknown " + pin + " polarity/width")
                v = remap(values[index], arm)
                return v if polarity else gate("$not", v)

            def reset_value(pin):
                value = params.get(pin + "_VALUE")
                if isinstance(value, int):
                    return str((value >> i) & 1)
                if not isinstance(value, str) or len(value) <= i or value[-1-i] not in "01xz":
                    raise ValueError("unknown " + pin + " value/width")
                return value[-1-i]

            outputs = []
            role_provenance = []
            try:
                for arm in (0, 1):
                    d, held = remap(conn["D"][i], arm), shared[q]
                    zero, one, hold = d_roles(conn["D"][i], arm, q)

                    def apply_branch(value, select):
                        nonlocal d, zero, one, hold
                        d = mux(d, value, select)
                        zero = mux(zero, "1" if value == "0" else "0", select)
                        one = mux(one, "1" if value == "1" else "0", select)
                        hold = mux(hold, "0", select)

                    def apply_enable(select):
                        nonlocal d, zero, one, hold
                        d = mux(held, d, select)
                        zero, one = mux("0", zero, select), mux("0", one, select)
                        hold = mux("1", hold, select)

                    if "EN" in conn and kind != "$sdffce":
                        en = active("EN", arm)
                        apply_enable(en)
                    if "SRST" in conn or "ARST" in conn:
                        pin = "SRST" if "SRST" in conn else "ARST"
                        rst = active(pin, arm)
                        apply_branch(reset_value(pin), rst)
                    if kind == "$sdffce":
                        en = active("EN", arm)
                        apply_enable(en)
                    if kind in {"$dffsr", "$dffsre"}:
                        st, cl = active("SET", arm, i), active("CLR", arm, i)
                        apply_branch("1", st)
                        apply_branch("0", cl)
                    force = either(zero, one)
                    # Force takes precedence over an algebraically coincident
                    # hold predicate; the roles must be disjoint and exhaustive.
                    hold = both(hold, inverse(force))
                    data = inverse(either(force, hold))
                    outputs.append((d, force, zero, one, hold, data))
            except ValueError as exc:
                row["unsupported"] = str(exc)
                continue
            n0, f0 = outputs[0][:2]
            n1, f1 = outputs[1][:2]
            names = {"n0": n0, "n1": n1,
                "retain0": gate("$eq", n0, shared[q]),
                "retain1": gate("$eq", n1, shared[q]),
                "equal": gate("$eq", n0, n1),
                "high_violation": gate("$and", f0, gate("$not", f1)),
                "low_violation": gate("$and", f1, gate("$not", f0))}
            for arm, (value, force, zero, one, hold, data) in enumerate(outputs):
                roles = {"force_zero": zero, "force_one": one,
                         "retain": hold, "data": data}
                for role, guard in roles.items():
                    names[f"{role}_guard{arm}"] = guard
                    if role != "data":
                        expected = (gate("$eq", value, shared[q]) if role == "retain" else
                                    gate("$eq", value, "1" if role == "force_one" else "0"))
                        names[f"{role}_valid{arm}"] = either(inverse(guard), expected)
                names[f"roles_disjoint{arm}"] = inverse(either(
                    both(zero, one), either(both(force, hold), both(either(force, hold), data))))
                names[f"roles_cover{arm}"] = either(either(zero, one), either(hold, data))
            row["query_ports"] = {k: f"q{len(rows)-1}_{k}" for k in names}
            for key, bit in names.items():
                ports[row["query_ports"][key]] = {"direction": "output", "bits": [bit]}
            row["priority"] = ("EN > SRST > D" if kind == "$sdffce" else
                               "CLR > SET > EN > D" if kind in {"$dffsr", "$dffsre"} else
                               "ARST > EN > D" if "ARST" in conn else
                               "SRST > EN > D" if "SRST" in conn else "EN > D")
            row["role_provenance"] = list({json.dumps(p, sort_keys=True): p
                                            for p in role_provenance}.values())
    module = {"attributes": {"top": 1}, "ports": ports, "cells": cells,
              "netnames": {n: {"hide_name": 0, "bits": p["bits"], "attributes": {}}
                           for n, p in ports.items()}}
    return {"modules": {"transition_pair": module}}, rows


def reset_control_mapping(netlist: dict, raw: Optional[dict], top: str,
                          signal: str, work_dir: Path,
                          container: Optional[str] = None) -> dict:
    """Classify all current identities using native paired-cofactor queries.

    This is the reset mapping of this existing owner, not a new flow gate.
    UNSUPPORTED/UNKNOWN identities remain named and prevent full polarity PASS.
    SAT countermodels classify functions; they are not a DUT formal FAIL.
    """
    result = {"signal": signal, "rows": [], "unresolved": [], "complete": False,
              "shared_current_q_and_data": True}
    tm = _top_module(netlist, top)
    if tm is None:
        return dict(result, unresolved=["missing current netlist"])
    _, mod = tm
    bit, why = _port_bit(mod, signal)
    if bit is None:
        return dict(result, unresolved=[why])
    census = _state_census(mod, netlist)
    unsupported = census["latch"] + census["memory"] + census["unclassified"]
    if unsupported:
        return dict(result, unresolved=unsupported)
    model, rows = _transition_pair(mod, bit)
    result["rows"] = rows
    # Map every raw Q bit into current Q using an exact common alias/index.
    raw_tm = _top_module(raw, top) if isinstance(raw, dict) else None
    identities = []
    current_q = {r["q"] for r in rows}
    if raw_tm is None:
        result["unresolved"].append("missing raw state census")
    else:
        raw_mod = raw_tm[1]
        raw_census = _state_census(raw_mod, raw)
        result["unresolved"] += raw_census["latch"] + raw_census["memory"] + raw_census["unclassified"]
        aliases = {}
        raw_drivers = _drivers(raw_mod)
        raw_q = {q for _, c, k in _cells(raw_mod) if k == "ff" for q in _bits(c, "Q")}

        def literal_undef(bit, seen=frozenset()):
            if isinstance(bit, str):
                return bit.lower() in {"x", "z"}
            if bit in seen or bit in raw_q:
                return False
            driver = raw_drivers.get(bit)
            if driver is None:
                return False
            return any(literal_undef(b, seen | {bit})
                       for p, bits in (driver.get("connections") or {}).items()
                       if (driver.get("port_directions") or {}).get(p) == "input"
                       for b in bits)

        undef_q = set()
        for _name, cell, kind in _cells(raw_mod):
            if kind == "ff":
                bad_value = any(isinstance(v, str) and any(x in v.lower() for x in "xz")
                                for p, v in (cell.get("parameters") or {}).items()
                                if p.endswith("_VALUE"))
                if bad_value or any(literal_undef(b) for p, bits in cell["connections"].items()
                                    if p != "Q" for b in bits):
                    undef_q.update(_bits(cell, "Q"))
        for name, net in (raw_mod.get("netnames") or {}).items():
            other = (mod.get("netnames") or {}).get(name, {}).get("bits") or []
            for i, qb in enumerate(net.get("bits") or []):
                if isinstance(qb, int) and i < len(other) and other[i] in current_q:
                    aliases.setdefault(qb, set()).add(other[i])
        for name, cell, kind in _cells(raw_mod):
            if kind == "ff":
                for i, qb in enumerate(cell.get("connections", {}).get("Q") or []):
                    mapped = aliases.get(qb, set())
                    identities.append({"cell": name, "q_index": i,
                                       "raw_q": qb, "current_q": sorted(mapped),
                                       "literal_undef_in_transition": qb in undef_q})
                    if qb in undef_q:
                        for row in rows:
                            if row["q"] in mapped:
                                row["unsupported"] = "literal-X/undef retained from raw next-state transition"
                    if len(mapped) != 1:
                        result["unresolved"].append(f"{name}.Q[{i}]: missing/ambiguous current Q identity")
    result["raw_identities"] = identities
    work_dir.mkdir(parents=True, exist_ok=True)
    model_path = work_dir / "transition_pair.json"
    _aa.write_json(model_path, model)
    script = [f"read_json {model_path.resolve()}", "hierarchy -top transition_pair",
              "log VIBEIC_TRANSITION_QUERY domain",
              "sat -timeout 10 -enable_undef -set-def-inputs -show-inputs"]
    specs = {}
    for i, row in enumerate(rows):
        if row.get("unsupported"):
            continue
        ports = row["query_ports"]
        queries = {"undef0": ("n0", None), "undef1": ("n1", None),
            "zero0": ("n0", "0"), "one0": ("n0", "1"),
            "zero1": ("n1", "0"), "one1": ("n1", "1"),
            "retain0": ("retain0", "1"), "retain1": ("retain1", "1"),
            "equal": ("equal", "1"), "high": ("high_violation", "0"),
            "low": ("low_violation", "0")}
        for arm in (0, 1):
            for role in ("force_zero", "force_one", "retain", "data"):
                queries[f"{role}_reachable{arm}"] = (f"{role}_guard{arm}", "0")
                if role != "data":
                    queries[f"{role}_valid{arm}"] = (f"{role}_valid{arm}", "1")
            for proof in ("roles_disjoint", "roles_cover"):
                queries[f"{proof}{arm}"] = (f"{proof}{arm}", "1")
        for key, (port, value) in queries.items():
            ident = f"{i}_{key}"
            specs[ident] = (i, key)
            script.append("log VIBEIC_TRANSITION_QUERY " + ident)
            option = f"-set-any-undef {ports[port]}" if value is None else f"-prove {ports[port]} {value}"
            script.append(f"sat -timeout 10 -enable_undef -set-def-inputs {option} -show-inputs -show-outputs")
    ys = work_dir / "transition_pair.ys"
    _aa.write_text(ys, "\n".join(script) + "\n")
    inner = f"yosys -Q -T -s {ys.resolve()}"
    if container:
        import _container_exec as ce
        argv = ce.exec_argv(container, inner, tag="formal_structural_check")
    else:
        argv = ["yosys", "-Q", "-T", "-s", str(ys.resolve())]
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        rc, log = process.returncode, process.stdout + process.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, log = 127, str(exc)
    _aa.write_text(work_dir / "transition_pair.log", log)
    domain = log.split("VIBEIC_TRANSITION_QUERY domain", 1)[-1].split(
        "VIBEIC_TRANSITION_QUERY 0_", 1)[0]
    domain_reachable = "SAT solving finished - model found:" in domain
    if not domain_reachable:
        result["unresolved"].append("paired current-Q/data domain has no measured reachable witness")
    sections = re.split(r"(?m)^VIBEIC_TRANSITION_QUERY (\d+_\w+)\s*$", log)
    answers = {}
    for ident, body in zip(sections[1::2], sections[2::2]):
        if "SAT proof finished - no model found: SUCCESS!" in body:
            answers[ident] = "PROVED"
        elif "SAT proof finished - model found: FAIL!" in body:
            answers[ident] = "REFUTED"
        elif "SAT solving finished - no model found." in body:
            answers[ident] = "NO_UNDEF"
        elif "SAT solving finished - model found:" in body:
            answers[ident] = "HAS_UNDEF"
        else:
            answers[ident] = "UNKNOWN"
    for i, row in enumerate(rows):
        a = {key: answers.get(f"{i}_{key}", "UNKNOWN") for j, key in specs.values() if j == i}
        row["native_queries"] = a
        row["classification"] = "UNSUPPORTED"
        row["conditional_roles"] = [{"reset_value": arm,
            "role": role.upper(), "guard_port": row.get("query_ports", {}).get(f"{role}_guard{arm}"),
            "reachable": a.get(f"{role}_reachable{arm}") == "REFUTED",
            "domain_query": a.get(f"{role}_reachable{arm}", "UNKNOWN"),
            "implication_query": (a.get(f"{role}_valid{arm}", "UNKNOWN") if role != "data" else
                                  "RESIDUAL_DATA_NOT_RESET_FORCE")}
            for arm in (0, 1) for role in ("force_zero", "force_one", "retain", "data")]
        role_proofs = [a.get(f"{role}_valid{arm}") for arm in (0, 1)
                       for role in ("force_zero", "force_one", "retain")]
        role_proofs += [a.get(f"{proof}{arm}") for arm in (0, 1)
                        for proof in ("roles_disjoint", "roles_cover")]
        if row.get("unsupported") or rc or not a or "UNKNOWN" in a.values():
            row["reason"] = row.get("unsupported") or f"native transition query unavailable (rc={rc})"
        elif any(a[k] != "NO_UNDEF" for k in ("undef0", "undef1")):
            row["reason"] = "literal-X or undefined next-state cofactor"
        elif any(proof != "PROVED" for proof in role_proofs):
            row["reason"] = "conditional role implication, disjointness or full-domain partition not proved"
        elif a["equal"] == "PROVED":
            row["classification"] = ("LOCAL_CLEAR" if not row["reset_reaches"] and
                any(p in row["controls"] for p in ("SRST", "ARST", "SET", "CLR")) else "NO_DEPENDENCE")
        elif set(row["reset_reaches"]) <= {"EN"}:
            row["classification"] = "RETENTION_CONTROL"
        elif (a["zero0"] == "PROVED" or a["one0"] == "PROVED") and (
                a["zero1"] == "PROVED" or a["one1"] == "PROVED"):
            row["classification"] = "DATA_VALUE"
        else:
            high = a["high"] == "PROVED" and a["low"] == "REFUTED"
            low = a["low"] == "PROVED" and a["high"] == "REFUTED"
            constant_high = a["zero1"] == "PROVED" or a["one1"] == "PROVED"
            constant_low = a["zero0"] == "PROVED" or a["one0"] == "PROVED"
            if high or low or constant_high != constant_low:
                row["classification"] = "RESET_FORCE"
                row["polarity"] = "active_high" if high or (not low and constant_high) else "active_low"
                row["total_force"] = constant_high if row["polarity"] == "active_high" else constant_low
                row["timing"] = "asynchronous" if any(p in row["reset_reaches"] for p in _ASYNC_PINS) else "synchronous"
            else:
                row["classification"] = "UNRESOLVED_DATA_DEPENDENCE"
                row["reason"] = "paired dependence has no closed reset-force sense/priority"
        if row["classification"] in {"UNSUPPORTED", "UNRESOLVED_DATA_DEPENDENCE"}:
            result["unresolved"].append(f"{row['cell']}.Q[{row['bit_index']}]: {row.get('reason')}")
    result.update(complete=not result["unresolved"], native_exit_code=rc,
                  paired_domain_reachable=domain_reachable,
                  model_sha256=file_sha256(model_path), script_sha256=file_sha256(ys),
                  log_sha256=file_sha256(work_dir / "transition_pair.log"),
                  work_dir=str(work_dir), query_count=len(specs))
    return result


def check_claim(netlist: dict, top: str, claim: dict,
                reset_mapping: Optional[dict] = None) -> dict:
    """Answer ONE structural claim from a yosys JSON netlist. Pure.

    Only what was SEEN passes. Every answer is read from the netlist, never
    from RTL text: which flops a reset forces and with what polarity (the
    SRST/ARST cell parameters, through a chain of inverters only), which flops
    are design state (they can influence an output), which clock pins the
    clock reaches. A cone this program cannot classify is NOT_DISCHARGED —
    never PASS, and never REFUTED (review rounds 2-4)."""
    rule = claim.get("rule")
    signal = str(claim.get("signal") or "")
    value = str(claim.get("value") or "")
    out = {"rule": rule, "signal": signal, "value": value}
    tm = _top_module(netlist, top)
    if tm is None:
        return dict(out, verdict=NOT_DISCHARGED,
                    finding=f"top module {top!r} is not in the netlist")
    mod_name, mod = tm
    out["module"] = mod_name
    census = _state_census(mod, netlist)
    other_state = census["latch"] + census["memory"] + census["unclassified"]
    cells = _cells(mod)
    flops = [(n, c) for n, c, k in cells if k == "ff"]
    if rule == "state_covered":
        want = [n for n in value.split(",") if n]
        facts = netlist_facts(netlist, top)
        if not facts["ok"]:
            return dict(out, verdict=NOT_DISCHARGED, finding=facts["why"])
        have = sorted(r["name"] for r in facts["registers"])
        out["flops"] = len(flops)
        out["verification_only"] = facts["verification_only"]
        if sorted(want) != have:
            return dict(out, verdict=NOT_DISCHARGED, observed=sorted(want),
                        netlist_state=have,
                        finding=(f"the observed registers {sorted(want)} are not "
                                 f"the netlist's design state {have}"))
        return dict(out, verdict=PASS,
                    finding=(f"the observed registers are exactly the netlist's "
                             f"design state {have} (all {len(flops)} flop(s); "
                             f"verification-only: {facts['verification_only']}); "
                             f"no latch, memory or unclassified cell"))
    bit, why = _port_bit(mod, signal)
    if bit is None:
        return dict(out, verdict=NOT_APPLICABLE,
                    finding=f"{why}; not the subject of rule {rule}")
    input_bits = {b for p in (mod.get("ports") or {}).values()
                  if p.get("direction") == "input"
                  for b in p.get("bits") or [] if isinstance(b, int)}
    cone = _cone(mod, bit)
    if rule == "clock_edge":
        want = {"posedge": 1, "negedge": 0}.get(value)
        if want is None:
            return dict(out, verdict=NOT_DISCHARGED,
                        finding=f"edge {value!r} has no structural rule")
        direct, via_logic, internal = [], [], []
        for n, c in flops:
            clk = _bits(c, "CLK")
            if bit in clk:
                direct.append((n, c))
            elif cone.intersection(clk):
                via_logic.append(n)
            elif not input_bits.intersection(clk):
                internal.append(n)
        # a memory port or a LATCH whose clock/enable the clock reaches
        other_clk = [n for n, c, k in cells if k in ("memory", "unclassified", "latch")
                     and (cone.intersection(_bits(c, "CLK"))
                          or cone.intersection(_bits(c, "EN")))]
        out["flops_clocked"] = len(direct)
        wrong = [n for n, c in direct
                 if _param_bit((c.get("parameters") or {})
                               .get("CLK_POLARITY")) != want]
        if wrong:
            return dict(out, verdict=REFUTED, cells=sorted(wrong),
                        finding=(f"{len(wrong)} of {len(direct)} flop(s) "
                                 f"clocked by {signal!r} do not sample on "
                                 f"{value}"))
        odd = sorted(via_logic + internal + other_clk)
        if odd:
            return dict(out, verdict=NOT_DISCHARGED, cells=odd,
                        finding=("state clocked through logic, by an internal "
                                 "net, or a latch/memory the clock reaches, "
                                 "whose edge this check cannot state: "
                                 + ", ".join(odd)))
        if not direct:
            return dict(out, verdict=NOT_DISCHARGED,
                        finding=f"no flop is clocked by port {signal!r}")
        return dict(out, verdict=PASS,
                    finding=(f"all {len(direct)} flop(s) clocked by "
                             f"{signal!r} have CLK_POLARITY={want} ({value}); "
                             f"no state is clocked through logic or an "
                             f"internal net"))
    rm = _reset_map(mod, bit)
    if reset_mapping is not None and rule in {"reset_port", "reset_sync", "reset_polarity"}:
        rows = reset_mapping.get("rows") or []
        force = [r for r in rows if r.get("classification") == "RESET_FORCE"]
        unresolved = reset_mapping.get("unresolved") or []
        out["reset_control_mapping"] = reset_mapping
        wrong = [r for r in force if rule == "reset_polarity" and r.get("polarity") != value]
        if wrong:
            return dict(out, verdict=REFUTED, cells=sorted({r["cell"] for r in wrong}),
                        finding="native paired next-state force sense contradicts declared polarity")
        if rule == "reset_sync":
            opposite = "asynchronous" if value == "synchronous" else "synchronous"
            bad = [r for r in force if r.get("timing") == opposite]
            # Preserve the existing structural timing counterevidence even
            # when a literal-X value prevents full function classification.
            old_bad = (rm["async"] + rm["async_x"] if value == "synchronous" else
                       rm["sync"] if value == "asynchronous" else [])
            if bad or old_bad:
                return dict(out, verdict=REFUTED,
                            cells=sorted({r["cell"] for r in bad + old_bad}),
                            finding="actual reset timing contradicts the declared timing")
        if rule == "reset_port" and force:
            return dict(out, verdict=PASS,
                        finding=f"native paired model identifies {len(force)} reset-forced Q bit(s)")
        if unresolved or not force or (rule == "reset_polarity" and value not in {"active_high", "active_low"}) or (
                rule == "reset_sync" and value not in {"synchronous", "asynchronous"}):
            return dict(out, verdict=NOT_DISCHARGED,
                        finding="reset-force identity/sense/priority is not fully closed: " +
                        ("; ".join(unresolved) or "no reset-forced state or unsupported claim value"))
        return dict(out, verdict=PASS,
                    finding=f"complete current-Q paired reset mapping closes {rule}={value} for every reset-force group")
    forced = rm["sync"] + rm["async"] + rm["async_x"]
    out["sync_reset_flops"] = len(rm["sync"])
    out["async_reset_flops"] = len(rm["async"]) + len(rm["async_x"])
    unclass = rm["clk"] + rm["opaque"]
    if rule == "reset_port":
        if forced:
            return dict(out, verdict=PASS,
                        finding=(f"port {signal!r} forces the reset of "
                                 f"{len(forced)} flop(s)"))
        return dict(out, verdict=NOT_DISCHARGED,
                    finding=(f"port {signal!r} exists but forces no flop's "
                             f"reset in the netlist"))
    if rule == "reset_polarity":
        if value not in ("active_high", "active_low"):
            return dict(out, verdict=NOT_DISCHARGED,
                        finding=f"polarity {value!r} has no structural rule")
        wrong = sorted(f["cell"] for f in forced
                       if f.get("polarity") not in (None, value))
        if wrong:
            return dict(out, verdict=REFUTED, cells=wrong,
                        finding=(f"{len(wrong)} flop(s) are reset by {signal!r} "
                                 f"{'active-low' if value == 'active_high' else 'active-high'}"
                                 f", against the declared {value}"))
        unknown = sorted(f["cell"] for f in forced if f.get("polarity") is None)
        unknown += sorted(f["cell"] for f in rm["data"])
        if unknown or unclass:
            return dict(out, verdict=NOT_DISCHARGED,
                        cells=unknown + [u["cell"] for u in unclass],
                        finding=("the reset reaches state whose polarity is not "
                                 "a netlist fact (through a gate, a set/clear "
                                 "pin, a clock pin, a latch or a memory)"))
        if not forced:
            return dict(out, verdict=NOT_DISCHARGED,
                        finding=f"{signal!r} forces no flop's reset")
        return dict(out, verdict=PASS,
                    finding=(f"every one of the {len(forced)} flop(s) {signal!r} "
                             f"resets is reset {value} (SRST/ARST polarity)"))
    if rule == "reset_sync":
        if value == "synchronous":
            if rm["async"] or rm["async_x"]:
                cells_ = sorted(f["cell"] for f in rm["async"] + rm["async_x"])
                return dict(out, verdict=REFUTED, cells=cells_,
                            finding=(f"{len(cells_)} flop(s) are reset "
                                     f"ASYNCHRONOUSLY by {signal!r} (directly "
                                     f"or through logic)"))
            if unclass:
                return dict(out, verdict=NOT_DISCHARGED,
                            cells=sorted(u["cell"] for u in unclass),
                            finding=(f"{signal!r} reaches a clock pin or state "
                                     f"this check does not model"))
            if not rm["sync"]:
                return dict(out, verdict=NOT_DISCHARGED,
                            finding=(f"{signal!r} forces no flop through a "
                                     f"synchronous reset in the netlist"))
            return dict(out, verdict=PASS,
                        finding=(f"{signal!r} resets {len(rm['sync'])} flop(s), "
                                 f"all synchronously; its whole combinational "
                                 f"cone reaches no async control, clock pin, "
                                 f"latch or memory"))
        if value == "asynchronous":
            # REFUTED only on a FORCED synchronous reset (SRST). A reset that
            # reaches a D pin is not classified (NOT_DISCHARGED); one that only
            # gates an ENABLE is not a reset at all (review round 4, H3).
            if rm["sync"]:
                cells_ = sorted(f["cell"] for f in rm["sync"])
                return dict(out, verdict=REFUTED, cells=cells_,
                            finding=(f"{len(cells_)} flop(s) are reset "
                                     f"SYNCHRONOUSLY by {signal!r} (SRST)"))
            if unclass or rm["data"]:
                return dict(out, verdict=NOT_DISCHARGED,
                            cells=sorted(u["cell"] for u in unclass + rm["data"]),
                            finding=(f"{signal!r} reaches a D input, a clock pin "
                                     f"or unmodelled state; whether that is a "
                                     f"reset is not classified"))
            if not (rm["async"] or rm["async_x"]):
                return dict(out, verdict=NOT_DISCHARGED,
                            finding=(f"{signal!r} controls no flop through an "
                                     f"asynchronous reset in the netlist"))
            return dict(out, verdict=PASS,
                        finding=(f"{signal!r} resets "
                                 f"{len(rm['async']) + len(rm['async_x'])} "
                                 f"flop(s), all asynchronously; it forces no "
                                 f"synchronous reset"))
        return dict(out, verdict=NOT_DISCHARGED,
                    finding=f"sync value {value!r} has no structural rule")
    return dict(out, verdict=NOT_DISCHARGED,
                finding=f"rule {rule!r} is not a structural rule")


# ── R-0915-155 — THE APPLICABILITY GATE ─────────────────────────────────────
#
# Five review rounds each found a new design class the program answered
# wrongly (a descended wrapper, a synchronizer, a gated clear, a folded
# register, an array element, a second clock). The ruling: the program closes
# ONLY the class it can prove soundly, and says NOT_DISCHARGED — the flow's
# designed expert fallback — for everything else. Never PASS, never REFUTED,
# never ERROR outside the class. The gate is evaluated on the proof's OWN
# netlist(s) BEFORE any closure; every failing precondition is named.
#
# THE CLASS:
#   C1  one clock domain: every flop's CLK is the declared clock port through
#       a pure BUFFER chain, and every flop samples on the same edge;
#   C2  every reset pin of every flop (SRST/ARST/SET/CLR/ALOAD) is driven by a
#       declared reset port through a pure inverter/buffer chain — so no flop
#       Q (a synchronizer) and no gate (a qualified clear) is in any reset path;
#   C3  a declared reset reaches NOTHING but those reset pins (no D, no EN, no
#       clock) — the only use of a reset is to reset;
#   C4  no latch, memory, array, $anyinit or unclassified cell;
#   C5  no register the structural read folds away that the proof keeps: the
#       design-state census of a read WITHOUT opt_dff (as `prep -noff` keeps
#       flops) must equal the census the checks use;
#   C6  every observed register name is a plain identifier (no `[ ]`, no
#       hierarchy dot) — an observer is refused, never truncated.
RAW_PASSES = "proc; flatten; opt_clean"
_RESET_PINS = ("SRST", "ARST", "SET", "CLR", "ALOAD")
# Plain = `[A-Za-z_][A-Za-z0-9_]*` ONLY. `$` (and so every escaped identifier)
# is outside: an observer pragma, a select pattern and a wire name cannot all
# carry it faithfully (round-6 review: `a$b` bound to `dut.a`).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _chain_source(drivers: Dict[int, dict], bit: int, allow_inv: bool
                  ) -> Tuple[Optional[int], int]:
    """Follow `bit` back through 1-bit buffers (and inverters if allowed) to
    the first bit no such cell drives. Returns (source bit, inversions)."""
    par, seen = 0, set()
    while bit not in seen:
        seen.add(bit)
        c = drivers.get(bit)
        if c is None:
            return bit, par
        a = _bits(c, "A")
        t = c.get("type")
        if len(a) != 1 or not (t in _BUFFERS or (allow_inv and t in _INVERTERS)):
            return bit, par
        if t in _INVERTERS:
            par ^= 1
        bit = a[0]
    return None, par


def applicability(netlist: dict, top: str, clock: str, resets: List[str],
                  raw_netlist: Optional[dict] = None) -> List[str]:
    """The R-0915-155 preconditions this design FAILS (empty = inside the
    class). Pure; read from the netlist only."""
    tm = _top_module(netlist, top)
    if tm is None:
        return [f"top module {top!r} is not in the netlist"]
    _, mod = tm
    fails: List[str] = []
    census = _state_census(mod, netlist)
    other = census["latch"] + census["memory"] + census["unclassified"]
    if other:
        fails.append("C4 state that is not a flop: " + ", ".join(sorted(other)))
    drivers = _drivers(mod)
    # C1-C3 look at DESIGN state only — the SAME relevance netlist_facts uses
    # (one definition). A verification-only helper (an `ifdef FORMAL` register
    # feeding an assert) is not the design (round 6, low).
    rel = _relevant_bits(mod)
    flops = [(n, c) for n, c, k in _cells(mod)
             if k == "ff" and rel.intersection(_bits(c, "Q"))]
    relevant_cells = {n for n, _ in flops}
    cbit, why = _port_bit(mod, clock)
    if cbit is None:
        fails.append(f"C1 the clock {clock!r}: {why}")
    else:
        bad = []
        for n, c in flops:
            clk = _bits(c, "CLK")
            src = _chain_source(drivers, clk[0], allow_inv=False)[0] if len(clk) == 1 else None
            if src != cbit:
                bad.append(n)
        if bad:
            fails.append(f"C1 flop(s) not clocked by {clock!r} through a pure "
                         f"buffer chain: " + ", ".join(sorted(bad)))
        edges = {_param_bit((c.get("parameters") or {}).get("CLK_POLARITY"))
                 for _, c in flops}
        if len(edges) > 1:
            fails.append("C1 flops sample on both clock edges")
    rbits = {}
    for r in resets:
        b, why = _port_bit(mod, r)
        if b is None:
            fails.append(f"C2 the reset {r!r}: {why}")
        else:
            rbits[b] = r
    for n, c in flops:
        for pin in _RESET_PINS:
            for b in _bits(c, pin):
                src = _chain_source(drivers, b, allow_inv=True)[0]
                if src not in rbits:
                    fails.append(f"C2 {n} ({c.get('type')}) {pin} is not driven "
                                 f"by a declared reset through a pure "
                                 f"inverter/buffer chain")
                    break
    for b, r in rbits.items():
        cone = _cone(mod, b)
        for n, c, k in _cells(mod):
            if k in ("comb", "metadata"):
                continue
            if k == "ff" and n not in relevant_cells:
                continue
            for p, d in (c.get("port_directions") or {}).items():
                if d != "input" or not cone.intersection(_bits(c, p)):
                    continue
                if k == "ff" and p in _RESET_PINS:
                    continue
                fails.append(f"C3 the reset {r!r} reaches {n}.{p} "
                             f"({c.get('type')}) — not a reset pin")
                break
    facts = netlist_facts(netlist, top)
    if facts.get("ok"):
        odd = [r["name"] for r in facts["registers"] if not _IDENT_RE.match(r["name"])]
        if odd:
            fails.append("C6 register name(s) that are not plain identifiers: "
                         + ", ".join(sorted(odd)))
        if raw_netlist is not None:
            raw = netlist_facts(raw_netlist, top)
            if not raw.get("ok"):
                fails.append(f"C5 the unoptimised read cannot state the design's "
                             f"state: {raw.get('why')}")
            elif sorted(r["name"] for r in raw["registers"]) != sorted(
                    r["name"] for r in facts["registers"]):
                fails.append(
                    "C5 the structural read folds away register(s) the proof "
                    "keeps: " + ", ".join(sorted(
                        set(r["name"] for r in raw["registers"])
                        ^ set(r["name"] for r in facts["registers"]))))
    elif not other:
        fails.append(f"C4 {facts.get('why')}")
    return fails


def temporal_applicability(netlist: dict, top: str) -> List[str]:
    """Extra precondition for the TEMPORAL claims, which the harness samples on
    the clock's POSEDGE: every flop must sample on the posedge."""
    tm = _top_module(netlist, top)
    if tm is None:
        return [f"top module {top!r} is not in the netlist"]
    neg = sorted(n for n, c, k in _cells(tm[1]) if k == "ff" and _param_bit(
        (c.get("parameters") or {}).get("CLK_POLARITY")) != 1)
    return ([f"T1 flop(s) not on the posedge the properties sample: "
             + ", ".join(neg)] if neg else [])


def file_sha256(path: Path) -> Optional[str]:
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def run_yosys(rtl_files: List[Path], top: str, work_dir: Path,
              container: Optional[str], simdef: str = "",
              tag: str = "", passes: str = "") -> Tuple[int, str, Path]:
    """Write the netlist with the flow's yosys. Returns (rc, log, netlist).

    `container=None` runs on this filesystem (the in-image route CI and the
    flow take); a name runs `docker exec` through the ONE guarded builder.
    `defines` are the proof task's own `-D` flags; `tag` names the files so
    one elaboration per task can sit side by side.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    netlist = work_dir / f"formal_structural_netlist{tag}.json"
    if netlist.exists():
        netlist.unlink()
    script = yosys_script([str(Path(f).resolve()) for f in rtl_files], top,
                          str(netlist.resolve()), simdef, passes)
    ys = work_dir / f"formal_structural_check{tag}.ys"
    _aa.write_text(ys, script.replace("; ", "\n") + "\n")
    inner = (f"export PATH=/foss/tools/bin:/foss/tools/yosys/bin:$PATH; "
             f"cd {work_dir.resolve()} && yosys -q -s {ys.name}")
    if container:
        import _container_exec as _ce
        argv = _ce.exec_argv(container, inner, tag="formal_structural_check")
    else:
        argv = ["bash", "-lc", inner]
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
        rc, log = p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError as exc:
        rc, log = 127, f"[formal_structural_check] {exc}\n"
    _aa.write_text(work_dir / f"formal_structural_check{tag}.log", log)
    return rc, log, netlist


def elaborations(rtl_files: List[Path], top: str, work_dir: Path,
                 container: Optional[str] = None,
                 simdef: str = "") -> List[dict]:
    """ONE elaboration: the chip's read of the DUT (R-0915-157), with its
    UNOPTIMISED twin (no opt_dff — flops kept as `prep -noff` keeps them) for
    the C5 census comparison. There is no second build to compare: the proof
    reads the DUT this same way."""
    def _load(rc, path):
        if rc == 0 and path.is_file():
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                return None
        return None
    rc, _log, path = run_yosys(rtl_files, top, work_dir, container, simdef)
    rrc, _rl, rpath = run_yosys(rtl_files, top, work_dir, container, simdef,
                                tag="_raw", passes=RAW_PASSES)
    return [{"defines": f"chip read ({simdef.strip() or 'no -D'})", "rc": rc,
             "path": path, "netlist": _load(rc, path), "raw": _load(rrc, rpath)}]


def check(rtl_files: List[Path], top: str, claims: List[dict],
          work_dir: Path, container: Optional[str] = None,
          simdef: str = "",
          klass: Optional[dict] = None) -> dict:
    """Answer every claim on EVERY proof elaboration. A claim passes only if
    it passes in all of them; elaborations that disagree are NOT_DISCHARGED.
    Returns the evidence record."""
    elabs = elaborations(rtl_files, top, work_dir, container, simdef)
    record = {
        "program": "formal_structural_check", "version": VERSION,
        "top": top, "rtl_files": [str(f) for f in rtl_files],
        # WHAT WAS MEASURED, by content — so a reader (and the read-back in
        # formal_property_run) can refuse evidence about some other RTL.
        "rtl_sha256": {str(f): file_sha256(Path(f)) for f in rtl_files},
        "elaborations": [{"defines": e["defines"], "yosys_rc": e["rc"],
                          "netlist": e["path"].name if e["path"].is_file() else None,
                          "netlist_sha256": (file_sha256(e["path"])
                                             if e["path"].is_file() else None)}
                         for e in elabs],
        "yosys_passes": YOSYS_PASSES,
        "yosys_rc": max(e["rc"] for e in elabs),
    }
    # R-0915-155 — the applicability gate, on EVERY elaboration, before any
    # claim is answered. Outside the class every claim is NOT_DISCHARGED with
    # the failing precondition(s) named; nothing is PASS or REFUTED there.
    fails: List[str] = []
    if klass is not None:
        for e in elabs:
            if e["netlist"] is None:
                fails.append(f"no netlist for defines {e['defines']!r}")
                continue
            for f in applicability(e["netlist"], top, klass.get("clock", ""),
                                   list(klass.get("resets") or []), e["raw"]):
                if f not in fails:
                    fails.append(f)
        record["applicability"] = {"class": "R-0915-155", "inside": not fails,
                                   "failed_preconditions": fails}
    mappings = {}
    for i, e in enumerate(elabs):
        if e["netlist"] is not None:
            for signal in sorted({c.get("signal", "") for c in claims if c.get("rule") in
                                  {"reset_port", "reset_sync", "reset_polarity"}}):
                mappings[i, signal] = reset_control_mapping(
                    e["netlist"], e["raw"], top, signal,
                    work_dir / f"reset_transition_{i}_{signal.encode().hex()}", container)
    results = []
    for claim in claims:
        answers = []
        for i, e in enumerate(elabs):
            reset_claim = claim.get("rule") in {"reset_port", "reset_sync", "reset_polarity"}
            # The new reset mapper owns its C2/C3/C6 extension only. Clock,
            # unknown-storage and raw-census failures retain their old guard.
            blocked = fails and (not reset_claim or any(
                not f.startswith(("C2 ", "C3 ", "C6 ")) for f in fails))
            if blocked:
                answers.append(dict(claim, verdict=NOT_DISCHARGED, finding=(
                    "outside the claim's supported class: " + "; ".join(fails))))
                continue
            if e["netlist"] is None:
                answers.append(dict(claim, verdict=NOT_DISCHARGED, finding=(
                    f"yosys did not produce a netlist (rc={e['rc']}, defines "
                    f"{e['defines']!r}); nothing was measured")))
            else:
                answers.append(check_claim(e["netlist"], top, claim,
                    mappings.get((i, claim.get("signal", ""))) if reset_claim else None))
        verdicts = {a["verdict"] for a in answers}
        if len(verdicts) == 1:
            results.append(answers[0])
        else:
            results.append(dict(claim, verdict=NOT_DISCHARGED,
                                per_elaboration=answers,
                                finding=("the proof's elaborations disagree: "
                                         + "; ".join(f"{e['defines'] or '(none)'}: "
                                                     f"{a['verdict']}"
                                                     for e, a in zip(elabs, answers)))))
    record["claims"] = results
    record["verdict"] = (PASS if results and all(
        r["verdict"] == PASS for r in results) else
        REFUTED if any(r["verdict"] == REFUTED for r in results)
        else NOT_DISCHARGED)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--rtl", type=Path, nargs="+", required=True)
    ap.add_argument("--top", required=True)
    ap.add_argument("--claim", action="append", default=[],
                    help="rule:signal[:value], e.g. clock_edge:clk:posedge")
    ap.add_argument("--work-dir", type=Path, default=Path("."))
    ap.add_argument("--container", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    claims = []
    for spec in args.claim:
        parts = spec.split(":")
        claims.append({"rule": parts[0], "signal": parts[1] if len(parts) > 1
                       else "", "value": parts[2] if len(parts) > 2 else ""})
    rec = check(args.rtl, args.top, claims, args.work_dir, args.container)
    out = json.dumps(rec, indent=2, ensure_ascii=False)
    if args.json:
        _aa.write_text(Path(args.json), out + "\n")
    print(out)
    return 0 if rec["verdict"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
