"""Step 9 source-owned mapping providers; registration is not qualification.

BLOCKING at prepare, producer consumption and import. Supplied source/image
facts establish one Yosys+ABC mapping engine. No arbitrary command, wrapper,
version probe or fixture can register a second independent native producer.
"""
from __future__ import annotations

import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from dataclasses import dataclass
import json

import execution_modes as em


@dataclass(frozen=True)
class SynthesisEngine:
    arm_id: str
    tool_id: str
    mapping_family: str
    engine_families: tuple[str, ...]
    native_entrypoint: str
    provenance_tool: str
    native_steps: tuple[str, ...]

    def contract(self) -> dict:
        return dict(schema=1, arm_id=self.arm_id, tool_id=self.tool_id,
                    mapping_family=self.mapping_family,
                    engine_families=list(self.engine_families),
                    native_entrypoint=self.native_entrypoint,
                    provenance_tool=self.provenance_tool,
                    native_steps=list(self.native_steps),
                    receipt_contract='librelane-mapped-synthesis-v1')


LIBRELANE = SynthesisEngine(
    'librelane-mapped-synthesis', 'librelane', 'yosys+abc', ('yosys', 'abc'),
    'phase3_one_shot_runner._step_synth_librelane', 'yosys',
    ('Yosys.JsonHeader', 'Yosys.Synthesis', 'Checker.YosysUnmappedCells',
     'Checker.YosysSynthChecks', 'Checker.NetlistAssignStatements'))


def population() -> dict:
    """Source routes only; actual image admission remains in prepare()."""
    return dict(schema=1, providers=[LIBRELANE.contract()],
                independent_mapping_families=[LIBRELANE.mapping_family],
                source_provider_count=1, native_qualification='NOT_MEASURED',
                same_engine_wrappers=['direct', 'librelane', 'slang'],
                unavailable_independent_engine=dict(
                    state='NOT_MEASURED', reason='SECOND_INDEPENDENT_ENGINE_UNAVAILABLE',
                    missing=['independent RTL-to-mapped-netlist engine identity',
                             'source-owned synthesis entrypoint and exact native command',
                             'current RTL/top/PDK/liberty input mapping',
                             'usable licence entitlement and aggregate licence cost',
                             'native image/tool/source identity qualification',
                             'mapped-netlist/stat/state/checker/provenance output contract',
                             'engine-specific primary consumer and canonical importer']))


def require_engine(arm_id: str = LIBRELANE.arm_id) -> SynthesisEngine:
    if arm_id != LIBRELANE.arm_id:
        raise em.Refusal('PRODUCTION_SYNTH_ENGINE_UNAVAILABLE', json.dumps(
            dict(requested=arm_id, **population()), sort_keys=True))
    return LIBRELANE


def lease_reservation(quota: dict) -> dict:
    """Reserve the real host + native-container total for each synthesis arm.

    This lease has no issued per-arm quota. The native boundary gives a
    container the full container_ram_mb, so charging only host_ram_mb would
    undercount concurrent arms. Reserving ram_mb serializes them within the
    existing aggregate scheduler; no worker/container limit is rewritten.
    """
    fields = ('cpus', 'ram_mb', 'host_ram_mb', 'container_ram_mb')
    if (any(type(quota.get(name)) is not int or quota[name] <= 0 for name in fields)
            or quota['host_ram_mb'] + quota['container_ram_mb'] != quota['ram_mb']):
        raise em.Refusal('PRODUCTION_SYNTH_LEASE_BUDGET_UNBOUND', repr(quota))
    return dict(cpus=quota['cpus'], ram_mb=quota['ram_mb'])


def require_spec(spec: dict) -> SynthesisEngine:
    """Only a source-owned implemented receipt contract reaches a consumer."""
    contract = spec.get('synthesis_engine')
    if not isinstance(contract, dict):
        raise em.Refusal('PRODUCTION_SYNTH_ENGINE_CONTRACT_MISSING', 'request.json')
    engine = require_engine(contract.get('arm_id'))
    if contract != engine.contract():
        raise em.Refusal('PRODUCTION_SYNTH_ENGINE_CONTRACT_CHANGED', engine.arm_id)
    return engine


def consume_producer(spec: dict, producer: dict) -> SynthesisEngine:
    engine = require_spec(spec)
    if (producer.get('synthesis_engine') != engine.contract()
            or producer.get('native_entrypoint') != engine.native_entrypoint):
        raise em.Refusal('PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED', engine.arm_id)
    # The existing full state/stat/checker and primary consumers still decide
    # PASS. This identity check cannot turn FAIL/NM into a measured generation.
    return engine
