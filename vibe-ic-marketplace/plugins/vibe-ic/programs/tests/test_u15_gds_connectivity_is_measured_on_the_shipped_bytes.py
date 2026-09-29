"""U15 (IC_BLOCKER_AUDIT 2026-09-29) — step 37.3 may not read PASS for the
shipped GDS's connectivity on a self-XOR or on a DEF-extracted LVS.

MEASURED on the spm IC-path tail (lane spmic2, 8HD-4, plugin 1.26.x, run
`cx_spmic2_run`, 2026-09-28): `reports/phase3/gds_xor.json` says PASS with 0
design-layer differences, but its reference is `chip_top.prefinish.gds` -- the
SAME KLayout stream-out kept before fill and seal ring -- and step 31's
`lvs_verdict.json` was written by the DEF-direct Magic extraction
(`_run_extraction_lvs`), with no layout extracted from the streamed bytes. No
measurement in that run read the connectivity of the GDS that ships.

The receipt below is that run's receipt, trimmed to the fields the judge reads.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gds_xor_check as G  # noqa: E402

SHIPPED = b"GDS-shipped-bytes"
LIVE = hashlib.sha256(SHIPPED).hexdigest()

_SPMIC2_RECEIPT = {
    "gate": "gds_xor_check", "verdict": "PASS", "rc": 0,
    "attestation": {"gds": "phase3/stage3/pnr/spm.gds",
                    "streamout_engine": "klayout", "binding": "attested"},
    "shipped_sha256_live": LIVE,
    "reference": {"kind": "retained",
                  "path": "phase3/stage3/pnr/chip_top.prefinish.gds",
                  "note": "the stream-out the gds step kept BEFORE fill and seal "
                          "ring, after grid snap; design-layer differences are "
                          "expected to be exactly 0"},
    "layers_compared": 45, "design_layer_differences": [],
    "design__xor_difference__count": 0,
    "reason": "the shipped GDS matches the retained pre-finishing reference on "
              "every design layer (0 differences across 45 layer(s) compared)",
}

#: step 31's verdict as the DEF-direct extraction wrote it on that run.
_SPMIC2_LVS_VERDICT = {
    "status": "PASS", "result": "PASS", "finding": "LVS_MATCH_POWER_VERIFIED",
    "compare_performed": True, "compare_evidence": "reports/phase3/lvs.rpt",
    "generated_by": "phase3_one_shot_runner:_run_extraction_lvs (#477)",
    "lvs_report": "reports/phase3/lvs.rpt",
}


def _put(project: Path, rel: str, doc) -> Path:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n")
    return p


def _receipt(project: Path, **over) -> None:
    doc = dict(_SPMIC2_RECEIPT)
    doc.update(over)
    _put(project, G.REPORT_REL, doc)


# ── the judge (the flow's own clause: --check) ───────────────────────────────

def test_a_self_xor_receipt_is_not_a_connectivity_pass(tmp_path):
    """RED on main: the judge returned rc 0 PASS for the spmic2 receipt."""
    _receipt(tmp_path)
    _put(tmp_path, G.LVS_VERDICT_REL, _SPMIC2_LVS_VERDICT)
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 2, line
    assert line.startswith("NOT_MEASURED [FLOW_DOES_NOT_PERFORM]"), line
    assert "retained pre-finishing stream" in line, line


def test_a_bound_connectivity_measurement_passes(tmp_path):
    _receipt(tmp_path, gds_connectivity={
        "verdict": "PASS", "basis": "lvs_extracted_from_shipped_gds",
        "subject_sha256": LIVE})
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 0, line
    assert "lvs_extracted_from_shipped_gds" in line


def test_a_connectivity_pass_about_other_bytes_is_not_measured(tmp_path):
    _receipt(tmp_path, gds_connectivity={
        "verdict": "PASS", "basis": "lvs_extracted_from_shipped_gds",
        "subject_sha256": "0" * 64})
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 2 and "not bound to the shipped GDS" in line, line


def test_an_unknown_basis_is_not_accepted(tmp_path):
    _receipt(tmp_path, gds_connectivity={
        "verdict": "PASS", "basis": "prefinish_self_xor", "subject_sha256": LIVE})
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 2, line


def test_a_measured_connectivity_fail_blocks(tmp_path):
    _receipt(tmp_path, gds_connectivity={
        "verdict": "FAIL", "basis": "magic_vs_klayout_stream_xor",
        "subject_sha256": LIVE})
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 1 and "FAILED" in line, line


def test_a_finishing_difference_still_fails_first(tmp_path):
    _receipt(tmp_path, verdict="FAIL", rc=1, design__xor_difference__count=2,
             design_layer_differences=[{"layer": 36, "datatype": 0,
                                        "differences": 2}])
    rc, line, _ = G.judge_receipt(tmp_path, G.REPORT_REL)
    assert rc == 1 and "36/0=2" in line, line


# ── the producer's measurement ───────────────────────────────────────────────

def test_a_def_extracted_lvs_does_not_measure_the_gds(tmp_path):
    _put(tmp_path, G.LVS_VERDICT_REL, _SPMIC2_LVS_VERDICT)
    conn = G.gds_connectivity(tmp_path, LIVE)
    assert conn["verdict"] == "NOT_MEASURED", conn
    assert conn["reason_class"] == "FLOW_DOES_NOT_PERFORM"
    assert "routed DEF" in conn["reason"], conn["reason"]


def _gds_lvs(status="PASS", sha=LIVE):
    return dict(_SPMIC2_LVS_VERDICT, status=status, result=status,
                layout_source={"kind": "gds", "path": "phase3/stage3/pnr/spm.gds",
                               "sha256": sha})


def test_an_lvs_extracted_from_the_shipped_gds_measures_it(tmp_path):
    _put(tmp_path, G.LVS_VERDICT_REL, _gds_lvs())
    conn = G.gds_connectivity(tmp_path, LIVE)
    assert conn["verdict"] == "PASS" and conn["basis"] == "lvs_extracted_from_shipped_gds"
    assert conn["subject_sha256"] == LIVE


def test_an_lvs_of_other_gds_bytes_does_not_count(tmp_path):
    _put(tmp_path, G.LVS_VERDICT_REL, _gds_lvs(sha="f" * 64))
    conn = G.gds_connectivity(tmp_path, LIVE)
    assert conn["verdict"] == "NOT_MEASURED", conn
    assert "different GDS" in conn["reason"]


def test_an_lvs_mismatch_on_the_shipped_gds_fails(tmp_path):
    _put(tmp_path, G.LVS_VERDICT_REL, _gds_lvs(status="FAIL"))
    assert G.gds_connectivity(tmp_path, LIVE)["verdict"] == "FAIL"


def _librelane_xor(project: Path, value, *, promoted=LIVE):
    _put(project, G.LIBRELANE_PROMOTION_REL,
         {"selection": "magic", "canonical_sha256": promoted})
    _put(project, G.LIBRELANE_STREAM_XOR_REL,
         {"verdict": "PASS" if value == 0 else "FAIL",
          "metrics": {"design__xor_difference__count": {
              "status": "MEASURED" if value == 0 else "FAIL", "value": value}}})


def test_the_magic_vs_klayout_stream_xor_measures_the_promoted_bytes(tmp_path):
    _librelane_xor(tmp_path, 0)
    conn = G.gds_connectivity(tmp_path, LIVE)
    assert conn["verdict"] == "PASS" and conn["basis"] == "magic_vs_klayout_stream_xor"


def test_a_nonzero_stream_xor_fails_even_beside_a_clean_lvs(tmp_path):
    _librelane_xor(tmp_path, 3)
    _put(tmp_path, G.LVS_VERDICT_REL, _gds_lvs())
    conn = G.gds_connectivity(tmp_path, LIVE)
    assert conn["verdict"] == "FAIL" and conn["basis"] == "magic_vs_klayout_stream_xor"


def test_a_stream_xor_of_other_promoted_bytes_does_not_count(tmp_path):
    _librelane_xor(tmp_path, 0, promoted="e" * 64)
    assert G.gds_connectivity(tmp_path, LIVE)["verdict"] == "NOT_MEASURED"


# ── end to end through the producer (LibreLane finishing branch) ─────────────

def _librelane_project(project: Path) -> None:
    gds = project / "phase3/stage4/gds/chip_top.gds"
    gds.parent.mkdir(parents=True, exist_ok=True)
    gds.write_bytes(SHIPPED)
    d = project / G.CANONICAL_DEF_REL
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("VERSION 5.8 ;\nDESIGN chip_top ;\nEND DESIGN\n")
    folder = project / "phase3/librelane/37.3-magic/state"
    _put(project, str((folder / "state_out.json").relative_to(project)),
         {"metrics": {"vibeic__finishing_xor__design_pair__count": 40}})
    _put(project, G.LIBRELANE_PROMOTION_REL,
         {"selection": "magic", "canonical_sha256": LIVE})
    _put(project, "phase3/librelane/37.3-magic-finishing-xor.json",
         {"verdict": "PASS", "metrics": {"vibeic__finishing_xor__defect__count": {
             "status": "MEASURED", "value": 0, "folder": str(folder)}}})


def test_the_producer_withholds_pass_until_connectivity_is_measured(tmp_path):
    _librelane_project(tmp_path)
    rc = G.main([str(tmp_path), "--json", str(tmp_path / G.REPORT_REL)])
    doc = json.loads((tmp_path / G.REPORT_REL).read_text())
    assert rc == 2 and doc["verdict"] == "NOT_DETERMINED", doc
    assert doc["reason_class"] == "FLOW_DOES_NOT_PERFORM"
    assert doc["gds_connectivity"]["verdict"] == "NOT_MEASURED"
    assert G.judge_receipt(tmp_path, G.REPORT_REL)[0] == 2


def test_the_producer_passes_with_the_stream_xor_bound_to_the_shipped_bytes(tmp_path):
    _librelane_project(tmp_path)
    _librelane_xor(tmp_path, 0)
    rc = G.main([str(tmp_path), "--json", str(tmp_path / G.REPORT_REL)])
    doc = json.loads((tmp_path / G.REPORT_REL).read_text())
    assert rc == 0 and doc["verdict"] == "PASS", doc
    assert doc["gds_connectivity"]["subject_sha256"] == LIVE
    assert G.judge_receipt(tmp_path, G.REPORT_REL)[0] == 0


# ── step 31's GDS-extracted LVS names the bytes it read ──────────────────────

def test_klayout_lvs_records_the_mask_gds_it_extracted():
    src = (Path(G.__file__).parent / "phase3_one_shot_runner.py").read_text()
    body = src.split("def _run_klayout_lvs(", 1)[1].split("\ndef ", 1)[0]
    assert '"layout_source": {"kind": "gds"' in body
    assert "_mask_gds, _mask_sha = gds_path, _sha256_file(gds_path)" in body
