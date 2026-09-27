"""tools/conftest.py — the consistency tier for the repo-level tools/ tests.

`tools/gatekeeper-land.sh::run_repo_tools_pytest` runs these tests with the
REPO root as cwd, where the plugin-root `conftest.py` (which loads
`programs/consistency_tier.py` through `pytest_plugins`) is not in scope. A
`pytest_plugins` line here would be a non-root conftest declaring plugins,
which pytest refuses; so the same module's hooks are re-exported by name
instead. One implementation, two entry points — never a second copy of the
rule: tests marked `@pytest.mark.consistency` are DESELECTED unless
VIBEIC_RUN_CONSISTENCY=1 (the x.y.0 FULL cadence, or the owner's request).
"""
import importlib.util
import sys
from pathlib import Path

_TIER = (Path(__file__).resolve().parent.parent / "vibe-ic-marketplace" / "plugins"
         / "vibe-ic" / "programs" / "consistency_tier.py")
_spec = importlib.util.spec_from_file_location("consistency_tier", _TIER)
_tier = sys.modules.get("consistency_tier")
if _tier is None:
    _tier = importlib.util.module_from_spec(_spec)
    sys.modules["consistency_tier"] = _tier
    _spec.loader.exec_module(_tier)

pytest_collection_modifyitems = _tier.pytest_collection_modifyitems
pytest_terminal_summary = _tier.pytest_terminal_summary


# 2026-09-27 (owner) — the outcome-state tier, the same one module the plugin-root
# conftest loads through `pytest_plugins` (`programs/_outcome_states.py`). The
# tools/ci tests have the same classes of non-red failure — a docker-less or
# tool-less host, a timing measurement under load — so they report the same
# states. Registered, not re-exported by name: its `pytest_terminal_summary`
# would otherwise collide with the consistency tier's above. `pytest_configure`
# is a historic hook, so a plugin registered here still receives it.
_STATES_PATH = _TIER.parent / "_outcome_states.py"


def pytest_configure(config):
    _tier.pytest_configure(config)
    if config.pluginmanager.has_plugin("_outcome_states"):
        return
    states = sys.modules.get("_outcome_states")
    if states is None:
        spec = importlib.util.spec_from_file_location("_outcome_states", _STATES_PATH)
        states = importlib.util.module_from_spec(spec)
        sys.modules["_outcome_states"] = states
        spec.loader.exec_module(states)
    config.pluginmanager.register(states, "_outcome_states")
