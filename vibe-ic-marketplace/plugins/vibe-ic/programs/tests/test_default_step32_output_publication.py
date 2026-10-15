"""Neutral native transport; real Step32 producer/policy/publisher/consumers."""
import hashlib
import json
from pathlib import Path

import pytest

import execution_backend_producers as backend
import librelane_contract as contract
import librelane_postroute_repair as repair
import phase3_one_shot_runner as runner
from test_backend_input_contract_sdc import _project
from test_pad_connected_pdn_ring import _pdk


REPAIR = "phase3/stage3/postroute_timing_repair"
RAW = REPAIR + "/postroute_timing_repair_decision.json"
CANONICAL = REPAIR + "/postroute_timing_repair_decision.canonical.json"


def _write(root, raw, content):
    p = root / raw
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


@pytest.mark.parametrize("case", ["clean", "adopted", "stale_canonical", "missing_raw", "missing_hash",
                                  "changed_source", "missing_outcome", "policy_reject", "measured_fail"])
def test_current_step32_publishes_before_its_output_receipt(tmp_path, monkeypatch, case):
    project = _project(tmp_path)
    _write(project, "input/step_0_5ic_answers.json", "{}\n")
    _write(project, "input/submission_template/neutral.json", "{}\n")
    _write(project, "phase3/stage3/pnr/routed.def", "DESIGN neutral ;\nEND DESIGN\n")
    _write(project, "phase3/stage3/pnr/neutral_pnr.v", "module neutral; endmodule\n")
    _write(project, "phase3/librelane_switch.json", '{"steps":{"32":"librelane"}}\n')
    stale = None
    if case == "stale_canonical":
        stale = _write(project, CANONICAL, '{"stale_previous_invocation":true}\n').read_bytes()
    pdk = _pdk(tmp_path, ring=None)
    monkeypatch.setattr(runner, "_backend_parameter_values", lambda *_: {
        "pdk_root": str(tmp_path), "image_id": "SOURCE_CONTROL_ONLY"})
    monkeypatch.setattr(backend, "_parameters", lambda *_: (runner, contract, pdk, "SOURCE_CONTROL_ONLY"))
    # The source control supplies image/PDK and native child transport only.
    # produce_signoff, the runner's native-row adapter and its result/handoff,
    # raw publisher, shared decision, canonical publisher and gates are real.
    monkeypatch.setattr(contract, "resolve_image", lambda *_: "SOURCE_CONTROL_ONLY")
    monkeypatch.setattr(contract, "pdk_root_resolution", lambda *_a, **_k: {"path": str(tmp_path)})
    monkeypatch.setattr(runner, "_docker_exec", lambda *_a, **_k: (0, "", ""))
    native_calls, native_raw = [], []

    def controlled_native(root, **_kwargs):
        native_calls.append("native_child_transport")
        adopted = "neutral-candidate" if case == "adopted" else None
        folder = "phase3/librelane/32-neutral/04-openroad-stapostpnr"
        state = _write(root, folder + "/state_out.json", '{"metrics":{"source_control":1}}\n')
        final = {"drv_count": 0, "setup_ws_min": 0.5, "hold_ws_min": 0.2,
                 "antenna_nets": 0, "antenna_pins": 0, "antenna_state": folder,
                 "sta_state": str(state), "sta_state_sha256": hashlib.sha256(state.read_bytes()).hexdigest()}
        baseline = dict(final)
        report = {"step": "32", "verdict": "FAIL" if case == "measured_fail" else "PASS",
                  "adopted": adopted, "corners": ["neutral_scene"], "baseline": baseline,
                  "final": final, "floors": {"setup": [0.0, "neutral fixture"], "hold": [0.0, "neutral fixture"]},
                  "candidates": [], "final_supply_ownership": {"verdict": "PASS"}}
        if adopted:
            baseline["drv_count"] = 1
            d = _write(root, "phase3/librelane/32-neutral/candidate.def", "DESIGN neutral ;\n# candidate\nEND DESIGN\n")
            n = _write(root, "phase3/librelane/32-neutral/candidate.v", "module neutral; /* candidate */ endmodule\n")
            selected = _write(root, "phase3/librelane/32-neutral/selected.json", json.dumps({"def": str(d), "nl": str(n)}))
            report["adopted_state"] = str(selected)
            report["candidates"] = [{"candidate": adopted, "closure_decision": "PROMOTED",
                "repair_metrics": {"vibeic__prr__changed": 1, "vibeic__prr__unrouted__added": 0},
                "measurement": {"sta_state_sha256": final["sta_state_sha256"]}}]
        source = _write(root, repair.REPORT_REL, json.dumps(report))
        repair._publish_declared_repair(root, report, source)
        if case == "missing_raw":
            (root / RAW).unlink()
        elif case == "missing_hash":
            data = json.loads((root / RAW).read_text()); data.pop("source_report_sha256")
            _write(root, RAW, json.dumps(data))
        elif case == "changed_source":
            source.write_text(source.read_text() + "\n")
        elif case == "missing_outcome":
            (root / REPAIR / "no_repair_needed.flag").unlink()
        elif case == "policy_reject":
            import postroute_timing_repair_decision as policy
            _write(root, dict(policy._NON_TIMING_SIGNOFF_ARTEFACTS)["ir_drop"], '{"verdict":"FAIL"}\n')
        if (root / RAW).is_file():
            native_raw.append((root / RAW).read_bytes())
        return report

    monkeypatch.setattr(repair, "run", controlled_native)
    result = runner._default_pnr_rows(project, "neutral", pdk, "SOURCE_CONTROL_ONLY", ["32"])
    receipt_path = project / "reports/phase3/default_steps/32/backend_result.json"
    receipt = json.loads(receipt_path.read_text())
    canonical = (json.loads((project / CANONICAL).read_text())
                 if (project / CANONICAL).is_file() else None)
    observation = {"case": case, "status": result.status, "detail": result.detail,
                   "receipt": receipt, "canonical": canonical, "native_calls": native_calls,
                   "source_control_only": True}
    _write(project, "API_OBSERVATION.json", json.dumps(observation, indent=2))
    assert native_calls == ["native_child_transport"]
    expected = "PASS" if case in ("clean", "adopted", "stale_canonical") else "FAIL" if case in ("policy_reject", "measured_fail") else "NOT_MEASURED"
    assert result.status == expected, result.detail
    if native_raw:
        assert (project / RAW).read_bytes() == native_raw[0], "canonical policy must retain producer bytes"
    if expected == "PASS":
        assert (project / CANONICAL).read_bytes() != stale
        assert canonical["basis"] == "current Step-32 STAPostPNR per-corner census"
        assert canonical["source_report"] == repair.REPORT_REL
        assert canonical["timing_repair_needed"] is False
        assert canonical["repair_needed"] is (case == "adopted")
        current = receipt["current_step_declared_producers"]
        assert current["verdict"] == "PRODUCED"
        assert "provenance.jsonl" not in current["inputs"]
        if case == "adopted":
            assert '"record": "promotion"' in (project / "provenance.jsonl").read_text()
        assert {RAW, CANONICAL} <= set(current["outputs"])
        assert receipt["canonical_step_result"]["status"] == "PASS"
        assert (project / REPAIR / ("repair_log.json" if case == "adopted" else "no_repair_needed.flag")).is_file()
    elif case == "policy_reject":
        assert canonical["repair_needed"] is True
        assert canonical["timing_repair_needed"] is False
        assert canonical["nontiming_failures"]
