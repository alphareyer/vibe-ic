"""Thin source-owned worker boundary; no scheduler or native/EDA admission."""
from pathlib import Path
from execution_frontend_providers import produce, ProviderResult

def run(step_id, project, **kwargs) -> ProviderResult:
    return produce(step_id, Path(project), **kwargs)
