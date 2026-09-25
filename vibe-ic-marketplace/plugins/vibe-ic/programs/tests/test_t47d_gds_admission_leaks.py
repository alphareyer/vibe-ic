"""The live SDR and handoff paths may write masks only after admission."""
import json
import time
from pathlib import Path

import foundry_handoff_pack_gen as H
import phase3_one_shot_runner as R
from _hostpaths import require_repo
from test_phase3_postpnr_disclosure_and_gds_guard import (
    _pdk, _project, _drive, _plan, OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, TOP,
)


def test_sdr_candidate_stream_is_private_and_digest_bound(tmp_path, monkeypatch):
    project = tmp_path / "run"
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    (pnr / "routed.def").write_text("candidate route\n")
    (project / "reports").mkdir()
    pdk = _pdk(project)
    pdk.drc_deck = "fake.deck"
    monkeypatch.setattr(R, "_layout_basis", lambda *a: ("a" * 64, ""))

    def fake_stream(proj, top, *_args, **_kwargs):
        time.sleep(0.01)
        out = R._pl.pnr_dir(proj)
        for name in (f"{top}.gds", f"{top}.filled.gds",
                     f"{top}.ring_reference.gds", "chip_top.prefinish.gds"):
            (out / name).write_bytes(b"fake EDA stream\n")
        return R.StepResult("gds", "PASS", 0.0, "fake EDA wrote stream")

    def fake_drc(proj, *_args):
        report = proj / "phase3/reports/drc.rpt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("<report-database><items/></report-database>\n")
        return R.StepResult("drc", "PASS", 0.0, "deck zero")

    def fake_lvs(proj, *_args):
        report = proj / "reports/phase3/lvs_verdict.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('{"status":"PASS"}\n')
        return R.StepResult("lvs", "PASS", 0.0, "match")

    monkeypatch.setattr(R, "step_gds", fake_stream)
    monkeypatch.setattr(R, "step_drc", fake_drc)
    monkeypatch.setattr(R, "step_lvs", fake_lvs)
    evidence = pnr / "sdr_transaction"
    admitted, reason = R._sdr_candidate_signoff_clean(
        project, "unit", pdk, "fake-container", evidence)
    assert admitted, reason
    assert len(list(pnr.glob("*.gds"))) == 0
    assert len(list((project / "phase3/scratch/sdr_candidates").rglob("*.gds"))) == 0
    record = json.loads((evidence / "candidate_gds_admission.json").read_text())
    assert record["layout_digest"] == "a" * 64
    assert record["kind"] == "PRIVATE_SDR_CANDIDATE"


def test_failed_gate_refuses_handoff_even_with_old_sources(tmp_path):
    project = tmp_path / "run"
    source = project / "phase3/stage3/pnr/unit.filled.gds"
    source.parent.mkdir(parents=True)
    source.write_bytes(require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
        "tests", "fixtures", "density_fill", "filled.gds").read_bytes())
    gate = project / "reports/phase3/prestream_gate.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({"verdict": "FAIL", "layout_digest": "b" * 64,
                                "failed_gates": ["antenna"]}))
    old_kit = project / "phase3/stage4/foundry_handoff/mask_spec.json"
    old_kit.parent.mkdir(parents=True, exist_ok=True)
    old_kit.write_text('{"previous_run": true}\n')
    assert H.main([str(project)]) == 2
    assert not source.exists()
    assert not list((project / "phase3/stage4/foundry_handoff").glob("*.gds"))
    assert list(old_kit.parent.iterdir()) == []


def test_prior_shipped_gds_is_quarantined_with_record(tmp_path, monkeypatch):
    project = _project(tmp_path / "run", cached_die=OLD_DIE, cached_util=OLD_UTIL)
    _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    monkeypatch.setattr(R, "step_prestream_gate", lambda *a, **k:
                        R.StepResult("prestream_gate", "FAIL", 0.0,
                                     "antenna refused", extras={
                                         "layout_digest": "b" * 64,
                                         "failed_gates": ["antenna"]}))
    locations = (f"phase3/stage3/pnr/{TOP}.gds",
                 f"phase3/stage4/gds/{TOP}.gds",
                 f"phase3/stage4/foundry_handoff/{TOP}.filled.gds",
                 f"phase3/stage4/foundry_handoff/gds/{TOP}.gds",
                 f"gds/{TOP}.gds")
    for rel in locations:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"prior run GDS\n")
    nested_alias = project / locations[3]
    nested_alias.unlink()
    nested_alias.symlink_to(project / locations[0])
    old_kit = project / "phase3/stage4/foundry_handoff/mask_spec.json"
    old_kit.write_text('{"previous_run": true}\n')
    gate = project / "reports/phase3/prestream_gate.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({"verdict": "FAIL", "layout_digest": "b" * 64}))
    R.main()
    assert sum((project / rel).exists() or (project / rel).is_symlink()
               for rel in locations) == 0
    assert list(old_kit.parent.iterdir()) == []
    assert _plan(project)["foundry_handoff"]["status"] == "NOT_MEASURED"
    records = list((project / "phase3/scratch/gds_quarantine").glob("*/record.json"))
    assert len(records) == 1
    doc = json.loads(records[0].read_text())
    assert "new phase-3 run" in doc["reason"]
    assert len(doc["files"]) == len(locations)
