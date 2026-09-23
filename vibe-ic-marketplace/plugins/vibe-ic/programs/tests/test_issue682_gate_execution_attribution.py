"""#682 — a gate that never ran read exactly like one that passed.

`flow_compliance_check` reported a STEP's verdict and never named the GATE that
produced it. Only the failing branches wrote the command; a gate that ran and
passed left no trace. So `grep -c <gate> flow_compliance_check.log` returned 0
for a gate that had just written `{"verdict": "FAIL", "rc": 1}`, 0 for one that
certainly ran, and 0 for one that was never wired — three different facts, one
answer.

It cost a false alarm: a round-report concluded from that grep that
`drv_promotion_corroboration` writes a blocking FAIL the compliance gate never
reads. Verified false — the gate is wired at step 23, it ran, it wrote its
verdict, and step 23 was FAIL. The inference only looked sound because the record
could not separate "never read" from "not recorded".

MEASURED after the fix, on the caravel_user_project x sky130A cell:

    GATE EXECUTION LEDGER: 74 invocation(s)
      GATE_RAN formal_proof_evidence_check         rc=0   PASS
      GATE_RAN cpu_functional_oracle_waiver_check  rc=1   FAIL
      ...
    gates recorded as RUN: 71
      drv_promotion_corroboration_check      did NOT run in this cell
      sta_corner_record_completeness_check   did NOT run in this cell

Same shape as #544 ("the run looked clean because the only gate that would have
disagreed had not spoken"), which fixed the AGGREGATION and left the
OBSERVABILITY open.
"""
from __future__ import annotations

import re
import importlib.util
import pathlib
import sys

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

_spec = importlib.util.spec_from_file_location(
    "flow_compliance_check", _PROGRAMS / "flow_compliance_check.py")
F = importlib.util.module_from_spec(_spec)
sys.modules["flow_compliance_check"] = F
try:
    _spec.loader.exec_module(F)
except SystemExit:
    pass


def setup_function(_fn):
    F._GATE_LEDGER.clear()


# ── the name a reader greps for ───────────────────────────────────────────
def test_the_recorded_name_is_the_one_a_reader_would_grep():
    assert F._gate_name("drv_promotion_corroboration_check . --json x.json") \
        == "drv_promotion_corroboration_check"
    assert F._gate_name("python3 /a/b/sta_corner_record_completeness_check.py .") \
        == "sta_corner_record_completeness_check"


def test_an_empty_command_does_not_crash_the_ledger():
    assert F._gate_name("") == "<empty>"


# ── a passing gate is recorded, which is the whole point ──────────────────
def test_a_gate_that_passed_is_recorded():
    """The defect in one assertion. Recording only failures is how an absence
    became indistinguishable from a pass."""
    F._record_gate_execution("some_check .", 0, "PASS")
    lines = "\n".join(F.gate_ledger_lines())
    assert "GATE_RAN some_check" in lines and "PASS" in lines


def test_every_outcome_is_recorded_not_just_the_bad_ones():
    for rc, verdict in ((0, "PASS"), (1, "FAIL"), (2, "VACUOUS_PASS"),
                        (3, "PASS_WITH_WAIVERS"), (None, "CRASHED"),
                        (None, "TIMEOUT"), (None, "NOT_FOUND")):
        F._record_gate_execution(f"g_{verdict.lower()}_check .", rc, verdict)
    lines = "\n".join(F.gate_ledger_lines())
    assert lines.count("GATE_RAN") == 7
    assert "launch-failed" in lines, "a gate that could not launch must say so"


def test_the_block_is_emitted_even_when_nothing_ran():
    """A record that appears only when there is something to report cannot be
    used to prove there was nothing."""
    lines = F.gate_ledger_lines()
    assert lines and "no program gate was invoked" in lines[0]


# ── the wiring, which is what makes it a record at all ────────────────────
def test_a_PASSING_gate_is_recorded_END_TO_END(tmp_path, monkeypatch):
    """MUTATION-DRIVEN, and the assertion this file was missing. Restricting the
    record to `if not ok` left all eight tests green while restoring the exact
    defect: a gate that ran and passed leaves no trace, so grep cannot tell it
    from one that never ran. Every other test here either builds the ledger by
    hand or reads the source; only this one drives the real evaluator and then
    asks whether the PASS is in the record."""
    called = {}

    def fake_inner(project, cmd_str):
        called["cmd"] = cmd_str
        return True, "all good"            # a PASS, the case that was invisible

    monkeypatch.setattr(F, "_%s__check_program_exit_zero" % "", fake_inner,
                        raising=False)
    monkeypatch.setattr(F, "__check_program_exit_zero", fake_inner,
                        raising=False)
    ok, _out = F._check_program_exit_zero(tmp_path, "quiet_passing_check .")
    assert ok is True
    lines = "\n".join(F.gate_ledger_lines())
    assert "GATE_RAN quiet_passing_check" in lines, (
        "a gate that RAN and PASSED left no trace — the whole defect")
    assert "PASS" in lines


# ──────────────────────────────────────────────────────────────────────
# READING THE WRAPPER'S OWN SOURCE
# ──────────────────────────────────────────────────────────────────────
# THE SLICE WAS THE DEFECT, and it cost a red on main that named the wrong thing.
#
# Three tests below used to extract "the wrapper" as the TEXT BETWEEN two `def`
# names -- `src[index("def _check_program_exit_zero") :
# index("def __check_program_exit_zero")]`. Nothing keeps those two definitions
# adjacent. #2501's receipt redirect landed `_resolved_key` and
# `_receipt_off_a_produced_document` in the gap, and their PROSE says "the readers
# run AFTER the subprocess" and "subprocess call" -- so
# `assert "subprocess" not in wrapper` went red on a property that is TRUE.
#
# MEASURED on main 1f537b5c0: the wrapper's own source is 213 lines, carries all
# three hint sentinels, and contains no `subprocess` at all; the slice dragged in
# 102 further lines defining two unrelated helpers.
#
# So the extraction is now by AST -- the function's OWN segment, whatever sits
# after it -- and the search runs over CODE with comments and string literals
# blanked. That is strictly stronger than the slice in both directions: a
# subprocess call added to the wrapper is still caught, and prose about one
# anywhere in the module is not.
def _wrapper_source() -> str:
    """The source of `_check_program_exit_zero` itself, by AST."""
    import ast
    src = (_PROGRAMS / "flow_compliance_check.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef)
               and n.name == "_check_program_exit_zero"), None)
    assert fn is not None, (
        "flow_compliance_check no longer defines a module-level "
        "`_check_program_exit_zero`; this whole file is about that wrapper")
    seg = ast.get_source_segment(src, fn)
    assert seg, "ast could not return the wrapper's source segment"
    return seg


def _code_only(text: str) -> str:
    """`text` with comments and string literals blanked, so a token search reads
    CODE. Length and line structure are preserved so an index into the result
    still points at the right line."""
    import io
    import tokenize
    out = list(text)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                (r1, c1), (r2, c2) = tok.start, tok.end
                lines = text.splitlines(keepends=True)
                base = sum(len(l) for l in lines[:r1 - 1])
                start = base + c1
                end = (sum(len(l) for l in lines[:r2 - 1]) + c2)
                for k in range(start, min(end, len(out))):
                    if out[k] != "\n":
                        out[k] = " "
    except (tokenize.TokenError, IndentationError):
        # A fragment that does not tokenize on its own is returned unchanged:
        # conservative, since the only cost is a search that also reads prose.
        return text
    return "".join(out)


def test_the_record_is_not_conditional_on_the_outcome():
    """The same property, read off the source: `_record_gate_execution` must sit
    at the wrapper's body indent, not under any branch."""
    wrapper = _wrapper_source()
    # R-0915-119 (icspm5 change 1) passes the gate report's enumeration as
    # `evidence=`, so the call spans lines; the call is found by its opening,
    # and it must still sit at the wrapper's body indent.
    lines = wrapper.splitlines()
    i = next(n for n, l in enumerate(lines)
             if "_record_gate_execution(" in l
             and not l.lstrip().startswith(("#", "def ")))
    assert lines[i].startswith("    _ledger_row = _record_gate_execution("), (
        f"the record is nested under a branch: {lines[i]!r}")
    assert lines[i + 1].strip().startswith("cmd_str, rc, verdict, reason_class"), (
        lines[i:i + 3])


def test_the_evaluator_records_every_return_by_WRAPPING():
    """LOAD-BEARING. Inserting a call at each of the eleven return points leaves
    a return added later unrecorded — and an unrecorded gate is exactly the
    defect. The wrapper cannot be bypassed by a new return."""
    wrapper = _code_only(_wrapper_source())
    assert "__check_program_exit_zero(project, cmd_str)" in wrapper
    # the four facts it records, whatever else (R-0915-119's `evidence=`) rides
    # along with them
    assert re.search(r"_record_gate_execution\(\s*cmd_str, rc, verdict, "
                     r"reason_class\b", wrapper), wrapper[-600:]


def test_the_verdict_is_read_from_the_snippet_not_re_derived():
    """One classification, not a second that can disagree with the first. A
    parallel derivation here would be a new way for the record to be wrong about
    the run it describes."""
    wrapper = _code_only(_wrapper_source())
    for sentinel in ("_VACUOUS_HINT_PREFIX", "_WAIVER_HINT_PREFIX",
                     "_CRASH_HINT_PREFIX"):
        assert sentinel in wrapper, sentinel
    assert "subprocess" not in wrapper, (
        "the wrapper must not run anything itself — and this now reads the "
        "wrapper's OWN code, so a `subprocess` in a comment or a docstring "
        "anywhere in the module cannot make this red, while a real call added to "
        "the wrapper still does")


def test_main_prints_the_ledger_unconditionally():
    """A block printed only on failure leaves the passing case exactly as
    unreadable as it was."""
    src = (_PROGRAMS / "flow_compliance_check.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = body.index("for _line in gate_ledger_lines():")
    # it must sit at function-body indent inside main, not under an `if`
    line = body[body.rfind("\n", 0, i) + 1:i + 40]
    assert line.startswith("    for _line"), line


# ── the repaired instrument, driven in both directions ─────────────────────

def test_the_extraction_reads_the_wrapper_and_not_its_neighbours():
    """THE DEFECT THIS REPAIRED, asserted directly. The old slice ran to the next
    `def` NAME, so anything landed in the gap became "the wrapper"."""
    import ast
    src = (_PROGRAMS / "flow_compliance_check.py").read_text(encoding="utf-8")
    own = _wrapper_source()
    i = src.index("def _check_program_exit_zero(project: Path, cmd_str: str)")
    j = src.index("def __check_program_exit_zero(", i)
    slice_ = src[i:j]
    assert own in slice_ and len(own) < len(slice_), (
        "the slice no longer contains extra lines; if the two definitions are "
        "adjacent again this test is the record of why it must not be relied on")
    swallowed = ast.parse(slice_[len(own):].strip())
    names = [n.name for n in swallowed.body if hasattr(n, "name")]
    assert names, "nothing in the gap — see the assertion above"
    # and the swallowed prose is what made a TRUE property read red
    assert "subprocess" in slice_ and "subprocess" not in own


def test_a_real_subprocess_call_in_the_wrapper_is_still_caught():
    """MUTATION. The repair must not be a relaxation: a call added to the
    wrapper's CODE still reddens the claim."""
    import pytest
    own = _wrapper_source()
    # ANCHORED ON THE FIRST RETURN, whatever it returns: the wrapper's return type
    # has been renamed once already (`_ProgramCheckOutcome` -> `_ProgramCheckResult`)
    # and an arm anchored on the name would silently stop mutating.
    ret = next(l for l in own.splitlines() if l.startswith("    return "))
    mutated = _code_only(own.replace(
        ret, "    subprocess.run(['true'])\n" + ret, 1))
    assert "subprocess" in mutated, (
        "the mutation did not land — this arm would be measuring nothing")
    with pytest.raises(AssertionError):
        assert "subprocess" not in mutated, "the wrapper must not run anything"


def test_prose_about_a_subprocess_cannot_redden_the_claim():
    """The other direction, and it is the one that went red on main: a comment or
    a docstring mentioning a subprocess is not a subprocess."""
    wrapper = _wrapper_source()
    with_prose = wrapper.replace(
        '    """', '    """A docstring that says subprocess, once.\n\n    ', 1)
    assert "subprocess" in with_prose
    assert "subprocess" not in _code_only(with_prose)


def test_code_only_blanks_strings_and_keeps_the_line_shape():
    """`_code_only` is load-bearing for both arms above, so it is driven directly:
    a token inside a literal is gone, a token in code survives, and the line
    count does not move (an index into the result still points at the right
    line)."""
    src = ('x = "subprocess in a literal"  # and in a comment\n'
           'import subprocess\n')
    out = _code_only(src)
    assert out.count("\n") == src.count("\n")
    assert out.splitlines()[0].count("subprocess") == 0, out.splitlines()[0]
    assert "subprocess" in out.splitlines()[1]
