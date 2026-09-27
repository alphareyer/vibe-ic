#!/usr/bin/env python3
"""The hermetic landing runner's profile: the one reader of its JSON.

`hermetic_runner_profile.json` states the container profile every landing arm
runs under -- engine, platform, user, network, read-only root, dropped
capabilities, tmpfs mounts, read-only subject/runtime/corpus mounts and the
progress protocol.  `hermetic_candidate_runner.py` is what enforces it; this
file is what reads the statement of it, and `test_hermetic_runner_profile.py`
binds the two.

The profile used to be the `runner` block of the protected-path byte register
that the two-step landing (PREPARE, then ACTIVATE) kept; the owner removed that
register and its procedure on 2026-09-27.  It
moved here without its `image`: the register stored an image digest, and image
identity is resolved at run time (owner, 2026-09-17), so `image()` asks the
runner's resolver and nothing here records a digest or a version.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).resolve().with_name("hermetic_runner_profile.json")
PROFILE_KEYS = frozenset({
    "schema", "profile_id", "engine", "platform", "user", "network",
    "read_only", "cap_drop", "security_opt", "tmpfs", "pull", "workdir",
    "subject_mount", "runtime_mount", "corpus_mount", "input_mounts",
    "runtime_overlays", "process_environment", "progress_protocol",
    "evidence_transport",
})


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vibeic_profile_{name}", Path(__file__).resolve().with_name(f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"{name} is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_primitives = _sibling("landing_record_primitives")
Refusal = _primitives.Refusal


def load(path: Path = PROFILE_PATH) -> dict[str, Any]:
    """The stated profile, strictly parsed, with exactly the known keys."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Refusal(f"cannot read the hermetic runner profile: {exc}") from exc
    value = _primitives.strict_loads(raw, what="hermetic runner profile")
    if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise Refusal(f"hermetic runner profile has the wrong keys: {got!r}")
    return value


def image() -> str:
    """The image a landing arm runs, resolved on this host when asked."""
    return _sibling("hermetic_candidate_runner").image_reference()


def resolved(path: Path = PROFILE_PATH) -> dict[str, Any]:
    """The stated profile plus the image resolved now."""
    return {**load(path), "image": image()}


if __name__ == "__main__":
    import json
    print(json.dumps(load(), indent=2, sort_keys=True))
