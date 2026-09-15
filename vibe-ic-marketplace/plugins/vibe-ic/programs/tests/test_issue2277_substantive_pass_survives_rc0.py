"""A gate that proved the equivalent by another route must not be re-tiered.

vibe-ic#2277, flow step 14. MEASURED on live main 79506306d (lane icspm4, run1).
The run's synthesis used an inline `yosys -p` command rather than a `.ys`
script, so `yosys_hilomap_required_check` and `yosys_script_template_check`
both exit 0, keep the verdict word `VACUOUS_PASS` on purpose (no `.ys` file
existed to audit) and record in their own gate-private `reason_class`
(`inline_yosys_p_mode_conformant`) that the inline command WAS extracted and
verified. The hilomap gate prints `SUBSTANTIVE_PASS:` for exactly this reader.

`_check_program_exit_zero`'s rc-0 branch threw that away: it saw a JSON
non-verdict, asked the taxonomy to classify a word outside its six classes, got
None, fell through to prose inference, matched no recogniser, fail-closed to
EXECUTION_ERROR and REWROTE the snippet as `INCOMPLETE: ...` -- which the
optional-clause reader turns into `__INCOMPLETE_HINT__` and the step into
INCOMPLETE. The rc-2 branch has handled precisely this since it was written
("That is a substantive PASS, not a non-verdict reason to classify"); this is
the same rule on the other exit code, plus the sibling gate printing the same
token the hilomap gate already prints.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F                        # noqa: E402

_SRC_TEMPLATE = (PROGRAMS / "yosys_script_template_check.py").read_text()
_SRC_HILOMAP = (PROGRAMS / "yosys_hilomap_required_check.py").read_text()

_REPORT = {
    "verdict": "VACUOUS_PASS",
    "reason_class": "inline_yosys_p_mode_conformant",
    "reason": ("no .ys scripts found; the inline `yosys -p` command echoed by "
               "the runner's synth log was extracted and verified conformant "
               "across 1 log(s)."),
}


def _project(tmp_path, report=None):
    p = tmp_path / "run"
    (p / "reports" / "phase2" / "gates").mkdir(parents=True)
    (p / "reports" / "phase2" / "gates" / "g.json").write_text(
        json.dumps(report if report is not None else _REPORT))
    return p


def _fake_gate(tmp_path, *, substantive: bool):
    """A stand-in gate: rc 0, a JSON non-verdict report, and the token or not."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    prog = bin_dir / "fake_inline_check.py"
    say = ('print("SUBSTANTIVE_PASS: the equivalent was verified by '
           'another route")\n' if substantive else "")
    prog.write_text(
        "import sys\n"
        'print("VACUOUS_PASS: no .ys scripts found")\n'
        f"{say}"
        "sys.exit(0)\n")
    return prog


def _drive(tmp_path, *, substantive: bool):
    proj = _project(tmp_path)
    prog = _fake_gate(tmp_path, substantive=substantive)
    cmd = f"{prog} . --json reports/phase2/gates/g.json"
    return F._check_program_exit_zero(proj, cmd)


# --------------------------------------------------------------------------- #
# the fix
# --------------------------------------------------------------------------- #
def test_the_token_keeps_the_rc0_gate_out_of_the_INCOMPLETE_tier(tmp_path):
    passed, out = _drive(tmp_path, substantive=True)
    assert passed is True
    assert not out.startswith("INCOMPLETE:")
    assert "EXECUTION_ERROR" not in out
    assert F._stdout_signals_token(out, F._SUBSTANTIVE_STDOUT_TOKEN), (
        "the snippet must still carry the token the step reader looks for")


def test_the_template_gate_prints_the_token_its_sibling_prints(tmp_path):
    """Both gates on step 14 reach the same tier by the same route, and one of
    them was silent about it."""
    assert "SUBSTANTIVE_PASS: no `.ys` script existed" in _SRC_HILOMAP
    assert "SUBSTANTIVE_PASS: no `.ys` script existed" in _SRC_TEMPLATE


# --------------------------------------------------------------------------- #
# negative controls: nothing else is exempted
# --------------------------------------------------------------------------- #
def test_a_gate_that_does_NOT_print_the_token_is_classified_as_before(tmp_path):
    """One property changed. A gate-private reason class the taxonomy does not
    know, with no substantive claim, still fail-closes -- the whole point of
    the fail-closed default."""
    passed, out = _drive(tmp_path, substantive=False)
    assert out.startswith("INCOMPLETE:")
    assert "EXECUTION_ERROR" in out


def test_a_FAILING_gate_can_never_reach_the_exemption(tmp_path):
    """`verdict` is PASS only when the process said so, so a red gate that
    printed the token anyway is still red."""
    proj = _project(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    prog = bin_dir / "red.py"
    prog.write_text('import sys\nprint("SUBSTANTIVE_PASS: nope")\nsys.exit(1)\n')
    passed, out = F._check_program_exit_zero(
        proj, f"{prog} . --json reports/phase2/gates/g.json")
    assert passed is False


def test_a_real_taxonomy_class_still_reaches_its_own_tier(tmp_path):
    """A report that states a class the taxonomy DOES know is untouched: it
    must still be tiered by that class, not swept up by this exemption."""
    proj = _project(tmp_path, report={"verdict": "VACUOUS_PASS",
                                      "reason_class": "BLOCKED_BY_UPSTREAM",
                                      "reason": "its producer never ran"})
    prog = _fake_gate(tmp_path, substantive=False)
    _, out = F._check_program_exit_zero(
        proj, f"{prog} . --json reports/phase2/gates/g.json")
    assert "BLOCKED_BY_UPSTREAM" in out
