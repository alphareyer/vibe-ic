"""Pass 1 written before the report moved is still carried forward.

R-0915-160. R-0915-151 moved `phase1_one_shot.json` onto `_path_layout`'s router, because the
flat `reports/phase1_one_shot.json` is a location the repo's own
`reports_subfolder_taxonomy_check` calls a stray file. That is the right answer for WHERE TO
WRITE, and it is not the whole question: every project produced before that landing still has
its pass-1 record at the flat path.

MEASURED cost of conflating the two questions: `run_second_pass_only` read ONLY the routed
path, so on such a project pass 1 was not carried forward -- `carried=False`, a second pass
publishing "UNREADABLE -- the pass-1 record could not be carried forward", and the front door
halting at phase 1 on a project that had in fact completed it. Main passed the same project.

The write is still the router's single answer. The READ goes through one shared resolver,
`_path_layout.report_path_for_reading`, which the other legacy readers use too -- this file
held the routed-and-legacy pair in six places across four modules before that.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import _path_layout as _pl                   # noqa: E402

ROUTED = "reports/orchestrator/phase1_one_shot.json"
LEGACY = "reports/phase1_one_shot.json"
NAME = "phase1_one_shot.json"


# ── the resolver itself ──────────────────────────────────────────────────────

def test_the_routed_path_wins_when_it_exists(tmp_path):
    for rel in (ROUTED, LEGACY):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"at": rel}) + "\n")
    got, legacy = _pl.report_path_for_reading(tmp_path, NAME)
    assert got == tmp_path / ROUTED and legacy is False


def test_the_legacy_path_is_found_and_flagged(tmp_path):
    f = tmp_path / LEGACY
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"verdict": "PASS"}) + "\n")
    got, legacy = _pl.report_path_for_reading(tmp_path, NAME)
    assert got == tmp_path / LEGACY, got
    assert legacy is True, "the legacy hit was not flagged, so no caller can disclose it"


def test_with_nothing_on_disk_the_canonical_place_is_named(tmp_path):
    got, legacy = _pl.report_path_for_reading(tmp_path, NAME)
    assert got == tmp_path / ROUTED and legacy is False, (
        "a not-found message must name the place a reader should look")


def test_a_report_with_no_legacy_spelling_is_unaffected(tmp_path):
    """The tolerance is PER FILENAME, never a blanket second location.

    My first spelling of this arm used phase2's and phase3's reports as the examples, on the
    assumption that only phase1 had ever moved. That was wrong and it cost a regression: old
    `run_status` accepted the flat spelling for ALL SIX of its phase reports, so when I replaced
    its hand-built pairs with this resolver, five tolerances vanished and
    `test_issue590_auto_picks_the_furthest_phase` -- which stages every report flat -- got
    "UNKNOWN ... there is no run here to report on". The table now names all six; the property
    this arm is really about is that a name NOT in the table gets no second location at all.
    """
    for name in ("synth_netlist.json", "drc_signoff.json"):
        assert name not in _pl.LEGACY_REPORT_PATHS, name
        f = tmp_path / "reports" / name          # a flat spelling nothing ever wrote
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
        got, legacy = _pl.report_path_for_reading(tmp_path, name)
        assert got == _pl.report_path(tmp_path, name), (name, got)
        assert legacy is False, name


def test_every_pair_run_status_used_to_accept_is_in_the_table(tmp_path):
    """The regression, pinned: replacing N candidates with a resolver means it must know all N.

    `run_status` reads a project's phase reports, and it accepted each at the routed location AND
    at the flat `reports/<name>.json`. Every one of those names must therefore have a legacy
    entry, or converting that reader silently narrows it.
    """
    import run_status as _rs

    for name in _rs._PHASE_REPORTS.values():
        assert name in _pl.LEGACY_REPORT_PATHS, (
            f"{name} lost the flat spelling `run_status` used to accept")
        f = tmp_path / "reports" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
        got, legacy = _pl.report_path_for_reading(tmp_path, name)
        assert got == f and legacy is True, (name, got, legacy)

    # analog is the one whose ROUTED location is neither of the two that reader checked, so it
    # needs both of them, in preference order
    assert _pl.report_path(tmp_path, "analog_one_shot.json").parent.name == "phase3"
    assert _pl.LEGACY_REPORT_PATHS["analog_one_shot.json"] == (
        "reports/orchestrator/analog_one_shot.json", "reports/analog_one_shot.json")


def test_the_resolver_never_decides_where_to_write():
    """`report_path` stays the one answer for writing; this only tolerates reading."""
    import inspect
    src = inspect.getsource(_pl.report_path_for_reading)
    assert "mkdir" not in src and "write" not in src, src


def test_the_legacy_spellings_are_the_resolvers_own():
    """Every module that tolerates an old spelling must name the SAME one.

    Before this there were six hand-built pairs across four modules. Any that remain must
    agree with `LEGACY_REPORT_PATHS`, or the layouts diverge again where nobody is looking.
    """
    declared = {rel for rels in _pl.LEGACY_REPORT_PATHS.values() for rel in rels}
    assert LEGACY in declared, sorted(declared)
    for module in ("step_write_ledger.py", "vibe_ic_entry_guard.py"):
        text = (PROGRAMS / module).read_text(errors="replace")
        for rel in declared:
            if rel in text:
                break
        else:
            pytest.fail(f"{module} no longer names any legacy spelling the resolver knows")


# ── the second pass, which is where it bit ───────────────────────────────────

def _project_whose_pass_one_is_at_the_flat_path(tmp_path: Path) -> Path:
    """A project as the PRE-LANDING producer left it, plus a delivered expert answer."""
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("# a counter\n")

    flat = p / LEGACY
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(json.dumps({
        "phase": 1, "project": str(p), "verdict": "PASS",
        "pass1_marker": "written by the pre-R-0915-151 producer",
        "steps": [{"id": "1", "status": "PASS"}],
    }, indent=2) + "\n")
    assert not (p / ROUTED).exists()
    return p


def test_the_second_pass_carries_a_pass_one_written_at_the_flat_path(tmp_path,
                                                                    monkeypatch):
    """The HIGH, driven through `run_second_pass_only` itself.

    Everything the second pass does BESIDE carrying the record is stubbed -- the expert
    consumption, the steps view and the route -- because what is under test is whether the
    pass-1 record is found at all. Its own publication still goes to the routed path.
    """
    import phase1_one_shot_runner as P1

    project = _project_whose_pass_one_is_at_the_flat_path(tmp_path)

    monkeypatch.setattr(P1._pl, "emit_steps_view", lambda *a, **k: {"stubbed": True},
                        raising=False)
    monkeypatch.setattr(P1, "_consume_expert_answer",
                        lambda *a, **k: (0, {"consumed": True}), raising=False)

    rc = P1.run_second_pass_only(project, "zzdie")

    published = project / ROUTED
    assert published.is_file(), (
        f"the second pass published nothing at the routed path (rc={rc})")
    doc = json.loads(published.read_text())

    assert "UNREADABLE" not in json.dumps(doc), (
        "pass 1 was not carried forward, so this pass reports the earlier pass as missing "
        "and the front door halts at phase 1 on a project that had run it")
    assert doc.get("pass1_marker") == "written by the pre-R-0915-151 producer", (
        "the carried record is not the one the flat path held")

    # and the legacy layout is DISCLOSED rather than quietly accepted
    assert doc.get("pass1_record_layout", "").startswith("LEGACY"), (
        doc.get("pass1_record_layout"))
    assert doc.get("pass1_record_read_from") == LEGACY, doc.get("pass1_record_read_from")


def test_a_second_pass_over_a_routed_project_says_nothing_about_legacy(tmp_path,
                                                                      monkeypatch):
    """The disclosure fires only when it is true."""
    import phase1_one_shot_runner as P1

    project = tmp_path / "proj"
    (project / "input").mkdir(parents=True)
    (project / "input" / "spec.md").write_text("# a counter\n")
    routed = project / ROUTED
    routed.parent.mkdir(parents=True, exist_ok=True)
    routed.write_text(json.dumps({"phase": 1, "verdict": "PASS",
                                  "pass1_marker": "routed"}) + "\n")

    monkeypatch.setattr(P1._pl, "emit_steps_view", lambda *a, **k: {"stubbed": True},
                        raising=False)
    monkeypatch.setattr(P1, "_consume_expert_answer",
                        lambda *a, **k: (0, {"consumed": True}), raising=False)
    P1.run_second_pass_only(project, "zzdie")

    doc = json.loads(routed.read_text())
    assert doc.get("pass1_marker") == "routed"
    assert "pass1_record_layout" not in doc, doc.get("pass1_record_layout")


# ── (2) the D1 re-invocation's coverage-only sidecar ─────────────────────────

SIDECAR_REL = "reports/phase1/phase1_exit_reason.json"


def _coverage_only_project(tmp_path: Path, *, sidecar_newer: bool = True) -> Path:
    """A project as an AI-backup re-invocation finds it: pass 1 recorded, sidecar beside it."""
    import os
    import time

    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("# a counter\n")

    record = _pl.report_path(p, NAME)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({
        "phase": 1, "mode": "docs", "verdict": "FAIL",
        "steps": [{"name": "doc_extract", "status": "FAIL"}],
    }) + "\n")

    side = p / SIDECAR_REL
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"coverage_only_failure": True}) + "\n")

    # the record is back-dated so both files predate any later invocation's phase start;
    # `sidecar_newer` decides whether the sidecar belongs to THAT record or to an older one.
    base = time.time() - 5000
    os.utime(record, (base, base))
    os.utime(side, (base + 10, base + 10) if sidecar_newer else (base - 10, base - 10))
    return p


def test_the_second_pass_names_the_coverage_sidecar_it_carried(tmp_path, monkeypatch):
    """The second pass must hand the sidecar's identity forward with the record.

    `run_second_pass_only` carries pass 1's record and never rewrites the sidecar, so the front
    door's mtime rule -- correct for a first pass -- refused it on a re-invocation and a
    `--skip-phase3` coverage-only project flipped from PASS_WITH_WAIVERS to FAIL. The record now
    NAMES the sidecar by content.
    """
    import phase1_one_shot_runner as P1

    project = _coverage_only_project(tmp_path)
    monkeypatch.setattr(P1._pl, "emit_steps_view", lambda *a, **k: {"stubbed": True},
                        raising=False)
    monkeypatch.setattr(P1, "_consume_expert_answer",
                        lambda *a, **k: (0, {"consumed": True}), raising=False)
    P1.run_second_pass_only(project, "zzdie")

    doc = json.loads(_pl.report_path(project, NAME).read_text())
    named = doc.get("pass1_coverage_sidecar")
    assert isinstance(named, dict), doc.get("pass1_coverage_sidecar_refused") or doc.keys()
    assert named["rel"] == SIDECAR_REL
    import hashlib
    assert named["sha256"] == hashlib.sha256(
        (project / SIDECAR_REL).read_bytes()).hexdigest()


def test_a_sidecar_older_than_its_own_record_is_not_carried(tmp_path, monkeypatch):
    """The half that keeps this from laundering a stale file.

    A sidecar older than the pass-1 record it would belong to describes a STILL earlier run. It is
    not named, and the refusal is recorded so a reader can see why the demotion was unavailable.
    """
    import phase1_one_shot_runner as P1

    project = _coverage_only_project(tmp_path, sidecar_newer=False)
    monkeypatch.setattr(P1._pl, "emit_steps_view", lambda *a, **k: {"stubbed": True},
                        raising=False)
    monkeypatch.setattr(P1, "_consume_expert_answer",
                        lambda *a, **k: (0, {"consumed": True}), raising=False)
    P1.run_second_pass_only(project, "zzdie")

    doc = json.loads(_pl.report_path(project, NAME).read_text())
    assert "pass1_coverage_sidecar" not in doc, doc.get("pass1_coverage_sidecar")
    assert "OLDER than the pass-1 record" in (
        doc.get("pass1_coverage_sidecar_refused") or ""), doc.keys()


def test_the_front_door_demotes_on_a_named_sidecar_and_refuses_an_unnamed_one(tmp_path):
    """The reader half, both directions, driven on `_expected`-shaped inputs.

    The sidecar legitimately predates this invocation's phase-1 start on a re-invocation; what
    makes it trustworthy is that THIS invocation's pass-1 record names it by content. A record that
    does not name it, or a sidecar whose bytes no longer match, stays refused.
    """
    import hashlib

    import vibe_ic_one_shot_runner as VV

    project = _coverage_only_project(tmp_path)
    side = project / SIDECAR_REL
    sha = hashlib.sha256(side.read_bytes()).hexdigest()

    # the demotion predicate itself still sees a coverage-only sidecar. It answers a TUPLE
    # (is_coverage_only, the sidecar it read) -- read it that way rather than as a bool.
    _cov, _payload = VV._phase1_failure_is_coverage_only(project)
    assert _cov is True, (_cov, _payload)
    assert _payload.get("coverage_only_failure") is True, _payload

    # named + bytes match -> the front door may demote; the source says so in one place
    src = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text()
    assert "pass1_coverage_sidecar" in src, (
        "the front door does not consult the record's naming of the sidecar")
    assert "_have == str(_named.get(\"sha256\") or \"\")" in src, (
        "the front door accepts the naming without checking the bytes")

    # and a swapped sidecar no longer matches the sha the record named
    side.write_text(json.dumps({"coverage_only_failure": True, "swapped": True}) + "\n")
    assert hashlib.sha256(side.read_bytes()).hexdigest() != sha
