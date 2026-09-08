#!/usr/bin/env python3
"""Regression for #2050 — `synth`'s FSM re-encoding makes the LEC recipe build
an INCONSISTENT miter, and the gate then blamed induction depth for it.

MEASURED, opentitan_aes x sky130A, image sha256:06537f7e8d3c, plugin v1.17.72:

  * `synth` runs `fsm`, whose `fsm_recode` pass re-assigns FSM state encodings.
    19 of 19 extracted FSMs were recoded to one-hot; the state registers changed
    BOTH encoding and width (e.g. a 3-bit sparse `...u_prim_alert_sender.state_q`
    became a 7-bit one-hot register).
  * `equiv_make` matches key points BY NAME and has its OWN guard for this: it
    SKIPS a signal whose gold and gate widths differ.  `splitnets -ports`
    DEFEATS that guard — despite the flag name it splits internal signals too
    (internal-only is the default; `-ports` means "and ports as well"), so
    equiv_make no longer sees 3 bits against 7, it sees `state_q[0..2]` on each
    side and matches them POSITIONALLY.  Those pairs are not the same signal.
  * Forcing them equal makes the miter's key-point set inconsistent, and
    `equiv_induct`'s base case — `ez->assume(all unproven key points equal at
    steps 1..k); ez->solve()` — goes UNSAT: `Circuit inherently diverges!`.
    ONE poisoned pair aborts the induction for the WHOLE design: 3242 points
    across every block were left unproven by 3 recoded registers in one alert
    sender.
  * A/B on the SAME prepared design (only the named flag varies):
      splitnets + no encfile  : 4072 points,  676 proven, base case UNSAT at step 2
      no splitnets            : 4012 points, 1483 proven, base case HOLDS
      + `equiv_make -encfile` : 4087 points, 4056 proven / 31 unproven, 1068 s
    and, on a ~50-line design that reproduces the SAME SIGNATURE — a sparse,
    Hamming-separated FSM whose state flop lives in a submodule, so the recoded
    signal is `u_state_regs.u_state_flop.q_o` exactly as in the real design:
      RED   (recipe as shipped): three equiv_induct rungs, byte-identical output,
             all three `Proof for base case failed. Circuit inherently diverges!`
             at step 2 -> 9 points, 2 proven, 7 unproven
      GREEN (`synth -encfile` + `equiv_make -encfile`): "Creating encoder/decoder
             for signal u_state_regs.u_state_flop.q_o." -> 10/10, "Equivalence
             successfully proven!"
  * Adding `-encfile` to synth leaves the netlist BYTE-IDENTICAL (same sha256 on
    opentitan_aes and on fsmtop) — it only records the translation.

Two defects are fixed here:
  (1) build_equiv_script could not consume an encoding translation at all, and
      unconditionally emitted the `splitnets -ports` that defeats equiv_make's
      width guard;
  (2) the gate reported the base-case-UNSAT shape as "a disclosed
      sequential-depth capability gap ... close with sign-off LEC, which handles
      deep sequential induction", and justified it with "a real difference
      produces a counterexample".  SCOPED CLAIM, and the scope is the point: the
      gate also reads non-yosys LEC reports, where a real counterexample count
      CAN arrive and the guard is meaningful.  On the YOSYS path it cannot:
      lec_run.py hardcodes `non_equivalent_points` to 0 (its own comment says "a
      genuine difference surfaces as `unproven`"), and no pass in yosys's
      passes/equiv emits any counterexample phrase.  So on the path this design
      took, the sentence told the reader that 0 counterexamples meant something,
      from a field that could never be anything else.

chip-AGNOSTIC: yosys pass names and log phrases only; no chip/PDK/vendor
literal, and no design name is required by any assertion below.
"""
import ast
import json
import re
import sys
import subprocess
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import lec_run                       # noqa: E402
import lec_equivalence_check as gate  # noqa: E402


_GOLD = ["/g/a.sv", "/g/b.sv"]
_GATE = "/n/netlist.v"


def _script(**kw):
    return lec_run.build_equiv_script(
        _GOLD, _GATE, "chip_top", None, gate_is_generic=True, **kw)


# ---------------------------------------------------------------------------
# (1) the recipe generator
# ---------------------------------------------------------------------------
def test_without_an_encfile_the_script_is_unchanged():
    """NO-LEAK: every caller that does not supply an encfile keeps the exact
    pre-change recipe, so no design's verdict can move."""
    s = _script()
    assert s.count("splitnets -ports\n") == 2, s
    assert "equiv_make gold gate equiv\n" in s
    assert "-encfile" not in s


def test_an_encfile_is_passed_to_equiv_make():
    s = _script(fsm_encfile="/r/fsm_encoding.enc")
    assert "equiv_make -encfile /r/fsm_encoding.enc gold gate equiv\n" in s


def test_an_encfile_suppresses_the_splitnets_that_defeats_the_width_guard():
    """`-encfile` is keyed on the WHOLE signal name (`.fsm <module> <signal>`),
    which a bit-blasted design no longer has; and it is `splitnets` that turns
    a width mismatch equiv_make would SKIP into positional bit matches."""
    s = _script(fsm_encfile="/r/fsm_encoding.enc")
    assert "splitnets" not in s, s


def test_the_encfile_changes_nothing_else_in_the_recipe():
    """MEMBERSHIP, not counts: the two scripts must differ ONLY by the two
    splitnets lines and the equiv_make flag."""
    a = _script().splitlines()
    b = _script(fsm_encfile="/r/f.enc").splitlines()
    removed = [l for l in a if l not in b]
    added = [l for l in b if l not in a]
    assert removed == ["splitnets -ports", "splitnets -ports",
                       "equiv_make gold gate equiv"], removed
    assert added == ["equiv_make -encfile /r/f.enc gold gate equiv"], added


# ---------------------------------------------------------------------------
# (2) the classifier — the two flat walls are not the same wall
# ---------------------------------------------------------------------------
# The AES shape.  Note that equiv_induct prints BOTH phrases on this shape: it
# returns straight after the base case, so `Proved 0 previously unproven` is
# there too.  Only the base-case line separates the two walls, which is why
# `induction_wall_kind` must check it FIRST.
_BASE_CASE_UNSAT = """\
Found 3396 unproven $equiv cells in module equiv:
  Proving existence of base case for step 1. (2962863 clauses over 1130042 variables)
  Proving induction step 1. (6000231 clauses over 2283008 variables)
  Proof for induction step failed. Extending to next time step.
  Proving existence of base case for step 2. (6000232 clauses over 2283008 variables)
  Proof for base case failed. Circuit inherently diverges!
Proved 0 previously unproven $equiv cells.
Found 4072 $equiv cells in equiv:
  Of those cells 830 are proven and 3242 are unproven.
"""

# A real depth wall: the base case HELD, the induction step did not close.
_DEPTH_WALL = """\
Found 909 unproven $equiv cells in module equiv:
  Proving existence of base case for step 1. (1263 clauses over 487 variables)
  Proving induction step 1. (2765 clauses over 1048 variables)
  Proof for induction step failed. Extending to next time step.
Proved 0 previously unproven $equiv cells.
Found 7259 $equiv cells in equiv:
  Of those cells 6350 are proven and 909 are unproven.
"""

# The REAL log of that ~50-line reproducer's RED arm, verbatim (862 clauses, not 6M) —
# a scale model of the design log, kept as evidence that the classifier is reading the
# shape yosys actually emits and not a shape I invented.
_REPRO_RED = """\
21. Executing EQUIV_INDUCT pass.
Found 7 unproven $equiv cells in module equiv:
  Proving existence of base case for step 1. (862 clauses over 322 variables)
  Proving induction step 1. (1883 clauses over 693 variables)
  Proof for induction step failed. Extending to next time step.
  Proving existence of base case for step 2. (1884 clauses over 693 variables)
  Proof for base case failed. Circuit inherently diverges!
Proved 0 previously unproven $equiv cells.
Found 9 $equiv cells in equiv:
  Of those cells 2 are proven and 7 are unproven.
"""

_PROVEN = """\
Found 65 $equiv cells in equiv:
  Of those cells 65 are proven and 0 are unproven.
  Equivalence successfully proven!
"""


def test_base_case_unsat_is_not_classified_as_a_depth_wall():
    assert lec_run.induction_wall_kind(_BASE_CASE_UNSAT) == "miter_inconsistent"


def test_a_real_depth_wall_is_still_a_depth_wall():
    assert lec_run.induction_wall_kind(_DEPTH_WALL) == "induction_depth"


def test_the_reproducers_real_log_classifies_as_miter_inconsistent():
    """Not a hand-written fixture: this is the verbatim equiv_induct output of the
    ~50-line reproducer's RED arm. It must land on the same side as the design log."""
    assert lec_run.induction_wall_kind(_REPRO_RED) == "miter_inconsistent"
    r = lec_run.build_report(
        lec_run.parse_equiv_output(_REPRO_RED), "sfsm3", "netlist.v", None)
    assert r["verdict"] == "INCONCLUSIVE"
    assert r["induction_wall_kind"] == "miter_inconsistent"
    assert r["unproven_points"] == 7


def test_a_clean_proof_has_no_wall():
    assert lec_run.induction_wall_kind(_PROVEN) == ""
    assert lec_run.induction_wall_kind("") == ""


def test_the_kind_reaches_the_report():
    for raw, want in ((_BASE_CASE_UNSAT, "miter_inconsistent"),
                      (_DEPTH_WALL, "induction_depth"),
                      (_PROVEN, "")):
        r = lec_run.build_report(
            lec_run.parse_equiv_output(raw), "chip_top", "netlist.v", None)
        assert r["induction_wall_kind"] == want, raw[:60]


# ---------------------------------------------------------------------------
# (3) the gate — name the right cause, and stop prescribing the one remedy
#     that provably cannot work
# ---------------------------------------------------------------------------
def _gate_on(tmp_path, raw):
    r = lec_run.build_report(
        lec_run.parse_equiv_output(raw), "chip_top", "netlist.v", None)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "lec.json").write_text(json.dumps(r))
    (tmp_path / "reports" / "lec.rpt").write_text(raw)
    return gate.audit(tmp_path), r


def test_gate_names_the_inconsistent_miter(tmp_path):
    res, _ = _gate_on(tmp_path, _BASE_CASE_UNSAT)
    rules = {f.rule for f in res.findings}
    assert "LEC_INCONCLUSIVE_MITER_INCONSISTENT" in rules, rules
    assert "LEC_NOT_EQUIVALENT" not in rules
    assert res.inconclusive is True and res.passed is False
    msg = [f.message for f in res.findings
           if f.rule == "LEC_INCONCLUSIVE_MITER_INCONSISTENT"][0]
    # It must NOT sell the reader a depth remedy for a consistency problem.
    assert "sequential-depth gap" in msg and "NOT a sequential-depth gap" in msg
    assert "fsm_recode" in msg and "-encfile" in msg


def test_gate_still_calls_a_real_depth_wall_a_depth_wall(tmp_path):
    res, _ = _gate_on(tmp_path, _DEPTH_WALL)
    rules = {f.rule for f in res.findings}
    assert "LEC_INCONCLUSIVE_NONCONVERGENCE" in rules, rules
    assert "LEC_INCONCLUSIVE_MITER_INCONSISTENT" not in rules


def test_gate_no_longer_claims_a_real_difference_would_show_a_counterexample(
        tmp_path):
    """lec_run.py hardcodes `non_equivalent_points` to 0 for the yosys path —
    its own comment says 'a genuine difference surfaces as `unproven`'.  A
    verdict that told the reader 0 counterexamples meant 'probably equivalent'
    was reasoning from a field that can never be anything else."""
    res, rep = _gate_on(tmp_path, _DEPTH_WALL)
    assert rep["non_equivalent_points"] == 0
    msg = [f.message for f in res.findings
           if f.rule == "LEC_INCONCLUSIVE_NONCONVERGENCE"][0]
    assert "a real difference produces a counterexample" not in msg
    assert "hardcodes that field to 0" in msg


def test_a_producer_that_never_recorded_the_kind_keeps_the_old_verdict(
        tmp_path):
    """BACKWARD COMPATIBILITY, and the negative control for the new branch: an
    lec.json written before this change has no `induction_wall_kind`, so even
    the base-case-UNSAT shape must fall through to the previous rule rather
    than crash or silently change direction."""
    r = lec_run.build_report(
        lec_run.parse_equiv_output(_BASE_CASE_UNSAT),
        "chip_top", "netlist.v", None)
    r.pop("induction_wall_kind", None)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "lec.json").write_text(json.dumps(r))
    (tmp_path / "reports" / "lec.rpt").write_text(_BASE_CASE_UNSAT)
    res = gate.audit(tmp_path)
    rules = {f.rule for f in res.findings}
    assert "LEC_INCONCLUSIVE_NONCONVERGENCE" in rules, rules
    assert res.inconclusive is True and res.passed is False


# ---------------------------------------------------------------------------
# (4) producer <-> consumer contract
#
# The fix is a CONJUNCTION: synth must WRITE the translation and LEC must READ
# it.  A missing file is not an error anywhere — it silently restores the old,
# name-positional matching — so the two halves have to be pinned to each other
# by a test, not by a comment.
# ---------------------------------------------------------------------------
def test_the_resolver_finds_the_file_the_synth_step_writes(tmp_path):
    net = tmp_path / "netlist.v"
    net.write_text("module t(); endmodule\n")
    assert lec_run.fsm_encfile_beside_netlist(str(net)) is None
    enc = tmp_path / lec_run.FSM_ENCFILE_NAME
    enc.write_text(".fsm t state_q\n.map 010 ---1\n")
    assert lec_run.fsm_encfile_beside_netlist(str(net)) == str(enc.resolve())


def test_the_resolver_never_guesses(tmp_path):
    assert lec_run.fsm_encfile_beside_netlist("") is None
    assert lec_run.fsm_encfile_beside_netlist(
        str(tmp_path / "does_not_exist.v")) is None


@pytest.mark.parametrize("invert_output", [False, True])
def test_empty_encoding_table_preserves_scalarized_register_proofs(
        tmp_path, invert_output):
    """A synth with no FSM writes an empty encfile. A later transform can
    scalarize a bus; its named points must still be proved, and a changed
    output must remain unproven. Exercise the resolver, recipe and real SAT.
    """
    rtl = tmp_path / "delay.v"
    rtl.write_text("""module delay(input clk, input d, output o);
reg [31:0] stages;
always @(posedge clk) stages <= {stages[30:0], d};
assign o = stages[31];
endmodule
""")
    enc = tmp_path / lec_run.FSM_ENCFILE_NAME
    net = tmp_path / "netlist.v"
    synth = tmp_path / "synth.ys"
    synth.write_text(f"read_verilog {rtl}\nsynth -top delay -encfile {enc}\n"
                     f"splitnets -ports\nwrite_verilog -noattr -noexpr {net}\n")
    p = subprocess.run(["yosys", "-s", str(synth)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert enc.read_bytes() == b"", "fixture must exercise synth's empty table"
    if invert_output:
        text = net.read_text()
        assert "assign o =" in text
        net.write_text(text.replace("assign o =", "assign o = ~", 1))
    script = lec_run.build_equiv_script(
        [str(rtl)], str(net), "delay", None, gate_is_generic=True,
        fsm_encfile=lec_run.fsm_encfile_beside_netlist(str(net)),
        ladder_rungs=3)
    ys = tmp_path / "equiv.ys"
    ys.write_text(script + "equiv_status -assert\n")
    p = subprocess.run(["yosys", "-s", str(ys)], capture_output=True, text=True)
    raw = p.stdout + p.stderr
    report = lec_run.build_report(lec_run.parse_equiv_output(raw),
                                  "delay", str(net), None)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "lec.json").write_text(json.dumps(report))
    (tmp_path / "reports" / "lec.rpt").write_text(raw)
    if invert_output:
        assert p.returncode == 1, raw
        assert report["unproven_points"] > 0, report
        assert gate.audit(tmp_path).passed is False
    else:
        assert p.returncode == 0, raw
        assert report["compared_points"] >= 32, report
        assert report["unproven_points"] == 0, report
        assert gate.audit(tmp_path).passed is True


def test_empty_synth_map_preserves_scalar_register_alignment(tmp_path):
    net = tmp_path / "netlist.v"
    net.write_text("module t(); endmodule\n")
    (tmp_path / lec_run.FSM_ENCFILE_NAME).write_text("")
    enc = lec_run.fsm_encfile_beside_netlist(str(net))
    assert enc is None, "no FSM was recoded, so bit matching must remain enabled"
    script = lec_run.build_equiv_script(
        ["rtl.v"], str(net), "t", None, fsm_encfile=enc)
    assert script.count("splitnets -ports") == 2


def _functions_owning(src: str, marker: str) -> set:
    """The INNERMOST function that owns each occurrence of `marker`, by NAME.

    A call-site's identity is the function it lives in, not the line it sits
    on: a line number moves with every edit above it, while the owning
    function is the thing a reader means by "the other call-site". Parsed
    with `ast` and located by offset, so a nested helper that carried the
    marker would be named as itself rather than credited to its parent.
    """
    lines = src.splitlines(keepends=True)
    starts, acc = [], 0
    for ln in lines:
        starts.append(acc)
        acc += len(ln)

    def _line_of(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    tree = ast.parse(src)
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and getattr(n, "end_lineno", None)]
    owners = set()
    for m in re.finditer(re.escape(marker), src):
        line = _line_of(m.start())
        enclosing = [n for n in funcs if n.lineno <= line <= n.end_lineno]
        if enclosing:
            owners.add(max(enclosing, key=lambda n: n.lineno).name)
    return owners


def test_both_phase2_synth_call_sites_write_the_encfile():
    """The netlist that step-13 compares can come from either synth call-site
    (built-in read_verilog, or the read_slang/sv2v fallback that a modern-SV
    design such as an OpenTitan-class IP takes).  BOTH must record the
    translation, or the LEC fix is live on only one of them.  Read as TEXT:
    importing design_one_shot_runner drags in the whole runner."""
    src = (_PROGRAMS / "design_one_shot_runner.py").read_text()
    # THE MEMBER SET, not its size. `len(sites) == 2` and `src.count(...) == 2`
    # were pins on a live population's SIZE with nothing pinning its MEMBERS:
    # one call-site leaving and one arriving in the same batch leaves both
    # numbers at 2 while the population has become a different set, and the pin
    # would never say so (`population_pin_without_its_member_set` names this
    # module and this assertion). The identities are the FUNCTIONS that own the
    # call-sites, compared as a set in BOTH directions; the count survives only
    # as `len()` of that same set, never as a typed literal.
    _SYNTH_CALL_SITES = {"_phase2_sv_synth_fallback", "step_yosys_synth"}
    sites = _functions_owning(src, "synth -top {synth_top} -flatten")
    assert sites == _SYNTH_CALL_SITES, {
        "missing": sorted(_SYNTH_CALL_SITES - sites),
        "unexpected": sorted(sites - _SYNTH_CALL_SITES)}
    # The CODE form (the f-string interpolation `-encfile {`), never the bare
    # word: the surrounding comments name the flag too, and matching those
    # would pass on a file where neither call-site actually carries it.
    enc = _functions_owning(src, "-encfile {")
    assert enc == _SYNTH_CALL_SITES, {
        "missing": sorted(_SYNTH_CALL_SITES - enc),
        "unexpected": sorted(enc - _SYNTH_CALL_SITES)}
    # BOTH halves of the conjunction over the SAME set: every synth call-site
    # is an encfile call-site and back again, so a third call-site that
    # forgot the flag cannot hide behind a second one that carries it twice.
    assert enc == sites, {"synth_only": sorted(sites - enc),
                          "encfile_only": sorted(enc - sites)}
    assert len(sites) == len(_SYNTH_CALL_SITES)
    assert "from lec_run import FSM_ENCFILE_NAME" in src
    # AND THE OTHER HALF OF THAT IMPORT MUST EXIST. Caught for real while
    # writing this: restoring lec_run.py from a stale snapshot during a
    # negative-control swap dropped the constant and the resolver while
    # leaving the producer's `from lec_run import FSM_ENCFILE_NAME` in place —
    # a shipped ImportError in the synth step that every OTHER test in this
    # file still passed, because they all read the producer as TEXT and never
    # execute the import. A conjunction needs both halves asserted.
    assert isinstance(getattr(lec_run, "FSM_ENCFILE_NAME", None), str)
    assert callable(getattr(lec_run, "fsm_encfile_beside_netlist", None))
    assert src.count('"fsm_encoding.enc"') == 0, (
        "the producer must not re-type the filename — one definition, in "
        "lec_run.FSM_ENCFILE_NAME, is what keeps a rename from silently "
        "disabling the fix")


def test_the_recipe_funnel_is_actually_wired_to_the_resolver():
    """The generator can take an encfile and the synth step can write one, and the fix
    still does NOTHING unless the two are joined — `_make_script`, the one funnel every
    recipe in lec_run.main() goes through, has to pass the resolver's answer.

    Parsed with `ast`, not grepped: a comment mentioning `fsm_encfile=` would satisfy a
    grep and wire nothing, and this is the third half of a conjunction whose other two
    halves each have their own test above."""
    import ast
    tree = ast.parse((_PROGRAMS / "lec_run.py").read_text())

    funnels = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_make_script"]
    assert len(funnels) == 1, [n.lineno for n in funnels]

    calls = [n for n in ast.walk(funnels[0])
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "build_equiv_script"]
    assert len(calls) == 1, len(calls)

    kw = {k.arg: k.value for k in calls[0].keywords if k.arg}
    assert "fsm_encfile" in kw, sorted(kw)
    value = kw["fsm_encfile"]
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), \
        ast.dump(value)
    assert value.func.id == "fsm_encfile_beside_netlist", value.func.id
    # …and it must be asked about the netlist under test, not about something else.
    assert len(value.args) == 1 and isinstance(value.args[0], ast.Name), \
        ast.dump(value)
    assert value.args[0].id == "gate_abs", value.args[0].id


def test_encoding_names_preserve_all_hierarchical_state_words(tmp_path):
    enc = tmp_path / "enc"
    enc.write_text(".fsm top state_q\n.map 00 --1\n"
                   ".fsm child pipe.state_q\n.map 01 -1-\n")
    names = lec_run.fsm_signal_names(str(enc))
    assert names == ["pipe.state_q", "state_q"]
    script = _script(fsm_encfile=str(enc), fsm_preserve_signals=names)
    assert script.count("w:state_q %d w:*.state_q %d") == 2
    assert script.count("w:pipe.state_q %d w:*.pipe.state_q %d") == 2
    assert script.count("select *\n") == 2


@pytest.mark.parametrize("content", [
    ".fsm t state[0]\n.map 00 --1\n", ".map 00 --1\n",
    ".fsm t state\n.unrecognized command\n",
])
def test_unknown_encoding_selection_retains_width_guard(tmp_path, content):
    enc = tmp_path / "enc"
    enc.write_text(content)
    names = lec_run.fsm_signal_names(str(enc))
    assert names is None
    assert "splitnets" not in _script(
        fsm_encfile=str(enc), fsm_preserve_signals=names)


def test_empty_encoding_is_observed_not_assumed(tmp_path):
    enc = tmp_path / "enc"
    assert lec_run.fsm_signal_names(str(enc)) is None
    enc.write_text("# no FSM was extracted\n")
    assert lec_run.fsm_signal_names(str(enc)) == []
    assert _script(fsm_encfile=str(enc), fsm_preserve_signals=[]).count(
        "splitnets -ports w:*\n") == 2


@pytest.mark.parametrize("broken_reset", [False, True])
def test_scalar_data_and_recoded_fsm_real_yosys(tmp_path, broken_reset):
    """A real scalarized synthesis DUT, its full state map, and reset mutation.

    Candidate must recover data anchors without pairing binary FSM bits with
    one-hot bits. The reset mutant must remain unproven on the same recipe.
    """
    import shutil
    import subprocess
    yosys = shutil.which("yosys")
    if not yosys:
        pytest.skip("real Yosys is unavailable")
    rtl = """module toy(input clk, input rst_n, input go, input [3:0] d,
                       output [3:0] q);
    reg [1:0] state;
    (* keep *) reg [3:0] data_q;
    always @(posedge clk) begin
      if (!rst_n) begin state <= 0; data_q <= 0; end
      else begin
        case (state)
          0: if (go) state <= 1;
          1: state <= 2;
          2: state <= 3;
          3: state <= 0;
        endcase
        if (state == 1) data_q <= d;
      end
    end
    assign q = data_q ^ {4{state == 2}};
    endmodule
    """
    gold = tmp_path / "gold.v"
    gate_rtl = tmp_path / "gate_rtl.v"
    gate = tmp_path / "gate.v"
    enc = tmp_path / "enc"
    wrapper = ("module testtop(input clk, input rst_n, input go, input [3:0] d, "
               "output [3:0] q); toy inner(clk, rst_n, go, d, q); endmodule\n")
    gold.write_text(rtl + wrapper)
    gate_rtl.write_text(rtl.replace("if (!rst_n)", "if (rst_n)")
                        if broken_reset else rtl)

    def run(script, name):
        p = tmp_path / (name + ".ys")
        p.write_text(script)
        res = subprocess.run([yosys, "-Q", "-T", "-s", str(p)],
                             capture_output=True, text=True, timeout=60)
        (tmp_path / (name + ".log")).write_text(res.stdout + res.stderr)
        assert res.returncode == 0, res.stdout[-1500:] + res.stderr
        return res.stdout

    run(f"read_verilog {gate_rtl}\nsynth -top toy -encfile {enc}\n"
        f"splitnets\nwrite_verilog -noexpr {gate}\n", "synth")
    gate.write_text(gate.read_text() + wrapper)
    names = lec_run.fsm_signal_names(str(enc))
    assert names == ["state"], enc.read_text()
    kwargs = dict(gold_files=[str(gold)], gate_netlist=str(gate), top="testtop",
                  liberty=None, gate_is_generic=True, fsm_encfile=str(enc),
                  ladder_rungs=2)
    before = run(lec_run.build_equiv_script(**kwargs), "baseline")
    flat_enc = tmp_path / "flat.enc"
    flat_enc.write_text(enc.read_text().replace(".fsm toy state", ".fsm toy inner.state"))
    import hashlib
    width = len(next(line.split()[2] for line in enc.read_text().splitlines()
                     if line.startswith(".map ")))
    restore = {"encoding_path": str(flat_enc),
               "encoding_sha256": hashlib.sha256(flat_enc.read_bytes()).hexdigest(),
               "aliases": {"inner.state": [f"inner.state[{i}]" for i in range(width)]}}
    after = run(lec_run.build_equiv_script(
        **kwargs, fsm_preserve_signals=names, scan_fsm_restore=restore), "candidate")
    b = lec_run.parse_equiv_output(before)
    c = lec_run.parse_equiv_output(after)
    assert c["total"] > b["total"], (b, c)
    assert "Presumably equivalent wires: inner.state[" not in after
    assert "Presumably equivalent wires: inner.data_q[" in after
    if broken_reset:
        assert not c["equivalent"] and c["unproven"] > 0, c
    else:
        assert c["equivalent"] and c["unproven"] == 0, c


def test_scan_mapping_follows_only_an_identified_flop_output(tmp_path):
    enc = tmp_path / "enc"
    enc.write_text(".fsm top state\n.map 0 -1\n.map 1 1-\n")
    pre = tmp_path / "pre.v"
    pre.write_text("module top();\n"
                   " FF f0 (.CK(clk), .D(d), .Q(state[0]));\n"
                   " FF f1 (.CK(clk), .D(d), .Q(state[1]));\nendmodule\n")
    gate = tmp_path / "scan.v"
    gate.write_text("module top();\n"
                    " wire \\core.state[0] ;\n wire \\scan_tail ;\n"
                    " FF \\core.f0 (.CK(clk), .D(muxed), .Q(\\core.state[0] ));\n"
                    " FF \\core.f1 (.CK(clk), .D(muxed), .Q(\\scan_tail ));\n"
                    "endmodule\n")
    lib = ('cell(FF) { pin(D) { direction : input; } '
           'pin(Q) { direction : output; function : "IQ"; } }')
    args = dict(encfile=str(enc), gate_netlist=str(gate), top="top",
                scan_mode={"internal_prefix": "core"}, pre_scan_netlist=str(pre),
                liberty_text=lib, dff_cells=["FF"])
    found = lec_run.scan_fsm_mapping(**args)
    assert found is not None
    assert found["aliases"] == {"_LECWRAP.core.state": [
        "_LECWRAP.core.state[0]", "_LECWRAP.scan_tail"]}
    assert ".fsm top _LECWRAP.core.state\n" in found["encoding_text"]
    # Input muxes must never become a claimed state alias.
    assert lec_run.scan_fsm_mapping(**dict(args, liberty_text=lib.replace(
        "direction : output", "direction : input"))) is None
    assert lec_run.scan_fsm_mapping(**dict(args, dff_cells=[])) is None
    assert lec_run.scan_fsm_mapping(**dict(args, scan_mode={})) is None
