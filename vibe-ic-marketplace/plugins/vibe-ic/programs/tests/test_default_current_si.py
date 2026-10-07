"""Step27 current-subject controls, including logical/physical identity drift.

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


def physical_project(tmp_path, physical):
    """Seal neutral source inputs with distinct artifact and module names."""
    from test_default_current_power import seal
    import librelane_contract as LC
    p, folder, lib = project(tmp_path)
    nl = p / "phase3/stage3/pnr/neutral_pnr.v"
    spef = p / "phase3/stage3/extracted/neutral.spef"
    nl.write_text(nl.read_text().replace("module neutral(", f"module {physical}("))
    spef.write_text(spef.read_text().replace('*DESIGN "neutral"', f'*DESIGN "{physical}"'))
    original = p / "phase3/librelane/22-config/OpenROAD.STAPostPNR.json"
    config = json.loads(original.read_text())
    config["DESIGN_NAME"] = physical
    put(original, config)
    put(folder / "config.json", config)
    fp = json.loads((folder / "input_fingerprint.json").read_text())
    fp["config"] = LC.digest(original)
    fp["state_files"] = {path: LC.digest(Path(path)) for path in fp["state_files"]}
    seal(folder, fp)
    return p, folder, lib


def emit_physical_si(p, lib):
    report = p / "reports/phase3/si_crosstalk.rpt"
    notes = []
    produced = R._emit_si_crosstalk_report(
        p, "neutral", R._pl.extracted_dir(p) / "neutral.spef",
        report.parent / "ir_drop.rpt", report, notes, pdk(lib), "offline")
    return produced, notes


@pytest.mark.parametrize("physical", ["neutral", "package_shell", "boundary_die"])
def test_si_artifact_stem_and_validated_module_are_independent(tmp_path, monkeypatch, physical):
    import _opensta_current as C
    p, folder, lib = physical_project(tmp_path, physical)
    fake_windows(monkeypatch, p)
    produced, notes = emit_physical_si(p, lib)
    assert produced is True, notes
    assert SG.main([str(p)]) == 0
    receipt = C.si_binding(p)
    assert receipt["top"] == "neutral"
    assert receipt.get("physical_top", receipt["top"]) == physical
    assert f"link_design {physical}" in Path(receipt["execution"]["script"]).read_text()
    assert (p / "phase3/stage3/extracted/neutral_si_timing.json").is_file()
    assert R._step27_current_si_due(p) is False


@pytest.mark.parametrize("mutation", ["source_config", "runtime_config", "missing_config_binding",
    "config_identity", "netlist_identity", "spef_identity", "stale_netlist", "stale_spef"])
def test_si_split_identity_refuses_mismatch_or_stale_input(tmp_path, monkeypatch, mutation):
    import librelane_contract as LC
    from test_default_current_power import seal
    p, folder, lib = physical_project(tmp_path, "package_shell")
    fake_windows(monkeypatch, p)
    # Pre-producer identity mutations remain refused even when a source-fixture
    # tool receipt binds the mismatched material. This is not native evidence.
    fp = json.loads((folder / "input_fingerprint.json").read_text())
    if mutation in ("source_config", "runtime_config", "config_identity"):
        path = (p / "phase3/librelane/22-config/OpenROAD.STAPostPNR.json"
                if mutation == "source_config" else folder / "config.json")
        config = json.loads(path.read_text())
        config["DESIGN_NAME"] = "foreign_die"
        put(path, config)
        if mutation == "config_identity":
            seal(folder, fp)
    elif mutation == "missing_config_binding":
        path = folder / "vibeic_receipt.json"
        receipt = json.loads(path.read_text())
        del receipt["sha256"]["config.json"]
        put(path, receipt)
    else:
        path = (p / "phase3/stage3/pnr/neutral_pnr.v" if "netlist" in mutation
                else p / "phase3/stage3/extracted/neutral.spef")
        path.write_text(path.read_text().replace("package_shell", "foreign_die"))
        if mutation.endswith("identity"):
            fp["state_files"][str(path)] = LC.digest(path)
            seal(folder, fp)
    produced, notes = emit_physical_si(p, lib)
    assert produced is False, notes
    assert SG.main([str(p)]) == 1
    assert R._step27_current_si_due(p) is True
    assert not (p / "reports/phase3/si_crosstalk.current.json").exists()


@pytest.mark.parametrize("mutation", ["physical_top", "top", "source_config", "runtime_config",
                                     "netlist", "spef", "execution"])
def test_si_consumer_revalidates_split_identity(tmp_path, monkeypatch, mutation):
    p, folder, lib = physical_project(tmp_path, "package_shell")
    fake_windows(monkeypatch, p)
    produced, notes = emit_physical_si(p, lib)
    assert produced is True, notes
    assert SG.main([str(p)]) == 0
    if mutation in ("top", "physical_top"):
        receipt = p / "reports/phase3/si_crosstalk.current.json"
        doc = json.loads(receipt.read_text())
        doc[mutation] = "foreign_die"
        put(receipt, doc)
    elif mutation == "execution":
        (folder / "invocation.log").unlink()
    else:
        path = {"source_config": p / "phase3/librelane/22-config/OpenROAD.STAPostPNR.json",
                "runtime_config": folder / "config.json",
                "netlist": p / "phase3/stage3/pnr/neutral_pnr.v",
                "spef": p / "phase3/stage3/extracted/neutral.spef"}[mutation]
        path.write_text(path.read_text() + "\n ")
    assert SG.main([str(p)]) == 1
    assert R._step27_current_si_due(p) is True


def test_declared_si_row_consumes_split_identity_and_blocks_drift(tmp_path, monkeypatch):
    import yaml
    import flow_compliance_check as FC
    from _hostpaths import require_repo
    flow = yaml.safe_load(require_repo('vibe-ic-marketplace', 'plugins', 'vibe-ic',
        'flow', 'phase1_phase2_phase3.yaml').read_text())
    row = next(row for row in flow['steps'] if str(row['id']) == '27')
    command = next(clause['program_exit_zero'] for clause in row['gate']['all_of']
                   if 'si_crosstalk_check' in clause.get('program_exit_zero', ''))
    p, folder, lib = physical_project(tmp_path, "package_shell")
    fake_windows(monkeypatch, p)
    produced, notes = emit_physical_si(p, lib)
    assert produced is True, notes
    result = FC._check_program_exit_zero(p, command)
    assert result[0] is True, result
    (folder / "invocation.log").unlink()
    result = FC._check_program_exit_zero(p, command)
    assert result[0] is False, result


def corner_project(tmp_path, physical="package_shell"):
    """Two receipt-bound SPEFs whose differences are legitimate corner data."""
    import librelane_contract as LC
    import librelane_postroute as LP
    from test_default_current_power import seal
    p, folder, lib = physical_project(tmp_path, physical)
    canonical = p / "phase3/stage3/extracted/neutral.spef"
    selected = write(canonical.parent / "spef_corners/neutral.max.spef",
                     canonical.read_text().replace("0.01", "0.02"))
    state_path = folder / "state_out.json"
    state = json.loads(state_path.read_text())
    state["spef"] = {"nom_*": str(canonical), "max_*": str(selected)}
    put(state_path, state)
    write(folder / "max_typ/sta.log",
          (folder / "nom_typ/sta.log").read_text().replace("nom_typ", "max_typ"))
    fp = json.loads((folder / "input_fingerprint.json").read_text())
    fp["state_files"][str(selected)] = LC.digest(selected)
    seal(folder, fp)
    record = json.loads((p / LP.STEP23_RECORD).read_text())
    record["sta_state_sha256"] = LC.digest(state_path)
    record["judgment"]["worst_setup"]["corner"] = "max_typ"
    put(p / LP.STEP23_RECORD, record)
    return p, folder, lib, selected


@pytest.mark.parametrize("physical", ["neutral", "package_shell"])
@pytest.mark.parametrize("caller_view", ["logical_alias", "selected_corner"])
def test_si_corner_spef_drives_screen_timing_and_consumer(tmp_path, monkeypatch, physical, caller_view):
    import _opensta_current as C
    p, folder, lib, selected = corner_project(tmp_path, physical)
    canonical = p / "phase3/stage3/extracted/neutral.spef"
    original = canonical.read_bytes()
    fake_windows(monkeypatch, p)
    report = p / "reports/phase3/si_crosstalk.rpt"
    notes = []
    supplied = canonical if caller_view == "logical_alias" else selected
    produced = R._emit_si_crosstalk_report(p, "neutral", supplied,
        report.parent / "ir_drop.rpt", report, notes, pdk(lib), "offline")
    assert produced is True, notes
    assert SG.main([str(p)]) == 0
    receipt = C.si_binding(p)
    bound = {r["role"]: r for r in receipt["inputs"]}
    assert receipt["tool_corner"] == "max_typ"
    assert bound["spef"]["path"] == str(selected)
    assert bound["spef"]["sha256"] == C.digest(selected)
    body = json.loads(report.with_suffix(".json").read_text())
    assert body["spef"] == str(selected)
    expected = R._si_coupling_metrics(*R._parse_spef_caps(selected.read_text()))
    assert body["max_crosstalk_noise"] == expected["max_crosstalk_noise_mv"]
    script = Path(receipt["execution"]["script"]).read_text()
    assert str(selected) in script and str(canonical) not in script
    assert canonical.read_bytes() == original
    assert R._step27_current_si_due(p) is False


@pytest.mark.parametrize("mutation", ["selected_bytes", "unbound_selected", "ambiguous_corner", "wrong_design"])
def test_si_corner_handoff_refuses_invalid_tool_view(tmp_path, monkeypatch, mutation):
    import librelane_contract as LC
    import librelane_postroute as LP
    from test_default_current_power import seal
    p, folder, lib, selected = corner_project(tmp_path)
    fp = json.loads((folder / "input_fingerprint.json").read_text())
    if mutation == "selected_bytes":
        selected.write_text(selected.read_text() + "\n ")
    elif mutation == "unbound_selected":
        del fp["state_files"][str(selected)]
        seal(folder, fp)
    elif mutation == "wrong_design":
        selected.write_text(selected.read_text().replace('"package_shell"', '"foreign_die"'))
        fp["state_files"][str(selected)] = LC.digest(selected)
        seal(folder, fp)
    else:
        path = folder / "state_out.json"
        state = json.loads(path.read_text())
        state["spef"]["*"] = state["spef"]["nom_*"]
        put(path, state)
        seal(folder, fp)
        record = json.loads((p / LP.STEP23_RECORD).read_text())
        record["sta_state_sha256"] = LC.digest(path)
        put(p / LP.STEP23_RECORD, record)
    fake_windows(monkeypatch, p)
    produced, notes = emit_physical_si(p, lib)
    assert produced is False, notes
    assert SG.main([str(p)]) == 1


@pytest.mark.parametrize("mutation", ["selected_bytes", "report_spef", "timing_spef", "receipt_corner", "step23_corner"])
def test_si_corner_consumer_rejects_stale_or_mixed_scene(tmp_path, monkeypatch, mutation):
    import _opensta_current as C
    import librelane_postroute as LP
    p, folder, lib, selected = corner_project(tmp_path)
    fake_windows(monkeypatch, p)
    produced, notes = emit_physical_si(p, lib)
    assert produced is True, notes
    assert SG.main([str(p)]) == 0
    report = p / "reports/phase3/si_crosstalk.json"
    current = report.with_suffix(".current.json")
    if mutation == "selected_bytes":
        selected.write_text(selected.read_text() + "\n ")
    elif mutation == "step23_corner":
        path = p / LP.STEP23_RECORD
        record = json.loads(path.read_text())
        record["judgment"]["worst_setup"]["corner"] = "nom_typ"
        put(path, record)
    elif mutation == "report_spef":
        doc = json.loads(report.read_text())
        doc["spef"] = str(p / "phase3/stage3/extracted/neutral.spef")
        put(report, doc)
        receipt = json.loads(current.read_text())
        for row in receipt["outputs"]:
            if row["role"] == "si_result":
                row["sha256"] = C.digest(report)
        put(current, receipt)
    else:
        path = (p / "phase3/stage3/extracted/neutral_si_timing.current.json"
                if mutation == "timing_spef" else current)
        receipt = json.loads(path.read_text())
        if mutation == "timing_spef":
            receipt["inputs"] = [C.file_record(p / "phase3/stage3/extracted/neutral.spef", "spef", p)
                                  if r["role"] == "spef" else r for r in receipt["inputs"]]
        else:
            receipt["tool_corner"] = "nom_typ"
        put(path, receipt)
    assert SG.main([str(p)]) == 1
    assert R._step27_current_si_due(p) is True
