"""U22 (tail-b): the PERC sweep takes the well tap and the pad roles from the LEF CLASS and
the PDK's WELLTAP_CELL, never from a cell-name token, and counts pads/ESD per instance.

RED on main, measured on the spm IC/DIE run (cx_spmic2_run, 8HD-4, 2026-09-28),
reports/phase3/perc_sweep.json:
  * welltap: status WELLTAP_GAP, reason ZERO_TAPS, n_tap 0 -- on a routed DEF with 36,186
    instances of the PDK's `CLASS core WELLTAP` cell, configured as the run's WELLTAP_CELL;
    the vintage guard then called the run a STALE pre-tapcell artefact;
  * esd_presence: pads 8, esd_cells 0, MISSING ("Likely ESD GAP") -- 8 distinct IO MASTERS,
    four of them spacers/corners, and MISSING read from names that do not spell a clamp.
The fixture is that run's DEF (component subset), its staged IO LEFs and one LibreLane config.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import perc_corpus_sweep as S

FIX = Path(__file__).parent / "fixtures" / "u22_perc_sweep_lef_class"
CFG = "phase3/librelane/15-floorplan/02-openroad-floorplan/config.json"


def _copy(tmp_path) -> Path:
    p = tmp_path / "run"
    shutil.copytree(FIX, p)
    return p


def _row(p: Path) -> dict:
    rows = S.sweep_dirs([str(p)])
    assert len(rows) == 1 and rows[0]["perc_chain_ran"]
    return rows[0]


def test_pdk_welltap_cell_is_a_tap(tmp_path):
    r = _row(_copy(tmp_path))
    assert r["welltap"]["status"] == "WELLTAP_PRESENT", r["welltap"]
    assert r["welltap"]["n_tap"] == 40
    assert r["vintage"]["verdict"] == "OK"


def test_lef_class_core_welltap_is_a_tap_without_the_config(tmp_path):
    """The LEF CLASS alone is enough: no WELLTAP_CELL config, a std-cell LEF whose
    tap MACRO says `CLASS core WELLTAP` (the PDK LEF's own text, reduced)."""
    p = _copy(tmp_path)
    (p / CFG).unlink()
    tap = json.loads((FIX / CFG).read_text())["WELLTAP_CELL"]
    (p / "phase3/stage3/extracted/stdcell.lef").write_text(
        f"VERSION 5.7 ;\nMACRO {tap}\n  CLASS core WELLTAP ;\n  SIZE 1.12 BY 3.92 ;\n"
        f"END {tap}\nEND LIBRARY\n")
    r = _row(p)
    assert r["welltap"]["status"] == "WELLTAP_PRESENT"
    assert r["physical_classes"]["welltap_from_lef_class"] == [tap]
    assert r["physical_classes"]["welltap_from_pdk_config"] == []


def test_no_lef_class_and_no_config_is_still_a_gap(tmp_path):
    """NEGATIVE ARM: with neither source the tap is not recognised (no name literal)."""
    p = _copy(tmp_path)
    (p / CFG).unlink()
    r = _row(p)
    assert r["welltap"]["status"] == "WELLTAP_GAP"


def test_pad_counts_are_instances_by_lef_class(tmp_path):
    e = _row(_copy(tmp_path))["esd_presence"]
    assert e["source"] == "lef_class"
    assert (e["signal_pads"], e["power_pads"], e["pads"]) == (36, 2, 38)
    assert e["structural"] == 4 + 24          # 4 corners + 24 IO spacers, never pads
    assert len(e["pad_masters"]) == 4


def test_esd_unstated_is_not_determined_not_missing(tmp_path):
    e = _row(_copy(tmp_path))["esd_presence"]
    assert e["esd_presence"] == "NOT_DETERMINED"
    assert e["esd_unidentified"] == 38 and e["esd_cells"] == 0
    assert e["status"] == "MANUAL_REVIEW"


def test_census_negation_and_identification_arms():
    classes = {"x_noesd_pad": "PAD INOUT", "x_gpiov2_pad": "PAD INOUT",
               "corner": "ENDCAP TOPLEFT", "sp": "PAD SPACER"}
    only_neg = S.pad_ring_census([("a", "x_noesd_pad"), ("c", "corner")], classes)
    assert only_neg["esd_presence"] == "MISSING" and only_neg["pads"] == 1
    both = S.pad_ring_census([("a", "x_noesd_pad"), ("b", "x_gpiov2_pad"),
                              ("s", "sp")], classes)
    assert both["esd_presence"] == "PRESENT" and both["esd_cells"] == 1
    assert both["structural"] == 1
    assert S.pad_ring_census([("a", "core_cell")], classes) == {"source": None}


def _drop_macros(p: Path, masters) -> None:
    """Remove the named MACRO blocks from every staged LEF (their CLASS unknown)."""
    import re
    for lef in (p / "phase3/stage3/extracted").glob("*.lef"):
        t = lef.read_text()
        for m in masters:
            t = re.sub(rf"^MACRO {re.escape(m)}\n.*?^END {re.escape(m)}\n", "", t,
                       flags=re.M | re.S)
        lef.write_text(t)


def test_ring_with_corners_but_no_classified_pad_is_not_determined(tmp_path):
    """Round-2 MAJOR: classified corners/spacers with no bond-pad LEF CLASS is a pad ring
    whose pads are unclassified -- NOT_DETERMINED naming them, never N/A (which also
    skipped the ESD topology check)."""
    p = _copy(tmp_path)
    pads = ["gf180mcu_fd_io__in_c", "gf180mcu_fd_io__bi_24t",
            "gf180mcu_fd_io__dvdd", "gf180mcu_fd_io__dvss"]
    _drop_macros(p, pads)
    r = _row(p)
    e = r["esd_presence"]
    assert e["source"] == "lef_class"
    assert e["status"] == "MANUAL_REVIEW" and e["esd_presence"] == "NOT_DETERMINED"
    assert e["pads"] == 0 and e["structural"] == 28
    assert sorted(e["unclassified_masters"]) == sorted(pads)
    assert "esd_topology" in r


def test_bare_pad_of_class_block_beside_a_corner_is_not_na():
    """The sky130-shape case: a bare (no-ESD) pad shipped as CLASS BLOCK next to a
    classified corner and spacer."""
    classes = {"lib_io__bare_pad": "BLOCK", "lib_io__corner_pad": "ENDCAP TOPRIGHT",
               "lib_io__com_bus_slice": "PAD SPACER"}
    got = S.pad_ring_census([("p0", "lib_io__bare_pad"), ("c0", "lib_io__corner_pad"),
                             ("s0", "lib_io__com_bus_slice")], classes)
    assert got["esd_presence"] == "NOT_DETERMINED" and got["status"] == "MANUAL_REVIEW"
    assert got["unclassified_masters"] == ["lib_io__bare_pad"]
