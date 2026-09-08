#!/usr/bin/env python3
"""Generate thin tests only for explicitly requested, authored contracts.

Ordinary guidance does not need a test module. Every existing module is
preserved, including generated modules that may have been hand-edited.
New wrappers use the shared implementation with all legacy logical cases.
"""
from pathlib import Path
import sys

from skill_authoring import ContractError, load_contract, requested_skills

TEMPLATE = '''\
"""Shared compliance wrapper for {skill_name}; report checks, not execution evidence."""
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR.parents[1] / "_shared"))
from generated_compliance_support import GeneratedCompliance

_CASE = GeneratedCompliance(_SKILL_DIR)


def test_compliance_yaml_loads():
    _CASE.test_compliance_yaml_loads()


def test_empty_output_fails_audit(tmp_path):
    _CASE.test_empty_output_fails_audit(tmp_path)


def test_good_output_passes_all_required(tmp_path):
    _CASE.test_good_output_passes_all_required(tmp_path)
'''


def main(argv=None):
    plugin = Path(__file__).resolve().parent.parent
    try:
        selected = requested_skills(plugin, argv)
        for skill in selected:
            load_contract(skill / 'compliance.yaml')
        for skill in selected:
            target = skill / 'tests' / 'test_compliance.py'
            generated = TEMPLATE.format(skill_name=skill.name)
            if target.exists():
                current = target.read_bytes()
                if current == generated.encode():
                    print(f'UNCHANGED shared compliance wrapper: {skill.name}')
                    continue
                print(f'UNCHANGED existing tests: {skill.name}; generated headers do not prove unedited content')
                continue
            target.parent.mkdir(exist_ok=True)
            target.write_text(generated)
            print(f'WROTE shared compliance wrapper: {skill.name}')
    except (ContractError, OSError) as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
