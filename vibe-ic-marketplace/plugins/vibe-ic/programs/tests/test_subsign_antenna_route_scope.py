"""Antenna sign-off reads reports for the shipped route and tool output only."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eda_report_audit import _check_antenna
import phase3_one_shot_runner as runner


def _tool_report(root: Path, *, clean: bool) -> None:
    out = root / "reports" / "phase3"
    out.mkdir(parents=True)
    n = 0 if clean else 2
    (out / "antenna.rpt").write_text(
        "# OpenROAD check_antennas tool output\n"
        f"antenna check: {n} net violations, {n} pin violations\n"
        f"antenna clean: {'YES' if clean else 'NO'}\n" + "# data\n" * 40)


def test_rejected_route_iteration_reports_are_preserved_outside_signoff(tmp_path):
    _tool_report(tmp_path, clean=True)
    pnr = tmp_path / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    # A failed EDA session can leave unsigned report bytes; an older invocation
    # may have left a second report in the same directory.
    (pnr / "antenna_iter_0.rpt").write_text("Net: rejected\n" * 40)
    (pnr / "antenna_iter_1.rpt").write_text("Net: previous\n" * 40)
    assert any(f.rule == "ANTENNA_NO_TOOL_SIGNATURE"
               for f in _check_antenna(tmp_path).findings)
    assert runner._archive_antenna_iteration_reports(pnr) == [
        "antenna_iter_0.rpt", "antenna_iter_1.rpt"]
    assert sorted(p.name for p in (pnr / ".antenna_report_history").iterdir()) == [
        "antenna_iter_0.0.rpt", "antenna_iter_1.0.rpt"]
    cleaned = _check_antenna(tmp_path)
    assert cleaned.passed
    assert not any(f.rule == "ANTENNA_NO_TOOL_SIGNATURE"
                   for f in cleaned.findings)
    # Re-entry preserves a later route's evidence in a distinct file.
    (pnr / "antenna_iter_0.rpt").write_text("Net: another\n" * 40)
    assert runner._archive_antenna_iteration_reports(pnr) == ["antenna_iter_0.rpt"]
    assert (pnr / ".antenna_report_history" / "antenna_iter_0.1.rpt").exists()


def test_rollback_metadata_is_not_an_antenna_tool_report(tmp_path):
    _tool_report(tmp_path, clean=True)
    out = tmp_path / "reports" / "phase3"
    (out / "antenna_repair_transaction.json").write_text(json.dumps({
        "program": "phase3_one_shot_runner:_pnr_rollback_refused_antenna_repair",
        "status": "ROLLED_BACK", "route_verified_at_ship": True,
        "note": "this is runner metadata, not a tool report" * 10,
    }))
    r = _check_antenna(tmp_path)
    assert r.passed, [f.rule for f in r.findings]
    assert r.summary["files_found"] == 1


def test_real_shipped_route_antenna_violation_still_fails(tmp_path):
    _tool_report(tmp_path, clean=False)
    r = _check_antenna(tmp_path)
    assert not r.passed
    assert r.summary["violations"] == 4
