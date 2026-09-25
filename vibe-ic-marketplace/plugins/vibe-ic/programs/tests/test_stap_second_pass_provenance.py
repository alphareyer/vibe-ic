"""A rerun must declare its final synth log and every rejected SDR tail write."""
import hashlib
import inspect
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as runner  # noqa: E402
import provenance_output_hash_completeness_check as hashes  # noqa: E402


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rows(project):
    return [json.loads(line) for line in (project / "provenance.jsonl").read_text().splitlines()]


def test_rejected_tail_selects_each_boundary_it_rewrites(tmp_path, monkeypatch):
    project = tmp_path
    out = project / "phase3/stage3/pnr"
    out.mkdir(parents=True)
    old = out / "boundary_after_postroute_spef_extract.def"
    old.write_bytes(b"incumbent")
    rel = old.relative_to(project).as_posix()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "openroad", "exit_code": 0, "outputs": {rel: _sha(b"incumbent")}
    }) + "\n")
    deck = ("if {[catch {write_def /mnt/phase3/stage3/pnr/"
            "boundary_after_postroute_spef_extract.def} _bd_e]} {\n"
            "write_def /mnt/phase3/stage3/pnr/routed.def\n"
            "write_verilog /mnt/phase3/stage3/pnr/core_pnr.v\n")
    products = (runner._pnr_tail_products(out, "/mnt/phase3/stage3/pnr", deck)
                + runner._pnr_sdr_boundary_products(
                    out, "/mnt/phase3/stage3/pnr", deck))
    assert "_pnr_sdr_boundary_products(" in inspect.getsource(
        runner._pnr_adopt_sdr_candidates)
    assert old in products
    assert [p.name for p in products] == [
        "routed.def", "core_pnr.v", "boundary_after_postroute_spef_extract.def"]

    def fake_eda(container, cmd, **kwargs):
        assert str(old) in kwargs["outputs"]
        assert not old.exists(), "the previous DEF must be set aside before EDA"
        old.write_bytes(b"rejected candidate tail")
        return 0, "", ""

    monkeypatch.setattr(runner, "_docker_exec", fake_eda)
    runner._declared_session_exec("fake", "openroad tail", products)
    assert old.read_bytes() == b"rejected candidate tail"


def test_sdr_does_not_claim_boundaries_absent_from_its_tail(tmp_path):
    out = tmp_path / "pnr"
    out.mkdir()
    deck = ("write_def /mnt/pnr/routed.def\n"
            "write_def /mnt/other/boundary_old.def\n")
    assert runner._pnr_sdr_boundary_products(out, "/mnt/pnr", deck) == []


def test_synth_rerun_declares_final_runner_log(tmp_path, monkeypatch):
    project = tmp_path
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text("module core(input a, output y); assign y=a; endmodule\n")
    synth = project / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    log = synth / "synth.log"
    log.write_bytes(b"old run")
    rel = log.relative_to(project).as_posix()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "yosys", "exit_code": 0, "outputs": {rel: _sha(b"old run")}
    }) + "\n")
    lib = tmp_path / "test.lib"
    lib.write_text("library(test) { cell(INV) { area : 1.0; } }\n")
    pdk = runner.PdkConfig(name="test", liberty=str(lib), tech_lef="test.lef",
                           cell_lef="test.lef", cell_gds=None, site="unit",
                           drc_deck=None)

    def fake_eda(container, cmd, **kwargs):
        marker = kwargs.get("marker")
        if marker and str(marker).endswith("_synth.v"):
            Path(marker).write_text("module core(input a, output y); assign y=a; endmodule\n")
            return 0, "Number of cells: 1\n", ""
        return 0, "", ""

    monkeypatch.setattr(runner, "_docker_exec", fake_eda)
    monkeypatch.setattr(runner, "_to_container_path", lambda path, container: path)
    runner.set_invocation_provenance_sink(project)
    try:
        result = runner.step_synth(project, "core", pdk, "fake")
    finally:
        runner.set_invocation_provenance_sink(None)
    assert result.status == "PASS", result.detail
    assert [r for r in _rows(project) if rel in r.get("outputs", {})][-1]["outputs"][rel] == _sha(log.read_bytes())
    verdict, findings = hashes.audit(project)
    assert verdict == "PASS", findings


def test_synth_rerun_supersedes_the_old_netlist_digest(tmp_path, monkeypatch):
    project = tmp_path
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text("module core(input a, output y); assign y=a; endmodule\n")
    synth = project / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    netlist = synth / "core_synth.v"
    netlist.write_bytes(b"old mapped bytes")
    rel = netlist.relative_to(project).as_posix()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "yosys", "exit_code": 0,
        "outputs": {rel: _sha(netlist.read_bytes())}
    }) + "\n")
    lib = project / "test.lib"
    lib.write_text("library(test) { cell(INV) { area : 1.0; } }\n")
    pdk = runner.PdkConfig(name="test", liberty=str(lib), tech_lef="test.lef",
                           cell_lef="test.lef", cell_gds=None, site="unit",
                           drc_deck=None)

    def fake_eda(container, cmd, **kwargs):
        marker = kwargs.get("marker")
        if marker and str(marker).endswith("_synth.v"):
            Path(marker).write_text(
                "module core(input a, output y); assign y=a; endmodule\n")
            return 0, "Number of cells: 1\n", ""
        return 0, "", ""

    monkeypatch.setattr(runner, "_docker_exec", fake_eda)
    monkeypatch.setattr(runner, "_to_container_path", lambda path, _container: path)
    result = runner.step_synth(project, "core", pdk, "fake")
    runner.set_invocation_provenance_sink(None)
    assert result.status == "PASS", result.detail
    declarations = [r["outputs"][rel] for r in _rows(project)
                    if rel in r.get("outputs", {})]
    assert declarations[-1] == _sha(netlist.read_bytes())
    assert hashes.audit(project)[0] == "PASS"


def test_synth_log_two_process_writes_and_undeclared_mutation(tmp_path, monkeypatch):
    project = tmp_path
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text(
        "module core(input a, output y); assign y=a; endmodule\n")
    synth = project / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    log = synth / "synth.log"
    log.write_bytes(b"original transcript")
    rel = log.relative_to(project).as_posix()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "yosys", "exit_code": 0,
        "outputs": {rel: _sha(log.read_bytes())}
    }) + "\n")
    lib = project / "test.lib"
    lib.write_text("library(test) { cell(INV) { area : 1.0; } }\n")
    pdk = runner.PdkConfig(name="test", liberty=str(lib), tech_lef="test.lef",
                           cell_lef="test.lef", cell_gds=None, site="unit",
                           drc_deck=None)
    calls = 0

    def fake_eda(container, cmd, **kwargs):
        nonlocal calls
        marker = kwargs.get("marker")
        if marker and str(marker).endswith("_synth.v"):
            calls += 1
            Path(marker).write_text(
                "module core(input a, output y); assign y=a; endmodule\n")
            return 0, f"Number of cells: {calls}\n", ""
        return 0, "", ""

    monkeypatch.setattr(runner, "_docker_exec", fake_eda)
    monkeypatch.setattr(runner, "_to_container_path", lambda path, container: path)
    for expected in (1, 2):
        # A new runner process starts with no sink. This is the state the
        # older test, which manually enabled the sink, could not reproduce.
        runner.set_invocation_provenance_sink(None)
        result = runner.step_synth(project, "core", pdk, "fake")
        assert result.status == "PASS", result.detail
        declarations = [r for r in _rows(project) if rel in r.get("outputs", {})]
        assert len(declarations) == expected + 1
        assert declarations[-1]["outputs"][rel] == _sha(log.read_bytes())
        assert hashes.audit(project)[0] == "PASS"

    # A mutation between sessions breaks the declared-input chain. The next
    # legitimate write must not launder that unexplained change.
    log.write_bytes(log.read_bytes() + b"\nundeclared mutation")
    runner.set_invocation_provenance_sink(None)
    result = runner.step_synth(project, "core", pdk, "fake")
    assert result.status == "PASS", result.detail
    declarations = [r for r in _rows(project) if rel in r.get("outputs", {})]
    assert len(declarations) == 3
    assert declarations[-1]["outputs"][rel] != _sha(log.read_bytes())
    assert hashes.audit(project)[0] == "FAIL"
    runner.set_invocation_provenance_sink(None)
