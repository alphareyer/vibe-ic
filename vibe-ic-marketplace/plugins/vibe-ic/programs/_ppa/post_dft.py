"""Retained post-DFT arm admission library (W1A-I1, HARVEST-before-delete).

Scan survival and conclusive equivalence must both PASS before an arm can
compete on measured area/count. The original admission/select functions stay
until their precise merged tool-path destination is proved. Native tool
production remains outside this library; no resynthesis producer is restored.
"""
from __future__ import annotations

import json
from pathlib import Path

import librelane_contract as ll
from _atomic_artefact import write_json


def admit(arm_report: Path, survival: dict, lec: dict, output: Path) -> dict:
    """Stamp feasibility onto an arm; an unproven arm can never win."""
    doc = json.loads(arm_report.read_text())
    reasons = []
    if str(survival.get('verdict')).upper() != 'PASS':
        reasons.append(f"scan survival {survival.get('verdict')}")
    if str(lec.get('verdict')).upper() != 'PASS' or lec.get('unproven_points') not in (0, [], None):
        reasons.append(f"equivalence {lec.get('verdict')}")
    doc['feasibility'] = {'scan_survival': survival.get('verdict'),
                          'equivalence': lec.get('verdict'),
                          'feasible': not reasons, 'reasons': reasons}
    if reasons:
        doc['verdict'] = 'NOT_FEASIBLE'
    write_json(output, doc)
    return doc

def select(admitted: dict[str, Path], output: Path) -> dict:
    """Pareto over feasible arms only (area, then count); ties stay open."""
    docs = {name: json.loads(path.read_text()) for name, path in admitted.items()}
    feasible = {name: admitted[name] for name, doc in docs.items()
                if doc.get('feasibility', {}).get('feasible')}
    if not feasible:
        result = {'selection': 'UNDETERMINED', 'reason': 'LL_NO_FEASIBLE_POST_DFT_ARM',
                  'arms': list(admitted)}
        write_json(output, result)
        return result
    return ll.select_arms(feasible, {'design__instance__area': 'min',
                                     'design__instance__count': 'min'}, output)
