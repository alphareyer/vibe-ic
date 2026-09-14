"""test_analog_deck_vector_retention.py — retaining the vectors a deck
MEASURES, and only those (lane icadc, 2026-09-15).

THE DEFECT THIS COVERS, MEASURED BY A/B ON ONE DECK.  `analog_a3_netlist_emit`
rendered its validation testbench with no `.save`, so ngspice kept the full
time vector of every internal node for the whole transient.  Two arms of ONE
deck differing by exactly the `.save` line, same image, same `--cpus 1`, same
`--memory 24g`, launched together:

    t_sim 18.05 us   with `.save`     RSS   42.8 MiB     542 kB per us
    t_sim 20.03 us   without          RSS  106.7 MiB    3784 kB per us

Over the 13824 us conversion record that deck runs, that is ~7.5 GB against
~52 GB.  The `.save` arm's extrapolation was checked against an independent
two-day run of the same circuit: measured 7.22 GiB at 11.8 ms against ~7.5 GB
predicted.

BOTH DIRECTIONS ARE PROVED HERE, AND SEPARATELY.  The mutation arms are the
ones that would pass quietly: a retention derived from the deck's PROSE (a
`* condition:` line names nodes in the same vocabulary the cards do, and a
`.save` carrying one would be refused as an unknown vector and the whole
retention lost), a retention that reads back its own `.save` instead of
deriving one, a `.save` written INSIDE `.control` (where it is the interactive
command that applies to one analysis, the same placement error
`_TB_INTEGRATION_OPTIONS` states for `.options`), and an EMPTY `.save`, which
retains nothing and makes every later reference answer `no such vector` on a
run that still exits 0.

Block, port and net names are synthetic.  No chip / SKU / foundry literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_deck_vector_retention as vr          # noqa: E402
import analog_a3_netlist_emit as a3                # noqa: E402


def _deck(*, control=("tran 5n 13824000n",
                      "meas tran vavg avg v(bit_out) from=1n to=2n",
                      "meas tran m1 max v(xdut.n1)"),
          extra=(), save=None):
    lines = ["* tb_conv — stimulus for the A3 block netlist",
             ".include conv.sp",
             "v_vdd vdd 0 1.2",
             "v_clk clk 0 pulse(0 1.2 0n 1n 1n 499n 1000n)",
             "xdut vdd 0 vin clk bit_out conv"]
    lines += list(extra)
    if save:
        lines.append(save)
    lines += [".control", *control, ".endc", ".end", ""]
    return "\n".join(lines)


def _save_card(deck):
    got = [ln for ln in deck.splitlines() if ln.startswith(".save ")]
    return got[0] if got else ""


# ── the deck that MUST gain the retention ──────────────────────────────────
def test_a_transient_deck_retains_exactly_what_it_measures():
    before = _deck()
    after, rec = vr.apply(before)
    assert rec["applied"] is True, rec
    assert _save_card(after) == ".save v(bit_out) v(xdut.n1)"
    assert rec["vectors"] == ["v(bit_out)", "v(xdut.n1)"], rec


def test_a_branch_current_the_deck_measures_is_retained_too():
    """THE MUTATION THAT WOULD PASS QUIETLY: a retention of node voltages alone
    leaves a measured branch current unsaved, and ngspice answers `no such
    vector` for it on a run that still exits 0."""
    after, rec = vr.apply(_deck(control=("tran 5n 10n",
                                         "meas tran i1 avg i(v_vdd)")))
    assert rec["vectors"] == ["i(v_vdd)"], rec
    assert _save_card(after) == ".save i(v_vdd)"


def test_the_card_is_written_above_control_not_inside_it():
    """Inside `.control` a `save` is the INTERACTIVE command, which applies to
    the next analysis only — the mirror of the `.options` placement rule."""
    after, _ = vr.apply(_deck())
    assert after.index(".save ") < after.index(".control")


def test_prose_is_not_a_card():
    """THE MUTATION THAT WOULD PASS QUIETLY: the deck's own `* condition:`
    lines describe the measurement in the cards' vocabulary. A retention that
    read them would name a vector ngspice does not have, the `.save` would be
    refused, and the memory bound would be lost on a deck that still runs."""
    deck = _deck(extra=("* condition: the loop is graded on v(xdut.not_a_node)",
                        "* condition: and never on i(v_ghost)"))
    after, rec = vr.apply(deck)
    assert rec["vectors"] == ["v(bit_out)", "v(xdut.n1)"], rec
    assert "not_a_node" not in _save_card(after)
    assert "v_ghost" not in _save_card(after)


def test_each_vector_appears_once_however_many_cards_read_it():
    after, rec = vr.apply(_deck(control=(
        "tran 5n 10n",
        "meas tran a max v(xdut.n1)", "meas tran b min v(xdut.n1)",
        "meas tran c avg v(xdut.n1)")))
    assert rec["vectors"] == ["v(xdut.n1)"], rec
    assert _save_card(after).count("v(xdut.n1)") == 1


# ── the decks that MUST NOT be touched (the negative controls) ─────────────
def test_an_op_only_deck_is_returned_byte_for_byte():
    before = _deck(control=("op", "print v(vout)"))
    after, rec = vr.apply(before)
    assert after == before
    assert (rec["applied"], rec["refused"]) == (False, "no_transient"), rec


def test_a_deck_that_already_declares_its_own_retention_is_left_alone():
    """And the refusal is NOT `no_measured_vector`: the two are different
    answers and a reader must be able to tell them apart."""
    before = _deck(save=".save v(bit_out)")
    after, rec = vr.apply(before)
    assert after == before
    assert (rec["applied"], rec["refused"]) == (False, "already_retained"), rec


def test_a_deck_that_reads_no_vector_is_refused_rather_than_given_an_empty_save():
    """THE MUTATION THAT WOULD PASS QUIETLY: an empty `.save` retains NOTHING,
    and every later reference answers `no such vector` on a run that exits 0."""
    before = _deck(control=("tran 5n 10n", "echo done"))
    after, rec = vr.apply(before)
    assert after == before
    assert (rec["applied"], rec["refused"]) == (False,
                                                "no_measured_vector"), rec
    assert ".save" not in after


def test_applying_twice_cannot_stack_two_retentions():
    once, _ = vr.apply(_deck())
    twice, rec = vr.apply(once)
    assert twice == once
    assert rec["refused"] == "already_retained", rec
    assert once.count(".save ") == 1


# ── the deck the PRODUCER writes carries it (the wiring) ───────────────────
def _ir(control=("tran 5n 13824000n",
                 "meas tran vavg avg v(bit_out) from=261120n to=512000n")):
    return {
        "block": "conv",
        "ports": ["vdd", "vss", "vin", "clk", "bit_out"],
        "rails": {"vdd": "vdd", "vss": "vss"},
        "internal_nets": ["n1", "n2"],
        "testbench": {
            "supply_exprs": ["vdd"], "env_exprs": {}, "conditions": [],
            "stimulus": ["v_vdd vdd 0 {supply}",
                         "v_clk clk 0 pulse(0 {supply} 0n 1n 1n 499n 1000n)",
                         "v_in vin 0 0.7"],
            "cards": [], "control": list(control),
        },
    }


def _render(ir):
    return a3.render_testbench(
        ir, {"analog_device_params": {"nominal_supply_v": 1.2}},
        {"vdd": 1.2}, [])


def test_the_producer_emits_the_retention_end_to_end():
    """THE WIRING ARM. And it covers the rail cards the producer writes AFTER
    the control block: a retention derived before them would omit every node
    `transient_rail_measurement` just asked for."""
    text, env, notes = _render(_ir())
    assert env["vector_retention"]["applied"] is True, notes
    assert _save_card(text) == ".save v(bit_out) v(xdut.n1) v(xdut.n2)"
    assert any("vector_retention=3 vector(s) retained" in n for n in notes), \
        notes


def test_the_producers_op_only_deck_gets_no_retention_and_says_why():
    text, env, notes = _render(_ir(control=("op", "print v(bit_out)")))
    assert _save_card(text) == ""
    assert env["vector_retention"]["refused"] == "no_transient"
    assert any("not applied: no_transient" in n for n in notes), notes
