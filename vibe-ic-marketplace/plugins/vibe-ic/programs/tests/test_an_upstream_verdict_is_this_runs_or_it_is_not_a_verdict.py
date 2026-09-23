"""An upstream verdict is THIS RUN's, or it is not a verdict.

R-0915-152. `release_docs_check` excuses 37.5ic's missing release documents when an UPSTREAM
step's own verdict says that step is blocked. The authority is right (R-0915-149); the
RECORD it read was not, in three independent ways, and each one is measured here rather than
argued:

  1. THE NAME. `reports/metrics/{sid}.json` is not what `step_metrics.emit` writes -- it
     normalises the id first, so step `37.4`'s row is `37_4.json` under key
     `37_4__flow__step_status`, and `M1`'s is `m1.json`. Every dotted or suffixed step was
     therefore looked for where nothing writes. The old code returned ABSENT for those, ABSENT
     is not a blocking tier, and a genuinely FAILED upstream step was recorded as passing
     while 37.5ic took the blame.
  2. THE RUN. That file persists across runs, and nothing in it said which invocation
     measured it, so one failed pass excused 37.5ic forever.
  3. THE SELF-FREEZE. `_emit_step_metrics` could not refresh its own `step_status`: the
     no-clobber filter that stops the wrapper overwriting a PROGRAM's measurement also
     stopped it overwriting its OWN previous value. Pass 1 FAIL, pass 2 PASS, file still
     FAIL -- driven twice below, not reasoned about.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC          # noqa: E402
import release_docs_check as RDC             # noqa: E402
import step_metrics as SM                    # noqa: E402

INV_ENV = "VIBEIC_FCC_INVOCATION"


class _Result:
    def __init__(self, status: str) -> None:
        self.status = status


@pytest.fixture()
def mine(monkeypatch):
    """This gate runs as a child of the audit, so it inherits the invocation id."""
    monkeypatch.setenv(INV_ENV, "THIS-RUN-1")
    return "THIS-RUN-1"


# ── 1. the name step_metrics actually writes ─────────────────────────────────

@pytest.mark.parametrize("sid", ["37.4", "37.5ic", "0.5ic", "M1", "23", "33"])
def test_the_verdict_is_read_from_the_file_step_metrics_writes(sid, tmp_path, mine):
    """Driven through `step_metrics.emit`, so the reader and the writer cannot disagree."""
    SM.emit(tmp_path, sid, {"step_status": "FAIL", "invocation": mine})

    written = tmp_path / SM.METRICS_REL / f"{SM.normalize_step(sid)}.json"
    assert written.is_file(), f"emit did not write {written}"

    status, why = RDC.step_verdict_now(tmp_path, sid)
    assert (status, why) == ("FAIL", ""), (
        f"step {sid}'s own FAIL was not read from {written.name}: {status} {why}")
    assert status in RDC._BLOCKING_STEP_STATUSES


def test_the_reader_does_not_spell_the_name_itself():
    """The normalisation is imported from the module that owns it, not reproduced."""
    import inspect
    src = inspect.getsource(RDC._metrics_row)
    assert "normalize_step" in src and "METRICS_REL" in src, src
    # and the un-normalised format string is no longer used to find the row
    assert "_STEP_STATUS_REL_FMT.format" in src, (
        "the fallback for a missing step_metrics import should still name a path")
    body = inspect.getsource(RDC.step_verdict_now)
    assert "_STEP_STATUS_REL_FMT" not in body, (
        "the verdict reader still builds the path itself instead of asking _metrics_row")


# ── 2. this run, not an earlier one ──────────────────────────────────────────

def test_a_row_from_another_invocation_is_not_this_runs_verdict(tmp_path, mine):
    SM.emit(tmp_path, "23", {"step_status": "PASS", "invocation": "AN-EARLIER-RUN"})
    status, why = RDC.step_verdict_now(tmp_path, "23")
    assert status == RDC._VERDICT_UNREADABLE, (status, why)
    assert "AN-EARLIER-RUN" in why and mine in why, why


def test_a_row_naming_no_invocation_cannot_be_shown_to_be_this_runs(tmp_path, mine):
    SM.emit(tmp_path, "23", {"step_status": "PASS"})
    status, why = RDC.step_verdict_now(tmp_path, "23")
    assert status == RDC._VERDICT_UNREADABLE, (status, why)
    assert "names no invocation" in why, why


def test_an_absent_row_is_unreadable_and_says_so(tmp_path, mine):
    """The threaded path: the upstream step may not have been evaluated yet.

    The old code answered "ABSENT", which is not a blocking tier and so was filed as the
    step PASSING -- silence read as a measurement. `UNREADABLE` is neither: it is not a
    blockage and it is not the step passing, and it carries its reason.
    """
    status, why = RDC.step_verdict_now(tmp_path, "33")
    assert status == RDC._VERDICT_UNREADABLE, (status, why)
    assert "no step record" in why and "33.json" in why, why
    assert status not in RDC._BLOCKING_STEP_STATUSES


def test_an_unreadable_upstream_verdict_is_named_in_the_published_record(tmp_path, mine):
    """An excuse may rest on a gap -- but never on an unnamed one.

    Driven through `upstream_blockage_now`, so the published record is the real one: the
    artefact reader names `post_route_summary.json`, the flow says which step declares it,
    and that step has no row at all in this project -- the threaded-path case.
    """
    rel = "reports/phase3/sta/post_route_summary.json"
    sid = RDC._declaring_step(rel)
    assert sid, "the flow no longer declares that path; re-measure this arm's premise"

    status, why = RDC.step_verdict_now(tmp_path, sid)
    assert (status, bool(why)) == (RDC._VERDICT_UNREADABLE, True), (status, why)

    out = RDC.upstream_blockage_now(tmp_path, "a")
    assert "upstream_verdicts_unreadable" in out, sorted(out)
    assert out["upstream_verdicts_unreadable"], (
        "an upstream verdict could not be read and the record does not name it")
    for s_, entry in out["upstream_verdicts_unreadable"].items():
        assert entry["status"] == RDC._VERDICT_UNREADABLE, (s_, entry)
        assert entry["reason"], (s_, entry)
        assert entry["verdict_readable"] is False, (s_, entry)
        # and it is NOT also filed as a blockage or as the step passing
        assert s_ not in (out.get("blocking_steps") or {}), s_
        assert s_ not in (out.get("upstream_steps_passing") or {}), s_


def test_an_unreadable_verdict_does_not_excuse_the_missing_documents(tmp_path, mine):
    """THE DIRECTION, and it is the one 72c's own ruling already fixed.

    `test_a_step_with_no_published_verdict_is_not_an_excuse` holds that "a step that has
    published NOTHING in this run has no verdict, so there is no evidence of blockage and
    the documents are still owed; 'no evidence' must never read as 'blocked'". Reporting an
    unreadable record as `NOT_MEASURED` would have inverted that, because `NOT_MEASURED` is
    one of `_BLOCKING_STEP_STATUSES` -- deleting every `reports/metrics/*.json` would then
    forgive 37.5ic's missing documents for ever. So `UNREADABLE` is its own tier, outside
    that set.
    """
    assert RDC._VERDICT_UNREADABLE not in RDC._BLOCKING_STEP_STATUSES

    out = RDC.upstream_blockage_now(tmp_path, "a")
    assert out["blocked"] is False, (
        "an upstream verdict nobody could read excused this release's missing documents")


# ── 3. the wrapper's own key is not frozen by the wrapper ────────────────────

def test_the_wrapper_can_refresh_its_own_step_status(tmp_path):
    """Two passes over one project, through the real emitter."""
    step = {"id": "23", "required_outputs": []}
    row = tmp_path / SM.METRICS_REL / "23.json"

    FCC._emit_step_metrics(tmp_path, step, _Result("FAIL"))
    assert json.loads(row.read_text())["23__flow__step_status"] == "FAIL"

    FCC._emit_step_metrics(tmp_path, step, _Result("PASS"))
    assert json.loads(row.read_text())["23__flow__step_status"] == "PASS", (
        "the wrapper could not refresh its OWN key, so a step that failed once reads FAIL "
        "for the rest of this tree's life")


def test_a_programs_own_measurement_is_still_protected(tmp_path):
    """The filter's real purpose must not move: only the wrapper's own keys are exempt.

    THE COLLISION HAS TO BE REAL for this to measure anything. My first spelling emitted a
    program's key and then ran the wrapper over a step with NO `required_outputs` -- so the
    wrapper had no value for that key and could not have overwritten it whatever the
    exemption said. The arm passed for a reason unrelated to the rule. Here the step
    DECLARES a report that carries the same key with a different number, so the wrapper
    genuinely wants to write it and the exemption is what stops it.
    """
    rel = "reports/phase3/area.json"
    report = tmp_path / rel
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"instance_area": 999}) + "\n")

    SM.emit(tmp_path, "24", {"instance_area": 42})          # the program's own measurement
    FCC._emit_step_metrics(tmp_path, {"id": "24", "required_outputs": [rel]},
                           _Result("PASS"))

    row = json.loads((tmp_path / SM.METRICS_REL / "24.json").read_text())
    assert row["24__flow__instance_area"] == 42, (
        "the wrapper overwrote a program's own measurement with the value it forwarded "
        "from that step's report -- the exemption is too wide")
    assert row["24__flow__step_status"] == "PASS"


def test_the_wrapper_records_which_invocation_measured_the_step(tmp_path):
    FCC._emit_step_metrics(tmp_path, {"id": "23", "required_outputs": []},
                           _Result("PASS"))
    row = json.loads((tmp_path / SM.METRICS_REL / "23.json").read_text())
    assert row["23__flow__invocation"] == FCC._invocation_id()


def test_the_exemption_names_only_the_wrappers_own_keys():
    """Pinned against the source, so the exemption cannot quietly widen."""
    import inspect
    src = inspect.getsource(FCC._emit_step_metrics)
    assert '_own_keys = ("step_status", "invocation")' in src, (
        "the no-clobber exemption is spelled differently; check it still names only the "
        "keys this wrapper is the sole author of")
    assert "if k in _own_keys" in src


# ── an upstream blockage is not yet scoped to the artefact, and why ──────────

def test_the_presence_check_understands_the_flows_own_spec_shapes(tmp_path):
    """`_artefact_present` is correct and is NOT what decides an excuse.

    R-0915-160 asks that an upstream blockage excuse this step only when it is ABOUT the
    artefact this step needed. Nothing on disk at gate time says which artefact an upstream
    verdict is about -- `reports/metrics/<sid>.json` carries scalars, and this run's compliance
    report is not written until after every step. "Did the step produce it" looked like a proxy
    and the tree refuted it: a GDS PRESENT but carrying no geometry with step 37 FAIL must
    excuse (four arms in `test_a_producers_stated_refusal_is_not_a_documentation_defect` hold
    that), while `power.json` PRESENT and carrying no number by design with step 33
    NOT_MEASURED must not. Both are "blocking tier + artefact present".

    So the helper stays, correct and unused by the classification, and the reason is recorded
    in `upstream_blockage_now`. This arm pins the helper's own behaviour and the fact that the
    classification does not consult it -- so when the producer change that makes the rule
    implementable lands, this is where it starts.
    """
    gds = tmp_path / "phase3/stage4/gds"
    gds.mkdir(parents=True)
    (gds / "zzdie.gds").write_bytes(b"\x00" * 64)
    assert RDC._artefact_present(tmp_path, "phase3/stage4/gds/*.gds") is True
    assert RDC._artefact_present(tmp_path, "nope/*.gds") is False
    assert RDC._artefact_present(tmp_path,
                                 "nope.json OR phase3/stage4/gds/zzdie.gds") is True
    assert RDC._artefact_present(tmp_path, "") is False

    import inspect
    src = inspect.getsource(RDC.upstream_blockage_now)
    assert "_artefact_present" not in src, (
        "the excuse is being scoped by presence, which reverses four accepted arms")
    assert "MEASURED refusal" in src, (
        "the reason the excuse is not scoped must stay written where the rule would go")


# ── the row a dependent step sees must not depend on the worker pool ─────────

def test_the_steps_that_read_another_steps_verdict_are_derived_not_tabled():
    """The set comes from the tree: a new such program is picked up without an edit."""
    import yaml as _yaml
    flow = _yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "flow"
         / "phase1_phase2_phase3.yaml").read_text())
    steps = flow.get("steps") or []

    readers = FCC._reads_another_steps_verdict()
    assert "release_docs_check" in readers, sorted(readers)
    # the EMITTER is not a dependent reader of its own rows
    assert "flow_compliance_check" not in readers
    for wrapper in ("stage2_compliance", "stage3_compliance"):
        assert wrapper not in readers, wrapper

    dependent = FCC.steps_that_read_another_steps_verdict(steps)
    assert dependent == ["37.5ip", "37.5ic"], dependent
    assert len(dependent) < len(steps) / 4, (
        f"{len(dependent)} of {len(steps)} steps in the second wave would cost real "
        f"concurrency; the point of deriving the set is that it is small")


def test_the_dependent_steps_are_evaluated_after_every_other_step():
    """Pinned against the scheduler's source: two waves, dependent ones last.

    A behaviour arm here would need a full 70-step audit twice; what must hold is the
    ORDER, and that is a property of this block.
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(FCC.main))
    assert "steps_that_read_another_steps_verdict" in src, (
        "the parallel path no longer asks which steps depend on another's row")
    assert "_wave1" in src and "_wave2" in src, src[:200]

    # and the results are rebuilt in the ORIGINAL order, so the waves are invisible
    assert "for _step in _eval_steps:" in src and "_by_id[str(_step.get(\"id\"))]" in src, (
        "results are appended per wave, so the parallel path no longer produces the same "
        "list as the sequential one")


def test_a_dependent_step_reads_a_row_written_before_it_runs(tmp_path, mine):
    """The property the waves buy, at the level the reader cares about.

    Wave 1 emits every other step's row; wave 2 then evaluates the dependent ones. So by the
    time `release_docs_check` runs, the rows for the steps it reads are on disk and carry THIS
    invocation -- which is exactly what `step_verdict_now` requires. Here the upstream rows are
    written first, as wave 1 does, and the read is the one the gate performs.
    """
    for sid in ("23", "33", "37", "37.4"):
        SM.emit(tmp_path, sid, {"step_status": "PASS", "invocation": mine})
    for sid in ("23", "33", "37", "37.4"):
        status, why = RDC.step_verdict_now(tmp_path, sid)
        assert (status, why) == ("PASS", ""), (sid, status, why)

    # and with wave 1's rows NOT yet written -- the pre-fix parallel race -- every one of
    # them is UNREADABLE rather than silently passing
    empty = tmp_path / "empty"
    empty.mkdir()
    for sid in ("23", "33", "37", "37.4"):
        assert RDC.step_verdict_now(empty, sid)[0] == RDC._VERDICT_UNREADABLE, sid
