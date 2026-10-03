"""Controller child that runs the existing mixed producer and consumers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from execution_adapters_mixed import (GATE_REPORTS, M2_SIDECAR_OUTPUTS, OUTPUTS,
                                      PRODUCERS)

GATES = {
    "M1": (("mixed_signal_merge_check", "reports/analog/mixed_signal/merge.json"),),
    "M2": (("mixed_signal_power_domain_run", "reports/analog/mixed_signal/power_domain_native_audit.json", "--check-only"),
           ("power_domain_crossing_check", "reports/analog/mixed_signal/power_domain_crossing_audit.json"),
           ("level_shifter_required_check", "reports/analog/mixed_signal/level_shifter_audit.json"),
           ("isolation_cell_required_check", "reports/analog/mixed_signal/isolation_audit.json"),
           ("power_domain_signal_crossing_check", "reports/phase2/gates/power_domain_signal_crossing.json")),
    "M3": (("mixed_signal_cosim_check", "reports/analog/mixed_signal/cosim_audit.json", "--require-current-production"),
           ("mixed_signal_interface_si_check", "reports/analog/mixed_signal/interface_si_audit.json", "--require-current-production")),
    "M4": (("mixed_signal_signoff_check", "reports/analog/mixed_signal/signoff_audit.json", "--require-current-production"),),
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("MIXED_M2_RECEIPT_DUPLICATE_KEY")
        result[key] = value
    return result


def _safe_relative_input(name):
    if (not isinstance(name, str) or not name or "\\" in name or
            Path(name).is_absolute() or Path(name).as_posix() != name or
            any(part in ("", ".", "..") for part in Path(name).parts)):
        raise ValueError("MIXED_M2_INPUT_PATH_UNSAFE")
    return Path(name)


def _bound_regular_file(root, relative):
    root = Path(root).resolve(strict=True)
    relative = _safe_relative_input(str(relative))
    candidate = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("MIXED_M2_INPUT_SYMLINK")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("MIXED_M2_INPUT_UNAVAILABLE") from exc
    if (not resolved.is_relative_to(root) or resolved != candidate or
            not resolved.is_file()):
        raise ValueError("MIXED_M2_INPUT_OUTSIDE_ROOT")
    return resolved


def rebind_m2_receipt_inputs(receipt, staged_project, project_subject, issued_subject,
                             issued_inputs, top_name):
    """Rebase a completed staged M2 receipt only after proving exact issued bytes."""
    if (not isinstance(receipt, dict) or
            receipt.get("schema") != "vibeic.mixed_signal.m2.v1"):
        raise ValueError("MIXED_M2_RECEIPT_INVALID")
    try:
        staged_root = Path(staged_project).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("MIXED_M2_STAGING_ROOT_INVALID") from exc
    subject = Path(project_subject)
    try:
        canonical_root = subject.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("MIXED_M2_SUBJECT_MISMATCH") from exc
    if (project_subject != issued_subject or not subject.is_absolute() or
            str(subject) != str(canonical_root) or
            staged_root == canonical_root or receipt.get("top") != top_name):
        raise ValueError("MIXED_M2_SUBJECT_MISMATCH")
    if not isinstance(issued_inputs, dict) or not issued_inputs:
        raise ValueError("MIXED_M2_ISSUED_INPUTS_INVALID")

    expected = {}
    canonical_paths = {}
    for name, expected_sha in issued_inputs.items():
        relative = _safe_relative_input(name)
        if (not isinstance(expected_sha, str) or
                not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
            raise ValueError("MIXED_M2_ISSUED_DIGEST_INVALID")
        staged_file = _bound_regular_file(staged_root, relative)
        canonical_file = _bound_regular_file(canonical_root, relative)
        if sha(staged_file) != expected_sha or sha(canonical_file) != expected_sha:
            raise ValueError("MIXED_M2_CURRENT_INPUT_DIGEST_MISMATCH")
        expected[relative.as_posix()] = expected_sha
        canonical_paths[relative.as_posix()] = str(canonical_file)

    raw_inputs = receipt.get("inputs")
    if not isinstance(raw_inputs, dict) or not raw_inputs:
        raise ValueError("MIXED_M2_RECEIPT_INPUTS_INVALID")
    staged_receipt_inputs = {}
    for raw_path, digest in raw_inputs.items():
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ValueError("MIXED_M2_RECEIPT_PATH_INVALID")
        path = Path(raw_path)
        try:
            relative = path.relative_to(staged_root)
        except ValueError as exc:
            raise ValueError("MIXED_M2_RECEIPT_PATH_OUTSIDE_STAGING") from exc
        relative = _safe_relative_input(relative.as_posix())
        staged_file = _bound_regular_file(staged_root, relative)
        if str(staged_file) != raw_path:
            raise ValueError("MIXED_M2_RECEIPT_PATH_NONCANONICAL")
        key = relative.as_posix()
        if key in staged_receipt_inputs:
            raise ValueError("MIXED_M2_RECEIPT_DUPLICATE_INPUT")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("MIXED_M2_RECEIPT_DIGEST_INVALID")
        staged_receipt_inputs[key] = digest
    # The issued Controller snapshot can include routing inputs that the M2
    # producer does not consume.  Bind every receipt entry to the issued set,
    # while requiring the receipt's own consumed set to be nonempty and
    # complete for the producer's declared inputs.
    receipt_keys = set(staged_receipt_inputs)
    if not receipt_keys or not receipt_keys <= set(expected):
        raise ValueError("MIXED_M2_RECEIPT_INPUT_SET_MISMATCH")
    for name, digest in staged_receipt_inputs.items():
        if digest != expected[name]:
            raise ValueError("MIXED_M2_RECEIPT_INPUT_SET_MISMATCH")

    liberty_keys = sorted(name for name in staged_receipt_inputs
                          if name.startswith("input/pdk/liberty/") and name.endswith(".lib"))
    if not liberty_keys:
        raise ValueError("MIXED_M2_RECEIPT_LIBERTY_SET_MISMATCH")
    staged_liberties = receipt.get("liberties")
    expected_staged_liberties = [str(_bound_regular_file(staged_root, name))
                                 for name in liberty_keys]
    if (not isinstance(staged_liberties, list) or
            len(staged_liberties) != len(set(staged_liberties)) or
            staged_liberties != expected_staged_liberties):
        raise ValueError("MIXED_M2_RECEIPT_LIBERTY_SET_MISMATCH")

    updated = dict(receipt)
    updated["inputs"] = {canonical_paths[name]: staged_receipt_inputs[name]
                          for name in sorted(staged_receipt_inputs)}
    updated["liberties"] = [canonical_paths[name] for name in liberty_keys]
    return updated


def _load_m2_receipt(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=_unique_json_object)


def validate_m2_producer_output_population(receipt, project):
    """Validate every current output named by the native producer receipt."""
    from mixed_signal_power_domain_run import DIR as producer_dir, OUTPUTS as producer_outputs

    expected = {f"{producer_dir}/{name}" for name in producer_outputs}
    expected.update(M2_SIDECAR_OUTPUTS)
    declared = receipt.get("outputs") if isinstance(receipt, dict) else None
    if not isinstance(declared, dict) or not declared:
        raise ValueError("MIXED_M2_PRODUCER_OUTPUTS_INVALID")
    normalized = {}
    for name, digest in declared.items():
        relative = _safe_relative_input(name)
        if relative.as_posix() != name:
            raise ValueError("MIXED_M2_PRODUCER_OUTPUT_PATH_UNSAFE")
        if (not isinstance(digest, str) or
                not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("MIXED_M2_PRODUCER_OUTPUT_DIGEST_INVALID")
        normalized[name] = digest
    if set(normalized) != expected:
        raise ValueError("MIXED_M2_PRODUCER_OUTPUT_SET_MISMATCH")
    verified = {}
    for name in sorted(expected):
        current = _bound_regular_file(project, name)
        actual = sha(current)
        if actual != normalized[name]:
            raise ValueError("MIXED_M2_PRODUCER_OUTPUT_DIGEST_MISMATCH")
        verified[name] = actual
    return verified


def _copy_project(inputs: Path, project: Path):
    project.mkdir(parents=True, exist_ok=False)
    for source in inputs.rglob("*"):
        rel = source.relative_to(inputs)
        target = project / rel
        if source.is_symlink():
            raise ValueError(f"MIXED_INPUT_SYMLINK:{rel}")
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _run(program, args, project):
    argv = [sys.executable, str(HERE / (program + ".py"))]
    if program == "mixed_signal_power_domain_run" and "--check-only" in args:
        # The M2 current consumer has a project-only interface.  Keep it
        # separate from the producer's --top/--container invocation.
        report = next((token for token in args if token.endswith(".json")), None)
        if report is None:
            raise ValueError("MIXED_M2_CHECK_REPORT_REQUIRED")
        argv += [str(project), "--check-only", "--json", str(project / report)]
    elif program == "mixed_signal_top_lvs_run":
        argv.append(str(project))
        top, container, pdk = args
        if pdk == "auto":
            resolved = project / "phase3/phase3_one_shot.json"
            if resolved.is_file():
                try:
                    pdk = str(json.loads(resolved.read_text()).get("pdk") or pdk)
                except (OSError, ValueError):
                    pass
        argv += ["--top", top, "--container", container, "--pdk", pdk,
                 "--json", str(project / "reports/analog/mixed_signal/top_lvs_run.json")]
    elif program == "mixed_signal_power_domain_run":
        argv.append(str(project))
        top, container = args
        argv += ["--top", top, "--container", container,
                 "--json", str(project / "reports/analog/mixed_signal/power_domain_producer_audit.json")]
    elif program == "mixed_signal_m3_run":
        argv.append(str(project))
        top, container = args
        argv += ["--top", top, "--container", container,
                 "--json", str(project / "reports/analog/mixed_signal/m3_producer_audit.json")]
    elif program == "mixed_signal_signoff_run":
        argv.append(str(project))
        (top,) = args
        argv += ["--top", top,
                 "--json", str(project / "reports/analog/mixed_signal/signoff_producer_audit.json")]
    else:
        argv += [str(project)]
        for token in args:
            if token == "--require-current-production":
                argv.append(token)
            elif token.endswith(".json"):
                argv += ["--json", str(project / token)]
            elif token == "--check-only":
                argv.append(token)
    report_arg = next((argv[i + 1] for i, token in enumerate(argv[:-1])
                       if token == "--json"), None)
    if report_arg:
        report_path = Path(report_arg)
        if not report_path.is_absolute():
            report_path = project / report_path
        if report_path.is_relative_to(project) and report_path.suffix == ".json":
            # A prior producer/gate report is not evidence for this execution.
            try:
                report_path.unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError("MIXED_REPORT_STALE_UNREMOVABLE") from exc
    done = subprocess.run(argv, cwd=project, text=True, capture_output=True)
    report = None
    if report_arg:
        path = project / report_arg
        if path.is_file():
            try:
                report = json.loads(path.read_text())
            except (OSError, ValueError):
                report = None
    if report is None:
        # Producer reports live at fixed current paths and gate reports use
        # the final argument ending in .json.
        candidates = [project / token for token in args if token.endswith(".json")]
        report = next((json.loads(p.read_text()) for p in candidates if p.is_file()), {})
    verdict = report.get("verdict") if isinstance(report, dict) else None
    if verdict is None and isinstance(report, dict) and "passed" in report:
        verdict = ("PASS" if report.get("passed") is True and
                   not report.get("vacuous") and not report.get("skip") else
                   "FAIL" if report.get("passed") is False else "NOT_MEASURED")
    # An old/stale PASS can never override a failed invocation.  Preserve a
    # measured FAIL, otherwise a nonzero process is FAIL only for the
    # producer/gate convention rc=1 and NOT_MEASURED for tool/runtime errors.
    if verdict == "FAIL":
        pass
    elif done.returncode == 0 and verdict == "PASS":
        pass
    elif done.returncode == 1:
        verdict = "FAIL"
    else:
        verdict = "NOT_MEASURED"
    return {"program": program, "argv": argv, "rc": done.returncode,
            "verdict": verdict, "stdout_sha256": hashlib.sha256(done.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(done.stderr.encode()).hexdigest()}


def execute(inputs: Path, outputs: Path, step_id: str, params: dict):
    if step_id not in OUTPUTS or not isinstance(params, dict):
        raise ValueError("MIXED_STEP_OR_PARAMETERS_INVALID")
    raw_binding = os.environ.get("VIBEIC_EXECUTION_BINDING", "")
    try:
        binding = json.loads(raw_binding)
    except ValueError as exc:
        raise ValueError("MIXED_ISSUED_BINDING_INVALID") from exc
    if (not isinstance(binding, dict) or binding.get("step_id") != step_id or
            not isinstance(binding.get("objective"), dict) or any(
                binding["objective"].get(k) != params.get(k)
                for k in ("top_name", "container", "pdk", "project_subject"))):
        raise ValueError("MIXED_ISSUED_SUBJECT_MISMATCH")
    outputs.mkdir(parents=True, exist_ok=True)
    project = outputs / "project"
    _copy_project(inputs, project)
    top = str(params.get("top_name") or "chip_top")
    container = str(params.get("container") or "vibeic-eda")
    pdk = str(params.get("pdk") or "auto")
    producer_args = ((top, container, pdk) if step_id == "M1" else
                     (top, container) if step_id in ("M2", "M3") else (top,))
    try:
        producer = _run(PRODUCERS[step_id], producer_args, project)
    except BaseException as exc:
        producer = {"program": PRODUCERS[step_id], "argv": [], "rc": None,
                    "verdict": "NOT_MEASURED", "detail": f"{type(exc).__name__}: {exc}"}
    producer_path = {
        "M1": "reports/analog/mixed_signal/top_lvs_run.json",
        "M2": "reports/analog/mixed_signal/power_domain_run.json",
        "M3": "reports/analog/mixed_signal/m3_run.json",
        "M4": "reports/analog/mixed_signal/signoff.json",
    }[step_id]
    producer_report = {}
    if (project / producer_path).is_file():
        try:
            producer_report = json.loads((project / producer_path).read_text())
        except (OSError, ValueError):
            pass
    producer_verdict = producer_report.get("verdict", producer.get("verdict", "NOT_MEASURED"))
    if producer_verdict not in ("PASS", "FAIL"):
        producer_verdict = "NOT_MEASURED"
    gate_records = []
    for spec in GATES[step_id]:
        gate, report_path, *flags = spec
        # M2's native check accepts the normal project positional input and
        # an explicit check-only audit path.
        args = list(flags)
        if gate != "mixed_signal_power_domain_run":
            args.append(report_path)
        else:
            args.extend((report_path,))
        try:
            record = _run(gate, tuple(args), project)
        except BaseException as exc:
            record = {"program": gate, "argv": [], "rc": None,
                      "verdict": "NOT_MEASURED",
                      "detail": f"{type(exc).__name__}: {exc}",
                      "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                      "stderr_sha256": hashlib.sha256(str(exc).encode()).hexdigest()}
        gate_records.append({"gate": gate, "verdict": record["verdict"],
                             "rc": record["rc"], "command": record["argv"],
                             "stdout_sha256": record["stdout_sha256"],
                             "stderr_sha256": record["stderr_sha256"]})
    producer_output_hashes = {}
    if step_id == "M2" and producer_verdict == "PASS":
        receipt_path = project / producer_path
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise ValueError("MIXED_M2_PRODUCER_RECEIPT_ABSENT_OR_UNSAFE")
        producer_receipt = _load_m2_receipt(receipt_path)
        if producer_receipt.get("verdict") != producer_verdict:
            raise ValueError("MIXED_M2_PRODUCER_VERDICT_MISMATCH")
        producer_output_hashes = validate_m2_producer_output_population(
            producer_receipt, project)
        rebound = rebind_m2_receipt_inputs(
            producer_receipt, project, params.get("project_subject"),
            binding["objective"].get("project_subject"), binding.get("inputs"), top)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=receipt_path.parent, delete=False) as stream:
            json.dump(rebound, stream, sort_keys=True, indent=2)
            stream.write("\n")
            temp_receipt = Path(stream.name)
        os.replace(temp_receipt, receipt_path)
    output_hashes = {}
    output_rels = set(OUTPUTS[step_id]) | set(GATE_REPORTS[step_id])
    output_rels.update(producer_output_hashes)
    if step_id == "M4":
        try:
            import _mixed_signal_top_pv as top_pv
            output_rels.update(top_pv.outputs(project))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    for rel in sorted(output_rels):
        path = project / rel
        if path.is_file() and not path.is_symlink():
            target = outputs / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            output_hashes[rel] = sha(target)
    receipt = {"schema": "vibeic/mixed-current-result/1", "step_id": step_id,
               "producer": PRODUCERS[step_id], "producer_verdict": producer_verdict,
               "producer_command": producer, "gate_records": gate_records,
               "outputs": output_hashes, "binding": binding,
               "detail": producer_report.get("reason", producer_report.get("detail", ""))}
    (outputs / "mixed-result.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path)
    parser.add_argument("outputs", type=Path)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--params-json", default="{}")
    args = parser.parse_args(argv)
    execute(args.inputs, args.outputs, args.step_id, json.loads(args.params_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
