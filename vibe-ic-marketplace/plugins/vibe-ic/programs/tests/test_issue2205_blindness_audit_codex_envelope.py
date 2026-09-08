#!/usr/bin/env python3
"""#2205 — a blindness audit that reports a NONEMPTY transcript clean without
reading its actionable tool commands.

`blindness_audit._harvest_tool_strings` recognised a tool call only by FIELD
NAME (`command` / `script` / `file_path` / …). A Codex `response_item` whose
`payload.type` is `custom_tool_call` carries its command in a STRING `input`,
and a `function_call` carries it in a JSON-serialised `arguments` blob —
neither field name is in the list, so the command existed and zero actionable
strings were harvested. The transcript was counted, never read, and the audit
printed PASS. §4.05 — the rule the audit exists to enforce — was then
guaranteed by nothing.

The fix has three parts, one test class each:
  (1) the two Codex envelopes are decoded (the issue's own acceptance);
  (2) an envelope the auditor CANNOT read is refused BY NAME (NOT_MEASURED,
      exit 3) — never a silent PASS and never a fabricated violation;
  (3) recognition is structural (the frame's own declared `type`), so the
      guard is reachable by mutation — admitting every envelope must turn the
      refusal green, which is what makes (2) a real assertion.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import blindness_audit as ba  # noqa: E402

DATASET = Path("/synthetic-dataset")
ALLOWED = ["specification.md"]
CMD = "python3 score_probe.py --run synthetic"


def _audit(text: str):
    """(violations, uninspected) for one transcript text."""
    un: list[dict] = []
    v = ba.audit_text(text, DATASET, ALLOWED, "t", uninspected=un)
    return v, un


def _codex_custom_tool_call(inner: str) -> str:
    return json.dumps({"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec",
        "input": "await tools.exec_command(" + json.dumps({"cmd": inner})
                 + ");"}})


def _codex_function_call(inner: str) -> str:
    return json.dumps({"type": "response_item", "payload": {
        "type": "function_call", "name": "exec_command",
        "arguments": json.dumps({"cmd": inner})}})


# ---------------------------------------------------------------- (1) decode
@pytest.mark.parametrize("name,text", [
    ("plain_control", CMD),
    ("script_control", json.dumps({"script": CMD})),
    ("codex_custom_tool_call", _codex_custom_tool_call(CMD)),
    ("codex_function_call", _codex_function_call(CMD)),
])
def test_scorer_self_run_is_seen_in_every_envelope(name, text):
    """The SAME actionable command must be detected in every envelope shape.
    Pre-fix the two Codex rows measured 0 (#2205 evidence table)."""
    v, un = _audit(text)
    assert [f["kind"] for f in v] == ["scorer-self-run"], (name, v, un)


def test_forbidden_dataset_read_is_seen_inside_a_codex_envelope():
    """V1 too, not just V2: an oracle read hidden in a Codex envelope."""
    v, _ = _audit(_codex_function_call(f"cat {DATASET}/Prob01/Prob01_ref.sv"))
    assert [f["kind"] for f in v] == ["dataset-file-access"], v
    assert v[0]["class"].startswith("hidden oracle file"), v[0]


def test_permitted_prompt_read_stays_clean_inside_a_codex_envelope():
    """Reading the ALLOWED prompt file is the whole point of the run."""
    v, un = _audit(_codex_custom_tool_call(
        f"cat {DATASET}/Prob01/specification.md"))
    assert v == [] and un == []


def test_command_free_codex_envelope_is_clean_not_refused():
    """A tool call that carries only benign text is inspected and clean —
    the refusal must not fire on every unfamiliar payload."""
    v, un = _audit(json.dumps({"type": "custom_tool_call", "name": "exec",
                               "input": "echo hello"}))
    assert v == [] and un == []


def test_tool_output_twin_is_not_required_to_be_inspected():
    """`custom_tool_call_output` is the tool's ANSWER, not an action the agent
    chose; requiring it would make every Codex transcript NOT_MEASURED."""
    v, un = _audit(json.dumps({"type": "custom_tool_call_output",
                               "call_id": "c1", "output": {"exit_code": 0}}))
    assert un == [], un
    assert v == []


# ------------------------------------------------- (2) refuse what it cannot read
OPAQUE = json.dumps({"type": "custom_tool_call", "name": "exec",
                     "encrypted_content": "b64:AAAA"})


def test_unreadable_envelope_is_not_clean_and_is_named():
    """An envelope with no readable actionable payload must be refused BY
    NAME — shape, tool and location — and must NOT be reported as a
    blindness violation (it is a coverage gap, not proof of a leak)."""
    v, un = _audit(OPAQUE)
    assert v == [], v
    assert len(un) == 1, un
    rec = un[0]
    assert rec["envelope_type"] == "custom_tool_call"
    assert rec["tool_name"] == "exec"
    assert rec["transcript"] == "t" and rec["line"] == 1


def test_cli_refuses_to_certify_an_unreadable_envelope(tmp_path):
    """End to end: rc=3 (AUDIT_ERROR / NOT MEASURED), never rc=0 PASS. The
    front door (benchmark_dispatch) already refuses to score on rc=3."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text(OPAQUE + "\n")
    cov = tmp_path / "cov.json"
    p = subprocess.run(
        [sys.executable, str(PROGRAMS / "blindness_audit.py"),
         "--dataset", str(DATASET), "--allowed-glob", "specification.md",
         "--coverage-json", str(cov), str(tdir)],
        capture_output=True, text=True)
    assert p.returncode == ba.EXIT_AUDIT_ERROR, (p.returncode, p.stdout, p.stderr)
    assert "PASS" not in p.stdout
    assert "NOT MEASURED" in p.stderr and "custom_tool_call" in p.stderr
    census = json.loads(cov.read_text())
    assert census["uninspected_tool_calls"] == 1, census


def test_cli_still_passes_a_readable_codex_transcript(tmp_path):
    """The refusal must not swallow the clean case (a vacuous guard that
    refuses everything is the same defect wearing the other hat)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text(
        _codex_custom_tool_call(f"cat {DATASET}/Prob01/specification.md")
        + "\n")
    p = subprocess.run(
        [sys.executable, str(PROGRAMS / "blindness_audit.py"),
         "--dataset", str(DATASET), "--allowed-glob", "specification.md",
         str(tdir)], capture_output=True, text=True)
    assert p.returncode == ba.EXIT_CLEAN, (p.returncode, p.stdout, p.stderr)
    assert "PASS" in p.stdout


def test_cli_still_fails_a_real_violation_in_a_codex_transcript(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text(
        _codex_function_call(f"cat {DATASET}/Prob01/Prob01_ref.sv") + "\n")
    p = subprocess.run(
        [sys.executable, str(PROGRAMS / "blindness_audit.py"),
         "--dataset", str(DATASET), "--allowed-glob", "specification.md",
         str(tdir)], capture_output=True, text=True)
    assert p.returncode == ba.EXIT_VIOLATION, (p.returncode, p.stdout)
    assert "dataset-file-access" in p.stdout


# ------------------------------------------------------------- (3) mutation
def test_mutation_admitting_every_envelope_makes_the_guard_go_quiet(
        monkeypatch):
    """A guard no mutation can reach is not a guard. Force the recogniser to
    treat every frame as already inspected and BOTH the decode and the
    refusal must collapse — that collapse is what the tests above assert
    against, so they cannot be passing for an unrelated reason."""
    monkeypatch.setattr(ba, "_harvest_frame_strings",
                        lambda node, depth=0: ["already inspected"])
    v, un = _audit(OPAQUE)
    assert un == [], "mutation did not reach the refusal — it is unreachable"

    # and with recognition itself blinded, the Codex command goes unseen
    # again: exactly the pre-fix measurement in the issue.
    monkeypatch.setattr(ba, "_is_call_frame", lambda node: False)
    v2, un2 = _audit(_codex_custom_tool_call(CMD))
    assert v2 == [] and un2 == [], (v2, un2)


@pytest.mark.parametrize("frame", [
    {"type": "function_call", "name": "exec_command", "arguments": "{broken"},
    {"type": "function_call", "name": "exec_command", "arguments": '"opaque"'},
    {"type": "custom_tool_call", "name": "exec", "opaque_payload": "encoded"},
])
def test_malformed_or_unsupported_payload_has_no_inspection_credit(frame):
    v, un = _audit(json.dumps(frame))
    assert v == []
    assert len(un) == 1


def test_call_description_and_tool_output_are_not_executed_commands():
    frame = {"type": "function_call", "name": "exec_command",
             "arguments": json.dumps({"cmd": "echo hello"}),
             "description": "Never run " + CMD}
    result = {"type": "custom_tool_call_output", "output": {"cmd": CMD}}
    v, un = _audit(json.dumps(frame) + '\n' + json.dumps(result))
    assert v == [] and un == []


def test_inspection_counts_and_findings_retain_each_call_identity(tmp_path):
    frame = {"type": "function_call", "name": "exec_command",
             "arguments": json.dumps({"cmd": CMD})}
    text = '\n'.join(json.dumps({**frame, "call_id": str(i)}) for i in (1, 2))
    calls = []
    v = ba.audit_text(text, DATASET, ALLOWED, "calls", coverage=calls)
    assert len(calls) == 2 and all(c["inspected"] for c in calls)
    assert [(f["line"], f["call_id"], f["tool_name"]) for f in v] == [
        (1, "1", "exec_command"), (2, "2", "exec_command")]
    transcript = tmp_path / "calls.jsonl"
    transcript.write_text(text + '\n' + OPAQUE)
    cov = tmp_path / "coverage.json"
    rc = ba.main(["--dataset", str(DATASET), "--coverage-json", str(cov), str(transcript)])
    assert rc == ba.EXIT_VIOLATION
    report = json.loads(cov.read_text())
    assert (report["tool_calls"], report["inspected_tool_calls"],
            report["uninspected_tool_calls"]) == (3, 2, 1)
