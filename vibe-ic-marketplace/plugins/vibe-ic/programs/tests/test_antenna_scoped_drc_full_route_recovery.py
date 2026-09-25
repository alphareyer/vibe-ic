"""A scoped antenna route may add DRC and damage unrelated wires.

The recovery must start from the saved ODB in a fresh EDA session.  The fake
below supplies only EDA file writes and its native transcript; the production
resume builder, transaction selector and emitted antenna Tcl remain real.
"""
import json
import inspect
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


_REFUSAL = (
    "ANTENNA_NATIVE_REROUTE_NONFATAL: DRT-0712\n"
    "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: "
    "checkpoint=/w/pnr/antenna_pass_pre.odb "
    "reason=ANTENNA_DIODE_ROLLED_BACK: 20 net(s) -- DRT-0712\n"
)


def _deck() -> str:
    return "\n".join((
        "read_verilog /w/design.v", "link_design top",
        R._PNR_RESUME_ELIDE_BEGIN, "puts BASE_ROUTE", R._PNR_RESUME_ELIDE_END,
        R._pnr_stage_begin("postroute_drv_repair"),
        "puts DRV_ALREADY_IN_CHECKPOINT",
        R._pnr_stage_end("postroute_drv_repair"),
        R._pnr_stage_begin("postroute_antenna_repair"),
        "puts ANTENNA_STAGE",
        R._pnr_stage_end("postroute_antenna_repair"),
        R._pnr_stage_begin("postroute_antenna_reconverge"),
        "puts ANTENNA_RECONVERGE",
        R._pnr_stage_end("postroute_antenna_reconverge"),
        "write_def /w/pnr/routed.def",
        "write_verilog /w/pnr/top_pnr.v", "write_def /w/pnr/top.def",
        R._pnr_stage_begin("postroute_setup_repair_estimate"),
        "puts ESTIMATE_WOULD_MUTATE_UNSHIPPED_ROUTE",
        R._pnr_stage_end("postroute_setup_repair_estimate"),
        "exit", "",
    ))


def _run(tmp_path, monkeypatch, *, clean=True, refusal=_REFUSAL,
         route_change=False, unrouted_gate=False):
    out = tmp_path / "pnr"
    out.mkdir()
    (out / R._ANTENNA_PASS_CHECKPOINT_NAME).write_bytes(b"verified odb")
    deck = tmp_path / "pnr.tcl"
    deck.write_text(_deck())
    calls = []

    def eda_writes(_container, _cmd, products, **_kwargs):
        calls.append(_cmd)
        for product in products:
            route_end = "( 11 0 )" if (route_change and
                "pnr_antenna_isolated_adopt" in _cmd and
                Path(product).name == "routed.def") else "( 10 0 )"
            Path(product).write_text(
                "NETS 1 ;\n    - n ( a p ) + ROUTED M1 ( 0 0 ) "
                f"{route_end} ;\nEND NETS\n"
                if str(product).endswith(".def") else "EDA output\n")
        if "pnr_antenna_isolated_retry" in _cmd:
            if clean:
                return (0,
                        "[INFO DRT-0634] Scoped detailed routing touched "
                        "1 net(s) and left others byte-identical.\n"
                        "[INFO DRT-0702] Post-route verification: "
                        "0 violation(s).\n"
                        "[INFO DRT-0711] Scoped detailed routing: "
                        "whole-design violations 0 on entry, 0 on exit\n"
                        "ANTENNA_ISOLATED_VERIFIED: lost=0 held_shrunk=0 "
                        "moved=0 placement=0 antenna=0\n"
                        "[INFO ANT-0002] Found 0 net violations.\n"
                        "[INFO ANT-0001] Found 0 pin violations.\n", "")
            return (1, "ANTENNA_ISOLATED_WIRE_DAMAGE: unrelated net\n", "")
        if "pnr_antenna_isolated_adopt" in _cmd:
            return (0,
                    "[INFO DRT-0702] Post-route verification: "
                    "0 violation(s).\n"
                    + ("[WARNING ANT-0018] 1 net with a gate and no "
                       "routing was NOT checked.\n" if unrouted_gate else "")
                    +
                    "ANTENNA_ISOLATED_TAIL_VERIFIED: antenna=0 "
                    "placement=0\n", "")
        if "pnr_antenna_full_retry" in _cmd:
            if clean == "residual":
                return (0,
                        "ANTENNA_FULL_ROUTE_VERIFIED: router DRC 0\n"
                        "ANTENNA_LOOP_CONVERGED: iter=1\n"
                        "[INFO DRT-0702] Post-route verification: "
                        "0 violation(s).\n"
                        "[INFO ANT-0002] Found 1 net violation.\n"
                        "[INFO ANT-0001] Found 1 pin violation.\n", "")
            if clean:
                return (0,
                        "ANTENNA_FULL_ROUTE_VERIFIED: router DRC 0, all "
                        "previously wired nets still wired, placement 0\n"
                        "ANTENNA_LOOP_CONVERGED: iter=1\n"
                        "[INFO DRT-0702] Post-route verification: "
                        "0 violation(s).\n"
                        "REPAIR_ANTENNA_DONE: diode=D iter=0 margin=0\n"
                        "[INFO ANT-0002] Found 0 net violations.\n"
                        "[INFO ANT-0001] Found 0 pin violations.\n", "")
            return (1, "ANTENNA_FULL_ROUTE_REFUSED: DRT-0712\n", "")
        return 0, "[INFO DRT-0702] Post-route verification: 0 violation(s).\n", ""

    monkeypatch.setattr(R, "_declared_session_exec", eda_writes)
    kwargs = dict(
        container="eda", out_dir=out, out_dir_c="/w/pnr", pnr_tcl=deck,
        log_text=refusal, hard_ceiling_s=60)
    if "antenna_diode_cell" in inspect.signature(
            R._pnr_rollback_refused_antenna_repair).parameters:
        kwargs["antenna_diode_cell"] = "D"
    rec = R._pnr_rollback_refused_antenna_repair(**kwargs)
    return rec, calls, out


def test_scoped_drc_recovers_from_untouched_odb_and_verifies_full_route(
        tmp_path, monkeypatch):
    rec, calls, out = _run(tmp_path, monkeypatch)
    assert rec["status"] == "RECOVERED"
    assert rec["antenna_repair"] == "APPLIED"
    assert rec["route_verified"] is True
    assert not R._antenna_full_retry_modified_after_verification(
        rec["combined_log"])
    assert len(calls) == 1
    assert (out / "antenna_full_retry_seed.odb").read_bytes() == b"verified odb"
    tcl = (out / "pnr_antenna_full_retry.tcl").read_text()
    assert "read_db /w/pnr/antenna_full_retry_seed.odb" in tcl
    assert "set ::_vic_antenna_full_route 1" in tcl
    assert "DRV_ALREADY_IN_CHECKPOINT" not in tcl
    assert "ANTENNA_STAGE" in tcl
    stage = R._antenna_repair_tcl(type("P", (), {"antenna_diode_cell": "D"})(),
                                  "/w/pnr")
    assert "detailed_route {*}$_vic_drc_opt" in stage
    assert "ANTENNA_FULL_ROUTE_LOST_WIRES" in stage
    assert "ANTENNA_FULL_ROUTE_PLACEMENT_FAILED" in stage
    receipt = R._disclose_antenna_rollback(
        tmp_path, out, [rec], _REFUSAL + rec["combined_log"])
    doc = json.loads(receipt.read_text())
    assert doc["status"] == "RECOVERED"
    assert doc["antenna_repair"] == "APPLIED"
    assert doc["route_verified_at_ship"] is True


def test_failed_full_route_restores_original_seed_and_names_failure(
        tmp_path, monkeypatch):
    rec, calls, out = _run(tmp_path, monkeypatch, clean=False)
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert "ANTENNA_FULL_ROUTE_NOT_VERIFIED" in rec["reason"]
    assert len(calls) == 2
    rollback = (out / "pnr_antenna_rollback.tcl").read_text()
    assert "read_db /w/pnr/antenna_full_retry_seed.odb" in rollback
    assert "ANTENNA_STAGE" not in rollback


def test_router_clean_but_antenna_dirty_is_not_adopted(tmp_path, monkeypatch):
    rec, calls, _ = _run(tmp_path, monkeypatch, clean="residual")
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert "ANTENNA_FULL_ROUTE_NOT_VERIFIED" in rec["reason"]
    assert len(calls) == 2


_SCOPED_DAMAGE = (
    "ANTENNA_ROUTER: version=fork scoped_reroute=1\n"
    "ANTENNA_SCOPED_HELD_WIRE_DAMAGE: 3 net(s) outside the antenna set "
    "lost wire\n"
    "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: "
    "checkpoint=/w/pnr/antenna_pass_pre.odb "
    "reason=ANTENNA_DIODE_ROLLED_BACK: 5 net(s); "
    "native_error=ANTENNA_SCOPED_HELD_WIRE_DAMAGE: 3 net(s)\n"
)


def test_scoped_damage_retries_same_target_nets_in_fresh_session(
        tmp_path, monkeypatch):
    rec, calls, out = _run(
        tmp_path, monkeypatch, refusal=_SCOPED_DAMAGE)
    assert rec["status"] == "RECOVERED"
    assert rec["antenna_repair"] == "APPLIED"
    assert rec["route_verified"] is True
    assert len(calls) == 2
    tcl = (out / "pnr_antenna_isolated_retry.tcl").read_text()
    assert "read_db /w/pnr/antenna_isolated_seed.odb" in tcl
    assert "repair_antennas D -iterations 1 -ratio_margin 0 -reroute" in tcl
    assert "ANTENNA_ISOLATED_WIRE_DAMAGE" in tcl
    assert "ANTENNA_ISOLATED_EXISTING_CELL_MOVED" in tcl
    tail = (out / "pnr_antenna_isolated_adopt.tcl").read_text()
    assert "read_db /w/pnr/antenna_isolated_candidate.odb" in tail
    assert "ANTENNA_STAGE" not in tail
    assert "ESTIMATE_WOULD_MUTATE_UNSHIPPED_ROUTE" not in tail
    assert "ANTENNA_ISOLATED_TAIL_REFUSED" in tail
    assert tail.index("ANTENNA_ISOLATED_TAIL_REFUSED") < tail.rindex("\nexit")
    receipt = R._disclose_antenna_rollback(
        tmp_path, out, [rec], _SCOPED_DAMAGE + rec["combined_log"])
    assert json.loads(receipt.read_text())["route_verified_at_ship"] is True


def test_scoped_damage_retry_cannot_ship_unverified_route(
        tmp_path, monkeypatch):
    rec, calls, out = _run(
        tmp_path, monkeypatch, clean=False, refusal=_SCOPED_DAMAGE)
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert len(calls) == 2
    assert "ANTENNA_STAGE" not in (out / "pnr_antenna_rollback.tcl").read_text()


def test_scoped_retry_rejects_changed_shipped_net_geometry(tmp_path, monkeypatch):
    rec, calls, _ = _run(tmp_path, monkeypatch, refusal=_SCOPED_DAMAGE,
                         route_change=True)
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert len(calls) == 3


def test_scoped_retry_rejects_zero_antenna_with_unrouted_gate(
        tmp_path, monkeypatch):
    rec, calls, _ = _run(tmp_path, monkeypatch, refusal=_SCOPED_DAMAGE,
                         unrouted_gate=True)
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert len(calls) == 3
