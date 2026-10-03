"""Aggregate supported Step27 source controls; no abnormal or mutation probes.

Transport substitution is SOURCE_FIXTURE_ONLY and provides no native credit.
"""
import ast
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
import pytest
import phase3_one_shot_runner as R
import librelane_signoff as LS
import si_crosstalk_check as SG
from test_default_current_power import project, pdk, put, write
from test_default_backend_composition import ReachedFollowingRow


def fake_windows(monkeypatch, p):
    def run(project, image, mounts, script, log, **kwargs):
        data = {pin: {'arr_rise_min': 1.0, 'arr_rise_max': 1.2, 'arr_fall_min': 1.0,
                'arr_fall_max': 1.2, 'slew_rise_max': 0.1, 'slew_fall_max': 0.1,
                'slack_max': 4.0} for pin in ('u0:Z', 'u1:Z', 'u2:A', 'z')}
        put(p / 'phase3/stage3/extracted/neutral_si_timing.json', {'pins': data})
        marker = re.search(r'VIBEIC_CURRENT_DONE [a-f0-9]+', script.read_text())[0]
        text = 'SOURCE_FIXTURE_ONLY\nSI_TIMING_JSON_EMIT_DONE\n' + marker + '\n'
        write(log, text)
        return SimpleNamespace(returncode=0, stdout=text, stderr='')
    monkeypatch.setattr(LS, 'run_sta_script', run)


@pytest.mark.parametrize("cached", [False, True], ids=["ORDINARY_FIRST_ADOPTION", "CURRENT_CACHE"])
def test_ordinary_si_caller_adopts_supported_current_subject(tmp_path, monkeypatch, cached):
    p, folder, lib = project(tmp_path)
    write(R._pl.pnr_dir(p) / "neutral.def", "VERSION 5.8 ;\nDESIGN neutral ;\nEND DESIGN\n")
    fake_windows(monkeypatch, p)
    report = p / "reports/phase3/si_crosstalk.rpt"
    if cached:
        assert R._emit_si_crosstalk_report(p, "neutral", R._pl.extracted_dir(p) / "neutral.spef",
                                          report.parent / "ir_drop.rpt", report, [], pdk(lib), "offline")
    producer = R._emit_si_crosstalk_report
    due = getattr(R, "_step27_current_si_due", None)
    observed = {"cache": "NO_CURRENT_BINDING_CHECK", "productions": 0}

    def admission(*args):
        needed = due(*args) if due else True
        observed["cache"] = "ADOPTION_REQUIRED" if needed else "CURRENT"
        return needed

    def emit(*args, **kwargs):
        observed["productions"] += 1
        return producer(*args, **kwargs)

    def following_row(*args, **kwargs):
        raise ReachedFollowingRow

    monkeypatch.setattr(R, "_step27_current_si_due", admission, raising=False)
    monkeypatch.setattr(R, "_emit_si_crosstalk_report", emit)
    monkeypatch.setattr(R, "_signoff_regen", lambda *args: False)
    monkeypatch.setattr(R, "_step25_native_density_due", lambda *args: False, raising=False)
    monkeypatch.setattr(R, "_emit_power_report", lambda *args, **kwargs: False)
    monkeypatch.setattr(R, "_docker_exec", lambda *args, **kwargs: (1, "", "outside scope"))
    monkeypatch.setattr(R, "_docker_exec_raw", lambda *args, **kwargs: (1, "", "outside scope"))
    monkeypatch.setattr(R, "_discover_container_corner_libs", lambda *args: [])
    monkeypatch.setattr(R, "_pnr_pdn_status", following_row)
    physical_pdk = R.PdkConfig("neutral", str(lib), "", "", None, "unitSite", None)
    with pytest.raises(ReachedFollowingRow):
        R.step_canonicalize_artefacts(p, "neutral", physical_pdk, "offline")
    assert observed["productions"] == (0 if cached else 1), observed
    assert observed["cache"] == ("CURRENT" if cached else "ADOPTION_REQUIRED"), observed
    assert SG.main([str(p), "--json", str(p / "si-row-gate.json")]) == 0
    assert "steps" not in json.loads((p / "phase3/librelane_switch.json").read_text())
    data = json.loads(report.with_suffix(".json").read_text())
    assert data["verdict"] == "ADVISORY_SCREEN_ONLY"


def test_accepted_si_binding_precedes_both_ordinary_formats():
    tree = ast.parse(inspect.getsource(SG.audit))
    bindings = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "si_binding"]
    assert len(bindings) == 1
    json_branch = next(n for n in tree.body[0].body if isinstance(n, ast.If)
                       and ast.unparse(n.test) == "jsn.exists()")
    assert bindings[0].lineno < json_branch.lineno
