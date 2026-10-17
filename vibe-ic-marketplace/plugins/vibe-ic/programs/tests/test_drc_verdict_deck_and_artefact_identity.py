#!/usr/bin/env python3
"""A DRC verdict must come from the sign-off deck, run on the shipped layout.

`step_drc` had an escape hatch: when the sign-off deck's violations were
>90% min-spacing/min-width rules, it re-streamed the layout through Magic
(which merges abutting same-layer geometry), ran **Magic's own** `drc
check` on the re-stream, and adopted THAT number as the sign-off verdict.

Two things are wrong with that, and both were measured on a real run:

  1. It is not a like-for-like comparison. Two different rule decks
     disagreeing on two different files says nothing about the streamout.
     On the measured run the sign-off deck reported 11 violations on the
     streamed GDS and Magic reported 0 on the merged GDS, so the step
     recorded `violations=0` while the canonical `drc_signoff.rpt` beside
     it carried 11. Running the SAME deck on that SAME merged GDS also
     returns 11 — the merge removed nothing.

  2. The adopted number belonged to a file that ships nowhere.
     `<top>.magic_merged.gds` is a diagnostic artefact; the layout the
     flow hands on is `<top>.gds`, and md5 confirms they are different
     files.

These tests pin the two properties that follow: the re-measurement uses
the same deck invocation with only the layout swapped, and an
unreadable re-measurement is never readable as "clean".
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_PROG = (Path(__file__).resolve().parent.parent / "phase3_one_shot_runner.py")
_spec = importlib.util.spec_from_file_location("_p3_drc_ident", _PROG)
p3 = importlib.util.module_from_spec(_spec)
sys.modules["_p3_drc_ident"] = p3
_spec.loader.exec_module(p3)


class _Pdk:
    drc_deck = "/foss/pdks/DECK/deck.lydrc"


def test_signoff_and_restream_use_the_same_deck_invocation(monkeypatch,
                                                           tmp_path):
    """Only the layout and the report path may differ between the two runs.

    This is the property whose absence produced the defect: the second
    measurement used to be a different tool running different rules.
    """
    seen = []

    def _fake_exec(container, cmd, marker=None, outputs=None,
                   inputs=None, transcript_path=None):
        seen.append(cmd)
        for o in (outputs or []):
            Path(o).parent.mkdir(parents=True, exist_ok=True)
            Path(o).write_text("<report-database></report-database>")
        return 0, "", ""

    monkeypatch.setattr(p3, "_docker_exec", _fake_exec)
    monkeypatch.setattr(p3, "_to_container_path", lambda s, c: s)

    a_gds, b_gds = tmp_path / "top.gds", tmp_path / "top.magic_merged.gds"
    a_gds.write_bytes(b"")
    b_gds.write_bytes(b"")
    p3._klayout_deck_exec(a_gds, tmp_path / "a.rpt", "top", _Pdk(), "ctr")
    p3._klayout_deck_exec(b_gds, tmp_path / "b.rpt", "top", _Pdk(), "ctr")

    assert len(seen) == 2
    norm = [c.replace(str(a_gds), "<L>").replace(str(b_gds), "<L>")
             .replace(str(tmp_path / "a.rpt"), "<R>")
             .replace(str(tmp_path / "b.rpt"), "<R>") for c in seen]
    assert norm[0] == norm[1], (
        "the re-measurement is not the same deck invocation:\n"
        f"  {norm[0]}\n  {norm[1]}")
    assert _Pdk.drc_deck in seen[0]
    assert _Pdk.drc_deck in seen[1]


def test_unreadable_restream_is_none_not_zero(monkeypatch, tmp_path):
    """A re-run that produced no report must not read as "and it was clean".

    `None` is the honest answer and the caller prints UNTESTED for it; a
    `0` here would re-open the exact hole this change closes.
    """
    monkeypatch.setattr(
        p3, "_docker_exec",
        lambda container, cmd, marker=None, outputs=None, inputs=None,
               transcript_path=None: (0, "", ""))
    monkeypatch.setattr(p3, "_to_container_path", lambda s, c: s)
    gds = tmp_path / "top.gds"
    gds.write_bytes(b"")
    total, per_rule = p3._klayout_deck_violations_on(
        gds, tmp_path / "missing.rpt", "top", _Pdk(), "ctr")
    assert total is None
    assert per_rule == {}


def test_stalled_restream_is_none_not_zero(monkeypatch, tmp_path):
    """A stall/ceiling kill must not be scored from a partial report."""
    def _stalled(container, cmd, marker=None, outputs=None, inputs=None,
                  transcript_path=None):
        for o in (outputs or []):
            Path(o).parent.mkdir(parents=True, exist_ok=True)
            Path(o).write_text("<report-database>")  # truncated
        return p3._RC_STALLED, "", ""

    monkeypatch.setattr(p3, "_docker_exec", _stalled)
    monkeypatch.setattr(p3, "_to_container_path", lambda s, c: s)
    gds = tmp_path / "top.gds"
    gds.write_bytes(b"")
    total, _ = p3._klayout_deck_violations_on(
        gds, tmp_path / "partial.rpt", "top", _Pdk(), "ctr")
    assert total is None


def test_magic_default_unit_box_is_an_empty_cell(tmp_path):
    """`0 0 1 1` is Magic's default box for a cell it could not read.

    Verbatim from the real tool (`magic -dnull -rcfile /dev/null`, which
    comes up on `minimum.tech`). The probe used to know only `0 0 0 0`,
    so this transcript scored geometry_loaded=True and its DRC of an
    empty cell was adopted as an authoritative 0.
    """
    transcript = (
        'Don\'t know how to read GDS-II:\n'
        'Nothing in "cifinput" section of tech file.\n'
        "Cell top couldn't be read\n"
        "Creating new cell\n"
        "MAGIC_DRC_COUNT 0\n"
        "MAGIC_BBOX 0 0 1 1\n"
    )
    v = p3._detect_vacuous_magic(transcript, drc_count=0)
    assert v["empty_bbox"] is True
    assert v["geometry_loaded"] is False
    assert v["vacuous"] is True


def test_a_real_bbox_is_still_loaded_geometry():
    """Over-fix guard: a genuine layout must stay non-vacuous."""
    v = p3._detect_vacuous_magic(
        "MAGIC_DRC_COUNT 0\nMAGIC_BBOX 0 0 880000 880000\n", drc_count=0)
    assert v["empty_bbox"] is False
    assert v["geometry_loaded"] is True
    assert v["vacuous"] is False


def test_zero_zero_zero_zero_still_detected():
    """Over-fix guard: the original spelling must not regress."""
    v = p3._detect_vacuous_magic("box values 0 0 0 0\n", drc_count=0)
    assert v["empty_bbox"] is True
    assert v["vacuous"] is True


@pytest.mark.parametrize("spelling", ["top_cell", "topcell"])
@pytest.mark.parametrize("selected", ["CLEAN_TOP", "DIRTY_TOP", "MISSING_TOP"])
def test_native_deck_uses_explicit_top(tmp_path, spelling, selected):
    """A real multi-top layout must obey either deck API spelling.

    This runs the original launcher, native exec seam and RDB counter. The
    synthetic rule forbids layer 2: CLEAN_TOP has none, DIRTY_TOP has one
    polygon. A missing top must refuse instead of selecting another cell.
    """
    klayout = shutil.which("klayout")
    if klayout is None:
        pytest.skip("native KLayout is required for this integration case")
    assert p3._local_exec_mode(), "run this case inside the EDA image"
    gds = tmp_path / "multiple_tops.gds"
    maker = tmp_path / "make_layout.rb"
    maker.write_text(
        "layout = RBA::Layout.new\n"
        "layout.dbu = 0.001\n"
        "allowed = layout.layer(1, 0)\n"
        "forbidden = layout.layer(2, 0)\n"
        "dirty = layout.create_cell('DIRTY_TOP')\n"
        "dirty.shapes(allowed).insert(RBA::Box.new(0, 0, 2000, 2000))\n"
        "dirty.shapes(forbidden).insert(RBA::Box.new(0, 0, 2000, 2000))\n"
        "clean = layout.create_cell('CLEAN_TOP')\n"
        "clean.shapes(allowed).insert(RBA::Box.new(0, 0, 2000, 2000))\n"
        "layout.write($output)\n",
        encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    made = subprocess.run(
        [klayout, "-b", "-r", str(maker), "-rd", f"output={gds}"],
        capture_output=True, text=True, env=env, timeout=30)
    assert made.returncode == 0, made.stdout + made.stderr
    assert gds.is_file() and gds.stat().st_size > 0
    deck = tmp_path / "selected_top.drc"
    layout_arg, report_arg = (("input", "report") if spelling == "top_cell"
                              else ("in_gds", "report_file"))
    deck.write_text(
        f"selected = ${spelling}\n"
        f"if selected\n  source(${layout_arg}, selected)\n"
        f"else\n  source(${layout_arg})\nend\n"
        f"report('Synthetic selected-top check', ${report_arg})\n"
        "input(2, 0).output('FORBIDDEN_LAYER', 'Synthetic forbidden layer')\n",
        encoding="utf-8")
    class NativeDeck:
        drc_deck = str(deck)
    rpt = tmp_path / "selected_top.rdb"
    assert not rpt.exists()
    rc, stdout, stderr = p3._klayout_deck_exec(
        gds, rpt, selected, NativeDeck(), "")
    root = ET.parse(rpt).getroot() if rpt.is_file() else None
    count = (p3._v1_6_597_count_klayout_xml_violations(rpt)
             if root is not None else None)
    observation = {
        "spelling": spelling, "requested_top": selected, "rc": rc,
        "stdout": stdout, "stderr": stderr,
        "reported_top": root.findtext("top-cell") if root is not None else None,
        "count": count, "report_exists": rpt.is_file(),
    }
    (tmp_path / "NATIVE_TOP_OBSERVATION.json").write_text(
        json.dumps(observation, indent=2) + "\n", encoding="utf-8")
    if selected == "MISSING_TOP":
        assert rc != 0, observation
        assert not rpt.exists(), observation
        return
    assert rc == 0, observation
    assert root is not None and root.tag == "report-database", observation
    assert root.findtext("top-cell") == selected, observation
    if selected == "CLEAN_TOP":
        assert count == (0, {}), observation
    else:
        assert count == (1, {"FORBIDDEN_LAYER": 1}), observation
