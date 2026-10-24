"""M06-M09 Default adapters over the existing mixed-signal producers.

The adapter does not translate a legacy component PASS into a canonical row
PASS.  A row-specific owner document binds the exact current material inputs,
geometry, scenario population and candidate/final role.  The completion
consumer then re-opens the native producer and gate reports before accepting
the supported row scope.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _atomic_artefact import write_json

SCHEMA = "vibeic.flow109.mixed-native.v1"
INPUT_SCHEMA = "vibeic.flow109.mixed-native-input.v1"
COMPLETION_SCHEMA = "vibeic.flow109.default-row-completion.v1"
PINNED_IMAGE = ("ghcr.io/vibeic/vibeic-eda@sha256:"
                "0fc30ecd1307a11bd7757d1c5710127cd88e245059f29eab8b52e38ba4d60406")
ROWS = {
    "M06": {"legacy": "M3", "scope": "candidate", "obligations": (
        "base_M5.candidate_ams_scenarios", "base_M5.candidate_ams_performance")},
    "M07": {"legacy": "M1", "scope": "assembly", "obligations": (
        "base_M6.mixed_gds_reference_identity", "base_M6.missing_duplicate_macro_instances")},
    "M08": {"legacy": "M3", "scope": "final", "obligations": (
        "ams_final.final_ams_scenarios", "ams_final.final_ams_noise_settling_calibration")},
    "M09": {"legacy": "M4", "scope": "signoff", "obligations": (
        "base_M7.mixed_requirement_evidence", "base_M7.mixed_model_final_geometry_identity")},
}


class Refusal(RuntimeError):
    def __init__(self, code, detail, *, status="NOT_MEASURED"):
        self.code, self.status = code, status
        super().__init__(f"{code}: {detail}")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refusal("MIXED_ROW_EVIDENCE_ABSENT_OR_INVALID", str(path)) from exc
    if not isinstance(value, dict):
        raise Refusal("MIXED_ROW_EVIDENCE_INVALID", str(path))
    return value


def _local(project, name):
    if (not isinstance(name, str) or not name or Path(name).is_absolute()
            or ".." in Path(name).parts):
        raise Refusal("MIXED_ROW_INPUT_PATH_INVALID", str(name))
    path = (project / name).resolve()
    if not path.is_relative_to(project):
        raise Refusal("MIXED_ROW_INPUT_PATH_INVALID", name)
    return path


def operation_sources():
    from execution_provider_catalog import source_closure
    roots = {Path(__file__), HERE / "flow109.py", HERE / "mixed_signal_m3_run.py",
             HERE / "_mixed_signal_native.py", HERE / "_mixed_signal_current.py",
             HERE / "mixed_signal_cosim_check.py", HERE / "mixed_signal_interface_si_check.py",
             HERE / "mixed_signal_top_lvs_run.py", HERE / "mixed_signal_merge_check.py",
             HERE / "mixed_signal_signoff_run.py", HERE / "mixed_signal_signoff_check.py",
             HERE / "_mixed_signal_top_pv.py"}
    return {str(path.relative_to(HERE.parent)): _sha(path)
            for path in sorted(source_closure(roots))}


def _current_inputs(project, step, top):
    if step in ("M06", "M08"):
        import mixed_signal_m3_run as m3
        values = m3.inputs(project, top)
    elif step == "M07":
        import mixed_signal_top_lvs_run as m1
        values = m1.current_merge_inputs(project, top)[-1]
    elif step == "M09":
        import _mixed_signal_top_pv as top_pv
        import mixed_signal_m3_run as m3
        spec, values = top_pv.request(project, top)
        values = dict(values)
        for name in (m3.RECEIPT, m3.DIR + "/merge.json",
                     m3.DIR + "/top_lvs.json", m3.DIR + "/power_domain_run.json"):
            path = _local(project, name)
            values[name] = m3.digest(path) if path.is_file() else None
        if spec.get("layout"):
            values[spec["layout"]] = m3.digest(_local(project, spec["layout"]))
    else:
        raise Refusal("MIXED_ROW_CONTEXT_MISMATCH", step)
    if not isinstance(values, dict) or not values or any(value is None for value in values.values()):
        raise Refusal("MIXED_ROW_REQUIRED_INPUT_ABSENT", step)
    return dict(sorted(values.items()))


def _command_plan(project, step, manifest, attempt):
    python = str(Path(sys.executable).resolve())
    top, pdk = manifest["top"], manifest["pdk"]
    if step in ("M06", "M08"):
        producer = [python, str(HERE / "mixed_signal_m3_run.py"), str(project),
                    "--top", top, "--container", "host", "--json", str(attempt / "producer.json")]
        gates = [
            [python, str(HERE / "mixed_signal_cosim_check.py"), str(project),
             "--require-current-production", "--json", str(attempt / "cosim.json")],
            [python, str(HERE / "mixed_signal_interface_si_check.py"), str(project),
             "--require-current-production", "--json", str(attempt / "si.json")],
        ]
    elif step == "M07":
        producer = [python, str(HERE / "mixed_signal_top_lvs_run.py"), str(project),
                    "--top", top, "--container", "host", "--pdk", pdk,
                    "--json", str(attempt / "producer.json")]
        gates = [[python, str(HERE / "mixed_signal_merge_check.py"), str(project),
                  "--json", str(attempt / "merge.json")]]
    else:
        producer = [python, str(HERE / "mixed_signal_signoff_run.py"), str(project),
                    "--top", top, "--json", str(attempt / "producer.json")]
        gates = [[python, str(HERE / "mixed_signal_signoff_check.py"), str(project),
                  "--require-current-production", "--json", str(attempt / "signoff.json")]]
    return producer, gates


def _run(argv, log):
    done = subprocess.run(argv, text=True, capture_output=True)
    log.write_text(done.stdout + done.stderr)
    return {"argv": argv, "returncode": done.returncode,
            "stdout_sha256": hashlib.sha256(done.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(done.stderr.encode()).hexdigest(),
            "log": {"path": str(log), "sha256": _sha(log)}}


def _semantic_pass(project, step, manifest):
    if step in ("M06", "M08"):
        import mixed_signal_m3_run as m3
        cosim, si = m3.audit(project, "cosim"), m3.audit(project, "si")
        receipt = _read(project / m3.RECEIPT)
        scenarios = receipt.get("scenario_ids")
        required = manifest.get("required_scenarios")
        if (not isinstance(required, list) or not required or scenarios != required
                or len(set(required)) != len(required)):
            raise Refusal("MIXED_ROW_SCENARIO_POPULATION_MISMATCH", str((required, scenarios)))
        if cosim.get("verdict") != "PASS" or si.get("verdict") != "PASS":
            raise Refusal("MIXED_ROW_CURRENT_CONSUMER_REFUSED", str((cosim, si)), status="FAIL")
        return {"cosim": cosim, "si": si, "scenario_ids": scenarios,
                "geometry_sha256": _sha(project / "phase3/mixed_signal/top_merged.gds")}
    if step == "M07":
        import mixed_signal_top_lvs_run as m1
        top_lvs = _read(project / "reports/analog/mixed_signal/top_lvs.json")
        merge = _read(project / "reports/analog/mixed_signal/merge.json")
        stale = m1.validate_current_receipt(project, top_lvs)
        if top_lvs.get("verdict") != "PASS" or merge.get("verdict") != "PASS" or stale:
            raise Refusal("MIXED_ROW_CURRENT_LVS_REFUSED", stale or str((merge, top_lvs)), status="FAIL")
        _, macros, _views, _netlist, detail, _hashes = m1.current_merge_inputs(project, manifest["top"])
        population = [(item["inst"], cell) for cell, items in detail["placements"].items()
                      for item in items]
        if not macros or len(population) != len({item[0] for item in population}):
            raise Refusal("MIXED_ROW_MACRO_POPULATION_INVALID", str(population), status="FAIL")
        return {"mixed_gds_sha256": _sha(project / "phase3/mixed_signal/top_merged.gds"),
                "macro_instances": population, "missing_duplicate_macro_instances": 0}
    import mixed_signal_signoff_run as signoff
    audit = signoff.audit(project)
    current = signoff.verify(project)
    if audit.get("verdict") != "PASS" or audit.get("ready_for_tapeout") is not True:
        raise Refusal("MIXED_ROW_CURRENT_SIGNOFF_REFUSED", str(audit), status="FAIL")
    return {"signoff": current,
            "final_geometry_sha256": _sha(project / "phase3/mixed_signal/top_merged.gds")}


def _prepare(project, context):
    step = context.get("requested_step")
    if step not in ROWS or context.get("native_step_id") != "flow109." + str(step):
        raise Refusal("MIXED_ROW_CONTEXT_MISMATCH", str(step))
    row = context.get("resolved_row") or {}
    dispatch = ((row.get("profile") or {}).get("dispatch") or {})
    if row.get("id") != step or dispatch.get("adapter") != "flow109_mixed_native":
        raise Refusal("MIXED_ROW_CONTEXT_MISMATCH", "source-owned dispatch required")
    manifest_path = project / "input/flow109" / (step + ".json")
    manifest = _read(manifest_path)
    if (manifest.get("schema") != INPUT_SCHEMA or manifest.get("step_id") != step
            or manifest.get("scope") != ROWS[step]["scope"]
            or manifest.get("image") != PINNED_IMAGE
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", str(manifest.get("top") or ""))
            or not isinstance(manifest.get("pdk"), str) or not manifest["pdk"]):
        raise Refusal("MIXED_ROW_INPUT_INVALID", str(manifest_path))
    current = _current_inputs(project, step, manifest["top"])
    if manifest.get("input_sha256") != current:
        raise Refusal("MIXED_ROW_INPUT_STALE_OR_INCOMPLETE", step)
    if manifest.get("geometry_sha256") != _sha(project / "phase3/mixed_signal/top_merged.gds"):
        raise Refusal("MIXED_ROW_GEOMETRY_IDENTITY_MISMATCH", step)
    return manifest, current, operation_sources()


def execute(project, context):
    project = Path(project).resolve()
    step = str(context.get("requested_step"))
    attempt = project / "reports/flow109" / step / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    result = {"schema": SCHEMA, "step_id": step, "context_sha256": context.get("context_sha256"),
              "component_status": "NOT_MEASURED", "native_started": False,
              "flow109_acceptance": "NOT_MEASURED", "whole_flow_acceptance": "NOT_MEASURED"}
    try:
        manifest, inputs, sources = _prepare(project, context)
        producer, gates = _command_plan(project, step, manifest, attempt)
        records = []
        for index, argv in enumerate([producer, *gates]):
            result["native_started"] = True
            record = _run(argv, attempt / ("command-%02d.log" % index))
            records.append(record)
            if record["returncode"] != 0:
                status = "FAIL" if record["returncode"] == 1 else "NOT_MEASURED"
                raise Refusal("MIXED_ROW_NATIVE_COMMAND_FAILED", str(argv), status=status)
        if _current_inputs(project, step, manifest["top"]) != inputs:
            raise Refusal("MIXED_ROW_INPUT_CHANGED_DURING_EXECUTION", step)
        evidence = _semantic_pass(project, step, manifest)
        outputs = {str(path.relative_to(project)): _sha(path)
                   for path in attempt.rglob("*") if path.is_file()}
        result.update(component_status="PASS", reason="MIXED_ROW_EXISTING_NATIVE_CHAIN_PASS",
                      manifest=manifest, input_sha256=inputs, source_sha256=sources,
                      commands=records, evidence=evidence, output_sha256=outputs)
    except Refusal as exc:
        result.update(component_status=exc.status, reason=exc.code, detail=str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(reason="MIXED_ROW_NATIVE_UNAVAILABLE_OR_UNSUPPORTED", detail=str(exc))
    result["receipt"] = {"path": str((attempt / "component.json").relative_to(project))}
    write_json(attempt / "component.json", result)
    return result


def _completion_base(step, context, code, detail, status="NOT_MEASURED"):
    return {"schema": COMPLETION_SCHEMA, "step_id": step, "default_row_status": status,
            "completed": False, "reason": code, "detail": detail,
            "context_sha256": context.get("context_sha256"),
            "selected_obligations": list(ROWS.get(step, {}).get("obligations", ())),
            "flow109_acceptance": "NOT_MEASURED", "whole_flow_acceptance": "NOT_MEASURED",
            "whole_ic_acceptance": "NOT_MEASURED"}


def consume_default_row_completion(project, context, component):
    project = Path(project).resolve()
    step = str(context.get("requested_step"))
    base = _completion_base(step, context, "MIXED_ROW_COMPLETION_NOT_MEASURED", "not consumed")
    try:
        if step not in ROWS or context.get("native_step_id") != "flow109." + step:
            raise Refusal("MIXED_ROW_COMPLETION_CONTEXT_MISMATCH", step)
        obligations = tuple(item.get("id") for item in
                            (context.get("resolved_row") or {}).get("obligations", ()))
        if obligations != ROWS[step]["obligations"]:
            raise Refusal("MIXED_ROW_COMPLETION_OBLIGATIONS_MISMATCH", str(obligations))
        receipt = component.get("receipt") if isinstance(component, dict) else None
        path = _local(project, receipt.get("path") if isinstance(receipt, dict) else "")
        if (not path.is_file() or path.name != "component.json"
                or not path.is_relative_to(project / "reports/flow109" / step)):
            raise Refusal("MIXED_ROW_COMPLETION_RECEIPT_MISSING", str(path))
        raw = _read(path)
        if raw != component or raw.get("schema") != SCHEMA or raw.get("step_id") != step \
                or raw.get("context_sha256") != context.get("context_sha256"):
            raise Refusal("MIXED_ROW_COMPLETION_RECEIPT_INVALID", str(path))
        if raw.get("component_status") == "FAIL":
            return {**base, "default_row_status": "FAIL", "reason": raw.get("reason"),
                    "component_receipt": {"path": str(path.relative_to(project)), "sha256": _sha(path)}}
        if raw.get("component_status") != "PASS":
            raise Refusal(raw.get("reason") or "MIXED_ROW_COMPLETION_NOT_MEASURED", "component did not pass")
        manifest, inputs, sources = _prepare(project, context)
        if raw.get("manifest") != manifest or raw.get("input_sha256") != inputs \
                or raw.get("source_sha256") != sources:
            raise Refusal("MIXED_ROW_COMPLETION_SOURCE_OR_INPUT_STALE", step)
        for name, expected in (raw.get("output_sha256") or {}).items():
            output = _local(project, name)
            if not output.is_file() or _sha(output) != expected:
                raise Refusal("MIXED_ROW_COMPLETION_EVIDENCE_STALE", name)
        commands = raw.get("commands")
        expected_plan = _command_plan(project, step, manifest, path.parent)
        expected_argv = [expected_plan[0], *expected_plan[1]]
        if (not isinstance(commands, list) or [item.get("argv") for item in commands] != expected_argv
                or any(item.get("returncode") != 0 for item in commands)):
            raise Refusal("MIXED_ROW_COMPLETION_INVOCATION_INVALID", step)
        evidence = _semantic_pass(project, step, manifest)
        if evidence != raw.get("evidence"):
            raise Refusal("MIXED_ROW_COMPLETION_DERIVATION_CHANGED", step)
        return {**base, "default_row_status": "PASS", "completed": True,
                "reason": "MIXED_ROW_SUPPORTED_SCOPE_COMPLETE", "detail": ROWS[step]["scope"],
                "component_receipt": {"path": str(path.relative_to(project)), "sha256": _sha(path)},
                "input_sha256": inputs, "source_sha256": sources, "evidence": evidence}
    except Refusal as exc:
        return {**base, "default_row_status": exc.status, "reason": exc.code, "detail": str(exc)}


__all__ = ["ROWS", "execute", "consume_default_row_completion", "operation_sources"]
