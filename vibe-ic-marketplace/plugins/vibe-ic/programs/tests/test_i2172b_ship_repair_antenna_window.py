"""vibe-ic#2172 (cz2172b) — the route that SHIPS is the one that must be
antenna-checked.

MEASURED, subservient x gf180mcuD, image
ghcr.io/vibeic/vibeic-eda@sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49:

  * `pnr.tcl` ends antenna-CLEAN — the DEF its own `write_def` produces
    re-reads as 0 net / 0 pin violations in a fresh OpenROAD session.
  * `signoff_spef_repair` then re-opens that DEF, clears the routing of 3232
    nets, re-routes, and PROMOTES the result over `routed.def` / `<top>.def`,
    unlinking `<top>.gds` so the GDS is re-derived from it.
  * That promoted route carried TWO antenna violations (i_clk 871.94, net828
    401.02, both against a Metal3 side-area limit of 400), which the KLayout
    sign-off deck reported as 7 `ANT.16_ii_ANT.4` items on the shipped GDS.
  * The step's emitted TCL contained NO `check_antennas` and NO
    `repair_antennas`, and `_emit_antenna_report` read the PnR log while
    stamping the promoted DEF as its subject — so the flow published
    `antenna clean: YES` for a design the sign-off deck failed.

Replaying the UNMODIFIED step on the lane's own inputs reproduced its
`routed_repaired.def` byte-for-byte (md5 e078bdb7516bd24fb5e5f014096f41c1)
with 2 net violations; the same replay with the window below, and nothing else
changed, reached `SHIP_ANT_SEQUENCE: 2 0` and produced a DEF with 0, at a cost
of 6 ps of setup (SHIP_WNS_POSTROUTE -1.234340664617269 -> -1.2403163291940147).

Every assertion here fails against the pre-fix runner.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phase3_one_shot_runner as R  # noqa: E402


DIODE = "somepdk_fd_sc__antenna"


def _cvg(diode=DIODE):
    return R._ship_postroute_convergence_tcl(
        "/cap.model", "/pnr", antenna_diode_cell=diode)


def test_window_is_emitted_with_the_pdk_diode_master():
    tcl = _cvg()
    assert "SHIP_ANT_BEGIN" in tcl
    assert "check_antennas" in tcl
    assert f"repair_antennas {DIODE} -iterations 1" in tcl
    assert "-reroute" in tcl


def test_window_runs_after_the_last_reroute_and_before_the_final_measurement():
    """Placement is the whole point: after the convergence loop's reroute (so it
    sees the route that ships) and before the FINAL real-SPEF measurement (so
    SHIP_WNS_POSTROUTE, the number the promotion gate keys on, describes the
    route the antenna repair left behind)."""
    tcl = _cvg()
    last_reroute = tcl.rindex("detailed_route -droute_end_iter")
    window = tcl.index("SHIP_ANT_BEGIN")
    final_meas = tcl.index("FINAL honest post-reroute real-SPEF measurement")
    postroute_wns = tcl.rindex('puts "SHIP_WNS_POSTROUTE')
    assert last_reroute < window < final_meas < postroute_wns


def test_window_escalates_the_margin_and_publishes_its_sequence():
    tcl = _cvg()
    assert "-ratio_margin $_sa_margin" in tcl
    assert "set _sa_margin [expr {$_sa_margin + 10}]" in tcl
    assert "SHIP_ANT_SEQUENCE" in tcl
    # A residual is NAMED, never left as a silent stop.
    assert "SHIP_ANT_NOT_CONVERGED" in tcl


def test_window_stops_on_zero_and_on_no_progress():
    tcl = _cvg()
    assert "set _sa_stop CONVERGED" in tcl
    assert "set _sa_stop NO_PROGRESS" in tcl


def test_no_diode_cell_discloses_instead_of_silently_emitting_nothing():
    tcl = _cvg(diode=None)
    assert "SHIP_ANT_SKIPPED" in tcl
    assert "repair_antennas" not in tcl.split("SHIP_ANT_SKIPPED")[1][:400]


def test_window_carries_no_design_or_pdk_literal():
    """The diode master is the PDK's own; the generator itself names none."""
    src = R._ship_antenna_window_tcl.__doc__ or ""
    body = R._ship_antenna_window_tcl(DIODE)
    for token in ("gf180", "sky130", "subservient", "i_clk", "net828"):
        assert token not in body, token
        assert token not in src, token


def test_the_shipped_tcl_carries_the_window():
    tcl = R._ship_signoff_spef_repair_tcl(
        "top", "/t.tlef", "/c.lef", "/ss.lib", "/pnr", "/cap.model", "Metal",
        4, filler_masters=["FILL_1"], antenna_diode_cell=DIODE)
    assert "SHIP_ANT_BEGIN" in tcl
    # ... and it is inside the window where the fillers are already removed.
    assert tcl.index("SHIP_RMFILL_NONFATAL") < tcl.index("SHIP_ANT_BEGIN")
    assert tcl.index("SHIP_ANT_END") < tcl.rindex("filler_placement")
    assert tcl.index("SHIP_ANT_END") < tcl.index("routed_repaired.def")


def _write_run(tmp_path, *, ship_log: str, promoted: bool,
               pnr_log_extra: str = "") -> Path:
    proj = tmp_path / "proj"
    pnr = proj / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / "top.def").write_text("DESIGN top ;\nEND DESIGN\n")
    (pnr / "openroad.log").write_text(
        "PNR_STAGE: postroute_antenna_repair\n"
        "[INFO ANT-0002] Found 0 net violations.\n"
        "[INFO ANT-0001] Found 0 pin violations.\n"
        "ANTENNA_POSTROUTE_DONE\n" + pnr_log_extra)
    if promoted:
        (pnr / "routed_base_prerepair.def").write_text("DESIGN top ;\n")
    if ship_log is not None:
        (pnr / "signoff_spef_repair.log").write_text(ship_log)
    return proj


def _pdk():
    return R.PdkConfig(name="p", liberty=Path("/l.lib"), tech_lef=Path("/t.tlef"),
                       cell_lef=Path("/c.lef"), cell_gds=Path("/c.gds"),
                       site="S", drc_deck="/d.drc")


def _emit(proj, tmp_path):
    rpt = tmp_path / "reports" / "phase3" / "antenna.rpt"
    notes: list = []
    ok = R._emit_antenna_report(proj, "top", _pdk(), "c",
                                rpt, notes)
    return ok, rpt, notes


def test_report_takes_its_counts_from_the_session_that_ships(tmp_path):
    """The PnR log says 0. The PROMOTED route says 2. The report must say 2."""
    proj = _write_run(
        tmp_path, promoted=True,
        ship_log=("SHIP_ANT_BEGIN\n"
                  "[INFO ANT-0002] Found 2 net violations.\n"
                  "[INFO ANT-0001] Found 2 pin violations.\n"
                  "SHIP_ANT_SEQUENCE: 2 2\n"
                  "SHIP_ANT_NOT_CONVERGED: stop=NO_PROGRESS\n"
                  "SHIP_ANT_END\n"))
    ok, rpt, _ = _emit(proj, tmp_path)
    assert ok
    txt = rpt.read_text()
    assert "2 net violations, 2 pin violations" in txt
    assert "antenna clean: NO" in txt
    assert "PROMOTED signoff_spef_repair route" in txt


def test_report_credits_a_repaired_promoted_route(tmp_path):
    proj = _write_run(
        tmp_path, promoted=True,
        ship_log=("SHIP_ANT_BEGIN\n"
                  "[INFO ANT-0002] Found 2 net violations.\n"
                  "[INFO ANT-0001] Found 2 pin violations.\n"
                  "[INFO ANT-0002] Found 0 net violations.\n"
                  "[INFO ANT-0001] Found 0 pin violations.\n"
                  "SHIP_ANT_SEQUENCE: 2 0\n"
                  "SHIP_ANT_END\n"))
    ok, rpt, _ = _emit(proj, tmp_path)
    assert ok
    txt = rpt.read_text()
    assert "0 net violations, 0 pin violations" in txt
    assert "antenna clean: YES" in txt
    # ... and the clean verdict is about the PROMOTED route, not the PnR one it
    # replaced. Pre-fix both said "0" and the report could not tell you which.
    assert "PROMOTED signoff_spef_repair route" in txt


def test_a_promotion_nothing_measured_is_not_a_clean_pass(tmp_path):
    """The exact silent hole: a promotion replaced the route the count was taken
    on, and no antenna check ran on the replacement."""
    proj = _write_run(tmp_path, promoted=True, ship_log="SHIP_SIGNOFF_REPAIR_DONE\n")
    ok, rpt, notes = _emit(proj, tmp_path)
    assert ok
    txt = rpt.read_text()
    assert "antenna clean: NO" in txt
    assert "antenna measured on: NOTHING" in txt
    assert any("SHIPPED ROUTE UNMEASURED" in n for n in notes)


def test_no_promotion_leaves_the_pnr_verdict_exactly_as_it_was(tmp_path):
    proj = _write_run(tmp_path, promoted=False, ship_log=None)
    ok, rpt, _ = _emit(proj, tmp_path)
    assert ok
    txt = rpt.read_text()
    assert "0 net violations, 0 pin violations" in txt
    assert "antenna clean: YES" in txt
    assert "antenna measured on: the PnR route" in txt
