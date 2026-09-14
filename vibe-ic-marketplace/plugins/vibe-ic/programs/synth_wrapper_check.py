#!/usr/bin/env python3
"""
synth_wrapper_check.py — Deterministic compliance check for synth-wrapper-gen.

ENFORCEMENT: advisory

The line above is a DECLARATION, in the anchored form `flow_gate_enforcement_
audit.declared_intent` reads. This program is wired into the flow as an
`advisory_program_exit_zero` clause: it RUNS on every project that reaches its
step, its findings are printed, and its exit code cannot deny the step its PASS
tier. That is deliberate — it was wired to make a real check reachable, not to
block a landing on debt it did not create — and the declaration says so where
the audit looks. Without it, "wired where it cannot block" and "nobody decided"
are the same record, and the reliable way to stay clean is to say nothing.
Verifies that synthesis wrapper files exist and contain valid module declarations,
DUT instantiations, and proper bidirectional port handling.

What it catches:
  1. NO_WRAPPER — no .v or .sv files matching *wrapper* found
  2. NO_MODULE_DECL — wrapper file has no 'module' declaration
  3. NO_DUT_INST — wrapper file has no module instantiation (no DUT being wrapped)
  4. STUB_FILE — wrapper has fewer than 5 lines of actual code
  5. NO_INOUT_DESIGN — no wrapper files found, but also no inout ports in design (INFO)

Usage:
    python3 synth_wrapper_check.py ./my_project
    python3 synth_wrapper_check.py ./my_project --json

Exit codes:
    0 = valid wrapper found, or no wrapper needed (no inout ports)
    1 = wrapper expected but missing or invalid

Generality: works for ANY IC project with synthesis wrappers.
No external tool dependencies — pure Python.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Python puts a file's own directory on `sys.path` only when that file is run
# as `__main__`; under `importlib.util.spec_from_file_location` — how the gates
# and much of the suite load a program — it does not, so the sibling imports
# below would raise ModuleNotFoundError. Restore the condition the file is
# written for. Idempotent, and the shape the sibling programs already use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple

import _design_module_set as _dms
import _gate_denominator as _gd
import _hdl_code_text  # offset-preserving comment/string blanker (#731)
import _path_layout as _pl


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    rule: str
    severity: str       # ERROR, WARNING, INFO
    message: str
    file: str = ""


@dataclass
class AuditResult:
    program: str
    passed: bool
    findings: List[Finding] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
MODULE_DECL_RE = re.compile(r'\bmodule\s+\w+', re.MULTILINE)
# Module instantiation: ModuleName [#(params)] instance_name (
INST_RE = re.compile(
    r'^\s*(\w+)\s+(?:#\s*\([^)]*\)\s*)?(\w+)\s*\(',
    re.MULTILINE,
)
INOUT_RE = re.compile(r'\binout\b', re.MULTILINE)

# Verilog keywords — cannot be module instantiation targets
VERILOG_KEYWORDS = {
    'module', 'endmodule', 'input', 'output', 'inout', 'wire', 'reg',
    'logic', 'assign', 'always', 'always_ff', 'always_comb', 'always_latch',
    'initial', 'begin', 'end', 'if', 'else', 'case', 'endcase', 'for',
    'while', 'parameter', 'localparam', 'generate', 'endgenerate', 'genvar',
    'function', 'endfunction', 'task', 'endtask', 'integer', 'real',
    'typedef', 'struct', 'enum', 'packed', 'signed', 'unsigned',
    'supply0', 'supply1', 'tri', 'wand', 'wor',
}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def discover_wrapper_files(base: Path) -> List[Path]:
    """Find .v or .sv files with 'wrapper' in the name."""
    found: List[Path] = []
    for ext in ("*.v", "*.sv"):
        for fpath in sorted(base.rglob(ext)):
            if "wrapper" in fpath.name.lower():
                found.append(fpath)
    return found


#: THE DESIGN'S OWN RTL SET IS THE SUBJECT, NOT THE PROJECT TREE.
#:
#: `design_has_inout` used to `base.rglob("*.v"/"*.sv")` over the WHOLE project
#: and answer True on the first `\binout\b` in any of them, over text nobody
#: stripped. MEASURED on a completed gf180mcuD run of `spm` (2026-09-15, lane
#: icspm2), the design declares NO inout anywhere — `phase2/stage1/rtl/spm.v`
#: has five ports, `input wire clk/rst/x/y` and `output wire p` — and the gate
#: still reported
#:
#:     [ERROR] NO_WRAPPER: No *wrapper*.v or *wrapper*.sv files found, but
#:                         design has inout ports
#:
#: on the strength of exactly three matches, none of which is a design port:
#:
#:   * `phase2/stage1/sim_full_stack/tb_spm_full.v:51`
#:         `// No inout pad in L9; drive_byte is a no-op for sync compatibility.`
#:     — a COMMENT whose sentence says there is no inout, read as proof of one.
#:     This is the #731 shape exactly: a declaration scanned out of text no
#:     stripper touched.
#:   * `phase3/stage3/pnr/spm_pnr.v:13-14` and
#:     `phase3/stage3/extracted/spm_pwraware_welltied.v:13-14`
#:         `inout VDD; inout VSS;`
#:     — the supply pins THE FLOW ITSELF writes into every power-aware netlist
#:     it emits. They are a property of the flow's own back end, present on
#:     every design that reaches phase 3, and they are not bidirectional signal
#:     ports. A gate that reads them refuses every routed design on evidence it
#:     manufactured one step earlier.
#:
#: So the population is now the same one the SYNTH STEP READS — the design's
#: RTL directory, `_path_layout.rtl_dir(project)`, which is the directory
#: `step_yosys_synth` hands to `read_verilog` — and the text is stripped of
#: comments and string literals before any declaration regex sees it. A design
#: that TRULY declares an inout port in its own RTL still needs its wrapper and
#: still gets `NO_WRAPPER`; that is `test_a_real_design_inout_still_demands_a_
#: wrapper`.
#:
#: The testbench is deliberately outside the population too. It is not the
#: design, and `synth_wrapper_gen`'s subject is the design's ports.
_DESIGN_RTL_UNIT = "design RTL source file under phase2/stage1/rtl"


def design_rtl_sources(base: Path) -> List[Path]:
    """The design's own RTL sources — the population the synth step reads.

    `_path_layout.rtl_dir` is the single place that spells that directory, and
    it is the one `design_one_shot_runner.step_yosys_synth` hands to
    `read_verilog`. When the project carries the flow's layout, that directory
    IS the population, and nothing under `phase3/` or `sim_full_stack/` enters.

    STANDALONE USE — this program's own docstring documents
    `synth_wrapper_check.py ./my_project` on a plain directory of sources, and
    `test_no_wrapper_no_inout_pass` / `test_no_wrapper_with_inout_fail` pin that
    shape: RTL written at the project root with no flow layout at all. So when
    `phase2/stage1/rtl` does not exist the population is the project's OWN
    top-level sources — NOT-RECURSIVE, deliberately. Recursing is what produced
    the measured false positive: it is a directory tree's SUBDIRECTORIES that
    hold the flow-written netlists and the testbench, never its root. A tree
    with neither a flow layout nor a root-level source examined nothing, and
    says so rather than answering "no inouts".
    """
    rtl = _pl.rtl_dir(base)
    found: List[Path] = []
    if rtl.is_dir():
        for glob in _dms.SOURCE_GLOBS:
            found.extend(rtl.rglob(glob))
    else:
        for glob in _dms.SOURCE_GLOBS:
            found.extend(base.glob(glob))
    return sorted({f for f in found
                   if f.is_file() and "wrapper" not in f.name.lower()})


def design_inout_evidence(base: Path) -> Tuple[Optional[bool], "_gd.Denominator"]:
    """Does the DESIGN declare an `inout` port?

    Returns ``(answer, denominator)``. ``answer`` is ``None`` when the design's
    RTL set is empty — nothing was examined, so neither "has inouts" nor "has
    none" was established, and the caller must not render the zero as either.
    """
    sources = design_rtl_sources(base)
    considered = len(sources)
    examined = 0
    witness = ""
    answer: Optional[bool] = None
    for fpath in sources:
        try:
            raw = fpath.read_text(errors="replace")
        except OSError:
            continue
        examined += 1
        # #731: strip comments AND string literals before a declaration regex
        # reads the text. `// No inout pad in L9` is a sentence, not a port.
        code = _hdl_code_text.strip_hdl_comments_and_strings(raw)
        if not witness:
            m = INOUT_RE.search(code)
            if m:
                line = code.count("\n", 0, m.start()) + 1
                rel = (str(fpath.relative_to(base))
                       if fpath.is_relative_to(base) else str(fpath))
                witness = f"{rel}:{line}"
    if examined:
        answer = bool(witness)
    denom = _gd.Denominator(
        unit=_DESIGN_RTL_UNIT,
        examined=examined,
        considered=considered,
        not_applicable_reason=(
            "" if examined else
            f"no readable design RTL source under {_pl.rtl_dir(base)} "
            f"({', '.join(_dms.SOURCE_GLOBS)}) — whether this design needs a "
            f"synthesis wrapper was NOT established, and the absence of a "
            f"wrapper is therefore not a finding"),
        details={"inout_witness": witness} if witness else {},
    )
    return answer, denom


def design_has_inout(base: Path) -> bool:
    """Back-compatible boolean form of :func:`design_inout_evidence`.

    ``None`` (nothing examined) renders as False here, because the only caller
    that still wants a bare bool is one asking "must I demand a wrapper?", and
    an unestablished need is not a demand. Callers that must tell the two
    apart use :func:`design_inout_evidence`.
    """
    answer, _denom = design_inout_evidence(base)
    return bool(answer)


def count_instantiations(text: str) -> int:
    """Count module instantiations in Verilog text, excluding keywords."""
    count = 0
    for m in INST_RE.finditer(text):
        mod_name = m.group(1)
        inst_name = m.group(2)
        if mod_name.lower() not in VERILOG_KEYWORDS and inst_name.lower() not in VERILOG_KEYWORDS:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------
def audit(project_dir: str) -> AuditResult:
    findings: List[Finding] = []
    base = Path(project_dir)

    if not base.exists() or not base.is_dir():
        findings.append(Finding(
            rule="DIR_MISSING",
            severity="ERROR",
            message=f"Project directory does not exist: {project_dir}",
        ))
        return AuditResult(
            program="synth_wrapper_check",
            passed=False,
            findings=findings,
            summary={"wrappers_checked": 0, "valid_wrappers": 0},
        )

    wrapper_files = discover_wrapper_files(base)

    if not wrapper_files:
        # Does the DESIGN need a wrapper? Answered from the design's own RTL
        # set (the population the synth step reads), comment/string-stripped.
        has_inout, denom = design_inout_evidence(base)
        if has_inout is None:
            findings.append(Finding(
                rule="INOUT_NOT_MEASURED",
                severity="INFO",
                message=("No wrapper files found, and whether one is needed was "
                         "NOT established: " + denom.line()),
            ))
            summary = {"wrappers_checked": 0, "valid_wrappers": 0,
                       "wrapper_needed": None}
            _gd.attach(summary, denom)
            return AuditResult(
                program="synth_wrapper_check",
                passed=True,
                findings=findings,
                summary=summary,
            )
        if not has_inout:
            findings.append(Finding(
                rule="NO_INOUT_DESIGN",
                severity="INFO",
                message=("No wrapper files found, but the design's own RTL "
                         "declares no inout ports — wrapper may not be needed "
                         f"({denom.line()})"),
            ))
            summary = {"wrappers_checked": 0, "valid_wrappers": 0,
                       "wrapper_needed": False}
            _gd.attach(summary, denom)
            return AuditResult(
                program="synth_wrapper_check",
                passed=True,
                findings=findings,
                summary=summary,
            )
        else:
            witness = (denom.details or {}).get("inout_witness", "")
            findings.append(Finding(
                rule="NO_WRAPPER",
                severity="ERROR",
                message=("No *wrapper*.v or *wrapper*.sv files found, but the "
                         "design's own RTL declares an inout port"
                         + (f" at {witness}" if witness else "")),
                file=witness.split(":")[0] if witness else "",
            ))
            summary = {"wrappers_checked": 0, "valid_wrappers": 0,
                       "wrapper_needed": True}
            _gd.attach(summary, denom)
            return AuditResult(
                program="synth_wrapper_check",
                passed=False,
                findings=findings,
                summary=summary,
            )

    valid_wrappers = 0

    for wf in wrapper_files:
        rel = str(wf.relative_to(base)) if wf.is_relative_to(base) else str(wf)
        file_errors = 0

        try:
            text = wf.read_text(errors="replace")
        except OSError as e:
            findings.append(Finding(
                rule="READ_ERROR",
                severity="ERROR",
                message=f"Cannot read file: {e}",
                file=rel,
            ))
            continue

        # #731: every DECLARATION regex below reads `code`, never `text` — a
        # `// module spm_wrapper` in a header banner mints a module the file
        # does not declare, and an `// inout` in a sentence reads as
        # bidirectional handling. The stub count below deliberately stays on
        # the RAW text: it is a size measurement, not a declaration scan, and
        # its own `//` filter is the behaviour already pinned by
        # `test_synth_wrapper_check`.
        code = _hdl_code_text.strip_hdl_comments_and_strings(text)

        # Check: not a stub (<5 non-comment, non-empty lines)
        code_lines = [
            l for l in text.split("\n")
            if l.strip() and not l.strip().startswith("//")
        ]
        if len(code_lines) < 5:
            findings.append(Finding(
                rule="STUB_FILE",
                severity="ERROR",
                message=f"Wrapper has only {len(code_lines)} lines of code (stub)",
                file=rel,
            ))
            file_errors += 1

        # Check: contains module declaration
        modules = MODULE_DECL_RE.findall(code)
        if not modules:
            findings.append(Finding(
                rule="NO_MODULE_DECL",
                severity="ERROR",
                message="No 'module' declaration found in wrapper",
                file=rel,
            ))
            file_errors += 1
        else:
            findings.append(Finding(
                rule="MODULE_FOUND",
                severity="INFO",
                message=f"Found {len(modules)} module declaration(s)",
                file=rel,
            ))

        # Check: contains at least 1 module instantiation (DUT)
        inst_count = count_instantiations(code)
        if inst_count == 0:
            findings.append(Finding(
                rule="NO_DUT_INST",
                severity="ERROR",
                message="No module instantiation found (no DUT being wrapped)",
                file=rel,
            ))
            file_errors += 1
        else:
            findings.append(Finding(
                rule="DUT_INST_FOUND",
                severity="INFO",
                message=f"Found {inst_count} module instantiation(s)",
                file=rel,
            ))

        # Check: inout / bidirectional handling
        has_inout_handling = bool(INOUT_RE.search(code))
        if has_inout_handling:
            findings.append(Finding(
                rule="INOUT_HANDLED",
                severity="INFO",
                message="Wrapper handles inout/bidirectional ports",
                file=rel,
            ))
        else:
            findings.append(Finding(
                rule="NO_INOUT_HANDLING",
                severity="WARNING",
                message="Wrapper does not contain any inout port declarations",
                file=rel,
            ))

        if file_errors == 0:
            valid_wrappers += 1

    passed = valid_wrappers > 0
    return AuditResult(
        program="synth_wrapper_check",
        passed=passed,
        findings=findings,
        summary={
            "wrappers_checked": len(wrapper_files),
            "valid_wrappers": valid_wrappers,
            "errors": sum(1 for f in findings if f.severity == "ERROR"),
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Deterministic compliance check for synth-wrapper-gen"
    )
    p.add_argument("project_dir", nargs="?", default=".")
    p.add_argument("--json", action="store_true",
                   help="Output JSON report to stdout")
    args = p.parse_args(argv)

    result = audit(args.project_dir)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        for f in result.findings:
            tag = f"[{f.file}] " if f.file else ""
            print(f"[{f.severity}] {f.rule}: {tag}{f.message}")
        status = "PASS" if result.passed else "FAIL"
        print(f"\n{status} — {result.summary}")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
