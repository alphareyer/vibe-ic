#!/usr/bin/env python3
"""full_stack_functional_tb.py — the FUNCTIONAL full-stack population (Step 5).

ENFORCEMENT: producer — writes evidence, decides no step. The Step-5 clause
`bit_level_full_stack_tb_check` reads the record this writes and is the only
reader that turns it into a verdict (R-0915-126: producer writes, gate reads).

WHY THIS EXISTS (FULLSTACKTB, 2026-09-29)
=========================================
Both benchmark ICs on the DIE route failed Step 5 for the same reason: the
full-stack testbench the flow generates is CONNECTIVITY-ONLY. Measured on the
source runs:

  spm          `tb_spm_full.v` binds the five ports, clocks the core for 2000
               cycles and checks that every output is not X. Zero L3 opcodes,
               zero golden compares; the step recorded `no_population`. A
               28-vector golden oracle existed -- against the bare CORE `spm`,
               not the pad-ring `chip_top` the die actually is.
  subservient  the same skeleton over the ten SRAM/GPIO ports; `results.json`
               `vectors_total: 0`, `functional_verified: false`.

and `bit_level_full_stack_tb_check` answered both with VACUOUS_PASS
(`DESIGN_DECLARED_NA`: "no command protocol") -- a design with no opcodes was
read as a design with no function.

WHAT IT DOES
============
For every L10 case the design's own input declares, the SAME real-oracle
ladder the Step-4 unit-TB producer uses (`testbench_gen.emit_unit_tbs` with the
substance-floor scaffold withheld:
delivered oracle, known-answer vector, stated vector over the declared bus,
declared-function golden, boot latency, reset invariants) is asked for a
self-checking testbench. Each family derives its expected value only from the
design INPUT -- the L-layer documents -- and is fail-closed: a case no family
can ground gets NO testbench, never a scaffold. The declared-function family is
handed the case itself (`case_stated=True`), so the population, the operand
corners and the reset-mid-computation scenario the case's OWN text states are
what it drives.

On a DIE route (`_tapeout_declaration.requests_pad_ring`, the one predicate the
phase-3 runner builds the ring on) the device under test is the PAD-RING chip
top that `io_pad_chip_top_gen` wrote, not the core: each testbench's core
instance is retargeted to the chip-top module (whose functional port surface
must be a superset of the core's -- refused otherwise), and it is compiled with
the PDK's own IO-cell and tie-cell simulation models (the closure
`sdf_gate_sim` already resolves for a routed chip top), staged into the run so
the simulator reads the bytes this record hashes. Before that chip top exists
(Phase 2 on a DIE route) nothing is run and the record says so.

Each testbench is compiled and run with iverilog (the pad models use switch
primitives) through the runner's ONE simulator dispatch site, and its transcript
is scored by `score_transcript`: PASS needs the case's own verdict marker, a
golden-count marker whose numerator equals its non-zero denominator, no FAIL
line and rc 0. A run that prints no marker is NOT_EXECUTED, never a pass.

THE STEP-5 BAR (R-0929-STEP5-BAR)
    PASS needs every declared case executed-and-passed through that top, or
    excluded by a named ruling, or declared not applicable by the design, or
    ISA-credited -- and ISA credit counts only when the CPU data-path program
    `cpu_datapath_program` builds from the design input (fetch, execute,
    store, load back over the DELIVERED memory) passed through the same top.
    See `unexecuted_disposition`.

OUTPUTS
    phase2/stage1/sim_full_stack/functional/<case>.v        the case testbench
    phase2/stage1/sim_full_stack/functional/run/cpu_datapath_program/
        cpu_datapath_program.hex                          the flow-built image
    phase2/stage1/sim_full_stack/functional/run/<case>/     build.log, run.log
    phase2/stage1/sim_full_stack/functional/models/         staged PDK models
    phase2/stage1/sim_full_stack/functional/functional_cases.json   the record

EXIT CODES (CLI)
    0  the record's verdict is PASS
    1  the record's verdict is FAIL (a case's oracle disagreed with the design)
    2  NOT_MEASURED (nothing executed, or the DIE's chip top is not built yet)

chip-AGNOSTIC: no design, PDK, cell or vendor literal. The module names come
from the design's RTL and the chip-top producer's record; the models from the
PDK tree that producer used.
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
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import _hdl_code_text as _hdl_text
import _path_layout as _pl
from _atomic_artefact import write_text as _atomic_write_text

PROGRAM = "full_stack_functional_tb"
SCHEMA = "vibeic.full_stack_functional.v1"
FUNCTIONAL_SUBDIR = "functional"
RECORD_NAME = "functional_cases.json"
#: The chip-top producer's record — the SAME path the phase-3 runner and
#: `sdf_gate_sim` read.
CHIP_TOP_RECORD_REL = "reports/phase3/io_pad_chip_top.json"
SIMULATOR = "iverilog"
BUILD_TIMEOUT_S = 900
RUN_TIMEOUT_S = 3600

# Case states. EXECUTED = PASSED | FAILED; everything else executed nothing.
PASSED = "passed"
FAILED = "failed"
ERRORED = "errored"            # the simulator ran and could not build / gave no verdict
NO_ORACLE = "no_oracle"        # no oracle family grounds the case
EXCLUDED = "excluded"          # the case's acceptance is not a functional verdict
SHORT = "short_population"     # executed, but fewer vectors than the case states
EXECUTED_STATES = (PASSED, FAILED)

# Record verdict words (the five, R-0915-85) and the taxonomy classes the gate
# publishes for a non-verdict.
PASS, FAIL, NOT_MEASURED = "PASS", "FAIL", "NOT_MEASURED"
ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
BLOCKED_BY_UPSTREAM = "BLOCKED_BY_UPSTREAM"
EXECUTION_ERROR = "EXECUTION_ERROR"

#: An acceptance that is a bare threshold PERCENTAGE ("≥ 95%") is a coverage
#: figure measured over a run, not a verdict a case oracle returns. "100% PASS"
#: is a verdict (every vector passes) and is NOT this shape.
_COVERAGE_FIGURE_RE = re.compile(
    r"^\s*(?:≥|>=|>|至少)?\s*\d+(?:\.\d+)?\s*%\s*$")


def functional_dir(project: Path) -> Path:
    return _pl.sim_full_stack_dir(project) / FUNCTIONAL_SUBDIR


def record_path(project: Path) -> Path:
    return functional_dir(project) / RECORD_NAME


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(project: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(project).resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# WHICH TOP A FUNCTIONAL FULL-STACK CASE MUST DRIVE
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(Path(path).read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def pad_ring_requested(project: Path, route: Optional[str] = None
                       ) -> Tuple[bool, str]:
    """(does this delivery carry a pad ring, basis).

    An explicit route wins (DIE carries one, HARDMACRO does not); otherwise the
    phase-3 runner's own predicate answers, so this cannot disagree with the
    run about whether a ring exists."""
    if route:
        r = str(route).strip().upper()
        if r not in ("DIE", "HARDMACRO"):
            raise ValueError(f"route must be DIE or HARDMACRO, got {route!r}")
        return r == "DIE", f"explicit route {r}"
    import _tapeout_declaration as _td
    ring = bool(_td.requests_pad_ring(project))
    return ring, ("_tapeout_declaration.requests_pad_ring: "
                  + ("a self-tape-out / taken slot that is not a HARDMACRO"
                     if ring else "no pad ring is requested for this delivery"))


def required_full_stack_top(project: Path, route: Optional[str] = None
                            ) -> Dict[str, Any]:
    """The top a functional full-stack case must instantiate.

    On a pad-ring route: the chip-top module `io_pad_chip_top_gen` recorded, in
    the file it recorded, hashed now. Absent / not WROTE -> `refusal` is set and
    NOTHING may be measured through it yet. Off the pad-ring route: the core,
    resolved from the RTL by the Step-4 unit-TB producer's own resolver."""
    ring, basis = pad_ring_requested(project, route)
    out: Dict[str, Any] = {"pad_ring": ring, "route_basis": basis,
                           "module": None, "source": None,
                           "source_sha256": None, "core_module": None,
                           "refusal": None, "refusal_class": None}
    if ring:
        rec = _read_json(Path(project) / CHIP_TOP_RECORD_REL)
        if not rec or rec.get("verdict") != "WROTE" \
                or not rec.get("chip_top_verilog") \
                or not rec.get("chip_top_module") or not rec.get("core_module"):
            out["refusal"] = (
                f"this delivery carries a pad ring ({basis}), so the full-stack "
                f"top is the pad-ring chip top `io_pad_chip_top_gen` writes; "
                f"{CHIP_TOP_RECORD_REL} records no written chip top yet, so no "
                f"functional case can be executed through it")
            out["refusal_class"] = BLOCKED_BY_UPSTREAM
            return out
        src = Path(project) / str(rec["chip_top_verilog"])
        if not src.is_file():
            out["refusal"] = (f"the chip-top record names {src} and that file "
                              f"is not on disk")
            out["refusal_class"] = EXECUTION_ERROR
            return out
        out.update(module=str(rec["chip_top_module"]),
                   source=str(rec["chip_top_verilog"]),
                   source_sha256=sha256_file(src),
                   core_module=str(rec["core_module"]))
        return out
    import testbench_gen as _tbg
    hint = _l9_top_module(project) or "chip_top"
    mod, _ports, why = _tbg.resolve_dut(project, hint)
    if mod is None:
        out["refusal"] = f"no core top resolves from the RTL: {why}"
        out["refusal_class"] = EXECUTION_ERROR
        return out
    out.update(module=mod, core_module=mod)
    return out


def _l9_top_module(project: Path) -> Optional[str]:
    d = _read_json(_pl.generated_docs_dir(project) / "L9_INTEGRATION_SPEC.json")
    if not d:
        return None
    v = d.get("top_module") or (d.get("fields") or {}).get("top_module")
    return str(v).strip() if isinstance(v, str) and v.strip() else None


def _instance_re(module: str) -> "re.Pattern":
    return re.compile(r"\b" + re.escape(module)
                      + r"(\s+(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
                        r"[A-Za-z_][A-Za-z0-9_$]*\s*(?:\[[^\]]*\]\s*)?\()")


def instance_count(text: str, module: str) -> int:
    """How many instances of `module` the HDL `text` makes (comments and
    strings blanked first, so a commented-out instance is not one)."""
    return len(_instance_re(module).findall(
        _hdl_text.strip_hdl_comments_and_strings(text)))


def retarget_instance(text: str, core: str, top: str
                      ) -> Tuple[Optional[str], str]:
    """Swap the testbench's ONE `core` instance for `top`, offsets from the
    comment-blanked text so a comment can neither be rewritten nor count."""
    blank = _hdl_text.strip_hdl_comments_and_strings(text)
    hits = list(_instance_re(core).finditer(blank))
    if len(hits) != 1:
        return None, (f"the testbench makes {len(hits)} instance(s) of the core "
                      f"`{core}`; exactly one is retargetable to `{top}`")
    m = hits[0]
    return (text[:m.start()] + top + text[m.start() + len(core):],
            f"core `{core}` instance retargeted to the full-stack top `{top}`")


# ---------------------------------------------------------------------------
# TRANSCRIPT SCORING — shared with the gate, which re-scores the logs
# ---------------------------------------------------------------------------
_TB_VERDICT_RE = re.compile(
    r"(?m)^\s*\[TB\s+([A-Za-z_][A-Za-z0-9_$]*)\]\s+(PASS|FAIL)\b")
_ORACLE_DONE_RE = re.compile(r"(?m)^\s*ORACLE_TB_DONE\s+pass=(\d+)/(\d+)")
_FAIL_LINE_RE = re.compile(r"(?m)^\s*(?:\[[^\]]*\]\s*)?FAIL\b")
_MISMATCH_RE = re.compile(r"(?m)^\s*ORACLE_MISMATCH\b")
#: R-0929-X-QUALIFIED — a cycle on which an output was X/Z and the oracle let
#: it pass because every qualifier the design input declares for it was known
#: and inactive. Carried into the record so the exemption is visible.
_X_EXEMPT_RE = re.compile(r"(?m)^\s*\[TB\s+[A-Za-z_][A-Za-z0-9_$]*\]\s+"
                          r"X_EXEMPT:\s*(.*)$")


def x_exemptions(text: str) -> List[str]:
    return [m.group(1).strip() for m in _X_EXEMPT_RE.finditer(text or "")]


def population_shortfall(want: Optional[int], checks: Optional[dict]
                         ) -> Optional[str]:
    """Why an executed-and-matched case did NOT measure the population its own
    text states, or None. A transcript that prints no golden count (a property
    oracle's bare `[TB x] PASS`) measured ONE scenario, so a stated population
    above one is short -- never assumed met."""
    if not want or want <= 1:
        return None
    got = (checks or {}).get("total")
    if not isinstance(got, int):
        return (f"the transcript states no executed vector count (one "
                f"scenario), but the case's own text states a population of "
                f"{want}")
    if got < want:
        return (f"{got} vector(s) executed and passed, but the case's own text "
                f"states a population of {want}")
    return None


def score_transcript(case: str, rc: Optional[int], text: str
                     ) -> Dict[str, Any]:
    """The case's verdict from ITS OWN simulation transcript.

    PASSED  rc 0, no FAIL / ORACLE_MISMATCH line, and either the case's own
            `[TB <case>] PASS` marker or an `ORACLE_TB_DONE pass=n/n`, n > 0.
    FAILED  the transcript carries the case's FAIL marker, any FAIL line, an
            ORACLE_MISMATCH, or a golden count below its denominator.
    ERRORED anything else — including a clean exit that printed no verdict,
            which is a testbench that judged nothing and is never a pass."""
    text = text or ""
    res = _score_transcript(case, rc, text)
    ex = x_exemptions(text)
    if ex:
        res["x_exemptions"] = ex
    return res


def _score_transcript(case: str, rc: Optional[int], text: str
                      ) -> Dict[str, Any]:
    done = list(_ORACLE_DONE_RE.finditer(text))
    words = [m.group(2) for m in _TB_VERDICT_RE.finditer(text)
             if m.group(1) == case]
    fail_line = bool(_FAIL_LINE_RE.search(text) or _MISMATCH_RE.search(text))
    checks = None
    if done:
        a, b = int(done[-1].group(1)), int(done[-1].group(2))
        checks = {"passed": a, "total": b}
        if b == 0:
            return {"state": ERRORED, "checks": checks,
                    "message": "ORACLE_TB_DONE over an empty population"}
        if a == b and not fail_line and rc == 0 and "FAIL" not in words:
            return {"state": PASSED, "checks": checks,
                    "message": f"{a}/{b} golden-scored vectors matched"}
        return {"state": FAILED, "checks": checks,
                "message": f"{a}/{b} golden-scored vectors matched"
                           + ("; ORACLE_MISMATCH" if fail_line else "")
                           + (f"; rc={rc}" if rc not in (0, None) else "")}
    if "FAIL" in words or fail_line:
        return {"state": FAILED, "checks": checks,
                "message": "the case's oracle reported FAIL"}
    if "PASS" in words and rc == 0:
        return {"state": PASSED, "checks": checks,
                "message": "the case's oracle reported PASS"}
    return {"state": ERRORED, "checks": checks,
            "message": (f"no verdict marker for `{case}` in the transcript "
                        f"(rc={rc}) — the testbench judged nothing")}


def acceptance_is_coverage_figure(case: dict) -> bool:
    return bool(_COVERAGE_FIGURE_RE.match(str(case.get("expected") or "")))


# ---------------------------------------------------------------------------
# MODELS FOR THE PAD-RING TOP
# ---------------------------------------------------------------------------
_INST_TOKEN_RE = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+(?:#\s*\([^;]*?\)\s*)?"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*\(")
_KEYWORDS = frozenset("""module endmodule input output inout wire reg assign
always initial begin end if else for case endcase function task generate
localparam parameter integer supply0 supply1 tri""".split())
_MODULE_DEF_RE = re.compile(r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)")


def masters_instantiated(text: str) -> List[str]:
    blank = _hdl_text.strip_hdl_comments_and_strings(text)
    out = []
    for m in _INST_TOKEN_RE.finditer(blank):
        mod = m.group(1)
        if mod in _KEYWORDS or m.group(2) in _KEYWORDS:
            continue
        if mod not in out:
            out.append(mod)
    return out


def default_model_resolver(project: Path, used_cells: set,
                           container: Optional[str]
                           ) -> Tuple[Optional[Dict[str, str]], str]:
    """{source path: model text} for `used_cells`, from the PDK tree the
    chip-top producer used (via `sdf_gate_sim`'s resolver), or (None, why)."""
    if not container:
        return None, "no EDA container to read the PDK models from"
    # NAME THE ATTACH REFUSAL. `sdf_gate_sim` reads the models through the
    # guarded exec, which refuses a container whose image is not the pinned
    # runtime and then reports only "no model". MEASURED on the replay host
    # when a newer release landed mid-session: the record said "no PDK cell
    # Verilog model resolves" over a PDK that was there, behind a refused exec.
    import _eda_pin
    state, detail = _eda_pin.container_pin_state(container)
    if state == "MISMATCH":
        return None, f"the EDA container cannot be attached: {detail}"
    import sdf_gate_sim as _sgs
    models = _sgs.resolve_cell_models(project, used_cells, container)
    if models is None:
        return None, "no PDK cell Verilog model resolves for the chip top's cells"
    models = _sgs.declared_io_models(project, container, used_cells, models)
    files = {p: models.files.get(p) or "" for p in models.paths}
    if not all(files.values()):
        return None, "a resolved PDK model file could not be read"
    return files, f"PDK models via sdf_gate_sim ({models.source})"


def stage_models(project: Path, out_dir: Path, used_cells: set,
                 container: Optional[str],
                 resolver: Optional[Callable] = None
                 ) -> Tuple[Optional[List[Dict[str, str]]], str]:
    """Stage the models the chip top needs into the run, or refuse by name."""
    files, why = (resolver or default_model_resolver)(project, used_cells,
                                                     container)
    if not files:
        return None, why
    defined = set()
    for text in files.values():
        defined.update(_MODULE_DEF_RE.findall(
            _hdl_text.strip_hdl_comments_and_strings(text)))
    missing = sorted(set(used_cells) - defined)
    if missing:
        return None, (f"no simulation model defines {missing[:8]} — the chip "
                      f"top cannot be elaborated")
    mdir = out_dir / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    staged = []
    for i, (src, text) in enumerate(files.items()):
        dst = mdir / f"{i:02d}_{Path(str(src)).name}"
        dst.write_text(text)
        staged.append({"source": str(src), "path": _rel(project, dst),
                       "sha256": sha256_file(dst)})
    return staged, why


# ---------------------------------------------------------------------------
# DISPATCH
# ---------------------------------------------------------------------------
def default_dispatch(argv: List[str], run_dir: Path, container: Optional[str],
                     tool: str, timeout: int) -> Tuple[int, str]:
    """The runner's ONE simulator dispatch site (via testbench_gen)."""
    import testbench_gen as _tbg
    return _tbg.default_dispatch(argv, run_dir, container, tool, timeout)


def _rtl_sources(project: Path) -> List[Path]:
    import testbench_gen as _tbg
    rtl = _pl.rtl_dir(project)
    if not rtl.is_dir():
        return []
    return _tbg.package_first_order(sorted(rtl.glob("*.sv"))
                                    + sorted(rtl.glob("*.v")))


# ---------------------------------------------------------------------------
# THE PRODUCER
# ---------------------------------------------------------------------------
def _design_input_digest(project: Path) -> Dict[str, Optional[str]]:
    gd = _pl.generated_docs_dir(project)
    out: Dict[str, Optional[str]] = {}
    for name in ("L10_TEST_CASES.json", "L2_FRS.json", "L9_INTEGRATION_SPEC.json",
                 "L8_RTL_CONSTANTS.json", "L8_TIMING_WAVEFORM.json"):
        f = gd / name
        out[name] = sha256_file(f) if f.is_file() else None
    return out


def _family_of(text: str) -> str:
    import testbench_gen as _tbg
    m = re.search(re.escape(_tbg.ORACLE_GENERATED_MARKER) + r"(\w+)", text)
    if m:
        return _tbg.ORACLE_FAMILY_BY_EMITTER.get(m.group(1), m.group(1))
    return "authored_or_delivered"


def _not_run_detail(project: Path, case: dict, ic_class: Optional[str],
                    report: dict, step4: Dict[str, Any]) -> Dict[str, Any]:
    """What is known about a case no oracle family grounds -- disclosure only."""
    import testbench_gen as _tbg
    name = str(case.get("name") or "")
    out: Dict[str, Any] = {}
    try:
        gap = _tbg.case_input_gap(project, case, ic_class)
    except Exception as exc:  # noqa: BLE001 — disclosure never breaks the run
        gap = {"error": repr(exc)}
    if gap:
        out["input_gap"] = gap
    if isinstance(case.get("applies_when"), dict):
        out["applies_when"] = case["applies_when"]
    for key in ("known_answer_vector_unbound", "stated_vector_unbound",
                "delivered_oracle_refusals", "oracle_errors"):
        rows = [r for r in (report.get(key) or [])
                if isinstance(r, dict) and str(r.get("case")) == name]
        if rows:
            out[key] = rows
    if step4:
        try:
            import _l10_execution as _l10x
            st, why = _l10x.case_state(name, step4)
            out["step4_execution"] = {"state": st, "detail": why}
        except Exception:  # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------------------
# R-0929-STEP5-BAR — what every declared case must be for Step 5 to PASS
# ---------------------------------------------------------------------------
# Root ruling (IC expert, 2026-09-29), found by FULLSTACKTB: the PASS bar
# counted only EXECUTED cases (subservient: 3 executed, 7 no_oracle including
# every CPU data-path case, and the record read PASS over them). Every L10 case
# must now be (a) executed and passed through the required full-stack top, or
# (b) excluded by a named ruling -- a firmware row whose named image the input
# lacks (R-0929-OWNER-SUB-ACCEPT (2)) -- or (c) credited by the ISA-suite rule
# (R-0929-OWNER-SUB-ACCEPT (1)), and (c) counts here ONLY when a CPU data-path
# case (`cpu_datapath_program`: fetch, execute, load/store over the delivered
# memory) executed and passed through that same top. Anything else is
# NOT_MEASURED: no case passes by construction.
#
# Two further dispositions were already landed contracts and are kept, each
# re-derived from the design input, never read off a record label: a case
# whose acceptance is a coverage FIGURE is not a functional case (FULLSTACKTB),
# and a case conditional on an option the design declares it does not have
# (`applies_when` against the declaration's selection, the reader the Step-4
# gate uses) is not a case of the delivered configuration.
DISP_EXCLUDED = "excluded_by_ruling"
DISP_NOT_APPLICABLE = "design_declared_na"
DISP_ISA_CREDITED = "isa_credited"
DISP_UNMEASURED = "not_measured"
DATAPATH_KEY = "cpu_datapath"


def unexecuted_disposition(project: Path, case: dict, ic_class: Optional[str],
                           datapath_passed: bool,
                           top_module: Optional[str] = None) -> Dict[str, Any]:
    """How a declared case that did not execute stands against the Step-5 bar.

    Asked of the design input every time (the producer AND the gate call it);
    `blocking` is True unless a named ruling or a landed declared-NA contract
    places the case outside the verdict."""
    name = str(case.get("name") or case.get("id") or "")
    import testbench_gen as _tbg
    ex = _tbg.input_absent_exclusion(project, case, ic_class)
    if ex:
        return {"case": name, "disposition": DISP_EXCLUDED, "blocking": False,
                "ruling": "R-0929-OWNER-SUB-ACCEPT (2)", "record": ex}
    import cpu_functional_oracle_waiver_check as _cfw
    _app, na = _cfw.split_design_declared_na(
        [case], _cfw.design_selected_options(project))
    if na:
        aw = case.get("applies_when") or {}
        return {"case": name, "disposition": DISP_NOT_APPLICABLE,
                "blocking": False,
                "basis": {"option": aw.get("option"), "stated": aw.get("stated"),
                          "design_selected": sorted(
                              _cfw.design_selected_options(project) or [])},
                "note": ("the case is conditional on an option the design "
                         "declares it does not have; not a case of the "
                         "delivered configuration, and not claimed verified")}
    import _l10_execution as _l10x
    credit, refusal = _l10x.isa_conformance_credit(project, name, case)
    if credit:
        if datapath_passed:
            return {"case": name, "disposition": DISP_ISA_CREDITED,
                    "blocking": False,
                    "ruling": ("R-0929-OWNER-SUB-ACCEPT (1), counted at Step 5 "
                               "by R-0929-STEP5-BAR (c)"),
                    "sentence": credit.get("sentence")}
        return {"case": name, "disposition": DISP_UNMEASURED, "blocking": True,
                "reason": (
                    f"ISA-suite credit exists ({credit.get('sentence')}), but "
                    f"R-0929-STEP5-BAR counts it at Step 5 only when a CPU "
                    f"data-path case executed and passed through "
                    f"`{top_module}` at the delivered memory size, and none "
                    f"did")}
    return {"case": name, "disposition": DISP_UNMEASURED, "blocking": True,
            "reason": (
                "not executed through the full-stack top, not excluded by a "
                "named ruling and not credited"
                + (f" (ISA credit refused: {refusal})" if refusal else ""))}


def generate(project: Path, container: Optional[str] = None,
             route: Optional[str] = None,
             dispatch: Optional[Callable] = None,
             model_resolver: Optional[Callable] = None) -> Dict[str, Any]:
    """Emit, run and score the functional full-stack population; write the
    record; return it. Never raises for a design reason — the record says."""
    t0 = time.time()
    project = Path(project)
    import testbench_gen as _tbg
    fdir = functional_dir(project)
    # The directory is this producer's alone, so a stale case TB can never
    # outlive the run that would have regenerated it.
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True, exist_ok=True)
    rec: Dict[str, Any] = {
        "program": PROGRAM, "schema": SCHEMA,
        "generated_at_unix": time.time(),
        "simulator": SIMULATOR,
        "design_input": _design_input_digest(project),
        "cases": [], "sources": [], "models": [],
    }

    def _finish(verdict: str, reason_class: Optional[str], why: str
                ) -> Dict[str, Any]:
        cases = rec["cases"]
        counts = {s: sum(1 for c in cases if c.get("state") == s)
                  for s in (PASSED, FAILED, ERRORED, NO_ORACLE, EXCLUDED, SHORT)}
        counts["rows"] = len(cases)
        counts["executed"] = counts[PASSED] + counts[FAILED]
        rec.update(verdict=verdict, reason_class=reason_class, reason=why,
                   counts=counts, elapsed_s=round(time.time() - t0, 1))
        _atomic_write_text(record_path(project),
                           json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        return rec

    try:
        top = required_full_stack_top(project, route)
    except ValueError as exc:
        return _finish(NOT_MEASURED, EXECUTION_ERROR, str(exc))
    rec["full_stack_top"] = top
    if top.get("refusal"):
        return _finish(NOT_MEASURED, top.get("refusal_class") or EXECUTION_ERROR,
                       top["refusal"])
    try:
        cases = _tbg.load_l10_cases(project)
    except (OSError, ValueError) as exc:
        return _finish(NOT_MEASURED, EXECUTION_ERROR,
                       f"L10_TEST_CASES.json is unreadable: {exc}")
    if not cases:
        return _finish(NOT_MEASURED, ZERO_DENOMINATOR,
                       "the design input declares no L10 test case, so there "
                       "is no functional case to run through the full stack")
    core = str(top["core_module"])
    core_mod, core_ports, why = _tbg.resolve_dut(project, core)
    if core_mod is None:
        return _finish(NOT_MEASURED, EXECUTION_ERROR,
                       f"the core `{core}` does not resolve from the RTL: {why}")
    sources: List[Path] = []
    if top["pad_ring"]:
        top_src = project / top["source"]
        top_text = top_src.read_text(errors="replace")
        top_ports = {n for _d, _w, n in _tbg._parse_ports(top_text,
                                                          top["module"])}
        missing = sorted({n for _d, _w, n in core_ports} - top_ports)
        if not top_ports or missing:
            return _finish(NOT_MEASURED, EXECUTION_ERROR, (
                f"the pad-ring top `{top['module']}` does not expose the core's "
                f"port(s) {missing[:8]} — a case written against the core "
                f"cannot be driven through it"))
        rtl_defined = set()
        for f in _rtl_sources(project):
            rtl_defined.update(_MODULE_DEF_RE.findall(
                _hdl_text.strip_hdl_comments_and_strings(
                    f.read_text(errors="replace"))))
        used = {m for m in masters_instantiated(top_text)
                if m not in rtl_defined and m != top["module"]}
        staged, mwhy = stage_models(project, fdir, used, container,
                                    model_resolver)
        rec["model_resolution"] = mwhy
        if staged is None:
            return _finish(NOT_MEASURED, EXECUTION_ERROR,
                           f"the pad-ring top's cell models: {mwhy}")
        rec["models"] = staged
        sources += [project / s["path"] for s in staged]
        sources.append(top_src)
    rtl_files = _rtl_sources(project)
    sources += rtl_files
    rec["sources"] = [{"path": _rel(project, s), "sha256": sha256_file(s)}
                      for s in sources]

    ic_class = _tbg._detect_ic_class(project)
    rec["ic_class"] = ic_class
    report: Dict[str, Any] = {}
    step4: Dict[str, Any] = {}
    try:
        import _l10_execution as _l10x
        step4 = _l10x.load_record(project)
    except Exception:  # noqa: BLE001
        step4 = {}
    disp = dispatch or default_dispatch
    run_root = fdir / "run"
    # ONE call into the Step-4 unit-TB producer's own ladder, into this
    # producer's directory, with the substance-floor scaffold withheld: a case
    # no real oracle grounds gets no file here, never a connectivity stand-in.
    n_emitted = _tbg.emit_unit_tbs(project, core_mod, None, report,
                                   out_dir=fdir, scaffold=False,
                                   case_stated=True)
    rec["ladder"] = {"emitted": n_emitted,
                     "dut_resolution": report.get("dut_resolution"),
                     "reason": report.get("reason")}
    for case in cases:
        name = str(case.get("name") or case.get("id") or "")
        entry: Dict[str, Any] = {
            "name": name,
            "l10_row": {k: case.get(k) for k in
                        ("kind", "stimulus", "expected", "evidence")
                        if case.get(k) is not None},
        }
        rec["cases"].append(entry)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", name):
            entry.update(state=NO_ORACLE,
                         reason=f"case name {name!r} is not a legal identifier")
            continue
        if acceptance_is_coverage_figure(case):
            # The ladder may have grounded it; a testbench for a case that is
            # not counted must not sit beside the ones that are.
            (fdir / f"{name}.v").unlink(missing_ok=True)
            entry.update(state=EXCLUDED, reason=(
                f"the case's acceptance `{case.get('expected')}` is a coverage "
                f"FIGURE measured over a run, not a verdict a case oracle "
                f"returns; it is not a functional full-stack case and is not "
                f"counted as one"))
            continue
        path = fdir / f"{name}.v"
        if not path.is_file():
            entry.update(state=NO_ORACLE, reason=(
                "no oracle family grounds this case from the design input"),
                **_not_run_detail(project, case, ic_class, report, step4))
            continue
        text = path.read_text(errors="replace")
        entry["family"] = _family_of(text)
        if top["pad_ring"]:
            new, rwhy = retarget_instance(text, core_mod, top["module"])
            if new is None:
                entry.update(state=NO_ORACLE, reason=rwhy,
                             tb=_rel(project, path))
                continue
            text = new
            path.write_text(text)
            entry["retarget"] = rwhy
        entry["tb"] = _rel(project, path)
        entry["tb_sha256"] = sha256_file(path)
        entry["instantiates"] = top["module"]
        try:
            import arith_oracle_tb_gen as _aog
            entry["declared_population"] = _aog.declared_vector_population(case)
        except Exception:  # noqa: BLE001
            entry["declared_population"] = None
        wd = run_root / name
        wd.mkdir(parents=True, exist_ok=True)
        vvp = wd / f"{name}.vvp"
        argv = ([SIMULATOR, "-g2012", "-s", name, "-o", str(vvp)]
                + [str(s) for s in sources] + [str(path)])
        brc, blog = disp(argv, wd, container, SIMULATOR, BUILD_TIMEOUT_S)
        (wd / "build.log").write_text(" ".join(argv) + "\n\n" + (blog or ""))
        entry["build_log"] = _rel(project, wd / "build.log")
        entry["build_rc"] = brc
        if brc != 0:
            entry.update(state=ERRORED, reason=(
                f"the simulator could not build this case (rc={brc}); nothing "
                f"about the design was judged"))
            continue
        rrc, rlog = disp(["vvp", "-n", str(vvp)], wd, container, SIMULATOR,
                         RUN_TIMEOUT_S)
        run_log = wd / "run.log"
        run_log.write_text(rlog or "")
        entry["run_log"] = _rel(project, run_log)
        entry["run_log_sha256"] = sha256_file(run_log)
        entry["run_rc"] = rrc
        score = score_transcript(name, rrc, rlog or "")
        entry.update(state=score["state"], checks=score["checks"],
                     reason=score["message"])
        if score.get("x_exemptions"):
            entry["x_exemptions"] = score["x_exemptions"]
        short = population_shortfall(entry.get("declared_population"),
                                     score["checks"])
        if score["state"] == PASSED and short:
            entry.update(state=SHORT, reason=short)
    dp = _datapath_case(project, core_mod, core_ports, top, sources, fdir,
                        run_root, disp, container,
                        {c["name"] for c in rec["cases"]})
    rec[DATAPATH_KEY] = dp
    dp_passed = dp.get("state") == PASSED
    bar = {"datapath": dp.get("state"), "dispositions": []}
    for case, entry in zip(cases, rec["cases"]):
        if entry.get("state") != NO_ORACLE:
            continue
        d = unexecuted_disposition(project, case, ic_class, dp_passed,
                                   top["module"])
        entry["step5_disposition"] = d
        bar["dispositions"].append(d)
    rec["step5_bar"] = bar
    blocking = [d["case"] for d in bar["dispositions"] if d["blocking"]]
    executed = sum(1 for c in rec["cases"] if c.get("state") in EXECUTED_STATES)
    failed = sum(1 for c in rec["cases"] if c.get("state") == FAILED)
    short = sum(1 for c in rec["cases"] if c.get("state") == SHORT)
    errored = sum(1 for c in rec["cases"] if c.get("state") == ERRORED)
    if failed or dp.get("state") == FAILED:
        return _finish(FAIL, None, (
            f"{failed} functional case(s)"
            + (" and the CPU data-path program" if dp.get("state") == FAILED
               else "")
            + f" through `{top['module']}` disagreed with the oracle their "
              f"design input states"))
    if short:
        return _finish(NOT_MEASURED, ZERO_DENOMINATOR, (
            f"{short} case(s) executed fewer vectors than their own text "
            f"states; that population was not measured"))
    if errored and executed:
        # A case with an oracle and a testbench that was built or run and gave
        # no verdict (build failure, simulator crash, a timeout with no marker
        # -- a reset scenario that never finishes is exactly that) is part of
        # the population and was not measured. Dropping it from the
        # denominator would let a design-caused hang read green.
        return _finish(NOT_MEASURED, EXECUTION_ERROR, (
            f"{errored} case(s) with an emitted testbench could not be built or "
            f"gave no verdict; {executed} executed — the population was not "
            f"fully measured"))
    if executed == 0:
        return _finish(NOT_MEASURED,
                       EXECUTION_ERROR if errored else ZERO_DENOMINATOR, (
            f"no functional case executed through `{top['module']}` "
            f"({errored} could not be built or gave no verdict; "
            f"{len(rec['cases']) - errored} were not grounded by any oracle "
            f"family or are not functional cases)"))
    if blocking:
        return _finish(NOT_MEASURED, ZERO_DENOMINATOR, (
            f"R-0929-STEP5-BAR: {len(blocking)} declared case(s) "
            f"{blocking[:8]} were neither executed through `{top['module']}`, "
            f"nor excluded by a named ruling, nor credited; {executed} "
            f"executed and passed"))
    kinds = {k: sum(1 for d in bar["dispositions"] if d["disposition"] == k)
             for k in (DISP_EXCLUDED, DISP_NOT_APPLICABLE, DISP_ISA_CREDITED)}
    return _finish(PASS, None, (
        f"{executed} functional case(s) executed through `{top['module']}` and "
        f"every one matched the oracle its design input states"
        + (f"; CPU data-path program PASSED" if dp_passed else "")
        + "".join(f"; {n} {k}" for k, n in kinds.items() if n)))


def _run_case(name: str, path: Path, sources: List[Path], wd: Path,
              disp: Callable, container: Optional[str], project: Path
              ) -> Dict[str, Any]:
    """Build and run one case testbench; the transcript scored, nothing else."""
    wd.mkdir(parents=True, exist_ok=True)
    vvp = wd / f"{name}.vvp"
    argv = ([SIMULATOR, "-g2012", "-s", name, "-o", str(vvp)]
            + [str(s) for s in sources] + [str(path)])
    brc, blog = disp(argv, wd, container, SIMULATOR, BUILD_TIMEOUT_S)
    (wd / "build.log").write_text(" ".join(argv) + "\n\n" + (blog or ""))
    out: Dict[str, Any] = {"build_log": _rel(project, wd / "build.log"),
                           "build_rc": brc}
    if brc != 0:
        out.update(state=ERRORED, reason=(
            f"the simulator could not build this case (rc={brc}); nothing "
            f"about the design was judged"))
        return out
    rrc, rlog = disp(["vvp", "-n", str(vvp)], wd, container, SIMULATOR,
                     RUN_TIMEOUT_S)
    run_log = wd / "run.log"
    run_log.write_text(rlog or "")
    score = score_transcript(name, rrc, rlog or "")
    out.update(run_log=_rel(project, run_log),
               run_log_sha256=sha256_file(run_log), run_rc=rrc,
               state=score["state"], checks=score["checks"],
               reason=score["message"])
    return out


def _datapath_case(project: Path, core_mod: str, core_ports: list,
                   top: Dict[str, Any], sources: List[Path], fdir: Path,
                   run_root: Path, disp: Callable, container: Optional[str],
                   taken: set) -> Dict[str, Any]:
    """Build (from the design input), retarget and run the CPU data-path
    program — R-0929-STEP5-BAR's case (c) precondition. Never raises."""
    import cpu_datapath_program as _cdp
    name = _cdp.CASE_NAME
    entry: Dict[str, Any] = {"name": name, "schema": _cdp.SCHEMA}
    if name in taken:
        entry.update(state=NO_ORACLE, reason=(
            f"a declared L10 case is already named `{name}`"))
        return entry
    try:
        built, why = _cdp.build(project, core_mod, core_ports, name)
    except Exception as exc:  # noqa: BLE001 — a builder crash is not a pass
        built, why = None, f"the data-path builder raised {exc!r}"
    if built is None:
        entry.update(state=NO_ORACLE, reason=(
            f"no CPU data-path program is built from the design input: {why}"))
        return entry
    entry.update({k: built[k] for k in ("program", "data_address",
                                        "expected_words", "facts",
                                        "delivered_memsize_bytes")})
    text = built["tb_text"]
    if top["pad_ring"]:
        text, rwhy = retarget_instance(text, core_mod, top["module"])
        if text is None:
            entry.update(state=NO_ORACLE, reason=rwhy)
            return entry
        entry["retarget"] = rwhy
    path = fdir / f"{name}.v"
    path.write_text(text)
    wd = run_root / name
    wd.mkdir(parents=True, exist_ok=True)
    hexp = wd / built["hex_name"]
    hexp.write_text(built["hex_text"])
    entry.update(tb=_rel(project, path), tb_sha256=sha256_file(path),
                 hex=_rel(project, hexp), hex_sha256=sha256_file(hexp),
                 instantiates=top["module"])
    entry.update(_run_case(name, path, sources, wd, disp, container, project))
    return entry


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--container", default=None)
    ap.add_argument("--route", default=None, choices=["DIE", "HARDMACRO"])
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    container = args.container
    if container is None:
        import _eda_pin as _pin
        container = _pin.default_container_name()
    rec = generate(args.project.resolve(), container, args.route)
    text = json.dumps({k: rec.get(k) for k in
                       ("verdict", "reason_class", "reason", "counts",
                        "full_stack_top")}, indent=2, ensure_ascii=False)
    if args.json:
        _atomic_write_text(args.json, text + "\n")
    print(text)
    return {PASS: 0, FAIL: 1}.get(rec.get("verdict"), 2)


if __name__ == "__main__":
    sys.exit(main())
