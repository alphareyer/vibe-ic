"""A DRV slack row cannot become setup or hold WNS."""

import hashlib
import json

import phase3_one_shot_runner as runner
import postroute_timing_repair_status_gen as status


def test_mcorner_trigger_reads_only_the_explicit_timing_slack():
    report = """=== SETUP corner: process=SS ===
worst slack max 2.70
  2.70 slack (MET)
max_fanout
u_core/wire49/Z 25.00 4.00 -21.00 slack (VIOLATED)
SIGNOFF_DRV_CENSUS max_fanout violators=1
=== HOLD corner: process=FF ===
worst slack min 0.44
  0.44 slack (MET)
max_fanout
u_core/wire49/Z 25.00 4.00 -21.00 slack (VIOLATED)
"""
    assert runner._parse_mcorner_ocv_slacks(report) == (2.70, 0.44)
    assert runner._worst_slack(report.split("=== HOLD")[0]) == 2.70


def test_status_generator_cannot_overrule_measured_step32_drv_decision(tmp_path):
    project = tmp_path / "ic"
    sta = project / "reports/phase3/sta_spef_based.rpt"
    sta.parent.mkdir(parents=True)
    sta.write_text("worst slack max 2.70\n  2.70 slack (MET)\n")
    (sta.parent / "mcorner_ocv_stance.json").write_text(json.dumps({
        "multi_process_corner": True, "report": "sta_mcorner_ocv.rpt",
        "violated_corners": [], "setup_worst_slack_ns": 2.70,
        "hold_worst_slack_ns": 0.44,
    }))
    source = sta.parent / "librelane_postroute_repair.json"
    source.write_text(json.dumps({"verdict": "FAIL", "adopted": "32-cand01"}))
    decision_dir = project / "phase3/stage3/postroute_timing_repair"
    decision_dir.mkdir(parents=True)
    (decision_dir / "postroute_timing_repair_decision.json").write_text(
        json.dumps({
            "repair_needed": True, "action": "candidate_adopted",
            "baseline": {"drv_count": 36}, "final": {"drv_count": 9},
            "source_report": "reports/phase3/librelane_postroute_repair.json",
            "source_report_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }))
    assert status.main([str(project)]) == 0
    assert not (decision_dir / "no_repair_needed.flag").exists()
    log = json.loads((decision_dir / "repair_log.json").read_text())
    assert log["verdict"] == "REPAIR_REQUIRED"


def test_stale_step32_receipt_does_not_certify_no_repair(tmp_path):
    project = tmp_path / "ic"
    sta = project / "reports/phase3/sta_spef_based.rpt"
    sta.parent.mkdir(parents=True)
    sta.write_text("  2.70 slack (MET)\n")
    output = project / "phase3/stage3/postroute_timing_repair"
    output.mkdir(parents=True)
    (output / "no_repair_needed.flag").write_text("stale\n")
    (output / "postroute_timing_repair_decision.json").write_text(json.dumps({
        "repair_needed": False,
        "source_report": "reports/phase3/librelane_postroute_repair.json",
        "source_report_sha256": "0" * 64,
    }))
    assert status.main([str(project)]) == 2
    assert not (output / "no_repair_needed.flag").exists()
