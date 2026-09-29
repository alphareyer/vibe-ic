"""R-0929-TOOL-DEFAULT, wave 3 item 3 (audit §3.3): step 37 is the tool's
stream-out, finishing and measurement (librelane_step37) on the chip path.

* The PDK fill script runs with its declared option
  `KLAYOUT_FILLER_OPTIONS Metal2_ignore_active=true` (LibreLane's gf180
  reference run); without it gf180 M2.4 stays one window short.
* The nonzero-origin seal-ring refusal is gone: the image's KLayout.SealRing
  spans x1-x0 / y1-y0 (vibeic/librelane #13).
* After XOR zero and equal DRC the KLayout stream is kept (the Magic arm's
  supply labels are misplaced, audit §3.3 #9).
"""
import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))
contract = importlib.import_module("librelane_contract")
step37 = importlib.import_module("librelane_step37")
from test_librelane_contract_production_defaults import _chip  # noqa: E402


def _stop_after_the_config(tmp_path, monkeypatch, die):
    seen = {}
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    configs = {}
    for step in step37.STEPS:
        body = {"meta": {"step": step}}
        if step == "KLayout.SealRing":
            body["DIE_AREA"] = die
        configs[step] = cfg / f"{step}.json"
        configs[step].write_text(json.dumps(body))

    def resolve(*args, **kwargs):
        seen["overlay"] = kwargs.get("overlay") or {}
        return configs

    def stop(project):
        raise contract.Refusal("TEST_STOP", "after the configs")

    monkeypatch.setattr(step37, "resolve_step_configs", resolve)
    monkeypatch.setattr(step37, "declaration_config", stop)
    with pytest.raises(contract.Refusal) as exc:
        step37.run(tmp_path, "img", tmp_path / "pdk", "procA", tmp_path / "r.def",
                   tmp_path / "n.v", tmp_path / "c.sdc", tmp_path / "out.gds")
    return seen, exc.value


def test_the_pdk_filler_runs_with_its_declared_option(tmp_path, monkeypatch):
    seen, _ = _stop_after_the_config(tmp_path, monkeypatch, [0, 0, 100, 100])
    value, source = seen["overlay"]["KLAYOUT_FILLER_OPTIONS"]
    assert value == {"Metal2_ignore_active": True}
    assert "fill_metal.rb" in source


def test_a_nonzero_origin_die_is_no_longer_refused(tmp_path, monkeypatch):
    _, refusal = _stop_after_the_config(tmp_path, monkeypatch, [10, 10, 90, 90])
    assert refusal.code == "TEST_STOP"


def test_an_equal_drc_tie_keeps_the_klayout_stream():
    assert step37.TIE_BREAK_ENGINE == "klayout"
    src = inspect.getsource(step37.run)
    assert "winner = TIE_BREAK_ENGINE" in src and 'winner = "magic"' not in src


def test_the_chip_path_defaults_step_37_to_the_tool(tmp_path):
    assert contract.selected_mode(_chip(tmp_path), "37") == "librelane"
