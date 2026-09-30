"""Bounded structural applicability for the three RTL-review audit producers.

Not a gate, a waiver, or a semantic classifier. A producer may establish an
empty subject only after a complete, closed RTL census and checked elaboration.
The consumer independently selects the source root and recomputes the proof.
Unresolved inputs, parameters/generate branches, preprocessing, unsupported
cells and external multi-bit data roles remain UNKNOWN. Names are never an
absence criterion. NOT_APPLICABLE is deliberately not PASS.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from _structural_absence import absence

SCHEMA = "vibeic.rtl_audit_applicability.v1"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNKNOWN = "UNKNOWN"
AUDITORS = frozenset({"interface_encoding_audit", "phy_counter_audit",
                      "crc_bitorder_check"})
SOURCE_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh"})
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_STATE_CELLS = frozenset({"$dff", "$adff", "$sdff", "$dffe", "$adffe",
                          "$sdffe", "$sdffce", "$dlatch", "$adlatch"})
_COMB_CELLS = frozenset({
    "$pos", "$not", "$and", "$or", "$xor", "$xnor", "$reduce_and",
    "$reduce_or", "$reduce_xor", "$reduce_xnor", "$reduce_bool",
    "$logic_not", "$logic_and", "$logic_or", "$shl", "$shr", "$sshl",
    "$sshr", "$shift", "$shiftx", "$lt", "$le", "$eq", "$ne", "$eqx",
    "$nex", "$ge", "$gt", "$add", "$sub", "$mul", "$div", "$mod",
    "$pow", "$neg", "$mux", "$pmux", "$demux", "$bmux", "$slice",
    "$concat",
})


def _scope_text(text: str) -> str:
    """Lexically blank comments/strings; never silently swallow malformed text."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated_comment")
            out.append(" ")
            i = end + 2
        elif text[i] == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            if i >= len(text):
                raise ValueError("unterminated_string")
            out.append(" ")
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _census(root: Path) -> tuple[list[Path], list[dict[str, Any]], list[str]]:
    root = root.absolute()
    if not root.is_dir() or root.resolve() != root:
        raise ValueError("source_root_missing_or_symlinked")
    files: list[Path] = []
    records: list[dict[str, Any]] = []
    texts: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink_in_source_census")
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError("source_not_regular_or_symlinked")
        raw = path.read_bytes()
        if len(raw) > _MAX_SOURCE_BYTES:
            raise ValueError("source_too_large")
        texts.append(_scope_text(raw.decode("utf-8", errors="strict")))
        files.append(path)
        records.append({"path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw)})
    if not files:
        raise ValueError("empty_source_census")
    return files, records, texts


def _quoted(path: Path) -> str:
    # The Yosys command language is not a shell, but still has command syntax.
    if any(c in str(path) for c in ('"', "\\", ";", "\n", "\r")):
        raise ValueError("unsupported_source_path")
    return '"' + str(path) + '"'


def _elaborate(files: list[Path], *, source_sha256: dict[str, str] | None = None) -> dict[str, Any]:
    executable = shutil.which("yosys")
    if executable is None:
        raise ValueError("elaborator_unavailable")
    with tempfile.TemporaryDirectory(prefix="rtl-audit-applicability-") as tmp:
        # Elaborate byte-bound immutable copies. Re-opening live source paths
        # in the tool would not prove that the census hashes describe the
        # input actually elaborated, even with a later directory recheck.
        snapshots = []
        for index, path in enumerate(files):
            _quoted(path)  # Keep the declared source-path support boundary.
            raw = path.read_bytes()
            if source_sha256 is not None and hashlib.sha256(raw).hexdigest() != source_sha256.get(str(path)):
                raise ValueError("source_changed_before_elaboration")
            snapshot = Path(tmp) / f"source_{index}.sv"
            snapshot.write_bytes(raw)
            snapshots.append(snapshot)
        output = Path(tmp) / "design.json"
        script = ("read_verilog -sv " + " ".join(_quoted(p) for p in snapshots)
                  + "; hierarchy -check; proc; write_json " + _quoted(output))
        result = subprocess.run([executable, "-Q", "-T", "-p", script],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not output.is_file():
            raise ValueError("elaboration_failed")
        payload = json.loads(output.read_text())
    if not isinstance(payload.get("modules"), dict) or not payload["modules"]:
        raise ValueError("empty_elaborated_module_census")
    return payload


def _module_facts(modules: dict[str, Any]) -> dict[str, Any]:
    """Enumerate every emitted module/cell, keeping unknowns blocking."""
    children: set[str] = set()
    child_count = 0
    wide = 0
    for module in modules.values():
        attrs = module.get("attributes", {})
        if any(int(str(attrs.get(k, "0")), 2) for k in ("blackbox", "whitebox")):
            raise ValueError("black_box_module")
        if module.get("memories"):
            raise ValueError("unsupported_memory")
        wide += sum(len(n.get("bits", [])) > 1
                    for n in module.get("netnames", {}).values())
        wide += sum(len(p.get("bits", [])) > 1
                    for p in module.get("ports", {}).values())
        for cell in module.get("cells", {}).values():
            kind = cell.get("type")
            if kind in modules:
                child_count += 1
                children.add(kind)
            elif kind not in _STATE_CELLS | _COMB_CELLS:
                raise ValueError("unsupported_or_unresolved_cell")
    roots = sorted(set(modules) - children)
    if len(roots) != 1:
        raise ValueError("ambiguous_or_recursive_hierarchy")
    return {"modules": sorted(modules), "top_module": roots[0],
            "child_instances": child_count, "wide_data_nets_and_ports": wide}


def _has_state_recurrence(module: dict[str, Any]) -> bool:
    """Non-identity state feedback, including mux selectors/case encodings.

    Only a literal bit's hold path through mux data inputs is ignored. All
    selectors, clock/reset/enable controls and transformed data retain their
    dependencies. A renamed counter, case-encoded counter, shift recurrence,
    or cycle through two registers therefore cannot acquire an absence proof.
    """
    cells = module.get("cells", {})
    state = {name: c for name, c in cells.items() if c["type"] in _STATE_CELLS}
    owners: dict[int, str] = {}
    drivers: dict[int, tuple[dict[str, Any], str, int]] = {}
    for name, cell in cells.items():
        for port, direction in cell.get("port_directions", {}).items():
            if direction != "output":
                continue
            for offset, bit in enumerate(cell["connections"][port]):
                if not isinstance(bit, int):
                    continue
                if bit in drivers or bit in owners:
                    raise ValueError("multiple_bit_drivers")
                if name in state:
                    if port != "Q":
                        raise ValueError("unsupported_state_output")
                    owners[bit] = name
                else:
                    drivers[bit] = (cell, port, offset)

    def origins(bit: Any, hold: int | None, transformed: bool,
                visiting: frozenset[int] = frozenset()) -> set[str]:
        if not isinstance(bit, int):
            return set()
        if bit in owners:
            return set() if bit == hold and not transformed else {owners[bit]}
        if bit not in drivers:
            return set()
        if bit in visiting:
            raise ValueError("combinational_cycle")
        cell, port, offset = drivers[bit]
        conns = cell["connections"]
        deps: set[str] = set()
        if cell["type"] in ("$mux", "$pmux") and port == "Y":
            width = len(conns["Y"])
            for input_port in ("A", "B"):
                for pos in range(offset, len(conns[input_port]), width):
                    deps |= origins(conns[input_port][pos], hold, transformed,
                                    visiting | {bit})
            for selector in conns["S"]:
                deps |= origins(selector, hold, True, visiting | {bit})
        else:
            for input_port, direction in cell["port_directions"].items():
                if direction == "input":
                    for source in conns[input_port]:
                        deps |= origins(source, hold, True, visiting | {bit})
        return deps

    graph: dict[str, set[str]] = {}
    for name, cell in state.items():
        conns = cell["connections"]
        if len(conns.get("D", [])) != len(conns["Q"]):
            raise ValueError("unsupported_state_data_shape")
        deps: set[str] = set()
        for data, held in zip(conns["D"], conns["Q"]):
            deps |= origins(data, held, False)
        for port, direction in cell["port_directions"].items():
            if direction == "input" and port != "D":
                for control in conns[port]:
                    deps |= origins(control, None, True)
        graph[name] = deps

    def cycle(node: str, active: frozenset[str]) -> bool:
        if node in active:
            return True
        return any(cycle(n, active | {node}) for n in graph[node])

    return any(cycle(n, frozenset()) for n in graph)


def assess(auditor: str, root: Path, *, files: Iterable[Path] | None = None,
           top_module: str | None = None) -> dict[str, Any]:
    """Build a reproducible absence claim or an explicit blocking UNKNOWN."""
    result: dict[str, Any] = {"schema": SCHEMA, "auditor": auditor,
                             "state": UNKNOWN}
    if auditor not in AUDITORS:
        result["reason"] = "unsupported_auditor"
        return result
    try:
        root = Path(root).absolute()
        sources, records, texts = _census(root)
        if files is not None and sorted(Path(p).absolute() for p in files) != sources:
            raise ValueError("source_set_incomplete")
        result["source"] = {"root": str(root), "files": records,
                            "sha256": hashlib.sha256(json.dumps(
                                records, sort_keys=True).encode()).hexdigest()}
        # Never turn default-configuration elision into a universal absence.
        if any(re.search(r"`|\\|\b(?:parameter|localparam|generate)\b", t)
               for t in texts):
            raise ValueError("unsupported_configuration_or_preprocessing")
        # Frontends can drop an unused unpacked memory before writing JSON.
        # Its absence there is not a complete census of the source structure.
        if any(re.search(r"\b(?:reg|logic|wire)\s+(?:signed\s+)?"
                         r"(?:\[[^\]]+\]\s*)?[A-Za-z_]\w*\s*\[", t)
               or re.search(r"\]\s*[A-Za-z_]\w*\s*\[", t) for t in texts):
            raise ValueError("unsupported_memory")
        modules = _elaborate(sources, source_sha256={
            str(path): record["sha256"] for path, record in zip(sources, records)
        })["modules"]
        # The tool read immutable snapshots of the census bytes. Reject live
        # source or membership changes while it ran as well, so the resulting
        # claim also describes the directory the consumer will remeasure.
        after_sources, after_records, _ = _census(root)
        if after_sources != sources or after_records != records:
            raise ValueError("source_changed_during_elaboration")
        facts = _module_facts(modules)
        if top_module is not None and top_module != facts["top_module"]:
            raise ValueError("declared_top_mismatch")
        result["facts"] = facts
        if auditor == "interface_encoding_audit":
            present = facts["child_instances"] != 0
            criterion = "closed_hierarchy_has_no_child_instances"
        elif auditor == "phy_counter_audit":
            # Child connections are not yet expanded into the state graph.
            if facts["child_instances"]:
                raise ValueError("unsupported_cross_module_state_graph")
            present = any(_has_state_recurrence(m) for m in modules.values())
            criterion = "closed_state_graph_has_no_nonidentity_recurrence"
        else:
            # Multi-bit external capture is intentionally UNKNOWN: its role
            # could be CRC data, even when no identifier contains 'crc'.
            present = facts["wide_data_nets_and_ports"] != 0
            criterion = "closed_scalar_design_has_no_multibit_crc_order_subject"
        if present:
            raise ValueError("subject_present_or_role_unknown")
        result["criterion"] = criterion
        result["structural_absence"] = absence(
            "elaborated source modules", len(modules), names=sorted(modules),
            detail=criterion)
        result["state"] = NOT_APPLICABLE
    except (OSError, ValueError, UnicodeError, KeyError, TypeError,
            subprocess.TimeoutExpired, RecursionError) as exc:
        result["reason"] = (str(exc) if isinstance(exc, ValueError)
                            else type(exc).__name__)
    return result


def source_root(files: Iterable[Path]) -> Path:
    """One explicit source directory, not an unbounded common-path guess."""
    parents = {Path(p).absolute().parent for p in files}
    if len(parents) != 1:
        raise ValueError("source_files_require_one_closed_directory")
    return next(iter(parents))


def from_files(auditor: str, files: Iterable[Path]) -> dict[str, Any]:
    selected = [Path(p) for p in files]
    try:
        return assess(auditor, source_root(selected), files=selected)
    except ValueError as exc:
        return {"schema": SCHEMA, "auditor": auditor, "state": UNKNOWN,
                "reason": str(exc)}


def verify(claim: Any, auditor: str, root: Path | None,
           *, top_module: str | None = None) -> tuple[bool, str]:
    """Consumer-side remeasurement, never a trust of the receipt's counters."""
    if root is None:
        return False, "consumer_source_root_undeclared"
    if not isinstance(claim, dict) or claim.get("state") != NOT_APPLICABLE:
        return False, "no_structural_not_applicable_claim"
    measured = assess(auditor, root, top_module=top_module)
    if measured.get("state") != NOT_APPLICABLE:
        return False, "remeasurement_" + measured.get("reason", UNKNOWN)
    if claim != measured:
        return False, "source_or_proof_drift"
    return True, measured["criterion"]
