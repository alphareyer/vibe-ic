"""The A3 testbench measured the block's OUTPUT and nothing else, so a node
that left the supply rails DURING the transient was visible only by opening
the waveform by hand (vibe-ic#2077).

MEASURED (lane czdsm3, 2026-09-07, then re-measured by lane czrailmeas on
8HD-4, image `vibeic-eda` 0.3.48
`sha256:8c5694abdf5c269c1d9def5368704e0c4b51c869d1d9c9380e123e07657fe9eb`,
ngspice-47). A second-order delta-sigma modulator powered between 0 and 1.2 V
put +3.34 V and +2.99 V on two of its own internal nodes, and:

  * the OPERATING POINT was clean on every one of them in the same run --
    `nn1` and `nn2` sit at 6.8e-07 V and at 0.036 / 1.055 V -- so
    `dc_op_rail_excursions`, which reads the table `ngspice -b` prints above
    the analysis, could not have seen it. A DC invariant reads a DC solution;
    this defect lives in the transient.
  * the excursion reproduces to three figures by deleting the `.options
    trtol=1` card v1.18.36 landed, i.e. it is what the shipped integration
    control is holding off, and nothing in the flow said so.

WHAT THIS FILE HOLDS, AND WHY IN THIS SHAPE
===========================================
The fix is a `.meas` card per internal node in the emitted testbench plus a
consumer that refuses an excursion and NAMES the node. That is two halves that
can rot apart -- an emitter whose cards nobody reads, or a reader matching a
prefix nothing writes -- so the guards below are written across the seam:

  * POPULATION, NOT A SPECIMEN. Every circuit class in the A2 topology library
    that declares a testbench is driven, and the cards are compared against
    that class's OWN `internal_nets` by MEMBERSHIP. A class added later
    without cards is a red, and a card list that quietly shrank is a red.
  * THE DECKS DIFFER, AND THEY SAY SO. czdsm3 chose byte-identity of the
    emitted decks over adding cards, which is why this landed as an open issue
    rather than as part of that change. Every deck now carries a
    `transient_rail_measurement=` provenance line -- INCLUDING the `op`-only
    ones, whose line says why they carry no cards -- so the difference is
    stated rather than diffed out of the artefact.
  * ASKED AGAINST ANSWERED. A `meas` ngspice refuses prints an error and the
    run still exits 0. The reader is therefore handed the nodes the deck
    ASKED for and reports the asked-minus-answered set, so "could not measure
    it" can never be read as "measured it and it was inside the rails".
  * THE CLAIM ABOUT THE GRAMMAR IS RE-MEASURED, not asserted -- see the
    falsifier at the bottom, which re-derives the whole denial vocabulary of
    `_prose_polarity` on every run.

No chip, PDK SKU, vendor or part number appears here. One OPEN PDK family name
appears as a `--pdk` REQUEST STRING, for the reason the integration-control
file states: the switched-capacitor class refuses a family that carries no
measured process constant, and without it this file's population silently
shrinks to the classes that do not need one.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import analog_a2_topology_emit as A2LIB          # noqa: E402
import analog_a3_netlist_emit as A3              # noqa: E402
from _analog_producer_fixture import (           # noqa: E402
    A1, A2, A3 as A3PROG, block, make_project, run_prog)

#: The same union of sizing rows the integration-control file binds, for the
#: same reason: one synthetic block per class is then admissible and the
#: population below is the whole library rather than the class that happened
#: to be measured.
SPECS = [
    {"name": "Vout", "target": 1.8, "unit": "V"},
    {"name": "Vin", "target": 3.0, "unit": "V"},
    {"name": "Vref", "target": 0.9, "unit": "V"},
    {"name": "Reff", "target": 50000.0, "unit": "ohm"},
    {"name": "ENOB", "target": 14.0, "unit": "bit"},
    {"name": "fclk", "target": 1.0, "min": 0.1, "max": 1.2, "unit": "MHz"},
    {"name": "OSR", "target": 256.0, "unit": "-"},
    {"name": "Order", "target": 2.0, "unit": "-"},
    {"name": "Vdd (core)", "target": 1.2, "unit": "V"},
    {"name": "Vin (diff)", "target": 1.0, "unit": "V"},
]
PDK = "ihp-sg13g2"

#: What the deck asks the simulator, as this producer writes it. Stated HERE
#: and not imported from the producer, so that renaming the producer's own
#: prefix cannot silently move what this file is checking for.
CARD_RE = re.compile(
    r"^\s*meas\s+tran\s+railx_(max|min)_(\S+)\s+(max|min)\s+v\(\s*"
    r"([^)\s]+)\s*\)\s*$")
PROV_KEY = "transient_rail_measurement="


def _classes_with_a_testbench():
    return sorted(k for k, v in A2LIB.LIBRARY.items()
                  if isinstance(v.get("testbench"), dict))


def _emit(tmp_path):
    """Drive A1 -> A2 -> A3 for one synthetic block of every class that
    declares a testbench. No container: `--verify-sim` is NOT passed, so every
    assertion about what was WRITTEN stands whether or not a simulator was
    reachable on the host that ran this."""
    classes = _classes_with_a_testbench()
    assert classes, "the topology library declares no testbench at all"
    blocks = [block(f"blk_{i}", btype, specs=SPECS)
              for i, btype in enumerate(classes)]
    project = make_project(tmp_path, blocks)
    run_prog(A1, project)
    run_prog(A2, project, "--pdk", PDK)
    run_prog(A3PROG, project, "--pdk", PDK)
    decks, netlists, irs, why = {}, {}, {}, {}
    for i, btype in enumerate(classes):
        d = project / "phase3/analog" / f"blk_{i}"
        tb = d / f"tb_blk_{i}.sp"
        if tb.exists():
            decks[btype] = tb.read_text(encoding="utf-8")
            netlists[btype] = (d / f"blk_{i}.sp").read_text(encoding="utf-8")
            irs[btype] = json.loads(
                (d / "topology.json").read_text(encoding="utf-8"))
            continue
        for gap in ("topology_gap.json", "netlist_gap.json"):
            g = d / gap
            if g.exists():
                why[btype] = json.loads(
                    g.read_text(encoding="utf-8")).get("status")
                break
        else:
            why[btype] = "NOTHING WRITTEN"
    return classes, decks, netlists, irs, why


def _cards(text):
    """`{node: {"max", "min"}}` for the rail cards of one deck, and the DUT
    instance each one names."""
    got, insts = {}, set()
    for raw in text.splitlines():
        m = CARD_RE.match(raw)
        if not m:
            continue
        assert m.group(1) == m.group(3), (
            f"the card name says `{m.group(1)}` and the function it calls is "
            f"`{m.group(3)}`: {raw.strip()}")
        node = m.group(4)
        inst, _, net = node.rpartition(".")
        insts.add(inst)
        got.setdefault(net, set()).add(m.group(1))
        assert net == m.group(2), (
            f"the card is named for `{m.group(2)}` and measures `{net}`, so "
            f"the row the reader gets back names the wrong node: {raw.strip()}")
    return got, insts


def _runs_a_transient(btype):
    ctl = (A2LIB.LIBRARY[btype].get("testbench") or {}).get("control") or []
    return any(isinstance(l, str) and l.strip().split()[:1] == ["tran"]
               for l in ctl)


def _internal_nets(ir):
    rails = {v for v in (ir.get("rails") or {}).values()
             if isinstance(v, str)}
    return {n for n in (ir.get("internal_nets") or [])
            if isinstance(n, str) and n and n not in rails}


# ═══ THE POPULATION GUARD ══════════════════════════════════════════════════
def test_every_class_that_declares_a_testbench_wrote_one(tmp_path):
    """Deliberately separate from every value guard below. Without it a
    fixture that admits no block at all would make each of them pass over an
    empty set."""
    classes, decks, _n, _i, why = _emit(tmp_path)
    assert set(decks) == set(classes), (
        f"classes declaring a testbench: {classes}; decks written: "
        f"{sorted(decks)}; refusal recorded for the rest: {why}")


def test_the_library_still_has_classes_on_BOTH_sides_of_the_transient_split(
        tmp_path):
    """THE CONTROL FOR THE TWO GUARDS BELOW. One of them grades the classes
    that run a transient and the other grades the classes that do not; if the
    library ever held only one kind, one of those would be reporting on an
    empty set and would say PASS for it."""
    classes, _d, _n, _i, _w = _emit(tmp_path)
    tran = [c for c in classes if _runs_a_transient(c)]
    op_only = [c for c in classes if not _runs_a_transient(c)]
    assert tran and op_only, (
        f"transient classes {tran}, op-only classes {op_only} — this file "
        f"needs both to be non-empty to mean anything")


# ═══ THE EMITTED CARDS ═════════════════════════════════════════════════════
def test_a_transient_deck_measures_every_one_of_its_internal_nodes(tmp_path):
    """MEMBERSHIP, not a count, and against the block's OWN IR: the node that
    left the rails on the block this was written for is not one anybody would
    have put on a list of interesting nodes."""
    classes, decks, _n, irs, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    bad = {}
    for btype, text in sorted(decks.items()):
        if not _runs_a_transient(btype):
            continue
        want = _internal_nets(irs[btype])
        got, _insts = _cards(text)
        if set(got) != want or any(v != {"max", "min"} for v in got.values()):
            bad[btype] = {"missing": sorted(want - set(got)),
                          "extra": sorted(set(got) - want),
                          "incomplete": sorted(
                              n for n, v in got.items()
                              if v != {"max", "min"})}
    assert not bad, (
        "these transient decks do not measure the max AND min of every "
        f"internal node the IR declares: {bad}")


def test_the_cards_name_the_instance_the_deck_actually_creates(tmp_path):
    """A card that names a device the deck does not create is answered by
    ngspice with `no such vector` on a run that still exits 0 — i.e. it
    measures nothing and says PASS. The instance is compared against the one
    the deck's own `X` line creates, not against a literal."""
    classes, decks, _n, _i, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    for btype, text in sorted(decks.items()):
        got, insts = _cards(text)
        if not got:
            continue
        created = {ln.split()[0] for ln in text.splitlines()
                   if ln[:1].lower() == "x"}
        assert insts and insts <= created, (
            f"{btype}: the rail cards measure inside {sorted(insts)} and the "
            f"deck creates {sorted(created)}")


def test_the_cards_are_inside_the_control_block_and_after_the_transient(
        tmp_path):
    """THE DIRECTION A CARELESS MOVE BREAKS, and it is the mirror image of the
    `.options` placement rule a few lines above them in the same deck: a
    `.options` card must be ABOVE `.control` to change the analysis, and a
    `meas` card must be BELOW the `tran` that produced the vector it reads.
    The same text on the wrong side of either line parses and measures
    nothing."""
    classes, decks, _n, _i, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    graded = 0
    for btype, text in sorted(decks.items()):
        lines = [l.strip() for l in text.splitlines()]
        idx = [i for i, l in enumerate(lines) if CARD_RE.match(l)]
        if not idx:
            continue
        graded += 1
        ctl = lines.index(".control")
        endc = lines.index(".endc")
        tran = min(i for i, l in enumerate(lines)
                   if l.split()[:1] == ["tran"])
        assert ctl < tran < min(idx) and max(idx) < endc, (
            f"{btype}: .control at {ctl}, tran at {tran}, cards at "
            f"{min(idx)}..{max(idx)}, .endc at {endc}")
    assert graded, "no deck carried a rail card, so nothing was graded"


def test_an_op_only_deck_carries_no_card_and_says_why(tmp_path):
    """`meas tran` on a deck that runs no transient has no vector to read.
    Emitting 2N cards that all fail would be 2N error lines per deck and a
    reader with no way to tell that from a node that really could not be
    measured. The absence is NAMED instead."""
    classes, decks, netlists, _i, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    graded = 0
    for btype, text in sorted(decks.items()):
        if _runs_a_transient(btype):
            continue
        graded += 1
        got, _insts = _cards(text)
        assert not got, (
            f"{btype} runs no transient and carries {len(got)} rail card(s)")
        line = [l for l in text.splitlines() if PROV_KEY in l]
        assert line and "none" in line[0].split(PROV_KEY, 1)[1][:8], (
            f"{btype} carries no card and does not say why: {line}")
        assert "no transient" in line[0], line
    assert graded, "no op-only deck was graded"


# ═══ THE DECKS DIFFER, AND THEY SAY SO ═════════════════════════════════════
def test_every_netlist_and_every_deck_states_the_measurement_in_provenance(
        tmp_path):
    """The one change that makes an emitted deck differ from the byte-
    identical decks that preceded it. Stated in the artefact, in BOTH files,
    UNCONDITIONALLY — a provenance line that appears only when there is
    something to boast about says nothing when it appears."""
    classes, decks, netlists, _i, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    missing = sorted(b for b in decks
                     if PROV_KEY not in decks[b]
                     or PROV_KEY not in netlists[b])
    assert not missing, (
        f"these blocks do not state their transient rail measurement in the "
        f"provenance of both the netlist and the testbench: {missing}")


def test_the_provenance_line_counts_the_nodes_the_deck_actually_measures(
        tmp_path):
    """The sentence and the cards come from ONE function so they cannot
    disagree; this is the guard that says so from outside it."""
    classes, decks, _n, _i, why = _emit(tmp_path)
    assert decks, f"no deck to grade; refusals: {why}"
    for btype, text in sorted(decks.items()):
        got, _insts = _cards(text)
        line = [l for l in text.splitlines() if PROV_KEY in l]
        assert len(line) == 1, (btype, line)
        stated = line[0].split(PROV_KEY, 1)[1].strip()
        if got:
            assert stated.split()[0] == str(len(got)), (btype, stated,
                                                        len(got))
        else:
            assert stated.startswith("none"), (btype, stated)


# ═══ THE CONSUMER ══════════════════════════════════════════════════════════
_SUPPLY = 1.2
#: One answered row, in both spellings ngspice-47 was MEASURED to print in the
#: pinned image: a short name is padded to a column and the `=` is its own
#: token; a long name runs straight into the `=`.
_ROW_PADDED = "railx_{k}_{n}            =  {v} at=  1.51850e-07"
_ROW_TIGHT = "railx_{k}_{n}={v} at=  1.51850e-07"


def _reader():
    fn = getattr(A3, "tran_rail_report", None)
    assert fn is not None, (
        "`analog_a3_netlist_emit.tran_rail_report` does not exist: the "
        "emitted cards are read by nobody, which is vibe-ic#2077")
    return fn


def _log(rows):
    return "\n".join(rows) + "\n"


def test_the_meas_row_reader_reads_the_grammar_at_all():
    """THE CONTROL FOR EVERY CONSUMER TEST BELOW. If this row did not come
    back, "no excursion was reported" would be a statement about a regex that
    matches nothing."""
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nn1", v="1.05e+00"),
                          _ROW_TIGHT.format(k="min", n="nn1", v="1.00e-02")]),
                    _SUPPLY, ["nn1"])
    assert got["invariant"] == "CHECKED"
    assert got["nodes_measured"] == ["nn1"]
    assert got["nodes_not_measured"] == []
    assert got["excursions"] == []


def test_a_node_the_transient_took_outside_the_rails_is_named():
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nn1", v="3.33717e+00"),
                          _ROW_PADDED.format(k="min", n="nn1", v="0.0e+00"),
                          _ROW_PADDED.format(k="max", n="nn2", v="2.99e+00"),
                          _ROW_PADDED.format(k="min", n="nn2", v="0.0e+00")]),
                    _SUPPLY, ["nn1", "nn2"])
    assert got["invariant"] == "CHECKED"
    assert [n for n, _ in got["excursions"]] == ["nn1", "nn2"], got
    assert got["excursions"][0][1] == pytest.approx(3.33717)


def test_a_node_below_the_lower_rail_is_caught_too():
    """The invariant is two-sided. A node driven a full supply BELOW ground is
    as unheld as one a full supply above the top rail, and a check that only
    looked up would pass every one of them."""
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nn1", v="1.0e+00"),
                          _ROW_PADDED.format(k="min", n="nn1",
                                             v="-2.5e+00")]),
                    _SUPPLY, ["nn1"])
    assert [n for n, _ in got["excursions"]] == ["nn1"]
    assert got["excursions"][0][1] == pytest.approx(-2.5)


def test_a_designed_excursion_inside_the_margin_is_not_refused():
    """THE OTHER DIRECTION, and the reason the margin is a full supply rather
    than the DC quarter: a bootstrapped or clock-coupled node is DESIGNED to
    reach about twice its own rail for part of a cycle, and a rule that
    refuses those refuses working circuits."""
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nboot", v="2.30e+00"),
                          _ROW_PADDED.format(k="min", n="nboot",
                                             v="-1.10e+00")]),
                    _SUPPLY, ["nboot"])
    assert got["excursions"] == [], got


def test_a_card_the_simulator_refused_is_NOT_a_node_that_came_back_clean():
    """`meas ... failed!` prints an error and the run still exits 0. Asked
    against answered, so the absence is reported as an absence."""
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nn1", v="1.0e+00"),
                          _ROW_PADDED.format(k="min", n="nn1", v="0.0e+00"),
                          "Error: measure  railx_max_nn2  max(TRIG) : no such "
                          "vector as 'v(xdut.nn2)'",
                          " meas tran railx_max_nn2 max v(xdut.nn2) failed!"]),
                    _SUPPLY, ["nn1", "nn2"])
    assert got["invariant"] == "CHECKED"
    assert got["nodes_measured"] == ["nn1"]
    assert got["nodes_not_measured"] == ["nn2"], got


def test_a_deck_with_no_cards_is_NOT_a_transient_that_was_checked():
    got = _reader()(_log(["Note: Simulation executed from .control section"]),
                    _SUPPLY, [])
    assert got["invariant"] == "NOT_MEASURED_NO_CARDS"
    assert got["excursions"] == []


def test_an_unknown_supply_is_NOT_a_transient_that_was_checked():
    got = _reader()(_log([_ROW_PADDED.format(k="max", n="nn1",
                                             v="3.33717e+00")]),
                    None, ["nn1"])
    assert got["invariant"] == "NOT_MEASURED_NO_SUPPLY"
    assert got["excursions"] == []
    assert got["nodes_not_measured"] == ["nn1"]


def test_the_new_rows_are_invisible_to_the_dc_op_reader():
    """THE SEAM BETWEEN THE TWO INVARIANTS. `dc_op_rail_excursions` matches a
    `<name> <value>` row anchored at end of line and keeps only names carrying
    an instance prefix. A `meas` result row is `<name>=  <value> at=  <time>`
    and its name has no dot, so it cannot enter that reader — but "cannot" is
    an argument, and this measures it. MEASURED on the real 380 KB ngspice log
    of the refused arm as well: 0 of its 214 rail rows match `_OP_ROW_RE`, and
    `dc_op_rail_excursions` returns [] on it while the transient reader
    returns two nodes."""
    log = _log([
        "Initial Transient Solution",
        "--------------------------",
        "Node                                   Voltage",
        "----                                   -------",
        "xdut.nn1                                 0.036",
        "v_vdd#branch                       -0.000964874",
        "",
        _ROW_PADDED.format(k="max", n="nn1", v="3.33717e+00"),
        _ROW_TIGHT.format(k="min", n="nn1", v="0.00000e+00"),
    ])
    assert A3.dc_op_rail_excursions(log, _SUPPLY) == [], (
        "a transient measurement row reached the operating-point reader")
    assert [n for n, _ in _reader()(log, _SUPPLY, ["nn1"])["excursions"]] \
        == ["nn1"]


def test_the_new_rows_do_not_join_the_block_own_measurement_list():
    """The producer collects the block's OWN measurements with `"MEAS" in ln`
    — the uppercase token its `echo` line carries. A rail row must not join
    that list, or a deck that measured nothing about its function would look
    like one that measured 214 things."""
    assert 'if "MEAS" in ln' in inspect.getsource(A3.verify_with_ngspice), (
        "the producer no longer selects the block's measurements the way this "
        "test assumes; re-derive the predicate before trusting the rows below")
    for row in (_ROW_PADDED.format(k="max", n="nn1", v="1.0e+00"),
                _ROW_TIGHT.format(k="min", n="nn1", v="0.0e+00"),
                "meas tran railx_max_nn1 max v(xdut.nn1)"):
        assert "MEAS" not in row, row


def test_the_requested_population_is_read_off_the_emitted_deck():
    fn = getattr(A3, "rail_meas_nodes_requested", None)
    assert fn is not None, "the reader has no way to know what was asked"
    deck = ("* condition: the input is held at 0.69 V, which must be 0.6\n"
            ".control\n"
            "tran 5n 100n\n"
            "meas tran railx_max_nn1 max v(xdut.nn1)\n"
            "meas tran railx_min_nn1 min v(xdut.nn1)\n"
            "meas tran railx_max_nn2 max v(xdut.nn2)\n"
            ".endc\n")
    assert fn(deck) == ["nn1", "nn2"]
    assert fn("") == []


# ═══ THE SEAM: THE PRODUCER ACTUALLY CALLS ITS OWN READER ══════════════════
def _verify_fn_source():
    return inspect.getsource(A3.verify_with_ngspice)


def test_the_producer_grades_the_transient_of_every_run_it_makes():
    """AT THE CALL SITE, over the AST. The reader is handed the simulator's
    combined output, the supply the deck drives, and the nodes the DECK asked
    for — a reader given a population it derived itself could not report an
    asked-minus-answered set."""
    tree = ast.parse(_verify_fn_source())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and ast.unparse(n.func).endswith("tran_rail_report")]
    assert len(calls) == 1, (
        "`verify_with_ngspice` does not grade the transient exactly once: "
        + ast.unparse(tree)[:400])
    args = [ast.unparse(a) for a in calls[0].args]
    assert args[:2] == ["out", "supply_v"], args
    assert args[2:] == ["rail_meas_nodes_requested(tb_text)"], args


def test_a_transient_excursion_is_a_refusal_and_not_an_emitted_netlist():
    """The same tier as a non-convergence and as a DC-op excursion: a run that
    took the block's own nodes outside its own rails has not verified the
    netlist, and every measurement the same log reports was taken across that
    excursion. Graded over the AST of `emit_for_block`, so the refusal cannot
    be a docstring."""
    src = inspect.getsource(A3.emit_for_block)
    assert "TRAN_NODE_OUTSIDE_RAIL" in src, (
        "`emit_for_block` never reads the transient verdict, so a deck whose "
        "transient left the rails is still emitted")
    tree = ast.parse(src.lstrip())
    guarded = [n for n in ast.walk(tree)
               if isinstance(n, ast.If)
               and "TRAN_NODE_OUTSIDE_RAIL" in ast.unparse(n.test)]
    assert len(guarded) == 1, ast.unparse(tree)[:400]
    body = ast.unparse(guarded[0])
    assert "write_gap" in body and "NETLIST_TRAN_OUTSIDE_RAILS" in body, body
    assert "emitted=False" in body, body


def test_the_two_rail_invariants_stay_two_answers():
    """The DC table and the transient answer DIFFERENT questions — the block
    this was written for was clean on one and out by volts on the other — so
    every return of the producer's verifier carries both records, and neither
    may be inferred from the other."""
    src = _verify_fn_source()
    assert src.count("tran_fields") >= 4, (
        "not every return of `verify_with_ngspice` carries the transient "
        "record, so some verdicts cannot say whether the transient was even "
        "looked at")
    for key in ("tran_rail_invariant", "tran_rail_excursions",
                "tran_rail_nodes_not_measured"):
        assert key in src, key


# ═══ THE `_NOT_PROSE` CLAIM, RE-MEASURED ═══════════════════════════════════
#: Every position a denial token can reach in the two productions this reader
#: parses: around the name, around the `=`, around the value, indented,
#: label-prefixed, AS the whole node name, and INSIDE the node name.
_DENIAL_POSITIONS = (
    "{t} railx_max_nn1            =  {v} at=  1.5e-07",
    "railx_max_nn1 {t}            =  {v} at=  1.5e-07",
    "railx_max_nn1            = {t} {v} at=  1.5e-07",
    "railx_max_nn1            =  {v} {t} at=  1.5e-07",
    "railx_max_nn1={t}{v} at=  1.5e-07",
    "railx_max_nn1={v} {t} at=  1.5e-07",
    "  {t}  railx_max_nn1         =  {v} at=  1.5e-07",
    "{t}: railx_max_nn1           =  {v} at=  1.5e-07",
    "railx_max_{t}                =  {v} at=  1.5e-07",
    "{t}                          =  {v} at=  1.5e-07",
)
_EXCURSION_V = "3.33717e+00"


def _denial_vocabulary():
    """`_prose_polarity`'s OWN denial words, read out of its own patterns.

    Read rather than re-typed: a hand-copied list would agree with the module
    by coincidence and would stop tracking it the first time a word is added.
    """
    import _prose_polarity as PP
    raw = PP._DENIAL_CORE + "|" + PP._DENIAL_RETIRED
    out = set()
    for m in re.findall(r"([A-Za-z][A-Za-z' -]{2,})", raw):
        m = m.strip()
        out.add(m[1:] if m.startswith("b") and len(m) > 3 else m)
    return sorted(w for w in out if len(w) >= 2 and not w.startswith("b"))


def test_the_not_prose_claim_for_the_meas_row_reader_is_falsifiable():
    """THE ARGUMENT BEHIND THE `_NOT_PROSE` ENTRY, RE-MEASURED HERE.

    THE PROPERTY: the grammar's only alternative to a value is SILENCE. A
    denial can stop the row from parsing — and the node is then reported as
    NOT MEASURED, which is the whole point of the asked-against-answered split
    — but no denial can make the reader publish a DIFFERENT voltage. That is
    what makes a polarity consult here a branch that can never fire.

    TWO OUTCOMES ARE NOT SILENCE, and both are counted separately rather than
    hidden, because a claim that quietly reclassifies its own counter-examples
    is not falsifiable:

      * the token appearing INSIDE the measurement name. The voltage is
        carried through unchanged and a node so named really is a node so
        named.
      * the token appearing AFTER the value. This row is deliberately NOT
        anchored at the end of the line — ngspice-47 appends `at= <time>` to
        every `max`/`min` row it prints, MEASURED in the pinned image, and a
        reader that demanded end-of-line would match none of them. So trailing
        text is ignored, the value stands, and the node stands. That is the
        claim HOLDING, not an escape from it: the grammar has no trailing form
        that retracts a measurement, and there is nothing for a polarity
        consult to do about one.

    Which shapes fall into that second bucket is DERIVED from the shapes
    themselves, never counted by hand.

    THE CONTRAST is the other half: the IDENTICAL strings read as PROSE are
    full of denials. The vocabulary is not inert; this grammar is."""
    import _prose_polarity as PP
    fn = _reader()
    tokens = _denial_vocabulary()
    assert len(tokens) >= 10, "the vocabulary was not read"

    inverted = refused = renamed = unaffected = trials = 0
    for tok in tokens:
        for shape in _DENIAL_POSITIONS:
            trials += 1
            row = shape.format(t=tok, v=_EXCURSION_V)
            got = fn(_log([row]), _SUPPLY, ["nn1", tok.lower()])
            exc = got["excursions"]
            if not exc:
                refused += 1
            elif {round(v, 5) for _, v in exc} != {3.33717}:
                inverted += 1
            elif {n for n, _ in exc} != {"nn1"}:
                renamed += 1
            else:
                unaffected += 1

    assert trials >= 100
    assert inverted == 0, (
        "a denial token changed the voltage this grammar publishes — the "
        "_NOT_PROSE entry for analog_a3_netlist_emit::tran_rail_report is "
        "false; delete the entry rather than this assertion")
    assert refused > 0, ("no denial could even disturb the parse; the "
                         "falsifier is not exercising the grammar")
    # DERIVED, never hard-coded counts: the tokens that are legal inside a
    # measurement name at all, and the shapes that put the token after the
    # value the reader has already taken.
    nameable = [t for t in tokens
                if A3._RAIL_MEAS_ROW_RE.match(
                    f"railx_max_{t}                =  {_EXCURSION_V}")]
    trailing = [s for s in _DENIAL_POSITIONS
                if s.index("{t}") > s.index("{v}")]
    assert renamed == len(nameable) > 0, (
        "a token inside the measurement name must rename the node and change "
        f"nothing else, one per nameable token; got {renamed} of "
        f"{len(nameable)}")
    assert unaffected == len(tokens) * len(trailing) > 0, (
        "the rows a denial leaves untouched must be exactly the ones that put "
        f"it after the value; got {unaffected}, shapes {len(trailing)}")
    assert refused == trials - renamed - unaffected

    prose = sum(1 for t in tokens for s in _DENIAL_POSITIONS
                if PP.is_denied(s.format(t=t, v=_EXCURSION_V)))
    assert prose > 0, ("the vocabulary found no denial in ANY of these "
                       "strings, so this test proves nothing about the "
                       "grammar — it would pass against an empty vocabulary")


def test_the_deck_reader_cannot_match_a_line_of_prose():
    """The second input's half of the same claim. Every natural-language line
    an A3 deck carries begins with the SPICE comment character, and the card
    reader anchors its keyword at the start of the line."""
    fn = getattr(A3, "rail_meas_nodes_requested", None)
    assert fn is not None
    for tok in _denial_vocabulary():
        commented = (f"* condition: {tok} meas tran railx_max_nn1 max "
                     f"v(xdut.nn1)\n")
        assert fn(commented) == [], (tok, commented)
