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
            section = []
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


def _source_mentions_declaration(project: Path, source: str, group: str) -> bool:
    """Recheck the input section named by L9 before honoring its locator."""
    if not isinstance(source, str) or not source or not isinstance(group, str):
        return False
    if source.startswith("__chip_root_docs__/"):
        candidates = [project / source.removeprefix("__chip_root_docs__/")]
    elif source.startswith("__chip_root__/"):
        name = source.removeprefix("__chip_root__/")
        candidates = [project / directory / name
                      for directory in ("", "doc", "docs", "input")]
    else:
        docs = project / "input" / "docs"
        candidates = []
        if docs.is_dir():
            for path in docs.rglob("*.md"):
                relative = path.relative_to(docs)
                encoded = ("__".join(relative.parts[:-1]) + "__" + path.name
                           if len(relative.parts) > 1 else path.name)
                if source in {relative.as_posix(), encoded}:
                    candidates.append(path)
    root = project.resolve()
    for path in candidates:
        try:
            relative = path.resolve().relative_to(root)
            parts = relative.parts
            input_docs = parts[:2] == ("input", "docs")
            docs_tree = parts[:1] in {
                ("doc",), ("docs",), ("Documentation",),
                ("documentation",)}
            readme = (len(parts) <= 4 and path.name.lower().startswith("readme")
                      and not set(parts).intersection({
                          "phase1", "phase2", "phase3", "reports",
                          "oracle", "golden", "harness"}))
            if not (input_docs or docs_tree or readme):
                continue
            body = path.read_text()
        except (OSError, ValueError):
            continue
        if any(section_group == group and
               any(_DECLARATION.search(line) for line in section)
               for _, section_group, section in _sections({source: body})):
            return True
    return False


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
        source = row.get("source_document")
        if not isinstance(group, str) or not re.fullmatch(r"[a-z][a-z_0-9]*", group):
            continue
        if not _source_mentions_declaration(project, source, group):
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
