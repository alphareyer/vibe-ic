"""A prior resize is spent only when its recorded floor reached the DEF."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import phase3_one_shot_runner as R
from _ppa import power as PW
from test_issue1215_pdn_em_derived_sizing import _TLEF


def _layout(project: Path, width_dbu: int) -> Path:
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    body = ("VERSION 5.8 ;\nDESIGN unit ;\nUNITS DISTANCE MICRONS 2000 ;\n"
            "SPECIALNETS 1 ;\n- supply + USE POWER\n"
            f"  + ROUTED MetalA {width_dbu} + SHAPE STRIPE "
            "( 0 0 ) ( 100000 0 ) ;\nEND SPECIALNETS\nEND DESIGN\n")
    for name in ("unit.def", PW._PDN_EM_SUBJECT_DEF):
        (pnr / name).write_text(body)
    (pnr / "pnr.tcl").write_text(
        "add_pdn_stripe -grid grid -layer MetalA -width 1.6 -pitch 20\n")
    return pnr / PW._PDN_EM_SUBJECT_DEF


def _sentinel(project: Path, subject: Path, *, floor: float = 3.95) -> Path:
    path = R._pl.pnr_dir(project) / R._PDN_EM_RESIZE_SENTINEL
    path.write_text(json.dumps({
        "reason": "pdn_em_first_pass_resize",
        "spent_on_def": hashlib.sha256(subject.read_bytes()).hexdigest(),
        "short": [{"layer": "MetalA", "drawn_um": 1.6,
                   "w_em_um": floor, "shortfall_x": round(floor / 1.6, 4)}],
    }))
    return path


def _measurement(project: Path, subject: Path) -> SimpleNamespace:
    rpt = R._pl.reports_phase3_dir(project)
    rpt.mkdir(parents=True, exist_ok=True)
    (rpt / "em.json").write_text(json.dumps({
        "max_segment_current_A": 0.002,
        "segments_analysed": 1,
        "subject_def_sha256": hashlib.sha256(subject.read_bytes()).hexdigest(),
    }))
    (rpt / "em.rpt").write_text("Total power 0.02\nSupply voltage 5.0\n")
    tech = project / "tech.tlef"
    tech.write_text(_TLEF)
    return SimpleNamespace(tech_lef=str(tech))


def test_stale_ineffective_sentinel_allows_real_resize(tmp_path, capsys):
    subject = _layout(tmp_path, 3200)  # 1.6 um, below recorded 3.95 um
    sentinel = _sentinel(tmp_path, subject)
    pdk = _measurement(tmp_path, subject)
    binds, why = PW._pdn_em_sentinel_binds(sentinel, tmp_path)
    assert binds is False, why
    decision = R._pdn_em_first_pass_resize(tmp_path, "unit", pdk, "unused")
    assert decision is not None
    assert decision["short"][0]["layer"] == "MetalA"
    assert "previous resize did not take effect" in capsys.readouterr().err


def test_effective_sentinel_blocks(tmp_path):
    subject = _layout(tmp_path, 8000)  # 4.0 um, above recorded floor
    sentinel = _sentinel(tmp_path, subject)
    binds, why = PW._pdn_em_sentinel_binds(sentinel, tmp_path)
    assert binds, why


def test_every_recorded_strap_must_meet_its_floor(tmp_path):
    subject = _layout(tmp_path, 8000)
    sentinel = _sentinel(tmp_path, subject)
    doc = json.loads(sentinel.read_text())
    doc["short"].append({"layer": "MetalB", "drawn_um": 1.6,
                         "w_em_um": 1.77, "shortfall_x": 1.1062})
    sentinel.write_text(json.dumps(doc))
    binds, why = PW._pdn_em_sentinel_binds(sentinel, tmp_path)
    assert binds is False, why  # MetalA took effect; MetalB never appeared


def test_same_run_still_narrow_second_pass_cannot_buy_third(tmp_path):
    subject = _layout(tmp_path, 3200)
    pdk = _measurement(tmp_path, subject)
    decision = R._pdn_em_first_pass_resize(tmp_path, "unit", pdk, "unused")
    assert decision is not None
    assert R._record_pdn_em_resize_spend(tmp_path, decision)
    sentinel = decision["sentinel"]
    assert json.loads(sentinel.read_text())["run_id"] == R._PDN_EM_RUN_ID
    subject = _layout(tmp_path, 4000)  # second pass changed DEF, still < 3.95
    pdk = _measurement(tmp_path, subject)
    assert R._pdn_em_first_pass_resize(tmp_path, "unit", pdk, "unused") is None
