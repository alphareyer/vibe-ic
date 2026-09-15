#!/usr/bin/env python3
"""repair_design must be given a DRV limit it can actually violate.

MEASURED (opentitan_aes x sky130A, 2026-09-15). The flow emitted
`set_max_capacitance 5.0` — the library's MAX characterised output-pin
max_capacitance, i.e. the STRONGEST driver's rated load — and no
`set_max_fanout` at all, because sky130's liberty declares no
`default_max_fanout`. The consequence, on the worst SS setup path:

    _27924_  sky130_fd_sc_hd__o2111ai_1 (minimum drive) -> 399 sinks -> 21.460 ns
    _28118_  sky130_fd_sc_hd__nor4b_1   (minimum drive) ->  80 sinks -> 10.100 ns

Two stages, 31.56 ns, 48% of a 65.63 ns path whose MEDIAN stage was 0.570 ns.
`repair_design` ran and reported a no-op: a 399-sink net on the weakest gate in
the library violated nothing, because a design-wide 5.0 pF ceiling is looser
than that cell's OWN liberty max_capacitance and there was no fanout limit.

Two rules follow, and both are about not lying to the resizer:
  * a design-wide `set_max_capacitance` taken from the strongest driver's
    ceiling is WORSE than none — it OVERRIDES every weak driver's own, tighter,
    per-cell limit. Emit none and let each driver's liberty value govern.
  * a missing fanout limit is not neutrality. Fall back to a conservative flow
    default, and SAY it is the flow's, never the library's.

Fixtures are synthetic liberty/SDC text: no PDK file is read and no real
design name appears, so a fix that special-cases a PDK cannot pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as P3      # noqa: E402

#: Resolved tolerantly so the PRE-FIX tree fails on the ANSWER rather than on
#: an AttributeError. An arity/attribute red proves nothing about the contract.
_FLOW_DEFAULT = getattr(P3, "FLOW_DEFAULT_MAX_FANOUT", None)


def _lib(tmp_path: Path, body: str) -> str:
    p = tmp_path / "fixture_corner.lib"
    p.write_text(body, encoding="utf-8")
    return str(p)


#: A library shaped like the real open PDK: a slew default, NO cap default,
#: NO fanout default, and per-pin max_capacitance values spanning weak..strong.
_NO_DEFAULTS = """
library (fixture_corner) {
  default_max_transition : 1.5 ;
  cell (fixture_weak) { pin (Y) { direction : output ; max_capacitance : 0.09 ; } }
  cell (fixture_strong) { pin (Y) { direction : output ; max_capacitance : 5.0 ; } }
}
"""

_WITH_DEFAULTS = """
library (fixture_corner) {
  default_max_transition : 0.75 ;
  default_max_capacitance : 0.20 ;
  default_max_fanout : 8 ;
  cell (fixture_strong) { pin (Y) { direction : output ; max_capacitance : 5.0 ; } }
}
"""


# ───────── the library declares nothing: flow default, honestly labelled ─────

def test_no_library_defaults_yields_a_real_fanout_limit(tmp_path):
    out = P3._liberty_drv_limits(_lib(tmp_path, _NO_DEFAULTS))
    assert out["max_fanout"] == _FLOW_DEFAULT is not None, (
        "a liberty with no default_max_fanout left repair_design with no "
        "fanout target at all — the 399-sink case")
    assert "flow default" in str(out["fanout_source"]).lower()


def test_the_flow_default_is_never_attributed_to_the_library(tmp_path):
    """A false provenance is the failure this disclosure exists to prevent."""
    out = P3._liberty_drv_limits(_lib(tmp_path, _NO_DEFAULTS))
    note = str(out["note"]) + str(out["fanout_note"])
    assert "flow" in note.lower()
    assert "default_max_fanout" not in str(out["fanout_source"])


def test_the_strongest_drivers_ceiling_is_not_imposed_as_a_cap(tmp_path):
    """5.0 pF is in the library; it must NOT become set_max_capacitance."""
    out = P3._liberty_drv_limits(_lib(tmp_path, _NO_DEFAULTS))
    assert out["max_capacitance_pf"] is None, (
        "the library's strongest-driver ceiling was imposed design-wide; it "
        "overrides every weak driver's own tighter per-cell limit")
    assert out["observed_max_pin_capacitance_pf"] == 5.0, (
        "the observed ceiling must still be RECORDED — suppressing a limit is "
        "a disclosure, not a deletion")
    assert "not imposed" in str(out["cap_note"])


# ───────────── NEGATIVE CONTROL: a library that declares its own ─────────────

def test_library_declared_defaults_win_over_the_flow(tmp_path):
    out = P3._liberty_drv_limits(_lib(tmp_path, _WITH_DEFAULTS))
    assert out["max_fanout"] == 8, "the flow default displaced a real library fact"
    assert "default_max_fanout" in str(out["fanout_source"])
    assert out["max_capacitance_pf"] == 0.20, (
        "a real default_max_capacitance must still be used")
    assert "default_max_capacitance" in str(out["cap_source"])
    assert out["max_transition_ns"] == 0.75


# ───────────── NEGATIVE CONTROL: an SDC that declares its own ────────────────

def test_a_design_declared_sdc_is_left_byte_identical(tmp_path):
    sdc = ("current_design fixture_top\n"
           "set_max_transition 0.4 [current_design]\n"
           "set_max_capacitance 0.05 [current_design]\n"
           "set_max_fanout 4 [current_design]\n")
    text, info = P3._ensure_staged_sdc_drv(sdc, _lib(tmp_path, _NO_DEFAULTS))
    assert text == sdc, "a design-declared DRV limit was overridden"
    assert info["added_max_fanout"] is None
    assert info["added_max_capacitance"] is None
    assert info["added_max_transition"] is None
    assert set(info["design_declared"]) == {
        "set_max_transition", "set_max_capacitance", "set_max_fanout"}


def test_a_design_declared_fanout_alone_still_wins(tmp_path):
    """Partial declaration: the design's fanout stands, the rest is supplied."""
    sdc = ("current_design fixture_top\n"
           "set_max_fanout 4 [current_design]\n")
    text, info = P3._ensure_staged_sdc_drv(sdc, _lib(tmp_path, _NO_DEFAULTS))
    assert info["added_max_fanout"] is None, (
        "the flow default displaced the design's own fanout cap")
    # Count CONSTRAINT lines only: the disclosure comment legitimately names
    # `set_max_fanout` when it reports what the design already declared.
    fanout_lines = [l.strip() for l in text.splitlines()
                    if l.strip().startswith("set_max_fanout")]
    assert fanout_lines == ["set_max_fanout 4 [current_design]"], fanout_lines
    assert info["added_max_transition"] == 1.5


def test_the_supplied_sdc_carries_fanout_and_no_ceiling_cap(tmp_path):
    """The positive case, end to end on the emitted SDC text."""
    sdc = "current_design fixture_top\n"
    text, info = P3._ensure_staged_sdc_drv(sdc, _lib(tmp_path, _NO_DEFAULTS))
    assert info["added_max_fanout"] == _FLOW_DEFAULT is not None
    assert info["added_max_capacitance"] is None
    emitted = [l.strip() for l in text.splitlines()
               if l.strip().startswith("set_max_")]
    assert any(l.startswith("set_max_fanout") for l in emitted), emitted
    assert not any(l.startswith("set_max_capacitance") for l in emitted), (
        f"the strongest-driver ceiling was emitted into the SDC: {emitted}")
