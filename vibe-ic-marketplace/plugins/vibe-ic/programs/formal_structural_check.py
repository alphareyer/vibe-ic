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


def check_claim(netlist: dict, top: str, claim: dict) -> dict:
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
    if klass is not None:
        fails: List[str] = []
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
        if fails:
            record["claims"] = [dict(c, verdict=NOT_DISCHARGED, finding=(
                "outside the class the program proves soundly (R-0915-155): "
                + "; ".join(fails))) for c in claims]
            record["verdict"] = NOT_DISCHARGED
            return record
    results = []
    for claim in claims:
        answers = []
        for e in elabs:
            if e["netlist"] is None:
                answers.append(dict(claim, verdict=NOT_DISCHARGED, finding=(
                    f"yosys did not produce a netlist (rc={e['rc']}, defines "
                    f"{e['defines']!r}); nothing was measured")))
            else:
                answers.append(check_claim(e["netlist"], top, claim))
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
