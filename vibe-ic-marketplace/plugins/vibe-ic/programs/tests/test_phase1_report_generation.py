"""The phase1 main-mode report writes must carry the producer generation binding.

`run_second_pass_only` already bound its write; the docs-mode and prompt-mode
main branches of ``phase1_one_shot_runner`` wrote ``phase1_one_shot.json``
without ``_bind_runner_report``. A dispatched run (benchmark_dispatch sets
``VIBEIC_RUNNER_INVOCATION_CONTEXT``) therefore refused EVERY phase1-inclusive
problem at collect time with ``RUNNER_REPORT_UNBOUND: fresh report has no
producer generation binding`` — measured live on a 312-problem VerilogEval
clean-room run: 312/312 refused, zero candidates, zero backup handoffs.

This test drives the REAL docs-mode branch end to end through the same
neutral producer/consumer seam ``test_runner_report_generation`` uses.
"""
import json
from pathlib import Path
import sys

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import benchmark_dispatch as bd


def _fixture(tmp_path):
    project = tmp_path / "run/projects/neutral"
    docs = project / "input/docs"
    docs.mkdir(parents=True)
    (docs / "spec.txt").write_text(
        "Implement a module named neutral with one 1-bit input a and one 1-bit "
        "output y. The output must follow the input at all times.\n")
    (project / "input/phase1_prompt.md").write_text(
        "Implement a module named neutral with one 1-bit input a and one 1-bit "
        "output y. The output must follow the input at all times.\n")
    runner = PROGRAMS / "phase1_one_shot_runner.py"
    return project, runner


def test_docs_mode_report_carries_producer_generation_binding(tmp_path):
    project, runner = _fixture(tmp_path)
    argv = [sys.executable, str(runner), str(project),
            "--ic-name", "neutral", "--route", "ip", "--mode", "docs"]
    # The dispatcher only stamps the invocation context on the canonical
    # vibe_ic_one_shot_runner.py argv; phase1_one_shot_runner sees it as an
    # inherited child environment. Reproduce that handoff exactly.
    import os
    import subprocess
    context = {
        "invocation_id": "phase1binding" + "0" * 18,
        "argv": list(argv),
        "project": str(project.resolve()),
        "source": bd._runner_source_snapshot(argv),
    }
    env = dict(os.environ)
    env[bd._RUNNER_CONTEXT_ENV] = json.dumps(context, sort_keys=True)
    subprocess.run(argv, check=False, capture_output=True, text=True,
                   env=env, timeout=900)
    report_path = project / "reports/orchestrator/phase1_one_shot.json"
    assert report_path.is_file(), "docs-mode main branch wrote no report"
    report = json.loads(report_path.read_text())
    binding = report.get("runner_binding")
    # The unfixed writer emitted no binding at all, which the collect-time
    # check reports as RUNNER_REPORT_UNBOUND for every fresh report.
    assert isinstance(binding, dict), (
        "fresh phase1 docs-mode report has no producer generation binding")
    assert binding["schema"] == "vibeic.runner_report_binding.v1"
    assert binding["invocation_id"] == context["invocation_id"]
    assert binding["project"] == str(project.resolve())
    assert binding["report_name"] == "phase1_one_shot.json"
    assert binding["producer"]["path"] == str(runner.resolve())


def test_standalone_run_without_context_stays_unbound(tmp_path):
    project, runner = _fixture(tmp_path)
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k != bd._RUNNER_CONTEXT_ENV}
    subprocess.run(
        [sys.executable, str(runner), str(project),
         "--ic-name", "neutral", "--route", "ip", "--mode", "docs"],
        check=False, capture_output=True, env=env, timeout=900)
    report = json.loads(
        (project / "reports/orchestrator/phase1_one_shot.json").read_text())
    # Standalone producers have no dispatch generation and keep their format.
    assert "runner_binding" not in report
