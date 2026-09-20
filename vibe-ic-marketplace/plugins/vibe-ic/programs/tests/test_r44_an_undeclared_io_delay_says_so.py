"""r44: an I/O delay the design never declared is the PLUGIN'S assumption, and
the SDC must say so.

The owner is being asked whether this die's boundary paths are timed by a
declared contract or by the flow's own number. That question was unanswerable
from the artefacts: when the design declares an I/O delay as a fraction of its
period the SDC discloses it (VIBEIC_DECLARED_IO_DELAY, with the citation) —
subservient does exactly that at L9 §9.1.3, 20 % of 20 ns = 4 ns — but when it
declares NOTHING the same SDC emitted a bare `2` with no comment at all.

The emitted VALUE is unchanged (dropping the constraint would leave boundary
paths unchecked, which is an optimism-direction change and the owner's call).
What changes is that silence is now labelled.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

DECLARED = "### 9.1.3 I/O delay\n- set_input_delay / set_output_delay: 20% of the clock period\n"
RETRACTED = ("### 9.1.3 I/O delay\n- the 20% I/O delay default does NOT apply "
             "to this interface; the clock period is not used to derive it\n")


def _project(tmp_path: Path, body: str = "") -> Path:
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints_floorplan.md").write_text(
        "# L9\n| gf180mcu_* | **20** | 50 MHz |\n" + body)
    return tmp_path


def test_a_declared_fraction_is_computed_and_cited(tmp_path):
    ns, note = R._declared_io_delay_ns(_project(tmp_path, DECLARED), 20.0)
    assert ns == 4.0
    assert "VIBEIC_DECLARED_IO_DELAY" in note and "20 ns x 20.0 % = 4 ns" in note
    assert "NOT_DECLARED" not in note


def test_silence_is_labelled_as_the_plugins_assumption(tmp_path):
    ns, note = R._declared_io_delay_ns(_project(tmp_path), 20.0)
    assert ns is None                      # the caller keeps its literal
    assert "VIBEIC_IO_DELAY_NOT_DECLARED" in note
    assert "THIS PLUGIN'S ASSUMPTION" in note
    assert "state no I/O delay" in note


def test_a_retracted_declaration_says_retracted_not_absent(tmp_path):
    ns, note = R._declared_io_delay_ns(_project(tmp_path, RETRACTED), 20.0)
    assert ns is None
    assert "RETRACT" in note, note


def test_the_emitted_value_did_not_move(tmp_path):
    """The disclosure is a comment; the number the SDC carries is the same."""
    sdc = R._build_auto_silicon_sdc(_project(tmp_path), top="d")
    assert "VIBEIC_IO_DELAY_NOT_DECLARED" in sdc
    assert "set_output_delay 2 " in sdc or "set_output_delay 2\n" in sdc
    declared = R._build_auto_silicon_sdc(_project(tmp_path / "b", DECLARED), top="d")
    assert "set_output_delay 4 " in declared


def test_a_declared_fraction_with_no_period_is_a_different_finding(tmp_path):
    """Missing PERIOD is not missing DECLARATION — the note must not say the
    design declared nothing when it declared a fraction."""
    ns, note = R._declared_io_delay_ns(_project(tmp_path, DECLARED), 0)
    assert ns is None and note == ""
