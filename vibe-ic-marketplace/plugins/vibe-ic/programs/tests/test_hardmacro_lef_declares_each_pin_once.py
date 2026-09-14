"""A delivered hardmacro LEF must declare each pin ONCE, and a normaliser that
merges pins must be unable to merge two pins that are not the same pin.

MEASURED 2026-09-15 (lane icspm) on a signed-off gf180mcuD hardmacro — the
artefact the whole HARDMACRO route exists to produce::

    phase3/stage4/hardmacro/spm.lef
      PIN lines      : 59
      distinct names : 38     (36 logical + VDD + VSS -- the correct count)
      MACRO blocks   : 1      (so the duplicates are INSIDE one macro)
      declared twice : 21     clk p rst y VSS and 16 x bits

and the two declarations are not copies — the second carries GEOMETRY ONLY::

    PIN clk                        PIN clk
      DIRECTION INPUT ;              PORT
      USE SIGNAL ;                     LAYER Metal3 ; RECT 2375.480 …
      ANTENNAGATEAREA 4.738000 ;     END
      PORT                           PORT
        LAYER Metal3 ; RECT …          LAYER Metal2 ; RECT 1366.820 …
      END                            END
    END clk                        END clk

WHERE IT COMES FROM, measured by running magic three ways on the same signed-off
inputs::

    input               PIN blocks  distinct  duplicated  no DIRECTION  OBS rects
    DEF only                36         36          0            0           49
    GDS only                38         38          0           38           94
    GDS + DEF (shipped)     58         38         20           38           94

Neither input can be dropped: GDS-only gives 38 pins of which ALL 38 have no
DIRECTION, and DEF-only loses 45 of the 94 OBS rects, which would let a parent
route straight over blocked area. (`gds labels no` and reading the DEF first
were both measured too: no effect on the duplication.) So the duplication is
repaired rather than avoided by shipping a worse abstract.

WHAT IT COST, end to end and measured::

    release_docs_check --arm ip   ->  [FAIL]
      PIN_COUNT_DISAGREES_WITH_NETLIST (spm): IP_DATASHEET.md states
      'Signal pins' = 56, derived from spm.lef; the netlist view spm.v declares
      36 logical pin bit(s).

    (59 PIN entries - 1 VDD - 2 VSS = 56, which is the number the datasheet
    states.)  After the merge, regenerating the documents from the normalised
    LEF gives 'Signal pins | 36 | phase3/stage4/hardmacro/spm.lef' and
    release_docs_check -> [PASS] with zero ERROR rules.

`_accept_lef` already refused a LEF with ZERO pins. The failure mode one step
past that — a pin declared twice — sailed through, because "at least one PIN"
cannot see it.

THE CONTROLS ARE THE POINT. A function that rewrites a delivered view must not
be able to lose geometry, reorder a macro, touch a well-formed file, or merge
two declarations that genuinely disagree. Each test below is one of those.

chip-AGNOSTIC: LEF syntax only. The fixture macro is `fixture_core`.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import digital_hardmacro_gen as G  # noqa: E402

import pytest  # noqa: E402

# The shipped shape, reduced: one macro, one pin declared twice, the second
# declaration carrying only geometry.
DUPLICATED = """VERSION 5.7 ;
MACRO fixture_core
  CLASS BLOCK ;
  ORIGIN 0.000 0.000 ;
  SIZE 100.000 BY 100.000 ;
  PIN clk
    DIRECTION INPUT ;
    USE SIGNAL ;
    ANTENNAGATEAREA 4.738000 ;
    PORT
      LAYER Metal3 ;
        RECT 10.000 10.000 20.000 20.000 ;
    END
  END clk
  PIN dout
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal2 ;
        RECT 30.000 30.000 40.000 40.000 ;
    END
  END dout
  PIN clk
    PORT
      LAYER Metal3 ;
        RECT 50.000 50.000 60.000 60.000 ;
    END
    PORT
      LAYER Metal2 ;
        RECT 70.000 70.000 80.000 80.000 ;
    END
  END clk
  OBS
    LAYER Metal1 ;
      RECT 0.000 0.000 100.000 100.000 ;
  END
END fixture_core
END LIBRARY
"""

WELL_FORMED = DUPLICATED[:DUPLICATED.index("  PIN clk\n    PORT\n")] + \
    DUPLICATED[DUPLICATED.index("  OBS\n"):]


def _pin_lines(text: str) -> list:
    return [l.split()[1] for l in text.splitlines()
            if l.strip().startswith("PIN ")]


# ── the defect, and its repair ──────────────────────────────────────────────

def test_the_measured_shape_declares_a_pin_twice():
    """The fixture is the defect, not a hypothetical."""
    assert _pin_lines(DUPLICATED).count("clk") == 2


def test_merge_leaves_each_pin_declared_once():
    out, rep = G.merge_duplicate_pin_declarations(DUPLICATED)
    names = _pin_lines(out)
    assert names.count("clk") == 1
    assert sorted(names) == ["clk", "dout"]
    assert rep["merged"] == {"fixture_core.clk": 2}


def test_no_geometry_is_lost():
    """THE CONTROL THAT MATTERS MOST. A merge that drops a shape ships a macro
    with a pin the parent cannot reach. Every RECT survives, and so does every
    PORT group."""
    out, _ = G.merge_duplicate_pin_declarations(DUPLICATED)
    assert out.count("RECT") == DUPLICATED.count("RECT")
    assert out.count("PORT") == DUPLICATED.count("PORT")
    for rect in ("RECT 10.000 10.000 20.000 20.000",
                 "RECT 50.000 50.000 60.000 60.000",
                 "RECT 70.000 70.000 80.000 80.000"):
        assert rect in out


def test_the_electrical_attributes_survive_the_merge():
    """The attributed declaration supplies DIRECTION/USE/ANTENNA*; the bare one
    must not erase them."""
    out, _ = G.merge_duplicate_pin_declarations(DUPLICATED)
    clk = out[out.index("PIN clk"):out.index("END clk")]
    assert "DIRECTION INPUT ;" in clk
    assert "USE SIGNAL ;" in clk
    assert "ANTENNAGATEAREA 4.738000 ;" in clk
    assert clk.count("DIRECTION") == 1        # carried over once, not twice


def test_everything_outside_a_duplicated_pin_is_untouched():
    out, _ = G.merge_duplicate_pin_declarations(DUPLICATED)
    for line in ("VERSION 5.7 ;", "MACRO fixture_core", "  CLASS BLOCK ;",
                 "  SIZE 100.000 BY 100.000 ;", "  OBS",
                 "END fixture_core", "END LIBRARY"):
        assert line in out.splitlines() or line in out
    # the non-duplicated pin passes through verbatim
    assert "  PIN dout\n    DIRECTION OUTPUT ;\n    USE SIGNAL ;" in out


# ── NEGATIVE CONTROLS ───────────────────────────────────────────────────────

def test_a_well_formed_lef_is_returned_unchanged_and_identically():
    """Not merely equivalent — the SAME object, so a correct file cannot be
    perturbed by reformatting."""
    out, rep = G.merge_duplicate_pin_declarations(WELL_FORMED)
    assert out is WELL_FORMED
    assert rep["merged"] == {}


def test_disagreeing_declarations_are_REFUSED_not_reconciled():
    """THE CENTRAL CONTROL. Two declarations that state different DIRECTIONs
    are not one pin, and picking one would invent the answer."""
    bad = DUPLICATED.replace(
        "  PIN clk\n    PORT\n      LAYER Metal3 ;\n        RECT 50.000",
        "  PIN clk\n    DIRECTION OUTPUT ;\n    PORT\n      LAYER Metal3 ;\n        RECT 50.000")
    with pytest.raises(G.DuplicatePinConflict) as exc:
        G.merge_duplicate_pin_declarations(bad)
    assert "DIRECTION" in str(exc.value)
    assert "clk" in str(exc.value)


def test_same_pin_name_in_two_macros_is_not_a_duplicate():
    """A pin is scoped to its macro. Merging `clk` across two macros would
    move geometry between cells."""
    two = WELL_FORMED.replace("END LIBRARY\n", "") + \
        WELL_FORMED.replace("VERSION 5.7 ;\n", "").replace(
            "fixture_core", "fixture_other")
    out, rep = G.merge_duplicate_pin_declarations(two)
    assert rep["merged"] == {}
    assert out is two


def test_a_lef_with_no_pins_is_left_alone():
    nopin = "MACRO empty_core\n  CLASS BLOCK ;\nEND empty_core\n"
    out, rep = G.merge_duplicate_pin_declarations(nopin)
    assert out is nopin and rep["merged"] == {}


def test_three_declarations_merge_into_one():
    """The shipped case was two; the contract is 'once', not 'at most twice'."""
    triple = DUPLICATED.replace(
        "  OBS\n",
        "  PIN clk\n    PORT\n      LAYER Metal1 ;\n"
        "        RECT 90.000 90.000 95.000 95.000 ;\n    END\n  END clk\n  OBS\n")
    out, rep = G.merge_duplicate_pin_declarations(triple)
    assert rep["merged"] == {"fixture_core.clk": 3}
    assert _pin_lines(out).count("clk") == 1
    assert out.count("RECT") == triple.count("RECT")


# ── the staging predicate itself: this is the arm the old code can RUN ──────

def test_accept_lef_does_not_stage_a_macro_with_a_pin_declared_twice(tmp_path):
    """THE CONTROL ARM THE PRE-FIX TREE CAN EXECUTE. `_accept_lef` exists in
    both trees, so this test does not ask whether a new function is present —
    it asks what the staging predicate DOES with the measured artefact, and the
    pre-fix answer is "stages it unchanged, pin declared twice".

    `_accept_lef` was written to refuse a PIN-LESS abstract. The failure one
    step past that is a pin declared twice, and "at least one PIN" cannot see
    it — so the malformed view reached `phase3/stage4/hardmacro/` and the
    release documents derived a pin count from it that no view supports.
    """
    produced = tmp_path / "produced.lef"
    produced.write_text(DUPLICATED)
    staged = tmp_path / "staged" / "fixture_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "")
    assert ok, why
    assert staged.is_file()
    names = _pin_lines(staged.read_text())
    assert names.count("clk") == 1, (
        "a macro was staged with a pin declared more than once")
    # and the delivered view still carries every shape it was given
    assert staged.read_text().count("RECT") == DUPLICATED.count("RECT")


def test_accept_lef_refuses_a_macro_whose_duplicates_disagree(tmp_path):
    """Degrade loudly: an irreconcilable LEF is not staged at all, and the
    reason names the pin and the attribute."""
    bad = DUPLICATED.replace(
        "  PIN clk\n    PORT\n      LAYER Metal3 ;\n        RECT 50.000",
        "  PIN clk\n    DIRECTION OUTPUT ;\n    PORT\n      LAYER Metal3 ;\n        RECT 50.000")
    produced = tmp_path / "produced.lef"
    produced.write_text(bad)
    staged = tmp_path / "staged" / "fixture_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "")
    assert ok is False
    assert "DIRECTION" in why and "clk" in why
    assert not staged.exists(), "a malformed macro must not be left on disk"


def test_accept_lef_still_refuses_a_pinless_abstract(tmp_path):
    """The predicate's ORIGINAL job, preserved. Green on both arms by
    construction — it is the guard that this change did not widen what gets
    staged, not evidence that the change works."""
    produced = tmp_path / "produced.lef"
    produced.write_text("MACRO empty_core\n  CLASS BLOCK ;\n  OBS\n  END\nEND empty_core\n")
    staged = tmp_path / "staged" / "empty_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "")
    assert ok is False and "NO `PIN`" in why
    assert not staged.exists()
