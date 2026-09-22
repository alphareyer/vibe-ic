"""A cap on PASSES is a budget; it is not a population and must not say it is.

MEASURED, red on live main 536d1bf11:

    test_emitter_population_pin_check.py::test_the_real_tree_has_no_undecidable_population
    the change moved a verdict on the shipped tree:
      [{"program": "phase3_one_shot_runner.py", "counter": "_wtpass",
        "increment_sites": 1, "denominator": 64,
        "denominator_kind": "comparison", "emitted_per_site": 2}]

and the gate was RIGHT. `emitter_population_pin_check` reads the script a
program EMITS and treats `$X >= <literal>` as the emitter stating that X's
population is that literal. The welltie coverage rung (#2483) emitted
`if {$_wtpass >= 64}`, so in the only vocabulary that gate has the deck claimed
64 members are emitted somewhere. They are not: `_wtpass` counts LOOP
ITERATIONS at run time, `incr _wtpass` sits at ONE written site, and
`_build_welltie_coverage_repair_tcl` is called twice — so K is a lower bound,
1 < 64 cannot be decided either way, and the shipped tree grew an undecidable
population.

THE GATE HAS NO DECLARATION REGISTER, and that is not an omission. Its
`not_determined` bucket is the honest "cannot decide", and
`test_the_real_tree_has_no_undecidable_population` asserts the shipped tree
holds none. There is nothing to declare `_wtpass` AS, because the gate only
ever compares counters that state a LITERAL denominator — "the only ones where
the emitter states the number itself". So the repair belongs in the RUNG: the
cap is compared against a NAME, the deck says what it means, and the gate has
nothing to mis-read. The cap's value is unchanged and still visible, now stated
once (`set _wtcap 64`) instead of spelled into a comparison and a message.
"""
import ast
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402
import emitter_population_pin_check as G  # noqa: E402


def _pdk():
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna")


def _emitted():
    return R._build_welltie_coverage_repair_tcl(_pdk(), 15.0, "test")


def test_the_budget_is_still_sixty_four_and_still_fires():
    """The repair may not change what the rung DOES. The cap is the same
    number, it is stated in the emitted script, and the loop still breaks on
    it."""
    assert R._WELLTIE_COVERAGE_PASS_CAP == 64
    tcl = _emitted()
    assert "set _wtcap 64" in tcl
    assert "if {$_wtpass >= $_wtcap} {" in tcl
    assert "WELLTIE_COVERAGE_REPAIR_PASS_CAP" in tcl
    # and the counter it bounds is still counted, once per pass
    assert tcl.count("incr _wtpass") == 1
    assert "set _wtpass 0" in tcl
    i_init = tcl.index("set _wtpass 0")
    assert i_init < tcl.index("set _wtcap 64") < tcl.index("incr _wtpass")


def test_the_emitted_script_states_no_population_for_the_pass_counter():
    """THE FIRST DIRECTION, asked of the gate's own extractor rather than of a
    string: `_wtpass` must carry NO denominator at all, so no comparison is
    attempted and nothing can be undecided."""
    src = Path(R.__file__).read_text()
    found, _denied = G.counters(src)
    by_name = {name: dens for name, _k, dens in found}
    # `counters` yields only counters that carry a denominator, so "states no
    # population" is spelled ABSENCE. That it is absence and not blindness is
    # what the mutation arm below proves: put the literal back and the same
    # call returns it.
    assert "_wtpass" not in by_name, by_name.get("_wtpass")
    assert "_prr_refused" in by_name, (
        "the extractor found no counter at all in this file — then this "
        "assertion is vacuous and proves nothing about `_wtpass`")
    # and the counter itself is still emitted, so there is something to bound
    assert "incr _wtpass" in _emitted()


def test_the_shipped_tree_holds_no_undecidable_population():
    """The gate's own verdict on the real tree, which is what went red."""
    src = Path(R.__file__).read_text()
    tree = ast.parse(src)
    found, _denied = G.counters_of(tree)
    multiplied = G.multiplied_counters(tree)
    undecidable = [
        (name, k, den, multiplied.get(name))
        for name, k, dens in found
        for _kind, den in dens
        if name in multiplied and k < den
    ]
    assert undecidable == [], undecidable


def test_restoring_the_bare_literal_makes_the_gate_undecidable_again():
    """THE OTHER DIRECTION, and the one that keeps this honest. Put the literal
    back into the comparison and the gate must report `_wtpass` undecidable
    again — with the same shape the handback quoted: 1 site, denominator 64,
    kind `comparison`, emitted twice per site."""
    src = Path(R.__file__).read_text()
    mutated = src.replace('"      if {$_wtpass >= $_wtcap} {\\n"',
                          '"      if {$_wtpass >= 64} {\\n"')
    assert mutated != src, "the comparison this test mutates is not there"
    tree = ast.parse(mutated)
    found, _denied = G.counters_of(tree)
    multiplied = G.multiplied_counters(tree)
    by_name = {name: (k, dens) for name, k, dens in found}
    k, dens = by_name["_wtpass"]
    assert ("comparison", 64) in dens, dens
    assert k == 1, k
    assert multiplied.get("_wtpass") == 2, multiplied.get("_wtpass")
    assert k < 64, "the undecidable case is k < denominator"


def test_the_named_cap_is_not_itself_a_new_population():
    """OVER-BREADTH CONTROL: `set _wtcap 64` must not become the next
    undecidable counter. It is never `incr`ed, so it is not a counter, and it
    states no `$X >= D` of its own."""
    src = Path(R.__file__).read_text()
    found, _denied = G.counters(src)
    assert "_wtcap" not in {name for name, _k, _d in found}


def test_the_message_states_the_budget_without_stating_a_population():
    """The PASS_CAP line says `$_wtpass of $_wtcap passes`. `of <name>` is not
    `of <digits>`, so it is not a population phrase — which is what let the
    message keep saying how big the budget was."""
    tcl = _emitted()
    assert "after $_wtpass of $_wtcap passes" in tcl
    assert not G.PHRASE.search("after $_wtpass of $_wtcap passes")
    # and the shape that WOULD have been a phrase, for contrast
    assert G.PHRASE.search("after $_wtpass of 64 passes")
