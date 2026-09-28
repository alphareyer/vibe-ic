#!/usr/bin/env python3
"""Advisory comparison of design-declared knobs and values seen by a consumer.

This is deliberately ADVISORY while existing published runs are swept. A
missing measurement is NOT_MEASURED, never agreement. Overrides need a cited
design-document row or an owner-attested answer; a flow-produced explanation
cannot waive a drift.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
from pathlib import Path

_KNOBS = ("SYNTH_MAX_FANOUT", "FP_CORE_UTIL", "PL_TARGET_DENSITY",
          "FP_PDN_VOFFSET")
_PERCENT = {"FP_CORE_UTIL"}
_TOL = 1e-6


def _number(value: object, knob: str) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if knob in _PERCENT and number > 1:
        number /= 100
    return number


def _source_files(project: Path):
    for folder in (project / "input" / "docs", project / "phase1" / "generated_docs"):
        if folder.is_dir():
            yield from sorted(folder.glob("L9*"))


def _cells(line: str) -> list[str]:
    return [re.sub(r"^\*\*(.*?)\*\*$", r"\1", c.strip()).strip().strip("`")
            for c in line.strip().strip("|").split("|")]


def _declared_number(value: str, knob: str) -> float | None:
    number = _number(value, knob)
    if number is None:
        # The floorplan consumer takes the low end of an L9 range.
        match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:%|[-–~]|\bto\b|\()", value)
        number = _number(match.group(1), knob) if match else None
    if number is None:
        return None
    if knob == "SYNTH_MAX_FANOUT" and (number <= 0 or not number.is_integer()):
        return None
    if knob in ("FP_CORE_UTIL", "PL_TARGET_DENSITY") and not 0 < number <= 1:
        return None
    return number


def _scoped_rank(scope: str, key: str, *, pdk: str, library: str) -> int | None:
    key = key.lower()
    if scope == "pdk":
        actual = re.sub(r"[^a-z0-9]", "", pdk.lower())
        row = re.sub(r"[^a-z0-9]", "", key)
        if actual and row and (actual.startswith(row) or row.startswith(actual)):
            return 0 if actual == row else 1
        return None
    # Same exact-before-glob and library-before-PDK order as the fanout reader.
    for index, actual in enumerate((library.lower(), pdk.lower())):
        if actual and key == actual:
            return index
    for index, actual in enumerate((library.lower(), pdk.lower())):
        if actual and fnmatch.fnmatchcase(actual, key):
            return index + 2
    return None


def collect_declared(project: Path, *, pdk: str = "", library: str = ""
                     ) -> dict[str, tuple[float, str]]:
    """Read L9 row and active scoped-column declarations with provenance."""
    found: dict[str, tuple[float, str]] = {}
    for path in _source_files(project):
        lines = path.read_text(errors="replace").splitlines()
        # A table's column map cannot carry into a later table. Keep its
        # first-cell scope and choose the active row, never another PDK's row.
        i = 0
        while i < len(lines):
            if not lines[i].lstrip().startswith("|"):
                i += 1
                continue
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append((i + 1, _cells(lines[i])))
                i += 1
            if not block:
                continue
            header = block[0][1]
            scope = header[0].lower() if header else ""
            scoped = scope in ("pdk", "library", "lib", "standard_cell_library")
            columns = {}
            if scoped:
                for index, cell in enumerate(header[1:], 1):
                    knob = "SYNTH_MAX_FANOUT" if cell.upper() == "MAX_FANOUT_CONSTRAINT" else cell.upper()
                    if knob in _KNOBS:
                        columns[knob] = index
                for knob, col in columns.items():
                    best = None
                    for line_no, cells in block[1:]:
                        if col >= len(cells):
                            continue
                        rank = _scoped_rank("pdk" if scope == "pdk" else "library",
                                            cells[0], pdk=pdk, library=library)
                        value = _declared_number(cells[col], knob)
                        if rank is not None and value is not None and (
                                best is None or rank < best[0]):
                            best = (rank, value, line_no)
                    if best is not None and knob not in found:
                        found[knob] = (best[1], f"{path.relative_to(project)}:{best[2]}")
                if columns:
                    continue
            for line_no, cells in block:
                if len(cells) < 2:
                    continue
                for index, cell in enumerate(cells[:-1]):
                    knob = ("SYNTH_MAX_FANOUT" if cell.upper() == "MAX_FANOUT_CONSTRAINT"
                            else cell.upper())
                    if knob not in _KNOBS or knob in found:
                        continue
                    value = _declared_number(cells[index + 1], knob)
                    if value is not None:
                        found[knob] = (value, f"{path.relative_to(project)}:{line_no}")
        # Row-oriented Markdown also permits a table without leading pipes.
        for line_no, line in enumerate(lines, 1):
            if line.lstrip().startswith("|") or "|" not in line:
                continue
            cells = _cells(line)
            for index, cell in enumerate(cells[:-1]):
                knob = ("SYNTH_MAX_FANOUT" if cell.upper() == "MAX_FANOUT_CONSTRAINT"
                        else cell.upper())
                if knob in _KNOBS and knob not in found:
                    value = _declared_number(cells[index + 1], knob)
                    if value is not None:
                        found[knob] = (value, f"{path.relative_to(project)}:{line_no}")
    return found


def _attested(project: Path, record: dict, knob: str, declared: float,
              applied: float) -> bool:
    if (record.get("knob") != knob or not record.get("reason") or
            _number(record.get("declared"), knob) != declared or
            _number(record.get("applied"), knob) != applied):
        return False
    decider = str(record.get("decided_by", ""))
    if decider == f"owner:{knob}":
        import json
        path = project / "input" / "step_0_5ic_answers.json"
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text())
            return (data.get("answer_provenance", {}).get(knob, {}).get("answered_by") == "owner"
                    and _number(data.get("answers", {}).get(knob), knob) == applied)
        except (ValueError, TypeError, AttributeError):
            return False
    if decider.startswith("document:"):
        # The decider must cite the actual design-input row with the applied
        # value; an arbitrary path string is not an attestation.
        m = re.fullmatch(r"document:(input/docs/L\d+[^/:]*):(\d+)", decider)
        if not m:
            return False
        path = project / m.group(1)
        if not path.is_file() or not path.resolve().is_relative_to((project / "input/docs").resolve()):
            return False
        lines = path.read_text(errors="replace").splitlines()
        n = int(m.group(2))
        return 1 <= n <= len(lines) and knob in lines[n - 1] and any(
            _number(cell.strip().strip("`* "), knob) == applied
            for cell in lines[n - 1].split("|"))
    return False


def compare(project: Path, applied: dict[str, tuple[object, str]], *,
            declared: dict[str, tuple[float, str]] | None = None,
            overrides: list[dict] | None = None,
            pdk: str = "", library: str = "") -> dict:
    """Report every supplied consumer value; this function never blocks."""
    declared = collect_declared(project, pdk=pdk, library=library) if declared is None else declared
    rows = {}
    for knob, (raw, consumer) in applied.items():
        if knob not in _KNOBS:
            continue
        actual = _number(raw, knob)
        declaration = declared.get(knob)
        if declaration is None or actual is None:
            status = "NOT_MEASURED"
            reason = f"{knob}: declaration or applied value unread"
        else:
            expected, source = declaration
            attested = next((rec for rec in overrides or []
                             if _attested(project, rec, knob, expected, actual)), None)
            status = ("PASS" if math.isclose(expected, actual, rel_tol=0, abs_tol=_TOL)
                      or attested else "FAIL")
            reason = (f"{knob}: declared {expected:g} at {source}; applied {actual:g} "
                      f"at {consumer}" + (f"; override {attested['decided_by']}: "
                                           f"{attested['reason']}" if attested else ""))
        rows[knob] = {"status": status, "reason": reason,
                      "declared": declaration[0] if declaration else None,
                      "applied": actual, "consumer": consumer}
    return {"mode": "ADVISORY", "rows": rows}


def observe_sdc(project: Path, sdc: Path, *, pdk: str = "", library: str = "") -> dict:
    """Read the applied fanout from the emitted SDC, not the producer's plan."""
    if not sdc.is_file():
        return compare(project, {"SYNTH_MAX_FANOUT": (None, f"{sdc}: NOT_READ")},
                       pdk=pdk, library=library)
    matches = re.findall(r"(?m)^\s*set_max_fanout\s+(\d+(?:\.\d+)?)\b",
                         sdc.read_text(errors="replace"))
    value = float(matches[-1]) if matches else None
    return compare(project, {"SYNTH_MAX_FANOUT": (value, str(sdc))},
                   pdk=pdk, library=library)


def write_sdc_report(project: Path, sdc: Path, *, pdk: str = "", library: str = "") -> Path:
    """Write the advisory beside the SDC that the next consumer will read."""
    from _atomic_artefact import write_text
    output = sdc.with_suffix(".parity.json")
    write_text(output, json.dumps(observe_sdc(project, sdc, pdk=pdk,
                                             library=library), indent=2) + "\n")
    return output


def observe_pnr(project: Path, deck: Path, *, cell_area_um2: float | None,
                core_area_um2: float | None,
                tech_lef_text: str = "", pdk: str = "", library: str = "",
                area_source: str = "") -> dict:
    """Read placement's deck and the sized core, keeping the two knobs apart."""
    text = deck.read_text(errors="replace") if deck.is_file() else ""
    density = re.findall(r"(?m)^global_placement\b[^\n]*\s-density\s+(\d+(?:\.\d+)?)\b",
                         text)
    vertical = set()
    layers = list(re.finditer(r"(?im)^\s*LAYER\s+([^\s;]+)\s*$", tech_lef_text))
    for index, match in enumerate(layers):
        block = tech_lef_text[match.end():layers[index + 1].start()
                              if index + 1 < len(layers) else len(tech_lef_text)]
        if re.search(r"\bDIRECTION\s+VERTICAL\s*;", block, re.I):
            vertical.add(match.group(1).lower())
    offsets = set()
    for line in text.splitlines():
        if "add_pdn_stripe" not in line or "-followpins" in line:
            continue
        layer = re.search(r"\s-layer\s+([^\s]+)", line)
        offset = re.search(r"\s-offset\s+(\d+(?:\.\d+)?)\b", line)
        if layer and offset and layer.group(1).lower() in vertical:
            offsets.add(float(offset.group(1)))
    vertical_offset = next(iter(offsets)) if len(offsets) == 1 else None
    actual_util = (cell_area_um2 / core_area_um2 if cell_area_um2 is not None
                   and core_area_um2 and core_area_um2 > 0 else None)
    return compare(project, {
        "FP_CORE_UTIL": (actual_util, f"cell_area/core_area ({area_source or deck})"),
        "PL_TARGET_DENSITY": (float(density[-1]) if density else None, str(deck)),
        "FP_PDN_VOFFSET": (vertical_offset,
                              f"{deck}: vertical layer from active tech LEF"),
    }, pdk=pdk, library=library)


def write_pnr_report(project: Path, deck: Path, *, cell_area_um2: float | None,
                     core_area_um2: float | None,
                     tech_lef_text: str = "", pdk: str = "", library: str = "") -> Path:
    """Compatibility spelling for the pre-run PLANNED report."""
    return write_planned_pnr_report(project, deck, cell_area_um2=cell_area_um2,
                                    core_area_um2=core_area_um2,
                                    tech_lef_text=tech_lef_text,
                                    pdk=pdk, library=library)


def write_planned_pnr_report(project: Path, deck: Path, *, cell_area_um2: float | None,
                             core_area_um2: float | None, tech_lef_text: str = "",
                             pdk: str = "", library: str = "") -> Path:
    from _atomic_artefact import write_text
    output = deck.with_name(deck.stem + ".planned.parity.json")
    report = observe_pnr(project, deck, cell_area_um2=cell_area_um2,
                         core_area_um2=core_area_um2, tech_lef_text=tech_lef_text,
                         pdk=pdk, library=library)
    report["evidence_stage"] = "PLANNED"
    report["mode"] = "PLANNED_ADVISORY"
    write_text(output, json.dumps(report, indent=2) + "\n")
    return output


def write_pending_pnr_report(project: Path, deck: Path) -> Path:
    """Clear any prior applied PASS before a new PnR invocation starts."""
    from _atomic_artefact import write_text
    output = deck.with_suffix(".parity.json")
    report = {"mode": "ADVISORY", "evidence_stage": "NOT_MEASURED",
              "rows": {knob: {"status": "NOT_MEASURED",
                              "reason": "final PnR route not accepted or not measured",
                              "declared": None, "applied": None, "consumer": None}
                       for knob in ("FP_CORE_UTIL", "PL_TARGET_DENSITY",
                                    "FP_PDN_VOFFSET")}}
    write_text(output, json.dumps(report, indent=2) + "\n")
    return output


_APPLIED_CELL_AREA = "vibeic__pnr__applied__cell_area_um2"
_APPLIED_CORE_AREA = "vibeic__pnr__applied__core_area_um2"


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_applied_pnr_report(project: Path, deck: Path, final_def: Path,
                             metrics: Path, *, tech_lef_text: str = "",
                             pdk: str = "", library: str = "") -> Path:
    """Use the accepted route's tool-measured geometry, never a sizing plan."""
    from _atomic_artefact import write_text
    output = deck.with_suffix(".parity.json")
    values = {}
    if final_def.is_file() and final_def.stat().st_size > 0 and metrics.is_file():
        try:
            loaded = json.loads(metrics.read_text())
            if isinstance(loaded, dict):
                values = loaded
        except (OSError, ValueError):
            pass
    # These are square micrometres, not percentages. Percent normalization
    # belongs only to L9's FP_CORE_UTIL declaration and the final ratio.
    cell = _number(values.get(_APPLIED_CELL_AREA), "AREA_UM2")
    core = _number(values.get(_APPLIED_CORE_AREA), "AREA_UM2")
    if cell is None or cell <= 0 or core is None or core <= 0:
        cell = core = None
    report = observe_pnr(project, deck, cell_area_um2=cell,
                         core_area_um2=core, tech_lef_text=tech_lef_text,
                         pdk=pdk, library=library, area_source=str(metrics))
    report["evidence_stage"] = "APPLIED" if final_def.is_file() else "NOT_MEASURED"
    report["evidence"] = {
        "final_def": str(final_def) if final_def.is_file() else None,
        "final_def_sha256": _digest_file(final_def)
                            if final_def.is_file() else None,
        "metrics": str(metrics) if metrics.is_file() else None,
        "cell_area_um2": cell, "core_area_um2": core,
    }
    write_text(output, json.dumps(report, indent=2) + "\n")
    return output
