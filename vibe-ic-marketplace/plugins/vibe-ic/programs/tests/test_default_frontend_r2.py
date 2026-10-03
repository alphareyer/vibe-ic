"""Production Default inputs and tool handoff, without a mode controller."""
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import _plugin_tree  # noqa: F401
import design_one_shot_runner as D
import librelane_contract as LC
import phase3_one_shot_runner as P3
import synth_handoff_netlist_check as H
import lec_equivalence_check as G
from test_t91_step2_verilator_lint_gate import _design, _runner, put, NEG
from test_step9_step14_read_the_pnr_netlist import _clause, _run_clause
from test_f1_lec_subject_is_pnr_input import _gate_arg


def _lint(tmp_path, monkeypatch, *, target="pdkA"):
    project, src, calls, tool = _design(tmp_path, "librelane", NEG, 0, 1)
    # Ordinary Phase 2 has declared inputs but no mode switch.
    (project / "phase3/librelane_switch.json").unlink()
    (project / "input/project.json").unlink()
    put(project / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json",
        {"doc_id": "L19", "fields": {"pdk_target": target}})
    monkeypatch.setattr(LC, "resolve_image", lambda *_: "img")
    monkeypatch.setattr(LC, "pdk_root_resolution", lambda *a, **k:
                        {"path": str(tmp_path / "pdkroot")})
    runner, step = _runner(monkeypatch, tool)
    return project, src, calls, step


def test_step2_default_uses_phase2_declared_pdk_and_normal_gate(tmp_path, monkeypatch):
    project, src, calls, step = _lint(tmp_path, monkeypatch)
    row = step(project)
    assert row is not None and row.status == "PASS", row
    cfg = project / "phase3/librelane/step2/lint_config.json"
    assert json.loads(cfg.read_text())["PDK"] == "pdkA"
    assert "L19" in json.loads(cfg.with_suffix(".provenance.json").read_text())["PDK"]
    assert any("librelane.steps" in c for c in calls)
    gate = json.loads((project / "reports/phase2/lint/verilator_lint_gate.json").read_text())
    assert gate["file_set"]["linted"] == [str(src.resolve())]
    assert row.extras["spec_conformance_tool_ports_rc"] is not None


@pytest.mark.parametrize("damage", ["missing", "conflict", "malformed", "untyped", "switch_conflict"])
def test_step2_bad_pdk_input_refuses_before_native_launch(tmp_path, monkeypatch, damage):
    project, _, calls, step = _lint(tmp_path, monkeypatch)
    target = project / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json"
    if damage == "missing":
        target.unlink()
    elif damage == "conflict":
        put(project / "input/project.json", {"pdk": "otherPDK"})
    elif damage == "malformed":
        target.write_text("{")
    elif damage == "untyped":
        put(target, {"fields": {"pdk_target": {"name": "pdkA"}}})
    else:
        put(project / "phase3/librelane_switch.json", {"pdk": "otherPDK"})
    row = step(project)
    assert row is not None and row.status == "FAIL", row
    assert "LL_PHASE2_PDK" in row.detail
    assert calls == []


def test_step2_project_input_is_an_existing_declared_source(tmp_path, monkeypatch):
    project, _, _, step = _lint(tmp_path, monkeypatch)
    (project / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json").unlink()
    put(project / "input/project.json", {"pdk": "pdkA"})
    assert step(project).status == "PASS"


def test_step2_staged_pdk_cannot_mask_conflicting_l19(tmp_path, monkeypatch):
    project, _, calls, step = _lint(tmp_path, monkeypatch)
    put(project / 'phase1/pdk_staging_read.json', {'adopted_pdk_target': 'otherPDK'})
    assert step(project).status == 'FAIL'
    assert calls == []


def _synthesis(tmp_path, monkeypatch, *, ordinary=False):
    from test_librelane_contract_production_defaults import _chip
    p = _chip(tmp_path / "project")
    if not ordinary:
        put(p / "phase3/librelane_switch.json", {"steps": {"9": "librelane"}})
    put(p / "input/project.json", {"pdk": "pdkA"})
    put(p / "phase1/generated_docs/L8_TIMING_WAVEFORM.json", {
        "clock_domains": [{"role": "primary", "source_pin": "clk", "period_ns": 10}]})
    put(p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json", {"top_module": "top"})
    put(p / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json", {
        "fields": {"pdk_target": "pdkA", "die_area_budget_um": "1000x1000"}})
    rtl = p / "phase2/stage1/rtl/top.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input clk, input a, output reg [3:0] q);\n"
                   "always @(posedge clk) q <= {q[2:0], a};\nendmodule\n")
    root = tmp_path / "pdkroot"
    root.mkdir()
    # A synthetic matching Liberty/LEF pair in an existing registry layout;
    # the production area emitter establishes um^2 from their cell footprints.
    lib = root / "sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    lib.parent.mkdir(parents=True)
    masters = ("lib__dffq_1", "lib__buf_1", "lib__tieh", "lib__tiel") + tuple(
        f'lib__extra_{i}' for i in range(4))
    lib.write_text('library (lib) { time_unit : "1ns";\n' + ''.join(
        f'cell ({c}) {{ area : 1; }}\n' for c in masters) + '}\n')
    lef = lib.parent.parent / 'lef/sky130_fd_sc_hd.lef'
    lef.parent.mkdir()
    lef.write_text(''.join(f'MACRO {c}\n  SIZE 1 BY 1 ;\nEND {c}\n'
                          for c in masters))
    pdk = SimpleNamespace(name="pdkA", liberty=str(lib), macro_libs=[], macro_lefs=[], macro_v=[])
    monkeypatch.setattr(LC, "resolve_image", lambda *_: "img")
    monkeypatch.setattr(LC, "pdk_root_resolution", lambda *a, **k: {"path": str(root)})
    monkeypatch.setattr(P3, "_detect_pdk", lambda project, name: pdk if name == "pdkA" else None)

    def resolve(project, image, source, output, **kw):
        cfg = json.loads(source.read_text())
        cfg.update({"SYNTH_TIEHI_CELL": "lib__tieh/Z", "SYNTH_TIELO_CELL": "lib__tiel/ZN"})
        put(output, cfg)
        return output
    monkeypatch.setattr(LC, "resolve_step_config", resolve)
    calls = []
    def chain(project, image, steps, **kw):
        calls.append([s[0] for s in steps])
        folders = []
        for index, (sid, cfg_path, state_in) in enumerate(steps, 1):
            folder = p / "phase3/librelane" / f"{index:02d}-{sid.lower().replace('.', '-')}"
            folder.mkdir(parents=True)
            cfg = json.loads(cfg_path.read_text())
            put(folder / "config.json", cfg)
            state = {"metrics": {}}
            if sid == 'Yosys.JsonHeader':
                header = folder / 'top.h.json'
                put(header, {'modules': {'top': {'ports': {}}}})
                state['json_h'] = str(header)
            if sid == "Yosys.Synthesis":
                raw = folder / "top.nl.v"
                text = "module top(clk, a, q);\ninput clk; input a; output [3:0] q;\nwire hi, lo;\n"
                text += ''.join(f"lib__dffq_1 r{i} (.CLK(clk), .D(a), .Q(q[{i}]));\n" for i in range(4))
                text += ''.join(f"lib__buf_1 b{i} (.A(a), .Y(n{i}));\n" for i in range(4))
                text += "lib__tieh th (.Z(hi));\nlib__tiel tl (.ZN(lo));\nendmodule\n"
                raw.write_text(text)
                (folder / "fsm_encoding.enc").write_text("# no recoded FSM\n")
                counts = {"lib__dffq_1": 4, "lib__buf_1": 4, "lib__tieh": 1, "lib__tiel": 1}
                state = {"nl": str(raw), "metrics": {"design__instance_unmapped__count": 0,
                         "synthesis__check_error__count": 0, "design__instance__count": 10,
                         "design__instance__area": 10}}
                put(folder / "reports/stat.json", {"modules": {"\\top": {
                    "num_cells": 10, "num_submodules": 0, "area": 10,
                    "num_cells_by_type": counts}}})
                (folder / "reports/stat.rpt").write_text("=== top ===\n   10 10 cells\n" + ''.join(
                    f"       {n} {n} {c}\n" for c, n in counts.items()) +
                    "   Chip area for module '\\top': 10.000000\n"
                    "     of which used for sequential elements: 4.000000\n")
            put(folder / "state_out.json", state)
            if sid == "Yosys.Synthesis":
                receipt = {"input": {"step": sid, "image": image,
                            "config": LC.digest(cfg_path),
                            "state_files": {str(header): LC.digest(header)},
                            "config_files": {str(rtl.resolve()): LC.digest(rtl)}},
                           "sha256": {str(f.relative_to(folder)): LC.digest(f)
                                      for f in folder.rglob('*') if f.is_file()}}
                put(folder / "vibeic_receipt.json", receipt)
            folders.append(folder)
        return folders
    monkeypatch.setattr(LC, "run_chain", chain)
    # A switch to the old generic producer must be observable without starting
    # an unrelated container. All real gate subprocesses remain executable.
    original_run = subprocess.run
    def subprocess_edge(argv, *args, **kwargs):
        if argv and Path(str(argv[0])).name == 'docker':
            raise AssertionError('generic Docker arm selected instead of LibreLane.run_chain')
        return original_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', subprocess_edge)
    return p, rtl, pdk, calls


def _produce(tmp_path, monkeypatch, *, ordinary=False):
    p, rtl, pdk, calls = _synthesis(tmp_path, monkeypatch, ordinary=ordinary)
    stale = p / "phase2/stage2/synth/netlist.v"
    stale.parent.mkdir(parents=True)
    stale.write_text("module old; endmodule\n")
    row = D.step_yosys_synth(p, "top", "unused") if ordinary else P3.step_synth(p, "top", pdk, "unused")
    assert row.status == "PASS", row.detail
    return p, rtl, calls


def _staged_synthesis_library(tmp_path, monkeypatch, route):
    """Reuse the production-caller fixture with the canonical staging shape."""
    import shutil

    p, _, pdk, calls = _synthesis(tmp_path, monkeypatch, ordinary=True)
    library = Path(pdk.liberty)
    root = library.parents[4]
    pdk.name = library.parents[3].name
    put(p / "input/project.json", {"pdk": pdk.name})
    doc = json.loads((p / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json").read_text())
    doc["fields"]["pdk_target"] = pdk.name
    put(p / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json", doc)
    monkeypatch.setattr(P3, "_detect_pdk", lambda project, name: pdk if name == pdk.name else None)
    image_root = Path("/image-selected-pdks")
    monkeypatch.setattr(P3, "PDKS_IN_CONTAINER", str(image_root))
    relative = library.relative_to(root)
    staging = {"path": str(root), "source": "resolved", "derivation": {
        "pdk": pdk.name, "image_pdk_root": str(image_root),
        "guest_path": str(image_root / pdk.name), "host_path": str(root / pdk.name)}}
    if route in ("ciel", "declared_ciel"):
        relative = Path("ciel/family/versions/version-id") / relative
    if route == "declared_ciel":
        target = root / relative
        shutil.copytree(root / pdk.name, target.parents[3])
        library = target
        staging = {"path": str(root), "source": "declared"}
    pdk.liberty = str(library if route == "host" else image_root / relative)
    monkeypatch.setattr(LC, "pdk_root_resolution", lambda *a, **k: staging)
    return p, pdk, calls, library, staging, relative


@pytest.mark.parametrize("route", ["ciel", "named", "host", "declared_ciel"])
def test_step9_staged_library_mapping_preserves_selected_bytes(tmp_path, monkeypatch, route):
    p, pdk, calls, library, staging, _ = _staged_synthesis_library(tmp_path, monkeypatch, route)
    expected_hash = LC.digest(library)
    row = D.step_yosys_synth(p, "top", "unused")
    assert row.status == "PASS", row.detail
    assert Path(pdk.liberty) == library
    assert LC.digest(Path(pdk.liberty)) == expected_hash
    cfg = json.loads((p / "phase3/librelane/synth_config.json").read_text())
    root = Path(staging["path"])
    expected = "/pdk/" + str(library.relative_to(root))
    assert cfg["LIB"] == {"*": [expected]}
    assert cfg["PDK"] == pdk.name
    assert "Yosys.Synthesis" in calls[0]
    assert H.bound_handoff(p)["verdict"] == "PASS"
    assert P3.pnr_input_netlist(p, "top")[0].read_bytes() == (p / "phase2/stage2/synth/netlist.v").read_bytes()


@pytest.mark.parametrize("damage", ["missing", "foreign", "staging_conflict"])
def test_step9_staged_library_missing_or_conflicting_assets_refuse_before_tool(
        tmp_path, monkeypatch, damage):
    p, pdk, calls, library, staging, relative = _staged_synthesis_library(tmp_path, monkeypatch, "ciel")
    root = Path(staging["path"])
    if damage == "missing":
        library.unlink()
    elif damage == "foreign":
        relative = Path(*relative.parts[:4], "otherPDK", *relative.parts[5:])
        decoy = root / relative
        decoy.parent.mkdir(parents=True)
        decoy.write_bytes(library.read_bytes())
        pdk.liberty = str(Path(P3.PDKS_IN_CONTAINER) / relative)
    else:
        staging["derivation"]["pdk"] = "otherPDK"
    row = D.step_yosys_synth(p, "top", "unused")
    assert row.status == "FAIL", row
    assert "LL_PDK_LIB_" in row.detail
    assert calls == []
    assert not (p / "phase2/stage2/synth/netlist.v").exists()


def test_step9_phase2_default_publishes_exact_tool_bytes_and_step14_passes(tmp_path, monkeypatch):
    p, _, calls = _produce(tmp_path, monkeypatch, ordinary=True)
    assert calls == [["Yosys.JsonHeader", "Yosys.Synthesis", "Checker.YosysUnmappedCells",
                     "Checker.YosysSynthChecks", "Checker.NetlistAssignStatements"]]
    mapped = p / "phase2/stage2/synth/top_synth.v"
    assert mapped.read_bytes() == (mapped.parent / "netlist.v").read_bytes()
    assert P3.pnr_input_netlist(p, "top")[0].read_bytes() == mapped.read_bytes()
    for gate in ("yosys_hilomap_required_check", "yosys_script_template_check"):
        cp = _run_clause(p, _clause("14", gate))
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert "VACUOUS" not in cp.stdout


def test_step9_default_keeps_existing_phase2_coupling_gate(tmp_path, monkeypatch):
    p, _, _, calls = _synthesis(tmp_path, monkeypatch, ordinary=True)
    monkeypatch.setattr(D, '_chip_top_coupling_refusals', lambda *_: [
        {'trigger_parameter': 'mode', 'required_parameter': 'width',
         'reason': 'UNSTATED', 'message': 'width is not declared'}])
    row = D.step_yosys_synth(p, 'top', 'unused')
    assert row.status == 'FAIL' and 'COUPLED PARAMETER NOT STATED' in row.detail
    assert calls == []


def test_step9_failed_rerun_invalidates_previous_publication(tmp_path, monkeypatch):
    p, _, _ = _produce(tmp_path, monkeypatch)
    assert H.bound_handoff(p)['verdict'] == 'PASS'
    def unavailable(*args, **kwargs):
        raise LC.Refusal('LL_STEP_ERROR', 'controlled failed current tool call')
    monkeypatch.setattr(LC, 'run_chain', unavailable)
    pdk = P3._detect_pdk(p, 'pdkA')
    assert P3.step_synth(p, 'top', pdk, 'unused').status != 'PASS'
    assert H.bound_handoff(p)['verdict'] == 'FAIL'
    assert _run_clause(p, _clause('14', 'yosys_hilomap_required_check')).returncode == 1


@pytest.mark.parametrize("damage", ["canonical", "mapped", "raw", "rtl", "receipt", "config", "stat", "header", "pretool", "bypass"])
def test_step9_changed_or_bypassed_tool_output_blocks_gates_and_pnr(tmp_path, monkeypatch, damage):
    p, rtl, _ = _produce(tmp_path, monkeypatch)
    folder = p / "phase3/librelane/02-yosys-synthesis"
    files = {"canonical": p / "phase2/stage2/synth/netlist.v",
             "mapped": p / "phase2/stage2/synth/top_synth.v", "raw": folder / "top.nl.v",
             "rtl": rtl, "receipt": folder / "vibeic_receipt.json",
             "config": folder / "config.json", "stat": folder / "reports/stat.json",
             "header": p / 'phase3/librelane/01-yosys-jsonheader/top.h.json'}
    if damage in files:
        with files[damage].open('a') as f:
            f.write("\n ")  # valid bytes, different identity (timestamps are irrelevant)
    elif damage == "pretool":
        (p / "phase2/stage2/synth/netlist.v").write_text("module old; endmodule\n")
    else:
        sidecar = p / "phase2/stage2/synth/synth_inputs.json"
        doc = json.loads(sidecar.read_text())
        doc.pop("librelane_synthesis", None)
        put(sidecar, doc)
    # A valid older recipe must not mask a changed tool product.
    (p / "phase2/stage2/synth/old.ys").write_text(
        "read_verilog -sv old.v\nsynth -flatten\ntechmap\n"
        "hilomap -hicell hi Z -locell lo Z\nwrite_verilog old_unrelated.v\n")
    for gate in ("yosys_hilomap_required_check", "yosys_script_template_check"):
        cp = _run_clause(p, _clause("14", gate))
        assert cp.returncode == 1, cp.stdout + cp.stderr
    row = P3.step_pnr(p, "top", None, "unused", "1000x1000", 0.5)
    assert row.status == "FAIL" and "LL_SYNTH_HANDOFF_INVALID" in row.detail
    assert D.step_lec_equivalence(p, "top", "unused")[-1].status == "FAIL"


def test_step9_consumers_refuse_changed_canonical_bytes(tmp_path, monkeypatch):
    p, _, _ = _produce(tmp_path, monkeypatch)
    with (p / 'phase2/stage2/synth/netlist.v').open('a') as stream:
        stream.write('\n ')
    with pytest.raises(ValueError, match='LL_SYNTH_HANDOFF_INVALID'):
        P3.pnr_input_netlist(p, 'top')
    assert D.step_lec_equivalence(p, 'top', 'unused')[-1].status == 'FAIL'
    import flow_compliance_check as F
    (p / 'phase2/stage2/synth/old.ys').write_text(
        'read_verilog -sv old.v\nsynth -flatten\ntechmap\n'
        'hilomap -hicell hi Z -locell lo Z\nwrite_verilog old_unrelated.v\n')
    assert F._run_yosys_gates(p)[0] is False


def test_step13_default_keeps_native_proof_of_current_handoff(tmp_path, monkeypatch):
    p, _, _ = _produce(tmp_path, monkeypatch)
    original_run = subprocess.run
    lec_runs = []
    def native(argv, *args, **kwargs):
        if len(argv) > 1 and str(argv[1]).endswith('lec_run.py'):
            lec_runs.append(argv)
            mapped = p / _gate_arg(argv)
            put(p / 'reports/lec.json', {
                'verdict': 'PASS', 'equivalent': True, 'compared_points': 4,
                'non_equivalent_points': 0, 'unproven_points': 0,
                'proof_identity': {'top': 'top', 'gate_netlist': {
                    'path': _gate_arg(argv), 'sha256': 'sha256:' + LC.digest(mapped)}}})
            (p / 'reports/lec.rpt').write_text('Equivalence successfully proven!\nProved 4 $equiv cells.\n')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return original_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', native)
    assert LC.selected_mode(p, "13") == "direct"
    rows = D.step_lec_equivalence(p, "top", "unused")
    assert rows[-1].status == "PASS", rows[-1].detail
    assert len(lec_runs) == 1 and _gate_arg(lec_runs[0]) == "phase2/stage2/synth/top_synth.v"
    assert not (p / "reports/lec_eqy.json").exists()
    assert G.main([str(p)]) == 0


@pytest.mark.parametrize('damage', ['zero_points', 'different_netlist'])
def test_step13_native_gate_rejects_vacuous_or_wrong_subject(tmp_path, monkeypatch, damage):
    test_step13_default_keeps_native_proof_of_current_handoff(tmp_path, monkeypatch)
    p = tmp_path / 'project'
    path = p / 'reports/lec.json'
    doc = json.loads(path.read_text())
    if damage == 'zero_points':
        doc['compared_points'] = 0
        (p / 'reports/lec.rpt').write_text('Equivalence successfully proven!\nProved 0 $equiv cells.\n')
    else:
        doc['proof_identity']['gate_netlist']['sha256'] = 'sha256:' + '0' * 64
    put(path, doc)
    assert G.main([str(p)]) == 1
