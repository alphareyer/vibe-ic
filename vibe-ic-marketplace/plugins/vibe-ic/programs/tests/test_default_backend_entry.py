"""Source CLI controls: native backend entries remain bounded and mode-specific.

No phase/tool is dispatched: every positive entry deliberately omits its exit.
"""
from pathlib import Path
import subprocess
import sys

import pytest

from _route_fixture import stage_owner_route


RUNNER = Path(__file__).resolve().parents[1] / "vibe_ic_one_shot_runner.py"


def observe(project, step, mode="default"):
    stage_owner_route(project, "ic")
    return subprocess.run(
        [sys.executable, str(RUNNER), str(project), "--route", "ic",
         "--execution-mode", mode, "--entry-step", step,
         "--no-dashboard", "--skip-hardware"],
        capture_output=True, text=True)


@pytest.mark.parametrize("step", ["32", "23", "35", "17", "15.5ic", "31"])
def test_supported_default_entry_reaches_required_exit_guard(tmp_path, step):
    result = observe(tmp_path, step)
    assert result.returncode == 2
    assert "needs --exit-step" in result.stderr, result.stdout + result.stderr
    assert "no runner can be entered" not in result.stderr


@pytest.mark.parametrize("step", ["999", "not_a_step", "36", "3"])
def test_unknown_or_non_native_span_keeps_entry_refusal(tmp_path, step):
    result = observe(tmp_path, step)
    assert result.returncode == 2
    assert "no runner can be entered" in result.stderr, result.stdout + result.stderr


def test_ultra_does_not_gain_midspan_entry(tmp_path):
    result = observe(tmp_path, "32", "ultra")
    assert result.returncode == 2
    assert "no runner can be entered" in result.stderr, result.stdout + result.stderr
    assert "needs --exit-step" not in result.stderr
    assert "[entry] step 32 ->" not in result.stdout


def test_other_runner_owner_is_not_reassigned_to_phase3(tmp_path):
    result = observe(tmp_path, "A2")
    assert result.returncode == 2
    assert "owned by analog_one_shot_runner" in result.stderr
    assert "[entry] step A2 -> phase3" not in result.stdout


def test_backend_window_authority_reads_the_shipped_canonical_row(monkeypatch):
    import _flow_yaml
    import execution_policy
    import phase3_one_shot_runner as phase3
    from _hostpaths import require_repo

    flow = _flow_yaml.load(require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "flow",
        "phase1_phase2_phase3.yaml"))
    row = next(item for item in flow["steps"] if str(item["id"]) == "32")
    assert row["stage"] == "stage3"
    monkeypatch.setattr(execution_policy, "request", lambda: {"mode": "default"})
    assert phase3._phase3_window_sites(str(row["id"]), str(row["id"])) == ["default_backend"]
    monkeypatch.setattr(execution_policy, "request", lambda: {"mode": "ultra"})
    assert phase3._phase3_window_sites(str(row["id"]), str(row["id"])) == ["enclosing_canonicalize"]
