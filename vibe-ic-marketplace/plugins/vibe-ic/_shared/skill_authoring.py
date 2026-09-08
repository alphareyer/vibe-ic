"""Scoped authoring helpers; validate declared contracts, never require one per skill."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

import skill_compliance_check as scc


class ContractError(ValueError):
    pass


def requested_skills(plugin: Path, argv=None) -> list[Path]:
    parser = argparse.ArgumentParser(description="Author only explicitly selected skills.")
    parser.add_argument("--skill", action="append", default=[], metavar="NAME")
    args = parser.parse_args(argv)
    selected = []
    for name in dict.fromkeys(args.skill):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ContractError(f"invalid skill name: {name}")
        path = plugin / "skills" / name
        if path.is_symlink() or path.resolve().parent != (plugin / "skills").resolve():
            raise ContractError(f"skill must be a direct local directory: {name}")
        if not (path / "SKILL.md").is_file():
            raise ContractError(f"missing skill instructions: {name}/SKILL.md")
        selected.append(path)
    if not selected:
        print("NO_ACTION: specify --skill NAME; ordinary guidance needs no generated contract or tests.")
    return selected


def load_contract(path: Path) -> dict:
    """Validate an authored schema without pretending its checks have run."""
    if not path.is_file():
        raise ContractError(f"referenced compliance contract is missing: {path}")
    try:
        spec = scc._load_yaml(path)
        if not isinstance(spec, dict) or spec.get("skill") != path.parent.name:
            raise ValueError("skill field must identify its directory")
        unread = set(spec) - scc._READ_TOPLEVEL_KEYS
        if unread:
            raise ValueError(f"unread contract keys: {sorted(unread)}")
        if spec.get("output_type") is not None and spec["output_type"] not in scc._KNOWN_OUTPUT_TYPES:
            raise ValueError("unknown output_type")
        requirements = spec.get("requirements", [])
        checks = spec.get("cross_checks", [])
        if not isinstance(requirements, list) or not isinstance(checks, list):
            raise ValueError("requirements and cross_checks must be lists")
        if not requirements and not checks:
            raise ValueError("empty draft is not an authored contract")
        ids = set()
        for row in [*requirements, *checks]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError("each contract rule needs an id")
            if row["id"] in ids:
                raise ValueError(f"duplicate contract id: {row['id']}")
            ids.add(row["id"])
        for row in requirements:
            if not isinstance(row.get("pattern"), str) or not row["pattern"]:
                raise ValueError(f"missing pattern: {row['id']}")
            if "required" in row and type(row["required"]) is not bool:
                raise ValueError(f"required must be boolean: {row['id']}")
            re.compile(row["pattern"], re.MULTILINE | re.IGNORECASE | re.DOTALL)
        for row in checks:
            if row.get("rule") not in scc.CROSS_CHECK_RULES:
                raise ValueError(f"unknown cross-check rule: {row.get('rule')}")
            if row.get("rule") == "audit_receipt_evidence" and not row.get("auditor"):
                raise ValueError(f"missing auditor: {row['id']}")
        return spec
    except Exception as exc:
        raise ContractError(f"malformed authored contract {path}: {exc}") from exc


def referenced_contracts(skill: Path) -> list[Path]:
    """Resolve concrete compliance references in instructions; examples stay examples."""
    plugin = skill.parent.parent.resolve()
    text = (skill / "SKILL.md").read_text()
    refs = set()
    for raw in re.findall(r"[\w./{}<>$*-]*compliance\.ya?ml", text):
        if any(c in raw for c in "{}<>$*") or any(part.isupper() for part in raw.split("/")):
            continue
        if "skills/" in raw:
            path = plugin / raw[raw.index("skills/"):]
        else:
            path = skill / raw
        path = path.resolve()
        if not path.is_relative_to(plugin):
            raise ContractError(f"contract reference leaves this plugin: {raw}")
        refs.add(path)
    return sorted(refs)


def validate_skill_contracts(skill: Path) -> None:
    paths = set(referenced_contracts(skill))
    local = skill / "compliance.yaml"
    if local.exists():
        paths.add(local.resolve())
    for path in sorted(paths):
        load_contract(path)
