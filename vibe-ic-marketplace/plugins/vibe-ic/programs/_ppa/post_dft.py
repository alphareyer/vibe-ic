"""Step 12 post-DFT resynthesis arms for the PPA layer (designs that declare DFT).

Scan insertion adds a mux per flop, so re-mapping the scan netlist is a real
area/timing lever. LibreLane Yosys.Resynthesis re-runs ABC on an input netlist
with a selectable SYNTH_STRATEGY; each strategy is an arm, and the direct
`opt_clean -purge` netlist is the baseline arm.

An arm is FEASIBLE only when both hold on ITS netlist:
  * dft_post_optimization_scan_survival_check PASS (the chain and its ports
    survived — LEC cannot see a lost chain, scan insertion is transparent);
  * step-13 equivalence PASS (a proved LEC record for that netlist).
Only feasible arms are compared, on measured values at identical scope. A
proxy alone never adopts an arm.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librelane_contract as ll  # noqa: E402
from _atomic_artefact import write_json  # noqa: E402
from _ppa.synthesis import STRATEGIES  # noqa: E402  the same nine LibreLane strategies


def run_resynthesis_arms(project: Path, image: str, base_config: dict,
                         scan_netlist: Path, *,
                         strategies: tuple[str, ...] = STRATEGIES,
                         mounts: list[tuple[Path, str]] | None = None,
                         pdk_root: str) -> dict[str, Path]:
    """Run Yosys.Resynthesis per strategy on the scan netlist; keep every arm."""
    if not scan_netlist.is_file():
        raise ll.Refusal('LL_SCAN_NETLIST_MISSING',
                         f'no scan netlist file at {scan_netlist}')
    if not strategies or any(s not in STRATEGIES for s in strategies):
        raise ll.Refusal('LL_SYNTH_STRATEGY_INVALID', str(strategies))
    root = project / 'phase3/librelane/ppa_post_dft'
    reports: dict[str, Path] = {}
    for strategy in strategies:
        slug = strategy.lower().replace(' ', '_')
        arm = root / slug
        arm.mkdir(parents=True, exist_ok=True)
        source, resolved = arm / 'resynthesis_input.json', arm / 'resynthesis_resolved.json'
        write_json(source, {**base_config, 'SYNTH_STRATEGY': strategy,
                            'meta': {'step': 'Yosys.Resynthesis'}})
        ll.resolve_step_config(project, image, source, resolved,
                               mounts=mounts, pdk_root=pdk_root)
        state = arm / 'state_in.json'
        write_json(state, {'nl': str(scan_netlist.resolve())})
        folder = ll.run_chain(project, image, [('Yosys.Resynthesis', resolved, state)],
                              mounts=mounts, pdk_root=pdk_root,
                              namespace=f'ppa_post_dft/{slug}/run')[-1]
        report = arm / 'arm.json'
        ll.judge_step(folder, ['design__instance__area', 'design__instance__count'],
                      report, scope={'stage': 'post_dft', 'netlist': 'scan'})
        reports[strategy] = report
    return reports


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
