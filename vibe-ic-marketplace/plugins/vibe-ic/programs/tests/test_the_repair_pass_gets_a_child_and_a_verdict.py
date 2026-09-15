"""R-0915-41 part 3 — the one SI-aware repair pass gets an EXECUTION SEAM.

Parts 1+2 plan the pass and judge a candidate; with no seam supplied every
open-envelope run could only record `NOT_EXECUTED` with its BEFORE numbers.
This is the seam: a CHILD OpenROAD session seeded from the shipped route
applies the planned pass, re-extracts the candidate's OWN parasitics, and the
nominal STA + the MCF fold are re-run ON THE CANDIDATE — so `accepts()` judges
three numbers that were measured rather than three that were assumed.

The subject is `subservient` x gf180mcuD, whose envelope is the run's last real
wall (MEASURED identically in r13, r14, r15, r16):

    si_mcf_sta_nominal.rpt     worst slack max  +2.4975
    si_mcf_sta_mcf_setup.rpt   worst slack max  -0.2660     <- the FAIL
    si_mcf_sta_mcf_hold.rpt    worst slack max  +4.7640

Both directions are pinned, because an execution seam is exactly where a
producer stops being able to be wrong only on paper:

  * THE HOLE THE SEAM OPENS — every comparison in `accepts()` skips when a
    number is absent, so a candidate whose child DIED (all three numbers None)
    fell through all three tests and was ADOPTED on nothing. It is now refused
    BY NAME, and so is a comparison against an unmeasured BASELINE;
  * a candidate that cannot be built at all is NOT_EXECUTED naming the missing
    precondition — never a REJECTED verdict about a candidate that never
    existed;
  * an adoption RE-DERIVES every sign-off artefact the promoted route made
    stale, and sidelines what it replaced;
  * every refusal leaves the shipping route BYTE-IDENTICAL.

The real-container control is opt-in via VIBEIC_SI_MCF_PROJECT (a finished
phase-3 project dir); it is skipped BY NAME when unset, never silently.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import si_mcf_repair as S          # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

_OPEN = {"verdict": "FAIL",
         "nominal": {"worst_setup_slack_ns": 2.4975},
         "corners": {"setup": {"worst_slack_after_ns": -0.266,
                               "worst_victim": {"net": "net690"}},
                     "hold": {"worst_slack_after_ns": 4.764,
                              "worst_victim": {"net": "clknet_leaf_21_i_clk"}}}}


def _proj(tmp_path, si_mcf=None):
    p = tmp_path / "proj"
    (p / "reports" / "phase3").mkdir(parents=True)
    (p / "reports" / "phase3" / "si_mcf_sta.json").write_text(
        json.dumps(si_mcf if si_mcf is not None else _OPEN))
    return p


# ── the hole the seam opens: an unmeasured candidate ───────────────────────

def test_a_child_that_died_is_refused_not_adopted(tmp_path):
    """THE DEFECT PART 3 MAKES REACHABLE. Three skipped comparisons are not
    three passed ones."""
    def _dead_child(project, container="", victims=()):
        return {"router_drc_before": 0, "router_drc": None,
                "nominal_setup_ns": None, "mcf_setup_ns": None,
                "candidate_sta_error": "the child wrote no candidate SPEF"}
    rec = S.run_once(_proj(tmp_path), runner=_dead_child)
    assert rec["decision"] == "REJECTED_CANDIDATE_DISCARDED", rec
    assert "NOT MEASURED" in rec["reason"]
    assert "router_drc" in rec["reason"]


def test_each_unmeasured_number_is_refused_by_its_own_name():
    base = {"router_drc": 0, "nominal_setup_ns": 2.4975, "mcf_setup_ns": -0.1}
    before = {"router_drc": 0, "nominal_setup_ns": 2.4975,
              "mcf_setup_ns": -0.266}
    for key in ("router_drc", "nominal_setup_ns", "mcf_setup_ns"):
        after = dict(base)
        after[key] = None
        ok, why = S.accepts(before, after)
        assert not ok, key
        assert key in why and "NOT MEASURED" in why, (key, why)


def test_an_unmeasured_baseline_is_not_a_comparison():
    """An unreadable router DRC report is not a clean route."""
    ok, why = S.accepts(
        {"router_drc": None, "nominal_setup_ns": 2.4975, "mcf_setup_ns": -0.266},
        {"router_drc": 0, "nominal_setup_ns": 2.4975, "mcf_setup_ns": -0.1})
    assert not ok
    assert "not measured" in why and "unmeasured baseline" in why


def test_a_fully_measured_improvement_is_still_accepted(tmp_path):
    """THE CONTROL that proves the new guard refuses the unmeasured and not
    everything."""
    def _good(project, container="", victims=()):
        return {"router_drc_before": 0, "router_drc": 0,
                "nominal_setup_ns": 2.4975, "mcf_setup_ns": -0.05}
    rec = S.run_once(_proj(tmp_path), runner=_good)
    assert rec["decision"] == "ADOPTED", rec["reason"]


# ── the router DRC count: 0 bytes is clean, unreadable is not zero ─────────

def test_an_empty_router_drc_report_is_the_tools_clean_zero(tmp_path):
    rpt = tmp_path / "routed_router.drc.rpt"
    rpt.write_text("")
    assert R._router_drc_count_from_report(rpt) == 0


def test_an_unreadable_router_drc_report_is_never_zero(tmp_path):
    rpt = tmp_path / "routed_router.drc.rpt"
    rpt.write_text("some text this reader does not know how to count\n")
    assert R._router_drc_count_from_report(rpt) is None


def test_the_router_drc_records_are_counted(tmp_path):
    rpt = tmp_path / "r.rpt"
    rpt.write_text("violation type: NS Metal\n  srcs: net:a\n"
                   "violation type: Short\n  srcs: net:b\n")
    assert R._router_drc_count_from_report(rpt) == 2


def test_a_missing_router_drc_report_is_not_a_count(tmp_path):
    assert R._router_drc_count_from_report(tmp_path / "nope.rpt") is None


# ── the child deck ─────────────────────────────────────────────────────────

def _deck(**kw):
    body = S.repair_tcl(folded_spef_c="/w/x.mcf_setup.spef",
                        victims=["net690", "clknet_leaf_21_i_clk"],
                        sdc_c="/w/constraint.sdc")
    args = dict(tech_lef_c="/pdk/tech.lef", cell_lef_c="/pdk/cells.lef",
                liberty_c="/pdk/ss.lib", pnr_dir_c="/w/pnr",
                txn_dir_c="/w/pnr/txn", sdc_c="/w/constraint.sdc",
                max_captable_c="/pdk/cap.captable", metal_prefix="Metal",
                thread_count=8, repair_body=body, filler_masters=["FILL1"])
    args.update(kw)
    return R._si_mcf_repair_child_tcl("subservient", **args)


@needs_tclsh
def test_the_child_deck_is_complete_tcl(tmp_path):
    """An unbalanced deck is the one defect a child cannot report: it dies
    before its receipt."""
    script = tmp_path / "chk.tcl"
    script.write_text(
        'set fh [open [lindex $argv 0] r]; set t [read $fh]; close $fh\n'
        'if {[info complete $t]} { puts COMPLETE } else { puts INCOMPLETE }\n')
    deck = tmp_path / "child.tcl"
    deck.write_text(_deck())
    r = subprocess.run([tclsh, str(script), str(deck)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "COMPLETE" in r.stdout


def test_the_child_restores_the_shipped_route_and_writes_only_a_candidate():
    deck = _deck()
    assert "read_def /w/pnr/routed.def" in deck
    for art in ("candidate.def", "candidate.odb", "candidate.spef",
                "subservient_pnr.v"):
        assert f"/w/pnr/txn/{art}" in deck, art
    # NOTHING is written at a shipping path — the whole point of a candidate.
    for line in deck.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for verb in ("write_def ", "write_db ", "write_verilog ", "write_spef "):
            if verb in stripped:
                assert "/w/pnr/txn/" in stripped, stripped


def test_the_candidate_gets_its_own_drc_report_not_the_shipping_routes():
    deck = _deck()
    assert "/w/pnr/txn/candidate_router_drc" in deck.replace(".", "_") \
        or "/w/pnr/txn/candidate_router.drc.rpt" in deck
    assert f"/w/pnr/{R.ROUTER_DRC_REPORT_NAME}" not in deck


def test_the_pass_is_si_aware_and_runs_exactly_once():
    deck = _deck()
    assert "read_spef /w/x.mcf_setup.spef" in deck
    assert deck.count("repair_timing") == 1
    assert "-max_passes 1" in deck
    assert "-repair_tns 0" in deck


def test_a_repair_that_changed_nothing_does_not_spend_a_reroute():
    """v1.8.43's rule, kept: a second route of the SAME netlist is a different
    route with its own quality lottery, and this pass may not spend one."""
    deck = _deck()
    noop = deck.index(R._SI_MCF_CHILD_NOOP)
    clear = deck.index("global_route")
    assert noop < clear, "the no-op guard must precede the reroute branch"
    assert "} else {" in deck[noop:clear]


def test_the_child_names_its_own_completion():
    assert R._SI_MCF_CHILD_DONE in _deck()


# ── arming the seam ────────────────────────────────────────────────────────

class _Pdk:
    name = "gf180mcuD"
    liberty = "/pdk/ss.lib"
    tech_lef = "/pdk/tech.lef"
    cell_lef = "/pdk/cells.lef"
    cell_gds = None
    site = "GF018hv5v_mcu_sc9"
    drc_deck = None
    metal_prefix = "Metal"
    antenna_diode_cell = None
    clk_buf = None
    clk_buf_root = None


def _phase3_tree(tmp_path, *, routed=True, sdc=True, folded=True):
    p = _proj(tmp_path)
    pnr = p / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    if routed:
        (pnr / "routed.def").write_text("VERSION 5.8 ;\nEND DESIGN\n")
    if sdc:
        (pnr / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    ex = p / "phase3" / "stage3" / "extracted" / "si_mcf"
    ex.mkdir(parents=True)
    if folded:
        (ex / "subservient.mcf_setup.spef").write_text("*SPEF \"IEEE 1481-1998\"\n")
    return p


def test_no_shipped_route_is_named_not_guessed(tmp_path):
    p = _phase3_tree(tmp_path, routed=False)
    with pytest.raises(R.SiMcfRepairSeamUnavailable) as e:
        R._si_mcf_repair_seam(p, "subservient", _Pdk(), "c")
    assert "routed.def" in str(e.value)


def test_no_folded_spef_means_this_is_not_an_si_aware_pass(tmp_path):
    p = _phase3_tree(tmp_path, folded=False)
    with pytest.raises(R.SiMcfRepairSeamUnavailable) as e:
        R._si_mcf_repair_seam(p, "subservient", _Pdk(), "c")
    assert "mcf_setup" in str(e.value) or "MCF-bounded" in str(e.value)
    assert "nominal repair" in str(e.value)


def test_an_unarmable_seam_records_not_executed_with_the_real_reason(tmp_path):
    """"there was no way to run it" must never arrive as a verdict about a
    candidate."""
    rec = S.run_once(
        _proj(tmp_path), runner=None,
        no_seam_reason="the execution seam could not be armed: no "
                       "phase3/stage3/pnr/routed.def exists")
    assert rec["decision"] == "NOT_EXECUTED"
    assert "routed.def" in rec["reason"]
    assert rec["after"] is None


def test_the_default_no_seam_reason_is_unchanged(tmp_path):
    rec = S.run_once(_proj(tmp_path))
    assert rec["decision"] == "NOT_EXECUTED"
    assert "no execution seam was supplied" in rec["reason"]


# ── the seam, with the child FAKED ─────────────────────────────────────────

def _fake_child(monkeypatch, tmp_path, *, write_candidate=True, log="",
                sta=None):
    calls = {}

    def _exec(container, cmd, timeout=1800, **kw):
        calls["cmd"] = cmd
        calls["marker"] = kw.get("marker")
        txn = tmp_path / "proj/phase3/stage3/pnr" / R._SI_MCF_TXN_DIRNAME
        if write_candidate:
            (txn / R._SI_MCF_CANDIDATE_DEF).write_text("DESIGN candidate ;\n")
            (txn / R._SI_MCF_CANDIDATE_ODB).write_text("odb")
            (txn / "subservient_pnr.v").write_text("module subservient(); endmodule\n")
            (txn / R._SI_MCF_CANDIDATE_SPEF).write_text("*SPEF\n")
            (txn / R._SI_MCF_CANDIDATE_DRC).write_text("")
        return 0, log or f"{R._SI_MCF_CHILD_DONE}\n", ""

    monkeypatch.setattr(R, "_docker_exec", _exec)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    monkeypatch.setattr(R, "_resolve_signoff_corner_libs",
                        lambda *a, **k: {"SS": "/pdk/ss.lib"})
    monkeypatch.setattr(R, "_max_captable_c", lambda *a, **k: "/pdk/cap")
    monkeypatch.setattr(R, "_sta_extra_liberties", lambda *a, **k: [])
    monkeypatch.setattr(R, "_def_reopen_extra_lefs_c", lambda *a, **k: [])
    monkeypatch.setattr(R, "_filler_masters_for_pdk", lambda pdk: ["FILL1"])
    monkeypatch.setattr(R, "_slot_geometry", lambda p: None)
    monkeypatch.setattr(R, "_l9_declared_die_area", lambda p: None)
    monkeypatch.setattr(R, "_l19_declared_die_area", lambda p: None)
    monkeypatch.setattr(R, "_padring_core_inset_um", lambda p: (None, None))
    monkeypatch.setattr(R, "_openroad_thread_count", lambda: 4)

    fake_sta = type(sys)("si_mcf_sta")
    fake_sta.run = lambda project, **kw: (sta if sta is not None else {
        "verdict": "PASS",
        "nominal": {"worst_setup_slack_ns": 2.4975},
        "corners": {"setup": {"worst_slack_after_ns": 0.02},
                    "hold": {"worst_slack_after_ns": 4.70}}})
    monkeypatch.setitem(sys.modules, "si_mcf_sta", fake_sta)
    return calls


def test_the_seam_runs_a_child_and_measures_the_candidate(tmp_path, monkeypatch):
    p = _phase3_tree(tmp_path)
    (p / "phase3/stage3/pnr" / R.ROUTER_DRC_REPORT_NAME).write_text("")
    calls = _fake_child(monkeypatch, tmp_path)
    seam = R._si_mcf_repair_seam(p, "subservient", _Pdk(), "c")
    after = seam(p, container="c", victims=["net690"])
    assert "openroad -no_init -exit" in calls["cmd"]
    assert calls["marker"] and calls["marker"].endswith("si_mcf_repair_child.tcl")
    assert after["child_completed"] is True
    assert after["router_drc_before"] == 0
    assert after["router_drc"] == 0
    assert after["nominal_setup_ns"] == 2.4975
    assert after["mcf_setup_ns"] == 0.02
    assert after["mcf_hold_ns"] == 4.70
    assert after["candidate_odb"] and after["candidate_spef"]


def test_a_noop_child_keeps_the_shipping_routes_own_drc_count(tmp_path, monkeypatch):
    """The route was not re-run; that is a MEASURED count, not a missing one."""
    p = _phase3_tree(tmp_path)
    (p / "phase3/stage3/pnr" / R.ROUTER_DRC_REPORT_NAME).write_text(
        "violation type: NS Metal\n")
    _fake_child(monkeypatch, tmp_path,
                log=f"{R._SI_MCF_CHILD_NOOP}: 1\n{R._SI_MCF_CHILD_DONE}\n")
    seam = R._si_mcf_repair_seam(p, "subservient", _Pdk(), "c")
    after = seam(p, container="c", victims=["net690"])
    assert after["repair_changed_no_instance"] is True
    assert after["router_drc"] == 1 == after["router_drc_before"]


def test_a_child_that_wrote_nothing_leaves_the_numbers_unmeasured(
        tmp_path, monkeypatch):
    p = _phase3_tree(tmp_path)
    (p / "phase3/stage3/pnr" / R.ROUTER_DRC_REPORT_NAME).write_text("")
    _fake_child(monkeypatch, tmp_path, write_candidate=False, log="died\n")
    seam = R._si_mcf_repair_seam(p, "subservient", _Pdk(), "c")
    after = seam(p, container="c", victims=["net690"])
    assert after["nominal_setup_ns"] is None
    assert after["mcf_setup_ns"] is None
    assert "could not be re-measured" in after["candidate_sta_error"]
    ok, why = S.accepts({"router_drc": 0, "nominal_setup_ns": 2.4975,
                         "mcf_setup_ns": -0.266}, after)
    assert not ok and "NOT MEASURED" in why


# ── the promotion ──────────────────────────────────────────────────────────

def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _adopted_tree(tmp_path):
    p = _phase3_tree(tmp_path)
    txn = p / "phase3/stage3/pnr" / R._SI_MCF_TXN_DIRNAME
    txn.mkdir(parents=True, exist_ok=True)
    (txn / R._SI_MCF_CANDIDATE_DEF).write_text("DESIGN candidate ;\n")
    (txn / R._SI_MCF_CANDIDATE_ODB).write_text("odb-candidate")
    (txn / "subservient_pnr.v").write_text("module subservient(); endmodule\n")
    (txn / R._SI_MCF_CANDIDATE_SPEF).write_text("*SPEF candidate\n")
    (p / "reports/phase3/si_mcf_repair.json").write_text(json.dumps(
        {"program": "si_mcf_repair", "decision": "ADOPTED"}))
    return p, txn


def _after_of(txn):
    return {"candidate_def": str(txn / R._SI_MCF_CANDIDATE_DEF),
            "candidate_odb": str(txn / R._SI_MCF_CANDIDATE_ODB),
            "candidate_netlist": str(txn / "subservient_pnr.v"),
            "candidate_spef": str(txn / R._SI_MCF_CANDIDATE_SPEF)}


def test_an_adoption_promotes_the_candidate_and_sidelines_what_it_replaced(
        tmp_path, monkeypatch):
    p, txn = _adopted_tree(tmp_path)
    routed = p / "phase3/stage3/pnr/routed.def"
    before_sha = _sha(routed)
    seen = []
    for name in ("step_gds", "step_drc", "step_lvs"):
        monkeypatch.setattr(R, name, (lambda n: (
            lambda proj, top, pdk, container: (
                seen.append(n) or R.StepResult(n, "PASS", 0.0, "ok"))))(name))
    notes = []
    R._si_mcf_repair_promote(p, "subservient", _Pdk(), "c",
                             _after_of(txn), notes)
    assert routed.read_text() == "DESIGN candidate ;\n"
    side = p / "phase3/stage3/pnr/routed.def.pre_si_mcf"
    assert side.is_file() and _sha(side) == before_sha
    assert (p / "phase3/stage3/pnr/subservient_pnr.v").is_file()
    assert (p / "phase3/stage3/extracted/subservient.spef").is_file()
    assert (p / "phase3/stage3/pnr/routed_si_mcf.odb").read_text() == "odb-candidate"
    assert seen == ["step_gds", "step_drc", "step_lvs"]
    rec = json.loads((p / "reports/phase3/si_mcf_repair.json").read_text())
    assert len(rec["promotion"]["promoted"]) == 4
    assert [r["step"] for r in rec["promotion"]["rederived"]] == \
        ["gds", "drc", "lvs"]
    assert rec["promotion"]["refused"] == ""


def test_a_rederivation_that_failed_is_disclosed_not_swallowed(
        tmp_path, monkeypatch):
    p, txn = _adopted_tree(tmp_path)
    monkeypatch.setattr(R, "step_gds",
                        lambda *a, **k: R.StepResult("gds", "PASS"))
    monkeypatch.setattr(R, "step_drc",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(R, "step_lvs",
                        lambda *a, **k: R.StepResult("lvs", "PASS"))
    R._si_mcf_repair_promote(p, "subservient", _Pdk(), "c", _after_of(txn), [])
    rec = json.loads((p / "reports/phase3/si_mcf_repair.json").read_text())
    drc = [r for r in rec["promotion"]["rederived"] if r["step"] == "drc"][0]
    assert drc["status"] == "ERROR" and "boom" in drc["detail"]


def test_an_incomplete_candidate_promotes_nothing(tmp_path, monkeypatch):
    p, txn = _adopted_tree(tmp_path)
    routed = p / "phase3/stage3/pnr/routed.def"
    before_sha = _sha(routed)
    called = []
    for name in ("step_gds", "step_drc", "step_lvs"):
        monkeypatch.setattr(R, name, lambda *a, **k: called.append(1))
    after = _after_of(txn)
    after["candidate_odb"] = None
    notes = []
    R._si_mcf_repair_promote(p, "subservient", _Pdk(), "c", after, notes)
    assert _sha(routed) == before_sha, "the shipping route was mutated"
    assert not called, "nothing may be re-derived when nothing was promoted"
    rec = json.loads((p / "reports/phase3/si_mcf_repair.json").read_text())
    assert rec["promotion"]["promoted"] == []
    assert "NOTHING was promoted" in rec["promotion"]["refused"]
    assert "routed_si_mcf.odb" in rec["promotion"]["refused"]


def test_a_refusal_leaves_the_shipping_route_byte_identical(tmp_path, monkeypatch):
    """The whole contract in one assertion: only an ADOPTED decision ever
    reaches the promotion, and everything else changes no shipped byte."""
    p = _phase3_tree(tmp_path)
    routed = p / "phase3/stage3/pnr/routed.def"
    before_sha = _sha(routed)

    def _worse(project, container="", victims=()):
        return {"router_drc_before": 0, "router_drc": 7,
                "nominal_setup_ns": 2.4975, "mcf_setup_ns": 0.1}
    rec = S.run_once(p, runner=_worse)
    assert rec["decision"] == "REJECTED_CANDIDATE_DISCARDED"
    assert _sha(routed) == before_sha
    assert not (p / "phase3/stage3/pnr/routed.def.pre_si_mcf").exists()


# ── the real-container control ─────────────────────────────────────────────

_REAL = os.environ.get("VIBEIC_SI_MCF_PROJECT", "").strip()


@pytest.mark.skipif(
    not _REAL,
    reason="VIBEIC_SI_MCF_PROJECT is unset: the real-report control needs a "
           "finished phase-3 project dir whose si_mcf_sta reported FAIL")
def test_the_plan_and_the_deck_come_from_a_real_runs_own_reports():
    """The control on r16's REAL reports: the victims, the folded SPEF and the
    restored route are the ones that run produced — none is invented here."""
    proj = Path(_REAL)
    rep = json.loads((proj / "reports/phase3/si_mcf_sta.json").read_text())
    assert rep.get("verdict") == "FAIL", "this control needs an OPEN envelope"
    plan = S.plan(proj)
    assert plan["run"] is True and plan["victims"], plan
    folded = sorted((proj / "phase3/stage3/extracted").glob(
        "si_mcf/*.mcf_setup.spef"))
    assert folded, "the run wrote no MCF-bounded setup SPEF"
    body = S.repair_tcl(folded_spef_c=str(folded[0]), victims=plan["victims"],
                        sdc_c=str(proj / "phase3/stage3/pnr/constraint.sdc"))
    for net in plan["victims"]:
        assert net in body
    assert str(folded[0]) in body
    assert body.count("repair_timing") == 1
