#!/usr/bin/env python3
"""F33: a layer is "owned" by the flow's filler only when that filler's fill IS
in the GDS being filled.

THE DEFECT (lane mig104, v1.25.51, reproduced here on the same tree)
--------------------------------------------------------------------
`_density_metal_fill` writes `metal_fill_density_cfg.json` BEFORE its engine
runs; `_die_density_fill` read the owned layers out of that config whatever the
engine then did; and `die_density_fill_gen` contested an owned layer when it
"carries geometry" -- which routing always gives a metal layer. So an engine
that FAILED, whose fill was never promoted, still made the die-wide pass leave
the PDK's metal pass out: the metals shipped with fill from NEITHER filler and
the step said PASS. Measured once by lane mig104 with a wrong cell name: the
metals stayed unfilled and the deck reported M2.4.

THE RULE NOW
------------
The owning engine's own record decides, never its configuration:
  * its in-place rewrite of THIS GDS ends at the GDS's digest now -> the layers
    are owned and the PDK pass that writes them is left out (as before);
  * it did not promote (a FAIL verdict, or no report at all) -> OWNER_FILL_ABSENT:
    the PDK generator's own pass fills them (the declared fallback), and the
    report and the PASS reason both say which engine filled them and why;
  * it says it promoted but no rewrite ends at this GDS -> OWNER_FILL_UNPROVEN:
    refused by name with the engine's own words, the GDS unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
for _p in (_PROGRAMS, _PROGRAMS / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import die_density_fill_gen as DDF                            # noqa: E402
import test_die_density_fill_gen as T                         # noqa: E402

_METALS = [34, 36, 42, 46, 81]
_REPORT = "reports/phase3/cmp_fill_emit.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _engine_report(tmp_path, rec):
    rep = tmp_path / _REPORT
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps(rec))
    return str(rep)


def _run(tmp_path, monkeypatch, owner_report, **kw):
    """The die-wide pass over a ROUTED die: every metal carries drawn
    geometry (datatype 0) and no dummy from anyone."""
    gds = T._project(tmp_path)
    if callable(owner_report):
        owner_report = owner_report(gds)
    before = T._measurement(T.DIE, T.DIE, {34: 1000.0})
    after = T._measurement(T.DIE, T.DIE,
                           {22: 1200.0, 30: 1400.0, 34: 900000.0})
    routed = {"%d/0" % layer: 500 for layer in _METALS}
    runner = T._FakeRunner([before, after], censuses=[routed],
                           pass_layers=T._PASS_LAYERS, siblings=T._SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60,
                  owned_layers=list(_METALS), owner_report=owner_report, **kw)
    return res["fill"], runner, gds


def test_a_failed_engine_does_not_own_the_metals_the_pdk_pass_fills_them(
        tmp_path, monkeypatch):
    rep = _engine_report(tmp_path, {
        "verdict": "FAIL", "reason": "density fill produced no report"})
    fill, runner, gds = _run(tmp_path, monkeypatch, rep)
    assert fill["state"] == "PASS", fill
    # the whole generator ran, metal pass included, and that is what shipped
    assert runner.fill_calls == [[]], runner.fill_calls
    assert fill["skipped_passes"] == [] and fill["contested_layers"] == []
    assert json.loads(gds.read_text()) == []
    # and it is SAID, with the engine's own cause, never silent
    assert fill["code"] == DDF.OWNER_FILL_ABSENT
    assert fill["owner_fill"]["state"] == "ABSENT"
    assert fill["owner_fill"]["fill_engine_for_owned_layers"] == "pdk_generator"
    assert DDF.OWNER_FILL_ABSENT in fill["reason"]
    assert "density fill produced no report" in fill["reason"]
    # the claim is still published: gds_xor_check reads it as the write set
    assert fill["owned_layers"] == _METALS


def test_an_engine_that_left_no_report_does_not_own_the_metals(
        tmp_path, monkeypatch):
    fill, runner, _gds = _run(tmp_path, monkeypatch, str(tmp_path / _REPORT))
    assert fill["state"] == "PASS", fill
    assert runner.fill_calls == [[]]
    assert fill["code"] == DDF.OWNER_FILL_ABSENT
    assert "left no report" in fill["owner_fill"]["cause"]


def test_an_engine_whose_fill_is_in_this_gds_owns_the_metals(
        tmp_path, monkeypatch):
    """The working engine: its rewrite ends at this GDS's digest, so the PDK
    metal pass is discovered and left out, exactly as before F33."""
    def promoted(gds):
        # the engine's dummy is now in the input the pass reads
        return _engine_report(tmp_path, {
            "verdict": "PASS", "gds_out": str(gds),
            "in_place_rewrites": [{"path": "phase3/stage3/pnr/spm.gds",
                                   "sha_before": "0" * 64,
                                   "sha_after": _sha(gds)}]})
    gds = T._project(tmp_path)
    before = T._measurement(T.DIE, T.DIE, {34: 1000.0})
    after = T._measurement(T.DIE, T.DIE, {22: 1200.0, 30: 1400.0, 34: 1000.0})
    cen_in = {"%d/4" % layer: 1 for layer in _METALS}
    runner = T._FakeRunner([before, after], censuses=[cen_in],
                           pass_layers=T._PASS_LAYERS, siblings=T._SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    fill = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                   1936, 2531, "spm", 8, False, None, True, None, 60,
                   owned_layers=list(_METALS),
                   owner_report=promoted(gds))["fill"]
    assert fill["state"] == "PASS", fill
    assert fill["owner_fill"]["state"] == "IN_GDS"
    assert fill["owner_fill"]["fill_engine_for_owned_layers"] == "flow_engine"
    assert fill["skipped_passes"] == ["fill_metal.rb"]
    assert "code" not in fill


def test_a_promotion_that_does_not_end_at_this_gds_is_refused_by_name(
        tmp_path, monkeypatch):
    rep = _engine_report(tmp_path, {
        "verdict": "PASS", "reason": "reached", "gds_out": "somewhere.gds",
        "in_place_rewrites": [{"path": "phase3/stage3/pnr/spm.gds",
                               "sha_before": None, "sha_after": "f" * 64}]})
    fill, runner, gds = _run(tmp_path, monkeypatch, rep)
    assert fill["state"] == "FAIL", fill
    assert fill["code"] == DDF.OWNER_FILL_UNPROVEN
    assert fill["reason"].startswith(DDF.OWNER_FILL_UNPROVEN)
    assert "PASS: reached" in fill["reason"]           # the engine's own words
    assert runner.fill_calls == []                      # nothing was filled
    assert gds.read_bytes() == b"unfilled"              # GDS unchanged


def test_the_cli_carries_the_owner_report_and_exits_on_the_refusal(
        tmp_path, monkeypatch, capsys):
    rep = _engine_report(tmp_path, {"verdict": "PASS", "gds_out": "x.gds"})
    gds = T._project(tmp_path)
    runner = T._FakeRunner([T._measurement(T.DIE, T.DIE, {34: 1.0})],
                           censuses=[{}])
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    argv = [str(tmp_path), "--gds", str(gds), "--script", "/pdk/fill_all.rb",
            "--in-place", "--die-width", "1936", "--die-height", "2531",
            "--owner-report", rep]
    for layer in _METALS:
        argv += ["--owned-layer", str(layer)]
    assert DDF.main(argv) == DDF.FAIL
    assert DDF.OWNER_FILL_UNPROVEN in capsys.readouterr().out


# ── the runner: the evidence it hands over, and that it is this run's ────────

import phase3_one_shot_runner as R                            # noqa: E402


def test_the_runner_hands_the_engine_report_over_with_the_owned_layers(
        tmp_path, monkeypatch):
    gds = T._project(tmp_path)
    (tmp_path / "phase3/stage3/pnr/metal_fill_density_cfg.json").write_text(
        json.dumps({"layers": [{"layer": [l, 0]} for l in _METALS]}))
    seen = {}
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: "/pdk_root/somepdk")
    monkeypatch.setattr(R, "declared_die_rect",
                        lambda p: ([0, 0, 1936, 2531], "slot"))

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(R._pr, "run_best_effort", fake_run)
    R._die_density_fill(tmp_path, "spm", None, gds, None)
    argv = seen["argv"]
    assert argv.count("--owned-layer") == len(_METALS)
    assert argv[argv.index("--owner-report") + 1] == str(tmp_path / _REPORT)


def test_the_runner_removes_an_earlier_runs_engine_report_before_filling(
        tmp_path, monkeypatch):
    """A report the engine wrote on an EARLIER run must not speak for this
    one: its skip paths write none, and the die-wide pass reads it."""
    gds = T._project(tmp_path)
    rep = Path(_engine_report(tmp_path, {"verdict": "PASS", "gds_out": "old"}))
    seen = {}

    def fake_run(argv, **kw):
        seen["report_present_when_engine_ran"] = rep.exists()
        return types.SimpleNamespace(returncode=2, stdout="VACUOUS_PASS: x",
                                     stderr="")
    monkeypatch.setattr(R._pr, "run_best_effort", fake_run)
    pdk = types.SimpleNamespace(
        metal_fill_density={"layers": [{"layer": [34, 0]}]})
    ok, _note = R._density_metal_fill(tmp_path, "spm", pdk, gds, None)
    assert ok is False
    assert seen["report_present_when_engine_ran"] is False
    assert not rep.exists()
