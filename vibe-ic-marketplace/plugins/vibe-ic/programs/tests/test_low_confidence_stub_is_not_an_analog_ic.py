#!/usr/bin/env python3
"""test_low_confidence_stub_is_not_an_analog_ic.py

A LOW-CONFIDENCE-ONLY BLOCK LIST DOES NOT MAKE A PURE-DIGITAL IC MIXED-SIGNAL.

WHAT WAS MEASURED (2026-09-10, plugin v1.20.15, on the checked-in corpora).
`benchmark_verify_report._is_analog_ic` decided an IC was mixed-signal from the
mere PRESENCE of `phase3/analog/analog_block_list.json`. On sha256 and on
subservient that file carries two blocks, `dac` and `esd`, BOTH
`low_confidence: true`, while each project's OWN `L5_ADI_SPEC.json` states
`no_analog: true`, `analog_blocks: []`, `analog_blocks_detected: false`. The
two stubs quote DENIALS as their evidence — the `dac` paragraph is
"Plugin 不需產生 … analog trim DAC" ("the plugin does NOT need to produce …"),
and the `esd` paragraph is not a design sentence at all but the emitter's own
changelog note.

CONSEQUENCE, COUNTED: Pillar 5 reported `PENDING — A-track verdict MISSING` on
a hash core, an obligation nothing can discharge because an A-track cannot
converge on a block that does not exist; and 13 Pillar-2 rows (A1..A9 plus
M1..M4) turned from N/A into applicable/PENDING. Fourteen cells held open by a
file the design's own L-doc contradicts.

THE PAIR, AND WHY BOTH HALVES ARE HERE. The predicate this repairs was itself
written to close a FALSE PASS — an analog IC whose A-track stopped before
layout used to read "pure-digital / PASS". Every test that pins the repair has
a control beside it that must keep the old closure intact:

  * a confident block still establishes analog          (u_hawaii_adc's shape)
  * an A-track artefact still establishes analog, even with no list at all
  * a stub list with NO readable L5 still establishes analog — "I could not
    look" is not "it said no", so a missing document can never N/A the pillar
  * only stubs + an L5 that DENIES is pure-digital       (sha256's shape)

Every fixture below is synthesised from neutral parts. No design, PDK, vendor
or IP-model identifier appears in this file.

Run: python3 -m pytest programs/tests/test_low_confidence_stub_is_not_an_analog_ic.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import benchmark_verify_report as R          # noqa: E402


def _project(tmp_path, blocks=None, l5=None, atrack=None, name="p"):
    p = tmp_path / name
    (p / "phase3" / "analog").mkdir(parents=True)
    if blocks is not None:
        (p / "phase3" / "analog" / "analog_block_list.json").write_text(
            json.dumps({"blocks": blocks}, ensure_ascii=False))
    if l5 is not None:
        (p / "phase1" / "generated_docs").mkdir(parents=True)
        (p / "phase1" / "generated_docs" / "L5_ADI_SPEC.json").write_text(
            json.dumps(l5))
    if atrack:
        d = p / "phase3" / "analog" / "blk"
        d.mkdir(parents=True, exist_ok=True)
        (d / atrack).write_text("{}" if atrack.endswith(".json") else "x")
    return p


_STUBS = [{"name": "b1", "type": "b1", "low_confidence": True},
          {"name": "b2", "type": "b2", "low_confidence": True}]
_CONFIDENT = [{"name": "b1", "type": "b1", "low_confidence": False}]
_L5_DENIES = {"no_analog": True, "analog_blocks": [],
              "analog_blocks_detected": False}
_L5_AFFIRMS = {"no_analog": False, "analog_blocks": ["b1"],
               "analog_blocks_detected": True}


# ── THE REPAIR ──────────────────────────────────────────────────────────────

def test_stubs_alone_against_a_denying_L5_are_not_an_analog_ic(tmp_path):
    """THE MEASURED SHAPE. Two low-confidence stubs, and the document that owns
    the fact says there is no analog. The L-doc wins."""
    assert R._is_analog_ic(_project(tmp_path, _STUBS, _L5_DENIES)) is False


def test_the_denial_may_be_spelled_either_way(tmp_path):
    """`no_analog: true` and `analog_blocks_detected: false` are the same
    statement; a repair that reads only one of them leaves the other live."""
    a = _project(tmp_path, _STUBS, {"no_analog": True}, name="a")
    b = _project(tmp_path, _STUBS, {"analog_blocks_detected": False}, name="b")
    assert R._is_analog_ic(a) is False and R._is_analog_ic(b) is False


# ── THE CONTROLS: the FALSE PASS this predicate closed stays closed ─────────

def test_one_confident_block_still_makes_it_an_analog_ic(tmp_path):
    """The genuine mixed-signal shape. A confident detection outranks any
    declaration — including an L5 that denies, which would then be the
    contradiction to chase, not a licence to skip the pillar."""
    assert R._is_analog_ic(_project(tmp_path, _CONFIDENT, _L5_DENIES)) is True
    assert R._is_analog_ic(_project(tmp_path, _CONFIDENT, None, name="q")) is True


def test_stubs_with_NO_readable_L5_still_make_it_an_analog_ic(tmp_path):
    """"I could not look" is not "it said no". Without this the repair would
    let a MISSING document N/A the load-bearing analog pillar — a worse defect
    than the one it fixes."""
    assert R._is_analog_ic(_project(tmp_path, _STUBS, None)) is True


def test_an_unparseable_L5_does_not_count_as_a_denial(tmp_path):
    p = _project(tmp_path, _STUBS, None)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs" / "L5_ADI_SPEC.json").write_text("{not json")
    assert R._l5_denies_analog(p) is False
    assert R._is_analog_ic(p) is True


def test_an_L5_that_affirms_analog_is_not_a_denial(tmp_path):
    assert R._l5_denies_analog(_project(tmp_path, _STUBS, _L5_AFFIRMS)) is False
    assert R._is_analog_ic(_project(tmp_path, _STUBS, _L5_AFFIRMS, name="z")) is True


def test_a_real_A_track_artefact_outranks_every_declaration(tmp_path):
    """The arm that closed the original FALSE PASS. A corner sweep on disk is a
    measurement; a denial in a document is a claim. The measurement wins, and
    it wins with no block list present at all."""
    for art in ("corner_results.json", "spec.json", "topology.md"):
        p = _project(tmp_path, None, _L5_DENIES, atrack=art,
                     name=f"t{art[:4]}")
        assert R._is_analog_ic(p) is True, art


def test_a_list_that_names_no_blocks_still_engages_the_pillar(tmp_path):
    """THE BOUNDARY OF THIS REPAIR, pinned so it cannot creep. A file with no
    `blocks` key states nothing this repair can weigh, and
    `test_pre_layout_analog_artefact_is_detected` /
    `test_legacy_root_block_list_still_detected` already assert that such a
    file — an A-track that stopped before layout — must keep Pillar 5 engaged.
    Reading their silence as "no analog" would reopen the silent FALSE PASS
    those tests closed, so presence still wins here."""
    assert R._is_analog_ic(_project(tmp_path, [], _L5_DENIES)) is True
    assert R._is_analog_ic(_project(tmp_path, None, None, name="n")) is False
