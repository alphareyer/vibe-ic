"""A register pair the ladder proves early must still hold for the rungs after.

MEASURED (LEC_CONVERGE, 2026-09-28, subservient = serv 1.4.0 on gf180mcuD,
yosys 4d572059c): step 13 stopped at 251/256. `equiv_induct -seq 4` proved the
register pairs, and `-seq 16`, run on that checkpoint, could no longer prove
`core.rf_mem_if.o_sram_wdata[0..4]` (`-seq 32`, `-undef` and
`equiv_simple -seq 12` on the same checkpoint stayed at 5 too). The same
recipe with the Liberty cells' flattened pin names hidden proved 256/256
through the unchanged ladder.

WHY. `equiv_make` rewires a gate consumer to the `$equiv` output only when the
consumer's canonical bit is the paired wire's own bit. It registers an internal
pair under the raw alias (equiv_make.cc `rd_signal_map.add(rdmap_gate, …)`),
while an output-port pair goes through `assign_map`. Flattening a Liberty cell
names its pins publicly, and `<inst>.IQ` wins canonical over the netlist's net
name. So a gate register is tied to its gold twin only by the `$equiv`
HYPOTHESIS, and a rung that proves the pair rewires its B to its A. From then
on the gate register is free, and a later point that needs it cannot be
proved at any depth.

THE FIXTURE is the smallest shape of that, synthesised at test time by the
real tool: a toggle register `r` that is internal (not a port, so its pair is
not rewired), an output that shows `r` only while `e` is high (so the output's
own hypothesis cannot re-derive `r`), and the constant-D, X-initialised shift
chains that `opt_dff` deletes. `z` needs 3 cycles, so `-seq 4` proves it
together with `r` and the ladder earns its next rung (#2232 stops a ladder
whose rung proved nothing). `y` needs 8, so only `-seq 16` can prove it, and
only while `r`'s pair still holds. MEASURED on origin/main 402288df7 in the
pinned image: INCONCLUSIVE, `-seq 4` 0 -> 2 proven, `-seq 16` 2 -> 2, `y`
unproven. With the pin names hidden: PASS 3/3.

THESE TESTS NEVER SKIP. They resolve yosys through `test_issue2050`'s own
resolver: this filesystem first, then the pinned container. With neither
reachable they FAIL, naming NOT_MEASURED.
"""
import ast
import json
import re
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import lec_run  # noqa: E402
# ONE resolver, the one #2050 wrote: no second way to find or run yosys.
from test_issue2050_lec_fsm_recode_breaks_the_miter import (  # noqa: E402
    _resolve_yosys_site)

#: A five-cell library: enough for `abc -liberty` (it needs a buffer) and a flop
#: modelled the way real Liberty flops are (an `ff` group, Q = "IQ").
_LIB = """library (tiny) {
  delay_model : table_lookup;
  cell (BUF) { area : 1; pin (A) { direction : input; } pin (Y) { direction : output; function : "A"; } }
  cell (INV) { area : 1; pin (A) { direction : input; } pin (Y) { direction : output; function : "!A"; } }
  cell (NAND2) { area : 2; pin (A) { direction : input; } pin (B) { direction : input; } pin (Y) { direction : output; function : "!(A&B)"; } }
  cell (NOR2) { area : 2; pin (A) { direction : input; } pin (B) { direction : input; } pin (Y) { direction : output; function : "!(A|B)"; } }
  cell (XOR2) { area : 3; pin (A) { direction : input; } pin (B) { direction : input; } pin (Y) { direction : output; function : "(A^B)"; } }
  cell (DFF) { area : 6; ff (IQ, IQN) { clocked_on : "CLK"; next_state : "D"; }
    pin (CLK) { direction : input; clock : true; } pin (D) { direction : input; } pin (Q) { direction : output; function : "IQ"; } }
}
"""

_GOLD = """module dut(input clk, input t, input e, input f, output y, output z);
  reg r;
  always @(posedge clk) if (t) r <= ~r;
  reg [7:0] sh;
  always @(posedge clk) sh <= {1'b0, sh[7:1]};
  reg [2:0] sq;
  always @(posedge clk) sq <= {1'b0, sq[2:1]};
  assign y = e & (r ^ sh[0]);
  assign z = f & (r ^ sq[0]);
endmodule
"""

#: The planted mismatch: the same shape with the observed register inverted.
#: Its synthesised gate is compared against `_GOLD`, so `y` really differs.
_PLANTED = _GOLD.replace("assign y = e & (r ^ sh[0]);",
                         "assign y = e & ~(r ^ sh[0]);")


def _synthesise(site, rtl_text: str, name: str) -> Path:
    """Gate netlist from `rtl_text`, by the tool: synth, map to `_LIB`."""
    rtl = site.dir / f"{name}_rtl.v"
    rtl.write_text(rtl_text)
    gate = site.dir / f"{name}_gate.v"
    rc, out = site.run(
        f"read_verilog {rtl}\n"
        "synth -top dut\n"
        f"dfflibmap -liberty {site.dir / 'tiny.lib'}\n"
        f"abc -liberty {site.dir / 'tiny.lib'}\n"
        "opt_clean\n"
        # `sh` is constant 0 after `opt_dff`; the gate netlist the flow grades
        # carries no such wire either (subservient's `wdata1_r`).
        "delete w:sh w:sq\n"
        "opt_clean\n"
        f"write_verilog -noattr {gate}\n", f"{name}_synth")
    assert rc == 0, out[-2000:]
    return gate


def _run_lec(site, gate: Path, tag: str) -> dict:
    """The real `lec_run.main` on a project holding `_GOLD` and `gate`."""
    proj = site.dir / f"proj_{tag}"
    (proj / "phase2/stage1/rtl").mkdir(parents=True)
    (proj / "phase2/stage2/synth").mkdir(parents=True)
    (proj / "phase2/stage1/rtl/dut.v").write_text(_GOLD)
    (proj / "phase2/stage2/synth/netlist.v").write_text(gate.read_text())
    rc = lec_run.main([str(proj), "--top", "dut",
                       "--container", site.where,
                       "--liberty", str(site.dir / "tiny.lib"),
                       "--timeout", "600"])
    report = json.loads((proj / "reports/lec.json").read_text())
    report["_rc"] = rc
    return report


@pytest.fixture
def site(tmp_path):
    s = _resolve_yosys_site(tmp_path)
    s.require()
    (s.dir / "tiny.lib").write_text(_LIB)
    return s


# ---------------------------------------------------------------------------
# the recipe
# ---------------------------------------------------------------------------
def _script(**kw):
    return lec_run.build_equiv_script(["/g/dut.v"], "/n/netlist.v", "dut",
                                      **kw)


def test_liberty_cell_wires_are_tagged_before_the_netlist_is_read():
    lines = _script(liberty="/pdk/x.lib").splitlines()
    tag = f"setattr -set {lec_run.LIBERTY_PIN_ATTR} 1 w:*"
    assert tag in lines
    i = lines.index(tag)
    # Right after the Liberty read and before the netlist: at that point the
    # gold is stashed, so `w:*` selects Liberty cell wires and nothing else.
    assert lines[i - 1] == "read_liberty -ignore_miss_func /pdk/x.lib"
    assert lines.index("read_verilog /n/netlist.v") > i
    assert lines.index("design -stash gold") < i


def test_the_tagged_wires_are_hidden_after_the_gate_is_flattened():
    lines = _script(liberty="/pdk/x.lib").splitlines()
    hide = f"rename -hide a:{lec_run.LIBERTY_PIN_ATTR}"
    assert lines.count(hide) == 1
    i = lines.index(hide)
    gate_start = lines.index("read_verilog /n/netlist.v")
    # Gate side only, after ITS flatten (the tag is copied onto the wires
    # `flatten` creates) and before `equiv_make` picks the pairs.
    assert gate_start < i < lines.index("design -stash gate")
    assert lines[i - 1] == "flatten"
    assert i < next(n for n, ln in enumerate(lines)
                    if ln.startswith("equiv_make"))


@pytest.mark.parametrize("kw", [{"liberty": None, "gate_is_generic": True},
                                {"liberty": None}])
def test_a_gate_with_no_liberty_is_untouched(kw):
    # A `$_`-cell gate has no cell pins to hide; its recipe must not move.
    assert lec_run.LIBERTY_PIN_ATTR not in _script(**kw)


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------
def test_a_register_pair_proved_early_still_holds_for_the_later_rungs(site):
    """RED before the fix: INCONCLUSIVE, `y` unproven, the ladder stopped on a
    rung that proved nothing. GREEN after: every point proven."""
    gate = _synthesise(site, _GOLD, "good")
    # The shape the defect needs, read from the tool's own output, so a later
    # synthesis that stops producing it fails here instead of passing vacuously.
    text = gate.read_text()
    assert re.search(r"^\s*wire r;", text, re.M), text
    assert not re.search(r"\b(sh|sq)\b", text), text
    rep = _run_lec(site, gate, "good")
    assert rep["verdict"] == "PASS", (
        rep.get("verdict"), rep.get("unproven_cells"),
        rep.get("verdict_explanation"))
    assert rep["equivalent"] is True
    assert rep["unproven_points"] == 0
    assert rep["compared_points"] == rep["miter_points"] > 0
    # A PASS names no unproven cell, although the `-seq 4` leg's workset
    # printed `y: failed` (the `-seq 16` leg closed it).
    assert rep["unproven_cells"] == []


def test_a_planted_mismatch_is_still_refused(site):
    gate = _synthesise(site, _PLANTED, "planted")
    rep = _run_lec(site, gate, "planted")
    assert rep["verdict"] != "PASS"
    assert rep["equivalent"] is False
    assert rep["unproven_points"] >= 1
    assert any(c.lstrip("\\") == "y" for c in rep["unproven_cells"]), rep


#: VERBATIM from this file's fixture through `lec_run.main` on the branch
#: (pinned image, 2026-09-28): the `-seq 4` leg, whose workset fails `y`, then
#: the `-seq 16` leg, which extends to step 8 and closes it. Only the
#: "Proving existence/induction step N (… clauses)" lines are left out.
_CLOSED_AFTER_A_FAILED_ATTEMPT = """
4. Executing EQUIV_INDUCT pass.
Found 2 unproven $equiv cells in module equiv:
  Proof for induction step failed. Extending to next time step.
  Proof for induction step failed. Extending to next time step.
  Proof for induction step failed. Extending to next time step.
  Proof for induction step failed. Trying to prove individual $equiv from workset.
  Trying to prove $equiv for \\y: failed.
  Trying to prove $equiv for \\z: success!
Proved 1 previously unproven $equiv cells.

6. Executing EQUIV_STATUS pass.
Found 3 $equiv cells in equiv:
  Of those cells 2 are proven and 1 are unproven.
  Unproven $equiv $auto$equiv_make.cc:258:find_same_wires$55: \\y_gold \\y_gate
Found a total of 1 unproven $equiv cells.

3. Executing EQUIV_STATUS pass.
Found 3 $equiv cells in equiv:
  Of those cells 2 are proven and 1 are unproven.
  Unproven $equiv $auto$equiv_make.cc:258:find_same_wires$55: \\y_gold \\y_gate
Found a total of 1 unproven $equiv cells.

4. Executing EQUIV_INDUCT pass.
Found 1 unproven $equiv cells in module equiv:
  Proof for induction step failed. Extending to next time step.
  Proof for induction step holds. Entire workset of 1 cells proven!
Proved 1 previously unproven $equiv cells.

6. Executing EQUIV_STATUS pass.
Found 3 $equiv cells in equiv:
  Of those cells 3 are proven and 0 are unproven.
  Equivalence successfully proven!
"""


def test_a_status_with_nothing_unproven_names_no_cell():
    parsed = lec_run.parse_equiv_output(_CLOSED_AFTER_A_FAILED_ATTEMPT)
    assert parsed["unproven"] == 0
    assert parsed["unproven_cells"] == []


def test_a_run_that_never_reached_status_still_names_the_workset():
    # #2182's fallback is kept for the case it was written for: the log ends
    # inside the induction pass, before any `equiv_status`.
    text = _CLOSED_AFTER_A_FAILED_ATTEMPT.split("\n6. Executing")[0]
    assert lec_run.parse_equiv_output(text)["unproven_cells"] == ["\\y"]


def test_no_test_here_skips():
    tree = ast.parse(Path(__file__).read_text())
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Attribute)
           and n.attr in ("skip", "skipif", "importorskip", "xfail")]
    assert not bad, f"line(s) {bad}: a test that cannot measure must FAIL"
