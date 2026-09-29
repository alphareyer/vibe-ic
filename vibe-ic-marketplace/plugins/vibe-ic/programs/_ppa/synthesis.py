"""LibreLane synthesis exploration evidence and post-route arm selection.

Synthesis and pre-PnR STA are proxy stages. Final selection consumes routed
measurements at identical scope and requires a proved LEC for each candidate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librelane_contract as ll  # noqa: E402
from _atomic_artefact import write_json  # noqa: E402


STRATEGIES = tuple(f"AREA {i}" for i in range(4)) + tuple(
    f"DELAY {i}" for i in range(5))
CLOCK_GATE_WIDTHS = (None, 4, 8, 16)


def strategy_config(base: dict, strategy: str, clock_gate_width: int | None = None) -> dict:
    if strategy not in STRATEGIES:
        raise ll.Refusal('LL_SYNTH_STRATEGY_INVALID', strategy)
    if clock_gate_width not in CLOCK_GATE_WIDTHS:
        raise ll.Refusal('LL_CLOCK_GATE_WIDTH_INVALID', str(clock_gate_width))
    config = dict(base)
    config['SYNTH_STRATEGY'] = strategy
    if clock_gate_width is not None:
        config['SYNTH_CLOCKGATE_MIN_WIDTH'] = clock_gate_width
    return config


def run_exploration(project: Path, image: str, base_config: dict,
                    header_state: Path, *, mounts: list[tuple[Path, str]] | None = None,
                    pdk_root: str,
                    clock_gate_width: int | None = None,
                    strategies: tuple[str, ...] = STRATEGIES) -> dict[str, Path]:
    """Run LibreLane's nine strategy arms through STAPrePNR; retain all arms."""
    reports: dict[str, Path] = {}
    if not strategies or any(strategy not in STRATEGIES for strategy in strategies):
        raise ll.Refusal('LL_SYNTH_STRATEGY_INVALID', str(strategies))
    root = project / 'phase3/librelane/ppa_synthesis'
    if clock_gate_width is not None:
        root /= f'clock_gate_{clock_gate_width}'
    for strategy in strategies:
        slug = strategy.lower().replace(' ', '_')
        arm_root = root / slug
        arm_root.mkdir(parents=True, exist_ok=True)
        raw = strategy_config(base_config, strategy, clock_gate_width)
        steps = []
        for step_id, name in [('Yosys.Synthesis', 'synthesis'),
                              ('OpenROAD.CheckSDCFiles', 'check_sdc'),
                              ('OpenROAD.STAPrePNR', 'sta')]:
            source = arm_root / f'{name}_input.json'
            resolved = arm_root / f'{name}_resolved.json'
            write_json(source, {**raw, 'meta': {'step': step_id}})
            ll.resolve_step_config(project, image, source, resolved,
                                   mounts=mounts, pdk_root=pdk_root)
            steps.append((step_id, resolved, header_state))
        namespace = f'ppa_synthesis/{slug}'
        if clock_gate_width is not None:
            namespace = f'ppa_synthesis/clock_gate_{clock_gate_width}/{slug}'
        folders = ll.run_chain(project, image, steps, mounts=mounts,
                               pdk_root=pdk_root, namespace=namespace)
        report = arm_root / 'pre_pnr.json'
        ll.judge_step(folders[-1], ['design__instance__area',
                                    'timing__setup__ws', 'timing__setup__tns'],
                      report, scope={'stage': 'pre_pnr', 'strategy': strategy})
        reports[strategy] = report
    return reports


def select_postroute(arms: dict[str, Path], output: Path,
                     *, require_power: bool = False) -> dict:
    """Admit only routed, same-scope, LEC-proved and area-gated candidates."""
    if not arms:
        result = {'selection': 'UNDETERMINED', 'reason': 'LL_NO_ROUTED_ARMS'}
        write_json(output, result)
        return result
    docs = {}
    for name, path in arms.items():
        doc = json.loads(path.read_text())
        docs[name] = doc
        scope = doc.get('scope', {})
        if scope.get('stage') != 'post_route' or doc.get('lec') != 'PROVEN' or \
                doc.get('area_budget') != 'PASS':
            result = {'selection': 'UNDETERMINED', 'reason': 'LL_POSTROUTE_FEASIBILITY',
                      'arm': name}
            write_json(output, result)
            return result
    objectives = {'timing__setup__ws': 'max',
                  'timing__setup__tns': 'max',
                  'design__instance__area': 'min'}
    has_power = all(doc.get('metrics', {}).get('power__total', {}).get('status') == 'MEASURED'
                    for doc in docs.values())
    if require_power and not has_power:
        result = {'selection': 'UNDETERMINED', 'reason': 'LL_POSTROUTE_POWER_NOT_MEASURED'}
        write_json(output, result)
        return result
    if has_power:
        objectives['power__total'] = 'min'
    return ll.select_arms(arms, objectives, output)
