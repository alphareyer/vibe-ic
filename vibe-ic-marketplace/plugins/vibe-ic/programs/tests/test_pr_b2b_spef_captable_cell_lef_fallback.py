"""PR-B2b (#180) — captable discovery falls back to the cell LEF when the tech
LEF was staged out of a /libs.ref/ path (e.g. asap7's normalized tech LEF).

The primary captable derivation slices the PDK root from the TECH-LEF path's
`/libs.ref/` substring. A named PDK whose tech LEF is staged into the project
(asap7, normalized for negative OFFSETs) no longer carries `/libs.ref/`, so the
primary derivation finds nothing. The fix adds a fallback that derives the PDK
root from the CELL LEF (always under <PDK>/libs.ref/ in-container). Guards:
(a) the fallback block IS emitted with the cell-LEF path when cell_lef_c is given,
(b) sky130/gf180-style callers are unaffected — the fallback is guarded by
    `_prs_rules eq ""` so it only runs when the primary derivation missed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402


# F13b: the post-route deck no longer derives a PDK root from LEF paths in
# Tcl -- it is handed the ruleset the PDK DECLARES, whose root comes from
# `_pdk_root_c`, which carries this same cell-LEF fallback on the Python side.
# The property is asserted there, and on the deck: a staged tech LEF changes
# nothing about the ruleset it extracts with.
def _declared(path: str) -> dict:
    return {"status": "DECLARED", "declaration": ["config.tcl"],
            "corners": {"nom": {"path": path, "pattern": "nom_*",
                                "declared_by": "config.tcl:RCX_RULES"}},
            "detail": ""}


def test_fallback_block_emitted_with_cell_lef():
    clef = "/foss/pdks/asap7/libs.ref/asap7sc7p5t/lef/asap7sc7p5t_28.lef"
    pdk = R.PdkConfig(name="fixture_pdk", liberty="/work/lib.lib",
                      tech_lef="/work/staged_tech.lef", cell_lef=clef,
                      cell_gds=None, site="s", drc_deck=None,
                      metal_prefix="M", tapcell_master="t",
                      antenna_diode_cell=None, pnr_exclude_cell_file=None)
    # the staged tech LEF has no /libs.ref/; the cell LEF supplies the root
    assert R._pdk_root_c(pdk) == "/foss/pdks/asap7"
    rules = "/foss/pdks/asap7/libs.tech/librelane/rules.openrcx.asap7.nom"
    tcl = R._post_route_spef_repair_tcl("/work/out", "/work/staged_tech.lef",
                                        clef, rcx_declaration=_declared(rules))
    assert f"set _prs_rules {{{rules}}}" in tcl


def test_default_no_cell_lef_is_backward_compatible():
    # legacy 2-arg style still works (cell_lef_c defaults to "")
    tcl = R._post_route_spef_repair_tcl("/work/out", "/work/tech.lef")
    assert isinstance(tcl, str) and tcl


def test_fallback_globs_the_same_librelane_captable_convention():
    """Staged or not, both LEF roots land on the SAME declared ruleset."""
    clef = "/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lef/x.lef"
    rules = "/foss/pdks/gf180mcuD/libs.tech/librelane/rules.openrcx.gf180mcuD.nom"
    staged = R._post_route_spef_repair_tcl(
        "/work/out", "/work/staged.lef", clef, rcx_declaration=_declared(rules))
    native = R._post_route_spef_repair_tcl(
        "/work/out", "/foss/pdks/gf180mcuD/libs.ref/tech/t.lef", clef,
        rcx_declaration=_declared(rules))
    assert f"set _prs_rules {{{rules}}}" in staged
    assert "glob -nocomplain" not in staged
    assert staged.replace("/work/staged.lef", "") == native.replace(
        "/foss/pdks/gf180mcuD/libs.ref/tech/t.lef", "")
