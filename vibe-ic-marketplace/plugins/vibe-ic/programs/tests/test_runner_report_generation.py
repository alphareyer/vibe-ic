"""Original runner closure at actual neutral producer/consumer boundaries."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import benchmark_dispatch as bd
import benchmark_io_adapter as bio

WIRE = "module neutral(input a,output y); assign y=a; endmodule\n"


def _fixture(tmp_path):
    project = tmp_path / "run/projects/neutral"
    prompt = project / "input/phase1_prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Implement neutral wire-through module with input a and output y.\n")
    bio._stage_public_original("neutral", prompt.read_text(), {}, project)
    rtl = project / "phase2/stage1/rtl/neutral.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text(WIRE)
    runner = tmp_path / "vibe_ic_one_shot_runner.py"
    runner.write_text('''import json, pathlib, sys
sys.path.insert(0, %r)
import design_one_shot_runner as producer
p = pathlib.Path(sys.argv[1]).resolve()
mode = sys.argv[2]
q = p/'reports/orchestrator/phase2_one_shot.json'
summary = {'project':str(p), 'verdict':'NOT_MEASURED',
           'steps':[{'name':'rtl_gen','status':'PASS','detail':'neutral current producer'}]}
if mode == 'generate':
    (p/'phase2/stage1/rtl/neutral.v').write_text(%r)
producer._write_phase2_report(q, summary, p)
if mode in {'generation','source','options','material','drop'}:
    doc = json.loads(q.read_text())
    if mode == 'drop':
        del doc['runner_binding']
        doc['detail'] = 'changed payload with omitted native generation'
    elif mode == 'generation':
        doc['runner_binding']['invocation_id'] = 'prior-generation'
    elif mode == 'source':
        doc['runner_binding']['source']['runner']['sha256'] = '0'*64
    elif mode == 'options':
        doc['runner_binding']['argv'].append('--other-window')
    else:
        doc['runner_binding']['material']['input'] = {}
    q.write_text(json.dumps(doc))
print('neutral completed stdout')
print('REFUSED: NEUTRAL_DOWNSTREAM after current report', file=sys.stderr)
raise SystemExit(1)
''' % (str(PROGRAMS), WIRE))
    return project, runner


def _run(project, runner, mode="fresh"):
    argv = [sys.executable, str(runner), str(project), mode]
    process = bd._RunnerBudget(1, 1, 1).run(argv)
    return process, bd._collect_runner_result(process, argv, "neutral", "neutral", project)


def _task(tmp_path, project, process, got, **kwargs):
    return bd._make_ai_review_task("neutral", project, got, {}, process.rc,
                                   tmp_path / "run", "PROGRAM",
                                   runner_invocation=process.invocation, **kwargs)


@pytest.mark.parametrize("mode", ["fresh", "generate"])
def test_native_phase2_write_seam_binds_generation_source_options_and_material(tmp_path, mode):
    project, runner = _fixture(tmp_path)
    if mode == "generate":
        (project / "phase2/stage1/rtl/neutral.v").unlink()
    process, got = _run(project, runner, mode)
    assert process.rc == 1 and process.error is None and got["ok"] is True
    report = json.loads((project / "reports/orchestrator/phase2_one_shot.json").read_text())
    binding = report["runner_binding"]
    assert binding["invocation_id"] == process.invocation_id
    assert binding["argv"] == process.invocation["argv"]
    assert binding["source"] == process.invocation["source_before"]
    assert binding["material"] == process.invocation["material_after"]
    assert binding["producer"]["path"] == str(PROGRAMS / "design_one_shot_runner.py")
    task = _task(tmp_path, project, process, got)
    assert bd._runner_reentry_reason(task, {}) is None
    if mode == "generate":
        assert process.invocation["material_before"]["output_rtl"] == {}


@pytest.mark.parametrize("mode", ["generation", "source", "options", "material"])
def test_native_report_cannot_claim_another_generation_or_subject(tmp_path, mode):
    project, runner = _fixture(tmp_path)
    process, got = _run(project, runner, mode)
    assert process.rc == 1 and process.error is None
    assert got["ok"] is False and "completion" not in got
    assert got["reason"].startswith("RUNNER_REPORT_UNBOUND:")


def test_native_protocol_cannot_be_dropped_on_a_later_report(tmp_path):
    project, runner = _fixture(tmp_path)
    first, good = _run(project, runner)
    assert good["ok"] is True
    second, bad = _run(project, runner, "drop")
    assert first.invocation_id != second.invocation_id
    assert bad["ok"] is False and "dropped its producer generation" in bad["reason"]


@pytest.mark.parametrize("explicit_keys", [False, True])
def test_same_rtl_fresh_runs_have_distinct_bound_tasks_and_immutable_history(tmp_path, explicit_keys):
    project, runner = _fixture(tmp_path)
    first, got = _run(project, runner)
    kwargs = {"review_key": "retry", "archive_key": "retry"} if explicit_keys else {}
    task1 = _task(tmp_path, project, first, got, **kwargs)
    archive1 = Path(task1["candidate_snapshot"]["manifest_path"]).parent
    old = {p: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in archive1.rglob("*") if p.is_file()}
    second, got2 = _run(project, runner)
    task2 = _task(tmp_path, project, second, got2, **kwargs)
    assert task1["rtl_sha256"] == task2["rtl_sha256"]
    assert task1["review_path"] != task2["review_path"]
    assert task1["candidate_snapshot"]["manifest_path"] != task2["candidate_snapshot"]["manifest_path"]
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in old.items())
    assert bd._runner_reentry_reason(task1, {}) is not None
    assert bd._runner_reentry_reason(task2, {}) is None


def test_actual_runner_bytes_and_frozen_options_are_checked_on_reentry(tmp_path):
    project, runner = _fixture(tmp_path)
    process, got = _run(project, runner)
    task = _task(tmp_path, project, process, got)
    receipt = Path(process.receipt_path)
    old = receipt.read_bytes()
    assert bd._runner_reentry_reason(task, {}) is None
    task["program_verification"]["runner_argv"].append("--different-window")
    assert "options differ" in bd._runner_reentry_reason(task, {})
    task["program_verification"]["runner_argv"].pop()
    runner.write_text("import sys\nraise SystemExit(2)\n")
    assert "RUNNER_SOURCE_UNBOUND" in bd._runner_reentry_reason(task, {})
    assert receipt.read_bytes() == old


def test_standalone_native_writer_retains_its_existing_format(tmp_path, monkeypatch):
    import design_one_shot_runner as producer
    monkeypatch.delenv(bd._RUNNER_CONTEXT_ENV, raising=False)
    out = tmp_path / "reports/orchestrator/phase2_one_shot.json"
    expected = {"project": str(tmp_path), "verdict": "NOT_MEASURED", "steps": []}
    producer._write_phase2_report(out, expected, tmp_path)
    assert json.loads(out.read_text()) == expected
