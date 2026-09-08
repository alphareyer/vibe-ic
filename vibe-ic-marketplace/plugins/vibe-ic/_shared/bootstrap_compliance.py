#!/usr/bin/env python3
"""Scaffold draft report-shape aids only for explicitly requested skills.

No arguments are a no-op. Existing authored contracts are never replaced.
Drafts are named compliance.draft.yaml and do not enter compliance.yaml consumers.
Only patterns found in the requested instructions are suggested; presentation
conventions such as Summary and Next are not invented for unrelated guidance.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


from skill_authoring import ContractError, requested_skills


def detect_skill_patterns(skill_md: str):
    reqs = []
    for m in re.finditer(r'`(Next:\s*(?:run\s+)?/\S+[^`]*)`', skill_md):
        line = m.group(1)
        slug = re.sub(r'[^a-z0-9]+', '_', line.lower())[:40]
        if '/' in line:
            tok = re.escape(line.split('/', 1)[1].split()[0])
            reqs.append((f"R_handoff_{slug}",
                         f"Handoff: {line}",
                         r"Next:\s*(?:run\s+)?" + tok))
    if re.search(r'##\s+Output\s+(format|schema)', skill_md, re.IGNORECASE):
        reqs.append(("R_has_output_section",
                     "Output format/schema section present",
                     r"(##\s+(?:Output|Findings|Result|Report))"))
    if re.search(r'##\s+Next\s+step', skill_md, re.IGNORECASE):
        reqs.append(("R_next_step_section",
                     "Next step section",
                     r"##\s+Next\s+step"))
    return reqs


def gen_yaml(skill_name, detected):
    all_reqs = detected
    seen, uniq = set(), []
    for r in all_reqs:
        if r[0] in seen: continue
        seen.add(r[0]); uniq.append(r)
    lines = [
        f"# DRAFT report-shape aid for skill '{skill_name}'. Review before authoring a contract.",
        "# This scaffold is not execution evidence or a task-completion gate.",
        f"# Refines vibe-ic/skills/{skill_name}/SKILL.md output requirements.",
        f"skill: {skill_name}",
        "",
        "requirements:" if uniq else "requirements: []",
    ]
    for rid, desc, pat in uniq:
        safe = pat.replace("'", "''")
        lines.append(f"  - id: {rid}")
        lines.append(f"    description: {json.dumps(desc)}")
        lines.append(f"    pattern: '{safe}'")
        lines.append(f"    skill_section: 'Output/Handoff'")
    lines.append("")
    lines.append("cross_checks: []")
    lines.append("")
    return '\n'.join(lines)


def main(argv=None):
    plugin = Path(__file__).resolve().parent.parent
    try:
        selected = requested_skills(plugin, argv)
        for skill in selected:
            authored = skill / "compliance.yaml"
            draft = skill / "compliance.draft.yaml"
            if authored.exists() or draft.exists():
                print(f"UNCHANGED: {skill.name} already has an authored contract or draft")
                continue
            detected = detect_skill_patterns((skill / "SKILL.md").read_text())
            draft.write_text(gen_yaml(skill.name, detected))
            print(f"DRAFT: skills/{skill.name}/compliance.draft.yaml; review only, not execution evidence")
    except (ContractError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
