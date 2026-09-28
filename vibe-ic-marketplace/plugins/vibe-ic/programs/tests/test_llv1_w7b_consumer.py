"""llv1 W7b: the runners' consumer mode for an external flow (`--librelane`).

Piece 1 -- Phase 2. Under the flag, `design_one_shot_runner` runs steps 1-8
unchanged and does not dispatch step 9 (`step_yosys_synth`) or the DFT/LEC chain
(11-13): LibreLane segment 1 synthesizes in phase 3 and vibe-ic runs 11-14
between the segments. The two sites are pruned by the SAME exit machinery a
declared `--exit-step 8` uses, each booked NOT_APPLICABLE with the flag that
pruned it and the phase that runs it. Without the flag nothing changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402
import step_preflight as SPF  # noqa: E402


def _d():
    import design_one_shot_runner as D
    return D


def test_phase2_under_the_flag_is_the_window_steps_1_to_8():
    D = _d()
    sites = SPF.RUNNER_PLANS["design_one_shot_runner"].sites
    pruned = D._exit_pruned_sites(sites, IF.CONSUMER_PHASE2_LAST_STEP)
    assert pruned == ["yosys_synth", "dft_lec_chain"]
    kept = [n for n, _ in sites if n not in pruned]
    assert kept == ["rtl_gen", "rtl_validate", "sim"]
    assert D.run_is_bounded(None, pruned, [n for n, _ in sites])


def test_the_design_runner_is_wired_and_the_mode_comes_from_the_flag():
    assert "design_one_shot_runner" in IF.WIRED_RUNNERS
    assert IF.consumer_mode(SimpleNamespace(librelane=True, orfs=False)) == \
        IF.IMPL_LIBRELANE
    assert IF.consumer_mode(SimpleNamespace(librelane=False, orfs=False)) is None


def test_the_sentinel_names_the_flag_and_the_phase():
    d = IF.consumer_sentinel_detail(IF.IMPL_LIBRELANE, "yosys_synth", ("9",))
    assert "--librelane" in d and "phase 3" in d and "9" in d
    assert "not missing" in d


class _Captured(Exception):
    def __init__(self, plan):
        super().__init__("captured")
        self.plan = list(plan)


def _project(p: Path) -> Path:
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, 14):
        (gd / f"L{i}_X.json").write_text("{}")
    (p / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    (p / "phase2" / "stage1" / "rtl" / "top.v").write_text(
        "module top(input clk); endmodule\n")
    return p


def _drive(monkeypatch, project: Path, *argv):
    """The real design main, every step stubbed PASS, captured at the step
    right after the synthesis site (the synth-log audit reads its result)."""
    D = _d()
    for name in dir(D):
        if name.startswith("step_") and callable(getattr(D, name)):
            monkeypatch.setattr(D, name, (lambda n: lambda *a, **k:
                                          D.StepResult(n, "PASS", 0.0, "stub"))(name))

    def _gate(project, runner, site, refusal, fn, *a, **k):
        return D.StepResult(site, "PASS", 0.0, "stub-gate")

    monkeypatch.setattr(D._spf, "gate", _gate)

    monkeypatch.setattr(D, "step_synth_log_audit",
                        lambda project, last: (_ for _ in ()).throw(
                            _Captured([last])))
    monkeypatch.setattr(D._canonical_admission, "admit_span",
                        lambda *a, **k: SimpleNamespace(
                            admitted=True, reason="ADMITTED", detail=""))
    monkeypatch.setattr(sys, "argv", ["design_one_shot_runner", str(project),
                                      *argv])
    with pytest.raises(_Captured) as ei:
        D.main()
    return ei.value.plan[0]


def test_the_real_phase2_main_does_not_dispatch_synthesis_under_the_flag(
        monkeypatch, tmp_path):
    flagged = _drive(monkeypatch, _project(tmp_path / "f"), "--librelane")
    assert flagged.name == "yosys_synth"
    assert flagged.status == "NOT_APPLICABLE"
    assert flagged.declared_by == "--librelane"
    assert "phase 3" in flagged.detail


def test_without_the_flag_synthesis_is_dispatched_as_before(monkeypatch,
                                                            tmp_path):
    default = _drive(monkeypatch, _project(tmp_path / "d"))
    assert (default.name, default.status, default.detail) == (
        "yosys_synth", "PASS", "stub-gate")


def test_the_flag_is_one_of_the_windows_declared_flags():
    D = _d()
    assert D.declared_window_flags(None, None, "--librelane") == ("--librelane",)
    assert D.declared_window_flags(None, "4", "--librelane") == (
        "--exit-step 4", "--librelane")
    assert D.declared_window_flags(None, None) == ()


def test_refresh_only_under_the_flag_stays_honoured(monkeypatch, tmp_path):
    """KNOBS declares --refresh-only HONOURED under the flag: it runs no step.
    The flag is a window flag, but it must not trip the refusal that an
    operator-declared --entry-step/--exit-step earns."""
    D = _d()
    assert IF.KNOBS["design_one_shot_runner"]["refresh_only"][0] == IF.HONOURED
    project = _project(tmp_path / "r")
    monkeypatch.setattr(D, "_run_refresh_only", lambda *a, **k: 17)
    monkeypatch.setattr(sys, "argv", ["design_one_shot_runner", str(project),
                                      "--librelane", "--refresh-only"])
    assert D.main() == 17
