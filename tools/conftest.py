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

pytest_configure = _tier.pytest_configure
pytest_collection_modifyitems = _tier.pytest_collection_modifyitems
pytest_terminal_summary = _tier.pytest_terminal_summary
