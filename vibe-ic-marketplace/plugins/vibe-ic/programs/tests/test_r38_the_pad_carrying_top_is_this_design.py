"""r38 (subservient x gf180mcuD as a DIE): the precheck must not refuse the
design's own pad ring.

MEASURED on r37: the stream's top cell is `chip_top` — the pad-carrying top
step 15.5ic built around the declared core — and `KLayout.CheckTopLevel`
answered REFUSAL "the top-level cell is 'chip_top'; the declaration names
'subservient'. This layout is not this design".

The substitution is read from the producer's own record, never inferred from a
name. Both directions: the recorded wrapper around the DECLARED core passes;
any other top cell, a record naming a different core, and no record at all
still fail exactly as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import general_precheck as G  # noqa: E402

GEOM = {"top_cells": ["chip_top"]}


def _blank():
    step = next(s for s in G.LADDER if s.step_id == "KLayout.CheckTopLevel")
    return G.StepEvidence(step_id=step.step_id, label=step.label,
                          order=step.order, source=step.source,
                          verdict=G.NOT_DETERMINED,
                          refuses_on=step.refuses_on)


def _record(tmp_path: Path, chip_top="chip_top", core="subservient"):
    rec = tmp_path / G.DERIVED_CHIP_TOP_REL
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"chip_top_module": chip_top,
                               "core_module": core}))
    return G._recorded_physical_top(tmp_path)


def test_the_recorded_pad_top_around_the_declared_core_passes(tmp_path):
    ev = _blank()
    G._step_top_level(ev, GEOM, "subservient", _record(tmp_path))
    assert ev.verdict == G.PASS
    assert "pad-carrying top" in ev.evidence and "chip_top" in ev.evidence
    assert ev.measured["physical_top"]["core"] == "subservient"


def test_the_declared_top_itself_still_passes(tmp_path):
    ev = _blank()
    G._step_top_level(ev, {"top_cells": ["subservient"]}, "subservient",
                      _record(tmp_path))
    assert ev.verdict == G.PASS and "as declared" in ev.evidence


def test_a_top_cell_the_record_does_not_name_still_fails(tmp_path):
    ev = _blank()
    G._step_top_level(ev, {"top_cells": ["someone_elses_chip"]}, "subservient",
                      _record(tmp_path))
    assert ev.verdict == G.FAIL and "not this design" in ev.evidence


def test_a_record_wrapping_a_different_core_still_fails(tmp_path):
    ev = _blank()
    G._step_top_level(ev, GEOM, "subservient",
                      _record(tmp_path, core="another_core"))
    assert ev.verdict == G.FAIL and "not this design" in ev.evidence


def test_without_a_record_the_verdict_is_unchanged(tmp_path):
    ev = _blank()
    G._step_top_level(ev, GEOM, "subservient", G._recorded_physical_top(tmp_path))
    assert ev.verdict == G.FAIL and "not this design" in ev.evidence


def test_a_record_that_names_no_wrapper_is_not_a_substitution(tmp_path):
    assert _record(tmp_path, chip_top="subservient") is None
    (tmp_path / G.DERIVED_CHIP_TOP_REL).write_text("{not json")
    assert G._recorded_physical_top(tmp_path) is None
