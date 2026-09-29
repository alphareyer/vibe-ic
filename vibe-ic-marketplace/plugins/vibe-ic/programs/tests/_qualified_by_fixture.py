"""Stage and SIGN a `qualified_by` field on a synthetic project
(R-0929-X-QUALIFIED-4): a design-input line to quote, the structured field on
the L9 port row, the D1 expectation that names the fact, and the D1 receipt
over those exact bytes (the repo's own `_ai_judgement_fixture.sign`)."""

import json
from pathlib import Path

from _ai_judgement_fixture import sign

INPUT_DOC = "input/docs/L3_external_interface.md"


def _l9_path(project: Path) -> Path:
    return Path(project) / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"


def stage_field(project: Path, output: str, port: str, level: str = "high",
                quote_lines=None, basis=None, row_width: int = 8) -> dict:
    """Write the design-input quotation and the L9 field; return the field."""
    project = Path(project)
    quote_lines = quote_lines or [f"| `{output}` | 8-bit | output | write data |",
                                  f"| `{port}` | 1-bit | output | write enable |"]
    doc = project / INPUT_DOC
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("### Port group\n\n| Sub-port | 寬度 | 方向 | 描述 |\n"
                   "|---|---|---|---|\n" + "\n".join(quote_lines) + "\n")
    if basis is None:
        basis = [{"file": INPUT_DOC, "line": 5 + i, "quote": q}
                 for i, q in enumerate(quote_lines)]
    field = {"port": port, "active_level": level, "basis": basis}
    l9p = _l9_path(project)
    l9p.parent.mkdir(parents=True, exist_ok=True)
    l9 = json.loads(l9p.read_text()) if l9p.is_file() else {}
    rows = l9.setdefault("top_ports", [])
    row = next((r for r in rows if r.get("name") == output), None)
    if row is None:
        row = {"name": output, "direction": "output", "width": row_width}
        rows.append(row)
    row["qualified_by"] = field
    l9p.write_text(json.dumps(l9, ensure_ascii=False))
    return field


def write_expectation(project: Path, output: str, port: str,
                      level: str = "high") -> None:
    exp = Path(project) / ("reports/audit/phase1/expert_parse_track_pack/"
                           "l_doc_expectations.json")
    exp.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(exp.read_text()) if exp.is_file() else {"expectations": []}
    doc["expectations"].append({
        "id": f"qualified_by:{output}", "layer": "L9_INTEGRATION_SPEC",
        "field_path": "top_ports",
        "requirement": f"{output} is qualified by {port} (active {level})",
        "evidence": ["the basis quotations"],
        "expected_tokens": [output, port, level],
        "qualified_by": {"output": output, "port": port,
                         "active_level": level}})
    exp.write_text(json.dumps(doc, ensure_ascii=False))


def sign_field(project: Path, output: str, port: str, level: str = "high",
               **kw) -> dict:
    """The whole review: field + expectation + D1 receipt over those bytes."""
    field = stage_field(project, output, port, level, **kw)
    write_expectation(project, output, port, level)
    sign(Path(project), "D1")
    return field
