#!/usr/bin/env python3
"""TAPEOUT-SIGNOFF P0 (LVS) — the tapeout tier must NOT credit a POWER_PIN_ONLY
waiver as a genuine LVS match, and must FAIL a real signal-net mismatch.

This is the honest half of the load-bearing LVS gap: the triage-tier
POWER_PIN_ONLY→LVS_MATCH_POWER_AWARE waiver (in _run_extraction_lvs) is fine for
triage, but a tape-out is defined by a GENUINE match. The tapeout gate refuses the
waiver. (The root fix — a power-aware gate netlist that reaches a genuine match — is
the tracked follow-up.)
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import lvs_tapeout_signoff_check as G  # noqa: E402


# A genuine netgen unique-match transcript.
_MATCH = """\
Subcircuit summary:
Circuit 1: spm                          |Circuit 2: spm
Number of devices: 812                  |Number of devices: 812
Netlists match uniquely.
Final result: Circuits match uniquely.
"""

# A power-pin-only mismatch (the universal sky130 OSS artifact): the only failing
# rows are power/tie nets; no signal-net evidence.
_POWER_PIN_ONLY = """\
Top level cell failed pin matching.
Cell pin lists for spm and spm do not match.
  VPWR|(no matching pin)
  VGND|(no matching pin)
  VPB |(no matching pin)
  VNB |(no matching pin)
Final result: Netlists do not match.
"""

# A real signal-net mismatch: `(no pin, node is …)` rows (the aes 517-row class).
_SIGNAL_NET = """\
Top level cell failed pin matching.
Cell pin lists for aes and aes do not match.
  (no pin, node is /_54440_/Y)
  (no pin, node is /_54441_/Y)
  data_out[7]|(no matching pin)
Final result: Netlists do not match.
"""


def test_genuine_match_passes():
    res = G.evaluate(_MATCH)
    assert res["tapeout_verdict"] == "GENUINE_MATCH"
    assert res["passed"] is True


def test_power_pin_only_is_not_a_tapeout_pass():
    # §4.05 (the whole point): POWER_PIN_ONLY is a triage waiver, NEVER a tapeout
    # pass — it must NOT release the tapeout tier.
    res = G.evaluate(_POWER_PIN_ONLY)
    assert res["tapeout_verdict"] == "WAIVED_PENDING_POWER_AWARE"
    assert res["passed"] is False
    assert "power-aware" in res["detail"]


def test_signal_net_mismatch_fails():
    res = G.evaluate(_SIGNAL_NET)
    assert res["tapeout_verdict"] == "SIGNAL_NET_MISMATCH"
    assert res["passed"] is False


def test_incomplete_run_fails_not_pass():
    # a truncated hierarchical run (match line but no Final result) is INCOMPLETE.
    incomplete = "Cell spm matches uniquely.\n(no Final result line)\n"
    res = G.evaluate(incomplete)
    assert res["tapeout_verdict"] == "INCOMPLETE"
    assert res["passed"] is False


def test_absent_report_is_io_error(tmp_path):
    res = G.check(tmp_path / "nope")
    assert res["tapeout_verdict"] == "IO_ERROR"
    assert res["passed"] is False


def test_check_reads_from_dir_and_main_exit_codes(tmp_path):
    (tmp_path / "lvs.rpt").write_text(_MATCH)
    assert G.main([str(tmp_path)]) == 0                    # genuine match → PASS
    ppo = tmp_path / "ppo"
    ppo.mkdir()
    (ppo / "comp.out").write_text(_POWER_PIN_ONLY)
    assert G.main([str(ppo)]) == 1                         # waived-pending → not a pass
    assert G.main([str(tmp_path / "absent")]) == 2         # IO error


def test_consistency_with_lvs_verdict_tokens():
    # the tapeout gate must agree with the shared classifier's base verdict.
    import lvs_verdict_tokens as lvt
    assert lvt.classify(_MATCH) == "MATCH"
    assert lvt.classify(_POWER_PIN_ONLY) == "MISMATCH"
    assert lvt.mismatch_class(_POWER_PIN_ONLY) == "POWER_PIN_ONLY"
    assert lvt.mismatch_class(_SIGNAL_NET) == "SIGNAL_NET_MISMATCH"


# A unique match reached only because the layout side carried EMPTY cell views:
# netgen compares each such cell by its pin list alone (the gate-level LVS shape
# a power-aware DEF-derived netlist against an abstract extraction produces).
_PLACEHOLDER_MATCH = """\
Warning: Equate pins:  cell gf180mcu_fd_sc_mcu7t5v0__dffq_1 is a placeholder, treated as a black box.
Cell gf180mcu_fd_sc_mcu7t5v0__dffq_1 (0) disconnected node: D
Warning: Equate pins:  cell gf180mcu_fd_sc_mcu7t5v0__nand2_1 is a placeholder, treated as a black box.
Warning: Equate pins:  cell gf180mcu_fd_sc_mcu7t5v0__dffq_1 is a placeholder, treated as a black box.
Subcircuit summary:
Circuit 1: chip_top                     |Circuit 2: chip_top
Cell pin lists are equivalent.
Device classes chip_top and chip_top are equivalent.

Final result: Circuits match uniquely.
"""

# A full-GDS device extraction: netgen names the PDK MOSFET subcircuits as black
# boxes (device models), and every standard cell is compared device-by-device.
_FULL_EXTRACTION_MATCH = """\
Circuit 1 cell nfet_06v0 and Circuit 2 cell nfet_06v0 are black boxes.
Circuit 1 cell pfet_06v0 and Circuit 2 cell pfet_06v0 are black boxes.
Number of devices: 192                     |Number of devices: 192
Device classes gf180mcu_fd_io__in_c and gf180mcu_fd_io__in_c[[0]] are equivalent.
Device classes chip_top and chip_top are equivalent.

Final result: Circuits match uniquely.
"""


def test_placeholder_blackbox_match_is_not_a_tapeout_pass():
    res = G.evaluate(_PLACEHOLDER_MATCH)
    assert res["base_verdict"] == "MATCH"
    assert res["tapeout_verdict"] == "GATE_LEVEL_BLACKBOX"
    assert res["passed"] is False
    assert res["placeholder_cells"] == [
        "gf180mcu_fd_sc_mcu7t5v0__dffq_1", "gf180mcu_fd_sc_mcu7t5v0__nand2_1"]


def test_primitive_device_blackboxes_in_full_extraction_still_pass():
    res = G.evaluate(_FULL_EXTRACTION_MATCH)
    assert res["tapeout_verdict"] == "GENUINE_MATCH"
    assert res["passed"] is True


def test_placeholder_blackbox_main_exit_code(tmp_path):
    (tmp_path / "lvs.rpt").write_text(_PLACEHOLDER_MATCH)
    assert G.main([str(tmp_path)]) == 1
