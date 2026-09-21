"""Tests for v0.1.58 R8 capture: design_one_shot_runner must regenerate
`reports/final_summary.md` BEFORE invoking step_final_audit so the audit's
agent_report_sha256_attestation_check sees fresh attestations for every
artefact this phase2 run just emitted.

Captured from v0.1.57 CVDP run: the runner's final_audit reported
"agent_report_sha256_attestation_check — FAIL: 1 attestation gap(s)"
on a --skip-phase3 --skip-analog --skip-hardware path even though the
gate PASSes when invoked directly with the same project state. Root
cause: emit_final_summary ran AFTER step_final_audit at line 3564 instead
of before, so the audit read a stale attestation table.

The FPGA path at line 3545 already followed the correct pattern
(emit_final_summary → step_fpga_burn); the --skip-hardware path was
missing the parallel.
"""
import ast
import re
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
RUNNER = PROGRAMS / "design_one_shot_runner.py"


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def _ordering_sites(tree: ast.AST):
    """(function, audit_linenos, emit_linenos) for every function that appends
    a `step_final_audit(project, phase=2, ...)`.

    THE SEARCH IS BOUNDED BY THE FUNCTION, NOT BY A CHARACTER COUNT. The
    previous version of this test asked whether an `emit_final_summary` call
    appeared within the prior 3000 characters, and its sibling asked about the
    prior 10 lines. Both are constants standing in for a structural
    relationship, and both went stale as `design_one_shot_runner.py` grew:
    MEASURED at 8ef2d9a41, the phase-2 audit append is at line 23281 and FOUR
    `emit_final_summary(project)` calls precede it in the same function, the
    nearest at line 23230 — 3181 characters and 51 lines away. So the ordering
    this module exists to enforce WAS satisfied and both tests reported that it
    was not.

    Widening the constant would buy green today and go stale on the next
    landing. This asks the question the docstring actually argues for: within
    ONE function body, does the summary get regenerated before the audit reads
    it? Same repair shape as 4bf31a60a, "the cron section is bounded by itself,
    not by 600 characters".
    """
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        audits, emits = [], []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name == "step_final_audit":
                phases = [k.value for k in node.keywords if k.arg == "phase"]
                if any(isinstance(p, ast.Constant) and p.value == 2
                       for p in phases):
                    audits.append(node.lineno)
            elif name == "emit_final_summary":
                emits.append(node.lineno)
        if audits:
            yield fn.name, sorted(audits), sorted(emits)


def test_emit_final_summary_precedes_step_final_audit():
    """Within the SAME function, a summary regeneration must precede the
    phase-2 audit append."""
    tree = ast.parse(RUNNER.read_text(errors="replace"))
    sites = list(_ordering_sites(tree))
    assert sites, "step_final_audit(phase=2) call not found"
    for fn_name, audits, emits in sites:
        first_audit = audits[0]
        before = [e for e in emits if e < first_audit]
        assert before, (
            f"in {fn_name}(): no emit_final_summary call precedes "
            f"step_final_audit(phase=2) at line {first_audit} — the audit "
            f"will read a stale attestation table and FAIL with a phantom "
            f"gap. emit_final_summary calls in this function: {emits}")


def test_no_other_step_is_appended_between_the_summary_and_the_audit():
    """The ordering intent stated as a STRUCTURAL fact instead of a 10-line
    window: nothing that appends another step may come between the LAST
    summary regeneration and the phase-2 audit.

    This is what "immediately before" was reaching for. The old spelling asked
    whether the literal string `emit_final_summary` appeared in the prior TEN
    LINES, which is a constant, and which said no at 8ef2d9a41 while the
    nearest call sat 51 lines above — satisfied, and reported unsatisfied. The
    property that actually protects the attestation table is that no FURTHER
    step is planned in between, because a step planned in between is what
    would emit a new artefact after the summary was written and leave the
    audit reading a stale table.
    """
    tree = ast.parse(RUNNER.read_text(errors="replace"))
    sites = list(_ordering_sites(tree))
    assert sites, "step_final_audit(phase=2) call not found"
    for fn_name, audits, emits in sites:
        first_audit = audits[0]
        before = [e for e in emits if e < first_audit]
        assert before, f"in {fn_name}(): no preceding emit_final_summary"
        last_emit = before[-1]
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == fn_name)
        intervening = sorted(
            {node.lineno: _call_name(node) for node in ast.walk(fn)
             if isinstance(node, ast.Call)
             and _call_name(node).startswith("step_")
             and last_emit < node.lineno < first_audit}.items())
        assert not intervening, (
            f"in {fn_name}(): step(s) are planned between the last "
            f"emit_final_summary (line {last_emit}) and "
            f"step_final_audit(phase=2) (line {first_audit}); anything that "
            f"emits an artefact there leaves the audit reading a stale "
            f"attestation table: {intervening}")


def test_fpga_burn_pattern_still_present():
    """The pre-fpga_burn emit_final_summary pattern (line ~3545, captured at
    v1.6.x) must NOT have been removed — that one is also load-bearing."""
    src = RUNNER.read_text()
    # The FPGA burn path uses emit_final_summary before step_fpga_burn
    # No need to over-specify line number; just check both appear in order.
    burn_pattern = re.compile(r"plan\.append\(\s*step_fpga_burn\(")
    m_burn = burn_pattern.search(src)
    assert m_burn is not None
    # An emit_final_summary call within 200 chars before the burn append
    head = src[max(0, m_burn.start() - 500):m_burn.start()]
    assert "_pl.emit_final_summary" in head, (
        "FPGA-burn path lost its pre-burn emit_final_summary — that pattern "
        "is also load-bearing for the SOF attestation gate.")
