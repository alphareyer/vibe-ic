"""A6/A7/A8 caller wiring into the existing fixed-step dispatcher."""

# `programs/` is a flat directory whose modules import each other by bare name
# (vibe-ic#2104): restore the condition a by-path load does not provide.
import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pathlib import Path
import execution_policy as policy
import execution_modes as em

STEPS = {'A6_block_pv': 'A6', 'A7_post_layout_resim': 'A7', 'A8_hardmacro_gen': 'A8'}


def dispatch_site(project: Path, block: dict, step_name: str) -> dict | None:
    """Default is inert; Ultra uses its existing live Registry and Controller."""
    step = STEPS.get(step_name)
    if step is None or policy._ordinary_runtime is None:
        return None
    runtime = policy._ordinary_runtime
    if Path(project).resolve() != runtime['project']:
        raise em.Refusal('ORDINARY_PROJECT_UNBOUND', str(project))
    # The fixed row consumes the whole current declaration. A per-block caller
    # cannot reinterpret a different/edited row as the same invocation.
    from execution_adapters_analog import _blocks, declaration_path
    names = _blocks(runtime['project'], declaration_path(runtime['project']))
    if block.get('name') not in names:
        raise em.Refusal('ANALOG_BLOCK_INPUT_UNBOUND', str(block.get('name')))
    return policy.dispatch_fixed_step(project, step)
