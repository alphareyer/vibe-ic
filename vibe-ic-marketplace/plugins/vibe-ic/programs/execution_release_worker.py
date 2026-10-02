"""Real source-owned release component for the common execution controller."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import execution_modes as em
from execution_provider_catalog import RELEASE_IDS
from execution_release_rows import ROWS
from execution_backend_worker import resolve_contract, resolve_input_contract, _binding

EXTERNAL_IDS = {"40", "41", "42", "43", "44"}


def _copy_inputs(inputs: Path, project: Path) -> None:
    source = inputs / "project"
    if source.is_dir():
        shutil.copytree(source, project)
    else:
        shutil.copytree(inputs, project, ignore=shutil.ignore_patterns("*.json"))


def _call(module_name: str, args: list) -> dict:
    module = importlib.import_module(module_name)
    fn = getattr(module, "main", None)
    if fn is None:
        raise RuntimeError(f"{module_name}.main is absent")
    try:
        rc = fn([str(x) for x in args])
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 2
    return {"program": module_name, "rc": int(rc) if isinstance(rc, int) else 0}


def produce(step: str, project: Path, params: dict) -> list[dict]:
    records: list[dict] = []
    if step == "14":
        records.append(_call("flow_compliance_check", [project, "--stage-id", "stage_analog", "--strict", "--json", project / "reports/analog/stage_analog_compliance.json"]))
        records.append(_call("synth_handoff_netlist_check", [project]))
    elif step == "16":
        import phase3_one_shot_runner as runner
        notes: list[str] = []
        target = project / "phase3/stage3/cts/clock_plan.json"
        actual = runner.emit_clock_plan(project, target, project / "phase3/stage3/pnr/floorplan.def", project / "phase3/stage3/pnr", notes)
        records.append({"program": "phase3_one_shot_runner.emit_clock_plan", "rc": 0 if actual else 2, "notes": notes})
    elif step == "35":
        records.append(_call("dfm_screen_check", [project, "--json", project / "reports/phase3/dfm_screen.json"]))
    elif step == "36":
        records.append(_call("tapeout_checklist_gen", [project]))
    elif step == "37.4":
        records.append(_call("signoff_metrics_aggregate", [project]))
    elif step == "37.5ip":
        # Call both declared canonical producers even when Magic/OpenSTA is
        # unavailable. Their refusal/capability records are evidence; this
        # worker never fabricates a LEF, Liberty, GDS or Verilog view.
        import digital_hardmacro_gen as hardmacro
        import phase3_one_shot_runner as runner
        pdk_root = str(params.get("pdk_root") or "")
        try:
            ip_result = runner.step_ip_release_docs_gen(
                project, params.get("design_name"), params.get("pdk_name"),
                params.get("source_sha"), params.get("module_role"))
            status = str(ip_result.status)
            rc = 0 if status == "PASS" else 1 if status == "FAIL" else 2
            records.append({"program": "phase3_one_shot_runner.step_ip_release_docs_gen",
                            "rc": rc, "status": status,
                            "verdict": status if status in {"PASS", "FAIL"} else "NOT_MEASURED",
                            "detail": ip_result.detail, "extras": ip_result.extras,
                            "outputs": ip_result.output_files})
            context_name = (ip_result.extras or {}).get("run_context")
            if context_name:
                records.append({"program": "phase3_one_shot_runner._write_ip_release_docs_context",
                                "rc": 0, "output": str(context_name)})
        except BaseException as exc:
            records.append({"program": "phase3_one_shot_runner.step_ip_release_docs_gen",
                            "rc": None, "verdict": "NOT_MEASURED", "reason": str(exc)})
        try:
            rc, report = hardmacro.run(
                project, pdk_root, bool(params.get("full_lef", False)),
                bool(params.get("pinonly", False)), str(params.get("container", "")),
                str(params.get("cell_lef", "")), str(params.get("metal_prefix", "met")))
            from dataclasses import asdict
            record_path = project / "reports/phase3/digital_hardmacro.json"
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps(asdict(report), sort_keys=True, indent=2) + "\n")
            records.append({"program": "digital_hardmacro_gen.run", "rc": rc,
                            "status": report.status, "reason": report.reason,
                            "output": str(record_path.relative_to(project))})
        except BaseException as exc:
            records.append({"program": "digital_hardmacro_gen.run", "rc": None,
                            "verdict": "NOT_MEASURED", "reason": f"{type(exc).__name__}: {exc}"})
    elif step == "37.5ic":
        records.append(_call("tapeout_precheck", [project]))
        records.append(_call("tapeout_docs_gen", [project, "--out-dir", project / "reports/phase3/docs"]))
        records.append(_call("ic_release_docs_gen", [project]))
    elif step == "38":
        records.append(_call("foundry_handoff_pack_gen", [project]))
    elif step == "39":
        records.append({"program": "fpga_on_board_attestation", "rc": None,
                        "verdict": "NOT_MEASURED",
                        "reason": "physical FPGA board evidence is absent"})
    elif step in EXTERNAL_IDS:
        records.append({"program": "external_handoff_record", "rc": None,
                        "verdict": "NOT_MEASURED",
                        "reason": "external physical handoff; no software producer"})
    else:
        raise em.Refusal("RELEASE_UNKNOWN_SOFTWARE_STEP", step)
    return records


def _gate(step: str, project: Path, gate: str) -> dict:
    # These are existing consumers. A consumer error is recorded as
    # NOT_MEASURED; it never becomes a source-owned PASS.
    args = [project]
    if gate == "flow_step_output_content_check":
        args += ["--mode", "dfm"] if step == "35" else ["--mode", "netlist"]
    elif gate in {"digital_hardmacro_check", "release_docs_check", "tapeout_signoff_check",
                  "foundry_handoff_package_check", "signoff_metrics_aggregate"}:
        args += ["--json", project / "reports/phase3" / (gate + ".json")]
    try:
        module = importlib.import_module(gate)
        fn = getattr(module, "main", None)
        if fn is None:
            return {"gate": gate, "verdict": "NOT_MEASURED", "reason": "consumer main absent"}
        rc = fn([str(x) for x in args])
    except BaseException as exc:
        return {"gate": gate, "verdict": "NOT_MEASURED", "reason": f"{type(exc).__name__}: {exc}"}
    return {"gate": gate, "verdict": "PASS" if rc == 0 else "FAIL" if rc == 1 else "NOT_MEASURED", "rc": rc}


def execute(inputs: Path, outputs: Path, *, step_id: str, params: dict) -> dict:
    inputs, outputs = Path(inputs), Path(outputs)
    outputs.mkdir(parents=True, exist_ok=True)
    project = outputs / "project"
    _copy_inputs(inputs, project)
    binding = _binding(inputs)
    input_contract = params.get("input_contract", ROWS[str(step_id)]["canonical"].get("required_inputs", ()))
    missing_inputs = resolve_input_contract(project, input_contract)
    records, gate_records = [], []
    producer_verdict = "NOT_MEASURED"
    missing: list[str] = []
    try:
        records = produce(str(step_id), project, params)
        producer_verdict = "FAIL" if any(r.get("rc") == 1 for r in records) else (
            "NOT_MEASURED" if any(r.get("rc") not in (0,) for r in records) else "PASS")
    except BaseException as exc:
        records.append({"program": "release-producer", "verdict": "NOT_MEASURED", "reason": f"{type(exc).__name__}: {exc}"})
    # Run every declared consumer even when the producer is absent or refused;
    # a partial receipt never upgrades the row to PASS.
    for gate in dict.fromkeys(ROWS[str(step_id)]["policy"]["mandatory_gate_programs"]):
        gate_records.append(_gate(str(step_id), project, gate))
    files, missing = resolve_contract(project, ROWS[str(step_id)]["canonical"]["required_outputs"])
    for name in files:
        source, target = project / name, outputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    fail = producer_verdict == "FAIL" or any(r.get("verdict") == "FAIL" for r in gate_records)
    nm = (producer_verdict == "NOT_MEASURED" or
          any(r.get("verdict") == "NOT_MEASURED" for r in gate_records) or
          bool(missing) or bool(missing_inputs))
    verdict = "FAIL" if fail else "NOT_MEASURED" if nm else "PASS"
    # Native qualification is earned only from the canonical producer's own
    # receipt.  In particular, the hardmacro row must carry a real Magic plus
    # OpenSTA receipt; a source-level worker rc0 or a copied historical report
    # is never promoted into native evidence.
    native_receipts = []
    if str(step_id) == "37.5ip":
        native_receipts = [r for r in records
                           if r.get("program") == "digital_hardmacro_gen.run"
                           and r.get("status") == "PASS" and r.get("rc") == 0]
    result = {"schema": "vibeic/release-evidence/2", "step_id": str(step_id),
              "source_sha": binding.get("source_sha"), "binding": binding,
              "producer": records, "producer_verdict": producer_verdict,
              "gate_records": gate_records, "outputs": files,
              "missing_outputs": missing, "input_contract": input_contract,
              "missing_inputs": missing_inputs, "qualification": verdict,
              "design_verdict": verdict, "physical_measurement": "NOT_MEASURED",
              "native_receipts": native_receipts}
    (outputs / "release-evidence.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", type=Path)
    ap.add_argument("outputs", type=Path)
    ap.add_argument("--step-id", required=True)
    ap.add_argument("--params-json", default="{}")
    args = ap.parse_args(argv)
    try:
        execute(args.inputs, args.outputs, step_id=args.step_id, params=json.loads(args.params_json))
        return 0
    except Exception as exc:
        args.outputs.mkdir(parents=True, exist_ok=True)
        (args.outputs / "release-evidence.json").write_text(json.dumps({
            "schema": "vibeic/release-evidence/2", "step_id": args.step_id,
            "qualification": "NOT_MEASURED", "design_verdict": "NOT_MEASURED",
            "reason": f"{type(exc).__name__}: {exc}"}, sort_keys=True) + "\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
