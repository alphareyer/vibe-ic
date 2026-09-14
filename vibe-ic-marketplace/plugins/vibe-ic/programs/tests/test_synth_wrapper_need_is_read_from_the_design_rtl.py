"""`synth_wrapper_check` asks the DESIGN whether it needs a wrapper (icspm2).

MEASURED FAILURE THIS PINS
==========================
A completed gf180mcuD run of `spm` (2026-09-15, lane icspm2) reached the
completion audit with

    [ERROR] NO_WRAPPER: No *wrapper*.v or *wrapper*.sv files found, but design
                        has inout ports

and the design declares NO inout port anywhere. `phase2/stage1/rtl/spm.v` has
five ports — `input wire clk`, `input wire rst`, `input wire [size-1:0] x`,
`input wire y`, `output wire p`. The three matches that produced the verdict,
each measured on that run tree:

  * `phase2/stage1/sim_full_stack/tb_spm_full.v:51`
        `// No inout pad in L9; drive_byte is a no-op for sync compatibility.`
    a COMMENT whose sentence says there is no inout — the #731 shape.
  * `phase3/stage3/pnr/spm_pnr.v:13-14`, and
  * `phase3/stage3/extracted/spm_pwraware_welltied.v:13-14`
        `inout VDD;` / `inout VSS;`
    the supply pins THE FLOW writes into every power-aware netlist it emits.

None of the three is a port of the design. Two of them are artefacts the flow
manufactured itself, so the gate refused a design on evidence its own back end
had written one step earlier, and it would do so on EVERY routed design.

THE TWO DIRECTIONS
==================
Every test here that asserts the false positive is gone has a sibling asserting
the gate still bites: a design whose OWN RTL declares an inout and that ships no
wrapper is still `NO_WRAPPER`, rc 1.
"""
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / 'synth_wrapper_check.py'
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
sys.path.insert(0, str(SCRIPT.parent))
import synth_wrapper_check as swc  # noqa: E402


# ---------------------------------------------------------------------------
# THE CONTROL ARM MUST *RUN*, NOT RAISE.
#
# A test that reaches `swc.design_inout_evidence` on the pre-fix blob dies with
# AttributeError, and an AttributeError observes nothing: it proves the symbol
# is new, not that the old code answered wrongly. These two shims route through
# `getattr`, so against the pre-fix tree the OLD `design_has_inout` runs and
# gives its own (wrong) answer, and the assertion below is the thing that fails.
# ---------------------------------------------------------------------------
def _inout_evidence(proj):
    fn = getattr(swc, "design_inout_evidence", None)
    if fn is not None:
        return fn(proj)
    import _gate_denominator as _gd
    answer = swc.design_has_inout(Path(proj))
    # The pre-fix gate discloses no denominator at all; say so in the shape the
    # assertions read, with the count it would have had to publish.
    n = len(_sources(proj))
    return answer, _gd.Denominator(
        unit="design RTL source file under phase2/stage1/rtl (pre-fix: the "
             "gate published no denominator)",
        examined=n,
        considered=n,
        not_applicable_reason=("" if n else
                               "pre-fix: the gate disclosed no denominator"))


def _sources(proj):
    fn = getattr(swc, "design_rtl_sources", None)
    if fn is not None:
        return fn(Path(proj))
    rtl = Path(proj) / "phase2/stage1/rtl"
    if not rtl.is_dir():
        return []
    return sorted(set(list(rtl.rglob("*.v")) + list(rtl.rglob("*.sv"))))

# The real design, byte-for-byte in the shape the run staged it.
SPM_RTL = """\
`default_nettype none
//============================================================================
// spm — serial-parallel (carry-save) integer multiplier
//============================================================================
module spm #(
    parameter size = 32
) (
    input  wire            clk,
    input  wire            rst,
    input  wire [size-1:0] x,
    input  wire            y,
    output wire            p
);
    assign p = 1'b0;
endmodule
"""

# The testbench line that produced the false positive, verbatim.
TB_WITH_INOUT_IN_A_COMMENT = """\
module tb_spm_full;
    reg clk = 0;
    task drive_byte(input [7:0] b);
      // No inout pad in L9; drive_byte is a no-op for sync compatibility.
    endtask
endmodule
"""

# The supply pins the flow writes into a power-aware netlist, verbatim.
POWER_AWARE_NETLIST = """\
module spm (clk, rst, x, y, p, VDD, VSS);
  inout VDD;
  inout VSS;
  input clk;
  output p;
endmodule
"""

REAL_INOUT_RTL = """\
module i2c_top (
    input  wire clk,
    inout  wire sda,
    output wire scl
);
endmodule
"""


def _project(tmp_path, rtl=None, tb=None, netlist=None, extracted=None):
    """A project laid out exactly the way the runner lays one out."""
    p = tmp_path / "proj"
    (p / "phase2/stage1/rtl").mkdir(parents=True)
    if rtl is not None:
        (p / "phase2/stage1/rtl/spm.v").write_text(rtl)
    if tb is not None:
        (p / "phase2/stage1/sim_full_stack").mkdir(parents=True)
        (p / "phase2/stage1/sim_full_stack/tb_spm_full.v").write_text(tb)
    if netlist is not None:
        (p / "phase3/stage3/pnr").mkdir(parents=True)
        (p / "phase3/stage3/pnr/spm_pnr.v").write_text(netlist)
    if extracted is not None:
        (p / "phase3/stage3/extracted").mkdir(parents=True)
        (p / "phase3/stage3/extracted/spm_pwraware_welltied.v").write_text(
            extracted)
    return p


# ---------------------------------------------------------------------------
# DIRECTION 1 — the false positive is gone
# ---------------------------------------------------------------------------
def test_the_measured_spm_tree_no_longer_reports_no_wrapper(tmp_path):
    """The whole measured tree at once: RTL + TB comment + two netlists."""
    proj = _project(tmp_path, rtl=SPM_RTL, tb=TB_WITH_INOUT_IN_A_COMMENT,
                    netlist=POWER_AWARE_NETLIST, extracted=POWER_AWARE_NETLIST)
    res = swc.audit(str(proj))
    rules = {f.rule for f in res.findings}
    assert "NO_WRAPPER" not in rules, (
        f"the measured false positive is back: {[f.message for f in res.findings]}")
    assert res.passed is True
    assert res.summary["wrapper_needed"] is False


def test_a_comment_saying_there_is_no_inout_does_not_mint_one(tmp_path):
    """#731, isolated: the TB comment alone."""
    proj = _project(tmp_path, rtl=SPM_RTL, tb=TB_WITH_INOUT_IN_A_COMMENT)
    answer, denom = _inout_evidence(proj)
    assert answer is False
    assert denom.examined == 1, denom.as_dict()


def test_a_comment_inside_the_design_rtl_itself_does_not_mint_one(tmp_path):
    """The stripper is applied to the design's OWN file, not only to siblings."""
    rtl = SPM_RTL.replace(
        "    assign p = 1'b0;",
        "    // this block has no inout port; see L9\n    assign p = 1'b0;")
    proj = _project(tmp_path, rtl=rtl)
    answer, _ = _inout_evidence(proj)
    assert answer is False


def test_a_string_literal_carrying_the_word_does_not_mint_one(tmp_path):
    """`$display("inout ...")` is code the scanner would otherwise read."""
    rtl = SPM_RTL.replace(
        "    assign p = 1'b0;",
        '    initial $display("inout sda is not present");\n'
        "    assign p = 1'b0;")
    proj = _project(tmp_path, rtl=rtl)
    answer, _ = _inout_evidence(proj)
    assert answer is False


def test_flow_written_supply_pins_are_outside_the_population(tmp_path):
    """`inout VDD/VSS` in a power-aware netlist is the flow's own artefact."""
    proj = _project(tmp_path, rtl=SPM_RTL, netlist=POWER_AWARE_NETLIST,
                    extracted=POWER_AWARE_NETLIST)
    answer, denom = _inout_evidence(proj)
    assert answer is False
    assert denom.examined == 1, (
        "the netlists under phase3/ must not be in the denominator: "
        f"{denom.as_dict()}")


def test_the_population_is_the_one_the_synth_step_reads(tmp_path):
    """Bound to `_path_layout.rtl_dir`, not to a second spelling of the path."""
    import _path_layout as _pl
    proj = _project(tmp_path, rtl=SPM_RTL, tb=TB_WITH_INOUT_IN_A_COMMENT)
    srcs = _sources(proj)
    assert [s.parent for s in srcs] == [_pl.rtl_dir(proj)]
    assert [s.name for s in srcs] == ["spm.v"]


# ---------------------------------------------------------------------------
# DIRECTION 2 — the gate still bites
# ---------------------------------------------------------------------------
def test_a_real_design_inout_still_demands_a_wrapper(tmp_path):
    """A design that TRULY declares an inout and ships no wrapper: rc 1."""
    proj = _project(tmp_path, rtl=REAL_INOUT_RTL)
    res = swc.audit(str(proj))
    rules = {f.rule for f in res.findings}
    assert "NO_WRAPPER" in rules, [f.message for f in res.findings]
    assert res.passed is False
    assert res.summary["wrapper_needed"] is True


def test_the_no_wrapper_finding_names_the_file_and_line_it_read(tmp_path):
    """A refusal a reader cannot re-derive is the one that gets worked around."""
    proj = _project(tmp_path, rtl=REAL_INOUT_RTL)
    res = swc.audit(str(proj))
    f = [x for x in res.findings if x.rule == "NO_WRAPPER"][0]
    assert "phase2/stage1/rtl/spm.v:3" in f.message, f.message
    assert f.file == "phase2/stage1/rtl/spm.v", f.file


def test_a_real_inout_beside_a_comment_and_supply_pins_still_bites(tmp_path):
    """The narrowing must not swallow a genuine port sitting next to noise."""
    rtl = REAL_INOUT_RTL.replace(
        "    input  wire clk,",
        "    // no inout here, honest\n    input  wire clk,")
    proj = _project(tmp_path, rtl=rtl, tb=TB_WITH_INOUT_IN_A_COMMENT,
                    netlist=POWER_AWARE_NETLIST)
    res = swc.audit(str(proj))
    assert res.passed is False
    assert {f.rule for f in res.findings} & {"NO_WRAPPER"}


def test_an_inout_in_a_second_design_file_is_still_found(tmp_path):
    """The whole RTL dir is the population, not just its first file."""
    proj = _project(tmp_path, rtl=SPM_RTL)
    (proj / "phase2/stage1/rtl/pads.sv").write_text(REAL_INOUT_RTL)
    answer, denom = _inout_evidence(proj)
    assert answer is True
    assert denom.examined == 2, denom.as_dict()


def test_cli_exit_code_is_1_for_a_real_inout_design(tmp_path):
    proj = _project(tmp_path, rtl=REAL_INOUT_RTL)
    with pytest.raises(SystemExit) as exc:
        swc.main([str(proj)])
    assert exc.value.code == 1


def test_cli_exit_code_is_0_for_the_measured_spm_tree(tmp_path):
    proj = _project(tmp_path, rtl=SPM_RTL, tb=TB_WITH_INOUT_IN_A_COMMENT,
                    netlist=POWER_AWARE_NETLIST, extracted=POWER_AWARE_NETLIST)
    with pytest.raises(SystemExit) as exc:
        swc.main([str(proj)])
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# DIRECTION 3 — an empty population is NOT_MEASURED, and says so
# ---------------------------------------------------------------------------
def test_an_absent_rtl_dir_is_not_measured_not_a_clean_bill(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    answer, denom = _inout_evidence(proj)
    assert answer is None, "a zero population must not answer False"
    assert denom.examined == 0
    assert denom.not_applicable_reason, "a zero denominator must say why (#496)"


def test_an_empty_rtl_dir_discloses_the_zero_in_the_report(tmp_path):
    proj = _project(tmp_path, rtl=None)
    res = swc.audit(str(proj))
    assert res.summary["wrapper_needed"] is None
    assert {f.rule for f in res.findings} == {"INOUT_NOT_MEASURED"}
    import _gate_denominator as _gd
    assert res.summary[_gd.DENOMINATOR_KEY]["examined"] == 0
    assert res.summary[_gd.DENOMINATOR_KEY]["not_applicable_reason"]


def test_the_denominator_names_its_unit(tmp_path):
    proj = _project(tmp_path, rtl=SPM_RTL)
    _answer, denom = _inout_evidence(proj)
    assert "phase2/stage1/rtl" in denom.unit


# ---------------------------------------------------------------------------
# The wrapper files' own declaration scans are stripped too (#731)
# ---------------------------------------------------------------------------
def test_a_commented_out_module_header_does_not_count_as_a_declaration(tmp_path):
    proj = _project(tmp_path, rtl=REAL_INOUT_RTL)
    (proj / "phase2/stage1/rtl/i2c_wrapper.v").write_text(
        "// module i2c_wrapper (inout sda);\n"
        "// my_design u_dut (.sda(sda));\n"
        "wire a;\nwire b;\nwire c;\nwire d;\nwire e;\n")
    res = swc.audit(str(proj))
    rules = {f.rule for f in res.findings}
    assert "NO_MODULE_DECL" in rules, [f.message for f in res.findings]
    assert "NO_DUT_INST" in rules, [f.message for f in res.findings]
    assert res.passed is False


def test_a_real_wrapper_beside_a_comment_still_passes(tmp_path):
    """The negative control for the test above."""
    proj = _project(tmp_path, rtl=REAL_INOUT_RTL)
    (proj / "phase2/stage1/rtl/i2c_wrapper.v").write_text(
        "// this wrapper wraps the module i2c_top\n"
        "module i2c_wrapper (inout wire sda);\n"
        "    wire sda_oe, sda_out, sda_in;\n"
        "    assign sda = sda_oe ? sda_out : 1'bz;\n"
        "    i2c_top u_dut (.sda(sda_in));\n"
        "endmodule\n")
    res = swc.audit(str(proj))
    rules = {f.rule for f in res.findings}
    assert "NO_MODULE_DECL" not in rules
    assert "NO_DUT_INST" not in rules
    assert res.passed is True


# ---------------------------------------------------------------------------
# STANDALONE USE — a plain directory of sources, no flow layout
# ---------------------------------------------------------------------------
def test_standalone_root_sources_are_the_population_when_there_is_no_layout(
        tmp_path):
    """`synth_wrapper_check.py ./my_project` on a bare directory still works.

    This is the shape `test_no_wrapper_with_inout_fail` pins, and narrowing the
    population must not take it away.
    """
    (tmp_path / "i2c_master.v").write_text(REAL_INOUT_RTL)
    res = swc.audit(str(tmp_path))
    assert res.passed is False
    assert "NO_WRAPPER" in {f.rule for f in res.findings}


def test_the_standalone_fallback_does_not_recurse(tmp_path):
    """The subdirectories are where the flow's own netlists live.

    A root-level design with no inout must stay clean even when a nested
    directory holds a power-aware netlist — that nesting IS the measured bug.
    """
    (tmp_path / "simple.v").write_text(SPM_RTL)
    (tmp_path / "gl").mkdir()
    (tmp_path / "gl" / "spm_pnr.v").write_text(POWER_AWARE_NETLIST)
    answer, denom = _inout_evidence(tmp_path)
    assert answer is False
    assert denom.examined == 1, denom.as_dict()


def test_a_tree_with_no_source_at_all_examines_nothing(tmp_path):
    (tmp_path / "notes.md").write_text("no rtl here\n")
    answer, denom = _inout_evidence(tmp_path)
    assert answer is None
    assert denom.examined == 0
