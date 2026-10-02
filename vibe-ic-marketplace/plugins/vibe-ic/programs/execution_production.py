"""Canonical Step 9 production front door.

Only the public ``execution_modes.Registry``/``Controller`` API is used here.
The adapter launches :mod:`execution_native_worker`, which calls the existing
LibreLane synthesis implementation once.  No Python fixture, handwritten gate
receipt, or caller supplied tool version is a producer.
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Mapping

from _atomic_artefact import write_json
import execution_modes as em
import execution_synthesis_engines as engines

NETLIST = engines.NETLIST
AREA = engines.AREA
STATS = engines.STATS
PRODUCER_RECEIPT = engines.PRODUCER_RECEIPT
NATIVE_COMMANDS = engines.NATIVE_COMMANDS
CANONICAL_NETLIST = engines.CANONICAL_NETLIST
CANONICAL_AREA = engines.CANONICAL_AREA
CANONICAL_STATS = engines.CANONICAL_STATS
CANONICAL_AREA_ANY = engines.CANONICAL_AREA_ANY
REQUIRED_GATES = engines.REQUIRED_GATES

PROGRAMS = Path(__file__).resolve().parent
FLOW = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
PORTFOLIO = PROGRAMS / "data" / "execution_modes_portfolio.json"
WORKER = PROGRAMS / "execution_native_worker.py"
RUNNER = PROGRAMS / "phase3_one_shot_runner.py"


class StepRequest:
    """Small source-bound request used by the normal runner route."""

    def __init__(self, step_id: str, project: Path, parameters: Mapping[str, object],
                 lease: Path | None = None, record: Path | None = None,
                 source_sha: str | None = None, source_files: Mapping[str, str] | None = None):
        self.step_id = str(step_id)
        self.project = Path(project)
        self.parameters = dict(parameters)
        self.lease = Path(lease) if lease is not None else None
        self.record = Path(record) if record is not None else None
        self.source_sha = source_sha or engines.source_sha()
        self.source_files = dict(source_files or source_identity()[1])


class PreparedStep:
    def __init__(self, context: em.Context | None, registry: em.Registry | None,
                 *, disposition: str = "execute", reason: str = ""):
        self.context, self.registry = context, registry
        self.disposition, self.reason = disposition, reason
        self.consume = import_selected
        self.adoption_paths = ("project/phase2/stage2/synth", "producer.json",
                               "native_commands.jsonl")


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _sha(path: Path) -> str:
    return em.digest(path)


def source_identity(extra: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Bind every executable/consumer used by the native worker."""
    files = {}
    for path in (PROGRAMS / "execution_modes.py", PROGRAMS / "execution_synthesis_engines.py",
                 PROGRAMS / "execution_production.py", WORKER, RUNNER,
                 PROGRAMS / "librelane_contract.py", PROGRAMS / "_atomic_artefact.py",
                 FLOW, PORTFOLIO):
        if not _regular(path):
            raise em.Refusal("PRODUCTION_SOURCE_MISSING", str(path))
        files[str(path.resolve())] = _sha(path)
    if extra:
        for name, value in extra.items():
            p = Path(name)
            if (not p.is_absolute() or not _regular(p) or
                    not isinstance(value, str) or _sha(p) != value):
                raise em.Refusal("ADAPTER_SOURCE_MISMATCH", str(p))
            files[str(p.resolve())] = value
    return engines.source_sha(), files


def _tool_and_image(image: object) -> tuple[str, dict, list[str]]:
    """Measure image identity; labels and caller self-reports are rejected."""
    missing = []
    if image is None or not str(image).strip():
        missing.append("image")
        return "UNMEASURED:image", {}, missing
    image_text = str(image)
    if not image_text.startswith("sha256:") or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_text):
        missing.append("image_id")
        return "UNMEASURED:image_id", {}, missing
    try:
        import librelane_image_facts as facts
        measured = facts.image_facts(image_text)
    except Exception as exc:  # absent Docker/EDA is an honest NM arm
        return "UNMEASURED:image_facts", {}, ["image_facts:" + type(exc).__name__]
    if not isinstance(measured, dict) or measured.get("image_id") != image_text:
        missing.append("measured_image_id")
    if not measured.get("librelane_version"):
        missing.append("librelane_version")
    return (image_text if not missing else "UNMEASURED:" + ",".join(missing),
            measured if isinstance(measured, dict) else {}, missing)


def _pdk_dict(pdk: object) -> dict:
    if is_dataclass(pdk) and not isinstance(pdk, type):
        values = asdict(pdk)
    elif isinstance(pdk, Mapping):
        values = dict(pdk)
    else:
        values = {}
        for name in ("name", "liberty", "tech_lef", "cell_lef", "cell_gds", "site", "drc_deck"):
            if hasattr(pdk, name):
                values[name] = getattr(pdk, name)
    def jsonable(value):
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, list):
            return [jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): jsonable(item) for key, item in value.items()}
        return value
    return jsonable(values)


def _pdk_liberty(pdk: object) -> Path | None:
    value = _pdk_dict(pdk).get("liberty")
    if not value:
        return None
    path = Path(str(value))
    return path if _regular(path) else None


def _canonical_inputs(project: Path, declaration: Path | None, pdk: object) -> dict[str, Path]:
    declaration = declaration or project / "input/submission_template/tapeout_declaration.json"
    if not _regular(declaration):
        raise em.Refusal("PRODUCTION_DECLARATION_MISSING", str(declaration))
    rtl_root = project / "phase2/stage1/rtl"
    if rtl_root.is_symlink() or not rtl_root.is_dir():
        raise em.Refusal("PRODUCTION_RTL_INPUT_MISSING", str(rtl_root))
    rtl = sorted(p for p in rtl_root.rglob("*") if _regular(p))
    if not rtl:
        raise em.Refusal("PRODUCTION_RTL_INPUT_MISSING", str(rtl_root))
    inputs = {"project/input/submission_template/tapeout_declaration.json": declaration}
    for path in rtl:
        rel = path.relative_to(rtl_root).as_posix()
        inputs["project/phase2/stage1/rtl/" + rel] = path
    liberty = _pdk_liberty(pdk)
    if liberty is None:
        raise em.Refusal("PRODUCTION_PDK_LIBERTY_MISSING", str(pdk))
    inputs["pdk/" + liberty.name] = liberty
    return inputs


def _request_spec(*, project: Path, top: str, pdk: object, image_id: str,
                  source_sha: str, source_files: Mapping[str, str],
                  inputs: Mapping[str, Path], lease: Path | None) -> dict:
    return {
        "schema": 1, "step_id": "9", "top": top,
        "original_project": str(project.resolve()),
        "pdk": _pdk_dict(pdk), "image_id": image_id,
        "source_sha": source_sha, "source_files": dict(source_files),
        "synthesis_engine": engines.LIBRELANE.contract(),
        "input_hashes": {name: _sha(path) for name, path in inputs.items()},
        "lease": str(lease.resolve()) if lease else None,
    }


def _native_adapter(context: em.Context, source_sha: str, source_files: Mapping[str, str],
                    image_id: str, facts: dict, spec_path: Path, pdk: object,
                    tool: object = None,
                    *, include_same_family_wrapper: bool = False) -> em.Adapter:
    objective = dict(context.objective)
    binary = Path(sys.executable).resolve()
    source_files = dict(source_files)
    source_files.setdefault(str(binary), _sha(binary))
    missing = []
    if not image_id.startswith("sha256:"):
        missing.append("image_id")
    chip_steps = (facts.get("flows", {}).get("Chip", [])
                  if isinstance(facts, dict) and isinstance(facts.get("flows"), dict)
                  else [])
    if image_id.startswith("sha256:") and not set(engines.LIBRELANE.native_steps).issubset(chip_steps):
        missing.append("native_steps")
    if not _regular(binary):
        missing.append("tool")
    if tool is not None:
        try:
            requested = Path(str(tool)).resolve()
        except (OSError, RuntimeError):
            requested = Path("/") / "missing-step9-tool"
        if not _regular(requested) or requested != binary:
            missing.append("tool_identity")
    if not spec_path.is_file():
        missing.append("request")
    if not context.inputs:
        missing.append("input")
    names = set(context.inputs)
    if "request.json" not in names:
        missing.append("input:request.json")
    if not any(name == "project/input/submission_template/tapeout_declaration.json" for name in names):
        missing.append("input:tapeout_declaration.json")
    if not any(name.startswith("project/phase2/stage1/rtl/") for name in names):
        missing.append("input:rtl")
    if not any(name.startswith("pdk/") for name in names):
        missing.append("input:pdk")
    allowed = ("request.json", "pdk/", "project/input/submission_template/tapeout_declaration.json",
               "project/phase2/stage1/rtl/")
    if any(not (name == allowed[0] or name.startswith(allowed[1]) or name == allowed[2]
                or name.startswith(allowed[3])) for name in names):
        missing.append("input:noncanonical")
    reason = "" if not missing else "MISSING_NATIVE_FACTS:" + ",".join(missing)
    adapter = em.Adapter(
        arm_id=engines.LIBRELANE.arm_id, tool_id=engines.LIBRELANE.tool_id,
        step_id="9", source_sha=source_sha, source_files=source_files,
        # This is measured image identity, never the caller's label/version.
        tool_version=image_id if image_id.startswith("sha256:") else "UNMEASURED:" + reason,
        engine_families=engines.LIBRELANE.engine_families,
        components=(em.Component("native-mapped-synthesis",
            (str(binary), str(WORKER), "--inputs", "{inputs}", "--outputs", "{outputs}"),
            timeout_s=7200),), validate=validate_synthesis,
        required_outputs=(NETLIST, PRODUCER_RECEIPT, NATIVE_COMMANDS),
        objective=objective,
        qualification_evidence=json.dumps({"engine": engines.LIBRELANE.contract(),
                                            "image_facts": facts}, sort_keys=True),
        available=not missing, availability_reason=reason,
        cpus=1, ram_mb=128,
        output_contract={CANONICAL_NETLIST: (NETLIST,),
                         CANONICAL_AREA_ANY: (NETLIST,)},
    )
    if not include_same_family_wrapper:
        return adapter
    # Ultra may disclose the wrapper so Controller's family deduplication is
    # exercised, but it cannot claim independent engine credit.
    return adapter


def build_registry(*, context: em.Context, image: object = None, tool: object = None,
                   inputs: Mapping[str, Path] | None = None, fixture: bool = False,
                   include_same_family_wrapper: bool = False,
                   request_path: Path | None = None, pdk: object = None) -> em.Registry:
    """Build the only source-owned provider; fixture arguments are rejected."""
    if inputs is not None and dict(inputs) != dict(context.inputs):
        raise em.Refusal("PRODUCTION_INPUT_POPULATION_CHANGED", "context/input mismatch")
    source, files = source_identity()
    facts = {}
    image_id, facts, missing = _tool_and_image(image)
    if fixture:
        missing = ["synthetic_fixture_forbidden"]
        image_id = "UNMEASURED:synthetic_fixture_forbidden"
    spec = request_path or Path("/definitely/missing/step9-request.json")
    registry = em.Registry()
    adapter = _native_adapter(context, source, files, image_id, facts, spec, pdk, tool,
                              include_same_family_wrapper=include_same_family_wrapper)
    registry.register(adapter)
    return registry


def _json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _gate_verdict(rc: int | None, report: dict | None) -> str:
    if rc == 1:
        return "FAIL"
    if rc != 0:
        return "NOT_MEASURED"
    if not isinstance(report, dict):
        return "NOT_MEASURED"
    verdict = report.get("verdict")
    if verdict in ("PASS", "FAIL"):
        return verdict
    if report.get("summary", {}).get("pass") is True:
        return "PASS"
    if report.get("summary", {}).get("pass") is False:
        return "FAIL"
    return "NOT_MEASURED"


def _run_gate(name: str, argv: list[str], directory: Path) -> tuple[str, Path]:
    script = PROGRAMS / (name + ".py")
    report = directory / (name + ".json")
    if not _regular(script):
        return "NOT_MEASURED", report
    command = [str(Path(sys.executable).resolve()), str(script), *argv,
               "--json", str(report)]
    try:
        cp = subprocess.run(command, cwd=directory, capture_output=True,
                            text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return "NOT_MEASURED", report
    return _gate_verdict(cp.returncode, _json(report)), report


def _output_hashes(outputs: Path) -> dict[str, str]:
    result = {}
    for path in outputs.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(outputs).as_posix()
            if (rel in (PRODUCER_RECEIPT, NATIVE_COMMANDS)
                    or rel.startswith("project/phase2/stage2/synth/")
                    or rel.startswith("project/phase3/")
                    or rel.startswith("project/provenance.jsonl")
                    or rel.startswith("validations/")):
                result[rel] = _sha(path)
    return result


def validate_synthesis(outputs: Path, binding: Mapping[str, object]) -> em.Evidence:
    """Freshly consume native outputs and gates; never trust a receipt string."""
    outputs = Path(outputs).resolve()
    gates = {name: "NOT_MEASURED" for name in binding.get("required_gates", REQUIRED_GATES)}
    producer_path = outputs / PRODUCER_RECEIPT
    commands_path = outputs / NATIVE_COMMANDS
    producer = _json(producer_path)
    hashes = _output_hashes(outputs)
    if producer is None or not commands_path.is_file():
        return em.Evidence(binding, "NOT_MEASURED", gates, hashes,
                           detail="native producer receipt/command journal missing")
    if producer.get("binding") != dict(binding):
        raise em.Refusal("PRODUCTION_WORKER_BINDING_UNBOUND", str(producer_path))
    status = producer.get("status")
    if status == "FAIL":
        return em.Evidence(binding, "FAIL", gates, hashes,
                           detail=producer.get("detail", "native producer measured FAIL"))
    if status != "PASS":
        return em.Evidence(binding, "NOT_MEASURED", gates, hashes,
                           detail=producer.get("detail", "native producer not measured"))
    if not engines.native_entrypoint_is_real(producer.get("native_entrypoint")):
        raise em.Refusal("PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED", "native_entrypoint")
    spec_path = outputs.parent / "inputs" / "request.json"
    spec = _json(spec_path)
    if spec is None:
        raise em.Refusal("PRODUCTION_REQUEST_MISSING", str(spec_path))
    if not isinstance(spec.get("image_id"), str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", spec["image_id"]):
        raise em.Refusal("PRODUCTION_IMAGE_UNBOUND", str(spec.get("image_id")))
    engine = engines.consume_producer(spec, producer)
    if producer.get("source_sha") != binding.get("source_sha") or spec.get("source_sha") != binding.get("source_sha"):
        raise em.Refusal("PRODUCTION_WORKER_SOURCE_UNBOUND", str(producer_path))
    if producer.get("source_files") != spec.get("source_files"):
        raise em.Refusal("PRODUCTION_WORKER_SOURCE_UNBOUND", "source_files")
    for name, expected in spec.get("source_files", {}).items():
        path = Path(name)
        if not _regular(path) or _sha(path) != expected:
            raise em.Refusal("ADAPTER_SOURCE_MISMATCH", name)
    expected_inputs = {k: v for k, v in binding.get("inputs", {}).items() if k != "request.json"}
    if producer.get("input_hashes") != expected_inputs:
        raise em.Refusal("PRODUCTION_WORKER_INPUT_MANIFEST_UNBOUND", str(producer_path))
    command_rows = []
    try:
        for line in commands_path.read_text().splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("command row")
            command_rows.append(row)
    except (OSError, ValueError, TypeError) as exc:
        raise em.Refusal("PRODUCTION_NATIVE_COMMAND_JOURNAL_INVALID", str(exc)) from exc
    if not any(row.get("native_entrypoint") == engine.native_entrypoint
               and row.get("image_id") == spec.get("image_id")
               and row.get("executed") is True for row in command_rows):
        raise em.Refusal("PRODUCTION_NATIVE_INVOCATION_UNPROVEN", str(commands_path))
    project = outputs / "project"
    if project.is_symlink() or not project.is_dir():
        raise em.Refusal("PRODUCTION_PROJECT_OUTPUT_MISSING", str(project))
    # The worker's writable project is a derived view. Rebind every canonical
    # declaration/RTL byte to the Controller-frozen input before any gate can
    # read it; a co-mutated output cannot launder a stale valid receipt.
    for name, expected in expected_inputs.items():
        if name.startswith("project/"):
            actual = project / name.removeprefix("project/")
        elif name.startswith("pdk/"):
            actual = outputs / name
        else:
            continue
        if not _regular(actual) or _sha(actual) != expected:
            raise em.Refusal("PRODUCTION_DERIVED_INPUT_CHANGED", name)
    declaration = project / "input/submission_template/tapeout_declaration.json"
    rtl_root = project / "phase2/stage1/rtl"
    netlist = project / CANONICAL_NETLIST
    area = project / CANONICAL_AREA
    stats = project / CANONICAL_STATS
    if not _regular(declaration) or not rtl_root.is_dir() or not any(_regular(p) for p in rtl_root.rglob("*")):
        raise em.Refusal("PRODUCTION_CANONICAL_INPUT_MISSING", str(project))
    if not _regular(netlist) or not netlist.read_text(errors="replace").strip():
        return em.Evidence(binding, "NOT_MEASURED", gates, hashes,
                           detail="native mapped netlist missing or empty")
    native_path = Path(str(producer.get("native_tool_netlist", "")))
    if (not _regular(native_path) or native_path.resolve() == netlist.resolve()
            or not native_path.resolve().is_relative_to(project)):
        raise em.Refusal("PRODUCTION_NATIVE_NETLIST_UNBOUND", str(native_path))
    area_valid = (_regular(area) and bool(re.search(
        r"(?:area|Area|AREA)[^0-9]*(?:[0-9]+(?:\.[0-9]+)?|[0-9]+e[+-]?[0-9]+)",
        area.read_text(errors="replace"))))
    stat = _json(stats)
    stats_valid = (isinstance(stat, dict)
                   and stat.get("schema") == "vibe-ic/synth-stats/1"
                   and type(stat.get("chip_area")) in (int, float)
                   and math.isfinite(stat["chip_area"])
                   and stat.get("netlist") == CANONICAL_NETLIST
                   and stat.get("netlist_digest") == _sha(netlist))
    if not (area_valid or stats_valid):
        return em.Evidence(binding, "NOT_MEASURED", gates, hashes,
                           detail="canonical area.rpt OR stats.json missing")
    native_tool_netlist = producer.get("native_tool_netlist")
    validation_dir = outputs / "validations"
    validation_dir.mkdir(exist_ok=True)
    rtl = [str(p) for p in sorted(rtl_root.rglob("*")) if _regular(p)]
    gates["synth_netlist_check"], _ = _run_gate(
        "synth_netlist_check", ["--netlist", str(netlist),
                                 "--tool-netlist", str(native_path),
                                 "--rtl", *rtl], validation_dir)
    liberty = spec.get("pdk", {}).get("liberty")
    if liberty:
        pdk_lib = Path(str(liberty))
        if not pdk_lib.is_file():
            pdk_lib = outputs / "pdk" / pdk_lib.name
    else:
        pdk_lib = outputs / "pdk" / "missing.lib"
    area_gate_argv = [str(project)]
    if pdk_lib.is_file():
        area_gate_argv += ["--library", str(pdk_lib)]
    gates["area_total_vs_budget_check"], _ = _run_gate(
        "area_total_vs_budget_check", area_gate_argv, validation_dir)
    gates["pdk_consistency_check"], _ = _run_gate(
        "pdk_consistency_check", ["--netlist", str(netlist), "--pdk-lib", str(pdk_lib)], validation_dir)
    gates["provenance_check"], _ = _run_gate(
        "provenance_check", [str(project), "--output", CANONICAL_NETLIST,
                              "--tool", engine.provenance_tool, "--require-measured"], validation_dir)
    hashes = _output_hashes(outputs)
    if "FAIL" in gates.values():
        verdict = "FAIL"
    elif all(gates.get(name) == "PASS" for name in REQUIRED_GATES):
        verdict = "PASS"
    else:
        verdict = "NOT_MEASURED"
    metrics = {}
    if isinstance(stat, dict) and type(stat.get("chip_area")) in (int, float) and math.isfinite(stat["chip_area"]):
        metrics["mapped_area_um2"] = stat["chip_area"]
    return em.Evidence(binding, verdict, gates, hashes, metrics,
                       detail="fresh canonical Step9 gate consumption")


def _make_context(project: Path, inputs: Mapping[str, Path], source: str,
                  source_files: Mapping[str, str], top: str) -> em.Context:
    objective = {"metric": "mapped_area_um2", "direction": "min", "top": top}
    return em.Context("9", source, inputs, objective, engines.REQUIRED_GATES,
                      native_mode="librelane")


def prepare(project: Path | StepRequest, top: str | None = None, pdk=None,
            container: str | None = None, *, image: str | None = None,
            declaration: Path | None = None, source_binding=None,
            record: Path | None = None) -> PreparedStep:
    if isinstance(project, StepRequest) or (
            hasattr(project, "step_id") and hasattr(project, "parameters")
            and hasattr(project, "source_sha") and hasattr(project, "source_files")):
        req = project
        params = req.parameters
        return prepare(req.project, str(params.get("top", "")), params.get("pdk"),
                       str(params.get("container", "")), image=params.get("image"),
                       declaration=Path(params["declaration"]) if params.get("declaration") else None,
                       source_binding=(req.source_sha, req.source_files), record=req.record)
    project = Path(project).resolve()
    if not top or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", str(top)):
        raise em.Refusal("PRODUCTION_TOP_PATH_UNSAFE", repr(top))
    actual_source, sources = source_identity()
    if source_binding:
        supplied_sha, supplied_files = source_binding
        if supplied_sha != actual_source or dict(supplied_files) != sources:
            raise em.Refusal("PRODUCTION_SOURCE_POPULATION_CHANGED", "9")
    inputs = _canonical_inputs(project, declaration, pdk)
    image_arg = image or container
    image_id, facts, image_missing = _tool_and_image(image_arg)
    record = Path(record) if record else Path(tempfile.mkstemp(prefix="step9-request-", suffix=".json")[1])
    spec = _request_spec(project=project, top=str(top), pdk=pdk, image_id=image_id,
                         source_sha=actual_source, source_files=sources,
                         inputs=inputs, lease=None)
    write_json(record, spec, sort_keys=True)
    inputs = dict(inputs)
    inputs["request.json"] = record
    context = _make_context(project, inputs, actual_source, sources, str(top))
    registry = em.Registry()
    adapter = _native_adapter(context, actual_source, sources, image_id, facts, record, pdk,
                              sys.executable)
    registry.register(adapter)
    return PreparedStep(context, registry)


def import_selected(project: Path, context: em.Context, controller: em.Controller,
                    run: Path, adoption: dict) -> dict:
    """Copy only the Controller-issued generation into canonical project paths."""
    del context, controller
    generation = adoption.get("selected_generation") or {}
    directory = Path(generation.get("directory", ""))
    if directory.is_symlink() or not directory.is_dir():
        raise em.Refusal("SELECTED_GENERATION_MISSING", str(directory))
    copied = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        rel = path.relative_to(directory).as_posix()
        if not rel.startswith("project/"):
            continue
        target = Path(project) / rel.removeprefix("project/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied[str(target.relative_to(project))] = em.digest(target)
    if not copied:
        raise em.Refusal("SELECTED_GENERATION_EMPTY", str(directory))
    return {"status": "IMPORTED", "step_id": "9", "copied": copied,
            "run": str(run), "adoption": adoption}


def dispatch_site(step_ids: tuple[str, ...], project: Path, parameters: Mapping[str, object]) -> dict | None:
    """Normal Step9 runner/CAPTURE entrypoint; Controller owns admission/adoption."""
    if "9" not in tuple(map(str, step_ids)):
        return None
    project = Path(project).resolve()
    declaration = Path(parameters.get("declaration", project / "input/submission_template/tapeout_declaration.json"))
    rtl = project / "phase2/stage1/rtl"
    # Legacy/unit runner calls without the canonical Step9 input continue to
    # their old direct path. They cannot create a production provider.
    if not _regular(declaration) or not rtl.is_dir() or not any(_regular(p) for p in rtl.rglob("*")):
        return None
    started = time.time()
    parent = project / "phase3/execution_modes/step9"
    parent.mkdir(parents=True, exist_ok=True)
    record = parent / "request.json"
    run = parent / "controller-run"
    if run.exists():
        shutil.rmtree(run)
    try:
        prepared = prepare(project, str(parameters.get("top", "")), parameters.get("pdk"),
                           str(parameters.get("container", "")),
                           image=parameters.get("image"), declaration=declaration, record=record)
        controller = em.Controller(prepared.registry, em.Budget(cpus=1, ram_mb=128))
        summary = controller.run(prepared.context, run, "default-mode")
        if not summary.get("arms") and summary.get("status") == "NOT_MEASURED":
            return {"status": "NOT_MEASURED", "duration_s": time.time() - started,
                    "detail": summary.get("reason", "no runnable native provider"),
                    "output_files": [], "reason_class": "tool_absent"}
        arm_id = engines.LIBRELANE.arm_id
        receipt_path = run / arm_id / "receipt.json"
        receipt = _json(receipt_path) or {}
        if receipt.get("status") != "ELIGIBLE":
            return {"status": receipt.get("status", "NOT_MEASURED"),
                    "duration_s": time.time() - started,
                    "detail": receipt.get("detail", receipt.get("reason", "native provider refused")),
                    "output_files": [], "reason_class": receipt.get("reason", "")}
        choice = {"arm_id": arm_id, "binding": prepared.context.binding(),
                  "receipt_sha256": em.digest(receipt_path),
                  "reviewer": "source-owned Step9 production route",
                  "rationale": "fixed canonical Step9 LibreLane producer; no AI reordering"}
        adoption = controller.adopt(prepared.context, run, choice)
        imported = import_selected(project, prepared.context, controller, run, adoption)
        files = list(imported["copied"])
        return {"status": "PASS", "duration_s": time.time() - started,
                "detail": "Controller READY -> ELIGIBLE -> ADOPTED -> IMPORTED",
                "output_files": files, "extras": {"controller_run": str(run),
                    "adoption": str(run / "adoption.json"), "receipt": str(receipt_path)},
                "reason_class": ""}
    except em.Refusal as exc:
        return {"status": "NOT_MEASURED", "duration_s": time.time() - started,
                "detail": str(exc), "output_files": [], "reason_class": exc.code}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "NOT_MEASURED", "duration_s": time.time() - started,
                "detail": repr(exc), "output_files": [], "reason_class": "PRODUCTION_ADAPTER_ERROR"}


STEP_IDS = ("9",)
