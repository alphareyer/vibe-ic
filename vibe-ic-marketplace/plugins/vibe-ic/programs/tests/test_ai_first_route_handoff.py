"""AI route choice must precede the ordinary design runner for every input."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import benchmark_dispatch as bd  # noqa: E402
import task_nature_route as tnr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _runtime_pair_fixture as runtime_pair  # noqa: E402
from _hostpaths import require_repo  # noqa: E402


@pytest.fixture(autouse=True)
def _matching_pair(monkeypatch):
    runtime_pair.assume_matching_runtime_pair(monkeypatch)


PROMPT = ("Design a pulse stretcher named top_module. Its input pulse_in "
          "must be reflected at pulse_out for two clock cycles.")


def _dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "generic_pulse_prompt.txt").write_text(PROMPT)
    return dataset


def _task(run: Path) -> dict:
    rows = bd._read_jsonl(run / bd._ROUTE_WORKLIST)
    assert len(rows) == 1
    return rows[0]


def _answer(task: dict, nature: str, disposition: str = "CONFIRM") -> dict:
    return {
        "schema": bd._AI_ROUTE_SCHEMA,
        "id": task["id"],
        "task_sha256": task["task_sha256"],
        "prompt_sha256": task["prompt_sha256"],
        "source_sha256": task["public_original_input"].get("source_sha256"),
        "routing_contract_sha256": task["routing_contract_sha256"],
        "disposition": disposition,
        "ai_nature": nature,
        "author": {"kind": "AI", "model": "general-review-model"},
        "blind": {"oracle_accessed": False},
        "rationale": "The supplied words describe the requested design task.",
        "prompt_evidence": [{
            "excerpt": "Design a pulse stretcher named top_module",
            "supports": "The user requests a new pulse-stretcher module.",
        }],
    }


def _write_answer(task: dict, answer: dict) -> None:
    path = Path(task["response_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer))


def test_no_ai_route_means_no_design_runner(tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    calls = []
    monkeypatch.setattr(bd._RunnerBudget, "run",
                        lambda *_args: calls.append(1) or bd._ProcessOutcome(rc=1))

    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []  # pre-fix control fails by value: a runner was called.
    task = _task(run)
    assert task["program_proposal"]["needs_ai_parse"] is True
    assert bd._read_jsonl(run / bd._BACKUP_WORKLIST) == []
    assert bd._read_jsonl(run / bd._REVIEW_WORKLIST) == []
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []
    assert json.loads((run / "solve_report.json").read_text())["routing_phase"] == "PENDING"


@pytest.mark.parametrize("tamper", ["prompt_hash", "model", "oracle", "nature"])
def test_invalid_ai_route_still_launches_no_runner(tmp_path, monkeypatch, tamper):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    calls = []
    monkeypatch.setattr(bd._RunnerBudget, "run", lambda *_args: calls.append(1))
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    answer = _answer(task, task["program_proposal"]["entry_nature"])
    if tamper == "prompt_hash":
        answer["prompt_sha256"] = "0" * 64
    elif tamper == "model":
        answer["author"]["model"] = "unknown"
    elif tamper == "oracle":
        answer["blind"]["oracle_accessed"] = True
    else:
        answer["ai_nature"] = "not_a_product_route"
    _write_answer(task, answer)
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []
    assert json.loads((run / "solve_report.json").read_text())["routing_phase"] == "PENDING"


def test_confirmed_ai_route_enters_only_its_derived_product_path(tmp_path,
                                                                   monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    calls = []

    def fake_runner(_budget, argv):
        calls.append(argv)
        return bd._ProcessOutcome(rc=1)

    monkeypatch.setattr(bd._RunnerBudget, "run", fake_runner)
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    _write_answer(task, _answer(task, "spec_generation"))
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert len(calls) == 1
    assert "--entry-step" not in calls[0]  # D1 is the normal front door.
    assert calls[0][calls[0].index("--exit-step") + 1] == "2"
    assert bd._read_jsonl(run / bd._BACKUP_WORKLIST)[0]["skill"] == "spec-to-rtl"
    result = json.loads((run / "solve_report.json").read_text())["results"][0]
    assert result["routing_verdict"]["source"] == "ai_confirmed"
    assert result["accepted"] is False


def test_changed_staged_prompt_invalidates_ai_route(tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    calls = []
    monkeypatch.setattr(bd._RunnerBudget, "run", lambda *_args: calls.append(1))
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    _write_answer(task, _answer(task, "spec_generation"))
    Path(task["prompt_path"]).write_text(PROMPT + " changed")
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []


def test_ai_override_selects_derived_midflow_entry(tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    (dataset / "generic_pulse_prompt.txt").write_text(
        "Fix the incorrect output inversion in this supplied module: "
        "module top_module(input wire a, output wire y); "
        "assign y = ~a; endmodule")
    original = tnr.classify_task_nature
    monkeypatch.setattr(tnr, "classify_task_nature", lambda *_a: {
        **original(PROMPT, False, "spec_generation"),
        "source": "synthetic_misroute_control"})
    calls = []
    frontdoors = []
    monkeypatch.setattr(bd, "_ensure_phase1_frontdoor",
                        lambda *_a: frontdoors.append(1) or {
                            "status": "GENERATED", "provenance": {"ran": True}})

    def fake_runner(_budget, argv):
        calls.append(argv)
        return bd._ProcessOutcome(rc=1)

    monkeypatch.setattr(bd._RunnerBudget, "run", fake_runner)
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    answer = _answer(task, "debug", "OVERRIDE")
    answer["rationale"] = (
        "The visible prompt supplies a complete module and asks to fix its "
        "incorrect inversion. Debugging that supplied RTL is the correct "
        "existing-design route, not generation from an absent design. " * 2)
    # The OVERRIDE is grounded in THIS prompt (the generic helper's excerpt
    # is from PROMPT, which this dataset replaced); length alone never was.
    answer["prompt_evidence"] = [{
        "excerpt": "Fix the incorrect output inversion in this supplied module",
        "supports": "Repairing the supplied module is the debug route.",
    }]
    _write_answer(task, answer)
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert frontdoors == [1]
    assert len(calls) == 1
    assert calls[0][calls[0].index("--entry-step") + 1] == "4"
    assert calls[0][calls[0].index("--exit-step") + 1] == "4"
    result = json.loads((run / "solve_report.json").read_text())["results"][0]
    assert result["routing_verdict"]["source"] == "ai_override"
    assert result["routing_verdict"]["nature"] == "debug"


def test_route_table_drift_refuses_before_runner(tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    calls = []
    monkeypatch.setattr(bd._RunnerBudget, "run", lambda *_a: calls.append(1))
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    _write_answer(task, _answer(task, "spec_generation"))
    monkeypatch.setitem(tnr.NATURE_ENTRY["spec_generation"],
                        "entry_step", "2")
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []


def test_route_confirmation_does_not_unlock_score(tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    monkeypatch.setattr(bd._RunnerBudget, "run",
                        lambda *_a: bd._ProcessOutcome(rc=1))
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    task = _task(run)
    _write_answer(task, _answer(task, "spec_generation"))
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    with pytest.raises(SystemExit, match="acceptance status"):
        bd._require_program_first_ai_acceptance(run)


def test_batch_waits_for_every_route_then_resumes_without_restaging(
        tmp_path, monkeypatch):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    (dataset / "generic_second_prompt.txt").write_text(
        "Design a second generic output buffer with one input and one output.")
    calls = []

    def fake_runner(_budget, argv):
        calls.append(argv)
        return bd._ProcessOutcome(rc=1)

    monkeypatch.setattr(bd._RunnerBudget, "run", fake_runner)
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    tasks = bd._read_jsonl(run / bd._ROUTE_WORKLIST)
    assert len(tasks) == 2
    staged = {task["id"]: Path(task["prompt_path"]).read_bytes()
              for task in tasks}
    _write_answer(tasks[0], _answer(tasks[0], "spec_generation"))
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []
    assert json.loads((run / "solve_report.json").read_text())["routing_phase"] == "PENDING"
    second = _answer(tasks[1], "spec_generation")
    second["prompt_evidence"] = []
    second["rationale"] = "This input requests a new buffer implementation. " * 4
    _write_answer(tasks[1], second)
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert len(calls) == 2
    assert {task["id"]: Path(task["prompt_path"]).read_bytes()
            for task in tasks} == staged
    report = json.loads((run / "solve_report.json").read_text())
    assert report["routing_phase"] == "COMPLETE"
    assert report["route_confirmed"] == 2


def test_checked_in_prompt_reaches_runner_only_after_ai_route(tmp_path,
                                                                monkeypatch):
    # This is a real checked-in benchmark input, not prose authored alongside
    # the route implementation. It exercises the consumer's staged prompt.
    prompt = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "real_benchmark",
        "directional_bump_fall_moore_prompt.md").read_text()
    assert "module TopModule" in prompt
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "real_fsm_prompt.txt").write_text(prompt)
    run = tmp_path / "run"
    calls = []
    monkeypatch.setattr(
        bd._RunnerBudget, "run",
        lambda _budget, argv: calls.append(argv) or bd._ProcessOutcome(rc=1))

    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []
    task = _task(run)
    assert task["prompt_sha256"] == bd._sha256_text(prompt)
    response = _answer(task, "spec_generation")
    response["prompt_evidence"] = [{
        "excerpt": "Create a Moore state machine for a creature that walks and falls.",
        "supports": "The input requests a new Moore FSM from prose.",
    }]
    _write_answer(task, response)
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert len(calls) == 1
    report = json.loads((run / "solve_report.json").read_text())
    assert report["routing_phase"] == "COMPLETE"
    assert report["results"][0]["routing_verdict"]["source"] == "ai_confirmed"


_FIX_PROMPT = ("Fix the incorrect output inversion in this supplied module: "
               "module top_module(input wire a, output wire y); "
               "assign y = ~a; endmodule")
_LONG_GENERIC = ("The route was selected after careful consideration of the "
                 "visible input and the general product flow table. " * 4)


def _override_run(tmp_path, monkeypatch, prompt: str):
    dataset, run = _dataset(tmp_path), tmp_path / "run"
    (dataset / "generic_pulse_prompt.txt").write_text(prompt)
    calls = []
    monkeypatch.setattr(bd, "_ensure_phase1_frontdoor",
                        lambda *_a: {"status": "GENERATED",
                                     "provenance": {"ran": True}})
    monkeypatch.setattr(
        bd._RunnerBudget, "run",
        lambda _budget, argv: calls.append(argv) or bd._ProcessOutcome(rc=1))
    assert bd.cmd_solve("verilogeval-human", str(dataset), str(run)) == 2
    return dataset, run, calls, _task(run)


@pytest.mark.parametrize("evidence", [
    pytest.param([], id="no_evidence"),
    pytest.param([{"excerpt": "Fix the supplied module's broken output",
                   "supports": "The user asks to debug an existing module."}],
                 id="excerpt_not_in_prompt"),
    pytest.param([{"excerpt": "pulse_in",
                   "supports": "x"}], id="claim_too_short"),
])
def test_override_without_verified_evidence_stays_pending(
        tmp_path, monkeypatch, evidence):
    # Review wave 52 (both lenses, MAJOR): a 160-character generic rationale
    # was accepted in place of any checked prompt excerpt, and a generation
    # prompt was routed to debug (entry 4).  Length is not grounding.
    dataset, run, calls, task = _override_run(tmp_path, monkeypatch, PROMPT)
    answer = _answer(task, "debug", "OVERRIDE")
    answer["prompt_evidence"] = evidence
    answer["rationale"] = _LONG_GENERIC
    assert len(answer["rationale"]) >= 160
    _write_answer(task, answer)
    decision, reasons = bd._validate_ai_route(task, run)
    assert decision is None
    assert any("OVERRIDE requires verified prompt evidence" in r
               for r in reasons), reasons
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []
    assert json.loads((run / "solve_report.json").read_text())[
        "routing_phase"] == "PENDING"


def test_override_evidence_must_be_cited_for_the_selected_nature(
        tmp_path, monkeypatch):
    # A real excerpt whose claim argues for some other route does not ground
    # the nature the response selects.
    dataset, run, calls, task = _override_run(tmp_path, monkeypatch, PROMPT)
    answer = _answer(task, "debug", "OVERRIDE")
    answer["prompt_evidence"] = [{
        "excerpt": "Design a pulse stretcher named top_module",
        "supports": "The user requests a brand-new module from prose.",
    }]
    answer["rationale"] = _LONG_GENERIC
    _write_answer(task, answer)
    decision, reasons = bd._validate_ai_route(task, run)
    assert decision is None
    assert any("no verified evidence claim names the selected ai_nature 'debug'" in r
               for r in reasons), reasons
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert calls == []


def test_grounded_override_with_short_rationale_is_accepted(tmp_path,
                                                             monkeypatch):
    # Sibling arm: evidence, not length, is what grounds an OVERRIDE.
    original = tnr.classify_task_nature
    monkeypatch.setattr(tnr, "classify_task_nature", lambda *_a: {
        **original(PROMPT, False, "spec_generation"),
        "source": "synthetic_misroute_control"})
    dataset, run, calls, task = _override_run(tmp_path, monkeypatch,
                                              _FIX_PROMPT)
    answer = _answer(task, "debug", "OVERRIDE")
    answer["rationale"] = "Supplied RTL must be repaired."
    answer["prompt_evidence"] = [{
        "excerpt": "Fix the incorrect output inversion in this supplied module",
        "supports": "Repairing supplied RTL is the debug route.",
    }]
    _write_answer(task, answer)
    decision, reasons = bd._validate_ai_route(task, run)
    assert reasons == [] and decision["source"] == "ai_override"
    assert bd.cmd_resume("verilogeval-human", str(dataset), str(run)) == 2
    assert len(calls) == 1
    assert calls[0][calls[0].index("--entry-step") + 1] == "4"
