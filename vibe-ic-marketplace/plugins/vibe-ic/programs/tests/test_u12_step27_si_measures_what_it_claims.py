"""U12 — Step 27 read PASS on an SI run that timed nothing.

Measured on the spm DIE tail (cx_spmic2_run): `si_mcf_sta.json` resolved
switching windows for 0 of 582 coupling nets and every corner slack was null
(the pad cells had no Liberty linked, so OpenSTA timed nothing through the
die top), the noise screen used Vdd = 1.8 V on a 5 V library, and still
`si_mcf_sta_check` said PASS and `si_crosstalk_check` returned rc 0 on an
ADVISORY_SCREEN_ONLY artefact. Step 27 read PASS.

Now:
  * si_mcf_sta links the IO Liberty at the SAME PVT as its corner liberty
    (from the pad-ring record) and takes Vdd from that liberty's own
    voltage_map; the runner's noise screen takes Vdd from the PDK liberty;
  * si_mcf_sta_check refuses a window run that resolved zero windows
    (WINDOWS_NOT_MEASURED) and a null corner slack (SLACK_NOT_MEASURED);
  * si_crosstalk_check never passes an advisory screen: NOT_MEASURED, rc 2.
Controls: a real fold with measured windows and slacks still PASSes; a
declared timing-window SI sign-off still PASSes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow_compliance_check as FCC  # noqa: E402
import phase3_one_shot_runner as runner  # noqa: E402
import si_crosstalk_check as SIC  # noqa: E402
import si_mcf_sta as M  # noqa: E402
import si_mcf_sta_check as G  # noqa: E402
from test_si_mcf_not_run_is_not_a_design_failure import (  # noqa: E402
    _project as _mcf_project)
from test_phase3_signoff_chain_organic import (  # noqa: E402
    _SPEF_SAMPLE, _fake_pdk, _mk_project)

_FLOW = _PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"


def _gate(proj: Path) -> dict:
    findings, stats = G.audit(proj)
    return G.build_report(findings, stats, str(proj))


def _cats(rep: dict) -> set:
    return {f["category"] for f in rep["findings"]}


# ── si_mcf_sta_check: a report that times nothing is not a pass ────────────
def _zero_window_null_slack(root: Path) -> Path:
    """The cx_spmic2_run shape: a window file that resolved nothing, and
    null slacks on both corners."""
    proj = _mcf_project(root, setup_after=None, hold_after=None)
    wj = proj / "windows.json"
    wj.write_text(json.dumps({"pins": {}}))
    rp = proj / "reports" / "phase3" / "si_mcf_sta.json"
    doc = json.loads(rp.read_text())
    doc["windows_json"] = str(wj)
    for c in doc["corners"].values():
        c["worst_slack_before_ns"] = None
    doc["nominal"] = {"worst_setup_slack_ns": None, "worst_hold_slack_ns": None}
    rp.write_text(json.dumps(doc))
    return proj


def test_zero_windows_and_null_slacks_do_not_pass(tmp_path):
    rep = _gate(_zero_window_null_slack(tmp_path / "p"))
    assert rep["verdict"] != "PASS"
    assert {"WINDOWS_NOT_MEASURED", "SLACK_NOT_MEASURED"} <= _cats(rep), rep
    assert rep["summary"]["pass"] is False


def test_control_a_measured_fold_still_passes(tmp_path):
    rep = _gate(_mcf_project(tmp_path / "p"))
    assert rep["verdict"] == "PASS", rep["findings"]


# ── si_crosstalk_check: ADVISORY_SCREEN_ONLY is never PASS ─────────────────
_SCREEN = {  # the emitter's advisory screen, as the spm tail wrote it
    "tool": "spef-coupling-cap-si-screen",
    "mode": "signal_integrity_crosstalk",
    "method": "per-net coupling ratio Cc/(Cc+Cg); worst-case capacitive-"
              "divider UPPER bound — advisory",
    "vdd_mv": 1800.0, "max_crosstalk_noise": 1759.26,
    "max_coupling_ratio": 0.9774, "mean_coupling_ratio": 0.3381,
    "nets_analyzed": 874, "nets_elevated_coupling_gt0p5": 310,
    "nets_coupling_dominated_gt0p9": 7, "violations_count": 0,
    "verdict": "ADVISORY_SCREEN_ONLY",
}


def _si_proj(root: Path, body: dict) -> Path:
    p = root / "reports" / "phase3" / "si_crosstalk.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body))
    return root


def test_advisory_screen_is_not_measured_rc2(tmp_path):
    proj = _si_proj(tmp_path, _SCREEN)
    assert SIC.main([str(proj), "--json", str(tmp_path / "o.json")]) == 2
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["verdict"] == "NOT_MEASURED"
    assert rep["screen_tier"] == "ADVISORY_SCREEN_ONLY"


def test_step27_does_not_read_pass_on_an_advisory_screen(tmp_path):
    proj = _si_proj(tmp_path, _SCREEN)
    step = next(s for s in yaml.safe_load(_FLOW.read_text())["steps"]
                if str(s.get("id")) == "27")
    subject = {**step, "required_outputs": ["reports/phase3/si_crosstalk.json"],
               "gate": {"all_of": step["gate"]["all_of"][:1]}}
    row = FCC.check_step(proj, subject, {})
    assert row.status == "NOT_MEASURED", (row.status, row.reasons)


def test_control_a_declared_timing_window_signoff_passes(tmp_path):
    proj = _si_proj(tmp_path, {"max_crosstalk_noise": 0.02,
                               "violations_count": 0,
                               "timing_window_signoff": True})
    assert SIC.main([str(proj)]) == 0


# ── the producer: IO views at the same PVT, Vdd from the PDK liberty ────────
def _liberty(path: Path, volts: float) -> Path:
    path.write_text(f"library (x) {{\n  voltage_map (VDD, {volts});\n"
                    f"  voltage_map (VSS, 0.0);\n  nom_voltage : {volts};\n}}\n")
    return path


def test_si_mcf_run_links_the_same_pvt_io_view_and_the_liberty_supply(
        tmp_path, monkeypatch):
    proj = _mcf_project(tmp_path / "p")
    lib = _liberty(tmp_path / "stdlib__ss_125C_4v50.lib", 4.5)
    views = [f"/pdk/io/lib/iolib__{pvt}.lib"
             for pvt in ("ss_125C_4v50", "ss_125C_2v25", "tt_025C_5v00")]
    rec = proj / M.IO_RECORD_REL
    rec.write_text(json.dumps({"io_library_liberty": views}))
    seen = {}

    def fake_windows(container, work, liberty_c, netlist_c, top, sdc_c, spef_c,
                     macro_libs_c, vdd_v, out, timeout=0):
        seen["libs"] = list(macro_libs_c)
        seen["vdd"] = vdd_v
        return {"pins": {}}, 0

    monkeypatch.setattr(M, "_run_windows", fake_windows)
    monkeypatch.setattr(M, "_run_sta_slack",
                        lambda *a, **k: (1.0, 0.2, "", 0))
    monkeypatch.setattr(M, "_to_container_path", lambda p, c: str(p))
    rep = M.run(proj, container="none", spef=str(proj / "design.spef"),
                liberty=str(lib), top="top",
                out_json=str(tmp_path / "si.json"),
                work_dir=str(tmp_path / "work"))
    assert rep["io_liberties"] == ["/pdk/io/lib/iolib__ss_125C_4v50.lib"]
    assert seen["libs"] == ["/pdk/io/lib/iolib__ss_125C_4v50.lib"]
    assert rep["vdd_v"] == 4.5 and seen["vdd"] == 4.5
    assert "liberty voltage_map" in rep["vdd_source"]


def test_runner_noise_screen_takes_vdd_from_the_pdk_liberty(tmp_path):
    project = _mk_project(tmp_path)
    rpt3 = runner._pl.reports_phase3_dir(project)
    rpt3.mkdir(parents=True, exist_ok=True)
    spef = tmp_path / "chip_top.spef"
    spef.write_text(_SPEF_SAMPLE)
    pdk = _fake_pdk()
    pdk.liberty = str(_liberty(tmp_path / "corner.lib", 5.0))
    notes = []
    assert runner._emit_si_crosstalk_report(
        project, "chip_top", spef, rpt3 / "ir_drop.rpt",
        rpt3 / "si_crosstalk.rpt", notes, pdk=pdk, container="none")
    j = json.loads((rpt3 / "si_crosstalk.json").read_text())
    assert j["vdd_mv"] == 5000.0, j
    assert "Vdd=5.0V" in (rpt3 / "si_crosstalk.rpt").read_text()


# ── Round 2 (review wave 58, R-0929-SI-VERDICT) ────────────────────────────
_R25_DELTA = {  # subservient r25's genuine delta-delay reading
    "verdict": "PASS", "violations_count": 0, "max_delta_delay_ns": 0.04,
    "pairs_slack_checked": 35902, "pairs_decoupled_by_window": 1192,
    "scope": "coupling delta-delay vs the victim's own STA path slack"}


def _runner_si_report(tmp_path, *, volts=5.0, delta=None):
    """si_crosstalk.json exactly as the runner emits it (advisory screen),
    optionally carrying the delta-delay block the timing-aware merge adds."""
    project = _mk_project(tmp_path)
    rpt3 = runner._pl.reports_phase3_dir(project)
    rpt3.mkdir(parents=True, exist_ok=True)
    spef = tmp_path / "chip_top.spef"
    spef.write_text(_SPEF_SAMPLE)
    pdk = _fake_pdk()
    pdk.liberty = str(_liberty(tmp_path / "corner.lib", volts)) \
        if volts is not None else str(tmp_path / "absent.lib")
    assert runner._emit_si_crosstalk_report(
        project, "chip_top", spef, rpt3 / "ir_drop.rpt",
        rpt3 / "si_crosstalk.rpt", [], pdk=pdk, container="none")
    if delta is not None:
        p = rpt3 / "si_crosstalk.json"
        j = json.loads(p.read_text())
        j["delta_delay"] = dict(delta)
        j["delta_delay_verdict"] = delta["verdict"]
        p.write_text(json.dumps(j))
    return project


def _prestream_si(project):
    spec = next(s for s in runner._PRESTREAM_GATES if s[0] == "si")
    row = runner._prestream_si_disclosure(
        runner._run_declared_signoff_gate(project, *spec))
    verdict, failed, unmeasured, _w = runner._prestream_status([row])
    return row, verdict


def test_prestream_a_clean_design_with_an_advisory_screen_still_streams(tmp_path):
    """BLOCKER (wave 58): the REAL checker on the runner-emitted report; the
    row is NOT_MEASURED and disclosed, and it does not quarantine the GDS."""
    project = _runner_si_report(tmp_path)
    row, verdict = _prestream_si(project)
    assert row.status == "NOT_MEASURED", row.detail
    assert row.extras.get("advisory_screen_only") is True
    assert verdict == "PASS"


def test_prestream_follows_a_genuine_delta_delay_verdict(tmp_path):
    row, verdict = _prestream_si(_runner_si_report(tmp_path, delta=_R25_DELTA))
    assert (row.status, verdict) == ("PASS", "PASS"), row.detail
    fail = dict(_R25_DELTA, verdict="FAIL", violations_count=3)
    row, verdict = _prestream_si(_runner_si_report(tmp_path / "f", delta=fail))
    assert (row.status, verdict) == ("FAIL", "FAIL"), row.detail


def test_a_genuine_delta_delay_pass_is_the_step27_verdict(tmp_path):
    project = _runner_si_report(tmp_path, delta=_R25_DELTA)
    assert SIC.main([str(project)]) == 0
    rep = SIC.build_report(*SIC.audit(project), str(project))
    assert rep["verdict_basis"] == "coupling_delta_delay_screen"


def test_a_kernel_disagreement_or_no_slack_basis_is_not_a_verdict(tmp_path):
    body = dict(_SCREEN, delta_delay=dict(_R25_DELTA),
                kernel_cross_check={"verdict": "DISAGREE"})
    assert SIC.main([str(_si_proj(tmp_path / "k", body))]) == 2
    body = dict(_SCREEN, delta_delay=dict(_R25_DELTA, verdict="ADVISORY"))
    assert SIC.main([str(_si_proj(tmp_path / "a", body))]) == 2
    body = dict(_SCREEN, delta_delay=dict(_R25_DELTA, pairs_slack_checked=0))
    assert SIC.main([str(_si_proj(tmp_path / "z", body))]) == 2


def test_a_subservient_r25_shaped_artefact_reads_step27_pass(tmp_path):
    """delta-delay PASS over 35902 slack-checked pairs; the MCF envelope
    self-reports -0.266 ns and is disclosed beside it (R-0915-66)."""
    proj = _mcf_project(tmp_path / "p", setup_after=-0.266)
    _si_proj(proj, dict(_SCREEN, delta_delay=dict(_R25_DELTA),
                        delta_delay_verdict="PASS"))
    import si_mcf_verdict_basis as VB
    VB.apply(proj)
    step = next(s for s in yaml.safe_load(_FLOW.read_text())["steps"]
                if str(s.get("id")) == "27")
    subject = {**step, "required_outputs": [
        "reports/phase3/si_crosstalk.json", "reports/phase3/si_mcf_sta.json"],
        "gate": {"all_of": [step["gate"]["all_of"][0], step["gate"]["all_of"][-1]]}}
    row = FCC.check_step(proj, subject, {})
    assert row.status == "PASS", (row.status, row.reasons)
    mcf = json.loads((proj / "reports/phase3/si_mcf_sta.json").read_text())
    assert mcf["verdict_basis"]["envelope"]["mcf_setup_ns"] == -0.266


def test_unresolved_vdd_runs_the_merge_and_nulls_only_noise_numbers(
        tmp_path, monkeypatch):
    """No 1.8 V default: a liberty that declares no supply keeps the
    timing-aware (delta-delay) merge running; only noise numbers are null."""
    seen = {}

    def fake_merge(project, top, pdk, container, spef, sbody, notes, vdd_v=0):
        seen["vdd"] = vdd_v
        sbody["delta_delay"] = dict(_R25_DELTA)

    monkeypatch.setattr(runner, "_merge_si_timing_aware", fake_merge)
    project = _runner_si_report(tmp_path, volts=None)
    j = json.loads((runner._pl.reports_phase3_dir(project)
                    / "si_crosstalk.json").read_text())
    assert "vdd" in seen and seen["vdd"] is None
    assert j["vdd_mv"] is None and j["max_crosstalk_noise"] is None
    assert j["vdd_source"] == "NOT_RESOLVED"
    assert j["delta_delay"]["verdict"] == "PASS"
    rpt = (runner._pl.reports_phase3_dir(project) / "si_crosstalk.rpt").read_text()
    assert "0.0 mV" not in rpt and "max_crosstalk_noise: null" in rpt


def test_the_scorer_nulls_noise_numbers_without_a_supply():
    import si_signoff_timing_aware as STA
    v = STA.score_si_timing_aware(STA.parse_spef(_SPEF_SAMPLE), {"pins": {}},
                                  vdd_v=None)
    assert v["max_base_noise_mv"] is None and v["watchlist_high_count"] is None
    assert v["vdd_unresolved"] is True
    assert isinstance(v["pairs_decoupled_by_window"], int)


def test_si_mcf_run_states_an_unresolved_supply(tmp_path, monkeypatch):
    proj = _mcf_project(tmp_path / "p")
    lib = tmp_path / "stdlib__ss_125C_4v50.lib"
    lib.write_text("library (x) {\n}\n")
    monkeypatch.setattr(M, "_run_windows", lambda *a, **k: ({"pins": {}}, 0))
    monkeypatch.setattr(M, "_run_sta_slack", lambda *a, **k: (1.0, 0.2, "", 0))
    monkeypatch.setattr(M, "_to_container_path", lambda p, c: str(p))
    rep = M.run(proj, container="none", spef=str(proj / "design.spef"),
                liberty=str(lib), top="top", out_json=str(tmp_path / "si.json"),
                work_dir=str(tmp_path / "work"))
    assert rep["vdd_v"] is None
    assert rep["vdd_source"].startswith("NOT_RESOLVED")


def test_timed_nothing_is_not_run_never_a_design_fail(tmp_path):
    rep = _gate(_zero_window_null_slack(tmp_path / "p"))
    assert rep["verdict"] == "NOT_RUN", rep["verdict"]


def test_control_a_healthy_run_with_a_window_file_passes(tmp_path):
    """Measured windows (overlapping, so the emitter's fold stands) and
    measured slacks: the window check must not refuse a real run."""
    proj = _mcf_project(tmp_path / "p")
    rec = {"arr_rise_min": 0.1, "arr_rise_max": 0.2, "arr_fall_min": 0.1,
           "arr_fall_max": 0.2, "slew_rise_max": 0.05, "slew_fall_max": 0.05}
    wj = proj / "windows.json"
    wj.write_text(json.dumps({"pins": {p: rec for p in
                                       ("ua:Z", "ub:Z", "ua/Z", "ub/Z")}}))
    rp = proj / "reports" / "phase3" / "si_mcf_sta.json"
    doc = json.loads(rp.read_text())
    doc["windows_json"] = str(wj)
    rp.write_text(json.dumps(doc))
    rep = _gate(proj)
    assert rep["summary"]["windows_resolved"] >= 1, rep["summary"]
    assert rep["verdict"] == "PASS", rep["findings"]
