"""Complete binary tables must not depend on state-local unused inputs."""
import itertools
import subprocess

import pytest

import moore_fsm_table_emit as M
from _hostpaths import require_repo


def _fixture(renamed=False):
    states = ("HOME", "TAKE", "PAUSE", "RETURN") if renamed else (
        "ALPHA", "BETA", "GAMMA", "DELTA")
    inputs = ("accept", "pending", "toggle") if renamed else (
        "gate", "tag", "edge_in")
    prompt = "\n".join(
        [" - input clk", " - input reset"]
        + [f" - input {name}" for name in inputs]
        + [" - output out_a", " - output out_b"])

    def next_index(state, bits):
        a, b, c = map(int, bits)
        return ((1 if a ^ c else 2), (3 if b else 2), 3,
                (1 if c else 0))[state]

    rows = {(s, bits): next_index(s, bits)
            for s in range(4)
            for bits in map("".join, itertools.product("01", repeat=3))}
    table = [f"STATES: {' '.join(states)}",
             f"INPUTS: {' '.join(inputs)}", "OUTPUTS: out_a out_b",
             f"RESET: {states[0]} sync active_high"]
    table += [f"TRANS: {states[s]} {bits} -> {states[nxt]}"
              for (s, bits), nxt in rows.items()]
    table += [f"OUT: {name} out_a={s >> 1} out_b={s & 1}"
              for s, name in enumerate(states)]
    return prompt, "\n".join(table), inputs, rows


def _run(rtl, tb, tmp_path):
    dut, bench, exe = (tmp_path / name for name in ("dut.sv", "tb.sv", "sim.vvp"))
    dut.write_text(rtl)
    bench.write_text(tb)
    compile_result = subprocess.run(
        ["iverilog", "-g2012", "-s", "tb", "-o", str(exe), str(dut), str(bench)],
        capture_output=True, text=True)
    assert compile_result.returncode == 0, compile_result.stderr
    result = subprocess.run(["vvp", str(exe)], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def _bench(inputs, body):
    a, b, c = inputs
    return f"""module tb;
  reg clk=0, reset=0, {a}=0, {b}=0, {c}=0;
  wire out_a, out_b;
  TopModule dut(.*);
  task tick; begin #1;clk=1;#1;clk=0;#1;end endtask
  task check(input [1:0] expected);
    if ({{out_a,out_b}} !== expected)
      $fatal(1,"expected=%b observed=%b",expected,{{out_a,out_b}});
  endtask
  task reach(input integer target);
    begin
      reset=1;{{{a},{b},{c}}}=0;tick;reset=0;
      if(target==1) begin {{{a},{b},{c}}}=3'b001;tick;end
      if(target>=2) begin {{{a},{b},{c}}}=0;tick;end
      if(target==3) tick;
      check(target);
    end
  endtask
  initial begin
    {body}
    $finish;
  end
endmodule
"""


@pytest.mark.parametrize("renamed", [False, True])
def test_every_binary_transition_and_output_is_preserved(tmp_path, renamed):
    prompt, table, inputs, rows = _fixture(renamed)
    rtl = M.synth(prompt, table)
    body = "\n".join(
        f"reach({state}); {{{','.join(inputs)}}}=3'b{bits};"
        f"#1;check(2'd{state});tick;check(2'd{nxt});"
        for (state, bits), nxt in rows.items())
    _run(rtl, _bench(inputs, body), tmp_path)


@pytest.mark.parametrize("renamed", [False, True])
def test_unknown_unused_inputs_preserve_partial_and_constant_transitions(tmp_path, renamed):
    prompt, table, inputs, rows = _fixture(renamed)
    rtl = M.synth(prompt, table)
    # Relevance changes by state; the first state needs nonadjacent bits.
    support = ({0, 2}, {1}, set(), {2})
    body = []
    for (state, bits), nxt in rows.items():
        for unknown in "xz":
            selector = "".join(bit if i in support[state] else unknown
                               for i, bit in enumerate(bits))
            body.append(f"reach({state}); {{{','.join(inputs)}}}=3'b{selector};"
                        f"#1;check(2'd{state});tick;check(2'd{nxt});")
    _run(rtl, _bench(inputs, "\n".join(body)), tmp_path)


def test_relevant_unknowns_and_invalid_states_keep_reset_defaults(tmp_path):
    prompt, table, inputs, _ = _fixture()
    body = []
    for state, relevant in ((0, (0, 2)), (1, (1,)), (3, (2,))):
        for index in relevant:
            for unknown in "xz":
                selector = list("000")
                selector[index] = unknown
                body.append(f"reach({state}); {{{','.join(inputs)}}}=3'b{''.join(selector)};"
                            "tick;check(2'b00);")
    for unknown in "xz":
        body.append(f"reach(1);dut.state=2'b{unknown}{unknown};#1;check(2'b00);"
                    f"tick;check(2'b00);{{{','.join(inputs)}}}=3'b001;"
                    "tick;check(2'b01);")
    _run(M.synth(prompt, table), _bench(inputs, "\n".join(body)), tmp_path)


def test_incomplete_constant_rows_are_refused_before_support_reduction():
    prompt, table, _, _ = _fixture()
    incomplete = table.replace("TRANS: GAMMA 111 -> DELTA\n", "")
    assert M.synth(prompt, incomplete) is None


def test_real_prompt_consumer_preserves_ignored_controls(tmp_path):
    import behavioral_fsm_synth

    prompt = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "real_benchmark", "directional_bump_fall_moore_prompt.md"
    ).read_text()
    rtl = behavioral_fsm_synth.synth(prompt)
    assert rtl is not None
    _run(rtl, """module tb;
  reg clk=0, areset=0, bump_left=0, bump_right=0, ground=1;
  wire walk_left,walk_right,aaah;
  TopModule dut(.*);
  task tick;begin #1;clk=1;#1;clk=0;#1;end endtask
  task check(input [2:0] expected);
    if({walk_left,walk_right,aaah} !== expected)
      $fatal(1,"expected=%b observed=%b",expected,{walk_left,walk_right,aaah});
  endtask
  initial begin
    areset=1;#1;check(3'b100);areset=0;
    ground=0;tick;check(3'b001);
    bump_left=1'bx;bump_right=1'bz;tick;check(3'b001);
    ground=1;tick;check(3'b100);
    $finish;
  end
endmodule
""", tmp_path)
