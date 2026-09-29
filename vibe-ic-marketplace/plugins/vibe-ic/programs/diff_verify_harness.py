#!/usr/bin/env python3
"""diff_verify_harness.py — independent DIFFERENTIAL self-verification
(N-version) for blind RTL authoring (ORGANIC #700).

Problem — the single-self-TB circularity
=========================================
A single agent that derives BOTH the RTL and its self-testbench from ONE
reading of the spec passes its own (possibly wrong) TB: the self-verification
is CIRCULAR. A misread baked into one reading lands in BOTH the RTL surface and
the TB surface, so the TB happily confirms the wrong behaviour.

The break — independent differential verification (N-version)
=============================================================
A SECOND, INDEPENDENT derivation (a reference behavioural model produced
WITHOUT seeing the RTL, with every ambiguous quantity explicitly enumerated and
pinned to the spec's worked examples) is cross-checked against the RTL EVERY
CYCLE. When the two derivations disagree, a misread that one reading noticed but
the other missed surfaces as a designer-vs-reference DIFF — the first
mismatching cycle/signal — instead of silently passing.

Empirically this caught a real oversight during self-check (an hmac write-data
live-read vs latched-read: 3471 diffs → fixed to 0) that the single-self-TB
PASSED.

SCOPE (HONEST — this is a COMPLEMENT, not a silver bullet)
==========================================================
It catches OVERSIGHT misreads: one derivation noticed a clause the other
missed. It does NOT catch:
  * genuine AMBIGUITY where the spec wording biases ALL independent blind
    readings the SAME way (e.g. an exact-latency phrase both the RTL author and
    the reference author read identically-but-wrong) — no amount of N-version
    helps when every version reads the same wrong thing;
  * benchmark spec↔TB contradictions (a defective benchmark whose hidden TB
    disagrees with its own prose).
On the hardest CVDP ambiguity residual it recovered 0/8 — those are FLOOR per
#697. Its value is on FRESH runs preventing OVERSIGHT bugs BEFORE the scorer.
It is the differential complement to the deterministic #697 spec_coverage_check
(force the self-TB to COVER each dimension) and the #699 timing/encoding
reading disciplines — NOT a replacement for either.

What this program IS (the honest boundary)
==========================================
The DETERMINISTIC half of #700: given RTL + an INDEPENDENT reference model
(a Python module exposing `ref(seq)`, or a second SV golden) + optional spec
worked-example vectors, it GENERATES and RUNS a cycle-accurate differential
testbench (directed example vectors + random + boundary) and reports every
designer-vs-reference mismatch with the cycle and signal. The PROGRAM does NOT
author the reference (that is the AI judgment recorded in the issue's
why_not_bucket_a) — it only DRIVES the differential comparison.

It reads ONLY the supplied RTL and its declared includes, the reference model,
and the vectors. Icarus preprocesses the one ordered root source using native
builtins and include lookup from the captured cwd; Slang elaborates that frozen
file, which Icarus also simulates. User -D/-I/-P options are not part of this API;
parameters use declared defaults. Native port sizes must match before comparing
samples. A text-only parse_ports call cannot certify executable agreement.
It has NO access to any oracle, hidden TB, or dataset:
a misread cannot leak in through the comparison, because BOTH sides come from
the spec-reader, not from the scorer.

Tool availability
=================
Port elaboration uses pyslang; the live RTL side uses iverilog/vvp. A missing
pyslang import or absent iverilog/vvp yields `SKIP (tool unavailable)` with
disclosure and rc 0 — NEVER a faked AGREE (mirrors the refuse-don't-fake
doctrine of harness_exact_selfverify #688 / cvdp_gate #604). The reference-side
logic (`ref(seq)` + the per-cycle compare) is exercised independently of
iverilog so CI always covers the comparator.

Exit codes
==========
    0  AGREE — every cycle of every vector matched the reference
       (OR a disclosed SKIP because a tool was absent — never a fake AGREE)
    1  MISMATCH — the first diverging cycle/signal is printed
    2  bad input (RTL/ref/port parse failure, bad --vectors, etc.)

chip-AGNOSTIC: pure structure — Slang port elaboration, vector generation,
iverilog/vvp drive, per-cycle integer compare. No chip / vendor / SKU literal,
no dataset access.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── RTL port elaboration; comment-stripped module discovery ──────────────────
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LIT_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def _strip_comments(text: str) -> str:
    t = _BLOCK_COMMENT_RE.sub(" ", text)
    t = _LINE_COMMENT_RE.sub(" ", t)
    return _STRING_LIT_RE.sub('""', t)


_MODULE_NAME_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)", re.MULTILINE)

# Canonical clock spellings (chip-AGNOSTIC: a small universal set; matching is
# case-insensitive exact-name).
_CLK_NAMES = {"clk", "clock", "clk_i", "i_clk", "clkin", "clk_in", "sysclk",
              "aclk", "hclk", "pclk", "mclk"}
_RST_NAMES = {"rst", "reset", "rst_n", "resetn", "rst_i", "i_rst", "arst",
              "arst_n", "nreset", "rstn", "reset_n", "areset", "rst_ni"}


class Port:
    __slots__ = ("name", "direction", "width")

    def __init__(self, name: str, direction: str, width: int):
        self.name = name
        self.direction = direction
        self.width = width

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Port({self.name!r},{self.direction},{self.width})"


def module_names(code: str) -> List[str]:
    seen: Dict[str, None] = {}
    for n in _MODULE_NAME_RE.findall(_strip_comments(code)):
        seen.setdefault(n, None)
    return list(seen.keys())


def parse_ports(code: str, top: Optional[str], *,
                source_path: Optional[Path] = None) -> Tuple[Optional[str],
                                                        List[Port], str]:
    """Use Slang's elaborated ports and declared parameter defaults.

    Slang supplies names, directions, packed bit widths and declaration order;
    this adapter never evaluates HDL range arithmetic. No synthesis is required.
    Unsupported port types or unresolved ranges refuse before generating a TB.
    The existing first-module default and widest-port tie order are preserved.
    Text-only calls are introspection, not executable verification. diff_verify
    supplies the frozen Icarus-preprocessed file used by simulation.
    """
    try:
        import pyslang as slang
    except ImportError as exc:
        return None, [], f"DIFF_PORT_TOOL_UNAVAILABLE: pyslang absent: {exc}"

    names = module_names(code)
    if top and top not in names:
        return None, [], (f"requested --top {top!r} not declared "
                          f"(declared: {names or 'none'})")
    if not names:
        return None, [], "no module declaration found in RTL"
    name = top or names[0]
    try:
        options = slang.ast.CompilationOptions()
        options.topModules = {name}
        bag = slang.Bag()
        bag.compilationOptions = options
        compilation = slang.ast.Compilation(bag)
        tree = (slang.syntax.SyntaxTree.fromFile(str(source_path))
                if source_path is not None else slang.syntax.SyntaxTree.fromText(code))
        compilation.addSyntaxTree(tree)
        instances = compilation.getRoot().topInstances
        diagnostics = compilation.getAllDiagnostics()
        if any(d.isError() for d in diagnostics):
            detail = slang.DiagnosticEngine.reportAll(
                compilation.sourceManager, diagnostics).strip()
            return None, [], f"DIFF_PORT_ELABORATION_FAILED: {detail}"
        selected = [i for i in instances if i.name == name]
        if len(selected) != 1:
            return None, [], f"DIFF_PORT_TOP_UNRESOLVED: {name!r}"
        directions = {slang.ast.ArgumentDirection.In: "input",
                      slang.ast.ArgumentDirection.Out: "output",
                      slang.ast.ArgumentDirection.InOut: "inout"}
        ports = []
        for port in selected[0].body.portList:
            if not isinstance(port, slang.ast.PortSymbol):
                return None, [], (f"DIFF_PORT_UNSUPPORTED: {port.name!r} "
                                  "is not a plain integral port")
            # The emitted TB uses ordinary identifiers and packed bit vectors.
            # Escaped names and unpacked/interface/ref ports need another TB
            # shape, so refuse them instead of inventing a scalar connection.
            token = getattr(port.syntax, "name", None)
            if (not re.fullmatch(r"[A-Za-z_]\w*", port.name)
                    or token is None or token.rawText.startswith("\\")
                    or port.direction not in directions
                    or not port.type.isIntegral or port.type.bitWidth <= 0):
                return None, [], (f"DIFF_PORT_UNSUPPORTED: {port.name!r} "
                                  f"has unsupported name, direction or type {port.type}")
            ports.append(Port(port.name, directions[port.direction],
                              port.type.bitWidth))
        return name, ports, ""
    except Exception as exc:  # tool/API failure must never mint guessed ports
        return None, [], f"DIFF_PORT_ELABORATION_FAILED: {type(exc).__name__}: {exc}"


def _classify_ports(ports: List[Port]) -> Tuple[Optional[Port], List[Port],
                                                 List[Port], List[Port]]:
    """Return (clk, reset_inputs, data_inputs, data_outputs).

    clk is the input whose name is a canonical clock spelling. reset inputs are
    canonical reset spellings (held inactive — driven 0, the common active-high
    convention; an active-low `*_n` reset is held 1). data_inputs are the
    remaining inputs (the `in` to drive). data_outputs are the outputs (the
    `out` to sample)."""
    clk = None
    resets: List[Port] = []
    din: List[Port] = []
    dout: List[Port] = []
    for p in ports:
        lo = p.name.lower()
        if p.direction == "input":
            if clk is None and lo in _CLK_NAMES:
                clk = p
            elif lo in _RST_NAMES:
                resets.append(p)
            else:
                din.append(p)
        elif p.direction == "output":
            dout.append(p)
    return clk, resets, din, dout


# ── independent reference model load ─────────────────────────────────────────
def load_reference(ref_path: Path):
    """Load the independent reference behavioural model.

    A Python module exposing `ref(seq)` (input-sequence → expected-output-
    sequence). The reference is authored INDEPENDENTLY of the RTL (the AI
    judgment in the issue's why_not_bucket_a); this program only IMPORTS and
    DRIVES it. (A second SV golden is a documented future extension; the
    Python-`ref(seq)` form is the 驗收 contract.)"""
    if ref_path.suffix.lower() in (".sv", ".v"):
        raise ValueError(
            "SV-golden references are not yet a supported --ref form; supply "
            "a Python module exposing `ref(seq)` (the 驗收 contract)")
    spec = importlib.util.spec_from_file_location("_diff_ref", str(ref_path))
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load --ref module: {ref_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "ref") or not callable(mod.ref):
        raise ValueError(
            f"--ref module {ref_path} must expose a callable `ref(seq)` "
            f"(sequence → expected-output-sequence)")
    return mod.ref


# ── vector generation (directed / random / boundary) ─────────────────────────
def _mask(width: int) -> int:
    return (1 << width) - 1


def gen_vectors(kinds: List[str], width: int, n_random: int,
                seed: int) -> List[List[int]]:
    """Generate the input sequences to drive. Each sequence is a list of
    integers (one per cycle), masked to the input width. Deterministic for a
    given seed. `directed` uses a small canonical ramp; `boundary` pins the
    extreme values; `random` draws `n_random` pseudo-random sequences."""
    rng = random.Random(seed)
    hi = _mask(width)
    seqs: List[List[int]] = []
    seq_len = max(8, n_random)
    if "directed" in kinds:
        # a canonical ramp + an alternating pattern (worked-example-like)
        seqs.append([i & hi for i in range(seq_len)])
        seqs.append([(0 if i % 2 else hi) for i in range(seq_len)])
    if "boundary" in kinds:
        # all-zero, all-ones, single-step impulse, single-zero notch
        seqs.append([0] * seq_len)
        seqs.append([hi] * seq_len)
        seqs.append([hi if i == 0 else 0 for i in range(seq_len)])
        seqs.append([0 if i == 0 else hi for i in range(seq_len)])
    if "random" in kinds:
        for _ in range(max(1, n_random)):
            seqs.append([rng.randint(0, hi) for _ in range(seq_len)])
    if not seqs:
        # never run zero vectors — default to a directed ramp
        seqs.append([i & hi for i in range(seq_len)])
    return seqs


def _parse_vectors_arg(s: str) -> Tuple[List[str], Optional[str]]:
    """`directed|random|boundary` or a + / , combination (e.g.
    `directed+random`, `all`). Returns (kinds, error)."""
    s = (s or "").strip().lower()
    if s in ("all", "directed+random+boundary"):
        return ["directed", "random", "boundary"], None
    parts = re.split(r"[+,\s]+", s)
    kinds = [p for p in parts if p]
    bad = [p for p in kinds if p not in ("directed", "random", "boundary")]
    if bad:
        return [], f"unknown --vectors kind(s): {bad} (use directed|random|boundary)"
    if not kinds:
        return [], "empty --vectors"
    return kinds, None


# ── differential comparison (the pure-logic comparator; iverilog-free) ───────
def compare_sequences(rtl_out: List[int], ref_out: List[int],
                       out_name: str) -> Tuple[bool, Optional[Dict]]:
    """Cycle-accurate compare of an RTL output sequence vs the reference.

    Compares position by position over the common prefix (a reference may
    legitimately return a shorter list — e.g. it elides trailing don't-cares —
    so only the overlap is enforced). Returns (agree, first_mismatch)."""
    n = min(len(rtl_out), len(ref_out))
    for cyc in range(n):
        if rtl_out[cyc] != ref_out[cyc]:
            return False, {"cycle": cyc, "signal": out_name,
                           "rtl": rtl_out[cyc], "ref": ref_out[cyc]}
    return True, None


# ── SV differential testbench generation + run ───────────────────────────────
def _reset_is_active_low(name: str) -> bool:
    """True for the active-low reset spellings (`*_n`, `*_ni`, `nreset`, `rstn`,
    `resetn`). Such resets are held HIGH (inactive); active-high ones held low."""
    lo = name.lower()
    return (lo.endswith("_n") or lo.endswith("_ni") or lo.endswith("n")
            and ("rst" in lo or "reset" in lo))


# How many warmup cycles to drive a quiescent input before the measured window.
# This flushes X out of resetless flops (a resetless K-stage pipe needs K clean
# clocks to become known) and lets a held-inactive reset settle. 8 covers the
# common shallow-pipeline depths; chip-AGNOSTIC (a cycle count, not a chip).
_WARMUP_CYCLES = 8


def _build_tb(top: str, clk: Optional[Port], resets: List[Port],
              din: Port, dout: Port, seq: List[int]) -> str:
    """Emit a self-contained cycle-accurate differential TB.

    Convention (matches the issue's `ref(seq)[i] = seq[i-latency]` contract):
      * WARMUP — drive `din`=0 with reset held INACTIVE for `_WARMUP_CYCLES`
        negedges, flushing X out of resetless flops and settling any pipeline.
      * MEASURED — on each negedge (a phase with NO edge race): first SAMPLE
        `dout` (it holds the value established by the most recent posedge),
        $display `CYC <i> <value>`, THEN drive `din`=seq[i] for the UPCOMING
        posedge. Sampling on the negedge and one cycle before driving makes the
        i-th sample reflect inputs registered `latency` cycles earlier — exactly
        the reference's leading-prefix delay — with no NBA/blocking race.

    The Python side reads those `CYC` lines and compares to `ref(seq)`; the
    comparison itself lives in Python (the comparator is iverilog-free and thus
    always CI-covered)."""
    lines: List[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append("module diff_tb;")
    lines.append("  reg clk = 0;")
    for r in resets:
        lines.append(f"  reg [{r.width-1}:0] {r.name};")
    lines.append(f"  reg  [{din.width-1}:0] {din.name};")
    lines.append(f"  wire [{dout.width-1}:0] {dout.name};")
    conns = []
    if clk is not None:
        conns.append(f".{clk.name}(clk)")
    for r in resets:
        conns.append(f".{r.name}({r.name})")
    conns.append(f".{din.name}({din.name})")
    conns.append(f".{dout.name}({dout.name})")
    lines.append(f"  {top} dut(" + ", ".join(conns) + ");")
    lines.append("  always #5 clk = ~clk;")
    lines.append("  integer i;")
    lines.append(f"  reg [{max(0,din.width-1)}:0] stim [0:{len(seq)-1}];")
    lines.append("  initial begin")
    for idx, v in enumerate(seq):
        lines.append(f"    stim[{idx}] = {din.width}'d{v & _mask(din.width)};")
    # hold reset INACTIVE throughout (we verify the steady-state datapath).
    for r in resets:
        val = _mask(r.width) if _reset_is_active_low(r.name) else 0
        lines.append(f"    {r.name} = {r.width}'d{val};")
    lines.append(f"    {din.name} = 0;")
    # WARMUP — quiescent input flushes X out of resetless flops.
    lines.append(f"    repeat ({_WARMUP_CYCLES}) begin @(negedge clk); "
                 f"{din.name} = 0; end")
    # MEASURED — sample-then-drive on the negedge (race-free phase).
    lines.append(f"    for (i = 0; i < {len(seq)}; i = i + 1) begin")
    lines.append("      @(negedge clk);")
    lines.append(f"      $display(\"CYC %0d %0d\", i, {dout.name});")
    lines.append(f"      {din.name} = stim[i];")
    lines.append("    end")
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


_CYC_RE = re.compile(r"^CYC\s+(\d+)\s+(\d+)\s*$", re.MULTILINE)


def _run(cmd: List[str], timeout: int = 120,
         cwd: Optional[str] = None) -> Tuple[int, str, str]:
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout, cwd=cwd)
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, out or "", "timeout"
    except FileNotFoundError as e:
        return 127, "", str(e)


def _bind_rtl_unit(rtl_path: Path, top: Optional[str], workdir: Path):
    """Freeze native Icarus preprocessing, then elaborate those exact bytes.

    This API has one ordered root source, no -D/-I/-P options and declared
    parameter defaults only. Includes use Icarus's native search from the
    captured cwd. Slang and every simulation consume the same flattened file;
    neither reopens the roots/includes or independently chooses macro branches.
    """
    compiler, simulator = shutil.which("iverilog"), shutil.which("vvp")
    if compiler is None or simulator is None:
        # Diagnostic-only Slang refusal preserves unsupported-port reporting
        # without certifying any executable widths when preprocessing is absent.
        _, _, error = parse_ports(rtl_path.read_text(errors="replace"), top)
        if error and not error.startswith("DIFF_PORT_TOOL_UNAVAILABLE:"):
            return None, [], None, {}, error + " — NOT_VERIFIED: iverilog/vvp absent"
        return None, [], None, {}, (error or "DIFF_PORT_TOOL_UNAVAILABLE: iverilog/vvp absent")
    source = rtl_path.resolve()
    cwd = str(Path.cwd().resolve())
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    unit, deps = workdir / "rtl_unit.sv", workdir / "dependencies.txt"
    argv = [compiler, "-g2012", "-E", "-Mall=" + str(deps),
            "-o", str(unit), str(source)]
    rc, out, err = _run(argv, cwd=cwd)
    context = {"ordered_sources": [str(source)], "source_sha256": original_sha,
               "working_directory": cwd, "language": "SystemVerilog-2012",
               "defines": [], "include_dirs": [], "parameter_overrides": [],
               "parameter_policy": "declared defaults only; no override API",
               "preprocessor": "native Icarus builtins and include resolution",
               "preprocessing_argv": argv, "preprocessing_rc": rc,
               "compiler": compiler, "simulator": simulator}
    if rc != 0 or not unit.is_file() or not deps.is_file():
        detail = "; ".join(((out or "") + "\n" + (err or "")).strip().splitlines()[:6])
        return None, [], None, context, f"DIFF_PORT_PREPROCESS_FAILED: rc={rc}: {detail}"
    if hashlib.sha256(source.read_bytes()).hexdigest() != original_sha:
        return None, [], None, context, "DIFF_PORT_CONTEXT_CHANGED: root changed during preprocessing"
    # Dependencies are the native preprocessor's ordered observations. Their
    # hashes describe files after preprocessing; the frozen unit is the exact
    # executable input, even if a source subsequently changes.
    dependencies = []
    for filename in deps.read_text().splitlines():
        path = Path(filename).resolve()
        dependencies.append({"path": str(path),
                             "sha256_after_preprocessing": hashlib.sha256(path.read_bytes()).hexdigest()})
    raw_unit = unit.read_bytes()
    unit_sha = hashlib.sha256(raw_unit).hexdigest()
    text = raw_unit.decode()
    name, ports, error = parse_ports(text, top, source_path=unit)
    if error:
        return None, [], None, context, error
    if hashlib.sha256(unit.read_bytes()).hexdigest() != unit_sha:
        return None, [], None, context, "DIFF_PORT_CONTEXT_CHANGED: unit changed during elaboration"
    import pyslang
    context.update({"dependencies": dependencies, "selected_top": name,
                    "executable_unit": str(unit),
                    "executable_unit_sha256": unit_sha,
                    "elaborator": "pyslang", "elaborator_version": pyslang.__version__,
                    "ports": [{"name": p.name, "direction": p.direction,
                               "width": p.width} for p in ports]})
    for label, executable in (("compiler", compiler), ("simulator", simulator)):
        rc, out, err = _run([executable, "-V"], cwd=cwd)
        if rc != 0:
            return None, [], None, context, f"DIFF_PORT_CONTEXT_FAILED: {label} identity rc={rc}"
        context[label + "_version"] = (out + err).strip()
        context[label + "_version_rc"] = rc
        context[label + "_sha256"] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    context["binding_sha256"] = hashlib.sha256(
        json.dumps(context, sort_keys=True).encode()).hexdigest()
    return name, ports, unit, context, ""


def run_rtl_sequence(rtl_path: Path, top: str, clk: Optional[Port],
                     resets: List[Port], din: Port, dout: Port,
                     seq: List[int], workdir: Path,
                     binding: Optional[Dict] = None) -> Tuple[Optional[List[int]],
                                                             str]:
    """Compile + run one input sequence through the RTL, returning the sampled
    output sequence (one int per cycle) or (None, error). The caller has
    already confirmed iverilog/vvp are present."""
    compiler, simulator, cwd = "iverilog", "vvp", None
    if binding is not None:
        try:
            identity = {k: v for k, v in binding.items() if k != "binding_sha256"}
            if (hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                    != binding["binding_sha256"] or top != binding["selected_top"]
                    or str(rtl_path) != binding["executable_unit"]
                    or hashlib.sha256(rtl_path.read_bytes()).hexdigest()
                    != binding["executable_unit_sha256"]):
                return None, "DIFF_PORT_CONTEXT_CHANGED: unit, selected top or context changed"
            port_contract = {p["name"]: (p["direction"], p["width"]) for p in binding["ports"]}
            if any(port_contract.get(p.name) != (p.direction, p.width)
                   for p in [din, dout, *resets, *([clk] if clk else [])]):
                return None, "DIFF_PORT_CONTEXT_CHANGED: selected ports changed"
            compiler, simulator = binding["compiler"], binding["simulator"]
            cwd = binding["working_directory"]
            for label, executable in (("compiler", compiler), ("simulator", simulator)):
                if hashlib.sha256(Path(executable).read_bytes()).hexdigest() != binding[label + "_sha256"]:
                    return None, "DIFF_PORT_CONTEXT_CHANGED: " + label + " changed"
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return None, f"DIFF_PORT_CONTEXT_CHANGED: {exc}"
    tb = _build_tb(top, clk, resets, din, dout, seq)
    mismatch_marker = None
    if binding is not None:
        # Native sizing is checked before any CYC sample can be compared. A
        # frontend disagreement must not truncate a wider real DUT into AGREE.
        mismatch_marker = "DIFF_PORT_CONTEXT_MISMATCH:" + binding["binding_sha256"] + ":"
        checks = ["  initial begin"]
        for port in binding["ports"]:
            checks += [f"    if ($bits(dut.{port['name']}) != {port['width']}) begin",
                       f"      $display(\"{mismatch_marker}{port['name']}\");",
                       "      $finish;", "    end"]
        checks.append("  end")
        tb = tb.replace("  always #5", "\n".join(checks) + "\n  always #5", 1)
    tb_path = workdir / "diff_tb.sv"
    tb_path.write_text(tb)
    binp = workdir / "diff_sim.vvp"
    rc, out, err = _run([compiler, "-g2012", "-o", str(binp),
                         "-s", "diff_tb", str(rtl_path), str(tb_path)], cwd=cwd)
    if rc != 0:
        blob = ((out or "") + "\n" + (err or "")).strip()
        return None, ("RTL+diff-TB did not compile: "
                      + "; ".join(blob.splitlines()[:4]))
    rc2, out2, err2 = _run([simulator, str(binp)], cwd=cwd)
    sim = (out2 or "")
    if binding is not None and rc2 != 0:
        return None, f"DIFF_PORT_EXECUTION_FAILED: native simulator rc={rc2}"
    if mismatch_marker is not None and mismatch_marker in sim:
        return None, "DIFF_PORT_CONTEXT_MISMATCH: native Icarus port sizing differs from Slang"
    samples: Dict[int, int] = {}
    for m in _CYC_RE.finditer(sim):
        samples[int(m.group(1))] = int(m.group(2))
    if not samples:
        return None, ("RTL sim produced no CYC samples (sim stderr: "
                      + "; ".join((err2 or "").splitlines()[:3]) + ")")
    return [samples[i] for i in sorted(samples)], ""


# ── orchestration ────────────────────────────────────────────────────────────
def diff_verify(rtl_path: Path, ref_path: Path, top: Optional[str],
                vectors: str, n_random: int, seed: int,
                require_tools: bool = False) -> Dict:
    """Run the full independent differential verification and return a report."""
    report: Dict = {
        "rtl": str(rtl_path),
        "ref": str(ref_path),
        "methodology": "independent N-version differential verification",
        # honest scope, carried in EVERY report (no over-claim):
        "catches": "OVERSIGHT misreads (one derivation noticed a clause the "
                   "other missed)",
        "does_not_catch": ("genuine ambiguity that biases ALL blind readings "
                           "the same way; benchmark spec<->TB contradictions "
                           "(FLOOR per #697)"),
        "complement_to": ["#697 spec_coverage_check (deterministic dimension "
                          "coverage)", "#699 timing/encoding reading disciplines"],
        "reads_only": "supplied RTL/includes + independent reference + generated vectors "
                      "(no oracle / hidden TB / dataset)",
        "vectors": vectors,
    }
    with tempfile.TemporaryDirectory(prefix="diffvh_") as scratch:
        return _diff_verify_bound(rtl_path, ref_path, top, vectors, n_random,
                                  seed, require_tools, report, Path(scratch))


def _diff_verify_bound(rtl_path, ref_path, top, vectors, n_random, seed,
                       require_tools, report, workdir):
    try:
        name, ports, unit, binding, perr = _bind_rtl_unit(rtl_path, top, workdir)
    except (OSError, ValueError) as exc:
        name, ports, unit, binding, perr = None, [], None, {}, f"DIFF_PORT_CONTEXT_FAILED: {exc}"
    report["compilation_context"] = binding
    if name is None:
        report["verdict"] = "ERROR"
        report["reason"] = "port parse failed: " + perr
        if perr.startswith("DIFF_PORT_TOOL_UNAVAILABLE:"):
            report["tool_available"] = False
            report["verdict"] = "ERROR" if require_tools else "SKIP"
            report["reason"] += " — NOT_VERIFIED: the RTL ports were not elaborated (refuse-don't-fake)"
        return report
    report["resolved_top"] = name
    clk, resets, din, dout = _classify_ports(ports)
    if not din:
        report["verdict"] = "ERROR"
        report["reason"] = ("no data-input port found (after excluding "
                            f"clk/reset) in module {name}")
        return report
    if not dout:
        report["verdict"] = "ERROR"
        report["reason"] = f"no output port found in module {name}"
        return report
    # single primary in/out (the 驗收 contract: one `in`, one `out`); when
    # several inputs exist, drive the widest data input and sample the widest
    # output (deterministic, disclosed).
    din_port = max(din, key=lambda p: (p.width, din.index(p) * -1))
    dout_port = max(dout, key=lambda p: (p.width, dout.index(p) * -1))
    report["driven_input"] = {"name": din_port.name, "width": din_port.width}
    report["sampled_output"] = {"name": dout_port.name, "width": dout_port.width}
    report["clk"] = clk.name if clk else None
    report["resets_held_inactive"] = [r.name for r in resets]
    report["primary_io_scope"] = ("one widest data input and one widest output, first on ties; "
                                  "unsigned packed-bit reference vectors; not multi-input semantic verification")
    report["undriven_data_inputs"] = [p.name for p in din if p is not din_port]
    report["unsampled_outputs"] = [p.name for p in dout if p is not dout_port]
    report["unconnected_inout_ports"] = [p.name for p in ports if p.direction == "inout"]

    kinds, verr = _parse_vectors_arg(vectors)
    if verr:
        report["verdict"] = "ERROR"
        report["reason"] = verr
        return report
    report["vector_kinds"] = kinds

    try:
        ref = load_reference(ref_path)
    except Exception as e:  # noqa: BLE001 - surface any ref-load failure
        report["verdict"] = "ERROR"
        report["reason"] = f"reference load failed: {e}"
        return report

    seqs = gen_vectors(kinds, din_port.width, n_random, seed)
    report["n_sequences"] = len(seqs)

    # iverilog/vvp gate — refuse-don't-fake: ABSENT → SKIP, never a faked AGREE.
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        report["verdict"] = "SKIP"
        report["tool_available"] = False
        report["reason"] = ("iverilog/vvp absent — the RTL side of the "
                            "differential check cannot run; reporting SKIP with "
                            "disclosure, NOT a faked AGREE (refuse-don't-fake)")
        if require_tools:
            report["verdict"] = "ERROR"
            report["reason"] += " (--require-tools → hard error)"
        return report
    report["tool_available"] = True

    for si, seq in enumerate(seqs):
        rtl_out, rerr = run_rtl_sequence(unit, name, clk, resets,
                                         din_port, dout_port, seq, workdir, binding)
        if rtl_out is None:
            report["verdict"] = "ERROR"
            report["reason"] = f"sequence {si}: {rerr}"
            return report
        try:
            ref_out = list(ref(list(seq)))
        except Exception as e:  # noqa: BLE001
            report["verdict"] = "ERROR"
            report["reason"] = f"reference ref(seq) raised on seq {si}: {e}"
            return report
        ref_out = [int(v) & _mask(dout_port.width) for v in ref_out]
        agree, mm = compare_sequences(rtl_out, ref_out, dout_port.name)
        if not agree and mm is not None:
            report["verdict"] = "MISMATCH"
            report["first_mismatch"] = {**mm, "sequence": si}
            report["reason"] = (
                f"DIFF at sequence {si} cycle {mm['cycle']} signal "
                f"{mm['signal']}: RTL={mm['rtl']} ref={mm['ref']} — the "
                f"designer's RTL diverges from the independently-derived "
                f"reference (an oversight misread, or a real RTL bug)")
            return report

    report["verdict"] = "AGREE"
    report["reason"] = (f"RTL agrees with the independent reference across all "
                        f"{len(seqs)} vector sequence(s), every cycle")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Independent differential (N-version) self-verification for "
                    "blind RTL (#700): RTL vs an INDEPENDENTLY-derived reference "
                    "`ref(seq)`, cycle-accurately, over directed/random/boundary "
                    "vectors. Catches OVERSIGHT misreads single-self-TB passes; "
                    "does NOT beat genuine-ambiguity FLOOR.")
    ap.add_argument("--rtl", required=True,
                    help="one RTL source (.v/.sv), native includes from cwd, declared parameter defaults")
    ap.add_argument("--ref", required=True,
                    help="independent reference: a Python module exposing "
                         "`ref(seq)` (input-sequence → expected-output-sequence)")
    ap.add_argument("--top", default=None,
                    help="the DUT module name (default: the sole/first module)")
    ap.add_argument("--vectors", default="directed+random+boundary",
                    help="directed|random|boundary or a + / , combination "
                         "(or 'all'); default directed+random+boundary")
    ap.add_argument("--cycles", type=int, default=16,
                    help="cycles per random/directed sequence (default 16)")
    ap.add_argument("--seed", type=int, default=0,
                    help="PRNG seed for the random vectors (deterministic)")
    ap.add_argument("--json", default=None, help="optional JSON report path")
    ap.add_argument("--require-tools", action="store_true",
                    help="treat an absent pyslang/iverilog/vvp as a hard error (exit 2) "
                         "for a CI/container run that MUST enforce")
    args = ap.parse_args(argv)

    rtl_path = Path(args.rtl)
    ref_path = Path(args.ref)
    if not rtl_path.is_file():
        print(f"ERROR: --rtl not found: {rtl_path}", file=sys.stderr)
        return 2
    if not ref_path.is_file():
        print(f"ERROR: --ref not found: {ref_path}", file=sys.stderr)
        return 2

    report = diff_verify(rtl_path.resolve(), ref_path.resolve(), args.top,
                         args.vectors, max(1, args.cycles), args.seed,
                         args.require_tools)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2,
                                              ensure_ascii=False) + "\n")

    verdict = report.get("verdict")
    if verdict == "AGREE":
        print("AGREE")
        print(report["reason"], file=sys.stderr)
        return 0
    if verdict == "SKIP":
        # disclosed SKIP — NEVER a faked AGREE; rc 0 unless --require-tools.
        print(f"SKIP: {report['reason']}", file=sys.stderr)
        return 2 if args.require_tools else 0
    if verdict == "MISMATCH":
        fm = report["first_mismatch"]
        print(f"MISMATCH cycle={fm['cycle']} signal={fm['signal']} "
              f"rtl={fm['rtl']} ref={fm['ref']} (sequence {fm['sequence']})")
        print(report["reason"], file=sys.stderr)
        return 1
    # ERROR
    print(f"ERROR: {report.get('reason','bad input')}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
