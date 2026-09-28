#!/usr/bin/env python3
"""isa_suite_producer.py — run the pinned public ISA suites on a processor
design's own RTL and judge each program by the suite's OWN criterion.

ENFORCEMENT: producer. It executes the declared L10 cases that ask for an ISA
instruction-set suite and publishes their execution rows, a transcript carrying
`VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction covered=N total=M` for
`instruction_coverage_measure`, and a receipt. The Step-4 gates judge those
rows; this program never gates a step itself.

WHY THIS EXISTS — the measurement, not a theory.

MEASURED (lane isaprod, 2026-09-28, vibeic-eda 0.3.84, a reused bit-serial
RV32I core behind an 8-bit SRAM port): the design's own verification plan asks
for "the whole RV32I instruction set, 100% PASS (RISC-V Compliance suite)" and
for Zifencei. The flow had no producer, so the cases sat at the substance floor.
Running riscv-arch-test 3.9.1 against Spike found a real defect in the supplied
RTL that the three reset oracles and riscv-tests (which never uses x31 and is
normally run on zero-initialised memory) could not see: 9/40 signatures matched
on the supplied RTL, 40/40 with the upstream one-line fix.

WHAT IT DOES, step by step (each step is a refusal, never a default):

  1. LOCK.     `isa_suites.lock.json` pins each suite by commit sha, tarball
               sha256 and the sha256 of every file compiled. A tarball or a
               file that does not match is NOT_MEASURED, never used.
  2. ACQUIRE.  The pinned tarball is fetched at run time (policy
               `external_suite_fetch`, owner ruling 2026-09-28: allowed) and
               verified; no network is NOT_MEASURED. Never a benchmark dir.
  3. ENV.      A CSR-free env (`riscv_test.h`, `model_test.h`) and a `link.ld`
               at the reset vector, written by this program from the
               declaration (ISA units, memsize, SRAM protocol, register-file
               storage) and the L3-derived reset vector. Nothing is copied
               from any harness.
  4. BUILD.    gcc `-mno-relax`, then an objdump GUARD: no 16-bit instruction
               on a core that declares no C, no CSR/ecall/ebreak in a program,
               and the image size against the program area.
  5. REFERENCE Spike on the same source (linked at Spike's DRAM base), with an
               instruction budget and a deadline; a Spike timeout or a missing
               signature is NOT_MEASURED ("noref").
  6. DUT.      Verilator on the UNMODIFIED staged RTL. The only change for the
               full suite is the memsize PARAMETER (derived: the next power of
               two that holds the largest program). The testbench SRAM is a
               plain array powered up NON-ZERO (0xFF and 0xA5 arms); halt and
               signature are taken by watching the SRAM write port; each
               program has a cycle cap, and a hang is a FAIL.
  7. JUDGE.    By each suite's own criterion: a signature equal to Spike's
               word for word (the first differing word is named), or
               `tohost == 1`. A case is PASS only if every program of its set
               passes on every power-up arm.
  8. SUBSET.   Every program that fits the program area at the DECLARED
               memsize also runs there, and is reported as its own row.
  9. ARMS.     When a disclosed reused-IP erratum changed the staged RTL
               (`reused_ip_erratum`), the unmodified input RTL is run as arm A
               and kept as evidence beside the verdict arm.
 10. LABEL.    Every number carries where it was measured: "full suite on the
               same RTL at MEMSIZE=<n> (parameter only); delivered-size subset
               at memsize <m>".

THE DENOMINATOR is derived, never a constant for a chip (owner ruling
2026-09-28, 3): the base ISA's instruction enumeration from the lock, minus the
trap-dependent instructions (ECALL, EBREAK) exactly when the declaration shows
no trap support (no Zicsr, so no trap target). They are named in the record.

chip-AGNOSTIC: the design facts come from `declaration.json`, the generated L
docs and the staged RTL's own port list; the suites from the lock. No chip,
vendor, PDK or case-name literal decides any branch.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import _atomic_artefact as _aa

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "isa_suites.lock.json"
POLICY_PATH = HERE / "isa_suite_policy.json"

RECEIPT_REL = "reports/phase2/isa_suites/isa_suite_receipt.json"
TRANSCRIPT_REL = "phase2/stage1/sim_professional/l10_unit_tb/{case}/run.log"
PRODUCER = "isa_suite_producer"

PASS = "PASS"
FAIL = "FAIL"
NOT_MEASURED = "NOT_MEASURED"

#: Spike's DRAM base: where its boot ROM jumps. A property of the reference
#: tool, not of any design; the DUT image is linked at the design's own reset
#: vector and the two builds are compared for identical code size.
SPIKE_DRAM_BASE = 0x80000000
#: Per-program DUT cycle cap: the DOMAIN deadline of a simulation. The largest
#: measured program took 370,540 cycles; a program that has not halted by 54x
#: that has hung, and a hang is a FAIL.
CYCLE_CAP = 20_000_000
#: Spike's instruction budget (its own domain deadline) and the wall deadline
#: that turns a stuck reference into NOT_MEASURED (rc 124).
SPIKE_INSTRUCTION_CAP = 50_000_000
SPIKE_DEADLINE_S = 300
#: Cycles to let a multi-beat store to `tohost` finish before it is read.
HALT_SETTLE_CYCLES = 64
#: The power-up patterns every program runs under. NON-ZERO on purpose: a
#: zero-initialised RAM hides a register-zeroing defect (measured).
INIT_PATTERNS = ("ff", "a5")
#: The stall grace of the one supervised container job (no forward progress).
STALL_GRACE_S = 1800
FETCH_DEADLINE_S = 300

TOOLS = {
    "gcc": "riscv64-unknown-elf-gcc",
    "objdump": "riscv64-unknown-elf-objdump",
    "objcopy": "riscv64-unknown-elf-objcopy",
    "nm": "riscv64-unknown-elf-nm",
    "spike": "spike",
    "verilator": "verilator",
}

# ════════════════════════════════════════════════════════════════════════════
# lock, policy, acquisition
# ════════════════════════════════════════════════════════════════════════════


def load_json(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def load_lock(path: Path = LOCK_PATH) -> Dict[str, Any]:
    return load_json(path)


def load_policy(path: Path = POLICY_PATH) -> Dict[str, Any]:
    return load_json(path)


def fetch_allowed(pol: Dict[str, Any]) -> Tuple[bool, str]:
    mode = pol.get("external_suite_fetch")
    if mode == "allowed":
        return True, "policy external_suite_fetch=allowed (owner ruling 1)"
    return False, (f"policy external_suite_fetch={mode!r}: fetching the public "
                   f"ISA suites is not allowed, so no ISA case can be measured")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_tarball(data: bytes, suite: Dict[str, Any]) -> Tuple[bool, str]:
    got = sha256_bytes(data)
    if got != suite.get("tarball_sha256"):
        return False, (f"tarball sha256 {got} != locked "
                       f"{suite.get('tarball_sha256')} for commit "
                       f"{suite.get('commit')}")
    return True, "tarball sha256 verified"


def extract_verified(data: bytes, suite: Dict[str, Any], dest: Path
                     ) -> Tuple[Optional[Path], str]:
    """Extract ONLY the locked files, each checked against its locked sha256.
    Returns `(suite_root, why)`; `None` on any mismatch or missing member."""
    root = str(suite.get("root") or "")
    files: Dict[str, str] = suite.get("files") or {}
    if not root or not files:
        return None, "lock entry names no root or no files"
    out = Path(dest) / root
    bad: List[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            members = {m.name: m for m in tf.getmembers() if m.isfile()}
            for rel, want in sorted(files.items()):
                m = members.get(f"{root}/{rel}")
                if m is None:
                    bad.append(f"{rel}: absent from the tarball")
                    continue
                fh = tf.extractfile(m)
                blob = fh.read() if fh else b""
                got = sha256_bytes(blob)
                if got != want:
                    bad.append(f"{rel}: sha256 {got} != locked {want}")
                    continue
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
    except (tarfile.TarError, OSError, EOFError) as exc:
        return None, f"tarball unreadable: {exc!r}"
    if bad:
        return None, "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)"
                                            if len(bad) > 5 else "")
    return out, f"{len(files)} locked file(s) verified"


def default_fetch(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=FETCH_DEADLINE_S) as r:  # noqa: S310
        return r.read()


def cache_dir() -> Path:
    base = _os.environ.get("VIBEIC_ISA_SUITE_CACHE") or str(
        Path(tempfile.gettempdir()) / "vibeic_isa_suites")
    return Path(base)


def acquire(suite_name: str, suite: Dict[str, Any], dest: Path,
            fetch: Optional[Callable[[str], bytes]] = None,
            use_cache: bool = True) -> Tuple[Optional[Path], str]:
    """Fetch (or reuse a cached copy of) the pinned tarball, verify it, and
    extract the locked files. Any failure is `(None, reason)`: NOT_MEASURED."""
    fetch = fetch or default_fetch
    cached = cache_dir() / f"{suite.get('commit')}.tar.gz"
    data: Optional[bytes] = None
    source = ""
    if use_cache and cached.is_file():
        blob = cached.read_bytes()
        if verify_tarball(blob, suite)[0]:
            data, source = blob, f"cache {cached}"
    if data is None:
        try:
            data = fetch(str(suite["tarball_url"]))
        except Exception as exc:  # noqa: BLE001 — no network is a reason
            return None, (f"{suite_name}: could not fetch "
                          f"{suite.get('tarball_url')}: {exc!r}")
        source = f"fetched {suite.get('tarball_url')}"
        ok, why = verify_tarball(data, suite)
        if not ok:
            return None, f"{suite_name}: {why}"
        if use_cache:
            try:
                _aa.write_bytes(cached, data)
            except OSError:
                pass
    root, why = extract_verified(data, suite, dest)
    if root is None:
        return None, f"{suite_name}: {why}"
    return root, f"{suite_name}@{suite.get('commit')}: {source}; {why}"


# ════════════════════════════════════════════════════════════════════════════
# design facts — declaration, L docs, the staged RTL's own port list
# ════════════════════════════════════════════════════════════════════════════

#: `sdp<width>_<role>..._sync<N>r` — a simple dual-port SRAM of <width>-bit
#: words whose read data is valid N clocks after the read strobe.
_SDP_RE = re.compile(r"^sdp(?P<width>\d+)_(?P<roles>[a-z_]+?)_sync(?P<lat>\d+)r$")
_SDP_ROLES = ("waddr", "wdata", "wen", "raddr", "rdata", "ren")
_RESET_CONST_NAMES = ("reset_pc", "reset_vector", "boot_addr", "boot_address")
_HEX_RE = re.compile(r"0x([0-9a-fA-F_]+)")


def _declaration(project: Path) -> Dict[str, Any]:
    doc = load_json(Path(project) / "plugin_output" / "declaration.json")
    return doc.get("fields") if isinstance(doc.get("fields"), dict) else doc


def declared_units(decl: Dict[str, Any]) -> List[str]:
    raw = decl.get("isa_extensions")
    if isinstance(raw, str):
        raw = re.split(r"[\s,+]+", raw)
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def trap_support(decl: Dict[str, Any]) -> Tuple[bool, str]:
    """Does the design declare a trap target? An explicit boolean wins;
    otherwise Zicsr (the extension that provides mtvec) decides."""
    for key in ("trap_support", "with_csr", "WITH_CSR"):
        if isinstance(decl.get(key), bool):
            return decl[key], f"declaration {key}={decl[key]}"
        if decl.get(key) in (0, 1):
            return bool(decl[key]), f"declaration {key}={decl[key]}"
    units = {u.lower() for u in declared_units(decl)}
    if "zicsr" in units:
        return True, "declaration isa_extensions includes Zicsr (a trap target exists)"
    return False, ("declaration isa_extensions has no Zicsr: no mtvec, so no "
                   "trap target (the WITH_CSR=0 shape)")


def reset_vector(project: Path, decl: Dict[str, Any]) -> Tuple[Optional[int], str]:
    for key in _RESET_CONST_NAMES:
        v = decl.get(key)
        if isinstance(v, int):
            return v, f"declaration {key}"
        if isinstance(v, str) and _HEX_RE.search(v):
            return int(_HEX_RE.search(v).group(1).replace("_", ""), 16), \
                f"declaration {key}"
    try:
        import _path_layout as _pl
        gd = _pl.generated_docs_dir(project)
    except Exception:  # noqa: BLE001
        gd = Path(project) / "phase1" / "generated_docs"
    doc = load_json(gd / "L8_RTL_CONSTANTS.json")
    rows: List[Any] = []

    def _walk(o: Any) -> None:
        if isinstance(o, dict):
            if isinstance(o.get("name"), str):
                rows.append(o)
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(doc)
    for row in rows:
        if str(row.get("name")).strip().lower() in _RESET_CONST_NAMES:
            m = _HEX_RE.search(str(row.get("default") or row.get("value") or ""))
            if m:
                return int(m.group(1).replace("_", ""), 16), (
                    f"L8_RTL_CONSTANTS {row.get('name')} (source "
                    f"{row.get('source')})")
    return None, ("no reset vector declared (declaration reset_pc/"
                  "reset_vector, or an L8_RTL_CONSTANTS row of that name)")


def parse_sram_protocol(token: str) -> Tuple[Optional[Dict[str, Any]], str]:
    m = _SDP_RE.match(str(token or "").strip().lower())
    if not m:
        return None, (f"sram_interface_protocol {token!r} is not a protocol "
                      f"this producer can model (sdp<width>_<roles>_sync<N>r)")
    roles = set(m.group("roles").split("_"))
    width = int(m.group("width"))
    if width != 8:
        return None, (f"sram_interface_protocol {token!r}: a {width}-bit word "
                      f"SRAM model is not implemented (byte-wide only)")
    return {"kind": "sdp", "width": width, "read_latency": int(m.group("lat")),
            "stated_roles": sorted(roles)}, "declared SRAM protocol parsed"


def bind_sram_ports(ports: List[Tuple[str, str, str]]
                    ) -> Tuple[Optional[Dict[str, str]], str]:
    """role -> the DUT's unique top port whose name ends in `_<role>`."""
    out: Dict[str, str] = {}
    for role in _SDP_ROLES:
        want_dir = "input" if role == "rdata" else "output"
        hits = [n for d, _w, n in ports
                if (n == role or n.endswith("_" + role))
                and (d or "").strip().lower().startswith(want_dir)]
        if len(hits) != 1:
            return None, (f"SRAM role {role!r}: {len(hits)} {want_dir} port(s) "
                          f"end in '_{role}' ({hits}) — need exactly one")
        out[role] = hits[0]
    return out, "every SRAM role bound to exactly one top port"


def _top_parameters(text: str, module: str) -> Dict[str, str]:
    m = re.search(r"\bmodule\s+" + re.escape(module) + r"\s*#\s*\((.*?)\)\s*\(",
                  text, re.S)
    if not m:
        return {}
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return {pm.group(1): pm.group(2).strip()
            for pm in re.finditer(r"parameter\s+(?:\[[^\]]*\]\s*)?(?:integer\s+)?"
                                  r"([A-Za-z_]\w*)\s*=\s*([^,]+)", body)}


def design_facts(project: Path, rtl_dir: Optional[Path] = None
                 ) -> Tuple[Optional[Dict[str, Any]], str]:
    """Everything the env, the link script and the testbench need, or a
    refusal naming the first missing fact."""
    project = Path(project)
    decl = _declaration(project)
    if not decl:
        return None, "no plugin_output/declaration.json"
    top = decl.get("top_module")
    clk = decl.get("clock_port_name")
    memsize = decl.get("memsize_bytes")
    if not (isinstance(top, str) and isinstance(clk, str)
            and isinstance(memsize, int) and memsize > 0):
        return None, ("declaration lacks top_module / clock_port_name / "
                      "memsize_bytes")
    pol = str(decl.get("reset_polarity") or "").lower()
    if pol not in ("active_high", "active_low"):
        return None, f"declaration reset_polarity {pol!r} is not active_high/low"
    sram, why = parse_sram_protocol(decl.get("sram_interface_protocol"))
    if sram is None:
        return None, why
    rv, rv_src = reset_vector(project, decl)
    if rv is None:
        return None, rv_src
    if rv >= memsize:
        return None, (f"reset vector {rv:#x} lies outside the {memsize}-byte "
                      f"SRAM and no address map places it there")
    import testbench_gen as _tg
    module, ports, dut_why = _tg.resolve_dut(project, top)
    if module is None:
        return None, f"DUT not bound: {dut_why}"
    roles, why = bind_sram_ports(ports)
    if roles is None:
        return None, why
    inputs = [(n, w) for d, w, n in ports
              if (d or "").strip().lower().startswith("input")]
    if not any(n == clk for n, _w in inputs):
        return None, f"clock port {clk!r} is not an input of {module}"
    rst_cands = [n for n, _w in inputs
                 if n not in (clk, roles["rdata"]) and re.search(r"rst|reset", n, re.I)]
    if len(rst_cands) != 1:
        return None, f"reset input not unique among {module}'s inputs: {rst_cands}"
    other_inputs = [n for n, _w in inputs
                    if n not in (clk, roles["rdata"], rst_cands[0])]
    rtl = Path(rtl_dir) if rtl_dir else project / "phase2" / "stage1" / "rtl"
    params: Dict[str, str] = {}
    for f in sorted(rtl.glob("*.v")) + sorted(rtl.glob("*.sv")):
        params = _top_parameters(f.read_text(errors="replace"), module)
        if params:
            break
    mem_params = [k for k, v in params.items() if v.strip() == str(memsize)]
    if len(mem_params) != 1:
        return None, (f"no unique parameter of {module} defaults to the "
                      f"declared memsize {memsize} ({mem_params}) — the full "
                      f"suite cannot be run at a larger memsize by parameter")
    units = declared_units(decl)
    traps, traps_why = trap_support(decl)
    rf_bytes = 0
    if str(decl.get("rf_storage") or "").lower() == "shared_sram":
        rf_bytes = 32 * 4        # 32 x-registers of XLEN=32 at the top of SRAM
    return {
        "top": module, "clock": clk, "reset": rst_cands[0],
        "reset_active_high": pol == "active_high",
        "tie_low_inputs": other_inputs, "sram": sram, "sram_ports": roles,
        "memsize_declared": memsize, "memsize_param": mem_params[0],
        "reset_pc": rv, "reset_pc_source": rv_src,
        "isa_units": units, "trap_support": traps, "trap_support_why": traps_why,
        "rf_reserved_bytes": rf_bytes,
        "has_c": any(u.lower() == "c" for u in units),
    }, "design facts derived from the declaration, L8/L3 and the RTL ports"


# ════════════════════════════════════════════════════════════════════════════
# which declared cases ask for which ISA unit
# ════════════════════════════════════════════════════════════════════════════


def _case_text(case: Dict[str, Any]) -> str:
    return " ".join(str(case.get(k) or "") for k in
                    ("stimulus", "expected", "description", "name"))


def bind_case(case: Dict[str, Any], units: List[str], xlen: int = 32
              ) -> List[str]:
    """The declared ISA units this case's OWN text names.

    The base unit is named as `RV<xlen><base>` (e.g. RV32I), an extension by
    its own multi-letter token (e.g. Zifencei). A unit the design does not
    declare binds nothing; a single-letter extension (M, C) is never matched
    from prose — it is too short to be a token of intent. The case NAME is
    included last so a name-only case is still bound by the unit it names."""
    text = _case_text(case)
    out = []
    for u in units:
        if len(u) == 1 and u.upper() == "I":
            if re.search(rf"(?i)(?<![A-Za-z0-9])RV{xlen}I(?![A-Za-z])", text):
                out.append(u)
        elif len(u) > 1 and re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(u)}(?![A-Za-z0-9])", text):
            out.append(u)
    return out


def bound_cases(project: Path, units: List[str]) -> Dict[str, List[str]]:
    import cpu_functional_oracle_waiver_check as _w
    import _path_layout as _pl
    gd = _pl.generated_docs_dir(project)
    rows = _w._declared_rows(gd / "L10_TEST_CASES.json",
                             ("test_cases", "cases", "vectors"))
    rows, _na = _w.split_design_declared_na(rows, _w.design_selected_options(project))
    out: Dict[str, List[str]] = {}
    for r in rows:
        name = r.get("name") or r.get("id") or r.get("case")
        if not name or r.get("applies_when"):
            continue
        u = bind_case(r, units)
        if u:
            out[str(name)] = u
    return out


# ════════════════════════════════════════════════════════════════════════════
# the flow-authored env, link script and testbench
# ════════════════════════════════════════════════════════════════════════════


def gen_env(facts: Dict[str, Any]) -> Dict[str, str]:
    """`riscv_test.h` (riscv-tests), `model_test.h` (riscv-arch-test) and the
    link script. No CSR, no trap: PASS/FAIL and HALT are a store to `tohost`."""
    riscv_test_h = """/* Flow-authored CSR-free env for riscv-tests (isa_suite_producer).
 * PASS/FAIL: a store to the HTIF-style `tohost` word: 1 = pass,
 * (TESTNUM<<1)|1 = fail at test TESTNUM (the same encoding Spike's HTIF reads). */
#ifndef VIBEIC_ISA_RISCV_TEST_H
#define VIBEIC_ISA_RISCV_TEST_H
#define RVTEST_RV32U .macro init; .endm
#define RVTEST_RV64U RVTEST_RV32U
#define TESTNUM gp
#define RVTEST_CODE_BEGIN .section .text.init; .align 2; .globl _start; _start: init;
#define RVTEST_CODE_END unimp
#define RVTEST_PASS fence; li t0, 1; la t1, tohost; sw t0, 0(t1); 1: j 1b;
#define RVTEST_FAIL fence; slli t0, TESTNUM, 1; ori t0, t0, 1; la t1, tohost; sw t0, 0(t1); 1: j 1b;
#define EXTRA_DATA
#define RVTEST_DATA_BEGIN EXTRA_DATA .pushsection .tohost,"aw",@progbits; .align 3; .global tohost; tohost: .word 0; .word 0; .global fromhost; fromhost: .word 0; .word 0; .popsection; .align 4; .global begin_signature; begin_signature:
#define RVTEST_DATA_END .align 4; .global end_signature; end_signature:
#endif
"""
    model_test_h = """/* Flow-authored CSR-free model for riscv-arch-test 3.x (isa_suite_producer).
 * RVMODEL_HALT stores 1 to `tohost`; the signature is begin..end_signature. */
#ifndef VIBEIC_ISA_MODEL_TEST_H
#define VIBEIC_ISA_MODEL_TEST_H
#define RVMODEL_HALT fence; li t0, 1; la t1, tohost; sw t0, 0(t1); 1: j 1b;
#define RVMODEL_DATA_BEGIN .pushsection .tohost,"aw",@progbits; .align 3; .global tohost; tohost: .word 0; .word 0; .global fromhost; fromhost: .word 0; .word 0; .popsection; .align 4; .global begin_signature; begin_signature:
#define RVMODEL_DATA_END .align 4; .global end_signature; end_signature:
#define RVMODEL_BOOT
#define RVMODEL_IO_INIT
#define RVMODEL_IO_WRITE_STR(_R, _STR)
#define RVMODEL_IO_CHECK()
#define RVMODEL_IO_ASSERT_GPR_EQ(_S, _R, _I)
#define RVMODEL_IO_ASSERT_SFPR_EQ(_F, _R, _I)
#define RVMODEL_IO_ASSERT_DFPR_EQ(_D, _R, _I)
#define RVMODEL_SET_MSW_INT
#define RVMODEL_CLEAR_MSW_INT
#define RVMODEL_CLEAR_MTIMER_INT
#define RVMODEL_CLEAR_MEXT_INT
#endif
"""
    def _ld(origin: int) -> str:
        return ("OUTPUT_ARCH(\"riscv\")\nENTRY(_start)\nSECTIONS {\n"
                f"  . = {origin:#x};\n"
                "  .text : { *(.text.init) *(.text) *(.text.*) }\n"
                "  . = ALIGN(4);\n  .tohost : { *(.tohost) }\n"
                "  .data : { *(.data) *(.data.*) *(.sdata) *(.rodata*) }\n"
                "  .bss : { *(.bss) *(.sbss) }\n  _end = .;\n}\n")
    return {"riscv_test.h": riscv_test_h, "model_test.h": model_test_h,
            "link_dut.ld": _ld(int(facts["reset_pc"])),
            "link_ref.ld": _ld(SPIKE_DRAM_BASE)}


def gen_tb(facts: Dict[str, Any]) -> str:
    """A testbench that owns a plain byte SRAM (no zero mask), powers it up to
    `+init=<hex byte>`, loads `+hex=`, releases reset and watches the SRAM
    WRITE PORT for the store to `tohost`; then dumps the signature."""
    p = facts["sram_ports"]
    lat = int(facts["sram"]["read_latency"])
    rst_on = "1'b1" if facts["reset_active_high"] else "1'b0"
    rst_off = "1'b0" if facts["reset_active_high"] else "1'b1"
    ties = "".join(f"    .{n}(1'b0),\n" for n in facts["tie_low_inputs"])
    read_pipe = "\n".join(
        [f"  reg [7:0] rpipe [0:{lat}];",
         "  integer k;",
         f"  always @(posedge clk) begin",
         f"    rpipe[0] <= ren ? mem[raddr % MEMSIZE] : rpipe[0];",
         f"    for (k = 1; k <= {lat}; k = k + 1) rpipe[k] <= rpipe[k-1];",
         f"  end",
         f"  always @* rdata = rpipe[{lat - 1}];"]) if lat > 1 else (
        "  always @(posedge clk) if (ren) rdata <= mem[raddr % MEMSIZE];")
    return f"""// Flow-authored ISA-suite testbench (isa_suite_producer). No harness copied.
`timescale 1ns/1ps
module isa_tb;
  parameter integer MEMSIZE = {facts['memsize_declared']};
  parameter integer CYCLE_CAP = {CYCLE_CAP};
  reg clk = 1'b0;
  reg rst = {rst_on};
  always #5 clk = ~clk;
  wire [31:0] waddr, raddr;
  wire [7:0] wdata;
  wire wen, ren;
  reg [7:0] rdata;
  reg [7:0] mem [0:MEMSIZE-1];
  always @(posedge clk) if (wen) mem[waddr % MEMSIZE] <= wdata;
{read_pipe}
  {facts['top']} #(.{facts['memsize_param']}(MEMSIZE)) dut (
    .{facts['clock']}(clk),
    .{facts['reset']}(rst),
{ties}    .{p['waddr']}(waddr),
    .{p['wdata']}(wdata),
    .{p['wen']}(wen),
    .{p['raddr']}(raddr),
    .{p['rdata']}(rdata),
    .{p['ren']}(ren));
  integer i, cyc, th, sb, se, fd, iv, settle;
  reg [1023:0] hexfile, sigfile;
  reg seen;
  initial begin
    if (!$value$plusargs("init=%h", iv)) iv = 0;
    for (i = 0; i < MEMSIZE; i = i + 1) mem[i] = iv[7:0];
    if (!$value$plusargs("hex=%s", hexfile)) begin $display("ISA_TB ERROR nohex"); $finish; end
    if (!$value$plusargs("tohost=%d", th)) begin $display("ISA_TB ERROR notohost"); $finish; end
    $readmemh(hexfile, mem);
    seen = 1'b0; settle = 0;
    repeat (10) @(posedge clk);
    rst = {rst_off};
    for (cyc = 0; cyc < CYCLE_CAP && settle < {HALT_SETTLE_CYCLES}; cyc = cyc + 1) begin
      @(posedge clk);
      if (wen && (waddr % MEMSIZE) >= th && (waddr % MEMSIZE) < th + 4) seen = 1'b1;
      if (seen) settle = settle + 1;
    end
    if (settle >= {HALT_SETTLE_CYCLES}) begin
      if ($value$plusargs("sig=%s", sigfile) && $value$plusargs("sigbeg=%d", sb) && $value$plusargs("sigend=%d", se)) begin
        fd = $fopen(sigfile, "w");
        for (i = sb; i < se; i = i + 4) $fwrite(fd, "%02x%02x%02x%02x\\n", mem[i+3], mem[i+2], mem[i+1], mem[i]);
        $fclose(fd);
      end
      $display("ISA_TB HALT tohost=%02x%02x%02x%02x cycles=%0d", mem[th+3], mem[th+2], mem[th+1], mem[th], cyc);
    end else
      $display("ISA_TB HANG cycles=%0d", cyc);
    $finish;
  end
endmodule
"""


# ════════════════════════════════════════════════════════════════════════════
# instruments — each turns a tool artefact into a status, calibrated
# ════════════════════════════════════════════════════════════════════════════

#: One objdump disassembly line: `   addr:\t<hex> \t<mnemonic> ...`.
_OBJDUMP_LINE_RE = re.compile(
    r"^\s*[0-9a-f]+:\s+(?P<hex>[0-9a-f]{4}(?:[0-9a-f]{4})?)\s+(?P<mn>\S+)",
    re.M)
_SYSTEM_MNEMONICS = ("ecall", "ebreak", "mret", "sret", "uret", "wfi")
_TB_HALT_RE = re.compile(r"^ISA_TB HALT tohost=(?P<th>[0-9a-fA-FxXzZ]{8}) cycles=(?P<cyc>\d+)", re.M)
_TB_HANG_RE = re.compile(r"^ISA_TB HANG cycles=(?P<cyc>\d+)", re.M)


def objdump_guard(disassembly: str, *, allow_compressed: bool,
                  size_bytes: Optional[int] = None,
                  program_area: Optional[int] = None) -> List[str]:
    """Every reason this image is not a program of the declared ISA.

    A 16-bit parcel on a core that declares no C is the measured trap: without
    `-mno-relax` an alignment `c.nop` is emitted, the reference traps on it,
    and a CSR-less core silently runs it. A CSR / system instruction in a
    program means the suite needs a trap environment the design lacks."""
    import instrument_calibration as _ic
    _ic.assert_calibrated("isa_suite_producer::objdump_guard")
    out: List[str] = []
    n16 = 0
    sysn: Dict[str, int] = {}
    for m in _OBJDUMP_LINE_RE.finditer(disassembly):
        mn = m.group("mn").lower()
        if len(m.group("hex")) == 4 or mn.startswith("c."):
            n16 += 1
        if mn.startswith("csr") or mn in _SYSTEM_MNEMONICS:
            sysn[mn] = sysn.get(mn, 0) + 1
    if n16 and not allow_compressed:
        out.append(f"{n16} 16-bit instruction(s) on a core that declares no C")
    if sysn:
        out.append("CSR/system instruction(s) " + ", ".join(
            f"{k} x{v}" for k, v in sorted(sysn.items())))
    if size_bytes is not None and program_area is not None \
            and size_bytes > program_area:
        out.append(f"image ends at {size_bytes} B, beyond the {program_area} B "
                   f"program area")
    return out


def parse_tb_transcript(text: str) -> Dict[str, Any]:
    """`{"status": halted|hang|no_result, "tohost": int|None, "cycles": int}`
    from the testbench's own transcript."""
    import instrument_calibration as _ic
    _ic.assert_calibrated("isa_suite_producer::parse_tb_transcript")
    m = _TB_HALT_RE.search(text or "")
    if m:
        th = m.group("th")
        return {"status": "halted",
                "tohost": int(th, 16) if re.fullmatch(r"[0-9a-fA-F]{8}", th)
                else None, "cycles": int(m.group("cyc"))}
    m = _TB_HANG_RE.search(text or "")
    if m:
        return {"status": "hang", "tohost": None, "cycles": int(m.group("cyc"))}
    return {"status": "no_result", "tohost": None, "cycles": 0}


def read_words(text: Optional[str]) -> Optional[List[str]]:
    if text is None:
        return None
    words = [w.strip().lower() for w in text.splitlines() if w.strip()]
    return words or None


def judge_signature(ref: Optional[List[str]], ref_rc: Optional[int],
                    dut: Dict[str, Any], dut_sig: Optional[List[str]]
                    ) -> Tuple[str, str]:
    """riscv-arch-test's criterion: DUT signature == reference, word for word."""
    if ref_rc == 124:
        return NOT_MEASURED, "noref: the reference model timed out (rc 124)"
    if not ref:
        return NOT_MEASURED, (f"noref: the reference model wrote no signature "
                              f"(rc {ref_rc})")
    if dut.get("status") == "hang":
        return FAIL, f"hang: no store to tohost within {dut.get('cycles')} cycles"
    if dut.get("status") != "halted":
        return FAIL, "the simulation reported no result"
    if not dut_sig:
        return FAIL, "halted but dumped no signature"
    for i, (a, b) in enumerate(zip(ref, dut_sig)):
        if a != b:
            ndiff = sum(1 for x, y in zip(ref, dut_sig) if x != y)
            return FAIL, (f"signature word {i} (byte offset {4 * i:#x}) is "
                          f"{b}, reference {a} ({ndiff} of {len(ref)} words "
                          f"differ)")
    if len(ref) != len(dut_sig):
        return FAIL, f"signature length {len(dut_sig)} != reference {len(ref)}"
    return PASS, f"{len(ref)} signature words equal the reference"


def judge_tohost(ref_rc: Optional[int], dut: Dict[str, Any]) -> Tuple[str, str]:
    """riscv-tests' criterion: tohost == 1 is PASS, (n<<1)|1 is FAIL at n."""
    if ref_rc == 124:
        return NOT_MEASURED, "noref: the reference model timed out (rc 124)"
    if ref_rc != 0:
        return NOT_MEASURED, (f"the reference model itself does not pass this "
                              f"program (rc {ref_rc})")
    if dut.get("status") == "hang":
        return FAIL, f"hang: no store to tohost within {dut.get('cycles')} cycles"
    th = dut.get("tohost")
    if dut.get("status") != "halted" or th is None:
        return FAIL, "the simulation reported no readable tohost"
    if th == 1:
        return PASS, "tohost == 1"
    if th & 1:
        return FAIL, f"self-check failed at test {th >> 1}"
    return FAIL, f"tohost {th:#x} is not a pass/fail encoding"


def fold(states: List[str]) -> str:
    """A set is PASS only if every member passed; any FAIL is FAIL."""
    if not states:
        return NOT_MEASURED
    if FAIL in states:
        return FAIL
    if all(s == PASS for s in states):
        return PASS
    return NOT_MEASURED


# ════════════════════════════════════════════════════════════════════════════
# INSIDE the image: build, guard, reference, DUT
# ════════════════════════════════════════════════════════════════════════════


def _run(argv: List[str], cwd: Path, log: Path, *, deadline: Optional[float] = None
         ) -> Tuple[int, str]:
    """One tool run. `deadline` is used ONLY for the reference model, where the
    brief makes a timeout a NOT_MEASURED; everything else is supervised by
    forward progress (the stall watchdog), never by a clock."""
    import subprocess
    with log.open("a") as fh:
        fh.write("$ " + " ".join(map(str, argv)) + "\n")
    if deadline is not None:
        try:
            pr = subprocess.run([str(a) for a in argv], cwd=str(cwd),
                                capture_output=True, text=True,
                                timeout=deadline)
            return pr.returncode, (pr.stdout or "") + (pr.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, f"deadline {deadline}s exceeded"
        except OSError as exc:
            return 127, repr(exc)
    import _watchdog as _wd
    res = _wd.run_host_supervised([str(a) for a in argv], cwd=str(cwd),
                                  stall_grace_s=STALL_GRACE_S)
    return res.rc, (res.out or "") + (res.err or "")


def _sym(nm_text: str, name: str) -> Optional[int]:
    for line in nm_text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def inside(job_path: Path) -> int:
    """Run a job description inside the image; write `results.json`."""
    job = json.loads(Path(job_path).read_text())
    work = Path(job["work"])
    log = work / "progress.log"
    res: Dict[str, Any] = {"programs": {}, "builds": {}, "sims": {}}
    out_json = work / "results.json"

    def _save() -> None:
        _aa.write_json(out_json, res)

    env = work / "env"
    march = job["march"]
    for prog in job["programs"]:
        pid = prog["id"]
        d = work / "build" / pid
        d.mkdir(parents=True, exist_ok=True)
        r: Dict[str, Any] = {}
        common = [TOOLS["gcc"], f"-march={march}", "-mabi=ilp32", "-static",
                  "-mno-relax", "-nostdlib", "-nostartfiles", "-mcmodel=medany",
                  f"-I{env}"] + [f"-I{x}" for x in prog["include_dirs"]]
        if prog["judge"] == "signature":
            common += ["-DXLEN=32", "-DTEST_CASE_1=True",
                       "-Wl,-e,rvtest_entry_point"]
        rc1, t1 = _run(common + ["-T", str(env / "link_dut.ld"), prog["src"],
                                 "-o", str(d / "dut.elf")], d, log)
        rc2, t2 = _run(common + ["-T", str(env / "link_ref.ld"), prog["src"],
                                 "-o", str(d / "ref.elf")], d, log)
        if rc1 or rc2:
            r["build"] = {"ok": False, "log": (t1 + t2)[-1500:]}
            res["programs"][pid] = r
            _save()
            continue
        _rc, dis = _run([TOOLS["objdump"], "-d", "-M", "no-aliases",
                         str(d / "dut.elf")], d, log)
        (d / "dut.dis").write_text(dis)
        _rc, nm = _run([TOOLS["nm"], str(d / "dut.elf")], d, log)
        _rc, nm_ref = _run([TOOLS["nm"], str(d / "ref.elf")], d, log)
        end, th = _sym(nm, "_end"), _sym(nm, "tohost")
        sb, se = _sym(nm, "begin_signature"), _sym(nm, "end_signature")
        end_ref = _sym(nm_ref, "_end")
        _run([TOOLS["objcopy"], "-O", "verilog", str(d / "dut.elf"),
              str(d / "dut.hex")], d, log)
        r["build"] = {"ok": end is not None and th is not None,
                      "end": end, "tohost": th, "sigbeg": sb, "sigend": se,
                      "size": None if end is None else end - job["reset_pc"],
                      "ref_size": None if end_ref is None
                      else end_ref - SPIKE_DRAM_BASE,
                      "disassembly": str(d / "dut.dis")}
        sig = d / "ref.sig"
        argv = [TOOLS["spike"], f"--isa={job['spike_isa']}",
                f"--instructions={SPIKE_INSTRUCTION_CAP}"]
        if prog["judge"] == "signature":
            argv += [f"+signature={sig}", "+signature-granularity=4"]
        argv.append(str(d / "ref.elf"))
        src, _t = _run(argv, d, log, deadline=SPIKE_DEADLINE_S)
        r["ref"] = {"rc": src, "sig": str(sig) if sig.is_file() else None}
        res["programs"][pid] = r
        _save()

    ends = [p["build"]["size"] for p in res["programs"].values()
            if p.get("build", {}).get("ok")]
    rf = job["rf_reserved_bytes"]
    full = _next_pow2(max([job["memsize_declared"]] + [e + rf for e in ends]))
    res["memsize_full"] = full
    sizes = {"full": full, "delivered": job["memsize_declared"]}
    for arm in job["arms"]:
        for label, msize in sizes.items():
            b = work / "sim" / f"{arm['name']}_{label}"
            b.mkdir(parents=True, exist_ok=True)
            argv = [TOOLS["verilator"], "--binary", "--timing", "-j", "4",
                    "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-MULTIDRIVEN",
                    "--top-module", "isa_tb", f"-GMEMSIZE={msize}",
                    "-Mdir", str(b / "obj"), "-o", "simv",
                    str(env / "isa_tb.v")] + arm["rtl_files"]
            rc, t = _run(argv, b, log)
            res["builds"][f"{arm['name']}_{label}"] = {
                "ok": rc == 0, "memsize": msize, "log": t[-1500:]}
            _save()
    for pid, r in res["programs"].items():
        bld = r.get("build") or {}
        if not bld.get("ok"):
            continue
        d = work / "build" / pid
        for arm in job["arms"]:
            for label, msize in sizes.items():
                key = f"{arm['name']}_{label}"
                if not res["builds"].get(key, {}).get("ok"):
                    continue
                if bld["size"] + rf > msize:
                    continue
                for iv in job["init_patterns"]:
                    sigf = d / f"dut_{key}_{iv}.sig"
                    argv = [str(work / "sim" / key / "obj" / "simv"),
                            f"+hex={d / 'dut.hex'}", f"+tohost={bld['tohost']}",
                            f"+init={iv}"]
                    if bld.get("sigbeg") is not None and bld.get("sigend") is not None:
                        argv += [f"+sig={sigf}", f"+sigbeg={bld['sigbeg']}",
                                 f"+sigend={bld['sigend']}"]
                    _rc, t = _run(argv, d, log)
                    res["sims"].setdefault(pid, {}).setdefault(key, {})[iv] = {
                        "rc": _rc, "transcript": t[-600:],
                        "sig": str(sigf) if sigf.is_file() else None}
        _save()
    res["done"] = True
    _save()
    return 0


# ════════════════════════════════════════════════════════════════════════════
# the container job (default executor)
# ════════════════════════════════════════════════════════════════════════════


def docker_executor(job_path: Path, work: Path) -> Tuple[int, str]:
    """One supervised, memory-capped `docker run` of the resolved image."""
    import _docker_memory as _dmem
    import _watchdog as _wd
    import p0_tool_frontend_check as _p0
    image = _p0.default_image()
    name = f"vibeic_isa_{_os.getpid()}_{uuid.uuid4().hex[:8]}"
    inner = (f"export PATH=/foss/tools/bin:$PATH; python3 "
             f"/vibeic/programs/{Path(__file__).name} --inside {job_path}")
    argv = ["docker", "run", "--rm", *_dmem.docker_memory_flags(),
            "--name", name, "--user", f"{_os.getuid()}:{_os.getgid()}",
            "-e", "HOME=/tmp", "-v", f"{work}:{work}",
            "-v", f"{HERE}:/vibeic/programs:ro",
            "--entrypoint", "bash", image, "-c", inner]
    res = _wd.run_host_supervised(argv, stall_grace_s=STALL_GRACE_S,
                                  log_path=str(work / "progress.log"))
    if res.rc == _wd.RC_STALLED:
        import subprocess
        subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                       check=False)
    return res.rc, f"image {image}\n" + (res.out or "")[-3000:] + (res.err or "")[-3000:]


# ════════════════════════════════════════════════════════════════════════════
# the host side
# ════════════════════════════════════════════════════════════════════════════


def instruction_total(lock: Dict[str, Any], facts: Dict[str, Any]
                      ) -> Tuple[int, List[str], str]:
    """(total, excluded instructions, why) for the base ISA unit."""
    unit = (lock.get("isa_units") or {}).get("I") or {}
    instrs = list(unit.get("instructions") or [])
    if facts.get("trap_support"):
        return len(instrs), [], (f"{len(instrs)}: the design declares trap "
                                 f"support ({facts.get('trap_support_why')})")
    excl = [i for i in unit.get("trap_dependent") or [] if i in instrs]
    return len(instrs) - len(excl), excl, (
        f"{len(instrs) - len(excl)} = {len(instrs)} RV32I instructions minus "
        f"{', '.join(excl)}: {facts.get('trap_support_why')}. "
        + str(unit.get("trap_dependent_reason") or ""))


def _arms(project: Path, rtl_dir: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The verdict arm (the staged RTL) and, when a disclosed erratum changed
    the staged copy, arm A: the same set with each deviated file replaced by
    its unmodified input original."""
    staged = sorted(str(p) for p in list(rtl_dir.glob("*.v")) + list(rtl_dir.glob("*.sv")))
    arms = [{"name": "staged", "rtl_files": staged}]
    notes: List[str] = []
    try:
        import reused_ip_erratum as _e
        doc = _e.read_flow_record(project)
    except Exception:  # noqa: BLE001
        doc = {}
    swaps = {}
    for row in doc.get("rows") or []:
        if row.get("status") in ("APPLIED", "ALREADY_APPLIED") and row.get("input_file"):
            swaps[Path(row["staged_file"]).name] = row["input_file"]
            notes.append(row.get("disclosure") or "")
    if swaps:
        unmod = [swaps.get(Path(f).name, f) for f in staged]
        arms.append({"name": "unmodified", "rtl_files": unmod})
    return arms, [n for n in notes if n]


def produce(project: Path, *, executor: Optional[Callable[[Path, Path],
                                                          Tuple[int, str]]] = None,
            fetch: Optional[Callable[[str], bytes]] = None,
            lock: Optional[Dict[str, Any]] = None,
            pol: Optional[Dict[str, Any]] = None,
            extra_arms: Optional[List[Dict[str, Any]]] = None,
            init_patterns: Tuple[str, ...] = INIT_PATTERNS,
            work_root: Optional[Path] = None, keep_work: bool = False,
            write: bool = True) -> Dict[str, Any]:
    """Produce the ISA-suite evidence for every declared case that asks for
    one. Returns the receipt (also written to `RECEIPT_REL`)."""
    project = Path(project)
    lock = load_lock() if lock is None else lock
    pol = load_policy() if pol is None else pol
    receipt: Dict[str, Any] = {"schema": "vibeic.isa_suite_receipt.v1",
                               "producer": PRODUCER, "cases": {},
                               "rows": [], "refusal": None}
    decl = _declaration(project)
    units = declared_units(decl)
    cases = bound_cases(project, units) if units else {}
    receipt["bound_cases"] = cases
    if not cases:
        receipt["refusal"] = ("no declared L10 case names a declared ISA unit "
                              "— nothing to produce")
        return receipt

    def _refuse(why: str) -> Dict[str, Any]:
        receipt["refusal"] = why
        for case in cases:
            receipt["rows"].append({"id": case, "verdict": "NOT_EXECUTED",
                                    "sim_executed": False,
                                    "detail": f"ISA suite NOT_MEASURED: {why}"})
        if write:
            _aa.write_json(project / RECEIPT_REL, receipt)
        return receipt

    ok, why = fetch_allowed(pol)
    receipt["fetch_policy"] = why
    if not ok:
        return _refuse(why)
    facts, why = design_facts(project)
    receipt["design_facts"] = facts
    if facts is None:
        return _refuse(why)
    total, excluded, total_why = instruction_total(lock, facts)
    receipt["instruction_total"] = {"total": total, "excluded": excluded,
                                    "why": total_why}
    wanted_units = sorted({u for us in cases.values() for u in us})
    progs = [dict(p, suite=sname)
             for sname, s in (lock.get("suites") or {}).items()
             for p in s.get("programs") or [] if p.get("unit") in wanted_units]
    if not progs:
        return _refuse(f"the lock holds no program for units {wanted_units}")
    work = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="vibeic_isa_"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        roots: Dict[str, Path] = {}
        acq: List[str] = []
        for sname in sorted({p["suite"] for p in progs}):
            root, awhy = acquire(sname, lock["suites"][sname],
                                 work / "suites", fetch=fetch)
            acq.append(awhy)
            if root is None:
                receipt["acquisition"] = acq
                return _refuse(awhy)
            roots[sname] = root
        receipt["acquisition"] = acq
        env = work / "env"
        env.mkdir(exist_ok=True)
        for fname, text in gen_env(facts).items():
            (env / fname).write_text(text)
        (env / "isa_tb.v").write_text(gen_tb(facts))
        rtl_dir = project / "phase2" / "stage1" / "rtl"
        arms, disclosures = _arms(project, rtl_dir)
        for extra in extra_arms or []:
            arms.append(extra)
        # The container sees only `work`: each arm's RTL is COPIED in, which
        # also freezes the exact bytes each arm was judged on.
        frozen = []
        for arm in arms:
            d = work / "rtl" / arm["name"]
            d.mkdir(parents=True, exist_ok=True)
            files = []
            for f in arm["rtl_files"]:
                dst = d / Path(f).name
                shutil.copy2(f, dst)
                files.append(str(dst))
            frozen.append({"name": arm["name"], "rtl_files": files,
                           "sha256": {Path(f).name: sha256_bytes(Path(f).read_bytes())
                                      for f in files}})
        arms = frozen
        receipt["arms"] = [a["name"] for a in arms]
        receipt["arm_rtl_sha256"] = {a["name"]: a["sha256"] for a in arms}
        receipt["deviation_disclosures"] = disclosures
        march = "rv32i" + "".join(f"_{u.lower()}" for u in facts["isa_units"]
                                  if len(u) > 1 and u.lower() != "zicsr")
        job = {
            "work": str(work), "march": march,
            "spike_isa": march + ("" if "zicsr" in march else "_zicsr"),
            "reset_pc": facts["reset_pc"],
            "memsize_declared": facts["memsize_declared"],
            "rf_reserved_bytes": facts["rf_reserved_bytes"],
            "init_patterns": list(init_patterns), "arms": arms,
            "programs": [{
                "id": p["id"], "judge": p["judge"],
                "src": str(roots[p["suite"]] / p["path"]),
                "include_dirs": [str(roots[p["suite"]] / x) for x in
                                 lock["suites"][p["suite"]].get("include_dirs") or []],
            } for p in progs],
        }
        receipt["reference"] = (f"spike --isa={job['spike_isa']} (Zicsr only "
                                f"for the reference's own boot ROM; the guard "
                                f"refuses any CSR in a program)")
        job_path = work / "job.json"
        _aa.write_json(job_path, job)
        rc, transcript = (executor or docker_executor)(job_path, work)
        results = load_json(work / "results.json")
        receipt["executor_rc"] = rc
        if not results.get("done"):
            return _refuse(f"the container job did not complete (rc {rc}): "
                           f"{transcript[-800:]}")
        vb = (results.get("builds") or {}).get("staged_full") or {}
        if not vb.get("ok"):
            return _refuse("the DUT testbench did not build on the staged RTL: "
                           + str(vb.get("log", "no build record"))[-600:])
        judged = judge_all(progs, results, arms, facts, init_patterns)
        receipt.update(judged)
        receipt["memsize_full"] = results.get("memsize_full")
        receipt["label"] = (
            f"full suite on the same RTL at MEMSIZE={results.get('memsize_full')} "
            f"(parameter only); delivered-size subset at memsize "
            f"{facts['memsize_declared']}")
        _case_rows(receipt, cases, progs, lock, facts, total, excluded, project,
                   write)
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    if write:
        _aa.write_json(project / RECEIPT_REL, receipt)
    return receipt


def judge_all(progs: List[Dict[str, Any]], results: Dict[str, Any],
              arms: List[Dict[str, Any]], facts: Dict[str, Any],
              init_patterns: Tuple[str, ...]) -> Dict[str, Any]:
    """Per program x arm x size: the suite's own verdict, with its reason."""
    per: Dict[str, Any] = {}
    full = results.get("memsize_full")
    for p in progs:
        pid = p["id"]
        r = (results.get("programs") or {}).get(pid) or {}
        b = r.get("build") or {}
        entry: Dict[str, Any] = {"suite": p["suite"], "unit": p["unit"],
                                 "instruction": p.get("instruction"),
                                 "role": p["role"], "judge": p["judge"],
                                 "size": b.get("size"), "arms": {}}
        per[pid] = entry
        if not b.get("ok"):
            entry["pre"] = (NOT_MEASURED, "build failed: "
                            + str(b.get("log", ""))[-300:])
            continue
        if b.get("ref_size") is not None and b.get("size") != b.get("ref_size"):
            entry["pre"] = (NOT_MEASURED, f"DUT and reference images differ in "
                            f"size ({b.get('size')} vs {b.get('ref_size')})")
            continue
        try:
            dis = Path(b["disassembly"]).read_text()
        except (OSError, KeyError, TypeError):
            dis = ""
        try:
            viol = objdump_guard(dis, allow_compressed=facts.get("has_c", False),
                                 size_bytes=b.get("size"),
                                 program_area=(full or 0) - facts["rf_reserved_bytes"])
        except Exception as exc:  # noqa: BLE001 — Uncalibrated may not judge
            entry["pre"] = (NOT_MEASURED, f"uncalibrated: {exc}")
            continue
        entry["fits_declared_memsize"] = (
            b.get("size") is not None and b["size"] + facts["rf_reserved_bytes"]
            <= facts["memsize_declared"])
        if viol:
            entry["pre"] = (NOT_MEASURED, "guard: " + "; ".join(viol))
            continue
        ref = (r.get("ref") or {})
        ref_words = None
        if ref.get("sig"):
            try:
                ref_words = read_words(Path(ref["sig"]).read_text())
            except OSError:
                ref_words = None
        for arm in arms:
            for label in ("full", "delivered"):
                key = f"{arm['name']}_{label}"
                sims = ((results.get("sims") or {}).get(pid) or {}).get(key)
                if not sims:
                    continue
                states = []
                for iv in init_patterns:
                    s = sims.get(iv) or {}
                    try:
                        dut = parse_tb_transcript(s.get("transcript") or "")
                    except Exception as exc:  # noqa: BLE001 — Uncalibrated
                        states.append({"init": iv, "state": NOT_MEASURED,
                                       "why": f"uncalibrated: {exc}",
                                       "cycles": None})
                        continue
                    if p["judge"] == "signature":
                        words = None
                        if s.get("sig"):
                            try:
                                words = read_words(Path(s["sig"]).read_text())
                            except OSError:
                                words = None
                        st, why = judge_signature(ref_words, ref.get("rc"), dut, words)
                    else:
                        st, why = judge_tohost(ref.get("rc"), dut)
                    states.append({"init": iv, "state": st, "why": why,
                                   "cycles": dut.get("cycles")})
                entry["arms"][key] = {"state": fold([x["state"] for x in states]),
                                      "by_init": states}
    return {"programs": per}


def _prog_state(entry: Dict[str, Any], key: str) -> Tuple[str, str]:
    if entry.get("pre"):
        return entry["pre"][0], entry["pre"][1]
    a = entry["arms"].get(key)
    if not a:
        return NOT_MEASURED, f"not run on {key}"
    bad = [x for x in a["by_init"] if x["state"] != PASS]
    why = "; ".join(f"init {x['init']}: {x['why']}" for x in bad) or \
        a["by_init"][0]["why"]
    return a["state"], why


def arm_summary(receipt: Dict[str, Any], arm: str) -> Dict[str, Any]:
    """`{unit: (passed, total)}` over the PRIMARY programs, full size."""
    out: Dict[str, List[int]] = {}
    for e in (receipt.get("programs") or {}).values():
        if e.get("role") != "primary" or e.get("suite") != "riscv-arch-test":
            continue
        st, _w = _prog_state(e, f"{arm}_full")
        c = out.setdefault(e["unit"], [0, 0])
        c[1] += 1
        c[0] += st == PASS
    return {u: tuple(v) for u, v in out.items()}


def _summary_text(s: Dict[str, Any]) -> str:
    return ", ".join(f"{u} {p}/{t}" for u, (p, t) in sorted(s.items()))


def unmodified_arm_summary(project: Path) -> Optional[str]:
    rec = load_json(Path(project) / RECEIPT_REL)
    if "unmodified" not in (rec.get("arms") or []):
        return None
    return "riscv-arch-test " + _summary_text(arm_summary(rec, "unmodified"))


def coverage_line(covered: int, total: int) -> str:
    return (f"VIBEIC_FUNCTIONAL_COVERAGE dimension=instruction "
            f"covered={covered} total={total}")


def _case_rows(receipt: Dict[str, Any], cases: Dict[str, List[str]],
               progs: List[Dict[str, Any]], lock: Dict[str, Any],
               facts: Dict[str, Any], total: int, excluded: List[str],
               project: Path, write: bool) -> None:
    per = receipt["programs"]
    verdict_key = "staged_full"
    for case, units in cases.items():
        lines = [f"ISA suite producer — case {case} — units {units}",
                 receipt.get("label", "")]
        for d in receipt.get("deviation_disclosures") or []:
            lines.append(f"DISCLOSED {d}")
        states, primary = [], []
        for pid, e in per.items():
            if e["unit"] not in units:
                continue
            for key in sorted({k for k in e["arms"]} | {verdict_key}):
                st, why = _prog_state(e, key)
                lines.append(f"  [{st}] {e['role']:13s} {pid} @ {key}: {why}")
            if e["role"] == "primary":
                st, _w = _prog_state(e, verdict_key)
                states.append(st)
                primary.append(pid)
        verdict = fold(states)
        passed = sum(1 for s in states if s == PASS)
        for arm in receipt.get("arms") or []:
            if arm != "staged":
                s = [_prog_state(per[p], f"{arm}_full")[0] for p in primary]
                lines.append(f"ARM {arm}: {sum(1 for x in s if x == PASS)}/{len(s)} "
                             f"primary programs pass — evidence, not the verdict")
        lines.append(f"CASE {case} {verdict}: {passed}/{len(states)} primary "
                     f"programs pass on the verdict arm (staged RTL)")
        cov = None
        if "I" in units:
            unit = (lock.get("isa_units") or {}).get("I") or {}
            enum = [i for i in unit.get("instructions") or [] if i not in excluded]
            ok = []
            for ins in enum:
                ps = [e for e in per.values() if e["unit"] == "I"
                      and e["role"] == "primary" and e.get("instruction") == ins]
                if ps and all(_prog_state(e, verdict_key)[0] == PASS for e in ps):
                    ok.append(ins)
            cov = {"covered": len(ok), "total": total, "excluded": excluded,
                   "uncovered": [i for i in enum if i not in ok]}
            lines.append(f"excluded from the instruction total: "
                         f"{', '.join(excluded) or 'none'} "
                         f"({receipt['instruction_total']['why']})")
            lines.append(coverage_line(len(ok), total))
        receipt["cases"][case] = {"verdict": verdict, "passed": passed,
                                  "primary_programs": len(states),
                                  "coverage": cov}
        row_verdict = verdict if verdict in (PASS, FAIL) else "NOT_EXECUTED"
        receipt["rows"].append({
            "id": case, "verdict": row_verdict,
            "sim_executed": verdict in (PASS, FAIL),
            "tb_file": str(project / RECEIPT_REL),
            "detail": (f"ISA suite ({receipt.get('label')}): {passed}/"
                       f"{len(states)} primary programs pass"
                       + "".join(f"; DISCLOSED {d}" for d in
                                 receipt.get("deviation_disclosures") or []))})
        if write:
            _aa.write_text(project / TRANSCRIPT_REL.format(case=case),
                           "\n".join(lines) + "\n")


def merge_rows(existing: List[Dict[str, Any]], isa_rows: List[Dict[str, Any]]
               ) -> List[Dict[str, Any]]:
    """The ISA producer's row replaces any row the scaffold executor wrote for
    the same case id; every other row is kept as it was."""
    ids = {r["id"] for r in isa_rows}
    return [r for r in existing if r.get("id") not in ids] + list(isa_rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", nargs="?", type=Path)
    ap.add_argument("--inside", type=Path, default=None,
                    help=argparse.SUPPRESS)
    ap.add_argument("--work", type=Path, default=None,
                    help="scratch directory (default: a fresh temp dir)")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--extra-arm", action="append", default=[],
                    metavar="NAME=DIR",
                    help="also run this RTL directory as a named evidence arm "
                         "(falsifiers); never the verdict")
    ap.add_argument("--init", action="append", default=None,
                    help="power-up byte patterns (default: ff and a5)")
    ns = ap.parse_args(argv)
    if ns.inside:
        return inside(ns.inside)
    if ns.project is None:
        ap.error("project is required")
    extra = []
    for spec in ns.extra_arm:
        name, _, d = spec.partition("=")
        files = sorted(str(p) for p in list(Path(d).glob("*.v")) + list(Path(d).glob("*.sv")))
        extra.append({"name": name, "rtl_files": files})
    rec = produce(ns.project.resolve(), extra_arms=extra,
                  init_patterns=tuple(ns.init or INIT_PATTERNS),
                  work_root=ns.work, keep_work=ns.keep_work)
    if rec.get("refusal") and not rec.get("cases"):
        print(f"[NOT_MEASURED] {PRODUCER}: {rec['refusal']}")
        return 2
    for case, c in rec["cases"].items():
        print(f"[{c['verdict']}] {case}: {c['passed']}/{c['primary_programs']} "
              f"primary programs — {rec.get('label')}")
        if c.get("coverage"):
            print("    " + coverage_line(c["coverage"]["covered"],
                                         c["coverage"]["total"])
                  + f" (excluded: {c['coverage']['excluded']})")
    for arm in rec.get("arms") or []:
        if arm != "staged":
            print(f"    arm {arm}: {_summary_text(arm_summary(rec, arm))}")
    return 0 if all(c["verdict"] == PASS for c in rec["cases"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
