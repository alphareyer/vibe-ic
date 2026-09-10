"""The output-buffer walk is MULTI-STAGE, and only the 1-stage case was pinned.

WHY THIS EXISTS. `5d4fbfcce` (v1.19.100) made LEC pin correspondence survive a
PnR-inserted output buffer, and shipped `test_lec_output_buffer_correspondence`
with it. Every case in that file uses ONE buffer, and its positive test asserts
`len(output_buffer_paths['Y']) == 1`. The refusal cases there are a second
DRIVER on the same net (ambiguous) — not a second STAGE in a chain.

But the design that motivated the fix needs more than one stage. Measured on
sha256's post-layout LEC (see THE MEASUREMENT below):
the four points the fix recovered sit behind a TWO-STAGE chain,

    net122 -> buf_6 wire123 -> net123 -> buf_6 wire122 -> read_data[11]

and recovering them moved sha256's unproven count 27 -> 23. So the behaviour
the benchmark actually depends on is the multi-stage walk, and nothing pinned
it: a regression that bounded the walk to a single hop would pass the whole
existing suite and silently return sha256 to 27.

MEASURED on live main d39110fcb8ab before writing this, driving the shipped
`classify_pin_permutation_points` over cascaded repeaters:

    1-stage chain: accepted=2 rejected=0 output_buffer_path_len=1
    2-stage chain: accepted=2 rejected=0 output_buffer_path_len=2
    3-stage chain: accepted=2 rejected=0 output_buffer_path_len=3

The walk is already correct. This file is the missing GUARD, not a fix, which
is why it ships with a mutation arm
(`tools/ci/mutation_arm/lec-output-buffer-walk-bounded-to-one-hop.patch`):
refusing the walk the moment one hop does not land on the upstream net is the
smallest edit that re-plants the pre-v1.19.100 behaviour, and these tests go
RED on it while the whole of `test_lec_output_buffer_correspondence.py` stays
GREEN — which is precisely why this file was owed.

PREDICTED DIRECTIONS, written before these ran:
  1 2-stage chain  -> accepted, path length 2
  2 3-stage chain  -> accepted, path length 3
  3 1-stage chain  -> unchanged (the case the old suite already pinned)
  4 the refusals   -> unchanged: a second driver, a non-buffer driver and an
                      inverting driver still refuse at EVERY chain depth, so
                      this pins transparency and never widens it
All four held. Under the mutation, 1..3 go RED and 4 stays GREEN.

THE MEASUREMENT THIS GUARD PROTECTS (relocated here from a standalone doc so
this branch is what it actually is -- tests plus a mutation arm -- and so the
numbers travel with the test that depends on them).

sha256 post-layout LEC. **INCONCLUSIVE. Neither a pass nor a failure.**
Anchors, both arms, same netlists:

    arm                                         classifier      acc/rej    proven  unproven  total
    run of record (2026-09-06, icsha lane)      pre-v1.19.100   138 / 75    11236        27  11263
    cyaes2 lane, tip f91aaa391                  with 5d4fbfcce  142 / 71    11240        23  11263

`non_equivalent_points` is null/absent in BOTH arms and BOTH logs contain ZERO
counterexample markers. The 23 are NOT_MEASURED, never non-equivalent.

Neither run was killed, so the count is readable: the run of record exited
`yosys_rc 0`; this lane's `End of script ... 902.98s, MEM 15110.62 MB peak`.

WHY THE 27 WAS STALE. `5d4fbfcce` (v1.19.100) landed 2026-09-09 01:25; the run
of record is 2026-09-06 04:41, three days earlier. Classifier A/B over the
identical 213 first-pass points, same netlists, same Liberty: exactly 4 points
moved rejected -> accepted, 0 moved back -- `__uuf__._08478_.A1/.A2` and
`__uuf__._08560_.A1/.A2`, textbook A1<->A2 swaps on `a21oi` (gold A1/A2
`_03043_`/`_03056_`; gate resolved `_03056_`/`_03043_`; B1 identical). They were
refused only for "an output net of the instance differs", which is the
PnR-inserted TWO-STAGE output buffer the fix now walks. Renames 125 -> 129,
unproven 27 -> 23.

THE REMAINING 23 -- ROOT CAUSES, NOT POINT IDS. The recipe is already the full
ladder (`equiv_simple -> -seq 4 -> -seq 16 -> -seq 64`); rung 3 reached
induction step 64 and every step 1..64 failed while fallback proved 4, so depth
is exhausted and induction failing at EVERY depth is not a depth wall.

  * 20 of 27 sat on pure drive-strength swaps (`a21oi_1->_4`, `maj3_1->_4`,
    `a32oi_1->_4`, `o32ai_1->_4`, `xnor2_1->_2`) -- functionally identical under
    the Liberty this recipe reads, so drive strength is not the cause.
  * Two distinct classifier-rejection causes remain: a pin carrying the SAME net
    on both sides (14 points), and an output net differing (4 -- now closed).
  * 9 were accepted and renamed and still did not prove.

NOT_MEASURED: whether a re-run of the full `phase3_one_shot_runner` post-layout
step (rather than a direct recipe replay) reports the same 23, and whether the
23 close at all.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lec_post_layout_check as L  # noqa: E402

LIB = '''library(neutral) {
cell(logic2) { pin(A){direction:input;} pin(B){direction:input;}
 pin(Y){direction:output; function:"A & B";} }
cell(repeater) { pin(I){direction:input;} pin(Z){direction:output; function:"I";} }
cell(inverter) { pin(I){direction:input;} pin(Z){direction:output; function:"!I";} }
}'''
GOLD = 'module top(input a,b, output y);\nlogic2 u (.A(a), .B(b), .Y(y));\nendmodule'


def _chain(n, last='repeater', extra=''):
    """logic2's output reaches `y` through `n` cascaded transparent cells.

    The A/B swap on `u` is the symmetry under test; the chain is what the walk
    has to see through.
    """
    wires = ', '.join(f'w{i}' for i in range(n))
    body = [f'module top(input a,b, output y);', f'wire {wires};',
            'logic2 u (.A(b), .B(a), .Y(w0));']
    for i in range(n - 1):
        body.append(f'repeater b{i} (.I(w{i}), .Z(w{i+1}));')
    body.append(f'{last} b{n-1} (.I(w{n-1}), .Z(y));')
    if extra:
        body.append(extra)
    body.append('endmodule')
    return '\n'.join(body)


def _classify(text):
    return L.classify_pin_permutation_points(['u.A', 'u.B'], GOLD, text, LIB)


# --------------------------------------------------------------------------
# 1 / 2 / 3 — the walk sees through a chain, at the depth the chain has
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stages", [1, 2, 3])
def test_the_walk_sees_through_a_chain_of_that_depth(stages):
    result = _classify(_chain(stages))
    assert not result['rejected'], (
        f"a {stages}-stage output-buffer chain was refused: "
        f"{[r.get('reason') for r in result['rejected']]}")
    assert len(result['accepted']) == 2
    path = result['accepted'][0]['output_buffer_paths']['Y']
    assert len(path) == stages, (
        f"the walk reported {len(path)} hop(s) for a {stages}-stage chain — a "
        "walk bounded shorter than the chain is the regression this pins")


def test_the_recovered_correspondence_is_the_swap_itself():
    """The point of seeing through the chain is that the A/B swap stays
    visible. A path that is walked but drops the rename buys nothing."""
    result = _classify(_chain(2))
    renames, _records = L.build_pin_correspondence_renames(
        result['accepted'], ['u.A', 'u.B'])
    assert renames == [('u.A', 'u.B'), ('u.B', 'u.A')], renames


# --------------------------------------------------------------------------
# 4 — the refusals must NOT widen with depth
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stages", [1, 2, 3])
def test_an_inverting_last_stage_is_never_transparent(stages):
    assert len(_classify(_chain(stages, last='inverter'))['rejected']) == 2


@pytest.mark.parametrize("stages", [1, 2, 3])
def test_a_second_driver_on_the_output_still_refuses(stages):
    got = _classify(_chain(stages, extra='repeater x9 (.I(a), .Z(y));'))
    assert len(got['rejected']) == 2, (
        "an ambiguous second driver was accepted; the walk must refuse a net "
        "it cannot attribute to ONE driver, at any depth")


@pytest.mark.parametrize("stages", [1, 2, 3])
def test_an_unmodelled_driver_still_refuses(stages):
    got = _classify(_chain(stages, extra='unmodelled u9 (.A(a), .Y(y));'))
    assert len(got['rejected']) == 2
