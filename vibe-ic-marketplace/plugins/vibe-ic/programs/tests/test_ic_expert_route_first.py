"""The owner chooses one delivery route before either Phase-1 front door runs."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import _submission_template as ST
import _tapeout_declaration as TD
import phase1_one_shot_runner as P1
import vibe_ic_one_shot_runner as ALL


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT.parent


def _call_all():
    try:
        return ALL.main()
    except SystemExit as exc:
        return exc.code


def _call_p1():
    try:
        return P1.main()
    except SystemExit as exc:
        return exc.code


def _answer(project, value="DIE", *, owner=True):
    path = project / ST.DESIGN_ANSWERS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"answers": {"deliverable": value, "other_answer": "keep me"}}
    if owner:
        doc["answer_provenance"] = {"deliverable": {
            "answered_by": "owner", "citation": "Owner: this is the delivery route."}}
    path.write_text(json.dumps(doc))
    return path


def test_expert_rule_is_first_and_names_both_routes():
    page = (PLUGIN / "agents/ic-expert-agent.md").read_text()
    first = next(line for line in page.splitlines() if line.startswith("## "))
    assert "§ 0.0" in first and "IC path or IP path" in first
    rule = page.split(first, 1)[1].split("\n## ", 1)[0]
    for phrase in ("DIE", "HARDMACRO", "IC path or IP path?", "before Phase 1",
                   "answer_provenance.deliverable", "15.5ic", "26.5ic",
                   "37.5ic", "37.5ip"):
        assert phrase in rule


@pytest.mark.parametrize("rel", [
    "commands/vibe-ic-all.md", "commands/vibe-ic-phase1.md",
    "commands/vibe-ic-benchmark.md", "skills/phase1/SKILL.md",
])
def test_each_front_door_names_the_first_rule(rel):
    text = (PLUGIN / rel).read_text()
    assert "IC path or IP path?" in text.splitlines()[:25].__str__()
    assert "ic-expert-agent.md" in text


@pytest.mark.parametrize("name", ["ic-expert-identity-session.sh",
                                         "ic-expert-identity-reminder.sh"])
def test_identity_hooks_remind_about_the_first_rule(name):
    text = (PLUGIN / "hooks" / name).read_text()
    assert "FIRST RULE: IC path or IP path?" in text
    assert "agents/ic-expert-agent.md" in text


@pytest.mark.parametrize("entry", ["all", "phase1"])
@pytest.mark.parametrize("raw,owner", [(None, False), ("NOT_DETERMINED", False),
                                        ("DIE", False)])
def test_undeclared_route_refuses_before_phase1_artifact(
        tmp_path, monkeypatch, capsys, entry, raw, owner):
    project = tmp_path / "project"
    project.mkdir()
    if raw is not None:
        _answer(project, raw, owner=owner)
    marker = project / "phase1/generated_docs/L1_DATASHEET.json"
    marker.parent.mkdir(parents=True)
    reached = []

    if entry == "all":
        monkeypatch.setattr(ALL._runner_lock, "acquire_or_reenter",
                            lambda *a: SimpleNamespace(release=lambda: None))
        monkeypatch.setattr(ALL, "_capture_container_image",
                            lambda *a: reached.append("capture") or {"verdict": "FAIL"})
        monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner.py",
                                            str(project), "--no-dashboard",
                                            "--require-image", "test"])
        rc = _call_all()
    else:
        monkeypatch.setattr(P1._runner_lock, "acquire_or_reenter",
                            lambda *a: SimpleNamespace(release=lambda: None))
        monkeypatch.setattr(P1, "run_second_pass_only",
                            lambda *a: marker.write_text("ran") or 0)
        monkeypatch.setattr(sys, "argv", ["phase1_one_shot_runner.py", str(project),
                                            "--second-track-only"])
        rc = P1.main()
    assert rc == 2
    assert not marker.exists()
    assert reached == []
    err = capsys.readouterr().err
    assert "IC path or IP path?" in err
    assert ST.DESIGN_ANSWERS_REL in err and "answer_provenance.deliverable" in err


@pytest.mark.parametrize("answer,provenance,disclosure", [
    (None, None, ("staged answers.deliverable=None", "answered_by='missing'",
                  "citation=None")),
    ("DIE", {"answered_by": "agent", "citation": "I inferred a die"},
     ("staged answers.deliverable='DIE'", "answered_by='agent'",
      "citation='I inferred a die'")),
    ("HARDMACRO", {"answered_by": "owner"},
     ("staged answers.deliverable='HARDMACRO'", "answered_by='owner'",
      "citation=None")),
])
def test_route_refusal_discloses_staged_answer_and_attestation(
        tmp_path, monkeypatch, capsys, answer, provenance, disclosure):
    project = tmp_path / "project"
    project.mkdir()
    if answer is not None:
        path = project / ST.DESIGN_ANSWERS_REL
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "answers": {"deliverable": answer},
            "answer_provenance": {"deliverable": provenance},
        }))
    reached = []
    monkeypatch.setattr(P1._runner_lock, "acquire_or_reenter",
                        lambda *a: SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(P1, "run_second_pass_only",
                        lambda *a: reached.append("phase1") or 0)
    monkeypatch.setattr(sys, "argv", ["phase1_one_shot_runner.py", str(project),
                                   "--second-track-only"])
    assert _call_p1() == 2
    assert reached == []
    err = capsys.readouterr().err
    assert "REFUSED: DELIVERY_ROUTE_UNDECLARED" in err
    assert "--route ic|ip" in err
    for field in disclosure:
        assert field in err


@pytest.mark.parametrize("route,deliverable", [("ic", "DIE"), ("ip", "HARDMACRO")])
def test_route_option_writes_owner_answer_and_preserves_other_input(
        tmp_path, monkeypatch, route, deliverable):
    _answer(tmp_path, "NOT_DETERMINED", owner=False)
    monkeypatch.setattr(ALL._runner_lock, "acquire_or_reenter",
                        lambda *a: SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(ALL, "_capture_container_image",
                        lambda *a: {"verdict": "FAIL", "reason": "test stop"})
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner.py", str(tmp_path),
                                        "--route", route, "--require-image", "test"])
    assert _call_all() == 2
    doc = json.loads((tmp_path / ST.DESIGN_ANSWERS_REL).read_text())
    assert doc["answers"] == {"deliverable": deliverable,
                               "other_answer": "keep me"}
    assert TD.answer(doc, "deliverable") == deliverable
    assert doc["answer_provenance"]["deliverable"]["answered_by"] == "owner"
    assert f"operator --route {route} on " in doc["answer_provenance"]["deliverable"]["citation"]


def test_contradicting_route_refuses_without_touching_answer(tmp_path, monkeypatch, capsys):
    path = _answer(tmp_path, "DIE")
    before = path.read_bytes()
    reached = []
    monkeypatch.setattr(ALL._runner_lock, "acquire_or_reenter",
                        lambda *a: SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(ALL, "_capture_container_image",
                        lambda *a: reached.append("capture") or
                        {"verdict": "FAIL", "reason": "test stop"})
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner.py", str(tmp_path),
                                        "--route", "ip", "--require-image", "test"])
    assert _call_all() == 2
    assert path.read_bytes() == before and reached == []
    assert "contradicts" in capsys.readouterr().err


def test_generated_owner_declaration_also_blocks_opposite_route(
        tmp_path, monkeypatch, capsys):
    _answer(tmp_path, "DIE")
    declaration = tmp_path / TD.DECLARATION_REL
    declaration.parent.mkdir(parents=True)
    declaration.write_text(json.dumps({
        "answers": {"deliverable": "HARDMACRO"},
        "answer_provenance": {"deliverable": {
            "answered_by": "owner", "citation": "Owner chose IP for this run."}},
    }))
    before = (tmp_path / ST.DESIGN_ANSWERS_REL).read_bytes()
    monkeypatch.setattr(ALL._runner_lock, "acquire_or_reenter",
                        lambda *a: SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(ALL, "_capture_container_image",
                        lambda *a: {"verdict": "FAIL", "reason": "test stop"})
    monkeypatch.setattr(sys, "argv", ["vibe_ic_one_shot_runner.py", str(tmp_path),
                                        "--route", "ic", "--require-image", "test"])
    assert _call_all() == 2
    assert (tmp_path / ST.DESIGN_ANSWERS_REL).read_bytes() == before
    assert "contradicts" in capsys.readouterr().err


def test_phase1_route_option_writes_owner_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(P1._runner_lock, "acquire_or_reenter",
                        lambda *a: SimpleNamespace(release=lambda: None))
    reached = []
    monkeypatch.setattr(P1, "run_second_pass_only",
                        lambda *a: reached.append("second pass") or 0)
    monkeypatch.setattr(sys, "argv", ["phase1_one_shot_runner.py", str(tmp_path),
                                        "--second-track-only", "--route", "ip"])
    assert _call_p1() == 0
    doc = json.loads((tmp_path / ST.DESIGN_ANSWERS_REL).read_text())
    assert TD.answer(doc, "deliverable") == "HARDMACRO"
    assert reached == ["second pass"]


def test_existing_owner_route_reaches_normal_phase1_dispatch(tmp_path, monkeypatch):
    _answer(tmp_path, "HARDMACRO")
    reached = []
    monkeypatch.setattr(P1._runner_lock, "acquire_or_reenter", lambda *a: object())
    monkeypatch.setattr(P1, "run_second_pass_only",
                        lambda *a: reached.append("second pass") or 0)
    monkeypatch.setattr(sys, "argv", ["phase1_one_shot_runner.py", str(tmp_path),
                                        "--second-track-only"])
    assert P1.main() == 0
    assert reached == ["second pass"]
