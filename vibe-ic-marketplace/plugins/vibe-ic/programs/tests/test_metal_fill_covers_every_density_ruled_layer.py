"""The fill config must cover every layer the DECK has a density rule for —
and say by name which it does not.

WHAT WAS MEASURED. On the run that motivated this, `metal_fill_config_gen`
emitted five metal entries because that is what the PDK's streamout layermap
and tech LEF between them produced. Nothing ever asked whether that set covers
the layers the DECK states a die-level coverage rule for. Against the deck the
same run signed off with, the answer is no: three of its eight die-level
density rules measure a layer this config emits no entry for — two naming the
stack's TOP metal and one naming poly. A generator that cannot raise a layer,
on a design whose sign-off rule demands it be raised, is a gap; a gap nobody
is told about is the same gap plus silence.

DERIVED, NEVER LISTED. The rule set is `die_level_deck_rule_attribution`'s
single derivation (the deck's own whole-die area identifier, and the `# Rule`
blocks that read it). The LAYER each rule measures comes from the block, in
the two shapes both open decks in the pinned image use — inline `X.area / die`
and a deck-scope `x_area = X.area` the block references. No rule name, no layer
name and no PDK appears in the logic or in this file's fixtures.

BOTH DIRECTIONS. A config that carries an entry for every density-ruled layer
refuses nothing; a config missing one is refused BY THE NAME of the rule and
of the identifier the rule measures. A deck with no die-level density rule at
all yields empty populations, not an empty refusal wearing the same face.
"""
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import metal_fill_config_gen as G  # noqa: E402
import die_level_deck_rule_attribution as D  # noqa: E402


INLINE_DECK = D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: layer_one coverage over the entire die
if (layer_one.area / chip_area) * 100 < 30
  extent.output('COV1.a', 'COV1.a')
end

# Rule COVX.a: layer_x coverage over the entire die
if (layer_x.area / chip_area) * 100 < 30
  extent.output('COVX.a', 'COVX.a')
end
"""

NAMED_DECK = D.FILE_SEP + """/deck/density.drc
CHIP = extent.sized(0.0)
chip_area = CHIP.area
l1_area = layer_one.area
l1_ratio = l1_area / chip_area

# Rule GLB.a: Min. global layer_one density.
if l1_ratio < 0.30
  output_global(chip_bbox, l1_area, chip_area, l1_ratio, :min)
end
"""

NO_DENSITY_DECK = D.FILE_SEP + """/deck/geom.rb
# Rule GEO.1: min width
layer_one.width(0.2.um).output('GEO.1', 'GEO.1')
"""


def _layers(*names):
    return [{"name": n, "layer": [10 + i, 0]} for i, n in enumerate(names)]


def test_the_layer_a_rule_measures_is_read_from_the_block_inline():
    got = G.density_rule_layer_identifiers(INLINE_DECK)
    assert got == {"COV1.a": ["layer_one"], "COVX.a": ["layer_x"]}


def test_the_layer_a_rule_measures_is_read_through_a_named_area():
    """The second deck shape: the ratio is built OUTSIDE the block and the
    block only references it. Reading the block alone finds no `X.area`."""
    got = G.density_rule_layer_identifiers(NAMED_DECK)
    assert got == {"GLB.a": ["layer_one"]}


def test_a_deck_with_no_die_level_density_rule_yields_no_population():
    assert G.density_rule_layer_identifiers(NO_DENSITY_DECK) == {}
    cov, unc = G.density_rule_coverage(NO_DENSITY_DECK, _layers("layer_one"))
    assert cov == {} and unc == {}
    assert G.density_coverage_refusal(unc) is None


def test_a_complete_config_refuses_nothing():
    cov, unc = G.density_rule_coverage(INLINE_DECK,
                                       _layers("layer_one", "layer_x"))
    assert sorted(cov) == ["COV1.a", "COVX.a"]
    assert unc == {}
    assert G.density_coverage_refusal(unc) is None


def test_a_density_ruled_layer_with_no_fill_entry_is_refused_by_name():
    cov, unc = G.density_rule_coverage(INLINE_DECK, _layers("layer_one"))
    assert sorted(cov) == ["COV1.a"]
    assert unc == {"COVX.a": ["layer_x"]}
    why = G.density_coverage_refusal(unc)
    assert why
    assert "COVX.a" in why and "layer_x" in why
    # the rule it CAN serve is not named in the refusal
    assert "COV1.a" not in why
    # and the refusal says what it does not know rather than asserting a gap
    assert "alias the deck resolves at run time" in why


def test_the_config_carries_both_populations_always():
    """Emitted even when empty, so `this deck states none` and `this config was
    built before the concept existed` stay distinguishable."""
    layermap = "Metal1 drawing 34 0\nMetal2 drawing 36 0\n"
    techlef = ("MANUFACTURINGGRID 0.005 ;\n"
               "LAYER Metal1\n TYPE ROUTING ;\n WIDTH 0.23 ;\n SPACING 0.23 ;\n"
               "END Metal1\n"
               "LAYER Metal2\n TYPE ROUTING ;\n WIDTH 0.28 ;\n SPACING 0.28 ;\n"
               "END Metal2\n")
    deck = D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: metal1 coverage over the entire die
if (metal1.area / chip_area) * 100 < 30
  extent.output('COV1.a', 'COV1.a')
end

# Rule COVT.a: top_metal coverage over the entire die
if (top_metal.area / chip_area) * 100 < 30
  extent.output('COVT.a', 'COVT.a')
end
"""
    cfg = G.build_metal_fill_config(layermap, techlef, deck,
                                    metal_prefix="Metal")
    der = cfg["_derivation"]
    assert "density_rules_covered" in der
    assert "density_rules_without_fill_entry" in der
    assert der["density_rules_covered"] == {"COV1.a": ["metal1"]}
    assert der["density_rules_without_fill_entry"] == {"COVT.a": ["top_metal"]}
    # metal2 has a fill entry and no density rule — that is not a refusal
    assert "metal2" not in G.density_coverage_refusal(
        der["density_rules_without_fill_entry"])


def test_the_cli_refuses_with_its_own_exit_code(tmp_path, capsys):
    """rc 3, not rc 2. rc 2 already means `no routing metal derivable` — a
    config that could not be BUILT — and folding the two would make a caller
    unable to tell that from a config that is usable but does not cover every
    density rule."""
    lm = tmp_path / "lm.map"
    lm.write_text("Metal1 drawing 34 0\n")
    tl = tmp_path / "t.tlef"
    tl.write_text("MANUFACTURINGGRID 0.005 ;\nLAYER Metal1\n TYPE ROUTING ;\n"
                  " WIDTH 0.23 ;\n SPACING 0.23 ;\nEND Metal1\n")
    dk = tmp_path / "d.rb"
    dk.write_text(D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: metal1 coverage over the entire die
if (metal1.area / chip_area) * 100 < 30
  extent.output('COV1.a', 'COV1.a')
end

# Rule COVT.a: top_metal coverage over the entire die
if (top_metal.area / chip_area) * 100 < 30
  extent.output('COVT.a', 'COVT.a')
end
""")
    rc = G.main(["--layermap", str(lm), "--techlef", str(tl),
                 "--deck", str(dk), "--metal-prefix", "Metal"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "REFUSED" in err and "COVT.a" in err and "top_metal" in err

    # the same inputs minus the uncoverable rule: rc 0, nothing refused
    dk.write_text(D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: metal1 coverage over the entire die
if (metal1.area / chip_area) * 100 < 30
  extent.output('COV1.a', 'COV1.a')
end
""")
    assert G.main(["--layermap", str(lm), "--techlef", str(tl),
                   "--deck", str(dk), "--metal-prefix", "Metal"]) == 0
    assert "REFUSED" not in capsys.readouterr().err
