"""TF24 — a post-route session that INSERTS cells must connect their supply,
and a candidate whose supply pins are not all on a declared supply net is
never promoted.

MEASURED (spm x gf180mcuD, vibeic-eda 0.3.79, pure direct flow; lane mig98
arm D, reproduced by lane migf24 on the same pre-repair DEF): the post-route
`signoff_spef_repair` session re-opens `routed.def` (VDD `( * VNW ) ( * VDD )`),
removes the fillers, inserts 15 + 1 `hold*` buffers, re-routes, re-fills and
writes `routed_repaired.def` — with VDD reduced to `( * DVDD )` plus an
explicit list that names neither the 16 buffers nor any refilled filler:
57,320 of 86,016 supply pins on no net. The step promoted it over
`routed.def`. The session registered no `add_global_connection` rule (they are
session state, not DEF) and never ran `global_connect`.

With the deck's own rules registered after `read_def` and applied before the
write, the same session on the same DEF writes `( * VNW ) ( * VDD )` again,
0 of 86,016 unowned, and the NETS section (every signal wire) is
byte-identical to the unfixed run: setup +1.486 ns / hold +0.091 ns both ways.

chip-AGNOSTIC: fixture nets are PWRNET / GNDNET; the calibration pair is the
registry's own real OpenROAD output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import instrument_calibration as IC  # noqa: E402
import pg_supply_pin_ownership_check as G  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402

CAL = PROGRAMS / "calibration"
CAL_LEF = CAL / "pg_supply_buf_1.lef"
CAL_POS = CAL / "pg_supply_unowned_positive.def"
CAL_NEG = CAL / "pg_supply_owned_negative.def"

DECK = """\
puts "PNR_BEGIN"
if {[catch {
  add_global_connection -net PWRNET -pin_pattern "^PWR$" -power
  add_global_connection -net GNDNET -pin_pattern "^GND$" -ground
  global_connect
  pdngen
} err]} { puts "PDN_FAILED: $err" }
detailed_route
"""

LEF = """\
MACRO BUFX
  CLASS CORE ;
  PIN A
    DIRECTION INPUT ;
  END A
  PIN PWR
    DIRECTION INOUT ;
    USE POWER ;
  END PWR
  PIN GND
    DIRECTION INOUT ;
    USE GROUND ;
  END GND
END BUFX
MACRO SPACER
  CLASS CORE SPACER ;
END SPACER
"""


def _def(components: str, specialnets: str, nets: str = "") -> str:
    ncomp = components.count(";")
    nsn = specialnets.count(";")
    nn = nets.count(";")
    return (f"VERSION 5.8 ;\nDESIGN t ;\nUNITS DISTANCE MICRONS 1000 ;\n"
            f"COMPONENTS {ncomp} ;\n{components}END COMPONENTS\n"
            f"SPECIALNETS {nsn} ;\n{specialnets}END SPECIALNETS\n"
            f"NETS {nn} ;\n{nets}END NETS\nEND DESIGN\n")


# ── the gate ────────────────────────────────────────────────────────────────

def test_the_gate_is_calibrated_on_real_tool_output():
    cal = IC.check("pg_supply_pin_ownership_check::judge")
    assert cal.state == IC.CALIBRATED, cal.as_dict()
    assert cal.positive_outcome == G.REFUSE_CODE
    assert cal.negative_outcome is None


def test_the_real_pair_names_the_inserted_instance():
    rec = G.judge(CAL_POS.read_text(), [CAL_LEF.read_text()])
    assert rec["verdict"] == "FAIL" and rec["code"] == G.REFUSE_CODE
    assert {o["instance"] for o in rec["off_supply_examples"]} == {"hold1"}
    assert rec["off_supply_pins"] == 4
    neg = G.judge(CAL_NEG.read_text(), [CAL_LEF.read_text()])
    assert neg["verdict"] == "PASS", neg


def test_wildcard_ownership_passes_and_explicit_omission_fails():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n- ins1 BUFX + PLACED ( 9 0 ) N ;\n"
    wild = ("- PWRNET ( * PWR ) + USE POWER ;\n"
            "- GNDNET ( * GND ) + USE GROUND ;\n")
    assert G.judge(_def(comps, wild), [LEF])["verdict"] == "PASS"
    explicit = ("- PWRNET ( u0 PWR ) + USE POWER ;\n"
                "- GNDNET ( u0 GND ) + USE GROUND ;\n")
    rec = G.judge(_def(comps, explicit), [LEF])
    assert rec["verdict"] == "FAIL"
    assert rec["off_supply_breakdown"]["no_net"] == 2
    assert {o["instance"] for o in rec["off_supply_examples"]} == {"ins1"}


def test_routing_points_after_the_first_plus_are_not_connections():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n"
    # `( u0 PWR )` appears only as a routing coordinate pair after `+ ROUTED`.
    sn = ("- PWRNET + USE POWER + ROUTED Metal1 100 ( u0 PWR ) ( 5 0 ) ;\n"
          "- GNDNET ( * GND ) + USE GROUND ;\n")
    rec = G.judge(_def(comps, sn), [LEF])
    assert rec["verdict"] == "FAIL"
    assert rec["off_supply_examples"][0]["pin"] == "PWR"


def test_a_power_pin_on_the_ground_net_is_refused():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n"
    sn = ("- PWRNET ( * GND ) + USE POWER ;\n"
          "- GNDNET ( * PWR ) + USE GROUND ;\n")
    rec = G.judge(_def(comps, sn), [LEF])
    assert rec["verdict"] == "FAIL"
    assert rec["off_supply_breakdown"]["wrong_kind"] == 2


def test_a_supply_pin_on_a_signal_net_is_refused():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n"
    sn = "- GNDNET ( * GND ) + USE GROUND ;\n"
    nets = "- tie ( u0 PWR ) ( u0 A ) + USE SIGNAL ;\n"
    rec = G.judge(_def(comps, sn, nets), [LEF])
    assert rec["verdict"] == "FAIL"
    assert rec["off_supply_breakdown"]["not_a_supply_net"] == 1


def test_an_unread_master_is_unmeasured_never_clean():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n- m0 MYSTERY + PLACED ( 9 0 ) N ;\n"
    sn = ("- PWRNET ( * PWR ) + USE POWER ;\n"
          "- GNDNET ( * GND ) + USE GROUND ;\n")
    rec = G.judge(_def(comps, sn), [LEF])
    assert rec["verdict"] == "NOT_MEASURED"
    assert rec["code"] == G.UNMEASURED_CODE
    assert rec["unknown_masters"] == {"MYSTERY": 1}


def test_a_proven_failure_outranks_an_unmeasured_remainder():
    comps = "- u0 BUFX + PLACED ( 0 0 ) N ;\n- m0 MYSTERY + PLACED ( 9 0 ) N ;\n"
    sn = "- GNDNET ( * GND ) + USE GROUND ;\n- PWRNET + USE POWER ;\n"
    assert G.judge(_def(comps, sn), [LEF])["verdict"] == "FAIL"


def test_no_declared_supply_net_is_unmeasured():
    comps = "- u0 SPACER + PLACED ( 0 0 ) N ;\n"
    rec = G.judge(_def(comps, ""), [LEF])
    assert rec["verdict"] == "NOT_MEASURED"


def test_cli_exit_codes_and_record(tmp_path):
    out = tmp_path / "r.json"
    assert G.main([str(CAL_POS), "--lef", str(CAL_LEF), "--json", str(out)]) == 1
    assert json.loads(out.read_text())["code"] == G.REFUSE_CODE
    assert G.main([str(CAL_NEG), "--lef", str(CAL_LEF)]) == 0
    assert G.main([str(tmp_path / "absent.def"), "--lef", str(CAL_LEF)]) == 2


# ── the producers ───────────────────────────────────────────────────────────

_EMITTERS = {
    "ship": ("routed_repaired.def", lambda: R._ship_signoff_spef_repair_tcl(
        "top", "t.lef", "c.lef", "ss.lib", "/p", "cap", "M", 2,
        filler_masters=["FILL1"], pg_rules_deck=DECK)),
    "restore": ("routed_cvg_restored.def", lambda: R._ship_cvg_restore_tcl(
        "top", "t.lef", "c.lef", "ss.lib", "/p", "cap", "M", 2,
        "/p/ship_cvg_pass0.def", filler_masters=["FILL1"],
        pg_rules_deck=DECK)),
    "escalation": ("routed_escalated.def",
                   lambda: R._ship_wire_length_escalation_tcl(
                       "top", "t.lef", "c.lef", "ss.lib", "/p", "cap", "M", 2,
                       filler_masters=["FILL1"], pg_rules_deck=DECK)),
    "si_mcf": (R._SI_MCF_CANDIDATE_DEF, lambda: R._si_mcf_repair_child_tcl(
        "top", tech_lef_c="t.lef", cell_lef_c="c.lef", liberty_c="ss.lib",
        pnr_dir_c="/p", txn_dir_c="/p/txn", sdc_c="/p/c.sdc",
        max_captable_c="cap", metal_prefix="M", thread_count=2,
        repair_body="repair_timing -setup\n", filler_masters=["FILL1"],
        pg_rules_deck=DECK)),
}


@pytest.mark.parametrize("site", sorted(_EMITTERS))
def test_every_inserting_session_registers_the_decks_rules_after_read_def(
        site):
    out_def, emit = _EMITTERS[site]
    tcl = emit()
    read = tcl.index("read_def ")
    for rule in ('add_global_connection -net PWRNET -pin_pattern "^PWR$" -power',
                 'add_global_connection -net GNDNET -pin_pattern "^GND$" -ground'):
        assert rule in tcl, site
        at = tcl.index(rule)
        assert at > read, site
        for creator in ("remove_fillers", "repair_design", "repair_timing",
                        "filler_placement"):
            if creator in tcl:
                assert at < tcl.index(creator), (site, creator)


@pytest.mark.parametrize("site", sorted(_EMITTERS))
def test_every_inserting_session_connects_after_its_last_insertion(site):
    out_def, emit = _EMITTERS[site]
    tcl = emit()
    import re
    m = re.search(r"write_def \S*" + re.escape(out_def), tcl)
    assert m, site
    write = m.start()
    marker = "_PG_NO_NET: total="
    apply_at = tcl.rindex("{global_connect}", 0, write)
    assert apply_at > tcl.index("read_def "), site
    assert apply_at < tcl.index(marker, apply_at) < write, site
    for creator in ("filler_placement", "repair_timing", "repair_design"):
        if creator in tcl[:write]:
            assert tcl.rindex(creator, 0, write) < apply_at, (site, creator)


def test_the_convergence_checkpoint_is_written_without_a_connect():
    """MEASURED: a `global_connect` before the mid-loop checkpoint (after the
    parasitics are annotated) made the next pass's `repair_design` die
    EST-0104 and the loop plateau at -0.186 ns instead of closing at +1.486."""
    loop = R._ship_postroute_convergence_tcl("cap", "/p")
    start = loop.index("for {set _cvg 0}")
    end = loop.index("write_def /p/ship_cvg_pass")
    assert "{global_connect}" not in loop[start:end]


def test_a_deckless_session_emits_no_rule_and_still_audits():
    tcl = R._ship_signoff_spef_repair_tcl(
        "top", "t.lef", "c.lef", "ss.lib", "/p", "cap", "M", 2)
    assert "add_global_connection" not in tcl
    assert "SHIP_PG_NO_NET: total=" in tcl


# ── the promotion refusal, driven through the real step ─────────────────────

def _stage(tmp_path, monkeypatch, candidate: Path):
    project = tmp_path / "proj"
    pnr = project / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    base = CAL_NEG.read_text()
    (pnr / "routed.def").write_text(base)
    (pnr / "top.def").write_text(base)
    (pnr / "top_pnr.v").write_text("module top; endmodule\n")
    (pnr / "pnr.tcl").write_text(DECK)
    pdk = SimpleNamespace(tech_lef=str(tmp_path / "t.lef"),
                          cell_lef=str(CAL_LEF), metal_prefix="M",
                          clk_buf_root=None, clk_buf="buf",
                          antenna_diode_cell=None, macro_lefs=[])
    monkeypatch.setattr(R, "_openroad_supports_postroute_spef_repair",
                        lambda c: True)
    monkeypatch.setattr(R, "_resolve_signoff_corner_libs",
                        lambda *a, **k: {"SS": "/lib/ss.lib"})
    monkeypatch.setattr(R, "_max_captable_c", lambda *a, **k: "/cap")
    monkeypatch.setattr(R, "_sta_extra_liberties", lambda *a, **k: [])
    monkeypatch.setattr(R, "_def_reopen_extra_lefs_c", lambda *a, **k: [])
    monkeypatch.setattr(R, "_filler_masters_for_pdk", lambda p: [])
    monkeypatch.setattr(R, "_slot_geometry", lambda p: None)
    monkeypatch.setattr(R, "_l9_declared_die_area", lambda p: None)
    monkeypatch.setattr(R, "_l19_declared_die_area", lambda p: None)
    monkeypatch.setattr(R, "_padring_core_inset_um", lambda p: (None, None))
    monkeypatch.setattr(R, "_cvg_restore_decision",
                        lambda *a, **k: {"verdict": "KEEP"})
    monkeypatch.setattr(R, "_cvg_prune_checkpoints", lambda *a, **k: 0)
    monkeypatch.setattr(R, "_cvg_best_pass_report", lambda *a, **k: None)
    monkeypatch.setattr(R, "_ship_convergence_exhaustion_report",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_drv_promotion_claim", lambda *a, **k: None)
    monkeypatch.setattr(R, "_record_route_promotion", lambda *a, **k: None)
    # The promotion POLICY is not under test here; every other clause of it
    # has its own file. This asks only whether the supply gate runs first.
    monkeypatch.setattr(R, "_ship_repair_should_promote", lambda *a: True)
    monkeypatch.setattr(R, "_parse_ship_repair_log",
                        lambda log: {"wns_before": -0.5,
                                     "wns_after_repair": 0.2,
                                     "wns_postroute": 1.4})

    def _producer(container, tcl_c, outs):
        # The ONLY fake: the EDA tool's own file writes.
        outs[0].write_text(candidate.read_text())
        outs[1].write_text("module top; endmodule // repaired\n")
        return 0, "SHIP_SIGNOFF_REPAIR_DONE\n", ""
    monkeypatch.setattr(R, "_run_route_producer", _producer)
    return project, pnr, pdk


def test_an_unpowered_candidate_is_refused_by_name_and_not_promoted(
        tmp_path, monkeypatch):
    project, pnr, pdk = _stage(tmp_path, monkeypatch, CAL_POS)
    before = (pnr / "routed.def").read_bytes()
    res = R.step_signoff_spef_repair(project, "top", pdk, "")
    assert res.detail.startswith(G.REFUSE_CODE), res.detail
    assert res.extras.get("refusal_code") == G.REFUSE_CODE
    assert (pnr / "routed.def").read_bytes() == before
    assert "hold1" not in (pnr / "top.def").read_text()
    rec = json.loads((project / "reports" / "phase3" /
                      "postroute_pg_ownership_signoff_spef_repair.json"
                      ).read_text())
    assert rec["verdict"] == "FAIL" and rec["site"] == "signoff_spef_repair"
    disclosed = json.loads((pnr / R._DRV_PROMOTION_NOT_RUN).read_text())
    assert disclosed["not_run_stage"] == "pg_supply_ownership_refused"
    assert G.REFUSE_CODE in disclosed["reason"]
    # the step also handed the producer the deck's rules
    assert "add_global_connection -net PWRNET" in (
        pnr / "signoff_spef_repair.tcl").read_text()


def test_a_powered_candidate_is_promoted_and_says_it_was_read(
        tmp_path, monkeypatch):
    project, pnr, pdk = _stage(tmp_path, monkeypatch, CAL_NEG)
    (pnr / "routed.def").write_text("# base\n" + CAL_NEG.read_text())
    res = R.step_signoff_spef_repair(project, "top", pdk, "")
    assert "SHIPPED" in res.detail, res.detail
    assert "POSTROUTE_PG_OWNERSHIP: PASS" in res.detail
    assert res.extras.get("pg_supply_ownership") == "PASS"
    assert (pnr / "routed.def").read_text() == CAL_NEG.read_text()


def test_an_unmeasurable_candidate_is_published_not_refused_and_not_clean(
        tmp_path, monkeypatch):
    """A master no readable LEF defines is NOT_MEASURED: the promotion stands
    (refusing would lose a timing repair to an unreadable LEF) and the
    note and record say NOT_MEASURED -- never PASS."""
    mystery = tmp_path / "mystery.def"
    mystery.write_text(CAL_NEG.read_text().replace(
        "gf180mcu_fd_sc_mcu7t5v0__buf_1", "UNREAD_MASTER"))
    project, pnr, pdk = _stage(tmp_path, monkeypatch, mystery)
    res = R.step_signoff_spef_repair(project, "top", pdk, "")
    assert "SHIPPED" in res.detail
    assert "POSTROUTE_PG_OWNERSHIP: NOT_MEASURED" in res.detail
    assert res.extras.get("pg_supply_ownership") == "NOT_MEASURED"


def test_an_unpowered_escalation_candidate_is_never_staged(
        tmp_path, monkeypatch):
    project, pnr, pdk = _stage(tmp_path, monkeypatch, CAL_POS)
    # Promotion is OFF in production (its own flag, its own tests); the
    # producer fix and the refusal are for the day it is switched on.
    monkeypatch.setattr(R, "_DRV_ESCALATION_PROMOTION_ENABLED", True)
    before = (pnr / "routed.def").read_bytes()
    measured = []
    monkeypatch.setattr(R, "_measure_signoff_drv_population",
                        lambda *a, **k: measured.append(a) or 5)

    def _producer(container, tcl_c, outs):
        outs[0].write_text(CAL_POS.read_text())
        outs[1].write_text("module top; endmodule // escalated\n")
        return 0, "SHIP_ESC_BEFORE_COUNT: 5\nSHIP_ESC_AFTER_COUNT: 1\n", ""
    monkeypatch.setattr(R, "_run_route_producer", _producer)
    res = R.step_signoff_drv_wire_length_repair(project, "top", pdk, "")
    assert res.detail.startswith(G.REFUSE_CODE), res.detail
    assert res.extras.get("refusal_code") == G.REFUSE_CODE
    assert (pnr / "routed.def").read_bytes() == before
    assert measured == [], "the candidate was staged for measurement"
    assert "add_global_connection -net PWRNET" in (
        pnr / "signoff_drv_escalation.tcl").read_text()


def test_an_unpowered_si_mcf_candidate_is_not_promoted(tmp_path, monkeypatch):
    project, pnr, pdk = _stage(tmp_path, monkeypatch, CAL_POS)
    rep = project / "reports" / "phase3"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "si_mcf_repair.json").write_text(json.dumps(
        {"decision": "ADOPTED"}))
    txn = pnr / "si_txn"
    txn.mkdir()
    cand = {"candidate_def": txn / "candidate.def",
            "candidate_netlist": txn / "top_pnr.v",
            "candidate_spef": txn / "candidate.spef",
            "candidate_odb": txn / "candidate.odb"}
    cand["candidate_def"].write_text(CAL_POS.read_text())
    for k in ("candidate_netlist", "candidate_spef", "candidate_odb"):
        cand[k].write_text("x\n")
    before = (pnr / "routed.def").read_bytes()
    notes: list = []
    R._si_mcf_repair_promote(project, "top", pdk, "",
                             {k: str(v) for k, v in cand.items()}, notes)
    assert (pnr / "routed.def").read_bytes() == before
    assert any(G.REFUSE_CODE in n for n in notes), notes
    rec = json.loads((rep / "si_mcf_repair.json").read_text())
    assert G.REFUSE_CODE in rec["promotion"]["refused"]
