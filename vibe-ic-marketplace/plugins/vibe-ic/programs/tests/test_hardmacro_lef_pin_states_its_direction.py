"""Every SIGNAL pin of a delivered hardmacro LEF must state its DIRECTION, and
the producer must take that direction from the DEF rather than invent one.

THE HALF #2249 COULD NOT REACH. `merge_duplicate_pin_declarations` (PR #2249)
unions the geometry of pins magic declared TWICE and carries their attributes
first-occurrence-unique, so a pin with an attributed declaration and a bare one
comes out attributed. A pin magic declared ONCE, BARE, has no attributed
sibling and leaves the merge exactly as bare as it went in.

MEASURED 2026-09-15 (lane icsub2) on the signed-off gf180mcuD kit this flow
shipped as its own 37.5ip deliverable, running the three stages over the real
`subservient.lef` + the real `subservient.def` the same invocation gave magic::

    stage                        signal PIN statements   without DIRECTION
    as shipped                          31                     14
    after the #2249 merge ALONE         31                     14     <-- unchanged
    after the merge + this fill         31                      0

    (33 distinct names, 18 of them declared twice and repaired by the merge;
     the remaining 15 were only ever bare. 15 - 1 supply = 14 signal pins.
     Independently on `spm`: 17 of 38 bare after the merge.)

WHY IT MATTERS. `DIRECTION` has NO LEF DEFAULT. A placer reading a pin without
one cannot tell a macro input from a macro output, and `release_docs_check
--arm ip` reports PIN_COUNT_DISAGREES_WITH_NETLIST because the abstract and the
netlist view describe different interfaces. The primary deliverable of the
whole HARDMACRO route was the artefact that was malformed, and
`digital_hardmacro_gen` still reported `PASS [PRODUCED]` over it because no
gate asked.

NOTHING IS GUESSED — that is the other half of what these tests pin. The
answer comes from the DEF's own `PINS` section through `read_interface`, the
same reader the `.v` and `.lib` views are built from (which is why THOSE two
views were already correct at 31 pins while the LEF was not). A pin the DEF
does not name gets nothing added and is REFUSED; a pin whose LEF direction
CONTRADICTS the DEF raises rather than being overwritten.

SUPPLY PINS ARE NOT ASKED. `USE POWER` / `USE GROUND` is where a LEF says which
rail a supply is, and the DEF's PINS section does not carry the PDN rails at
all (subservient's DEF has 31 pins = the 8 module ports expanded, no VDD/VSS).
A supply carrying a USE and no DIRECTION is a complete supply declaration, and
a test that called it a defect would force the producer to invent a direction
for a rail.

chip-AGNOSTIC: LEF/DEF syntax only. The fixture macro is `fixture_core`.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import digital_hardmacro_gen as G  # noqa: E402
import digital_hardmacro_check as C  # noqa: E402

import pytest  # noqa: E402


# The measured shape, reduced: one macro whose pins magic declared ONCE and
# BARE (`o_bare`, `d[1]`), beside ones it attributed (`clk`, `d[0]`) and a
# supply that carries only a USE — exactly the mix the shipped artefact had.
BARE = """VERSION 5.7 ;
MACRO fixture_core
  CLASS BLOCK ;
  ORIGIN 0.000 0.000 ;
  SIZE 100.000 BY 100.000 ;
  PIN clk
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal3 ;
        RECT 10.000 10.000 20.000 20.000 ;
    END
  END clk
  PIN o_bare
    PORT
      LAYER Metal2 ;
        RECT 30.000 30.000 40.000 40.000 ;
    END
  END o_bare
  PIN d[0]
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal2 ;
        RECT 50.000 30.000 60.000 40.000 ;
    END
  END d[0]
  PIN d[1]
    PORT
      LAYER Metal2 ;
        RECT 70.000 30.000 80.000 40.000 ;
    END
  END d[1]
  PIN VSS
    USE GROUND ;
    PORT
      LAYER Metal5 ;
        RECT 0.000 90.000 100.000 95.000 ;
    END
  END VSS
  OBS
    LAYER Metal1 ;
      RECT 0.000 0.000 100.000 100.000 ;
  END
END fixture_core
END LIBRARY
"""

DEF_FOR_BARE = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN fixture_core ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
PINS 4 ;
    - clk + NET clk + DIRECTION INPUT + USE SIGNAL
      + PORT + LAYER Metal3 ( -200 -200 ) ( 200 200 ) + PLACED ( 15000 15000 ) N ;
    - o_bare + NET o_bare + DIRECTION OUTPUT + USE SIGNAL
      + PORT + LAYER Metal2 ( -200 -200 ) ( 200 200 ) + PLACED ( 35000 35000 ) N ;
    - d[0] + NET d[0] + DIRECTION OUTPUT + USE SIGNAL
      + PORT + LAYER Metal2 ( -200 -200 ) ( 200 200 ) + PLACED ( 55000 35000 ) N ;
    - d[1] + NET d[1] + DIRECTION OUTPUT + USE SIGNAL
      + PORT + LAYER Metal2 ( -200 -200 ) ( 200 200 ) + PLACED ( 75000 35000 ) N ;
END PINS
END DESIGN
"""


def _iface(def_text: str = DEF_FOR_BARE):
    return G.read_interface(def_text)


def _rects(text: str) -> int:
    """RECT STATEMENTS, not the substring. `DIRECTION` CONTAINS "RECT", so a
    naive `count("RECT")` grows by one for every attribute this fill adds and
    a geometry-preservation test written that way reports loss-of-geometry
    when nothing moved. Found by this file's own control going red."""
    return sum(1 for l in text.splitlines() if l.strip().startswith("RECT "))


def _pin_block(text: str, name: str) -> str:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == f"PIN {name}":
            j = i
            while j < len(lines) and lines[j].strip() != f"END {name}":
                j += 1
            return "\n".join(lines[i:j + 1])
    return ""


# ── the defect is real, and the #2249 merge cannot reach it ────────────────

def test_the_fixture_really_does_carry_bare_signal_pins():
    """The fixture is the measured defect, not a hypothetical."""
    assert "PIN o_bare\n    PORT" in BARE
    assert BARE.count("DIRECTION") == 2          # clk and d[0] only


def test_the_duplicate_merge_alone_leaves_them_bare():
    """THE LOAD-BEARING NEGATIVE CONTROL. #2249 landed on main and the pins
    this file exists for are still bare after it, which is why a second fix is
    owed. If this ever goes green, the two halves have become one and this
    file should be re-argued, not deleted."""
    merged, report = G.merge_duplicate_pin_declarations(BARE)
    assert report["merged"] == {}, "the fixture has no duplicates to merge"
    assert "DIRECTION" not in _pin_block(merged, "o_bare")
    assert "DIRECTION" not in _pin_block(merged, "d[1]")


# ── the repair ────────────────────────────────────────────────────────────

def test_every_signal_pin_gains_the_direction_the_def_states():
    out, rep = G.fill_pin_attributes_from_def(BARE, _iface())
    assert rep["signal_pins_without_direction"] == []
    assert "DIRECTION OUTPUT ;" in _pin_block(out, "o_bare")
    assert "DIRECTION OUTPUT ;" in _pin_block(out, "d[1]")
    assert sorted(rep["filled"]) == ["d[1]", "o_bare"]
    assert rep["filled"]["o_bare"] == {"direction": "OUTPUT", "use": "SIGNAL"}


def test_the_direction_is_the_defs_and_not_a_majority_or_a_name_guess():
    """`o_bare` is spelled like an output and `d[1]` sits beside an output —
    both would be guessed OUTPUT. Ask the DEF for the OPPOSITE and the LEF
    must say the opposite: the value comes from the document, not from the
    name and not from the neighbours."""
    flipped = DEF_FOR_BARE.replace(
        "- o_bare + NET o_bare + DIRECTION OUTPUT",
        "- o_bare + NET o_bare + DIRECTION INPUT").replace(
        "- d[1] + NET d[1] + DIRECTION OUTPUT",
        "- d[1] + NET d[1] + DIRECTION INPUT")
    out, _ = G.fill_pin_attributes_from_def(BARE, _iface(flipped))
    assert "DIRECTION INPUT ;" in _pin_block(out, "o_bare")
    assert "DIRECTION INPUT ;" in _pin_block(out, "d[1]")


def test_an_attributed_pin_is_not_rewritten():
    out, rep = G.fill_pin_attributes_from_def(BARE, _iface())
    assert "clk" not in rep["filled"] and "d[0]" not in rep["filled"]
    assert _pin_block(out, "clk") == _pin_block(BARE, "clk")
    assert _pin_block(out, "d[0]") == _pin_block(BARE, "d[0]")


def test_geometry_and_everything_outside_a_filled_pin_are_untouched():
    out, _ = G.fill_pin_attributes_from_def(BARE, _iface())
    assert _rects(out) == _rects(BARE)
    for token in ("CLASS BLOCK ;", "ORIGIN 0.000 0.000 ;",
                  "SIZE 100.000 BY 100.000 ;", "OBS", "END fixture_core"):
        assert out.count(token) == BARE.count(token)
    assert [l.split()[1] for l in out.splitlines()
            if l.strip().startswith("PIN ")] == \
           [l.split()[1] for l in BARE.splitlines()
            if l.strip().startswith("PIN ")]


def test_a_complete_lef_is_returned_unchanged_and_identically():
    complete, _ = G.fill_pin_attributes_from_def(BARE, _iface())
    again, rep = G.fill_pin_attributes_from_def(complete, _iface())
    assert again is complete, "a second pass must be a no-op, not a rewrite"
    assert rep["filled"] == {}


# ── the refusals: nothing is guessed ──────────────────────────────────────

def test_a_pin_the_def_does_not_name_is_reported_not_filled():
    short = DEF_FOR_BARE.replace(
        "    - o_bare + NET o_bare + DIRECTION OUTPUT + USE SIGNAL\n"
        "      + PORT + LAYER Metal2 ( -200 -200 ) ( 200 200 ) "
        "+ PLACED ( 35000 35000 ) N ;\n", "")
    out, rep = G.fill_pin_attributes_from_def(BARE, _iface(short))
    assert "o_bare" in rep["unresolved"]
    assert "o_bare" in rep["signal_pins_without_direction"]
    assert "DIRECTION" not in _pin_block(out, "o_bare"), (
        "a pin the DEF cannot answer for must be left alone, not guessed")


def test_a_lef_direction_contradicting_the_def_is_REFUSED_not_overwritten():
    contradicting = DEF_FOR_BARE.replace(
        "- clk + NET clk + DIRECTION INPUT", "- clk + NET clk + DIRECTION OUTPUT")
    with pytest.raises(G.PinAttributeConflict) as exc:
        G.fill_pin_attributes_from_def(BARE, _iface(contradicting))
    assert "clk" in str(exc.value) and "DIRECTION" in str(exc.value)


def test_a_supply_pin_carrying_only_a_use_is_not_a_bare_signal_pin():
    """VSS is in no DEF PINS section and states its rail through USE. Calling
    it a defect would force the producer to invent a direction for a rail."""
    _, rep = G.fill_pin_attributes_from_def(BARE, _iface())
    assert "VSS" not in rep["signal_pins_without_direction"]
    assert "VSS" in rep["unresolved"], (
        "it is still DISCLOSED as unanswerable from the DEF")


def test_pins_outside_a_macro_are_not_touched():
    stray = "PIN loose\n  PORT\n  END\nEND loose\n" + BARE
    out, rep = G.fill_pin_attributes_from_def(stray, _iface())
    assert "loose" not in rep["signal_pins_without_direction"]
    assert "loose" not in rep["filled"]


# ── the producer refuses to STAGE a malformed abstract ─────────────────────

def test_accept_lef_does_not_stage_a_macro_with_a_direction_less_signal_pin(
        tmp_path):
    """THE CONTROL ARM THE PRE-FIX TREE CAN EXECUTE. `_accept_lef` exists in
    both trees; this asks what the STAGING PREDICATE does with the measured
    artefact. Pre-fix answer: stages it, `o_bare` and `d[1]` with no
    DIRECTION. Post-fix: stages it with the DEF's directions."""
    produced = tmp_path / "produced.lef"
    produced.write_text(BARE)
    staged = tmp_path / "staged" / "fixture_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "", DEF_FOR_BARE)
    assert ok, why
    text = staged.read_text()
    assert "DIRECTION OUTPUT ;" in _pin_block(text, "o_bare")
    assert "DIRECTION OUTPUT ;" in _pin_block(text, "d[1]")
    assert _rects(text) == _rects(BARE)


def test_accept_lef_refuses_when_the_def_cannot_answer(tmp_path):
    """Degrade loudly. A signal pin nothing can attribute is not shipped, and
    the reason names it."""
    produced = tmp_path / "produced.lef"
    produced.write_text(BARE)
    staged = tmp_path / "staged" / "fixture_core.lef"
    short = DEF_FOR_BARE.replace(
        "    - o_bare + NET o_bare + DIRECTION OUTPUT + USE SIGNAL\n"
        "      + PORT + LAYER Metal2 ( -200 -200 ) ( 200 200 ) "
        "+ PLACED ( 35000 35000 ) N ;\n", "")
    ok, why = G._accept_lef(produced, staged, 0, "", short)
    assert ok is False
    assert "o_bare" in why and "DIRECTION" in why
    assert not staged.exists(), "a malformed macro must not be left on disk"


def test_accept_lef_refuses_a_bare_signal_pin_even_with_no_def_at_all(tmp_path):
    """The repair needs a DEF; the QUESTION does not. Gating the refusal on
    the repair's input would leave the one path that cannot repair as the one
    path that cannot complain."""
    produced = tmp_path / "produced.lef"
    produced.write_text(BARE)
    staged = tmp_path / "staged" / "fixture_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "")
    assert ok is False and "DIRECTION" in why
    assert not staged.exists()


def test_accept_lef_still_stages_a_complete_abstract(tmp_path):
    """The guard that this change did not narrow what gets staged."""
    complete, _ = G.fill_pin_attributes_from_def(BARE, _iface())
    produced = tmp_path / "produced.lef"
    produced.write_text(complete)
    staged = tmp_path / "staged" / "fixture_core.lef"
    ok, why = G._accept_lef(produced, staged, 0, "", DEF_FOR_BARE)
    assert ok, why
    assert staged.is_file()


# ── the independent gate ──────────────────────────────────────────────────

def test_parse_lef_reports_direction_PER_SPELLING_not_per_base():
    """A base-name rollup cannot see this defect: one attributed bit makes a
    whole bus look attributed. MEASURED — over the shipped kit the rollup saw
    2 of the 14 bare statements, because only those 2 were scalars."""
    d = C.parse_lef(BARE, "fixture_core")
    assert d["direction"]["d"] == "output", (
        "the base rollup calls the bus attributed")
    assert d["direction_spelled"]["d[1]"] == "", (
        "the spelled view sees the bit that is not")


def test_parse_lef_does_not_let_a_bare_duplicate_erase_an_attributed_one():
    """LAST WINS WOULD BE WRONG, and it was: magic emits the attributed
    declaration and the bare GDS-derived one for the SAME pin, bare last."""
    dup = BARE.replace(
        "  PIN o_bare\n",
        "  PIN clk\n    PORT\n      LAYER Metal2 ;\n"
        "        RECT 1.000 1.000 2.000 2.000 ;\n    END\n  END clk\n"
        "  PIN o_bare\n", 1)
    d = C.parse_lef(dup, "fixture_core")
    assert d["direction_spelled"]["clk"] == "input"


def test_the_gate_refuses_a_lef_whose_signal_pin_has_no_direction():
    d = C.parse_lef(BARE, "fixture_core")
    bare = sorted(s for s, v in d["direction_spelled"].items()
                  if not v and C.base_name(s, d["bus_chars"]) in d["signal"])
    assert bare == ["d[1]", "o_bare"]


def test_the_gate_is_green_on_the_repaired_lef():
    complete, _ = G.fill_pin_attributes_from_def(BARE, _iface())
    d = C.parse_lef(complete, "fixture_core")
    bare = sorted(s for s, v in d["direction_spelled"].items()
                  if not v and C.base_name(s, d["bus_chars"]) in d["signal"])
    assert bare == []
    assert "VSS" not in d["signal"], "the supply is not asked for a direction"


# ── the gate END TO END, executable on BOTH trees ─────────────────────────

def test_the_whole_gate_refuses_a_kit_whose_lef_drops_a_direction(tmp_path):
    """THE CONTROL THAT NEEDS NO NEW SYMBOL. It drives the shipped gate over a
    complete four-view kit whose only defect is a LEF bus BIT with no
    DIRECTION, and asks for the gate's verdict.

    Pre-fix the gate reported **PASS** over it: `parse_lef` had read DIRECTION
    since the gate was written and nothing consumed it, and the base-name
    rollup saw `dout[0]`'s direction and called the whole bus attributed. That
    is how a malformed primary deliverable reached `phase3/stage4/hardmacro/`
    with `PASS [PRODUCED]` beside it.

    Every other axis of the kit is deliberately left agreeing, so a red here
    is this clause and nothing else.
    """
    from test_digital_hardmacro_check import LEF_OK, make_kit, rules
    holed = LEF_OK.replace(
        "  PIN dout[1]\n    DIRECTION OUTPUT ;\n    USE SIGNAL ;\n",
        "  PIN dout[1]\n", 1)
    assert holed != LEF_OK and holed.count("DIRECTION") == LEF_OK.count(
        "DIRECTION") - 1
    got = rules(make_kit(tmp_path, lef=holed))
    assert "LEF_SIGNAL_PIN_NO_DIRECTION" in got, (
        "the gate passed a kit whose LEF does not say which way dout[1] "
        f"points; it reported {sorted(got)}")


def test_the_whole_gate_still_passes_the_agreeing_kit(tmp_path):
    """The guard that the new clause did not redden a correct kit."""
    from test_digital_hardmacro_check import make_kit, rules
    got = rules(make_kit(tmp_path))
    assert "LEF_SIGNAL_PIN_NO_DIRECTION" not in got
    assert "LEF_BUS_DIRECTION_MIXED" not in got


def test_the_whole_gate_refuses_a_bus_whose_bits_point_both_ways(tmp_path):
    from test_digital_hardmacro_check import LEF_OK, make_kit, rules
    mixed = LEF_OK.replace(
        "  PIN dout[1]\n    DIRECTION OUTPUT ;",
        "  PIN dout[1]\n    DIRECTION INPUT ;", 1)
    got = rules(make_kit(tmp_path, lef=mixed))
    assert "LEF_BUS_DIRECTION_MIXED" in got
