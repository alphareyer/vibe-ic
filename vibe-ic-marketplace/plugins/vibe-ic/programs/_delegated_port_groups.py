"""Resolve declaration-named top-level ports within their input section."""

from __future__ import annotations

import json
import re
from pathlib import Path

_HEADING = re.compile(r"^#{2,6}\s*([A-Za-z][\w-]*)\s+port\s+group\b", re.I)
_PORT = re.compile(r"`([A-Za-z_][A-Za-z_0-9]*)`")
_DECLARATION = re.compile(r"\bdeclaration\.json\b", re.I)


def _sections(extracted: dict[str, str]) -> list[tuple[str, str, list[str]]]:
    """Keep each port group inside its own Markdown heading boundary."""
    sections = []
    for source, body in sorted((extracted or {}).items()):
        lines = (body or "").splitlines()
        for i, line in enumerate(lines):
            match = _HEADING.match(line.strip())
            if match is None:
                continue
            section = [line]
            for following in lines[i + 1:]:
                if following.lstrip().startswith("#"):
                    break
                section.append(following)
            sections.append((source, match.group(1).lower(), section))
    return sections


def _examples(section: list[str]) -> list[str]:
    examples = []
    for row in section:
        if row.lstrip().startswith("|"):
            first_cell = row.split("|", 2)[1]
            for name in _PORT.findall(first_cell):
                if name not in examples:
                    examples.append(name)
    return examples


def extract_delegated_groups(extracted: dict[str, str]) -> list[dict]:
    """Record groups whose own Markdown section mentions declaration.json.

    This is a source locator, not prose-derived authority over any port name.
    The resolver grants only names in this group's declaration entry.
    """
    groups: list[dict] = []
    for source, group, section in _sections(extracted):
        if not any(_DECLARATION.search(line) for line in section):
            continue
        groups.append({
            "group": group,
            "authority": "plugin_output/declaration.json",
            "example_ports": _examples(section),
            "source_document": source,
        })
    return groups


_EXCLUDED_INPUT_PARTS = frozenset({
    "oracle", "harness", "golden", "reference", "expected",
    "phase1", "phase2", "phase3", "reports",
})


def _source_mentions_declaration(
        project: Path, source: str, group: str,
        source_documents: object) -> bool:
    """Recheck only the exact Phase-1-inventoried input section.

    L9's source locator cannot expand the input set. In particular, no docs
    tree walk or root README fallback is safe at this Phase-2 boundary.
    """
    if (not isinstance(source, str) or not source
            or not isinstance(group, str)
            or not isinstance(source_documents, list)):
        return False
    try:
        root = project.resolve()
        input_root = (project / "input" / "docs").resolve()
        if input_root.relative_to(root).parts != ("input", "docs"):
            return False
    except (OSError, ValueError):
        return False

    candidates: set[Path] = set()
    for recorded in source_documents:
        if not isinstance(recorded, str) or "\\" in recorded:
            continue
        parts = recorded.split("/")
        if (len(parts) < 3 or parts[:2] != ["input", "docs"]
                or any(part in ("", ".", "..") for part in parts)
                or any(part.casefold() in _EXCLUDED_INPUT_PARTS
                       for part in parts[2:])):
            continue
        rel = Path(*parts[2:])
        encoded = "__".join(parts[2:-1] + [parts[-1]])
        locators = {rel.as_posix(), encoded, recorded,
                    f"__chip_root_docs__/{recorded}"}
        if source in locators:
            candidates.add(input_root / rel)
    if len(candidates) != 1:
        return False
    path = candidates.pop()
    try:
        resolved = path.resolve()
        if resolved != path:
            return False
        relative = resolved.relative_to(root)
        if (relative.parts[:2] != ("input", "docs")
                or any(part.casefold() in _EXCLUDED_INPUT_PARTS
                       for part in relative.parts[2:])):
            return False
        body = path.read_text()
    except (OSError, UnicodeError, ValueError):
        return False
    return any(section_group == group and
               any(_DECLARATION.search(line) for line in section)
               for _, section_group, section in _sections({source: body}))


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
        declaration_path = project / "plugin_output" / "declaration.json"
        if declaration_path.resolve() != (
                project.resolve() / "plugin_output" / "declaration.json"):
            return []
        declaration = json.loads(declaration_path.read_text())
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
        source = row.get("source_document")
        if not isinstance(group, str) or not re.fullmatch(r"[a-z][a-z_0-9]*", group):
            continue
        if not _source_mentions_declaration(
                project, source, group, l9.get("source_documents")):
            continue
        if not isinstance(examples, list) or not all(
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
