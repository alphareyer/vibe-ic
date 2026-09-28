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
_INTERFACE_OWNER = re.compile(
    r"\b([A-Za-z][\w-]*)\s+(?:interface|port\s+group)\b", re.I)


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


def _identifiers(sections: list[tuple[str, str, list[str]]]) -> dict[str, set[str]]:
    """Learn sibling identities from headings and their example-port prefixes."""
    identities: dict[str, set[str]] = {}
    for _, group, section in sections:
        owned = identities.setdefault(group, {group})
        names = _examples(section)
        if not names:
            continue
        tokens = [name.lower().split("_") for name in names]
        common = []
        for position in range(min(map(len, tokens))):
            if len({parts[position] for parts in tokens}) != 1:
                break
            common.append(tokens[0][position])
        # A single example's final token is the signal, not a group prefix.
        if len(names) == 1 and common:
            common.pop()
        semantic = (common[1:] if common and common[0] in {"i", "o", "io"}
                    else common)
        if semantic:
            owned.add("_".join(common) + "_")
            owned.add(semantic[0])
    return identities


def _names_another_group(clause: str, group: str,
                         identifiers: dict[str, set[str]]) -> bool:
    """A sibling heading or port prefix anywhere in the sentence vetoes it."""
    others = set().union(*(names for owner, names in identifiers.items()
                           if owner != group)) if identifiers else set()
    if any(re.search(rf"\b{re.escape(name)}" +
                     ("" if name.endswith("_") else r"\b"), clause, re.I)
           for name in others):
        return True
    # An explicit interface owner can be foreign even when that interface has
    # no heading in this document. This also covers of/for and passive forms.
    return any(match.group(1).lower() not in {group, "this", "the", "its"}
               for match in _INTERFACE_OWNER.finditer(clause))


def _table_fixes_group(section: list[str], group: str) -> bool:
    """An explicit table assignment cannot coexist with name delegation."""
    group_ref = rf"(?:{re.escape(group)}|(?:this|the)\s+(?:port\s+)?group)"
    name_ref = (rf"(?:\b{group_ref}\s+(?:(?:port|signal|pin)\s+)?names?\b|"
                rf"\b(?:port|signal|pin)\s+names?\s+(?:of|for)\s+{group_ref}\b)")
    table_rule = rf"(?:{name_ref}.{{0,100}}\b(?:fixed|defined|specified|determined|set|listed)\s+(?:by|in)\s+(?:the\s+)?table\b|\btable\b.{{0,100}}\b(?:fixes|defines|specifies|determines|sets|lists)\b.{{0,100}}{name_ref})"
    for line in section:
        for clause in _CLAUSES.split(line):
            if is_denied(clause) is None and re.search(table_rule, clause, re.I):
                return True
    return False


def _delegates_names(line: str, group: str,
                     identifiers: dict[str, set[str]]) -> bool:
    """Use section, subject, source, and group identity instead of verb lists."""
    for clause in _CLAUSES.split(line):
        if (re.search(_DECLARATION, clause, re.I)
                and (re.search(_PORT_NAMES, clause, re.I)
                     or re.search(_ZH_PORT_NAMES, clause))
                and is_denied(clause) is None
                and not _names_another_group(clause, group, identifiers)):
            return True
    return False


def extract_delegated_groups(extracted: dict[str, str]) -> list[dict]:
    """Extract Markdown group delegation into an actionable L9 field.

    The heading names the group; an affirmative sentence names the authority;
    the first column of the following table names illustrative ports. A bare
    mention of declaration.json does not delegate any other group.
    """
    groups: list[dict] = []
    sections = _sections(extracted)
    identifiers = _identifiers(sections)
    for source, group, section in sections:
        authority_lines = [row for row in section
                           if _delegates_names(row, group, identifiers)]
        if not authority_lines or _table_fixes_group(section, group):
            continue
        examples = _examples(section)
        if examples:
            groups.append({
                "group": group,
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
