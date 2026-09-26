"""The fixture admission is bound to the layout it admitted — never a bypass.

`_prestream_admission_fixture.admit_fixture_layout` lets the stream-out suites
reach their subject behind v1.24.73's pre-stream boundary (#2635). It must not
become a way around that boundary: the runner's own `_ga.gate_passed`
comparison still has to refuse a layout the record does not describe, and a
record that is not a PASS.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))
import phase3_one_shot_runner as R  # noqa: E402
from _prestream_admission_fixture import admit_fixture_layout  # noqa: E402

_REFUSED = "pre-stream gate refused this layout"
_MAP_REASON = "streamout layer map missing"


def _setup(tmp_path, monkeypatch):
    pnr = tmp_path / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / "top.def").write_text("DESIGN top ;\nEND DESIGN\n")
    monkeypatch.setattr(R._pl, "pnr_dir", lambda _p: pnr)
    monkeypatch.setattr(R, "_magic_def_to_gds",
                        lambda *a, **k: (False, "no magic"))
    monkeypatch.setattr(R, "_docker_exec", lambda *a, **k: (1, "", "no tool"))
    pdk = R.PdkConfig(name="t", liberty="l", tech_lef="t.lef",
                      cell_lef="c.lef", cell_gds="c.gds", site="s",
                      drc_deck="/pdk/x.lydrc", metal_prefix="met",
                      calibre_drc=None, lefdef_layermap=None)
    return pnr, pdk


def test_an_admitted_fixture_reaches_the_step_behind_the_boundary(
        tmp_path, monkeypatch):
    _pnr, pdk = _setup(tmp_path, monkeypatch)
    admit_fixture_layout(monkeypatch, R, tmp_path, "top", pdk, "container")
    res = R.step_gds(tmp_path, "top", pdk, "container")
    assert _REFUSED not in res.detail, res.detail
    assert (res.status, _MAP_REASON in res.detail) == ("FAIL", True), res.detail


def test_a_routed_layout_changed_after_admission_is_refused(
        tmp_path, monkeypatch):
    pnr, pdk = _setup(tmp_path, monkeypatch)
    admit_fixture_layout(monkeypatch, R, tmp_path, "top", pdk, "container")
    with (pnr / "routed.def").open("a") as fh:
        fh.write("# edited after the gate passed\n")
    res = R.step_gds(tmp_path, "top", pdk, "container")
    assert res.status == "NOT_MEASURED", (res.status, res.detail)
    assert _REFUSED in res.detail, res.detail
    assert _MAP_REASON not in res.detail


def test_a_record_that_is_not_a_pass_is_refused(tmp_path, monkeypatch):
    _pnr, pdk = _setup(tmp_path, monkeypatch)
    digest = admit_fixture_layout(monkeypatch, R, tmp_path, "top", pdk,
                                  "container")
    gate = R._pl.reports_phase3_dir(tmp_path) / "prestream_gate.json"
    gate.write_text(json.dumps({"verdict": "FAIL",
                                "layout_digest": digest}) + "\n")
    res = R.step_gds(tmp_path, "top", pdk, "container")
    assert res.status == "NOT_MEASURED", (res.status, res.detail)
    assert _REFUSED in res.detail, res.detail
