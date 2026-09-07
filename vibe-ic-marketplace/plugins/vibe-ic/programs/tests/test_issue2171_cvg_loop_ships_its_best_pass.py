"""tests/test_issue2171_cvg_loop_ships_its_best_pass.py — vibe-ic#2171

The post-route convergence loop shipped its LAST pass, not its BEST one. These
tests pin the repair on both halves of it:

  * the SELECTION — one declared rule, in one module, and a NON-MONOTONE series
    must not ship its last state. The negative control replaces the rule with
    "last wins" and requires the same body to reach the opposite answer, so a
    green here can never be a green that could not fail.
  * the MECHANISM — the loop keeps every pass's GEOMETRY, the restore runs in a
    fresh session and repairs nothing, and a restore whose re-measurement
    disagrees with the winning pass's own number is REFUSED rather than shipped.

Every series in the first half is REAL, quoted from this fleet's own published
run transcripts (paths in the lane evidence), not invented for the test.
"""
from __future__ import annotations

import pytest

from programs import postroute_cvg_best_pass_select as S
from programs import phase3_one_shot_runner as R


# --- the real, measured series ---------------------------------------------
# subservient x sky130A: the arm quoted in the issue. pass2 -0.6197 was measured
# and thrown away for pass3 -0.9067.
CZ2160_WNS = [-1.756220107938106, -1.119278923447789,
              -0.6196963118057012, -0.9066845308957868]
# subservient x sky130A, the earlier sighting: pass1 -0.0923 -> shipped -0.2593.
RBSUB_WNS = [-1.0408634249115625, -0.09232614933898985, -0.25925928804281995]
# caravel_user_project x sky130A: a run that had CLOSED setup at pass6 and
# shipped a VIOLATED state.
CARAVEL_WNS = [-21.87426479641451, -10.89390652965478, -9.755940331727988,
               -4.372113461687701, -3.627393599275424, -1.0292389454257644,
               0.29105607636735903, -0.966522889080168]
CARAVEL_DRV = [2338, 1605, 1086, 822, 677, 578, 474, 438]
CARAVEL_SHIPPED = -0.7270735330706027


def _log(wns, drv=None, hold=None, *, shipped=None, final_drv=None,
         final_hold=None, checkpoints=True, terminal="SHIP_CVG_PLATEAU",
         violations=None):
    """A transcript in the loop's own marker vocabulary.

    `violations` is one router count per pass, emitted BEFORE that pass's
    checkpoint exactly as the router emits it before `write_def` freezes the
    geometry."""
    out = []
    for i, w in enumerate(wns):
        if violations is not None:
            out.append(f"[INFO DRT-0199]   Number of violations = "
                       f"{violations[i]}")
        out.append(f"SHIP_WNS_CVG_PASS{i}: {w}")
        out.append(f"SHIP_DRV_CVG_PASS{i}: "
                   f"{drv[i] if drv is not None else -1}")
        out.append(f"SHIP_HOLD_CVG_PASS{i}: "
                   f"{hold[i] if hold is not None else 'UNMEASURED'}")
        if checkpoints:
            out.append(f"SHIP_CVG_CKPT: pass={i} "
                       f"def=/w/pnr/ship_cvg_pass{i}.def wns={w}")
    if terminal:
        out.append(terminal)
    out.append(f"SHIP_CVG_FINAL_DRV: "
               f"{final_drv if final_drv is not None else -1}")
    out.append(f"SHIP_CVG_FINAL_HOLD: "
               f"{final_hold if final_hold is not None else 'UNMEASURED'}")
    out.append(f"SHIP_WNS_POSTROUTE: "
               f"{shipped if shipped is not None else wns[-1]}")
    return "\n".join(out) + "\n"


# ===========================================================================
# 1. THE SELECTION
# ===========================================================================

@pytest.mark.parametrize("wns,want_pass,want_gain", [
    (CZ2160_WNS, "2", 0.2870),
    (RBSUB_WNS, "1", 0.1669),
])
def test_a_non_monotone_series_does_not_ship_its_last_pass(wns, want_pass,
                                                           want_gain):
    """The defect, on the two series that were actually measured."""
    d = S.decide(_log(wns))
    assert d["verdict"] == "RESTORE", d["detail"]
    assert d["winner"]["label"] == want_pass
    assert d["gain_ns"] == pytest.approx(want_gain, abs=1e-4)
    assert d["restore_def"].endswith(f"ship_cvg_pass{want_pass}.def")


def test_a_monotone_series_restores_nothing():
    """The common case must stay free. A selector that fires on a converging
    run would pay for a second OpenROAD session on every design."""
    d = S.decide(_log([-4.0, -2.5, -1.0, -0.2]))
    assert d["verdict"] == "LAST_IS_BEST", d["detail"]
    assert d["restore_def"] is None


def test_a_closed_pass_beats_a_violated_last_pass():
    """caravel_user_project, exactly as it was published: the loop ran out of
    passes (no terminal marker), so the final state is a NEW geometry whose DRV
    the transcript never states. R2 is inapplicable on an unmeasured count, and
    setup decides: pass 6 had CLOSED setup and the run shipped a violation."""
    d = S.decide(_log(CARAVEL_WNS, drv=CARAVEL_DRV,
                      shipped=CARAVEL_SHIPPED, terminal=""))
    assert d["verdict"] == "RESTORE", d["detail"]
    assert d["winner"]["label"] == "6"
    assert S.State("w", d["winner"]["wns"]).setup_met
    assert not S.State("f", CARAVEL_SHIPPED).setup_met
    assert d["gain_ns"] == pytest.approx(1.0181, abs=1e-4)


def test_the_same_series_is_refused_once_the_final_drv_is_measured():
    """R2 on the same real numbers. The emitter now publishes the final state's
    DRV, so the comparison the historical transcript could not make becomes
    available — and it goes the other way. This is the pair that proves R2 is
    doing work rather than sitting inert: same series, one measurement added,
    opposite verdict."""
    d = S.decide(_log(CARAVEL_WNS, drv=CARAVEL_DRV,
                      shipped=CARAVEL_SHIPPED, final_drv=438, terminal=""))
    assert d["verdict"] == "REFUSED_DRV_REGRESSION", d["detail"]
    assert "474" in d["detail"] and "438" in d["detail"]
    assert d["restore_def"] is None
    assert d["gain_ns"] == pytest.approx(1.0181, abs=1e-4), (
        "the gain the run could not take is still reported — a refusal that "
        "hides the number it refused is unreadable evidence")


# --- the negative control ---------------------------------------------------
def test_restoring_last_wins_makes_the_selection_tests_red(monkeypatch):
    """THE MUTATION. Replace the declared rule with "the last pass always
    wins" — the behaviour this issue exists to remove — and require the SAME
    series to reach the opposite verdict.

    Without this, a green above could be a green that cannot fail: `decide`
    compares every pass against the FINAL state, so a rule that always prefers
    the incumbent-displacing candidate collapses the whole selection back onto
    the last state."""
    monkeypatch.setattr(S, "beats", lambda cand, best: False)
    for wns in (CZ2160_WNS, RBSUB_WNS, CARAVEL_WNS):
        d = S.decide(_log(wns))
        assert d["verdict"] == "LAST_IS_BEST", (
            "the last-wins mutation must reach the defect's own verdict; if it "
            "does not, the tests above are not measuring the rule")
        assert d["restore_def"] is None


# --- the hold refusal -------------------------------------------------------
def test_a_pass_that_breaks_a_met_hold_never_wins_on_setup():
    """Clause 1: hold is never traded for setup. Pass 2 buys 5 ns of setup and
    breaks a hold that pass 1 met; pass 1 must still be the winner."""
    d = S.decide(_log([-6.0, -1.0, 4.0], hold=[0.20, 0.15, -0.30],
                      shipped=-2.0, final_hold=-0.30))
    assert d["verdict"] == "RESTORE", d["detail"]
    assert d["winner"]["label"] == "1"


def test_an_unmeasured_hold_neither_disqualifies_nor_certifies():
    """UNMEASURED is not a violation, and it is not a clean bill either: the
    same series with hold absent falls through to the setup clause."""
    d = S.decide(_log([-6.0, -1.0, 4.0], shipped=-2.0))
    assert d["verdict"] == "RESTORE"
    assert d["winner"]["label"] == "2", (
        "with hold unmeasured the setup clause decides — reading an absent "
        "hold as a violation would silently refuse the better pass")


# --- the met-state DRV refinement ------------------------------------------
def test_a_winner_that_buys_setup_with_design_rules_is_refused():
    """R2, on the caravel iter4 series as published: setup rose to +8.25 ns at
    pass 2 and the shipped state has +2.98 ns — but pass 2 carries 254
    design-rule violations against the shipped state's 235. The loop's own
    closure test needs BOTH axes, so setup is not bought with design rules."""
    d = S.decide(_log([3.7484469922336565, 6.196357380357645,
                       8.251668026874947, 2.981652342753835,
                       2.981652342753835, 2.981652342753835],
                      drv=[263, 275, 254, 237, 235, 235], final_drv=235))
    assert d["verdict"] == "REFUSED_DRV_REGRESSION", d["detail"]
    assert d["winner"]["label"] == "2"
    assert d["restore_def"] is None


def test_an_unmeasured_drv_neither_refuses_a_winner_nor_excuses_one():
    w = S.State("2", 8.0, drv=254)
    assert S.refuse_drv_regression(w, S.State("final", 3.0, drv=235))
    assert S.refuse_drv_regression(w, S.State("final", 3.0, drv=-1)) is None
    assert S.refuse_drv_regression(w, S.State("final", 3.0, drv=None)) is None
    assert S.refuse_drv_regression(S.State("2", 8.0, drv=-1),
                                   S.State("final", 3.0, drv=235)) is None
    assert S.refuse_drv_regression(w, S.State("final", 3.0, drv=254)) is None


def test_an_unmeasured_drv_cannot_win_the_drv_clause():
    """The emitter writes -1 when the probe could not run. A -1 must never be
    read as "zero violations", which would let an unmeasured pass beat a
    measured clean one."""
    assert not S.beats(S.State("1", 1.0, drv=-1), S.State("0", 1.0, drv=0))
    assert not S.beats(S.State("1", 1.0, drv=None), S.State("0", 1.0, drv=0))


def test_a_tie_leaves_the_incumbent_so_the_winner_is_the_earliest():
    """No restore may be bought for a difference the loop itself would not call
    a difference."""
    assert not S.beats(S.State("1", -1.0), S.State("0", -1.0))
    assert not S.beats(S.State("1", -1.0 + S.TIE_TOL_NS / 2),
                       S.State("0", -1.0))
    assert S.beats(S.State("1", -1.0 + 10 * S.TIE_TOL_NS),
                   S.State("0", -1.0))


# --- the states that are not selections ------------------------------------
def test_a_winner_with_no_checkpoint_is_reported_not_ignored():
    """A best number with no geometry is the defect this issue is about, not a
    fix for it — so it gets its OWN verdict and never reads as LAST_IS_BEST."""
    d = S.decide(_log(CZ2160_WNS, checkpoints=False))
    assert d["verdict"] == "BEST_UNRESTORABLE", d["detail"]
    assert d["winner"]["label"] == "2"
    assert d["restore_def"] is None


def test_a_loop_that_never_ran_is_not_a_selection_result():
    assert S.decide("SHIP_REPAIR_NOOP: 1 (base route kept)\n"
                    )["verdict"] == "LOOP_NOT_ENTERED"
    assert S.decide("")["verdict"] == "NO_SERIES"
    assert S.decide(_log([-1.0, -2.0]).replace("SHIP_WNS_POSTROUTE",
                                               "SHIP_NOT_A_MARKER"),
                    )["verdict"] == "NO_SERIES"


def test_an_unrouted_final_state_is_still_compared():
    """A reroute that aborted publishes SHIP_WNS_UNROUTED instead. The state
    still exists and still has to be beaten before an earlier pass may ship."""
    log = _log(CZ2160_WNS).replace("SHIP_WNS_POSTROUTE",
                                   "SHIP_WNS_UNROUTED")
    assert S.decide(log)["verdict"] == "RESTORE"


# --- DRC attribution --------------------------------------------------------
def test_the_restored_route_is_judged_on_its_own_drc_count():
    """The router's count belongs to the geometry it produced. Attributing the
    LAST count to a restored earlier checkpoint would be the same
    "report describes a tree that did not ship" defect, one level down."""
    log = _log(CZ2160_WNS, violations=[7, 3, 0, 11])
    assert S.pass_route_violations(log) == {0: 7, 1: 3, 2: 0, 3: 11}
    d = S.decide(log)
    assert d["winner"]["label"] == "2"
    assert d["winner_route_violations"] == 0


def test_a_pass_with_no_stated_drc_count_is_absent_not_zero():
    log = _log([-1.0, -2.0], violations=None)
    assert S.pass_route_violations(log) == {}
    assert S.decide(_log(CZ2160_WNS))["winner_route_violations"] is None


# ===========================================================================
# 2. THE MECHANISM
# ===========================================================================

def test_the_loop_checkpoints_every_pass_and_measures_hold():
    tcl = R._ship_postroute_convergence_tcl("/pdk/max.rules", "/w/pnr")
    assert "write_def /w/pnr/ship_cvg_pass${_cvg}.def" in tcl
    assert 'puts "SHIP_CVG_CKPT: pass=$_cvg' in tcl
    assert "sta::worst_slack -min" in tcl
    assert 'puts "SHIP_HOLD_CVG_PASS${_cvg}' in tcl
    # the final state is measured on the SAME three axes as every pass
    assert "SHIP_CVG_FINAL_DRV" in tcl and "SHIP_CVG_FINAL_HOLD" in tcl
    # the checkpoint marker is emitted only where write_def succeeded
    fail_at = tcl.index("SHIP_CVG_CKPT_NONFATAL")
    ok_at = tcl.index('puts "SHIP_CVG_CKPT: pass=')
    assert fail_at < ok_at, ("the success marker must be the else-branch of the "
                            "write_def catch, so a pass can never advertise a "
                            "checkpoint it does not have")


def test_the_restore_session_repairs_nothing():
    """It re-reads, re-measures and re-emits. A restore that ran the resizer
    would not be a restore of the pass it claims to restore."""
    tcl = R._ship_cvg_restore_tcl("top", "/t.lef", "/c.lef", "/ss.lib",
                                  "/w/pnr", "/pdk/max.rules", "Metal", 4,
                                  "/w/pnr/ship_cvg_pass2.def")
    for forbidden in ("repair_design", "repair_timing", "global_route",
                      "detailed_route", "remove_fillers"):
        assert forbidden not in tcl, forbidden
    assert "read_def /w/pnr/ship_cvg_pass2.def" in tcl
    assert "SHIP_RESTORE_WNS" in tcl and "SHIP_CVG_RESTORE_DONE" in tcl
    # the measurement basis is COPIED from the repair session, not re-chosen
    for same in ("set_wire_rc -signal -layer Metal1",
                 "set_wire_rc -clock -layer Metal5",
                 "set_timing_derate -early 0.95",
                 "set_timing_derate -late 1.05",
                 "define_process_corner -ext_model_index 0 X",
                 "estimate_parasitics -detailed_routing"):
        assert same in tcl, same
    # and it must not overwrite the repair session's own parasitics evidence
    assert "signoff_repair_max.spef" not in tcl
    assert "ship_cvg_restored_max.spef" in tcl


def test_a_checkpoint_that_is_not_on_disk_refuses_the_restore(tmp_path):
    """The marker says a DEF was written; only the filesystem says it can be
    read. A restore may key on the second."""
    d = R._cvg_restore_decision(tmp_path, _log(CZ2160_WNS))
    assert d["verdict"] == "BEST_UNRESTORABLE", d["detail"]
    (tmp_path / "ship_cvg_pass2.def").write_text("")
    assert R._cvg_restore_decision(
        tmp_path, _log(CZ2160_WNS))["verdict"] == "BEST_UNRESTORABLE"
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\nEND DESIGN\n")
    d = R._cvg_restore_decision(tmp_path, _log(CZ2160_WNS))
    assert d["verdict"] == "RESTORE", d["detail"]
    assert d["restore_def_host"] == str(tmp_path / "ship_cvg_pass2.def")


def _restored(tmp_path, top="top"):
    (tmp_path / "routed_cvg_restored.def").write_text("DESIGN x ;\n")
    (tmp_path / f"{top}_pnr_cvg_restored.v").write_text("module top; endmodule\n")
    (tmp_path / "routed_repaired.def").write_text("LAST PASS DEF\n")
    (tmp_path / f"{top}_pnr_repaired.v").write_text("LAST PASS V\n")


def test_a_verified_restore_publishes_the_restored_number(tmp_path):
    _restored(tmp_path)
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    d = R._cvg_restore_decision(tmp_path, _log(CZ2160_WNS,
                                               violations=[9, 4, 0, 6]))
    parsed = {"wns_postroute": -0.9066845308957868, "route_violations": 6}
    rlog = (f"SHIP_RESTORE_WNS: {CZ2160_WNS[2]}\n"
            "SHIP_UNROUTED_NETS: 0\n"
            "SHIP_CVG_RESTORE_DONE\n")
    ok, why = R._cvg_apply_restore(tmp_path, "top", d, rlog, parsed)
    assert ok, why
    assert parsed["wns_postroute"] == pytest.approx(CZ2160_WNS[2])
    assert parsed["route_violations"] == 0, (
        "the promotion gate must judge the restored route's own DRC count, "
        "not the count belonging to the route it replaces")
    assert parsed["cvg_restored_from_pass"] == "2"
    assert (tmp_path / "routed_repaired.def").read_text() == "DESIGN x ;\n"
    assert (tmp_path / "top_pnr_repaired.v").read_text().startswith("module")


@pytest.mark.parametrize("rlog,why", [
    (f"SHIP_RESTORE_WNS: {CZ2160_WNS[2] + 0.5}\nSHIP_CVG_RESTORE_DONE\n",
     "diverges from the winning pass"),
    ("SHIP_RESTORE_WNS: -0.6196963118057012\n", "never reached DONE"),
    ("SHIP_CVG_RESTORE_DONE\n", "no re-measurement at all"),
])
def test_an_unproven_restore_is_refused_and_changes_nothing(tmp_path, rlog, why):
    """Proven by RE-MEASURING the restored design, never by the loop's own
    bookkeeping — and a refusal leaves every session-1 artefact untouched."""
    _restored(tmp_path)
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    d = R._cvg_restore_decision(tmp_path,
                                _log(CZ2160_WNS, violations=[9, 4, 0, 6]))
    parsed = {"wns_postroute": CZ2160_WNS[-1], "route_violations": 0}
    ok, reason = R._cvg_apply_restore(tmp_path, "top", d, rlog, parsed)
    assert not ok, why
    assert reason
    assert parsed["wns_postroute"] == CZ2160_WNS[-1]
    assert "cvg_restored_from_pass" not in parsed
    assert (tmp_path / "routed_repaired.def").read_text() == "LAST PASS DEF\n"
    assert (tmp_path / "top_pnr_repaired.v").read_text() == "LAST PASS V\n"


def test_a_verified_number_with_no_artefact_promotes_nothing(tmp_path):
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    (tmp_path / "routed_repaired.def").write_text("LAST PASS DEF\n")
    (tmp_path / "top_pnr_repaired.v").write_text("LAST PASS V\n")
    d = R._cvg_restore_decision(tmp_path,
                                _log(CZ2160_WNS, violations=[9, 4, 0, 6]))
    parsed = {}
    ok, reason = R._cvg_apply_restore(
        tmp_path, "top", d,
        f"SHIP_RESTORE_WNS: {CZ2160_WNS[2]}\nSHIP_CVG_RESTORE_DONE\n", parsed)
    assert not ok and "missing or empty" in reason
    assert (tmp_path / "routed_repaired.def").read_text() == "LAST PASS DEF\n"


def test_the_checkpoints_are_pruned_and_the_winner_is_kept(tmp_path):
    for i in range(4):
        (tmp_path / f"ship_cvg_pass{i}.def").write_text("x")
    keep = tmp_path / "ship_cvg_pass2.def"
    assert R._cvg_prune_checkpoints(tmp_path, keep=keep) == 3
    assert keep.is_file()
    assert R._cvg_prune_checkpoints(tmp_path) == 1
    assert not list(tmp_path.glob("ship_cvg_pass*.def"))


# ===========================================================================
# 3. THE DECLARATION
# ===========================================================================

def test_the_published_rule_is_the_rule_that_is_applied():
    """The policy has to be findable by a reader who never opens the selector,
    and a future edit that changes the behaviour without changing the
    declaration has to go red here."""
    rule = S.DECLARED_RULE.lower()
    for phrase in ("setup wns decides", "total order", "hold",
                   "unmeasured", "drv", "earliest", "refused"):
        assert phrase in rule, phrase
    assert str(S.TIE_TOL_NS) in S.DECLARED_RULE
    # the tolerances the rule names are the loop's own, not new constants
    assert S.SETUP_MET_TOL_NS == 0.001
    tcl = R._ship_postroute_convergence_tcl("/pdk/max.rules", "/w/pnr")
    assert f"$_cvg_wns >= -{S.SETUP_MET_TOL_NS}" in tcl, (
        "the selector's notion of MET must be the loop's own closure "
        "tolerance, or the two can disagree about which passes closed")
    # every run publishes it, so the decision can be read without this file
    assert S.decide(_log(CZ2160_WNS))["declared_rule"] == S.DECLARED_RULE


def test_a_terminal_break_derives_the_final_state_axes_from_the_last_pass():
    """Every terminal marker breaks IMMEDIATELY after the measurement and
    before any repair, so on those exits the final geometry IS the last pass's.
    Deriving its DRV/hold from that pass is exact; leaving them unmeasured would
    silently disable the met-state DRV clause on every archived transcript."""
    log = _log([1.0, 8.0, 3.0], drv=[263, 254, 235], hold=[0.2, 0.2, 0.2],
               shipped=3.0, terminal="SHIP_CVG_PLATEAU")
    log = log.replace("SHIP_CVG_FINAL_DRV: -1", "SHIP_CVG_FINAL_DRV: ")
    _, final, _ = S.parse_states(log)
    assert final.drv == 235 and final.hold == pytest.approx(0.2)
    # and the derivation is load-bearing: WITH it, R2 can see 254 against 235
    # and refuses; without it the final DRV would be unmeasured, R2 would be
    # inapplicable, and the run would restore a DRV regression.
    assert S.decide(log)["verdict"] == "REFUSED_DRV_REGRESSION"


def test_a_bound_exhausted_final_state_keeps_its_axes_unmeasured():
    """No terminal marker means the loop rerouted once more: the final state is
    NEW, and its DRV/hold are genuinely unmeasured, not the last pass's."""
    log = _log([1.0, 8.0, 3.0], drv=[263, 254, 235], shipped=3.0, terminal="")
    log = log.replace("SHIP_CVG_FINAL_DRV: -1", "SHIP_CVG_FINAL_DRV: ")
    _, final, _ = S.parse_states(log)
    assert final.drv is None
    # R2 is then inapplicable and setup decides: pass 1 (+8.0) beats +3.0
    d = S.decide(log)
    assert d["verdict"] == "RESTORE" and d["winner"]["label"] == "1"


def test_a_restore_whose_route_drc_was_never_stated_is_not_taken(tmp_path):
    """Fail-safe in the direction that changes nothing. Without this the step
    would swap in a route the promotion gate then declines on a None DRC count,
    turning a run main would have promoted into one that keeps the base route.
    A restore that cannot be judged is simply not taken."""
    _restored(tmp_path)
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    d = R._cvg_restore_decision(tmp_path, _log(CZ2160_WNS))   # no router lines
    assert d["verdict"] == "RESTORE" and d["winner_route_violations"] is None
    parsed = {"wns_postroute": CZ2160_WNS[-1]}
    ok, why = R._cvg_apply_restore(
        tmp_path, "top", d,
        f"SHIP_RESTORE_WNS: {CZ2160_WNS[2]}\nSHIP_CVG_RESTORE_DONE\n", parsed)
    assert not ok and "never stated" in why
    assert parsed["wns_postroute"] == CZ2160_WNS[-1]
    assert (tmp_path / "routed_repaired.def").read_text() == "LAST PASS DEF\n"


# ===========================================================================
# 4. POLARITY — a value its own RECORD denies is not a measurement
# ===========================================================================
#
# These are the arms that make the `_prose_polarity` consult non-vacuous, and
# they exist because the GATE cannot: `prose_polarity_consulted_check` is
# satisfied by the PRESENCE of a call from its name set, so deleting the
# `is_denied` branch while keeping `sentence_scope` leaves it green. MEASURED in
# this lane — the gate passed on a tree with the refusal removed. A consult
# nobody can see fail is a green light, so the behaviour is pinned here.

def test_a_router_count_its_own_record_denies_is_not_a_measurement():
    """The transcript carries the flow's own English beside the router's output,
    so a sentence can reach the count regex."""
    good = _log(CZ2160_WNS, violations=[7, 3, 0, 11])
    assert S.pass_route_violations(good) == {0: 7, 1: 3, 2: 0, 3: 11}
    denied = good.replace(
        "[INFO DRT-0199]   Number of violations = 0",
        "SHIP_NOTE: no route was written here, so Number of violations = 0 "
        "describes nothing")
    got = S.pass_route_violations(denied)
    assert got[2] == 3, (
        "the denied count must not be read; pass 2 falls back to the last "
        "count that was actually stated")
    assert got[0] == 7 and got[1] == 3


def test_a_denied_restore_measurement_is_not_promoted(tmp_path):
    _restored(tmp_path)
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    d = R._cvg_restore_decision(tmp_path,
                                _log(CZ2160_WNS, violations=[9, 4, 0, 6]))
    parsed = {"wns_postroute": CZ2160_WNS[-1]}
    # The marker is at line start and the number AGREES, so verification would
    # accept it on the number alone — the record's own sentence is the only
    # thing that refuses. Anything less than this does not exercise polarity:
    # a marker placed mid-line is refused by the anchored regex instead, which
    # is how the first version of this arm passed while measuring nothing.
    rlog = (f"SHIP_RESTORE_WNS: {CZ2160_WNS[2]} — the reroute did not complete, "
            f"so this is not a measurement of the restored design\n"
            f"SHIP_CVG_RESTORE_DONE\n")
    ok, why = R._cvg_apply_restore(tmp_path, "top", d, rlog, parsed)
    assert not ok, why
    assert "DENIES" in why and "not" in why
    assert parsed["wns_postroute"] == CZ2160_WNS[-1]
    assert (tmp_path / "routed_repaired.def").read_text() == "LAST PASS DEF\n"


def test_a_denied_unrouted_count_does_not_overwrite_what_was_measured(tmp_path):
    """The third refusal, and it needed its own arm: removing it left the other
    two arms green, so it was carrying no proof at all until this one existed.

    Here verification SUCCEEDS — the restored number is stated plainly and
    agrees — so the routing-integrity read is actually reached, and only the
    denial in its own record stops it replacing a measured value."""
    _restored(tmp_path)
    (tmp_path / "ship_cvg_pass2.def").write_text("DESIGN x ;\n")
    d = R._cvg_restore_decision(tmp_path,
                                _log(CZ2160_WNS, violations=[9, 4, 0, 6]))
    rlog = (f"SHIP_RESTORE_WNS: {CZ2160_WNS[2]}\n"
            f"SHIP_UNROUTED_NETS: 3 — the probe could not run, so this is not a "
            f"count of anything\n"
            f"SHIP_CVG_RESTORE_DONE\n")
    parsed = {"wns_postroute": CZ2160_WNS[-1], "unrouted_nets": 0}
    ok, why = R._cvg_apply_restore(tmp_path, "top", d, rlog, parsed)
    assert ok, why
    assert parsed["unrouted_nets"] == 0, (
        "a denied count must not overwrite the value that WAS measured; "
        "reading it would have refused a promotion on a number the record "
        "itself retracts")
    assert parsed["wns_postroute"] == pytest.approx(CZ2160_WNS[2])
