"""A widthless PSM edge is screened against the wire that actually covers it."""
import hashlib
import json
import os
import sys
from pathlib import Path

PROGRAMS = Path(os.environ.get("T52_PROGRAMS", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PROGRAMS))

import em_peak_current_authority_check as authority


def _project(tmp_path, *, wide=True, matching_digest=True):
    project = tmp_path / "ic"
    reports = project / "reports/phase3"
    pnr = project / "phase3/stage3/pnr"
    reports.mkdir(parents=True)
    pnr.mkdir(parents=True)
    # Two wires on the same net and layer. The narrow wire is elsewhere, so
    # its per-layer minimum cannot be used as the wide segment's width.
    width = 24000 if wide else 3200
    def_text = ("VERSION 5.8 ;\nUNITS DISTANCE MICRONS 2000 ;\n"
                "SPECIALNETS 1 ;\n- PWR + USE POWER\n"
                " + ROUTED Metal4 3200 + SHAPE RING ( 100000 100000 ) "
                "( 200000 100000 )\n"
                f" NEW Metal4 {width} + SHAPE STRIPE ( 1000000 1000000 ) "
                "( 1000000 2000000 ) ;\nEND SPECIALNETS\n")
    final_def = pnr / "routed.def"
    final_def.write_text(def_text)
    (reports / "em_segments.csv").write_text(
        "Node0 Layer,Node0 X location,Node0 Y location,"
        "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
        "Metal4,500,600,Metal4,500,700,0.003\n")
    (reports / "em.rpt").write_text(
        "Net : PWR\nTotal power : 4.0e-02 W\n"
        "Supply voltage : 5.0e+00 V\nMaximum current : 3.0e-03 A\n")
    digest = hashlib.sha256(final_def.read_bytes()).hexdigest()
    (reports / "em.json").write_text(json.dumps({
        "power_nets": ["PWR"], "subject_def": "phase3/stage3/pnr/routed.def",
        "subject_def_sha256": digest if matching_digest else "0" * 64}))
    jmax = tmp_path / "jmax.json"
    jmax.write_text(json.dumps({"layers": {"Metal4": {
        "kind": "routing", "width_um": 1.6,
        "jmax_mA_per_um": 0.67}}}))
    return project, jmax


def test_post_resize_screen_uses_same_net_local_def_width(tmp_path):
    project, jmax = _project(tmp_path)
    verdict, result = authority.evaluate(project, jmax, None, 0.1)
    assert verdict == "PASS", result
    assert result["supply_current_screen"]["screened"] is True
    assert result["jmax_screen"]["offender_count"] == 0
    assert result["jmax_screen"]["summary"]["local_def_width_uses"] == 1


def test_real_narrow_wire_and_stale_def_still_fail(tmp_path):
    narrow, jmax = _project(tmp_path / "narrow", wide=False)
    assert authority.jmax_tier(narrow, jmax, None, 0.1)["verdict"] == "FAIL"
    stale, jmax = _project(tmp_path / "stale", matching_digest=False)
    assert authority.jmax_tier(stale, jmax, None, 0.1)["verdict"] == "FAIL"
    wrong_net, jmax = _project(tmp_path / "wrong_net")
    summary = wrong_net / "reports/phase3/em.json"
    doc = json.loads(summary.read_text())
    doc["power_nets"] = ["GND"]
    summary.write_text(json.dumps(doc))
    assert authority.jmax_tier(wrong_net, jmax, None, 0.1)["verdict"] == "FAIL"
