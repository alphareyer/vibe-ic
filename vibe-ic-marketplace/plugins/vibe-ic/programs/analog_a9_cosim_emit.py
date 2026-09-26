#!/usr/bin/env python3
"""A9 mixed-signal co-simulation PRODUCER — it runs the scenarios L22 declares.

ROLE: PRODUCER, not a gate. It writes
``phase3/mixed_signal/cosim/mixed_signal_results.json``; ``mixed_signal_cosim_
check`` judges it against the same L22 rows.

WHY IT READS ITS SCENARIOS AND NEVER LISTS THEM
-----------------------------------------------
The A9 gate used to count the scenarios in this report, and whoever wrote the
report chose them: a producer that lists one easy scenario certifies itself.
Phase 1 (`l22_analog_verification_plan_emit`) now fixes the set from the input
alone — ``L22.verification_plan.cosim_scenarios[]`` — before this program
exists in the run. This program runs exactly those ids, stamps the sha256 of
the L22 it read into every row, and adds nothing. A scenario it cannot run is
reported NOT_MEASURED with the reason, never dropped.

TWO ARMS, ONE ENGINE BUILD
--------------------------
Both arms run the fork ngspice A4 uses, on the A3 block netlists A3/A4 use.

  * ``ngspice`` — a row whose every criterion is a declared bound on a named
    pin (a supply feeding another block) needs no digital side: the two A3
    testbenches are composed at the declared connection and the pins are
    measured over the transient.
  * ``d_cosim`` — a row that reads the digital output (sign, monotonicity,
    range, logic level, window independence) runs ngspice's ``d_cosim`` with
    the Icarus shim. The Verilog side is a testbench OBSERVER, labelled
    ``role: instrument``: it counts the output's ones per window of the
    declared oversampling ratio and runs the declared-order cascade of
    integrators (the incremental converter's CoI decimator). It is derived
    from L5 only, because the input declares no decimator of its own.

Before any d_cosim launch the program opens ``libvvp`` at an ABSOLUTE path
(passed as ``lib_args[0]`` too, so the run does not depend on the loader
cache). An image built without ``--enable-libvvp`` cannot load it: every
d_cosim row is then NOT_MEASURED, the line ``ENV_REFUSED:`` is printed, and the
program exits ``EX_ENV_REFUSED`` after writing what the ngspice arm measured.

VERDICTS, PER ROW
-----------------
PASS / FAIL come only from a measured value against the row's own criterion.
NOT_MEASURED names why. A row L22 graded ``UNBOUNDED_IN_INPUT`` is reported
``UNBOUNDED`` whatever it measured: with no criterion it can never pass. A
full-record ENOB/SNDR (IEEE 1241 sine fit) is not graded here at transistor
level; that stays with A4 and the analog acceptance producer.

EXIT CODES (the analog producer contract, `_analog_producer_common`)
    0   results written for every declared row
    2   HONEST GAP — L22 declares no scenario; ``cosim_scenario_gap.json``
        says why (A9 is then not verified, never PASS or FAIL)
    64  usage error
    69  ENV_REFUSED — the container/engine refused; rows it could not run are
        NOT_MEASURED in the results

chip-AGNOSTIC: no block, pin, PDK or design literal. Every name comes from L22
and the A3 artefacts; every bound is an L5 record's own value.
"""
from __future__ import annotations

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import hashlib
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import _analog_producer_common as _pc
import _path_layout as _pl
from _atomic_artefact import write_json, write_text
from l_doc_consumer_contract import l_doc_fields, load_l_doc

TOOL = "analog_a9_cosim_emit"
RESULTS = "mixed_signal_results.json"
GAP_FILE = "cosim_scenario_gap.json"
SCHEMA_VERSION = 1

#: Where the EDA image installs the Icarus engine library. The d_cosim shim
#: takes it from ``lib_args[0]`` and falls back to a bare-name loader lookup
#: only when that argument is absent, so passing the absolute path is what
#: makes the run independent of the loader cache. Overridable per image.
LIBVVP_DEFAULT = "/foss/tools/iverilog/lib/libvvp.so.1"
LIBVVP_ENV = "VIBEIC_LIBVVP"

#: Verdict words a row can carry. The gate reads them; nothing here rounds one
#: up into another.
PASS, FAIL, NOT_MEASURED, UNBOUNDED = "PASS", "FAIL", "NOT_MEASURED", "UNBOUNDED"

#: Criteria that need the digital side to be read.
_DIGITAL_CRITERIA = frozenset({"polarity", "monotonic", "in_range",
                               "logic_level", "window_independent"})
_RESOLUTION_WORDS = frozenset({"enob", "sndr", "sinad"})
_OVERSAMPLING_WORDS = frozenset({"osr", "oversampling"})
_ORDER_WORDS = frozenset({"order"})
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)\s+([^*]*)", re.IGNORECASE)
_TRAN_RE = re.compile(r"^\s*\.?tran\s+(\S+)\s+(\S+)", re.IGNORECASE)
_PULSE_RE = re.compile(r"pulse\s*\(([^)]*)\)", re.IGNORECASE)
_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
       "k": 1e3, "meg": 1e6, "g": 1e9}
_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
#: The observer's own line. The grammar is THIS program's `$display`, so the
#: reader and the writer are one module; nothing else prints it.
_OBS_RE = re.compile(r"^A9OBS window=(\d+) ones=(\d+) code=(-?\d+) x=(\d+) "
                     r"n=(\d+)\s*$", re.MULTILINE)


# ── reading ─────────────────────────────────────────────────────────────────
def _tokens(value: Any) -> set:
    return {t.lower() for t in _WORD_RE.findall(str(value or ""))}


def load_plan(project: Path) -> Tuple[Optional[Path], Optional[str], dict]:
    """(L22 path, its sha256, verification_plan) — the plan may be empty."""
    path, doc = load_l_doc(project, "L22")
    if path is None or doc is None:
        return path, None, {}
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = l_doc_fields(doc).get("verification_plan")
    return path, sha, plan if isinstance(plan, dict) else {}


def _spice_number(raw: str) -> Optional[float]:
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:e[-+]?\d+)?)(meg|[fpnumkg])?[a-z]*",
                     raw.strip().lower())
    if not m:
        return None
    return float(m.group(1)) * _SI.get(m.group(2) or "", 1.0)


def _ports(netlist: str, block: str) -> List[str]:
    for line in netlist.splitlines():
        m = _SUBCKT_RE.match(line)
        if m and m.group(1).lower() == block.lower():
            return [p for p in m.group(2).split() if "=" not in p]
    return []


def parse_testbench(text: str, block: str, ports: List[str]) -> dict:
    """The A3 testbench as data: which node each pin is on, and its elements.

    Only independent sources and passive elements are kept; analysis cards,
    `.save` and `.control` blocks are this program's own to write.
    """
    pin_node: Dict[str, str] = {}
    elements: List[dict] = []
    tran: Optional[Tuple[float, float]] = None
    in_control = False
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if not line or line.startswith("*"):
            continue
        if low.startswith(".control"):
            in_control = True
            continue
        if low.startswith(".endc"):
            in_control = False
            continue
        m = _TRAN_RE.match(line)
        if m and tran is None:
            step, stop = _spice_number(m.group(1)), _spice_number(m.group(2))
            if step and stop:
                tran = (step, stop)
        if in_control or line.startswith("."):
            continue
        parts = line.split()
        kind = parts[0][0].lower()
        if kind == "x" and parts[-1].lower() == block.lower():
            for pin, node in zip(ports, parts[1:-1]):
                pin_node[pin] = node
            continue
        if kind in "virc" and len(parts) >= 4:
            elements.append({"name": parts[0], "nodes": parts[1:3],
                             "value": " ".join(parts[3:])})
    return {"pin_node": pin_node, "elements": elements, "tran": tran}


def _artefacts(project: Path, block: str) -> Optional[dict]:
    bdir = _pl.analog_dir(project) / block
    netlist, bench = bdir / f"{block}.sp", bdir / f"tb_{block}.sp"
    if not (netlist.is_file() and bench.is_file()):
        return None
    ports = _ports(netlist.read_text(errors="replace"), block)
    tb = parse_testbench(bench.read_text(errors="replace"), block, ports)
    if not ports or not tb["pin_node"]:
        return None
    return {"netlist": netlist, "testbench": bench, "ports": ports, **tb}


def _clock_of(tb: dict) -> Optional[dict]:
    """The testbench's clock: a pulse source, its element and its numbers."""
    for el in tb["elements"]:
        m = _PULSE_RE.search(el["value"])
        if not m:
            continue
        args = [_spice_number(a) for a in m.group(1).split()]
        if len(args) >= 7 and all(a is not None for a in args[:7]):
            return {"element": el, "low": args[0], "high": args[1],
                    "delay": args[2], "rise": args[3], "fall": args[4],
                    "period": args[6]}
    return None


def _block_spec(plan: dict, block: str, words: frozenset) -> Optional[dict]:
    for row in plan.get("analog") or []:
        if not isinstance(row, dict) or row.get("block") != block:
            continue
        for spec in row.get("specifications") or []:
            if _tokens(spec.get("name")) & words:
                return spec
    return None


# ── deck composition ────────────────────────────────────────────────────────
def _node(block: str, node: str) -> str:
    return "0" if node in ("0", "gnd") else f"{block}_{node}"


def compose(project: Path, row: dict, arts: Dict[str, dict]
            ) -> Tuple[List[str], Dict[Tuple[str, str], str]]:
    """Instances + kept testbench elements for the row's blocks.

    A connection the row DECLARES replaces the consumer's pin node with the
    supplier's, and every testbench element that drove or loaded either pin is
    dropped: the declared other block is now what is on that node.
    Returns (deck lines, {(block, pin): node}).
    """
    where: Dict[Tuple[str, str], str] = {}
    for block in row.get("blocks") or []:
        for pin, node in arts[block]["pin_node"].items():
            where[(block, pin)] = _node(block, node)
    connect = (row.get("stimulus") or {}).get("connect") or []
    replaced: set = set()
    if len(connect) == 2:
        src, dst = connect
        a, b = (src["block"], src["pin"]), (dst["block"], dst["pin"])
        if a in where and b in where:
            replaced = {where[a], where[b]}
            where[b] = where[a]
    lines: List[str] = []
    for block in row.get("blocks") or []:
        art = arts[block]
        lines.append(f".include {art['netlist']}")
    for block in row.get("blocks") or []:
        art = arts[block]
        for el in art["elements"]:
            nodes = [_node(block, n) for n in el["nodes"]]
            if replaced & set(nodes):
                continue
            lines.append(f"{el['name']}__{block} {' '.join(nodes)} "
                         f"{el['value']}")
        pins = " ".join(where[(block, p)] for p in art["ports"]
                        if (block, p) in where)
        lines.append(f"x{block} {pins} {block}")
    return lines, where


def ngspice_deck(project: Path, row: dict, arts: Dict[str, dict],
                 cycles: int) -> Tuple[str, List[dict]]:
    """A composed analog deck for a row whose criteria are pin bounds."""
    body, where = compose(project, row, arts)
    clocks = [c for c in (_clock_of(arts[b]) for b in row["blocks"]) if c]
    steps = [arts[b]["tran"][0] for b in row["blocks"] if arts[b]["tran"]]
    step = min(steps) if steps else None
    if clocks:
        start = max(c["delay"] for c in clocks)
        span = cycles * max(c["period"] for c in clocks)
    else:
        start, span = 0.0, max(arts[b]["tran"][1] for b in row["blocks"]
                               if arts[b]["tran"])
    stop = start + span
    settle = start + span / 2
    probes: List[dict] = []
    control = [".control", f"tran {step:g} {stop:g}"]
    saves = []
    for i, crit in enumerate(row.get("criteria") or []):
        node = where.get((crit.get("block"), crit.get("pin")))
        if node is None:
            probes.append({"criterion": i, "node": None})
            continue
        saves.append(f"v({node})")
        for stat in ("min", "max"):
            name = f"a9_{i}_{stat}"
            control.append(f"meas tran {name} {stat} v({node}) "
                           f"from={settle:g} to={stop:g}")
        # The A4 reader takes a `meas` result from its `echo "MEAS k=" $&k`
        # summary: ngspice prints the native line as `k = v at= t`, which its
        # native-line pattern (a value that ENDS the line) does not match.
        control.append(f'echo "MEAS a9_{i}_min=" $&a9_{i}_min '
                       f'" a9_{i}_max=" $&a9_{i}_max')
        probes.append({"criterion": i, "node": node})
    control += [".endc", ".end"]
    head = [f"* {TOOL} — scenario {row['id']}: {row.get('description')}",
            f"* window: {cycles} clock period(s) from the first edge; graded "
            f"over the second half ({settle:g} s .. {stop:g} s) — an "
            f"instrument choice, not a bound"]
    deck = "\n".join(head + body + [".options trtol=1",
                                    ".save " + " ".join(_ordered(saves))]
                     + control) + "\n"
    return deck, probes


def _ordered(values: List[str]) -> List[str]:
    out: List[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def observer_verilog(window: int, order: int) -> str:
    """The testbench observer: ones per window, and an order-L CoI cascade."""
    stages = max(1, int(order))
    regs = "\n".join(f"  integer acc{k};" for k in range(stages))
    zero = " ".join(f"acc{k} = 0;" for k in range(stages))
    chain = ["      acc0 = acc0 + (bit_in === 1'b1 ? 1 : 0);"]
    chain += [f"      acc{k} = acc{k} + acc{k - 1};" for k in range(1, stages)]
    return f"""// {TOOL} observer — role: instrument, derived from L5 only
// window = the declared oversampling ratio; cascade = the declared order.
`timescale 1ns/1ps
module a9_observer(input wire clk, input wire bit_in, output reg mark);
  parameter integer N = {int(window)};
  integer n, ones, xs, win;
{regs}
  initial begin
    n = 0; ones = 0; xs = 0; win = 0; mark = 1'b0; {zero}
  end
  always @(posedge clk) begin
    if (bit_in !== 1'b0 && bit_in !== 1'b1) xs = xs + 1;
    else begin
      ones = ones + (bit_in ? 1 : 0);
{chr(10).join(chain)}
    end
    n = n + 1;
    if (n == N) begin
      $display("A9OBS window=%0d ones=%0d code=%0d x=%0d n=%0d",
               win, ones, acc{stages - 1}, xs, N);
      win = win + 1; n = 0; ones = 0; xs = 0; {zero}
      mark = ~mark;
    end
  end
endmodule
"""


def dcosim_deck(project: Path, row: dict, arts: Dict[str, dict], *,
                vvp: str, libvvp: str, windows: int, window: int,
                clock_hz: Optional[float] = None,
                input_volts: Optional[float] = None) -> Optional[str]:
    """One d_cosim deck: the block, its testbench, the bridges, the observer."""
    observe = row.get("observe") or []
    if len(observe) != 1:
        return None
    block, pin = observe[0]["block"], observe[0]["pin"]
    art = arts[block]
    clock = _clock_of(art)
    if clock is None or pin not in art["pin_node"]:
        return None
    body, where = compose(project, {"blocks": [block]}, arts)
    period = clock["period"] if not clock_hz else 1.0 / clock_hz
    if clock_hz:
        name = clock["element"]["name"] + f"__{block}"
        edge = clock["rise"]
        body = [ln if not ln.startswith(name + " ") else
                (f"{name} {' '.join(_node(block, n) for n in clock['element']['nodes'])} "
                 f"pulse({clock['low']:g} {clock['high']:g} {clock['delay']:g} "
                 f"{edge:g} {clock['fall']:g} {period / 2 - edge:g} "
                 f"{period:g})") for ln in body]
    stim = row.get("stimulus") or {}
    if input_volts is not None and stim.get("pin") in art["pin_node"]:
        node = where[(block, stim["pin"])]
        body = [ln for ln in body
                if not (ln.split()[0][0].lower() == "v"
                        and node in ln.split()[1:3])]
        body.append(f"v_a9_in {node} 0 {input_volts:g}")
    rail = clock["high"]
    clk_node = _node(block, clock["element"]["nodes"][0])
    stop = clock["delay"] + windows * window * period
    return "\n".join([
        f"* {TOOL} — scenario {row['id']}: {row.get('description')}",
        "* bridge thresholds: the data bit resolves below 1/3 and above 2/3 of "
        "the testbench's own logic rail and is X between them; the clock "
        "switches at half the rail with NO X band, because 0->X and X->1 are "
        "each a Verilog posedge and an X band would count every clock twice "
        "(measured). Instrument definitions, not bounds.",
        *body,
        f".model a9_adc adc_bridge in_low={rail / 3:g} in_high={2 * rail / 3:g}",
        f".model a9_clk_adc adc_bridge in_low={rail / 2:g} in_high={rail / 2:g}",
        f"a9_clk [{clk_node}] [a9_dclk] a9_clk_adc",
        f"a9_bit [{where[(block, pin)]}] [a9_dbit] a9_adc",
        f"a9_obs [a9_dclk a9_dbit] [a9_mark] a9_observer",
        f".model a9_observer d_cosim simulation=\"ivlng\" "
        f"sim_args=[\"{vvp}\"] lib_args=[\"{libvvp}\"]",
        ".options trtol=1",
        f".save v({where[(block, pin)]})",
        ".control", f"tran {art['tran'][0] if art['tran'] else period / 200:g} "
        f"{stop:g}", ".endc", ".end"]) + "\n"


# ── grading ─────────────────────────────────────────────────────────────────
def grade_bounds(row: dict, probes: List[dict], meas: Dict[str, float]
                 ) -> List[dict]:
    """Each pin-bound criterion against the min/max the transient measured."""
    out: List[dict] = []
    for probe in probes:
        crit = dict((row.get("criteria") or [])[probe["criterion"]])
        lo = meas.get(f"a9_{probe['criterion']}_min")
        hi = meas.get(f"a9_{probe['criterion']}_max")
        if probe["node"] is None or lo is None or hi is None:
            crit.update(verdict=NOT_MEASURED,
                        reason="the pin was not measured")
            out.append(crit)
            continue
        ok = ((not isinstance(crit.get("min"), (int, float)) or lo >= crit["min"])
              and (not isinstance(crit.get("max"), (int, float))
                   or hi <= crit["max"]))
        crit.update(measured={"min": lo, "max": hi, "node": probe["node"]},
                    verdict=PASS if ok else FAIL)
        out.append(crit)
    return out


def observer_windows(log: str) -> List[dict]:
    return [{"window": int(m.group(1)), "ones": int(m.group(2)),
             "code": int(m.group(3)), "x": int(m.group(4)),
             "n": int(m.group(5))} for m in _OBS_RE.finditer(log or "")]


def grade_structural(kind: str, points: List[Tuple[Any, List[dict]]]
                     ) -> Tuple[str, str]:
    """A structural criterion over (stimulus point, observer windows) pairs.

    The first window of every run is discarded: without a declared reset its
    start is not aligned to the block's own conversion window.
    """
    usable = [(p, w[1:]) for p, w in points]
    if not usable or any(not w for _, w in usable):
        return NOT_MEASURED, "no complete observer window after the first"
    if kind == "logic_level":
        bad = [p for p, w in usable if any(x["x"] for x in w)]
        return (FAIL, f"unresolved samples at {bad}") if bad else (PASS, "")
    if kind == "in_range":
        bad = [p for p, w in usable
               if any(not 0 <= x["ones"] <= x["n"] for x in w)]
        return (FAIL, f"count outside [0, N] at {bad}") if bad else (PASS, "")
    if kind == "window_independent":
        codes = [x["code"] for _, w in usable for x in w]
        if len(codes) < 2:
            return NOT_MEASURED, "fewer than two complete windows"
        return (PASS, "") if len(set(codes)) == 1 else (
            FAIL, f"consecutive windows gave codes {codes}")
    first = [w[0]["ones"] for _, w in usable]
    if kind == "polarity":
        if len(first) < 2:
            return NOT_MEASURED, "fewer than two sweep points"
        return (PASS, "") if first[-1] > first[0] else (
            FAIL, f"the count did not rise across the sweep: {first}")
    if kind == "monotonic":
        if len(first) < 2:
            return NOT_MEASURED, "fewer than two sweep points"
        ok = all(b >= a for a, b in zip(first, first[1:]))
        return (PASS, "") if ok else (FAIL, f"non-monotonic counts {first}")
    return NOT_MEASURED, f"no grader for {kind!r}"


def row_verdict(row: dict, criteria: List[dict]) -> str:
    if row.get("grading") == "UNBOUNDED_IN_INPUT" or not criteria:
        return UNBOUNDED
    words = [c.get("verdict") for c in criteria]
    if FAIL in words:
        return FAIL
    if all(w == PASS for w in words):
        return PASS
    return NOT_MEASURED


def arm_of(row: dict) -> str:
    crit = row.get("criteria") or []
    if crit and all(isinstance(c.get("pin"), str) and "quantity" in c
                    for c in crit):
        return "ngspice"
    return "d_cosim"


# ── the environment ─────────────────────────────────────────────────────────
class Engine:
    """Runs commands where the EDA tools are: a container, or this process."""

    def __init__(self, container: str):
        self.container = container

    def run(self, cmd: str, deadline_s: int = 0):
        import _container_exec as _ce
        import subprocess
        if not self.container:
            return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                  text=True)
        return _ce.run_in_container(self.container, cmd, deadline_s=deadline_s)

    def ngspice(self, deck: Path) -> Tuple[bool, Dict[str, float], str]:
        """The A4 sweep's own ngspice path: login shell, fork binary, meas."""
        if not self.container:
            cp = self.run(f"cd {shlex.quote(str(deck.parent))} && "
                          f"ngspice -b {shlex.quote(deck.name)} 2>&1")
            meas = {m.group(1): float(m.group(2)) for m in re.finditer(
                r"^\s*(a9_\w+)\s*=\s*([-+0-9.eE]+)", cp.stdout or "",
                re.MULTILINE)}
            return cp.returncode == 0, meas, cp.stdout or ""
        import analog_real_corner_sweep as _ars
        ok, meas, txt, _status = _ars._run_ngspice(
            self.container, str(deck), cwd=str(deck.parent),
            run_to_completion=True)
        return ok, {k: v for k, v in (meas or {}).items()
                    if k.startswith("a9_") and v is not None}, txt or ""

    def loadable(self, library: str) -> Tuple[bool, str]:
        """dlopen `library` by its absolute path, where the engine will."""
        probe = ("python3 -c 'import ctypes,sys; ctypes.CDLL(sys.argv[1])' "
                 f"{shlex.quote(library)}")
        cp = self.run(probe, deadline_s=60)
        # The image's login profile prints `[INFO]` banner lines on stdout;
        # the loader's own words are the last line on stderr.
        detail = [ln for ln in ((cp.stderr or "").strip().splitlines()
                                or (cp.stdout or "").strip().splitlines())
                  if not ln.startswith("[INFO]")]
        return cp.returncode == 0, detail[-1] if detail else ""


# ── the run ─────────────────────────────────────────────────────────────────
def _rel(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def run(project: Path, *, engine: Engine, libvvp: str, cycles: int = 16,
        windows: int = 2, launch: bool = True) -> Tuple[int, dict]:
    project = project.resolve()
    out_dir = _pl.mixed_signal_cosim_dir(project)
    l22_path, sha, plan = load_plan(project)
    rows = plan.get("cosim_scenarios")
    if not isinstance(rows, list) or not rows:
        gap = {
            "producer": TOOL, "schema_version": SCHEMA_VERSION,
            "l22": _rel(project, l22_path) if l22_path else None,
            "l22_sha256": sha,
            "cosim_status": plan.get("cosim_status") or (
                "PLAN_HAS_NO_COSIM_SCENARIOS" if plan
                else "NO_L22_VERIFICATION_PLAN"),
            "cosim_scenario_gaps": plan.get("cosim_scenario_gaps") or [],
            "reason": ("L22 declares no co-simulation scenario, so A9 has "
                       "nothing it may run; it is not verified"),
        }
        write_json(out_dir / GAP_FILE, gap)
        return _pc.RC_HONEST_GAP, gap

    blocks = sorted({b for r in rows for b in r.get("blocks") or []}
                    | {o["block"] for r in rows for o in r.get("observe") or []})
    arts = {b: _artefacts(project, b) for b in blocks}
    needs_dcosim = any(arm_of(r) == "d_cosim" for r in rows)
    vvp_ok, vvp_why = (engine.loadable(libvvp) if needs_dcosim and launch
                       else (False, "not probed"))
    env_refusals: List[str] = []
    results: List[dict] = []
    for row in rows:
        rid = str(row.get("id"))
        arm = arm_of(row)
        rdir = out_dir / rid
        rec: Dict[str, Any] = {"id": rid, "arm": arm, "l22_sha256": sha,
                               "grading": row.get("grading"),
                               "evidence": row.get("evidence") or []}
        missing = [b for b in (row.get("blocks") or [])
                   + [o["block"] for o in row.get("observe") or []]
                   if not arts.get(b)]
        crit = row.get("criteria") or []
        if missing:
            rec.update(verdict=NOT_MEASURED, reason=(
                f"no A3 netlist + testbench for {sorted(set(missing))}"))
            results.append(rec)
            continue
        if arm == "ngspice":
            deck, probes = ngspice_deck(project, row, arts, cycles)
            write_text(rdir / "deck.sp", deck)
            rec["deck"] = _rel(project, rdir / "deck.sp")
            if not launch:
                rec.update(verdict=NOT_MEASURED, reason="--no-launch")
                results.append(rec)
                continue
            ok, meas, log = engine.ngspice(rdir / "deck.sp")
            write_text(rdir / "ngspice.log", log)
            rec["log"] = _rel(project, rdir / "ngspice.log")
            graded = grade_bounds(row, probes, meas)
            rec["criteria"] = graded
            rec["verdict"] = row_verdict(row, graded)
            if not ok and rec["verdict"] == PASS:
                rec["verdict"] = NOT_MEASURED
            results.append(rec)
            continue
        # d_cosim arm
        resolution = [c for c in crit
                      if set(re.findall(r"[a-z]+", str(c.get("quantity") or "")
                                        .lower())) & _RESOLUTION_WORDS]
        block = (row.get("observe") or [{}])[0].get("block")
        osr = _block_spec(plan, block, _OVERSAMPLING_WORDS) if block else None
        order = _block_spec(plan, block, _ORDER_WORDS) if block else None
        window = osr.get("target") if osr else None
        stages = int(order.get("target")) if order and isinstance(
            order.get("target"), (int, float)) else 1
        if not isinstance(window, (int, float)) or not row.get("observe"):
            rec.update(verdict=NOT_MEASURED if crit else UNBOUNDED, reason=(
                "no declared oversampling ratio or observed digital pin for "
                "the observer window"))
            results.append(rec)
            continue
        vfile = rdir / "a9_observer.v"
        write_text(vfile, observer_verilog(int(window), stages))
        rec["observer"] = {"role": "instrument", "window": int(window),
                           "cascade_order": stages,
                           "source": _rel(project, vfile),
                           "derived_from": [s.get("name") for s in (osr, order)
                                            if s]}
        points = _points(row, arts[block])
        decks = []
        for label, clock_hz, volts in points:
            deck = dcosim_deck(project, row, arts, vvp=str(rdir / "a9_observer"),
                               libvvp=libvvp, windows=windows + 1,
                               window=int(window), clock_hz=clock_hz,
                               input_volts=volts)
            if deck is None:
                continue
            path = rdir / f"deck_{label}.sp"
            write_text(path, deck)
            decks.append((label, path))
        rec["decks"] = [_rel(project, p) for _, p in decks]
        graded = []
        for c in crit:
            c = dict(c)
            if c in resolution:
                c.update(verdict=NOT_MEASURED, reason=(
                    "a full-record ENOB/SNDR needs an IEEE 1241 sine fit; A9 "
                    "runs a few windows at transistor level, so this bound "
                    "stays with A4 / analog acceptance"))
            graded.append(c)
        if not launch:
            reason = "--no-launch"
        elif not vvp_ok:
            reason = (f"{_pc.ENV_REFUSED_TOKEN} libvvp is not loadable at "
                      f"{libvvp} ({vvp_why or 'no detail'}): this image's "
                      "iverilog was built without --enable-libvvp, so "
                      "ngspice d_cosim cannot run")
            env_refusals.append(reason)
        else:
            reason = ""
        if reason:
            for c in graded:
                c.setdefault("verdict", NOT_MEASURED)
                c.setdefault("reason", reason)
            rec.update(criteria=graded, verdict=row_verdict(row, graded),
                       reason=reason)
            results.append(rec)
            continue
        comp = engine.run(f"iverilog -o {shlex.quote(str(rdir / 'a9_observer'))}"
                          f" {shlex.quote(str(vfile))} 2>&1", deadline_s=300)
        if comp.returncode != 0:
            for c in graded:
                c.setdefault("verdict", NOT_MEASURED)
                c.setdefault("reason", "the observer did not compile")
            rec.update(criteria=graded, verdict=row_verdict(row, graded),
                       reason=(comp.stdout or "").strip()[-400:])
            results.append(rec)
            continue
        runs = []
        for label, path in decks:
            ok, _meas, log = engine.ngspice(path)
            write_text(path.with_suffix(".log"), log)
            runs.append((label, observer_windows(log)))
        rec["windows"] = {label: w for label, w in runs}
        for c in graded:
            if "verdict" in c:
                continue
            verdict, why = grade_structural(c.get("structural", ""), runs)
            c["verdict"] = verdict
            if why:
                c["reason"] = why
        rec.update(criteria=graded, verdict=row_verdict(row, graded))
        results.append(rec)

    report = {
        "producer": TOOL, "schema_version": SCHEMA_VERSION,
        "l22": _rel(project, l22_path), "l22_sha256": sha,
        "declared_ids": [str(r.get("id")) for r in rows],
        "engine": {"container": engine.container or None,
                   "libvvp": libvvp, "libvvp_loadable": vvp_ok,
                   "libvvp_probe": vvp_why},
        "scenarios": results,
        "counts": {w: sum(1 for r in results if r.get("verdict") == w)
                   for w in (PASS, FAIL, NOT_MEASURED, UNBOUNDED)},
    }
    write_json(out_dir / RESULTS, report)
    if env_refusals:
        return _pc.EX_ENV_REFUSED, report
    return _pc.RC_OK, report


def _points(row: dict, art: dict) -> List[Tuple[str, Optional[float],
                                               Optional[float]]]:
    """(label, clock Hz, input volts) for each run a d_cosim row needs."""
    stim = row.get("stimulus") or {}
    kind = stim.get("kind")
    if kind == "clock":
        scale = _UNIT_HZ.get(str(stim.get("unit") or "").lower())
        if scale:
            return [(f"f{i}", float(p) * scale, None)
                    for i, p in enumerate(stim.get("points") or [])]
    if kind == "dc_sweep" and stim.get("u_grid"):
        ref = _reference_pair(art, stim.get("normalised_to"))
        if ref:
            low, high = ref
            return [(f"u{i}", None, low + float(u) * (high - low))
                    for i, u in enumerate(stim["u_grid"])]
        return []
    return [("held", None, None)]


def _reference_pair(art: dict, name: Any) -> Optional[Tuple[float, float]]:
    """The (low, high) the testbench drives on the reference's pins."""
    words = {w for w in _tokens(name) if len(w) >= 3}
    volts = []
    for pin, node in art["pin_node"].items():
        if not any(w in pin.lower() for w in words):
            continue
        for el in art["elements"]:
            if el["name"][0].lower() == "v" and node in el["nodes"]:
                val = _spice_number(el["value"].split()[-1])
                if val is not None:
                    volts.append(val)
    if len(volts) != 2:
        return None
    return min(volts), max(volts)


def main(argv: Optional[List[str]] = None) -> int:
    import _eda_pin as _pin
    ap = _pc.ProducerArgumentParser(prog=TOOL, description=__doc__)
    ap.add_argument("project", type=Path)
    ap.add_argument("--container", default=None,
                    help="EDA container; '' runs the tools in this process")
    ap.add_argument("--libvvp", default=None)
    ap.add_argument("--cycles", type=int, default=16,
                    help="clock periods an analog-arm transient covers")
    ap.add_argument("--windows", type=int, default=2,
                    help="observer windows graded per d_cosim run (one more "
                         "is run and discarded)")
    ap.add_argument("--no-launch", action="store_true",
                    help="write decks and the results record, run nothing")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if not args.project.is_dir():
        print(f"{_pc.USAGE_ERROR_TOKEN} {args.project} is not a directory",
              file=sys.stderr)
        return _pc.EX_USAGE
    container = (args.container if args.container is not None
                 else os.environ.get("VIBEIC_ANALOG_CONTAINER")
                 or _pin.default_container_name())
    libvvp = args.libvvp or os.environ.get(LIBVVP_ENV) or LIBVVP_DEFAULT
    rc, report = run(args.project, engine=Engine(container), libvvp=libvvp,
                     cycles=args.cycles, windows=args.windows,
                     launch=not args.no_launch)
    if args.json:
        write_json(Path(args.json), {"rc": rc, **report})
    if rc == _pc.RC_HONEST_GAP:
        print(_pc.honest_gap_line(TOOL, report["reason"]), file=sys.stderr)
    for row in report.get("scenarios") or []:
        print(f"{row['id']:4} {row['arm']:8} {row.get('verdict')}"
              + (f" — {row['reason']}" if row.get("reason") else ""))
    if rc == _pc.EX_ENV_REFUSED:
        first = next(r["reason"] for r in report["scenarios"]
                     if str(r.get("reason", "")).startswith(
                         _pc.ENV_REFUSED_TOKEN))
        print(first, file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
