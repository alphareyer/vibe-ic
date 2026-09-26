"""`selected_mode` and the contract's PRODUCTION_DEFAULTS (q7 PART 0, T109).

A lane that CUT OVER sets its step's production default here; a project
opts out by naming the step `direct` in its switch file. Before this table,
"no switch" could only ever mean `direct`, so no cut-over could land.
"""
from __future__ import annotations

import json

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import librelane_contract as LC


def _switch(tmp_path, steps):
    p = tmp_path / "phase3" / "librelane_switch.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"steps": steps}))


def test_no_switch_follows_the_production_default(tmp_path, monkeypatch):
    monkeypatch.setitem(LC.PRODUCTION_DEFAULTS, "S1", "librelane")
    assert LC.selected_mode(tmp_path, "S1") == "librelane"
    assert LC.selected_mode(tmp_path, "S2") == "direct"
    _switch(tmp_path, {"S2": "dual"})          # file present, key absent
    assert LC.selected_mode(tmp_path, "S1") == "librelane"


def test_an_explicit_direct_is_the_projects_opt_out(tmp_path, monkeypatch):
    monkeypatch.setitem(LC.PRODUCTION_DEFAULTS, "S1", "librelane")
    _switch(tmp_path, {"S1": "direct"})
    assert LC.selected_mode(tmp_path, "S1") == "direct"


def test_an_invalid_mode_is_refused_from_either_source(tmp_path, monkeypatch):
    _switch(tmp_path, {"S1": "maybe"})
    with pytest.raises(LC.Refusal) as exc:
        LC.selected_mode(tmp_path, "S1")
    assert exc.value.code == "LL_INVALID_SWITCH"
    monkeypatch.setitem(LC.PRODUCTION_DEFAULTS, "S2", "bogus")
    with pytest.raises(LC.Refusal):
        LC.selected_mode(tmp_path, "S2")


def test_every_production_default_is_a_mode_the_contract_knows():
    assert all(m in ("direct", "librelane", "dual")
               for m in LC.PRODUCTION_DEFAULTS.values())
