"""Regression for run7's promoted-route device-layer fill loss."""
from __future__ import annotations

import inspect
from pathlib import Path
import sys


PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402


MASTERS = [
    "gf180mcu_fd_sc_mcu7t5v0__fillcap_64",
    "gf180mcu_fd_sc_mcu7t5v0__fill_64",
    "gf180mcu_fd_sc_mcu7t5v0__fill_1",
]


def _deck(**fill_context: bool) -> str:
    return R._ship_signoff_spef_repair_tcl(
        "chip_top", "/pdk/tech.lef", "/pdk/cells.lef", "/pdk/ss.lib",
        "/work/pnr", "/pdk/max.captable", "Metal", 8,
        filler_masters=MASTERS, **fill_context)


def test_pad_wrapper_repair_restores_device_free_active_row_fill():
    tcl = _deck(sparse_active_row_fill=True)
    assert "remove_fillers" in tcl
    assert "SPARSE_DIE_ACTIVE_ROW_FILL:" in tcl
    assert "selector=functional_core_mterm" in tcl
    assert "VIBEIC_ACTIVE_ROW_FILL_" in tcl
    below = tcl[tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL:"):
                tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL_DONE:")]
    assert "fillcap_64" not in below
    assert "fill_64" in below
    assert tcl.index("remove_fillers") < tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL:")
    # NAME THE ARTEFACT, not the first bare `write_def` token, because the bare
    # token DOES NOT MATCH A COMMAND — it matches whatever mentions the word first,
    # comments included. MEASURED on the vibe-ic#2171 deck: the first occurrence of
    # `write_def` is at offset 11255 and it is inside a COMMENT
    # ("the marker is emitted ONLY when write_def actually succeeded"); the first
    # actual command is 112 bytes later at 11367. The CONTROL settles it — delete
    # the checkpoint COMMAND entirely and leave only that comment, and the old
    # assertion is STILL red (`assert 21441 < 11255`). A sentence of prose could
    # fail this file, and did.
    #
    # The property is that the route the run SHIPS is written with its device-layer
    # fill restored, and the shipped route is `routed_repaired.def` BY NAME. The
    # per-pass convergence checkpoints are deliberately taken PRE-fill: the loop
    # runs with `remove_fillers` in force because a fully tiled core leaves
    # `detailed_placement` no legal site, and `_ship_cvg_restore_tcl` puts the fill
    # back itself before it writes anything it ships (pinned below).
    assert (tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL_DONE:")
            < tcl.index("write_def /work/pnr/routed_repaired.def"))
    # ...and the sibling arm, so the checkpoint write can never drift into the
    # shipped position without this file noticing: every convergence checkpoint is
    # written INSIDE the loop, i.e. before the refill block runs.
    assert (tcl.index("write_def /work/pnr/ship_cvg_pass${_cvg}.def")
            < tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL:"))


def test_context_absence_keeps_the_bounded_skip_arm():
    tcl = _deck()
    assert "SPARSE_DIE_FILL_SKIPPED:" in tcl
    assert "SPARSE_DIE_ACTIVE_ROW_FILL:" not in tcl


def test_production_step_derives_and_forwards_all_three_floorplan_facts():
    source = inspect.getsource(R.step_signoff_spef_repair)
    for token in (
        "_slot_geometry(project)",
        "_l9_declared_die_area(project)",
        "_l19_declared_die_area(project)",
        "_padring_core_inset_um(project)",
        "slot_pinned_core=_repair_slot is not None",
        "design_declared_die=_repair_declared_die",
        "sparse_active_row_fill=bool(",
    ):
        assert token in source


def test_the_restore_deck_refills_before_the_route_it_ships():
    """vibe-ic#2171 — the convergence checkpoint is pre-fill BY DESIGN, so the
    session that restores one has to put the fill back itself.

    Without this arm the pair is only half-checked: the repair deck would be proven
    to refill before it ships, and the restore deck — which writes a route the flow
    can promote in exactly the same way — would be proven to do nothing at all.
    It also pins that the restore session does NOT clear fill, because the
    checkpoint it reads never had any."""
    tcl = R._ship_cvg_restore_tcl(
        "chip_top", "/pdk/tech.lef", "/pdk/cells.lef", "/pdk/ss.lib",
        "/work/pnr", "/pdk/max.captable", "Metal", 8,
        "/work/pnr/ship_cvg_pass2.def",
        filler_masters=MASTERS, sparse_active_row_fill=True)
    assert "SPARSE_DIE_ACTIVE_ROW_FILL:" in tcl
    assert "remove_fillers" not in tcl
    assert (tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL_DONE:")
            < tcl.index("write_def /work/pnr/routed_cvg_restored.def"))
    assert (tcl.index("SPARSE_DIE_ACTIVE_ROW_FILL_DONE:")
            < tcl.index("write_verilog /work/pnr/chip_top_pnr_cvg_restored.v"))
