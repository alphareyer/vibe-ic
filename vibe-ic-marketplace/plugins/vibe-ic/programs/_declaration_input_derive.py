"""Derive declaration facts from supplied RTL and explicit design L-doc statements.

The caller supplies the top and the consume manifest.  A Verilog name, an
example cell, or a parameter's spelling alone is never an interface choice.
Every observation names its source; disagreement is returned to the emitter
as a blocking, named conflict.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _docker_memory as _dmem  # noqa: E402
import _progress_run as _progress  # noqa: E402
import _prose_polarity as _polarity  # noqa: E402


def elaborate_supplied(project: Path, top: str, files: List[str]) -> Dict[str, Any]:
    """Yosys read_verilog + hierarchy -top, with a bounded, memory-capped run.

    JSON is emitted into a temporary mount.  The project is mounted read-only;
    only files the supplied-RTL manifest identifies are admitted to the read.
    """
    import p0_tool_frontend_check as _p0

    with tempfile.TemporaryDirectory(prefix="declaration-yosys-") as scratch:
        cidfile = Path(scratch) / "cid"
        script = ("read_verilog -sv " + " ".join(shlex.quote(p) for p in files)
                  + "; hierarchy -top " + shlex.quote(top)
                  + "; proc; opt; write_json /out/elaborated.json")
        cmd = ["docker", "run", *_dmem.docker_memory_flags(), "--rm",
               "--network", "none", "--cidfile", str(cidfile),
               "-v", f"{project}:/work:ro",
               "-v", f"{scratch}:/out", "-w", "/work",
               _p0.default_image(), "--skip", "yosys", "-Q", "-T", "-p", script]
        try:
            cp = _progress.run(cmd, hard_ceiling_s=90)
        except Exception:
            # The stall watchdog reaps its Docker client. Stop ONLY the
            # container whose ID this invocation recorded, so the Yosys work
            # cannot survive as an orphan inside the daemon.
            try:
                cid = cidfile.read_text().strip()
                if re.fullmatch(r"[0-9a-f]{64}", cid):
                    subprocess.run(["docker", "stop", "--time", "5", cid],
                                   capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
            raise
        if cp.returncode:
            raise RuntimeError("Yosys elaboration refused (rc=%d): %s" % (
                cp.returncode, (cp.stderr or cp.stdout)[-700:]))
        return json.loads((Path(scratch) / "elaborated.json").read_text())


def _doc_lines(project: Path):
    for root in (project / "input" / "docs", project / "phase1" / "input_doc"):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("L*")):
            if path.is_file() and path.suffix.lower() in (".md", ".txt"):
                for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    yield str(path.relative_to(project)), number, line


def _integer_parameter(module: Dict[str, Any], name: str) -> Optional[int]:
    raw = module.get("parameter_default_values", {}).get(name)
    if not isinstance(raw, str) or not re.fullmatch(r"[01]+", raw):
        return None
    return int(raw, 2)


def _cell(cell: str) -> str:
    return cell.strip().strip("`\"' ")


def _rows(project: Path):
    for rel, line_no, line in _doc_lines(project):
        if line.lstrip().startswith("|") and line.count("|") >= 4:
            yield rel, line_no, [_cell(c) for c in line.strip().strip("|").split("|")]


def _parameter_defaults(project: Path, parameter: str):
    """Numeric defaults from a parameter table with an explicit Default column.

    A fixed table grammar is required: one row's second number alone is not
    evidence that it is this parameter's default.  The shared prose-polarity
    guard still excludes denied rows.
    """
    documents: Dict[str, List[Tuple[int, str]]] = {}
    for rel, number, line in _doc_lines(project):
        documents.setdefault(rel, []).append((number, line))
    for rel, lines in documents.items():
        header: Optional[List[str]] = None
        for number, line in lines:
            if not line.lstrip().startswith("|"):
                header = None
                continue
            cells = [_cell(c) for c in line.strip().strip("|").split("|")]
            if (len(cells) >= 2 and re.search(r"default|預設", cells[1], re.I)
                    and re.search(r"parameter|參數", cells[0], re.I)):
                header = cells
                continue
            if header is None or len(cells) < 2 or cells[0] != parameter:
                continue
            if _polarity.is_denied(line):
                continue
            if re.fullmatch(r"\d+", cells[1]):
                yield int(cells[1]), rel, number


def _observed(value: Any, provenance: str, detail: str) -> Dict[str, Any]:
    return {"value": value, "provenance": provenance,
            "provenance_detail": detail}


def derive(project: Path, contract: Dict[str, Any], top: Optional[str],
           supplied: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[str]]:
    """Return (single-source facts, named source conflicts, named tool gaps)."""
    if not supplied or not top:
        return {}, [], []
    entries = supplied.get("files") or []
    staged = [e for e in entries if e.get("status") == "staged"
              and e.get("byte_identical") is True]
    if (not staged or any(e.get("status") not in
                          ("staged", "pruned_out_of_cone") for e in entries)
            or any(e.get("status") == "staged"
                   and e.get("byte_identical") is not True for e in entries)):
        return {}, [], ["supplied RTL staging is incomplete or differs from input"]
    files = [e["staged"] for e in staged]
    try:
        elaborated = elaborate_supplied(project, top, files)
        module = elaborated["modules"][top]
    except Exception as exc:  # tool unavailability is an explicit gap
        return {}, [], ["supplied RTL Yosys elaboration: %s" % exc]
    ports = module.get("ports") or {}
    names = {f["name"] for f in contract["fields"]}
    source = (module.get("attributes") or {}).get("src", "Yosys top")
    facts: Dict[str, Dict[str, Any]] = {}
    conflicts: List[str] = []

    def add(name: str, value: Any, provenance: str, detail: str) -> None:
        if name not in names:
            return
        old = facts.get(name)
        if old is not None and (type(old["value"]) is not type(value)
                                or old["value"] != value):
            conflicts.append("%s: %r from %s conflicts with %r from %s" % (
                name, old["value"], old["provenance_detail"], value, detail))
        elif old is None:
            facts[name] = _observed(value, provenance, detail)
        elif detail != old["provenance_detail"]:
            old.setdefault("corroborated_by", []).append(detail)

    add("top_module", top, "derived_from_supplied_rtl",
        "Yosys hierarchy -top %s, %s" % (top, source))

    # A `<parameter>_bytes` field can be read from an elaborated top parameter
    # only when the L-doc explicitly gives that parameter byte units.  An
    # example/primary value remains independent evidence and is checked by the
    # emitter rather than silently overridden.
    for name in names:
        if not name.endswith("_bytes"):
            continue
        param = name[:-6]
        value = _integer_parameter(module, param)
        if value is None:
            continue
        unit_rows = [(rel, n) for rel, n, cells in _rows(project)
                     if cells and cells[0] == param and any(
                         re.search(r"\bbytes?\b|位元組", c, re.I) for c in cells[1:])]
        if unit_rows:
            add(name, value, "derived_from_supplied_rtl",
                "Yosys %s parameter %s=%d; byte unit in %s:%d" % (
                    source, param, value, *unit_rows[0]))
            for doc_default, rel, number in _parameter_defaults(project, param):
                add(name, doc_default,
                    "derived_from_ldoc:%s:%d" % (rel, number),
                    "%s:%d declares parameter %s default=%d" % (
                        rel, number, param, doc_default))

    # `CLOCK_PORT = X` is a design statement.  The exact named input must
    # exist on Yosys' elaborated top.  This is never selected by a clock-ish
    # spelling alone.
    clock_rows = []
    for rel, n, line in _doc_lines(project):
        if _polarity.is_denied(line):
            continue
        for match in re.finditer(r"\bCLOCK_PORT\s*=\s*([A-Za-z_]\w*)", line):
            clock_rows.append((match.group(1), rel, n))
    for value, rel, n in clock_rows:
        if value not in ports or ports[value].get("direction") != "input":
            conflicts.append("clock_port_name: %s:%d declares %s, absent from Yosys top input ports" % (
                rel, n, value))
        else:
            add("clock_port_name", value, "derived_from_supplied_rtl",
                "Yosys top input %s, designated by %s:%d" % (value, rel, n))

    # Reset polarity is explicit in the input table.  Require its named port
    # on the elaborated top; a spelling such as rst_n alone is not polarity.
    for rel, n, cells in _rows(project):
        if len(cells) < 4 or not re.search(r"reset|復位|重置", cells[3], re.I):
            continue
        if _polarity.is_denied(cells[3]):
            continue
        mark = re.search(r"\bactive[-_ ](high|low)\b", cells[3], re.I)
        if not mark:
            continue
        port = cells[0]
        if port not in ports or ports[port].get("direction") != "input":
            conflicts.append("reset_polarity: %s:%d names reset port %s, absent from Yosys top input ports" % (
                rel, n, port))
        else:
            add("reset_polarity", "active_" + mark.group(1).lower(),
                "derived_from_ldoc:%s:%d" % (rel, n),
                "%s:%d names reset input %s and polarity; Yosys confirms the port" % (
                    rel, n, port))

    # A pin count is the width of the input document's named port, checked
    # against Yosys.  A minimum such as >=1 is a constraint, never the count.
    for name in names:
        match = re.fullmatch(r"([A-Za-z_]+)_pin_count", name)
        if not match:
            continue
        token = match.group(1).lower()
        for rel, n, cells in _rows(project):
            if (len(cells) < 4 or token not in cells[3].lower()
                    or "output" not in cells[2].lower()):
                continue
            port = cells[0]
            if port not in ports or ports[port].get("direction") != "output":
                conflicts.append("%s: %s:%d names %s, absent from Yosys top output ports" % (
                    name, rel, n, port))
                continue
            width = len(ports[port].get("bits") or [])
            explicit = re.fullmatch(r"(\d+)\s*-?\s*bit", cells[1], re.I)
            if explicit and int(explicit.group(1)) != width:
                conflicts.append("%s: %s:%d says %s bits, Yosys top %s has %d" % (
                    name, rel, n, explicit.group(1), port, width))
            else:
                add(name, width, "derived_from_supplied_rtl",
                    "Yosys top output %s width %d, named by %s:%d" % (
                        port, width, rel, n))

    # A contract example becomes a fact only when its adjacent annotation
    # expressly says it is this design's selected choice.  Other examples and
    # menus stay unresolved for the D1 expert hand-off.
    for field in contract["fields"]:
        choices = [(value, annotation) for value, annotation in
                   (field.get("value_annotations") or {}).items()
                   if annotation.strip().lower() in (
                       "this design choice", "selected for this design",
                       "本 chip 設計選擇", "本設計選擇")]
        if len(choices) == 1:
            value = choices[0][0]
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
            line = field.get("line")
            rel = contract["source"]
            add(field["name"], value,
                "derived_from_ldoc:%s:%s" % (rel, line),
                "%s:%s explicitly marks %r as this design's choice" % (
                    rel, line, value))
        elif len(choices) > 1:
            conflicts.append("%s: input contract designates multiple choices" % field["name"])
    return facts, conflicts, []
