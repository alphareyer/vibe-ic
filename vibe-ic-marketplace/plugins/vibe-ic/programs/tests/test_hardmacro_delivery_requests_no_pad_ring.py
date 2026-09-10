#!/usr/bin/env python3
"""Step 15.5ic — a HARDMACRO delivery requests NO pad ring, whatever slot
geometry is staged beside it.

THE DEFECT, MEASURED ON spm x gf180mcuD
---------------------------------------
`_chip_path_requests_pad_ring` requested a chip pad ring on the mere PRESENCE
of `input/submission_template/slots/*.yaml`. On spm that directory holds the
operator's CATALOGUE of four slot sizes, while its sibling
`tapeout_declaration.json` declares `deliverable: HARDMACRO`, leaves every
`pad_*` answer NOT_DETERMINED, and states in its own rationale that the design
"takes no operator slot whose geometry could supply one".

A catalogue is not a choice. Reading it as one gave a 111x111 um core a
3162x3162 um die -- 28x the core in each dimension -- and every physical
sign-off failure followed from THAT GEOMETRY rather than from the design:

    pdn_em_resize  FAIL  Metal2 9.5->18.72 um (1.97x short), Metal4 1.6->18.72
                         um (11.7x short), Metal5 1.6->8.37 um (5.23x short),
                         against openroad-psm's own measured worst segment
    pnr            FAIL  ANTENNA_SPARE_TIE_UNPROTECTED on the preserved spare
                         pool's tie-lo nets
    sta_record     FAIL  2 max_fanout DRV rows, both design rows

The seal-ring band already reasons the right way and says so (#2112: "a macro
somebody else places, not a die that will be fabricated"), and this module's
sibling states the doctrine outright -- "NEITHER IS A PROPERTY OF BEING ON A
SHUTTLE. BOTH ARE PROPERTIES OF BEING A DIE". The pad-ring predicate alone
never consulted the delivery. That asymmetry is the whole defect.

WHAT EVERY TEST BELOW BREAKS
----------------------------
  * a HARDMACRO declaration stops suppressing the ring        -> red
  * a DIE declaration stops requesting one                    -> red
  * an absent/unreadable/NOT_DETERMINED answer changes anything -> red
  * the #1410 self-tape-out path loses its ring               -> red
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phase3_one_shot_runner as R  # noqa: E402


def _slots(project: Path) -> Path:
    d = project / "input" / "submission_template" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("0p5x0p5", "0p5x1", "1x0p5", "1x1"):
        (d / f"{name}.yaml").write_text("slot: catalogue entry\n")
    return d


def _declare(project: Path, deliverable) -> None:
    tmpl = project / "input" / "submission_template"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "tapeout_declaration.json").write_text(json.dumps(
        {"schema": "vibe-ic/tapeout_declaration/1",
         "answers": {"deliverable": deliverable, "top_cell": "spm",
                     "pad_order_by_side": "NOT_DETERMINED"}}) + "\n")


def test_hardmacro_declaration_suppresses_the_ring_despite_a_slot_catalogue(
        tmp_path):
    """THE FIX. The catalogue is present and the declaration says HARDMACRO."""
    _slots(tmp_path)
    assert R._chip_path_requests_pad_ring(tmp_path) is True, (
        "a staged slot catalogue with no declaration must keep requesting a "
        "ring -- otherwise this test proves nothing about the declaration")
    _declare(tmp_path, "HARDMACRO")
    assert R._chip_path_requests_pad_ring(tmp_path) is False


def test_a_die_declaration_still_requests_the_ring(tmp_path):
    """The control. Same catalogue, opposite answer, opposite verdict."""
    _slots(tmp_path)
    _declare(tmp_path, "DIE")
    assert R._chip_path_requests_pad_ring(tmp_path) is True


def test_a_non_answer_leaves_behaviour_unchanged(tmp_path):
    """NOT_DETERMINED is not an answer, and neither is an unreadable file."""
    _slots(tmp_path)
    for value in ("NOT_DETERMINED", "NOT_APPLICABLE", "", None):
        _declare(tmp_path, value)
        assert R._chip_path_requests_pad_ring(tmp_path) is True, value
    (tmp_path / "input/submission_template/tapeout_declaration.json"
     ).write_text("{ this is not json\n")
    assert R._chip_path_requests_pad_ring(tmp_path) is True


def test_self_tapeout_without_a_declaration_keeps_its_ring(tmp_path):
    """#1410 preserved: a die taping ITSELF out has no operator template."""
    tmpl = tmp_path / "input" / "submission_template"
    tmpl.mkdir(parents=True)
    assert R._chip_path_requests_pad_ring(tmp_path) is False
    (tmpl / "SELF_TAPEOUT.txt").write_text("x\n")
    assert R._chip_path_requests_pad_ring(tmp_path) is True
    _declare(tmp_path, "DIE")
    assert R._chip_path_requests_pad_ring(tmp_path) is True


def test_the_answer_reader_reports_its_own_basis(tmp_path):
    """A predicate that cannot say WHY is not auditable."""
    assert R._declaration_deliverable_answer(tmp_path)[0] is None
    assert "not on disk" in R._declaration_deliverable_answer(tmp_path)[1]
    _declare(tmp_path, "hardmacro")
    answer, why = R._declaration_deliverable_answer(tmp_path)
    assert answer == "HARDMACRO", "the answer is case-normalised"
    assert "tapeout_declaration.json" in why
