"""Resolve input-authorised, declaration-named top-level port groups.

Only a group explicitly delegated by the design input can use names from
declaration.json. The example table remains evidence of the illustrative
spellings, not a second mandatory interface.
"""

from __future__ import annotations

import json
import re
import os as _os
import sys as _sys
from pathlib import Path

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from _prose_polarity import is_denied


_HEADING = re.compile(r"^#{2,6}\s*([A-Za-z][\w-]*)\s+port\s+group\b", re.I)
_PORT = re.compile(r"`([A-Za-z_][A-Za-z_0-9]*)`")
_DECLARATION = r"(?:plugin_output/)?declaration\.json"
_PORT_NAMES = r"(?:\b(?:port|signal|pin|sub-?port)\s+names?\b|\bnames?\s+(?:of|for)\s+(?:this|the)\s+(?:port|signal|pin|sub-?port)\b)"
_ZH_PORT_NAMES = r"(?:訊號|信号|埠|端口)\s*(?:名稱|名称)"
_CLAUSES = re.compile(r"[;；。]|[.!?](?=\s|$)")


def _delegates_names(line: str, group: str) -> bool:
    """Require an affirmative grammatical link from group port names to the file.

    A filename and a port word in adjacent statements are not authority. Split
    at clause boundaries before checking either the relation or its polarity.
    """
    modifier = rf"(?:(?:the|this|actual|concrete|exact|{re.escape(group)})\s+)*"
    relationships = (
        # The port names are declared by the plugin in declaration.json.
        rf"{_PORT_NAMES}[^;；。]{{0,100}}?\b(?:declared|defined|specified|"
        rf"determined|set|named)\s+(?:by\s+(?:the\s+)?plugin\s+)?"
        rf"(?:by|in|via|through|from)\s+{_DECLARATION}",
        # The port names come from declaration.json.
        rf"{_PORT_NAMES}[^;；。]{{0,70}}?\b(?:come|derive|are\s+sourced)\s+"
        rf"from\s+{_DECLARATION}",
        # declaration.json defines the group's port names.
        rf"{_DECLARATION}\s+(?:declares|defines|specifies|determines|sets)\s+"
        rf"{modifier}{_PORT_NAMES}",
        rf"{_DECLARATION}\s+is\s+(?:the\s+)?(?:authority|source\s+of\s+truth)\s+"
        rf"for\s+{modifier}{_PORT_NAMES}",
        rf"{_ZH_PORT_NAMES}[^;；。]{{0,50}}?由\s*(?:Plugin\s*)?(?:在|於|透過)?\s*"
        rf"{_DECLARATION}\s*(?:宣告|聲明|指定|定義|定义)",
        rf"{_DECLARATION}\s*(?:宣告|聲明|指定|定義|定义)[^;；。]{{0,30}}?"
        rf"{_ZH_PORT_NAMES}",
    )
    for clause in _CLAUSES.split(line):
        if is_denied(clause) is None and any(
                re.search(pattern, clause, re.I) for pattern in relationships):
            return True
    return False


def extract_delegated_groups(extracted: dict[str, str]) -> list[dict]:
    """Extract Markdown group delegation into an actionable L9 field.

    The heading names the group; an affirmative sentence names the authority;
    the first column of the following table names illustrative ports. A bare
    mention of declaration.json does not delegate any other group.
    """
    groups: list[dict] = []
    for source, body in sorted((extracted or {}).items()):
        lines = (body or "").splitlines()
        for i, line in enumerate(lines):
            match = _HEADING.match(line.strip())
            if not match:
                continue
            section = []
            for following in lines[i + 1:]:
                if following.lstrip().startswith("#"):
                    break
                section.append(following)
            authority_lines = [row for row in section
                               if _delegates_names(row, match.group(1))]
            if not authority_lines:
                continue
            examples = []
            for row in section:
                if not row.lstrip().startswith("|"):
                    continue
                first_cell = row.split("|", 2)[1]
                for name in _PORT.findall(first_cell):
                    if name not in examples:
                        examples.append(name)
            if examples:
                groups.append({
                    "group": match.group(1).lower(),
                    "authority": "plugin_output/declaration.json",
                    "example_ports": examples,
                    "source_document": source,
                })
    return groups


def resolve_delegated_groups(project: Path | None, l9: dict) -> list[dict]:
    """Bind each L9 group to exactly one identifier-valued declaration map.

    A missing, malformed, or ambiguous declaration yields no relaxation.
    Values that are parameters rather than Verilog identifiers are ignored.
    """
    if project is None or not isinstance(l9, dict):
        return []
    rows = l9.get("plugin_declared_port_groups")
    if not isinstance(rows, list) or not rows:
        return []
    try:
        declaration = json.loads(
            (project / "plugin_output" / "declaration.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(declaration, dict):
        return []
    resolved = []
    for row in rows:
        if not isinstance(row, dict) or row.get("authority") != "plugin_output/declaration.json":
            continue
        group = row.get("group")
        examples = row.get("example_ports")
        if not isinstance(group, str) or not re.fullmatch(r"[a-z][a-z_0-9]*", group):
            continue
        if not isinstance(examples, list) or not examples or not all(
                isinstance(name, str) and name.isidentifier() for name in examples):
            continue
        candidates = [declaration.get(key) for key in
                      (group, f"{group}_interface", f"{group}_port_names")
                      if isinstance(declaration.get(key), dict)]
        if len(candidates) != 1:
            continue
        names = {value for value in candidates[0].values()
                 if isinstance(value, str) and value.isidentifier()}
        if names:
            resolved.append({"group": group, "example_ports": set(examples),
                             "declared_ports": names})
    return resolved
