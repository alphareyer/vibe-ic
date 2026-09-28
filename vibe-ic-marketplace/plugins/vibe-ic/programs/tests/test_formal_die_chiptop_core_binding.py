"""DIE chip_top's L8 obligations follow the recorded L9 core interface.

The structural reader and the Step-5/gate consumers run normally.  Only the
external SBY executor's transcript is substituted in the accept/refute arms.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import formal_harness_gen as gen  # noqa: E402
import formal_property_run as proof  # noqa: E402
import formal_proof_evidence_check as gate  # noqa: E402


RTL = """module core(input clk, input rst, input d, output reg q);
  always @(posedge clk) if (rst) q <= 1'b0; else q <= d;
endmodule
"""
L8_IDS = {
    "L8.clock_and_reset_waveform.clocks.0.edge",
    "L8.clock_and_reset_waveform.resets.0.polarity",
    "L8.clock_and_reset_waveform.resets.0.sync",
    "L8.clock_and_reset_waveform.resets.0.port_description",
}
NAME_ID = "L8.clock_and_reset_waveform.resets.0.name"


def _project(root):
    docs = root / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L3_PROTOCOL.json").write_text(json.dumps({"no_opcodes_in_input": True}))
    (docs / "L6_FSM.json").write_text(json.dumps({"no_fsm_in_input": True}))
    (docs / "L8_TIMING_WAVEFORM.json").write_text(json.dumps({
        "clock_and_reset_waveform": {
            "clocks": [{"name": "clk", "edge": "posedge"}],
            "resets": [{"name": "rst", "polarity": "active_high",
                        "sync": "synchronous",
                        "port_description": (
                            "synchronous active-high; all internal state is "
                            "zero one cycle after assertion")}]}}))
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": "core", "top_ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst", "direction": "input", "width": 1},
            {"name": "d", "direction": "input", "width": 1},
            {"name": "q", "direction": "output", "width": 1}]}))
    route = root / "input/submission_template/tapeout_declaration.json"
    route.parent.mkdir(parents=True)
    route.write_text(json.dumps({
        "schema": "vibe-ic/tapeout_declaration/1",
        "answers": {"deliverable": "DIE"},
        "answer_provenance": {"deliverable": {
            "answered_by": "owner", "citation": "synthetic fixture declaration"}}}))
    rtl = root / "phase2/stage1/rtl/core.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text(RTL)
    return rtl


def _run(root, monkeypatch, status):
    rtl = _project(root)
    emitted = gen.generate(project=root, top="chip_top", container=None)
    assert emitted["verdict"] == "EMITTED", emitted

    def fake_sby(sby_path, formal_dir, container, timeout, mem_limit_kb=None):
        stem = sby_path.stem
        safety = "PASS" if status == "PASS" else "FAIL"
        return (f"[{stem}_safety] engine_0: abc pdr\n"
                f"[{stem}_safety] DONE ({safety}, rc={0 if safety == 'PASS' else 1})\n"
                f"[{stem}_bmc] engine_0: abc bmc3\n"
                f"[{stem}_bmc] DONE (PASS, rc=0)\n")

    monkeypatch.setattr(proof, "detect_engines", lambda container: {})
    monkeypatch.setattr(proof, "_run_sby", fake_sby)
    result = proof.run(root, harness=Path(emitted["harness_path"]), rtl=[rtl],
                       top=emitted["harness_module"], container=None)
    return emitted, result, gate.audit(root)


def test_die_generated_top_binds_four_l8_claims_to_core_and_proves(tmp_path, monkeypatch):
    emitted, result, checked = _run(tmp_path, monkeypatch, "PASS")
    contract = json.loads((tmp_path / "phase2/stage1/formal/property_contract.json").read_text())
    rows = {r["id"]: r for r in contract["unresolved_obligations"]}
    assert L8_IDS | {NAME_ID} <= rows.keys()
    assert all("program_refused" not in rows[oid] for oid in L8_IDS | {NAME_ID})
    assert emitted["flow_generated_core_binding"] is True
    assert emitted["proves_declared_top"] is False
    assert result["all_proved"] is True
    assert contract["chip_read_top"] == "core"
    assert contract["flow_generated_core_binding"]["ports"] == {"clk": "clk", "rst": "rst", "d": "d"}
    assert set(result.get("program_discharged_obligations") or []) >= L8_IDS
    assert NAME_ID in checked.get("discharged_by_binding", [])


def test_ambiguous_l9_mapping_stays_open(tmp_path, monkeypatch):
    _project(tmp_path)
    l9_path = tmp_path / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9 = json.loads(l9_path.read_text())
    l9["top_ports"].append({"name": "rst", "direction": "input", "width": 1})
    l9_path.write_text(json.dumps(l9))
    emitted = gen.generate(project=tmp_path, top="chip_top", container=None)
    rows = {r["id"]: r for r in emitted["unresolved_obligations"]}
    assert all("program_refused" in rows[oid] for oid in L8_IDS - {
        "L8.clock_and_reset_waveform.clocks.0.edge"})
    assert "program_refused" in rows[NAME_ID]
    assert (tmp_path / "phase2/stage1/formal/formal_authoring_request.json").is_file()


def test_two_pads_for_one_reset_port_stay_open(tmp_path):
    _project(tmp_path)
    record = tmp_path / "reports/phase3/io_pad_chip_top.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "verdict": "WROTE", "chip_top_module": "chip_top",
        "core_module": "core", "pad_instances": {
            "pad_clk": {"port": "clk", "direction": "input"},
            "pad_rst_a": {"port": "rst", "direction": "input"},
            "pad_rst_b": {"port": "rst", "direction": "input"}}}))
    emitted = gen.generate(project=tmp_path, top="chip_top", container=None)
    rows = {r["id"]: r for r in emitted["unresolved_obligations"]}
    assert "program_refused" in rows[NAME_ID]
    assert (tmp_path / "phase2/stage1/formal/formal_authoring_request.json").is_file()


def test_failed_proof_cannot_close_mapped_reset(tmp_path, monkeypatch):
    emitted, result, checked = _run(tmp_path, monkeypatch, "FAIL")
    rows = {r["id"]: r for r in emitted["unresolved_obligations"]}
    assert "program_refused" not in rows[NAME_ID]
    assert emitted["flow_generated_core_binding"] is True
    assert result["all_proved"] is False
    assert NAME_ID not in checked.get("discharged_by_binding", [])
    assert checked["verdict"] != "PASS"
