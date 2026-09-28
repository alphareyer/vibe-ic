#!/usr/bin/env python3
"""_cdc_netlist.py — the step-3 CDC/RDC rules over a Yosys JSON netlist.

Review row 3 (70-step migration, lane mig-rtlver): no CDC/RDC engine exists in
the image (Verilator 5.053 rejects `--cdc`; no LibreLane step, no OpenROAD
command), so the rules stay ours. What moves is their INPUT. The regex front
ends in `cdc_async_input_check`, `clock_domain_reg_crossing_check` and
`reset_dependency_check` re-derive from RTL text what a netlist states: which
flop is clocked by which net, which net resets it (async or sync), what feeds
its D pin, and which names are top-level ports. A spelling decided a verdict
there (#2063: an internal `b_raw` wire read as an async input port); here
port-ness, clock and reset are structural facts.

FRONT END. The netlist is exactly what LibreLane's `Yosys.JsonHeader` writes
(librelane/scripts/pyosys/json_header.py in vibeic-eda 0.3.77):

    hierarchy -check -top <T> -nokeep_prints -nokeep_asserts
    rename -top <T>; proc; flatten; opt_clean -purge; json -o <out>

`build()` runs those passes (local yosys, else the image
`librelane_contract.resolve_image` answers), and `load()` accepts either that output or a LibreLane
`Yosys.JsonHeader` `json_h` file. After `proc` without `opt_dff`, a sync
reset is the outermost `$mux` in front of D with a constant arm, an enable is
a `$mux` whose other arm is the flop's own Q, and an async reset is the `ARST`
pin of `$adff`.

The step switch (`librelane_contract.selected_mode(project, "3")`):
  direct    — regex front ends only (the default; unchanged)
  librelane — the netlist rules decide; a missing netlist refuses (no fallback)
  dual      — both run; findings are the union and every disagreement between
              the two front ends is reported by name.

chip-AGNOSTIC: nothing here names a design, PDK, cell or vendor. The only
names consulted are yosys's own internal cell types.
"""
from __future__ import annotations

import json
import re
import shutil  # noqa: F401 — tests pin the exec route through shutil.which (docker)
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Where the runner writes the netlist and where the gates read it.
NETLIST_REL = "reports/phase2/cdc/netlist.json"
NETLIST_LOG_REL = "reports/phase2/cdc/netlist.yosys.log"

#: `librelane/scripts/pyosys/json_header.py` after reading the sources.
JSON_HEADER_PASSES = (
    "hierarchy -check -top {top} -nokeep_prints -nokeep_asserts",
    "rename -top {top}",
    "proc",
    "flatten",
    "opt_clean -purge",
)

FLOP_TYPES = {"$dff", "$adff", "$dffe", "$adffe", "$sdff", "$sdffe",
              "$sdffce", "$dffsr", "$dffsre", "$aldff", "$aldffe"}
#: Single-input cells a clock or reset may pass through and stay the same net.
_TRANSPARENT = {"$buf", "$pos", "$not", "$_BUF_", "$_NOT_"}
_MEMORY = {"$mem", "$mem_v2", "$memrd", "$memrd_v2"}
_OUTPUT_PORTS = {"Y", "Q", "RD_DATA", "DATA"}


class Refusal(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def gate_mode(project: Path) -> str:
    """The step-3 front-end mode from the project's step switch."""
    import librelane_contract
    return librelane_contract.selected_mode(Path(project), "3")


# ─────────────────────────────── front end ────────────────────────────────

def yosys_script(files: List[Path], top: Optional[str], out: Path,
                 sv: bool = True) -> str:
    reads = [f"read_verilog {'-sv ' if sv else ''}-defer {f}" for f in files]
    passes = list(JSON_HEADER_PASSES)
    if top:
        passes = [p.format(top=top) for p in passes]
    else:
        passes = ["hierarchy -check -auto-top -nokeep_prints -nokeep_asserts"
                  ] + passes[2:]
    return "; ".join(reads + passes + [f"json -o {out}"])


def build(project: Path, rtl_files: List[Path], top: Optional[str],
          image: Optional[str] = None, docker: str = "docker") -> Path:
    """Write NETLIST_REL for `rtl_files` with the Yosys.JsonHeader passes."""
    project = Path(project).resolve()
    if not rtl_files:
        raise Refusal("CDC_NETLIST_NO_RTL", "no RTL files to read")
    out = project / NETLIST_REL
    log = project / NETLIST_LOG_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.unlink(missing_ok=True)
    files = [Path(f).resolve() for f in rtl_files]
    script = yosys_script(files, top, tmp)
    # WHERE yosys runs is `_eda_tool_route`'s decision, not the host PATH's:
    # a host yosys used to win here whenever one existed, so the netlist this
    # CDC check reads depended on which machine ran it (on 8HD-9 that is
    # /usr/bin/yosys 0.9). With a container route the image runs it; the image
    # is only resolved on that route (switch `image` > VIBEIC_LIBRELANE_IMAGE >
    # this host's released image), and the LOCAL route (no docker client)
    # checks that this PATH's yosys has every command the script runs.
    import _eda_tool_route as _tool_route
    if not image and not _tool_route.local_route():
        import librelane_contract as _ll
        try:
            image = _ll.resolve_image(project)
        except _ll.Refusal as exc:
            raise Refusal("CDC_NETLIST_TOOL_UNAVAILABLE",
                          f"a container route exists and no image: {exc}") from None
    try:
        done = _tool_route.run(["yosys", "-q", "-p", script],
                               cwd=project, image=image,
                               capture_output=True, text=True)
    except _tool_route.ToolRouteRefused as exc:
        raise Refusal("CDC_NETLIST_TOOL_UNAVAILABLE", str(exc)) from None
    ran = getattr(done, "args", None)
    argv = list(ran) if isinstance(ran, (list, tuple)) else ["yosys"]
    log.write_text("$ " + " ".join(map(str, argv)) + "\n" + done.stdout + done.stderr)
    if done.returncode or not tmp.is_file():
        raise Refusal("CDC_NETLIST_BUILD_FAILED",
                      f"yosys rc={done.returncode}; {NETLIST_LOG_REL}")
    tmp.replace(out)
    return out


# ─────────────────────────────── the model ────────────────────────────────

def _int(value) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 2) if set(text) <= {"0", "1"} else int(text)


@dataclass
class Flop:
    name: str
    type: str
    clk: object
    clk_pol: int
    d: List[object]
    q: List[object]
    arst: Optional[object] = None
    srst: Optional[object] = None      # outermost reset-mux select
    label: str = ""


@dataclass
class Netlist:
    top: str
    ports: Dict[str, Tuple[str, List[object]]]
    cells: Dict[str, dict]
    names: Dict[object, str]
    drivers: Dict[object, Tuple[str, str]] = field(default_factory=dict)
    flops: List[Flop] = field(default_factory=list)
    memories: int = 0

    @property
    def port_bits(self) -> Dict[object, str]:
        return {b: n for n, (d, bits) in self.ports.items()
                if d in ("input", "inout") for b in bits}

    def is_const(self, bit) -> bool:
        return isinstance(bit, str)

    def flop_of_q(self) -> Dict[object, Flop]:
        return {b: f for f in self.flops for b in f.q}

    def root(self, bit, limit: int = 32) -> object:
        """Follow a net back through single-input buffers/inverters."""
        for _ in range(limit):
            drv = self.drivers.get(bit)
            if not drv:
                return bit
            cell = self.cells[drv[0]]
            if cell["type"] not in _TRANSPARENT:
                return bit
            bit = cell["connections"]["A"][0]
        return bit

    def bit_label(self, bit) -> str:
        if self.is_const(bit):
            return f"const:{bit}"
        port = {b: n for n, (_d, bits) in self.ports.items() for b in bits}
        if bit in port:
            return port[bit]
        return self.names.get(bit, f"net#{bit}")

    def domain(self, flop: Flop) -> str:
        return self.bit_label(self.root(flop.clk))

    def cone(self, bits: Iterable, skip: Optional[Set[object]] = None
             ) -> Tuple[Set[object], Set[object]]:
        """Backward combinational cone. Returns (flop Q bits, port bits)."""
        q_of = self.flop_of_q()
        ports = self.port_bits
        seen: Set[object] = set()
        found_q: Set[object] = set()
        found_p: Set[object] = set()
        stack = [b for b in bits if not self.is_const(b)]
        while stack:
            bit = stack.pop()
            if bit in seen or (skip and bit in skip):
                continue
            seen.add(bit)
            if bit in q_of:
                found_q.add(bit)
                continue
            if bit in ports:
                found_p.add(bit)
                continue
            drv = self.drivers.get(bit)
            if not drv:
                continue
            cell = self.cells[drv[0]]
            if cell["type"] in FLOP_TYPES or cell["type"] in _MEMORY:
                continue
            for pin, conn in cell["connections"].items():
                if cell.get("port_directions", {}).get(pin) == "output" or \
                        (not cell.get("port_directions") and pin in _OUTPUT_PORTS):
                    continue
                stack.extend(b for b in conn if not self.is_const(b))
        return found_q, found_p

    def direct_source(self, flop: Flop, index: int) -> Optional[object]:
        """The one bit D[index] samples with no logic between, else None.

        Passing a `$mux` is allowed when its other arm is a constant (reset or
        clear) or the flop's own Q (hold/enable): those arms add no data.
        """
        bit = flop.d[index]
        own = flop.q[index]
        for _ in range(64):
            if self.is_const(bit):
                return None
            drv = self.drivers.get(bit)
            if not drv:
                return bit
            cell = self.cells[drv[0]]
            if cell["type"] in FLOP_TYPES or bit in self.port_bits:
                return bit
            if cell["type"] in ("$buf", "$pos", "$_BUF_"):
                bit = cell["connections"]["A"][0]
                continue
            if cell["type"] in ("$mux", "$_MUX_"):
                conns = cell["connections"]
                pos = conns["Y"].index(bit)
                arms = (conns["A"][pos], conns["B"][pos])
                data = [a for a in arms if not self.is_const(a) and a != own]
                if len(data) != 1:
                    return None
                bit = data[0]
                continue
            return None
        return None


def _outer_reset_mux(nl: Netlist, d_bits: List[object]) -> Optional[object]:
    """The select of the $mux driving every D bit with a constant arm."""
    selects: Set[object] = set()
    for bit in d_bits:
        drv = nl.drivers.get(bit)
        if not drv:
            return None
        cell = nl.cells[drv[0]]
        if cell["type"] not in ("$mux", "$_MUX_"):
            return None
        conns = cell["connections"]
        pos = conns["Y"].index(bit)
        if not (nl.is_const(conns["A"][pos]) or nl.is_const(conns["B"][pos])):
            return None
        selects.add(conns["S"][0])
    return selects.pop() if len(selects) == 1 else None


def load(path: Path, top: Optional[str] = None) -> Netlist:
    """Read a Yosys JSON netlist (JsonHeader shape) into the CDC model."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refusal("CDC_NETLIST_UNREADABLE", f"{path}: {exc}") from exc
    mods = doc.get("modules") if isinstance(doc, dict) else None
    if not isinstance(mods, dict) or not mods:
        raise Refusal("CDC_NETLIST_UNREADABLE", f"{path}: no modules")
    if top is None:
        tops = [n for n, m in mods.items()
                if _int(m.get("attributes", {}).get("top", 0) or 0)]
        if len(tops) != 1:
            tops = [n for n, m in mods.items()
                    if not _int(m.get("attributes", {}).get("blackbox", 0) or 0)]
        if len(tops) != 1:
            raise Refusal("CDC_NETLIST_TOP_AMBIGUOUS", f"{path}: {sorted(tops)}")
        top = tops[0]
    if top not in mods:
        raise Refusal("CDC_NETLIST_TOP_MISSING", f"{path}: {top}")
    mod = mods[top]
    ports = {n: (p.get("direction", ""), list(p.get("bits", [])))
             for n, p in mod.get("ports", {}).items()}
    names: Dict[object, str] = {}
    for n, net in sorted(mod.get("netnames", {}).items(),
                         key=lambda kv: (_int(kv[1].get("hide_name", 0)), kv[0])):
        for i, b in enumerate(net.get("bits", [])):
            if isinstance(b, int) and b not in names:
                width = len(net.get("bits", []))
                names[b] = n if width == 1 else f"{n}[{i}]"
    nl = Netlist(top=top, ports=ports, cells=mod.get("cells", {}), names=names)
    for cname, cell in nl.cells.items():
        dirs = cell.get("port_directions") or {}
        for pin, conn in cell.get("connections", {}).items():
            out = dirs.get(pin) == "output" if dirs else pin in _OUTPUT_PORTS
            if out:
                for b in conn:
                    if isinstance(b, int):
                        nl.drivers[b] = (cname, pin)
        if cell["type"] in _MEMORY:
            nl.memories += 1
    for cname, cell in nl.cells.items():
        if cell["type"] not in FLOP_TYPES:
            continue
        c = cell["connections"]
        params = cell.get("parameters", {})
        if "CLK" not in c:
            continue
        flop = Flop(name=cname, type=cell["type"], clk=c["CLK"][0],
                    clk_pol=_int(params.get("CLK_POLARITY", 1)),
                    d=list(c.get("D", [])), q=list(c.get("Q", [])),
                    arst=(c.get("ARST") or [None])[0])
        flop.srst = (c.get("SRST") or [None])[0] or \
            _outer_reset_mux(nl, flop.d)
        flop.label = nl.bit_label(flop.q[0]).split("[")[0] if flop.q else cname
        nl.flops.append(flop)
    return nl


# ──────────────────────────────── the rules ───────────────────────────────

def _finding(rule: str, severity: str, message: str, **evidence) -> dict:
    return {"rule": rule, "severity": severity, "message": message,
            "file": NETLIST_REL, "line": 0, "evidence": evidence}


def _sync_stage2(nl: Netlist, stage1: Flop, index: int, domain: str) -> bool:
    """Is stage1.Q[index] sampled directly by another flop of `domain`?"""
    bit = stage1.q[index]
    for f in nl.flops:
        if f is stage1 or nl.domain(f) != domain:
            continue
        for i in range(len(f.d)):
            if nl.direct_source(f, i) == bit:
                return True
    return False


def _data_cones(nl: Netlist) -> List[Tuple[Flop, int, Set[object]]]:
    """Every flop D bit's cone, computed once per netlist.

    The sync-reset select is excluded: a reset reaching a flop is a reset
    question (the RDC rule), not a data crossing.
    """
    cached = getattr(nl, "_cones", None)
    if cached is None:
        cached = []
        for f in nl.flops:
            skip = {f.srst} if f.srst is not None else None
            for i, d in enumerate(f.d):
                q, p = nl.cone([d], skip=skip)
                cached.append((f, i, q | p))
        nl._cones = cached
    return cached


def _consumers(nl: Netlist, source_bit) -> List[Tuple[Flop, int, bool]]:
    """(flop, D index, direct?) for every flop whose data cone reads the bit."""
    return [(f, i, nl.direct_source(f, i) == source_bit)
            for f, i, cone in _data_cones(nl) if source_bit in cone]


def clock_domains(nl: Netlist) -> List[str]:
    return sorted({nl.domain(f) for f in nl.flops})


def reg_crossing_findings(nl: Netlist) -> List[dict]:
    """A flop of domain A read by a flop of domain B (clock_domain_reg_crossing)."""
    import instrument_calibration
    instrument_calibration.assert_calibrated("_cdc_netlist::reg_crossing_findings")
    domains = clock_domains(nl)
    if len(domains) < 2:
        return []
    findings: List[dict] = []
    per_pair: Dict[Tuple[str, str], List[Tuple[Flop, bool]]] = {}
    for src in nl.flops:
        send = nl.domain(src)
        synced_all: Dict[str, bool] = {}
        for idx, bit in enumerate(src.q):
            for f, i, direct in _consumers(nl, bit):
                recv = nl.domain(f)
                if recv == send:
                    continue
                ok = direct and _sync_stage2(nl, f, i, recv)
                synced_all[recv] = synced_all.get(recv, True) and ok
        for recv, ok in synced_all.items():
            per_pair.setdefault((send, recv), []).append((src, ok))
    for (send, recv), rows in sorted(per_pair.items()):
        control = any(ok for _f, ok in rows)
        for src, ok in rows:
            width = len(src.q)
            ev = {"register": src.label, "width": width, "from_domain": send,
                  "to_domain": recv, "synchronised": ok,
                  "control_path_synchronised": control, "cell": src.name}
            if ok:
                if width > 1 and "gray" not in src.label.lower():
                    findings.append(_finding(
                        "CDC_MULTIBIT_NO_GRAY", "WARN",
                        f"{width}-bit '{src.label}' crosses '{send}'->'{recv}' "
                        f"through per-bit synchronisers with no gray evidence",
                        **ev, gray_evidence=False))
                continue
            qualified = control and width > 1
            findings.append(_finding(
                "CDC_UNSYNCED_DATA_QUALIFIED" if qualified else "CDC_REG_NO_SYNC",
                "WARN" if qualified else "ERROR",
                (f"{width}-bit '{src.label}' crosses '{send}'->'{recv}' "
                 f"unsynchronised beside a synchronised control path")
                if qualified else
                (f"'{src.label}' (clock '{send}') is read by clock '{recv}' "
                 f"with no 2-flop synchroniser"),
                **ev))
    return findings


def async_input_findings(nl: Netlist, is_candidate) -> List[dict]:
    """A top-level async input port sampled without a 2-flop synchroniser.

    `is_candidate(name)` is the regex gate's own port-name classifier; the
    candidate set is drawn from the netlist's PORTS only, so an internal net is
    never an async input whatever it is called (#2063).
    """
    import instrument_calibration
    instrument_calibration.assert_calibrated("_cdc_netlist::async_input_findings")
    clock_or_reset = {nl.root(f.clk) for f in nl.flops} | \
        {nl.root(f.arst) for f in nl.flops if f.arst is not None}
    findings: List[dict] = []
    for name, (direction, bits) in sorted(nl.ports.items()):
        if direction not in ("input", "inout") or not is_candidate(name):
            continue
        for bit in bits:
            if bit in clock_or_reset:
                continue
            for f, i, direct in _consumers(nl, bit):
                if direct and _sync_stage2(nl, f, i, nl.domain(f)):
                    continue
                findings.append(_finding(
                    "ASYNC_INPUT_NO_SYNC", "ERROR",
                    f"async input port '{name}' reaches flop '{f.label}' "
                    + ("through logic" if not direct else
                       "with no second synchroniser stage"),
                    port=name, flop=f.label, direct=direct))
                break
    return findings


def reset_dependency_findings(nl: Netlist) -> List[dict]:
    """A reset net whose own fan-in contains a flop it resets (a cycle)."""
    import instrument_calibration
    instrument_calibration.assert_calibrated("_cdc_netlist::reset_dependency_findings")
    resets: Dict[object, List[Flop]] = {}
    for f in nl.flops:
        for r in (f.arst, f.srst):
            if r is not None and not nl.is_const(r):
                resets.setdefault(nl.root(r), []).append(f)
    reset_of: Dict[str, Set[object]] = {}
    for r, flops in resets.items():
        for f in flops:
            reset_of.setdefault(f.name, set()).add(r)
    edges: Dict[object, Set[object]] = {r: set() for r in resets}
    q_of = nl.flop_of_q()
    for r in resets:
        qbits, _ports = nl.cone([r])
        for qb in qbits:
            for r2 in reset_of.get(q_of[qb].name, ()):
                edges.setdefault(r2, set()).add(r)
    findings: List[dict] = []
    reported: Set[frozenset] = set()
    for start in sorted(edges, key=str):
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for nxt in edges.get(node, ()):
                if nxt == start:
                    cyc = frozenset(path)
                    if cyc not in reported:
                        reported.add(cyc)
                        labels = [nl.bit_label(b) for b in path]
                        findings.append(_finding(
                            "CIRCULAR_RESET_DEPENDENCY", "ERROR",
                            "reset net(s) " + " -> ".join(labels + [labels[0]])
                            + " feed back through flops they reset",
                            cycle=labels))
                elif nxt not in path and len(path) < 16:
                    stack.append((nxt, path + [nxt]))
    return findings


def judge(nl: Netlist, rule: str, is_candidate=None) -> List[dict]:
    """The one entry every gate uses to judge a netlist.

    Each rule carries its own `assert_calibrated` line: one pair per rule, so
    a calibrated crossing rule cannot vouch for the reset rule.
    """
    if rule == "reg_crossing":
        return reg_crossing_findings(nl)
    if rule == "async_input":
        return async_input_findings(nl, is_candidate or (lambda _n: False))
    if rule == "reset_dependency":
        return reset_dependency_findings(nl)
    raise ValueError(rule)


# ───────────────────────────── the dual arm ───────────────────────────────

def disagreement(regex_findings: List[dict], netlist_findings: List[dict],
                 regex_scanned: int) -> Dict[str, object]:
    """Name what one front end reports that the other does not.

    Findings are compared by (rule, blocking?) counts and by verdict; the
    two front ends name signals differently (source text vs netlist nets),
    so a per-signal comparison would report spelling, not disagreement.
    """
    def _block(rows):
        return sorted({r["rule"] for r in rows if r.get("severity") == "ERROR"})
    rv = "FAIL" if _block(regex_findings) else ("PASS" if regex_scanned else "NOT_MEASURED")
    nv = "FAIL" if _block(netlist_findings) else "PASS"
    return {"regex_verdict": rv, "netlist_verdict": nv,
            "regex_blocking_rules": _block(regex_findings),
            "netlist_blocking_rules": _block(netlist_findings),
            "agree": rv == nv and _block(regex_findings) == _block(netlist_findings)}


def netlist_for_gate(project: Path, explicit: Optional[str]) -> Optional[Path]:
    path = Path(explicit) if explicit else Path(project) / NETLIST_REL
    return path if path.is_file() else None


def resolve_mode(project: Path, front_end: Optional[str],
                 netlist_arg: Optional[str]) -> str:
    """`--front-end` wins; an explicit `--netlist` opts in; else the switch."""
    if front_end and front_end != "auto":
        return {"regex": "direct", "netlist": "librelane"}.get(front_end, front_end)
    if netlist_arg:
        return "librelane"
    return gate_mode(project)


def apply_front_end(project: Path, mode: str, netlist_arg: Optional[str],
                    rule: str, regex_findings: List[dict], regex_scanned: int,
                    is_candidate=None) -> Tuple[List[dict], Dict[str, object]]:
    """The findings a step-3 gate reports under `mode`, and what it read.

    direct    -> the regex findings, untouched.
    librelane -> the netlist findings only; no netlist is a blocking refusal.
    dual      -> the union, plus the named disagreement.
    """
    if mode == "direct":
        return regex_findings, {"front_end": "regex"}
    path = netlist_for_gate(project, netlist_arg)
    if path is None:
        return ([_finding("NETLIST_MISSING", "ERROR",
                          f"step-3 mode '{mode}' needs the Yosys JSON netlist "
                          f"{netlist_arg or NETLIST_REL}; the regex front end is "
                          f"not substituted for it")],
                {"front_end": "netlist" if mode == "librelane" else "dual",
                 "netlist": str(netlist_arg or NETLIST_REL), "netlist_read": False})
    import instrument_calibration
    try:
        nl = load(path)
        rows = judge(nl, rule, is_candidate)
    except (Refusal, instrument_calibration.Uncalibrated) as exc:
        code = getattr(exc, "code", "NETLIST_RULE_UNCALIBRATED")
        return ([_finding(code, "ERROR", str(exc))],
                {"front_end": mode, "netlist": str(path), "netlist_read": False})
    import hashlib
    extra: Dict[str, object] = {
        "netlist": str(path), "netlist_read": True,
        "netlist_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "netlist_top": nl.top, "netlist_flops": len(nl.flops),
        "netlist_clock_domains": clock_domains(nl)}
    if mode == "librelane":
        extra["front_end"] = "netlist"
        return rows, extra
    extra["front_end"] = "dual"
    extra["front_end_comparison"] = disagreement(regex_findings, rows, regex_scanned)
    return regex_findings + rows, extra
