#!/usr/bin/env python3
"""Link an existing authored report contract for explicitly requested skills.

This authoring operation neither creates a landing gate nor certifies execution.
No arguments leave every skill unchanged. Existing instructions are preserved.
"""
from pathlib import Path
import sys

from skill_authoring import ContractError, load_contract, referenced_contracts, requested_skills

NOTE = """

The report-shape contract is declared in [compliance.yaml](compliance.yaml).
Run the shared `skill_compliance_check.py --requirements` driver with that
contract and the report when this check is relevant to the requested work.
Regex satisfaction checks report shape; it does not establish that EDA,
simulation, or behavioral verification ran. Declared audit-receipt checks
still require their producer's measured evidence. This link adds no global
landing requirement and does not certify task completion.
"""


def main(argv=None):
    plugin = Path(__file__).resolve().parent.parent
    try:
        selected = requested_skills(plugin, argv)
        for skill in selected:
            load_contract(skill / "compliance.yaml")
            for reference in referenced_contracts(skill):
                load_contract(reference)
        for skill in selected:
            md = skill / "SKILL.md"
            content = md.read_text()
            if (skill / "compliance.yaml").resolve() in referenced_contracts(skill):
                print(f"UNCHANGED: {skill.name} already references its contract")
                continue
            md.write_text(content.rstrip() + NOTE)
            print(f"LINKED report-shape contract: {skill.name}")
    except (ContractError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
