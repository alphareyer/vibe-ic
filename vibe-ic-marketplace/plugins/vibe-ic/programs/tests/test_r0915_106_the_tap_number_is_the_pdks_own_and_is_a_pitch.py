"""The tap number is the PDK's own, and it is a PITCH. R-0915-106.

(a) `PdkConfig.tapcell_distance_um` defaults to 14.0 and says so in its own
comment: "SKY130 latch-up rule typical". MEASURED on subservient x gf180mcuD:
that SKY130 constant was the budget in use on a gf180mcuD die while gf180mcuD
declares its own number one directory away --
`libs.tech/librelane/config.tcl:108`, `set ::env(FP_TAPCELL_DIST) 20` (read
back from the pinned image 0.3.67). The PDK's KLayout deck states no numeric
tap-spacing rule at all: `layers_def.drc:191` defines `latchup_mk` (137/5) as
a MARKER layer it counts. So the PDK's own flow configuration is the authority.

(b) OpenROAD's own help text reads `-distance dist` as the distance BETWEEN
tapcells, and the PDK's reference flow passes FP_TAPCELL_DIST straight to it.
It is a PITCH. Taps on a 20 um pitch put every point within 10 um of one, so
the coverage radius is pitch/2. The flow previously used one number as both,
making the coverage test twice as permissive as the insertion it checked.

Both directions: a PDK that declares nothing falls back to the registry value
and SAYS so; the override used by measurement arms is disclosed as an
override; and the emitted deck carries the pitch, the radius and the source.
"""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _pdk(tech_lef="/pdk/root/gf180mcuD/libs.ref/x/lef/y.lef"):
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef=tech_lef, cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fixture_fd_sc__filltie")


def test_the_radius_is_the_declared_distance():
    """R-0915-106(b) read the number as a pitch and halved it; R-0915-108
    corrected that from the tool's own output -- `-distance D` lays ties 2D
    apart in a row with the phase alternating, so D IS the max point-to-tap
    distance. Counted at D/2 the tool's untouched layout read 1346 of 2622
    uncovered."""
    assert R.tapcell_coverage_radius_um(20.0) == 20.0
    assert R.tapcell_coverage_radius_um(15.0) == 15.0


def test_the_pdks_own_declaration_is_read(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/gf180mcuD")
    monkeypatch.setattr(
        R, "_read_pdk_text",
        lambda path, container=None: ("set ::env(FP_TAPCELL_DIST) 20\n"
                                      if "librelane" in path else None))
    value, why = R.pdk_declared_tapcell_pitch_um(_pdk())
    assert value == 20.0
    assert why == "libs.tech/librelane/config.tcl:FP_TAPCELL_DIST"


def test_a_pdk_that_declares_nothing_falls_back_and_says_so(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    monkeypatch.setattr(R, "_read_pdk_text", lambda path, container=None: "")
    value, why = R.tapcell_pitch_um(_pdk())
    assert value == 14.0
    assert "registry fallback" in why and "declares none" in why


def test_an_unresolvable_pdk_directory_is_not_a_declaration(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "")
    value, why = R.pdk_declared_tapcell_pitch_um(_pdk())
    assert value is None and "no PDK directory" in why


def test_a_zero_or_junk_declaration_is_refused(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    for text in ("set ::env(FP_TAPCELL_DIST) 0\n",
                 "set ::env(FP_TAPCELL_DIST) abc\n",
                 "# FP_TAPCELL_DIST is not set here\n"):
        monkeypatch.setattr(R, "_read_pdk_text",
                            lambda path, container=None, t=text: t)
        assert R.pdk_declared_tapcell_pitch_um(_pdk())[0] is None


def test_the_measurement_override_is_disclosed_as_one(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    monkeypatch.setattr(R, "_read_pdk_text", lambda path, container=None: "")
    monkeypatch.setenv(R._TAP_PITCH_ENV, "20")
    value, why = R.tapcell_pitch_um(_pdk())
    assert value == 20.0 and "override" in why, \
        "a report produced under an override must never read as PDK-declared"


def test_a_junk_override_does_not_take_effect(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    monkeypatch.setattr(R, "_read_pdk_text", lambda path, container=None: "")
    for bad in ("", "  ", "0", "-3", "not-a-number"):
        monkeypatch.setenv(R._TAP_PITCH_ENV, bad)
        assert R.tapcell_pitch_um(_pdk())[0] == 14.0


# ── the emitted decks ─────────────────────────────────────────────────────
def test_insertion_uses_the_pitch_and_names_its_source():
    tcl = R._build_tapcell_tcl(_pdk(), 20.0, "PDK-declared (config.tcl)")
    assert "tapcell -distance 20.0" in tcl
    assert "source=PDK-declared (config.tcl)" in tcl


def test_the_repair_carries_pitch_radius_and_source():
    tcl = R._build_welltie_coverage_repair_tcl(
        _pdk(), 20.0, "libs.tech/librelane/config.tcl:FP_TAPCELL_DIST")
    assert "pitch=20.0um" in tcl
    assert "radius=20.0um" in tcl   # R-0915-108: the radius IS the distance
    assert "FP_TAPCELL_DIST" in tcl
    assert "set _wtpitch" in tcl and "set _wtrad" in tcl


def test_the_anchors_are_derived_from_where_the_tool_put_the_ties():
    """R-0915-108 replaced the pitch GRID with the exact extremal points --
    row ends and midpoints between consecutive ties -- because a grid anchored
    on the row origin falls out of phase with OpenROAD's two-phase
    checkerboard (first tie 19.6 um in on half the rows, 39.2 on the rest)."""
    tcl = R._build_welltie_coverage_repair_tcl(_pdk(), 20.0, "x")
    assert "lappend _wtpts $_wtrx0($_wty) $_wtrx1($_wty)" in tcl
    assert "[lindex $_wtc [expr {$_wti - 1}]] + [lindex $_wtc $_wti]" in tcl
    assert "foreach {_wtax0 _wtax1} $_wtanc($_wty)" not in tcl, \
        "the per-instance anchor model is gone"
    assert "for {set _wtpx $_wtrx0($_wty)}" not in tcl, \
        "and so is the row-origin grid"


def test_the_search_window_is_the_radius():
    tcl = R._build_welltie_coverage_repair_tcl(_pdk(), 20.0, "x")
    assert "set _wtmaxk [expr {$_wtrad / $_wtsw}]" in tcl
    assert "> $_wtrad} { continue }" in tcl


def test_an_unplaceable_pitch_point_names_the_cell_it_sits_inside():
    tcl = R._build_welltie_coverage_repair_tcl(_pdk(), 20.0, "x")
    assert "inside [$_wtin getName]" in tcl
    assert "row_edge_clips_the_window" in tcl


def test_a_pdk_with_no_tapcell_master_still_emits_no_repair():
    pdk = R.PdkConfig(name="p", liberty="/l", tech_lef="/t", cell_lef="/c",
                      cell_gds=None, site="S", drc_deck=None, metal_prefix="M")
    assert "WELLTIE_COVERAGE_REPAIR_SKIPPED" in \
        R._build_welltie_coverage_repair_tcl(pdk, 20.0, "x")


def test_the_emitted_deck_is_balanced_tcl():
    """It was not, and OpenROAD said so at the END of the file.

    MEASURED 2026-09-21: the pitch-grid rewrite left the pitch-point loop
    unclosed, so `pnr.tcl` was one `}` short. OpenROAD parses the whole file
    before running it, so the failure surfaced as
    `[ERROR STA-0341] incomplete command at end of file` at
    `postroute_spef_extract` -- a stage with nothing to do with well ties --
    and killed three measurement arms at once, before the SDR stage they were
    launched to exercise. A brace count is cheap; a wasted arm is not.
    """
    for pitch in (None, 14.0, 20.0):
        deck = R._build_welltie_coverage_repair_tcl(_pdk(), pitch, "x")
        bal = sum(l.count("{") - l.count("}") for l in deck.splitlines())
        assert bal == 0, f"pitch={pitch}: deck is {bal:+d} braces"


def test_every_tap_deck_this_module_emits_is_balanced_tcl():
    pdk = _pdk()
    for deck in (R._build_tapcell_tcl(pdk, 20.0, "x"),
                 R._build_welltie_coverage_repair_tcl(pdk, 20.0, "x"),
                 R._build_escalating_legalize_tcl(
                     "M", "_m",
                     tie_recover_tcl=R._build_welltie_coverage_repair_tcl(
                         pdk, 20.0, "x"))):
        bal = sum(l.count("{") - l.count("}") for l in deck.splitlines())
        assert bal == 0, f"unbalanced by {bal:+d}"
