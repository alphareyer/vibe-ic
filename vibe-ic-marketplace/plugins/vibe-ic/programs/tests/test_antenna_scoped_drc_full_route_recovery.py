"""A scoped antenna route may add DRC and damage unrelated wires.

The recovery must start from the saved ODB in a fresh EDA session.  The fake
below supplies only EDA file writes and its native transcript; the production
resume builder, transaction selector and emitted antenna Tcl remain real.
"""
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
        "",
    ))


def _run(tmp_path, monkeypatch, *, clean=True):
    out = tmp_path / "pnr"
    out.mkdir()
    (out / R._ANTENNA_PASS_CHECKPOINT_NAME).write_bytes(b"verified odb")
    deck = tmp_path / "pnr.tcl"
    deck.write_text(_deck())
    calls = []

    def eda_writes(_container, _cmd, products, **_kwargs):
        calls.append(_cmd)
        for product in products:
            Path(product).write_text("EDA output\n")
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
    rec = R._pnr_rollback_refused_antenna_repair(
        container="eda", out_dir=out, out_dir_c="/w/pnr", pnr_tcl=deck,
        log_text=_REFUSAL, hard_ceiling_s=60)
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
