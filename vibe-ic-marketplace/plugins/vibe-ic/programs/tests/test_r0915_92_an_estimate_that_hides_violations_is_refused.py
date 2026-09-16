"""R-0915-92: the resizer's estimated parasitics may not hide the grader's violations.

MEASURED on subservient x gf180mcuD. r28 (main bd308d420) signed off SS setup
-0.71 ns; a full-run bisect put it on the landing of `_rsz_parasitics_source_tcl`
(r27's result, +0.03 ns, byte-identical PnR, returns with it reverted). On the
post-route SDR checkpoint: SPEF worst setup slack -1.108 ns, and +4.013 ns after
`estimate_parasitics -global_routing`, so `repair_timing -setup` printed RSZ-0098
"No setup violations found". The guarded helper, run on that same checkpoint in
the pinned image, printed REFUSED_HIDES_VIOLATIONS, re-read the SPEF, found 26
violating endpoints and ended at +0.132 ns.

These cases execute the EMITTED Tcl with the STA calls stubbed, both directions.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phase3_one_shot_runner as R  # noqa: E402
from _tcl_walk import walk          # noqa: E402

SPEF = "/grader/sdr_pass.spef"


def _run(tmp_path, spef_wns, est_wns, est_fails=False):
    walker = f"""
namespace eval sta {{}}
set ::phase spef
proc sta::worst_slack {{args}} {{ if {{$::phase eq "spef"}} {{ return {spef_wns} }} else {{ return {est_wns} }} }}
proc estimate_parasitics {{args}} {{ {"error EST-0005" if est_fails else "set ::phase est"} }}
proc read_spef {{path}} {{ set ::phase spef; puts "STUB_READ_SPEF $path" }}
source [lindex $argv 0]
puts "PHASE_AFTER $::phase"
"""
    out, err, _ = walk(walker, R._rsz_parasitics_source_tcl("SDR_SETUP", SPEF), tmp_path)
    assert not err.strip(), err
    return out


def test_an_estimate_that_hides_the_graders_violations_is_refused(tmp_path):
    out = _run(tmp_path, -1.108, 4.013)
    assert "REFUSED_HIDES_VIOLATIONS spef_wns=-1.108 est_wns=4.013" in out
    assert f"STUB_READ_SPEF {SPEF}" in out
    assert "PHASE_AFTER spef" in out


def test_an_optimistic_estimate_that_still_shows_violations_is_kept(tmp_path):
    out = _run(tmp_path, -1.108, -0.4)
    assert "REFUSED" not in out and "STUB_READ_SPEF" not in out
    assert "estimate_parasitics -global_routing OK" in out
    assert "PHASE_AFTER est" in out


def test_a_grader_without_violations_never_reaches_the_refusal(tmp_path):
    out = _run(tmp_path, 0.5, 4.013)
    assert "REFUSED" not in out and "STUB_READ_SPEF" not in out
    assert "PHASE_AFTER est" in out


def test_a_failed_estimate_still_says_wire_load(tmp_path):
    out = _run(tmp_path, -1.108, 4.013, est_fails=True)
    assert "FAILED EST-0005" in out and "REFUSED" not in out


def test_without_a_grader_spef_the_helper_is_unchanged():
    tcl = R._rsz_parasitics_source_tcl("SDR")
    assert "worst_slack" not in tcl and "read_spef" not in tcl


def test_both_sdr_call_sites_pass_the_graders_spef():
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    for tag in ("SDR", "SDR_SETUP"):
        assert f'_rsz_parasitics_source_tcl("{tag}", f"{{out_dir_c}}/sdr_pass.spef")' in src, tag


# ── (2) the refusal reaches the step's record, not only Tcl stdout ─────────
import json  # noqa: E402


def _stage_tree(tmp_path, refusal_text):
    out = tmp_path / "pnr"
    for stage, dirname in R._SDR_TXN_DIRS.items():
        (out / dirname).mkdir(parents=True, exist_ok=True)
        (out / dirname / "receipt.tsv").write_text(
            "status\treason\tbefore_router_drc\tafter_router_drc\n"
            "ACCEPTED\trouter_drc_preserved_clean\t0\t0\n")
    (out / "sdr_child_postroute_drv_repair.log").write_text(
        "[INFO RSZ-0098] something\n" + refusal_text)
    return out


def test_the_refusal_is_in_the_transaction_record(tmp_path):
    # The line the emitted Tcl actually prints, from the stub walk above.
    printed = _run(tmp_path, -1.108, 4.013)
    line = next(l for l in printed.splitlines() if "REFUSED_HIDES_VIOLATIONS" in l)
    proj = tmp_path / "proj"
    out = _stage_tree(tmp_path, line + "\n")
    recs = R._disclose_sdr_transactions(proj, out, [], {})
    rec = next(r for r in recs if r["stage"] == "postroute_drv_repair")
    assert rec["parasitics_estimate_refused"] == [{
        "tag": "SDR_SETUP", "grader_spef_wns": "-1.108", "estimate_wns": "4.013",
        "spef_reread": "OK", "log": "sdr_child_postroute_drv_repair.log"}]
    doc = json.loads((proj / "reports/phase3/sdr_transactions.json").read_text())
    assert doc["parasitics_estimate_refusals"] == 1


def test_no_refusal_line_means_no_refusal_in_the_record(tmp_path):
    printed = _run(tmp_path, -1.108, -0.4)   # AES-DRV shape: estimate kept
    proj = tmp_path / "proj"
    out = _stage_tree(tmp_path, printed)
    recs = R._disclose_sdr_transactions(proj, out, [], {})
    assert all("parasitics_estimate_refused" not in r for r in recs)
    doc = json.loads((proj / "reports/phase3/sdr_transactions.json").read_text())
    assert doc["parasitics_estimate_refusals"] == 0


def test_a_failed_spef_reread_is_recorded_as_failed(tmp_path):
    line = ("SDR_RSZ_PARASITICS: REFUSED_HIDES_VIOLATIONS spef_wns=-2.0 "
            "est_wns=1.0 -- SPEF re-read FAILED no such file; this repair sees "
            "the estimate\n")
    proj = tmp_path / "proj"
    out = _stage_tree(tmp_path, line)
    recs = R._disclose_sdr_transactions(proj, out, [], {})
    rec = next(r for r in recs if r["stage"] == "postroute_drv_repair")
    assert rec["parasitics_estimate_refused"][0]["spef_reread"] == "FAILED"
