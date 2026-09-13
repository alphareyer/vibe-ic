"""The bounded Step-13 LEC policy is an explicit, identity-bound opt-in."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parent.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import canonical_run_admission as admission  # noqa: E402
import design_one_shot_runner as phase2  # noqa: E402
import vibe_ic_one_shot_runner as frontdoor  # noqa: E402


def test_frontdoor_forwards_only_an_explicit_bounded_lec_value():
    common = dict(top_name="spm", container="vibeic-eda",
                  max_rtl_repair_retries=3, skip_hardware=False,
                  skip_phase3=False, skip_analog=False,
                  entry_step=None, exit_step=None)
    unbounded = frontdoor._phase2_runner_argv(
        Path("/run/spm"), lec_max_completed_rungs=None, **common)
    assert "--lec-max-completed-rungs" not in unbounded

    bounded = frontdoor._phase2_runner_argv(
        Path("/run/spm"), lec_max_completed_rungs=3, **common)
    assert bounded == [
        "/run/spm", "--top-name", "spm", "--container", "vibeic-eda",
        "--max-rtl-repair-retries", "3", "--lec-max-completed-rungs", "3",
    ]


def test_phase2_forwards_only_the_step13_opt_in():
    common = dict(project=Path("/run/spm"),
                  lec_run=Path("/plugin/programs/lec_run.py"),
                  gate_netlist="phase2/stage2/synth/netlist.v", top_name="spm",
                  container="vibeic-eda")
    unbounded = phase2._lec_run_argv(
        lec_max_completed_rungs=None, **common)
    assert "--max-completed-rungs" not in unbounded

    bounded = phase2._lec_run_argv(lec_max_completed_rungs=7, **common)
    assert bounded == [
        sys.executable, "/plugin/programs/lec_run.py", "/run/spm",
        "--gold-rtl-dir", "phase2/stage1/rtl",
        "--gate-netlist", "phase2/stage2/synth/netlist.v", "--top", "spm",
        "--container", "vibeic-eda", "--json", "reports/lec.json",
        "--max-completed-rungs", "7",
    ]


def test_bounded_lec_cli_value_must_be_positive_at_both_entries():
    for parser_type in (frontdoor._positive_completed_rung_cap,
                        phase2._positive_completed_rung_cap):
        assert parser_type("1") == 1
        for invalid in ("0", "-1", "not-an-int"):
            try:
                parser_type(invalid)
            except argparse.ArgumentTypeError:
                pass
            else:  # pragma: no cover - assertion makes the contract explicit
                raise AssertionError(f"accepted invalid completed-rung cap: {invalid}")


def test_bounded_lec_policy_changes_canonical_phase2_identity(tmp_path):
    project = tmp_path / "spm"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("serial multiplier\n")
    programs = [PROGRAMS / "design_one_shot_runner.py"]
    common = dict(container_image="sha256:" + "a" * 64,
                  program_paths=programs)
    unbounded = admission.admit(
        project, "phase2", config={"top_name": "spm",
                                    "lec_max_completed_rungs": None}, **common)
    bounded = admission.admit(
        project, "phase2", config={"top_name": "spm",
                                    "lec_max_completed_rungs": 2}, **common)
    assert unbounded.admitted and bounded.admitted
    assert unbounded.identity_sha256 != bounded.identity_sha256
    assert bounded.identity["dispatch_config"]["lec_max_completed_rungs"] == 2
