"""D is the foundry's rule, the radius IS D, and coverage is per row.

R-0915-108. THE EVIDENCE THAT DECIDED IT: `wire70` -- the case the per-row
rule was written for -- was a PDK DECK rule, not this plugin's own instrument.
Read from the pinned image 0.3.67, gf180mcuD
`libs.tech/klayout/tech/drc/rule_decks/comp.rb`:

    :558  DF.13_LV  Max distance of Nwell tap ... is 20um
    :586  DF.13_MV  ... is 15um
    :614  DF.14_LV  Max distance of substrate tap ... is 20um
    :634  DF.14_MV  ... is 15um

and the spm run's sign-off deck put 60 of its 70 violations on DF.13_MV (41)
and DF.14_MV (19). Two of them sat 7.84 um from a tie ONE ROW BELOW and still
violated: the rule grows the tap INSIDE nwell and the neighbouring row's nwell
is a separate island.

TWO NUMBERS LIVE IN THIS PDK. `FP_TAPCELL_DIST` (20) is what the reference
FLOW asks its inserter for; DF.13/DF.14 is what the FOUNDRY requires of the
finished layout. For a 5 V library they differ by 5 um and the foundry's is
the one that fires -- subservient and spm use `gf180mcu_fd_sc_mcu7t5v0`, so
D is 15 um there.

AND THE RADIUS IS D, NOT D/2. MEASURED on the pristine post-tapcell DEF:
given `-distance 20` OpenROAD placed ties 39.2 um apart WITHIN A ROW, first
tie 19.6 um in on 56 rows and 39.2 um in on the other 56 -- a two-phase
checkerboard. D is already the max point-to-tap distance it builds for.
Counted at D/2 the tool's own untouched output read 1346 of 2622 uncovered.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

U = 2000


def _pdk(**kw):
    base = dict(name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
                cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
                tapcell_master="fixture_fd_sc__filltie")
    base.update(kw)
    return R.PdkConfig(**base)


_DECK = ("# Rule DF.13_LV: Max distance of Nwell tap is 20um.\n"
         "# Rule DF.13_MV: Max distance of Nwell tap is 15um.\n"
         "# Rule DF.14_LV: Max distance of substrate tap is 20um.\n"
         "# Rule DF.14_MV: Max distance of substrate tap is 15um.\n")


def _patch(monkeypatch, deck=_DECK, volts=5.0):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/gf180mcuD")
    monkeypatch.setattr(R, "_read_pdk_text",
                        lambda path, container=None: (deck if "comp.rb" in path
                                                      else ""))
    monkeypatch.setattr(R, "_pdk_nominal_voltage",
                        lambda pdk, container=None: volts)


# ── the number ────────────────────────────────────────────────────────────
def test_a_five_volt_library_reads_the_MV_rule(monkeypatch):
    _patch(monkeypatch, volts=5.0)
    D, why = R.deck_tap_max_distance_um(_pdk())
    assert D == 15.0
    assert "DF.13_MV/DF.14_MV" in why and "medium-voltage" in why


def test_a_low_voltage_library_reads_the_LV_rule(monkeypatch):
    _patch(monkeypatch, volts=1.8)
    D, why = R.deck_tap_max_distance_um(_pdk())
    assert D == 20.0
    assert "DF.13_LV/DF.14_LV" in why


def test_an_undetermined_class_reads_the_stricter(monkeypatch):
    """A coverage rule may not fail open."""
    _patch(monkeypatch, volts=None)
    D, why = R.deck_tap_max_distance_um(_pdk())
    assert D == 15.0
    assert "STRICTER" in why and "may not fail open" in why


def test_the_foundry_rule_outranks_the_reference_flow_knob(monkeypatch):
    """FP_TAPCELL_DIST is what the flow ASKS; DF.13/DF.14 is what the foundry
    REQUIRES. Both are present in this PDK and they differ by 5 um."""
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/gf180mcuD")
    monkeypatch.setattr(
        R, "_read_pdk_text",
        lambda path, container=None: (_DECK if "comp.rb" in path
                                      else "set ::env(FP_TAPCELL_DIST) 20\n"))
    monkeypatch.setattr(R, "_pdk_nominal_voltage",
                        lambda pdk, container=None: 5.0)
    D, why = R.tap_max_distance_um(_pdk())
    assert D == 15.0 and why.startswith("PDK DRC deck")


def test_without_a_deck_rule_the_flow_knob_is_used_and_disclosed(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    monkeypatch.setattr(
        R, "_read_pdk_text",
        lambda path, container=None: ("set ::env(FP_TAPCELL_DIST) 20\n"
                                      if "config.tcl" in path else ""))
    D, why = R.tap_max_distance_um(_pdk())
    assert D == 20.0
    assert "reference-flow knob" in why and "no DF.13/DF.14" in why


def test_with_neither_the_registry_value_is_disclosed_as_a_fallback(monkeypatch):
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk/root/x")
    monkeypatch.setattr(R, "_read_pdk_text", lambda path, container=None: "")
    D, why = R.tap_max_distance_um(_pdk())
    assert D == 14.0 and "registry fallback" in why


# ── the radius ────────────────────────────────────────────────────────────
def test_the_radius_is_the_distance_not_half_of_it():
    assert R.tapcell_coverage_radius_um(15.0) == 15.0
    assert R.tapcell_coverage_radius_um(20.0) == 20.0


# ── the coverage spec, and the calibration ────────────────────────────────
def test_a_pristine_insertion_at_D_leaves_only_the_row_ends():
    """What the reference insertion does and does not cover, exactly."""
    D = 15 * U
    x0, x1 = 0, 400 * U
    ties = list(range(D, x1, 2 * D))          # the tool's own construction
    unc = R.uncovered_row_points(x0, x1, ties, D)
    assert unc == [x1], "only the far row end is beyond D of every tie"


def test_the_calibration_reads_zero_once_the_flow_adds_its_row_end_ties():
    """THE CALIBRATION. 0 uncovered under this reading, after our insertion."""
    D = 15 * U
    x0, x1 = 0, 400 * U
    ties = sorted(list(range(D, x1, 2 * D)) + [x0, x1])
    assert R.uncovered_row_points(x0, x1, ties, D) == []


def test_a_row_with_no_tie_at_all_is_uncovered_not_silently_clean():
    D = 15 * U
    assert R.uncovered_row_points(0, 400 * U, [], D) == [0, 400 * U]


def test_a_gap_wider_than_two_D_is_uncovered_at_its_midpoint():
    D = 15 * U
    ties = [0, 400 * U]
    unc = R.uncovered_row_points(0, 400 * U, ties, D)
    assert unc == [200 * U]


# ── spec and implementation may not drift ─────────────────────────────────
def test_the_emitted_tcl_computes_the_same_three_kinds_of_point():
    tcl = R._build_welltie_coverage_repair_tcl(_pdk(), 15.0, "DF.13_MV")
    assert "lappend _wtpts $_wtrx0($_wty) $_wtrx1($_wty)" in tcl, "row ends"
    assert "[lindex $_wtc [expr {$_wti - 1}]] + [lindex $_wtc $_wti]" in tcl, \
        "midpoints between consecutive ties"
    assert "abs($_wtt - $_wtcx) <= $_wtrad" in tcl, "the same predicate"
    assert "radius=15.0um" in tcl


def test_the_per_row_rule_is_stated_with_its_deck_grounding():
    tcl = R._build_welltie_coverage_repair_tcl(_pdk(), 15.0, "DF.13_MV")
    for ln in [l for l in tcl.splitlines()
               if "abs(" in l and "_wtcx" in l and "_wtrad" in l]:
        assert "_wty" not in ln, f"the coverage test took a y term: {ln}"


def test_the_emitted_deck_is_balanced_tcl():
    for D in (None, 15.0, 20.0):
        tcl = R._build_welltie_coverage_repair_tcl(_pdk(), D, "x")
        bal = sum(l.count("{") - l.count("}") for l in tcl.splitlines())
        assert bal == 0, f"D={D}: {bal:+d} braces"
