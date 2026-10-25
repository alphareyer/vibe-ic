"""The SENSOR THRESHOLD LADDER + CHANGE-DIRECTION artifact type.

A monotonic quantity sensed by N thresholds, a zone table, and one further
output asserted by the DIRECTION of the last zone change.  Before
`threshold_ladder_synth` no recogniser in the registry covered this shape, so
every such prompt WAIVED to the AI backup and the answer depended on which way
the authoring agent read one ambiguous sentence.

The explicit previous/current relation owns intermediate direction. Extreme
zone overrides and reset are separate from that relation.

The fixture below is a synthetic battery-gauge ladder, not a benchmark prompt:
the solver must fire on the SHAPE, never on a remembered design.
"""
import subprocess

import pytest

# Reuse the fixture and registry setup; that module never imports the owner.
from test_threshold_ladder_registry_route import LADDER, reg
import threshold_ladder_synth as tl


def test_the_ladder_prompt_is_program_solved():
    """GREEN: the shape emits RTL, and the registry routes to this type."""
    rtl = tl.synth(LADDER)
    assert rtl is not None
    assert "module TopModule" in rtl
    kind, reg_rtl = reg.generate(LADDER, "TopModule")
    assert kind == "threshold_ladder"
    assert reg_rtl == rtl
    # The verbatim nonzero bus range is part of the interface contract.
    assert "input [3:1] c" in rtl
    assert "[2:0] c" not in rtl
    # Extreme row overrides and the two independent interior rows remain checked.
    assert "boost = 1'b1;" in _row(rtl, 0)
    assert "boost = 1'b0;" in _row(rtl, 3)
    assert all("boost = direction_asserted;" in _row(rtl, z) for z in (1, 2))


def _row(rtl, zone):
    return next(line for line in rtl.splitlines() if f"2'd{zone}:" in line)



@pytest.mark.parametrize("cell,expected", [("shed1, shed2", 0), ("shed1, shed2, shed3", 1)])
def test_the_zone_outputs_are_read_from_the_table(cell, expected):
    """MUTATION: move an output to a different zone; the RTL must follow."""
    rtl = tl.synth(LADDER.replace("| shed1, shed2\n", f"| {cell}\n"))
    assert rtl is not None
    assert f"shed3 = 1'b{expected};" in _row(rtl, 1)


def test_it_does_not_fire_on_another_family():
    """FALSE-POSITIVE guard: a K-map prompt must stay with its own owner."""
    kmap = """
For the following Karnaugh map, give the circuit implementation.

      ab
  cd  00  01  11  10
  00 | 0 | 0 | 0 | 1 |
  01 | 1 | 0 | 0 | 0 |
  11 | 1 | 0 | 1 | 1 |
  10 | 1 | 0 | 0 | 1 |

module TopModule (
  input c,
  input d,
  output [3:0] mux_in
);
"""
    assert tl.synth(kmap) is None


@pytest.mark.parametrize("relation", ["lower", "higher"])
@pytest.mark.parametrize("polarity", ["active-high", "active-low"])
@pytest.mark.parametrize("wording", ['direct', 'actuator'])
def test_native_direction_sequence(tmp_path, relation, polarity, wording):
    prompt = LADDER.replace("was lower", "was " + relation).replace("active-high", polarity)
    if wording == 'actuator':
        prompt = prompt.replace(
            'the boost converter (controlled by boost) should be enabled',
            'the auxiliary feed should be increased by opening the reserve gate (controlled by boost)')
        prompt = ('The nominal output between the upper and lower thresholds also uses '
                  'the reading previous to the last sample.\n\n') + prompt
    kind, rtl = reg.generate(prompt, "TopModule")
    assert kind == "threshold_ladder"
    assert rtl is not None
    assert ("if (reset)" if polarity == "active-high" else "if (!reset)") in rtl
    rising = int(relation == "lower")
    falling = 1 - rising
    # Includes hold after both directions, extremes, and reset from an interior.
    sequence = [(1, 0, 15), (0, 1, 6 + rising), (0, 1, 6 + rising),
                (0, 3, 2 + rising), (0, 1, 6 + falling),
                (0, 1, 6 + falling), (0, 0, 15), (0, 7, 0),
                (0, 3, 2 + falling), (1, 3, 15), (0, 1, 6 + rising)]
    checks = []
    for i, (reset, sensors, expected) in enumerate(sequence):
        reset = reset if polarity == "active-high" else 1 - reset
        checks.append(f"reset = {reset}; c = 3'd{sensors}; #5; clk = 1; #1; "
                      f"if ({{shed3,shed2,shed1,boost}} !== 4'd{expected}) "
                      f'$fatal(1, "step {i}: got %d expected {expected}", '
                      "{shed3,shed2,shed1,boost}); #4; clk = 0;")
    design = tmp_path / "design.v"
    design.write_text(rtl)
    tb = tmp_path / "tb.v"
    tb.write_text("""module tb;
reg clk = 0, reset = 0;
reg [3:1] c = 0;
wire shed3, shed2, shed1, boost;
TopModule dut(clk, reset, c, shed3, shed2, shed1, boost);
initial begin
""" + "\n".join(checks) + '\n$display("SEQUENCE_PASS"); $finish; end\nendmodule\n')
    for command in (["iverilog", "-g2012", "-s", "tb", "-o", str(tmp_path / "sim"),
                     str(design), str(tb)], ["vvp", str(tmp_path / "sim")]):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    assert "SEQUENCE_PASS" in result.stdout



_RELATION = next(p for p in LADDER.split("\n\n") if p.startswith("If ")).strip()


@pytest.mark.parametrize("old,new", [
    (_RELATION, ""),  # no relation, despite the extreme pin
    (_RELATION, _RELATION + "\n" + _RELATION.replace("lower", "higher")),
    *[(_RELATION, _RELATION.replace(old, new)) for old, new in [
        ("lower", "equal"), ("controlled by boost", "controlled by foreign"),
        ("should be enabled", "should not be enabled"),
        ("previous level", "previous output"), ("current level", "current output"),
        ("lower", "lower or higher")]],
    ("| c[1], c[2]       |", "| c[2], c[3]       |"),  # broken inclusion chain
    ("  output reg boost\n", "  output reg boost,\n  output reg alarm\n"),  # nonunique output
], ids=["missing", "contradictory", "unsupported", "foreign-output", "negated",
        "foreign-previous", "foreign-current", "ambiguous", "broken-chain", "extra-output"])
def test_unowned_or_unclosed_ladder_refuses(old, new):
    assert old in LADDER
    assert tl.synth(LADDER.replace(old, new)) is None


@pytest.mark.parametrize("old,new,expected", [
    ("asserted, and all four outputs asserted", "asserted", "sensed > zone"),
    (_RELATION, "If the previous zone is higher than the current zone, boost should be asserted.",
     "sensed < zone"),
])
def test_relation_is_independent_of_extreme_pin_and_output_spelling(old, new, expected):
    rtl = tl.synth(LADDER.replace(old, new).replace("boost", "assist"))
    assert rtl is not None
    assert "assist = direction_asserted;" in rtl
    assert expected in rtl


@pytest.mark.parametrize('effect', [
    'should not be increased by opening', 'should be decreased by opening',
    'should be increased by closing',
])
def test_actuator_wording_does_not_infer_unsupported_polarity(effect):
    prompt = LADDER.replace(
        'the boost converter (controlled by boost) should be enabled',
        'the auxiliary feed ' + effect + ' the reserve gate (controlled by boost)')
    assert tl.synth(prompt) is None


@pytest.mark.parametrize('extra', [
    'If the previous level was lower than the present level, boost should not be enabled.',
    'When the previous level was higher than now, boost is disabled.',
    'The previous level must disable boost.',
    'Unless the previous level was equal to the present level, boost is asserted.',
    'The current level is lower than the previous level, boost is disabled.',
    'The sensed level was lower than the previous level, boost is disabled.',
    'The sensed level was equal to the previous level, boost is asserted.',
])
def test_unsupported_extra_previous_conditions_are_not_silently_dropped(extra):
    assert tl.synth(LADDER + '\n' + extra) is None


_PRIOR_CONTRADICTION = (
    'If the prior level was higher than the current level, boost should be disabled.'
)


def _route_outcome(prompt):
    direct = "refused" if tl.synth(prompt) is None else "emitted"
    registry = "no-route" if reg.generate(prompt, "TopModule") == (None, None) else "routed"
    return direct, registry


def test_exact_prior_contradiction_refuses_synth_and_registry_route():
    prompt = LADDER + '\n' + _PRIOR_CONTRADICTION
    assert _route_outcome(prompt) == ("refused", "no-route")


@pytest.mark.parametrize('extra', [
    'If the earlier level was higher than the present level, boost should be disabled.',
    'If the last-sample level was higher than now, boost should be disabled.',
])
def test_other_prior_sample_comparisons_are_not_silently_dropped(extra):
    prompt = LADDER + '\n' + extra
    assert _route_outcome(prompt) == ("refused", "no-route")


def test_noncomparative_prior_sample_narrative_remains_accepted():
    prompt = LADDER + '\nThe prior sample is retained for diagnostic reporting.'
    rtl = tl.synth(prompt)
    assert rtl is not None
    assert reg.generate(prompt, "TopModule") == ("threshold_ladder", rtl)
