"""The stated hermetic runner profile agrees with the runner that enforces it.

`hermetic_runner_profile.json` is a statement; `hermetic_candidate_runner.py`
is the code that starts the container.  Each clause below reads one field of
the statement through its one reader and requires the runner's own constant or
argv to say the same thing, so the statement cannot drift from what runs.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_CI = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", _CI / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P = _load("hermetic_runner_profile")
R = _load("hermetic_candidate_runner")
RUNNER_SRC = (_CI / "hermetic_candidate_runner.py").read_text(encoding="utf-8")


def test_the_profile_parses_with_exactly_its_keys():
    profile = P.load()
    assert set(profile) == P.PROFILE_KEYS
    assert profile["schema"] == 1


def test_the_profile_stores_no_image_identity():
    raw = P.PROFILE_PATH.read_text(encoding="utf-8")
    assert "image" not in P.load()
    assert "sha256:" not in raw
    assert re.search(r"\b[0-9a-f]{64}\b", raw) is None


def test_the_profile_refuses_a_stored_image(tmp_path):
    doc = P.load()
    doc["image"] = "ghcr.io/example/image@sha256:" + "0" * 64
    bad = tmp_path / "profile.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(P.Refusal, match="wrong keys"):
        P.load(bad)


def test_the_profile_refuses_duplicate_keys(tmp_path):
    bad = tmp_path / "profile.json"
    bad.write_text('{"schema": 1, "schema": 1}', encoding="utf-8")
    with pytest.raises(P.Refusal, match="duplicate"):
        P.load(bad)


def test_user_platform_and_workdir_are_the_runners():
    profile = P.load()
    assert profile["user"] == R.USER
    assert profile["platform"] == R.PLATFORM
    assert profile["workdir"] == R.WORKDIR


def test_tmpfs_mounts_are_the_runners():
    stated = {}
    for row in P.load()["tmpfs"]:
        destination, options = row.split(":", 1)
        stated[destination] = options
    assert stated == dict(R.CANDIDATE_TMPFS)


def test_isolation_flags_appear_in_the_runners_container_argv():
    profile = P.load()
    assert profile["engine"] == "docker"
    assert profile["network"] == "none" and '"--network", "none"' in RUNNER_SRC
    assert profile["read_only"] is True and '"--read-only"' in RUNNER_SRC
    assert profile["cap_drop"] == ["ALL"] and '"--cap-drop", "ALL"' in RUNNER_SRC
    assert profile["pull"] == "never" and '"--pull=never"' in RUNNER_SRC
    assert profile["security_opt"] == ["no-new-privileges:true"]
    assert '"no-new-privileges=true"' in RUNNER_SRC
    assert profile["progress_protocol"] == "VIBEIC_PROGRESS/1"
    assert R.PROGRESS_PREFIX == b"VIBEIC_PROGRESS "


def test_the_image_is_resolved_by_the_runner_not_read_from_the_profile(monkeypatch):
    digest = "sha256:" + "7" * 64
    monkeypatch.setattr(P, "_sibling", lambda name: type(
        "Runner", (), {"image_reference": staticmethod(
            lambda: f"ghcr.io/vibeic/vibeic-eda@{digest}")}))
    assert P.image().endswith("@" + digest)
    assert P.resolved()["image"].endswith("@" + digest)
