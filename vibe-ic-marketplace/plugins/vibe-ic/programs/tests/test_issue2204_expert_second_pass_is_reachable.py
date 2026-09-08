#!/usr/bin/env python3
"""test_issue2204_expert_second_pass_is_reachable.py

`phase1_expert_parse_track` ends its first pass by telling the operator to
invoke the `vibe-ic:ic-expert-agent` subagent and "re-run to consume its
answer". THE ONE CANONICAL FRONT DOOR COULD NOT PERFORM THAT RE-RUN.
`vibe_ic_one_shot_runner._phase1_decision` returned `(False, "")` as soon as
`L_count >= 13`, and Phase 1 emits 28 L documents — so from the first
successful run onward every orchestrator invocation SKIPPED Phase 1 and a
delivered expert answer sat unread on disk forever.

MEASURED before the fix, on a synthesised project carrying 28 L documents, the
subagent's `l_doc_expectations.json` on disk and an `expert_parse_track.json`
whose `ai_subtrack.status` is still `HANDOFF_EMITTED`:

    _phase1_decision(project, force_skip=False) -> (False, '')

— identical to the answer it gives for the same project with no answer file at
all. The decision could not see the difference between "phase 1 has run" and
"phase 1 has run AND its expert answer has been read".

WHAT IS PINNED HERE, AND WHY BOTH DIRECTIONS ARE PINNED
------------------------------------------------------
A change that always re-runs Phase 1 would make the first half of this file
green and would be a wall-clock tax on every run, not a fix. So the SKIP
direction is pinned as hard as the RE-ENTER direction:

  * an UNREAD delivered answer re-enters Phase 1, in a mode that runs the
    second track ALONE and re-extracts nothing;
  * a root whose answer was read — under EVERY status the track can record
    once it has opened an answer — is still SKIPPED;
  * a root with no answer at all is still SKIPPED;
  * an operator's `--skip-phase1` is still a skip.

The last three are CONTROLS: they are green before this fix and must stay
green after it. A fix that reddens them has replaced one defect with a worse
one.

TERMINATION is keyed to the bytes the producer actually read. An unchanged
refused answer is skipped; a corrected answer or an answer without a read
receipt is retried. The subprocess lifecycle fixtures below reproduce both
independent review variants. Their L-docs and initial PASS summary are synthetic
seam scaffolding, not certified extraction or golden benchmark evidence.

Every fixture is synthesised here from neutral parts. No design, PDK, vendor
or IP-model identifier appears anywhere in this file.

Run: python3 -m pytest programs/tests/test_issue2204_expert_second_pass_is_reachable.py -q
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import vibe_ic_one_shot_runner as ORCH        # noqa: E402
import phase1_one_shot_runner as P1           # noqa: E402
import phase1_expert_parse_track as TRACK     # noqa: E402
import _path_layout as _pl                    # noqa: E402


_INPUT_DOC = """# Block specification

The converter accepts an external reference on the REFHI terminal and
digitises to 12 bits at 500 ksps. Trim values are restored at power-up.
"""

#: A well-formed answer in the shape `answer_schema_mismatch` accepts.
_ANSWER = {
    "expectations": [{
        "id": "reference-terminal-is-a-named-port",
        "layer": "L9_INTERFACE",
        "field_path": "top_ports",
        "requirement": "the external reference terminal the input names must "
                       "appear as a named port",
        "expected_tokens": ["REFHI"],
        "evidence": ["the input names REFHI as an external terminal"],
    }],
}


def _report(project: Path) -> Path:
    return _pl.report_path(project, "phase1/expert_parse_track.json")


def _answer_path(project: Path) -> Path:
    return (_report(project).parent / "expert_parse_track_pack"
            / "l_doc_expectations.json")


def _project(tmp_path: Path, name: str, *, l_docs: int = 28,
             answer: bool = False, status: str | None = "HANDOFF_EMITTED",
             report: bool = True) -> Path:
    """A project in a stated Phase-1 state. Nothing is inferred: the L-doc
    count, the answer's presence and the track's recorded status are three
    independent knobs, because they are three independent facts on disk."""
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, l_docs + 1):
        (gd / f"L{i}_LAYER.json").write_text(json.dumps({"doc_id": f"L{i}"}))
    rep = _report(p)
    rep.parent.mkdir(parents=True, exist_ok=True)
    (rep.parent / "expert_parse_track_pack").mkdir(parents=True, exist_ok=True)
    if report:
        rep.write_text(json.dumps({
            "program": TRACK.PROGRAM,
            "verdict": "PASS",
            "ai_subtrack": {"status": status, "expectations": [],
                            **({"answer_sha256": hashlib.sha256(
                                json.dumps(_ANSWER).encode()).hexdigest()}
                               if answer and status != TRACK.AI_HANDOFF_EMITTED
                               else {})},
        }))
    if answer:
        _answer_path(p).write_text(json.dumps(_ANSWER))
    return p


# ── the defect: an unread answer could not re-enter Phase 1 ────────────────

def test_an_unread_expert_answer_re_enters_phase1(tmp_path):
    """The whole of #2204. The answer is on disk and the track's own record
    still says it has not seen one — that is a pending SECOND PASS, and the
    front door must perform it."""
    p = _project(tmp_path, "unread", answer=True, status="HANDOFF_EMITTED")
    assert _answer_path(p).is_file(), "fixture: the answer must be on disk"

    pending, why = ORCH._expert_answer_pending(p)
    assert pending is True, why
    assert "nobody has read" in why

    run, mode = ORCH._phase1_decision(p, force_skip=False)
    assert run is True, (
        "an answer the subagent delivered is on disk and the expert track's "
        "own record says nobody has read it; the front door skipped Phase 1 "
        "anyway, so the re-run the track asks for can never happen")
    assert mode == ORCH._P1_MODE_EXPERT_SECOND_PASS, mode
    # NOT the extraction mode. Re-running D1 would cost a full extraction on
    # every such run and would move the L documents the answer was authored
    # against.
    assert mode != "docs"


def test_the_unread_status_is_the_tracks_own_constant():
    """The orchestrator spells the status rather than importing it, so it keeps
    working against an older track binary. This is the pin that stops the two
    spellings drifting apart unnoticed — a drift would make the decision blind
    again, silently."""
    assert ORCH._EXPERT_AI_UNREAD == TRACK.AI_HANDOFF_EMITTED
    # And the two path components must name the same file the track writes.
    assert ORCH._EXPERT_ANSWER_NAME == "l_doc_expectations.json"
    assert ORCH._EXPERT_PACK_DIRNAME == "expert_parse_track_pack"
    # `evaluate` builds the pack dir from the report path; the orchestrator
    # must land on the same place.
    src = Path(TRACK.__file__).read_text(errors="replace")
    assert f'"{ORCH._EXPERT_PACK_DIRNAME}"' in src
    assert f'"{ORCH._EXPERT_ANSWER_NAME}"' in src


# ── termination: at most one re-entry per delivered answer ─────────────────

@pytest.mark.parametrize("status,expect_pending", [
    (TRACK.AI_HANDOFF_EMITTED, True),
    (TRACK.AI_CONSUMED, False),
    (TRACK.AI_CONSUMED_EMPTY, False),
    (TRACK.AI_SCHEMA_MISMATCH, False),
    (TRACK.AI_ERROR, False),
])
def test_re_entry_is_at_most_once_per_delivered_answer(
        tmp_path, status, expect_pending):
    """A read receipt suppresses retries of the same consumed/refused bytes.
    HANDOFF is unconsumed even if a contradictory fixture supplies a digest.
    """
    p = _project(tmp_path, f"s_{status}", answer=True, status=status)
    pending, why = ORCH._expert_answer_pending(p)
    assert pending is expect_pending, f"status={status}: {why}"
    run, _mode = ORCH._phase1_decision(p, force_skip=False)
    assert run is expect_pending, f"status={status}"


def test_a_missing_or_unparseable_record_beside_an_answer_is_pending(tmp_path):
    """The track's report is a MANDATORY output. An answer on disk with no
    readable record of anyone having opened it is the same unread state, and it
    is stated rather than assumed clean."""
    p = _project(tmp_path, "norecord", answer=True, report=False)
    pending, why = ORCH._expert_answer_pending(p)
    assert pending is True and "no report" in why

    q = _project(tmp_path, "badrecord", answer=True)
    _report(q).write_text("{not json")
    pending, why = ORCH._expert_answer_pending(q)
    assert pending is True and "does not parse" in why


# ── the controls: green before this fix, and they must stay green ─────────

def test_a_genuinely_complete_root_is_still_skipped(tmp_path):
    """CONTROL — and a TRUE control, which is why it names nothing this fix
    introduced. A control that touches the new helper is green only on the
    fixed arm, which makes it a second copy of the defect test rather than
    evidence that the skip direction survived. (It was written that way first;
    it went red on the unfixed arm for the wrong reason, and that is recorded
    in this lane's missed predictions.)

    A fix that always re-runs Phase 1 is a wall-clock tax on every run wearing
    the clothes of progress. A root whose expert answer was read, and a root
    that never had one, are both finished with Phase 1 — before this change and
    after it."""
    read = _project(tmp_path, "read", answer=True, status=TRACK.AI_CONSUMED)
    assert ORCH._phase1_decision(read, force_skip=False) == (False, "")

    none = _project(tmp_path, "noanswer", answer=False)
    assert ORCH._phase1_decision(none, force_skip=False) == (False, "")


def test_an_absent_answer_is_not_pending(tmp_path):
    """The helper's own negative: with no answer on disk there is nothing to
    consume, and the reason says so rather than defaulting to a silent False."""
    none = _project(tmp_path, "nopending", answer=False)
    pending, why = ORCH._expert_answer_pending(none)
    assert pending is False and "no expert answer" in why


def test_an_operator_forced_skip_is_still_a_skip(tmp_path):
    """CONTROL. `--skip-phase1`, and the Phase-2 entry step that routes through
    it, must not be overridden by a pending second pass."""
    p = _project(tmp_path, "forced", answer=True, status="HANDOFF_EMITTED")
    assert ORCH._phase1_decision(p, force_skip=True) == (False, "")


# ── the second pass itself ────────────────────────────────────────────────

def test_the_phase1_runner_accepts_second_track_only():
    """The mode the orchestrator dispatches has to exist on the runner it
    dispatches to; a mode nothing accepts is the same dead end one file
    along."""
    cp = subprocess.run(
        [sys.executable, str(_PROGRAMS / "phase1_one_shot_runner.py"),
         "--help"], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert "--second-track-only" in cp.stdout


def test_the_second_pass_runs_the_second_track_and_re_extracts_nothing(
        tmp_path, monkeypatch):
    """The second pass consumes ONE delivered answer. It must not re-derive the
    L documents that answer was authored against, and it must not erase what
    pass 1 recorded in the file every caller reads for Phase 1's verdict."""
    p = _project(tmp_path, "secondpass", answer=True, status="HANDOFF_EMITTED")
    (p / "reports").mkdir(exist_ok=True)
    (p / "reports" / "phase1_one_shot.json").write_text(json.dumps({
        "phase": 1, "mode": "docs", "verdict": "PASS",
        "steps": [{"name": "doc_extract", "status": "PASS"}]}))
    gd = _pl.generated_docs_dir(p)
    before = {f.name: f.read_bytes() for f in gd.glob("L*.json")}

    calls = []

    def _fake_second_track(project, rc_in):
        calls.append(project)
        return 0

    monkeypatch.setattr(P1, "run_phase1_second_track", _fake_second_track)
    rc = P1.run_second_pass_only(p, "UNNAMED_CHIP")

    assert rc == 0
    assert calls == [p], "the second track is the ONE thing this pass runs"
    after = {f.name: f.read_bytes() for f in gd.glob("L*.json")}
    assert after == before, "the doc-extraction track was re-run under the answer"

    summary = json.loads((p / "reports" / "phase1_one_shot.json").read_text())
    assert summary["mode"] == "expert_second_pass"
    assert summary["second_pass"]["doc_extraction_rerun"] is False
    assert summary["second_pass"]["pass1_summary_carried_forward"] is True
    # pass 1's own record survives — the second pass reports, it does not
    # overwrite the extraction's report to close a hand-off.
    assert summary["steps"] == [{"name": "doc_extract", "status": "PASS"}]


def test_a_delivered_answer_is_consumed_and_the_front_door_then_skips(tmp_path):
    """END TO END over the REAL track, both directions in one project.

    Pass 1 with no answer records HANDOFF_EMITTED and the front door — before
    this fix — skipped forever. Plant the answer: the front door now re-enters.
    Run the track again: the answer is CONSUMED and the front door goes back to
    skipping. The re-entry happened exactly once."""
    p = tmp_path / "e2e"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, 29):
        (gd / f"L{i}_LAYER.json").write_text(json.dumps({"doc_id": f"L{i}"}))

    argv = [sys.executable, str(_PROGRAMS / "phase1_expert_parse_track.py"),
            str(p)]
    cp1 = subprocess.run(argv, capture_output=True, text=True)
    assert cp1.returncode in (0, 2, TRACK.AWAITING_EXIT_CODE), cp1.stderr[-800:]
    rec1 = json.loads(_report(p).read_text())
    assert rec1["ai_subtrack"]["status"] == TRACK.AI_HANDOFF_EMITTED
    assert not _answer_path(p).is_file()
    # The state the whole fleet was in: nothing to consume, so nothing to do.
    assert ORCH._phase1_decision(p, force_skip=False) == (False, "")

    # The subagent answers.
    _answer_path(p).write_text(json.dumps(_ANSWER))
    run, mode = ORCH._phase1_decision(p, force_skip=False)
    assert run is True and mode == ORCH._P1_MODE_EXPERT_SECOND_PASS, (
        "the answer the hand-off asked for is on disk and the front door "
        "still refuses the re-run the hand-off asked for")

    # The second pass reads it.
    cp2 = subprocess.run(argv, capture_output=True, text=True)
    assert cp2.returncode in (0, 2, TRACK.AWAITING_EXIT_CODE), cp2.stderr[-800:]
    rec2 = json.loads(_report(p).read_text())
    assert rec2["ai_subtrack"]["status"] == TRACK.AI_CONSUMED, \
        rec2["ai_subtrack"].get("reason")

    # …and the front door stops re-entering. AT MOST ONCE per delivered answer.
    assert ORCH._phase1_decision(p, force_skip=False) == (False, "")


# ── the front door's own half: the mode must reach the runner as an argv ───

def test_the_front_door_maps_the_mode_onto_the_flag_it_dispatches():
    """PROVED BY PARSE, not by grepping the file for a string.

    `_phase1_decision` returning a mode is worth nothing if the call site does
    not turn that mode into the argument the runner reads. Nothing else in this
    file joins the two halves: the decision tests stop at the mode, and the
    execution test below starts from the argv. This is the seam, and it is
    asserted over the syntax tree of the branch that owns it — a comment or a
    docstring naming the flag cannot satisfy it.
    """
    import ast
    src = Path(ORCH.__file__).read_text(errors="replace")
    tree = ast.parse(src)
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")

    def _guards_second_pass(test: ast.expr) -> bool:
        return any(isinstance(n, ast.Attribute)
                   and n.attr == "_P1_MODE_EXPERT_SECOND_PASS"
                   or isinstance(n, ast.Name)
                   and n.id == "_P1_MODE_EXPERT_SECOND_PASS"
                   for n in ast.walk(test))

    branches = [n for n in ast.walk(main)
                if isinstance(n, ast.If) and _guards_second_pass(n.test)]
    assert branches, ("nothing in vibe_ic_one_shot_runner.main branches on the "
                      "second-pass mode, so the mode the decision returns "
                      "reaches no argv")
    # ONLY the branch's own BODY. `ast.walk` on an `If` also walks `orelse`,
    # which is the `elif p1_mode == "docs"` arm — so a walk of the whole node
    # sees the extraction flags too and the second assertion below could never
    # hold. (It did not, first time round; the test was wrong, not the code.)
    flags = {c.value for b in branches for n in b.body
             for c in ast.walk(n) if isinstance(c, ast.Constant)
             and isinstance(c.value, str)}
    assert "--second-track-only" in flags, (
        "the second-pass branch does not build the flag "
        "`phase1_one_shot_runner` needs; the branch would run a full "
        "re-extraction instead")
    # And it must NOT ask for the extraction mode: `--mode docs` in this branch
    # would re-derive the L documents under the answer that was authored
    # against them.
    assert "docs" not in flags and "--mode" not in flags


def test_the_dispatched_argv_actually_consumes_the_answer(tmp_path):
    """EXECUTE THE ARGV. A test that asserts the front door *would* pass
    `--second-track-only` and never runs it proves a string, not a behaviour —
    this repo has measured that shape more than once. So: build a project in
    the pending state with the REAL first pass, run the REAL runner with the
    REAL flag, and read what changed.
    """
    p = tmp_path / "dispatched"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "spec.md").write_text(_INPUT_DOC)
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, 29):
        (gd / f"L{i}_LAYER.json").write_text(json.dumps({"doc_id": f"L{i}"}))
    # PASS 1, for real — this is what writes HANDOFF_EMITTED and the pack.
    cp1 = subprocess.run(
        [sys.executable, str(_PROGRAMS / "phase1_expert_parse_track.py"),
         str(p)], capture_output=True, text=True)
    assert cp1.returncode in (0, 2, TRACK.AWAITING_EXIT_CODE), cp1.stderr[-800:]
    assert json.loads(_report(p).read_text())["ai_subtrack"]["status"] \
        == TRACK.AI_HANDOFF_EMITTED
    # A pass-1 summary the second pass must carry forward, not overwrite.
    (p / "reports").mkdir(exist_ok=True)
    (p / "reports" / "phase1_one_shot.json").write_text(json.dumps({
        "phase": 1, "mode": "docs", "verdict": "PASS",
        "steps": [{"name": "doc_extract", "status": "PASS"}]}))
    l_docs_before = {f.name: f.read_bytes() for f in gd.glob("L*.json")}

    # The subagent answers, and the front door dispatches THIS argv.
    _answer_path(p).write_text(json.dumps(_ANSWER))
    run, mode = ORCH._phase1_decision(p, force_skip=False)
    assert run and mode == ORCH._P1_MODE_EXPERT_SECOND_PASS

    cp2 = subprocess.run(
        [sys.executable, str(_PROGRAMS / "phase1_one_shot_runner.py"),
         str(p), "--ic-name", "UNNAMED_CHIP", "--second-track-only"],
        capture_output=True, text=True)
    assert cp2.returncode == 0, (cp2.stdout[-1500:], cp2.stderr[-1500:])

    rec = json.loads(_report(p).read_text())
    assert rec["ai_subtrack"]["status"] == TRACK.AI_CONSUMED, \
        rec["ai_subtrack"].get("reason")
    summary = json.loads((p / "reports" / "phase1_one_shot.json").read_text())
    assert summary["mode"] == "expert_second_pass"
    assert summary["second_pass"]["doc_extraction_rerun"] is False
    assert summary["steps"] == [{"name": "doc_extract", "status": "PASS"}], \
        "the second pass overwrote what pass 1 recorded"
    assert {f.name: f.read_bytes() for f in gd.glob("L*.json")} == l_docs_before

    # …and the front door stops re-entering.
    assert ORCH._phase1_decision(p, force_skip=False) == (False, "")


def _invoke_lifecycle(project, label, *, track=False, first_pass=False,
                      extract_rc=None):
    """Retain the real producer's argv, rc, output and both report snapshots."""
    argv = [sys.executable, "-B", str(_PROGRAMS / (
        "phase1_expert_parse_track.py" if track else "phase1_one_shot_runner.py")),
        str(project)]
    if not track:
        argv += ["--ic-name", "review_counter"]
        if not first_pass:
            argv += ["--second-track-only"]
    if extract_rc is not None:
        # Only the extraction delegate is a seam. main(), preflight, route,
        # expert subprocess, summary and CLI return path are the real code.
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "import phase1_one_shot_runner as p; "
                f"p._run_docs_mode = lambda *a: {extract_rc}; "
                "sys.argv = sys.argv[2:]; raise SystemExit(p.main())")
        argv = [sys.executable, "-B", "-c", code, str(_PROGRAMS), *argv[2:]]
    cp = subprocess.run(argv, capture_output=True, text=True)
    (project / f"{label}.stdout").write_text(cp.stdout)
    (project / f"{label}.stderr").write_text(cp.stderr)
    row = {"argv": argv, "rc": cp.returncode}
    for key, path in (("track", _report(project)),
                      ("summary", project / "reports/phase1_one_shot.json")):
        if path.is_file():
            row[key] = json.loads(path.read_text())
    (project / f"{label}.json").write_text(json.dumps(row, indent=2))
    return row


@pytest.fixture(params=["invalid_json", "wrong_schema"])
def refused_then_corrected(tmp_path, request):
    # Same prompt/answer and two refused byte strings as the independent review.
    p = _project(tmp_path, request.param, report=False)
    (p / "input/docs/spec.md").write_text(
        "# Counter specification\n\nThe counter accepts an external clock at "
        "the REFCLK terminal and presents a 16-bit count.\n")
    answer = {"expectations": [{
        "id": "prompt-named-external-clock", "layer": "L9_INTERFACE",
        "field_path": "top_ports",
        "requirement": "The external clock terminal REFCLK must appear as a named port.",
        "expected_tokens": ["REFCLK"],
        "evidence": ["The counter accepts an external clock at the REFCLK terminal."],
    }]}
    before = {f.name: f.read_bytes() for f in _pl.generated_docs_dir(p).glob("L*.json")}
    handoff = _invoke_lifecycle(p, "01_handoff", track=True)
    assert handoff["rc"] == 4
    assert handoff["track"]["ai_subtrack"]["status"] == TRACK.AI_HANDOFF_EMITTED
    assert ORCH._phase1_decision(p, False) == (False, "")
    pass1 = {"phase": 1, "mode": "docs", "verdict": "PASS",
             "steps": [{"name": "doc_extract", "status": "PASS"}]}
    (p / "reports/phase1_one_shot.json").write_text(json.dumps(pass1))
    malformed = "{" if request.param == "invalid_json" else '{"ports": ["REFCLK"]}'
    _answer_path(p).write_text(malformed)
    assert ORCH._phase1_decision(p, False) == (True, "expert_second_pass")
    refused = _invoke_lifecycle(p, "02_refused")
    assert refused["rc"] == 1
    assert refused["summary"]["verdict"] == "FAIL"
    assert refused["track"]["ai_subtrack"]["status"] == (
        TRACK.AI_ERROR if request.param == "invalid_json" else TRACK.AI_SCHEMA_MISMATCH)
    # Repeat the decision to catch an unchanged-refusal retry loop.
    for _ in range(3):
        assert ORCH._phase1_decision(p, False) == (False, "")
    _answer_path(p).write_text(json.dumps(answer, indent=2) + "\n")
    corrected_decision = ORCH._phase1_decision(p, False)
    corrected = _invoke_lifecycle(p, "03_corrected")
    assert corrected["rc"] == 0
    assert corrected["track"]["ai_subtrack"]["status"] == TRACK.AI_CONSUMED
    assert corrected["track"]["ai_convergence"]["consumed"] == 1
    assert corrected["summary"]["steps"] == pass1["steps"]
    assert {f.name: f.read_bytes() for f in _pl.generated_docs_dir(p).glob("L*.json")} == before
    for _ in range(3):
        assert ORCH._phase1_decision(p, False) == (False, "")
    (p / "decision.json").write_text(json.dumps(corrected_decision))
    return corrected_decision, corrected


def test_corrected_refused_answer_reenters(refused_then_corrected):
    decision, _ = refused_then_corrected
    assert decision == (True, "expert_second_pass")


def test_successful_retry_clears_only_second_pass_failure(refused_then_corrected):
    _, result = refused_then_corrected
    assert result["summary"]["verdict"] == "PASS", result["summary"]
    assert result["summary"]["second_pass"]["rc"] == result["rc"] == 0


@pytest.mark.parametrize("record", [[], {}, {"ai_subtrack": None},
    {"ai_subtrack": {"status": []}}, {"ai_subtrack": {"status": {}}},
    {"ai_subtrack": {"status": "UNKNOWN"}},
    {"ai_subtrack": {"status": "CONSUMED"}},
    {"ai_subtrack": {"status": "ERROR"}}])
def test_an_unidentified_answer_is_pending(tmp_path, record):
    p = _project(tmp_path, "no_read_receipt", answer=True)
    _report(p).write_text(json.dumps(record))
    assert ORCH._phase1_decision(p, False) == (True, "expert_second_pass")


def test_pack_assembly_error_does_not_claim_to_have_read_the_answer(tmp_path, monkeypatch):
    import ic_expert_backup_pack as pack
    p = _project(tmp_path, "assembly_error", answer=True, report=False)

    def refuse_assembly(**kwargs):
        raise OSError("fixture output directory unavailable")

    monkeypatch.setattr(pack, "assemble", refuse_assembly)
    report = TRACK.evaluate(p)
    _report(p).write_text(json.dumps(report))
    assert report["ai_subtrack"]["status"] == TRACK.AI_ERROR
    assert "answer_sha256" not in report["ai_subtrack"]
    assert ORCH._phase1_decision(p, False) == (True, "expert_second_pass")


def test_changed_consumed_answer_reenters_but_mtime_alone_does_not(tmp_path):
    import os
    p = _project(tmp_path, "changed_consumed", answer=True, status=TRACK.AI_CONSUMED)
    answer = _answer_path(p)
    os.utime(answer, (1, 1))
    assert ORCH._phase1_decision(p, False) == (False, "")
    answer.write_text(json.dumps(_ANSWER) + "\n")
    os.utime(answer, (1, 1))
    assert ORCH._phase1_decision(p, False) == (True, "expert_second_pass")


def test_actual_first_pass_failure_survives_consumption(tmp_path):
    p = tmp_path / "no_input"
    p.mkdir()
    first = _invoke_lifecycle(p, "01_no_input", first_pass=True)
    assert first["rc"] == 1, first
    assert first["summary"]["verdict"] == "FAIL"
    # Provide synthetic L-docs/answer for the retry seam without re-extraction.
    (p / "input/docs").mkdir(parents=True)
    (p / "input/docs/spec.md").write_text(_INPUT_DOC)
    gd = _pl.generated_docs_dir(p)
    gd.mkdir(parents=True, exist_ok=True)
    for i in range(1, 29):
        (gd / f"L{i}_LAYER.json").write_text(json.dumps({"doc_id": f"L{i}"}))
    _answer_path(p).parent.mkdir(parents=True, exist_ok=True)
    _answer_path(p).write_text(json.dumps(_ANSWER))
    before = {f.name: f.read_bytes() for f in gd.glob("L*.json")}
    second = _invoke_lifecycle(p, "02_consumed")
    assert second["track"]["ai_subtrack"]["status"] == TRACK.AI_CONSUMED
    assert second["rc"] == 1, second
    assert second["summary"]["verdict"] == "FAIL"
    assert second["summary"]["second_pass"]["rc"] == 0
    assert second["summary"]["steps"] == first["summary"]["steps"]
    assert {f.name: f.read_bytes() for f in gd.glob("L*.json")} == before


@pytest.mark.parametrize("extract_rc", [0, 1])
def test_initial_expert_failure_is_separate_from_extraction(tmp_path, extract_rc):
    p = _project(tmp_path, "initial_refusal", answer=True, report=False)
    _answer_path(p).write_text('{"ports": ["REFHI"]}')
    first = _invoke_lifecycle(p, "01_initial_refusal", first_pass=True,
                              extract_rc=extract_rc)
    assert first["rc"] == 1, first
    assert first["track"]["ai_subtrack"]["status"] == TRACK.AI_SCHEMA_MISMATCH
    _answer_path(p).write_text(json.dumps(_ANSWER))
    second = _invoke_lifecycle(p, "02_corrected")
    assert second["track"]["ai_subtrack"]["status"] == TRACK.AI_CONSUMED
    assert second["rc"] == extract_rc, second
    assert second["summary"]["verdict"] == ("FAIL" if extract_rc else "PASS")
    assert second["summary"]["steps"] == first["summary"]["steps"]
    assert second["summary"]["pass1"]["rc"] == extract_rc
    assert first["summary"]["steps"][0]["status"] == ("FAIL" if extract_rc else "PASS")


@pytest.mark.parametrize("prior", [None, {"mode": "expert_second_pass", "verdict": "FAIL"},
    {"mode": "docs", "verdict": "PASS", "pass1": {"rc": False, "verdict": "PASS"}}])
def test_unknown_first_pass_cannot_become_pass(tmp_path, prior):
    p = _project(tmp_path, "unknown_pass1", answer=True)
    if prior is not None:
        (p / "reports/phase1_one_shot.json").write_text(json.dumps(prior))
    result = _invoke_lifecycle(p, "01_consumed")
    assert result["track"]["ai_subtrack"]["status"] == TRACK.AI_CONSUMED
    assert result["rc"] == 1
    assert result["summary"]["verdict"] == "FAIL"
    assert result["summary"]["second_pass"]["rc"] == 0


def test_empty_answer_is_recorded_without_consumption_credit(tmp_path):
    p = _project(tmp_path, "empty_answer", answer=True)
    (p / "reports/phase1_one_shot.json").write_text(json.dumps({
        "mode": "docs", "verdict": "PASS"}))
    _answer_path(p).write_text('{"expectations": []}')
    result = _invoke_lifecycle(p, "01_empty")
    assert result["track"]["ai_subtrack"]["status"] == TRACK.AI_CONSUMED_EMPTY
    assert result["track"]["execution"]["complete"] is False
    assert "INCOMPLETE" in result["summary"]["second_track"]
    assert P1._expert_track_completion(result["track"])[0] is False
    assert ORCH._phase1_decision(p, False) == (False, "")
    _answer_path(p).write_text(json.dumps(_ANSWER))
    assert ORCH._phase1_decision(p, False) == (True, "expert_second_pass")
