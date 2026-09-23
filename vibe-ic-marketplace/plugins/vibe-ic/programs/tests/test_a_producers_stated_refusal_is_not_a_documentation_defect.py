"""A producer's stated refusal is an upstream fact, not negligence by the writer.

MEASURED on spm run22, READ-ONLY. Step 37.5ic published:

    [FAIL] release_docs_check — RELEASE_DOCUMENTATION_ABSENT (spm): the run signs
    off layout `spm` under phase3/stage4/gds and
    phase3/stage4/documentation/ic/spm carries no release documentation.

Every word of that is true. And in the same run tree, from the run's own
orchestrator record:

    ic_release_docs_gen   status NOT_MEASURED   duration 2.26 s   producer_rc 1
      flow_step 37.5ic    output_files []
      "NOT RELEASABLE — no product documents written. 1 release(s) examined.;
       the artefacts this run signed off carry no substance (2 refusal(s)):
         STA_NO_SLACK  [sta]   reports/phase3/sta/post_route_summary.json
         POWER_NO_TOTAL [power] reports/phase3/power.json
       A release document for a run that did not pass is worse than no document."

I verified both of the producer's reasons myself rather than repeating its word:
`post_route_summary.json` is 1128 bytes with keys [findings, passed, program,
subject, summary] and carries NO numeric slack field anywhere; `power.json` is 152
bytes with keys [analysis_mode, evidence, source, tool, verdict] and carries NO
numeric power or total field. The producer ran, and it was RIGHT to refuse.

So the documents are OWED (step 37.5ic declares PRELIMINARY_DATASHEET.md and
RELEASE_NOTES.md under phase3/stage4/documentation/ic/*/ and names
`ic_release_docs_gen` under `programs:`), the producer exists and ran, and the only
thing wrong was the CLASSIFICATION: a refusal that survived solely as prose, in a
log and an orchestrator report this gate cannot read, got re-described downstream
as "nobody wrote them".

R-0915-146 — AND THE FIRST CUT OF THIS WAS WRONG IN THE WAY I KNOW BEST. It granted
BLOCKED_BY_UPSTREAM on the RECORD'S SHAPE: a `program`, `verdict: REFUSED`, one or
more reason strings. That is exactly what R-0915-136 names — a program's CLAIM about
a state is not the state — applied to my own change. Three confirmed consequences:

  * STALE: run N refuses for STA_NO_SLACK / POWER_NO_TOTAL, the STA and power
    records are then fixed WITHOUT the producer re-running (the audit never runs
    `ic_release_docs_gen`; it is not in `_PRE_AUDIT_PRODUCERS`), and a re-audit still
    answers NOT_MEASURED/BLOCKED — blaming an STA that is fine, where the step owes
    its documents and FAIL is correct. A hand-written record works identically; the
    record was bound to no file, no digest, no tree_sha.
  * A CRASH READ AS BLOCKAGE: `_clear_refusal` ran only on the success tail, so any
    exception left the previous run's record in place and rc 1 read as "declined".
  * A FOREIGN RELEASE: a leftover record for `spm` excused an absent `spm_v2`.

So the gate now RE-DERIVES the blockage per release, on the current tree, with the
producer's OWN predicates (`_ic_release_artefacts.audit(project, release).errors`,
`tapeout_docs_gen.release_blockers(load_metrics(...))`) — both pure readers, both
imported rather than reimplemented. Every failing release must be blocked NOW, so a
sibling's blockage cannot excuse another's absence. The record survives as a POINTER
in the disclosure and is never the evidence, which the report says in as many words
(`producer_refusal_is_evidence: false`). And the producer clears any stale record
BEFORE anything can raise, so a crash leaves no account of a refusal that did not
happen.

WHAT THIS CHANGE DOES, AND WHAT IT REFUSES TO DO.
  * the producer records its refusal as DATA beside the prose it already prints
    (`reports/phase3/release_docs_producer_refusal.json`), from the same reasons,
    so the record can never say something the log does not;
  * a later SUCCESS clears that record, because a stale refusal would tell this
    gate for ever that the producer had declined;
  * the gate reads it and publishes NOT_MEASURED with
    `reason_class = BLOCKED_BY_UPSTREAM` — a class `_flow_reason_taxonomy` holds
    in INCOMPLETE and NOT in SKIP_ELIGIBLE, so the step stays OWED and cannot be
    read as satisfied — at rc 2, the non-verdict exit, NOT the VACUOUS_PASS
    announcement that the audit promotes to a pass tier;
  * NOTHING becomes a pass, and THE FAIL ARM IS KEPT: an absent document set with
    no refusal record is still FAIL/RELEASE_DOCUMENTATION_ABSENT, which is the
    defect this gate was written for. Documents that exist and fail on their
    content are still FAIL.

The two upstream records are the real work and they are NOT fixed here: a
post-route STA summary with no slack number and a power record with no power
number belong to whoever owns those two artefacts. Writing release documents over
them is precisely the cheat the producer refuses.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import _flow_reason_taxonomy as _T                            # noqa: E402
import ic_release_docs_gen as GEN                             # noqa: E402
import release_docs_check as CHK                              # noqa: E402
import flow_compliance_check as _FCC       # noqa: E402


def _signed_off_die(project: Path, release: str = "zzdie") -> None:
    """The state that makes the document set OWED: a sign-off layout exists."""
    gds = project / "phase3/stage4/gds"
    gds.mkdir(parents=True, exist_ok=True)
    (gds / f"{release}.gds").write_bytes(b"HEADER\x00" * 64)


def _refusal(project: Path, *, program: str = "ic_release_docs_gen",
             verdict: str = "REFUSED", reasons=("STA_NO_SLACK [sta] ...",),
             blockers=()) -> Path:
    out = project / CHK.PRODUCER_REFUSAL_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "program": program, "verdict": verdict,
        "releases_examined": 1, "releases": ["zzdie"],
        "substance_refusals": list(reasons),
        "release_blockers": list(blockers),
        "documents_written": [],
        "why": "a release document for a run that did not pass is worse than none",
    }) + "\n")
    return out


# ── the two paths the gate now distinguishes ───────────────────────────────

def test_absent_documents_over_a_healthy_upstream_is_still_a_fail(tmp_path):
    """THE ARM THAT MUST NOT MOVE, re-pinned to the rule as it now stands.

    It used to read "absent documents and NO RECORD is a FAIL", which was the right
    statement while the record was the evidence. Under R-0915-146 the question is
    the TREE: a die signed off whose upstream is healthy owes its documents, record
    or no record, and that is still FAIL on content. The no-record half is now
    covered by `test_a_genuine_blockage_needs_no_record_at_all` from the other
    side.
    """
    _healthy_upstream(tmp_path)
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "FAIL", result.summary
    assert result.passed is False
    assert [f.rule for f in result.findings] == ["RELEASE_DOCUMENTATION_ABSENT"]
    assert result.summary.get("reason_class") in (None, ""), result.summary


def test_absent_documents_over_a_blocked_upstream_is_not_measured(tmp_path):
    _blocked_upstream(tmp_path)
    _refusal(tmp_path, reasons=(
        "STA_NO_SLACK [sta] `reports/phase3/sta/post_route_summary.json`: no slack",
        "POWER_NO_TOTAL [power] `reports/phase3/power.json`: no power number"))
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "NOT_MEASURED", result.summary
    assert result.passed is False, "a blocked step is not a pass"
    assert result.summary["reason_class"] == _T.BLOCKED_BY_UPSTREAM
    assert result.summary["reason"] == "release_docs_upstream_blocked"
    # the reasons published are the ones OBSERVED on the tree, not the record's
    assert result.summary["producer_refusals"], result.summary
    assert result.summary["producer_refusal_is_evidence"] is False


def test_the_class_keeps_the_step_owed(tmp_path):
    """The load-bearing property of the class chosen: BLOCKED_BY_UPSTREAM is in
    INCOMPLETE and NOT in SKIP_ELIGIBLE, so nothing reads the step as satisfied.
    If this ever flips, a blocked release-docs step becomes a silent skip."""
    assert _T.BLOCKED_BY_UPSTREAM in _T.INCOMPLETE
    assert _T.BLOCKED_BY_UPSTREAM not in _T.SKIP_ELIGIBLE


def test_the_exit_is_the_non_verdict_code_and_not_a_vacuous_pass(tmp_path):
    """rc 2 carries "this is not a verdict"; the VACUOUS_PASS sentinel would be
    promoted to a pass tier by the audit, so it must NOT be printed here."""
    _blocked_upstream(tmp_path)
    _refusal(tmp_path)
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "release_docs_check.py"), str(tmp_path),
         "--arm", "ic", "--json", str(tmp_path / "reports/phase3/rd.json")],
        capture_output=True, text=True, timeout=600)
    assert r.returncode == 2, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert "VACUOUS_PASS" not in r.stdout and "VACUOUS_PASS" not in r.stderr, (
        "the vacuous sentinel promotes this to a pass tier; an upstream-blocked "
        "step is not a pass")
    assert "NOT MEASURED" in r.stderr, r.stderr[-400:]
    doc = json.loads((tmp_path / "reports/phase3/rd.json").read_text())
    assert doc["summary"]["reason_class"] == _T.BLOCKED_BY_UPSTREAM
    # REMOVING THE RECORD CHANGES NOTHING, which is the whole point: the tree is
    # still blocked, so the verdict is still the non-verdict exit.
    (tmp_path / CHK.PRODUCER_REFUSAL_REL).unlink()
    r2 = subprocess.run(
        [sys.executable, str(PROGRAMS / "release_docs_check.py"), str(tmp_path),
         "--arm", "ic"], capture_output=True, text=True, timeout=600)
    assert r2.returncode == 2, (r2.returncode, r2.stdout[-300:])

    # and a HEALTHY upstream with absent documents still exits 1 — the FAIL arm the
    # review asked me to keep, now keyed on the tree instead of on a record.
    import shutil
    healthy = tmp_path / "healthy"
    healthy.mkdir()
    _healthy_upstream(healthy)
    r3 = subprocess.run(
        [sys.executable, str(PROGRAMS / "release_docs_check.py"), str(healthy),
         "--arm", "ic"], capture_output=True, text=True, timeout=600)
    assert r3.returncode == 1, (r3.returncode, r3.stdout[-300:])


# ── the record is read strictly ────────────────────────────────────────────

def test_a_refusal_naming_no_reason_is_not_evidence(tmp_path):
    _healthy_upstream(tmp_path)
    _refusal(tmp_path, reasons=(), blockers=())
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_another_programs_document_is_not_this_producers_refusal(tmp_path):
    _healthy_upstream(tmp_path)
    _refusal(tmp_path, program="somebody_else_gen")
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_a_record_that_does_not_say_refused_is_not_a_refusal(tmp_path):
    _healthy_upstream(tmp_path)
    _refusal(tmp_path, verdict="PASS")
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_an_unreadable_record_is_not_a_refusal(tmp_path):
    _healthy_upstream(tmp_path)
    out = tmp_path / CHK.PRODUCER_REFUSAL_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not json\n")
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


# ── the producer's half ────────────────────────────────────────────────────

def test_the_producer_writes_the_record_when_it_refuses(tmp_path):
    """END TO END on the producer: an empty project makes it refuse, and the
    refusal must land as data at the path the gate reads."""
    _signed_off_die(tmp_path)
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "ic_release_docs_gen.py"), str(tmp_path)],
        capture_output=True, text=True, timeout=900)
    rec = tmp_path / GEN.REFUSAL_REL
    if r.returncode == 1:
        assert rec.is_file(), (r.returncode, r.stderr[-600:])
        doc = json.loads(rec.read_text())
        assert doc["program"] == "ic_release_docs_gen"
        assert doc["verdict"] == "REFUSED"
        assert doc["substance_refusals"] or doc["release_blockers"], doc
        assert doc["documents_written"] == []
        # and the RECORD is still only a pointer: with no upstream step publishing a
        # blocking verdict on this tree, the documents are owed and the verdict is
        # FAIL however emphatic the record is.
        assert CHK.producer_refusal(tmp_path) is not None
        assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"
    else:
        # The producer took a different path on this fixture (vacuous: no
        # artefact class at all). Then it must NOT have written a refusal —
        # a record of a refusal that did not happen is the fail-open direction.
        assert not rec.exists(), (r.returncode, r.stdout[-400:])


def test_the_two_paths_agree_on_the_same_path_constant():
    """The producer writes where the gate reads. Two spellings of one path is the
    defect that makes a record invisible."""
    assert GEN.REFUSAL_REL == CHK.PRODUCER_REFUSAL_REL


def test_a_success_clears_a_previous_refusal_record(tmp_path):
    """THE FAIL-OPEN DIRECTION, closed: a record that outlived its refusal would
    tell the gate for ever that the producer had declined."""
    rec = tmp_path / GEN.REFUSAL_REL
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text('{"program": "ic_release_docs_gen", "verdict": "REFUSED"}\n')
    GEN._clear_refusal(tmp_path)
    assert not rec.exists()
    # idempotent: clearing an absent record is not an error
    GEN._clear_refusal(tmp_path)


def test_clearing_is_wired_into_the_producers_success_path():
    """Source pin: the success return must be preceded by the clear. Anchored on
    the CALL NAME, because an anchor on surrounding text breaks for no reason."""
    import ast
    src = (PROGRAMS / "ic_release_docs_gen.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_clear_refusal"]
    assert calls, ("nothing clears a stale refusal record on the producer's "
                   "success path")


# ── R-0915-146: the blockage is RE-DERIVED, the record is only a pointer ────

#: A GDS whose record walk finds real geometry: length-4 BOUNDARY records, the same
#: walk `analog_a5_layout_check._gds_geometry_count` performs (2-byte big-endian
#: length, record type at offset 2). Synthesised rather than copied from a run,
#: because a fixture that needs a real 100 MB layout is a fixture nobody can run.
_GDS_WITH_GEOMETRY = b"\x00\x04\x08\x00" * 64


#: A metrics record whose every sign-off property PASSES its own predicate. Built
#: from `tapeout_docs_gen.MANUFACTURABILITY + ELECTRICAL` at runtime rather than
#: typed, so a new property added there cannot leave this fixture silently
#: "healthy" while the shipped predicate has something new to say about it.
def _passing_metrics() -> dict:
    import tapeout_docs_gen as _tap
    out = {}
    for _label, key, pred in _tap.MANUFACTURABILITY + _tap.ELECTRICAL:
        value = 0 if pred(0) else 1
        assert pred(value), (key, value)
        out[key] = value
    return out


def _step_status(project: Path, sid: str, status: str) -> None:
    """A step's OWN published verdict for THIS RUN, written the way the flow writes it.

    THROUGH `step_metrics.emit`, not by hand. R-0915-152. My earlier spelling built
    `reports/metrics/{sid}.json` and the key `{sid}__flow__step_status` itself, and both
    are wrong for a dotted or suffixed id: `emit` NORMALISES first, so step 37.4's row is
    `37_4.json` under `37_4__flow__step_status` and M1's is `m1.json`. A fixture that spells
    a producer's filename itself proves nothing about the reader -- the same mistake as
    writing a report where no producer writes it.

    It also carries the INVOCATION, because that is what makes the row this run's rather
    than a leftover, and the reader now requires it.
    """
    import step_metrics as _sm                              # noqa: PLC0415
    _sm.emit(project, sid, {"step_status": status,
                            "invocation": _FCC._invocation_id()})


def _step_outputs(project: Path, sid: str, status: str,
                  required: "list[str]", reasons: "list[str]") -> None:
    """The step's own per-step OUTPUT record, written the way the auditor writes it.

    R-0915-165. Through `flow_compliance_check._emit_step_output_record`, not by hand: the
    record's shape, its path (normalised sid), its invocation stamp and -- crucially -- WHICH of
    the declared outputs land in `missing` versus `unusable` are all that function's decisions.
    A fixture that spelled them itself would be asserting against its own opinion of the
    producer.
    """
    class _R:                                                # the shape the writer reads
        pass
    r = _R()
    r.status = status
    r.reasons = list(reasons)
    _FCC._emit_step_output_record(project, {"id": sid, "required_outputs": list(required)}, r)


def _forget_step_status(project: Path, sid: str) -> None:
    """Remove a step's row, by the name `step_metrics` actually writes."""
    import step_metrics as _sm                              # noqa: PLC0415
    (project / _sm.METRICS_REL / f"{_sm.normalize_step(sid)}.json").unlink(
        missing_ok=True)


def _real_writer_formats(project: Path) -> None:
    """THE FLOW'S OWN HEALTHY RECORDS, in the formats the real writers use.

    This is the fixture my previous cut did not have, and its absence is why the
    defect survived: I wrote NO sta/power files at all and called that healthy.

      * the runner writes reports/phase3/power.json with
        {tool, source, analysis_mode, verdict, evidence} and NO NUMBER -- by design;
        `eda_report_audit` says so in as many words ("It carries no number of its
        own").
      * `sta_report_check` writes a summary carrying the BOOL `has_wns_tns`, which
        the producer's number scan skips because `_numbers_under_key` ignores bools.

    So both records are HEALTHY and the producer's predicates still call them hollow.
    A gate that reads those predicates as blockage blames steps 23 and 33 while their
    own gates PASS -- which is the whole finding.
    """
    pw = project / "reports/phase3/power.json"
    pw.parent.mkdir(parents=True, exist_ok=True)
    pw.write_text(json.dumps({"tool": "opensta",
                              "source": "phase3/stage3/pnr/power.rpt",
                              "analysis_mode": "vectorless_sdc",
                              "verdict": "PASS",
                              "evidence": "report_power output below"},
                             indent=2) + "\n")
    sta = project / "reports/phase3/sta/post_route_summary.json"
    sta.parent.mkdir(parents=True, exist_ok=True)
    sta.write_text(json.dumps({"files_found": 1, "has_wns_tns": True,
                               "verdict": "PASS"}, indent=2) + "\n")


def _healthy_upstream(project: Path, release: str = "zzdie") -> None:
    """Everything the producer's OWN predicates need to report NO blockage.

    BOTH halves, because the producer has two: the artefact audit (a sign-off GDS
    that carries geometry) AND the sign-off properties in
    `phase3/final/metrics.json`. An absent metrics file is itself a blocker in the
    producer's own words -- "no sign-off property was decided by any artefact of
    this run" -- so a fixture without one is not healthy, it is blocked for a second
    reason. That cost me a round of red arms.
    """
    gds = project / "phase3/stage4/gds"
    gds.mkdir(parents=True, exist_ok=True)
    (gds / f"{release}.gds").write_bytes(_GDS_WITH_GEOMETRY)
    metrics = project / CHK.PRODUCER_METRICS_REL
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text(json.dumps(_passing_metrics()) + "\n")
    _real_writer_formats(project)
    # and the upstream steps publish their OWN verdicts for this run
    for sid in ("23", "33", "37", "37.4"):
        _step_status(project, sid, "PASS")


def _blocked_upstream(project: Path, release: str = "zzdie") -> None:
    """A genuinely blocked upstream: the layout has no geometry AND the step that
    DECLARES it (37) says so in its own published verdict. Both halves, because the
    verdict is now what decides and the artefact only names which step to read."""
    gds = project / "phase3/stage4/gds"
    gds.mkdir(parents=True, exist_ok=True)
    (gds / f"{release}.gds").write_bytes(b"\x00" * 448)
    _real_writer_formats(project)
    metrics = project / CHK.PRODUCER_METRICS_REL
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text(json.dumps(_passing_metrics()) + "\n")
    for sid in ("23", "33", "37.4"):
        _step_status(project, sid, "PASS")
    _step_status(project, "37", "FAIL")
    # R-0915-165 — and the record that says WHAT step 37's failure is about. The layout is on
    # disk and carries no geometry, so step 37's own gate names it: measured,
    # `gds_substance_check` reports `phase3/stage4/gds/<release>.gds: [MALFORMED_RECORD] ...`.
    # Without this the blockage is real but unattributable, and R-0915-165 withholds the excuse
    # -- correctly, since "no record, no excuse" is the same direction as
    # `test_a_step_with_no_published_verdict_is_not_an_excuse`.
    _step_outputs(
        project, "37", "FAIL",
        required=["phase3/stage4/gds/*.gds"],
        reasons=[f"phase3/stage4/gds/{release}.gds: [MALFORMED_RECORD] Record at offset 0 "
                 f"declares length 0 (< 4); the stream is not a valid GDSII record chain"])


def test_the_blockage_is_observable_on_the_current_tree(tmp_path):
    """The predicates ARE callable from the gate — the review asked me to say so if
    they were not. Both directions of the re-derivation itself."""
    _blocked_upstream(tmp_path)
    blocked = CHK.upstream_blockage_now(tmp_path, "zzdie")
    assert blocked["blocked"] is True, blocked
    # the artefact NAMES the step; the step's own verdict DECIDES
    assert "37" in blocked["blocking_steps"], blocked
    assert blocked["blocking_steps"]["37"]["status"] == "FAIL"
    assert any("GDS_NO_GEOMETRY" in n or "geometry" in n.lower()
               for n in blocked["named"]), blocked


def test_a_named_artefact_whose_step_PASSES_is_not_an_excuse(tmp_path):
    """THE FINDING, DIRECTLY. The flow's own healthy records do not satisfy the
    producer's predicates — the runner's power.json carries no number by design, and
    step 23's summary carries the bool `has_wns_tns`, which the number scan skips. So
    the producer refuses and NAMES steps 23 and 33 while those steps' own verdicts
    say PASS. A reader that cannot digest a declared format is 37.5ic's OWN defect:
    FAIL, never upstream blockage."""
    _healthy_upstream(tmp_path)
    b = CHK.upstream_blockage_now(tmp_path, "zzdie")
    assert b["named"], "the predicates no longer complain at all; fixture drift"
    assert b["blocked"] is False, b
    assert b["upstream_steps_passing"], b
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_a_step_that_published_not_measured_is_a_blockage(tmp_path):
    """R-0915-140's other tier: a step that did not measure is blocking, exactly as
    a FAILing one is."""
    _blocked_upstream(tmp_path)
    _step_status(tmp_path, "37", "NOT_MEASURED")
    b = CHK.upstream_blockage_now(tmp_path, "zzdie")
    assert b["blocked"] is True and b["blocking_steps"]["37"]["status"] == "NOT_MEASURED"


def test_a_step_with_no_published_verdict_is_not_an_excuse(tmp_path):
    """DELIBERATE, and the conservative direction: a step that has published NOTHING
    in this run has no verdict, so there is no evidence of blockage and the documents
    are still owed. "No evidence" must never read as "blocked"."""
    _healthy_upstream(tmp_path)
    (tmp_path / "phase3/stage4/gds/zzdie.gds").write_bytes(b"\x00" * 448)
    for sid in ("23", "33", "37", "37.4"):
        _forget_step_status(tmp_path, sid)
    b = CHK.upstream_blockage_now(tmp_path, "zzdie")
    assert b["blocked"] is False, b
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_a_stale_record_over_a_fixed_upstream_is_a_fail(tmp_path):
    """THE DEFECT, DIRECTLY: the record still says REFUSED, and the upstream it
    blamed is healthy now. The step owes its documents, so this is FAIL — on
    7f0808acc it was NOT_MEASURED/BLOCKED."""
    _healthy_upstream(tmp_path)
    _refusal(tmp_path, reasons=(
        "STA_NO_SLACK [sta] `reports/phase3/sta/post_route_summary.json`: no slack",
        "POWER_NO_TOTAL [power] `reports/phase3/power.json`: no power number"))
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "FAIL", (result.verdict_tier, result.summary)
    assert [f.rule for f in result.findings] == ["RELEASE_DOCUMENTATION_ABSENT"]


def test_a_hand_written_record_over_a_healthy_upstream_is_a_fail(tmp_path):
    """A record is a file anyone can write. It is not evidence about the tree."""
    _healthy_upstream(tmp_path)
    _refusal(tmp_path, reasons=("I say it is blocked",))
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_a_genuine_blockage_needs_no_record_at_all(tmp_path):
    """The other direction: the upstream really is blocked NOW, and no record
    exists. That is still NOT_MEASURED/BLOCKED_BY_UPSTREAM — the record was never
    what made it true."""
    _blocked_upstream(tmp_path)
    assert not (tmp_path / CHK.PRODUCER_REFUSAL_REL).exists()
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "NOT_MEASURED", (result.verdict_tier,
                                                   result.summary)
    assert result.summary["reason_class"] == _T.BLOCKED_BY_UPSTREAM
    assert result.summary["reason"] == "release_docs_upstream_blocked"
    assert result.summary["producer_refusal_is_evidence"] is False
    assert result.summary["upstream_blockage"]["zzdie"]["blocked"] is True


def test_a_siblings_blockage_excuses_but_must_name_the_step(tmp_path):
    """RE-PINNED, and my previous arm here was wrong. It asserted FAIL for a healthy
    release beside a blocked one — but `ic_release_docs_gen` audits the WHOLE RUN and
    writes NOTHING when any class refuses, so `zzdie_v2`'s documents cannot be written
    while `zzdie` is hollow, and calling that a documentation defect blamed v2 for
    zzdie's breakage. The producer's scope is the run.

    What must NOT happen is an ANONYMOUS excuse, so every excused release names the
    step and the artefact that blocked it."""
    _healthy_upstream(tmp_path, "zzdie_v2")
    _blocked_upstream(tmp_path, "zzdie")          # sets step 37 FAIL
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "NOT_MEASURED", (result.verdict_tier,
                                                   result.summary)
    blocking = result.summary["blocking_steps"]
    assert "37" in blocking, blocking
    assert blocking["37"]["status"] == "FAIL"
    assert blocking["37"]["artefact"].endswith(".gds"), blocking
    # and each release carries the same named attribution, so none is excused silently
    for rel, b in result.summary["upstream_blockage"].items():
        assert b["blocked_by"], (rel, b)


def test_a_producer_crash_leaves_no_record_to_misread(tmp_path):
    """M2: the record is cleared BEFORE any predicate can raise, so a crash cannot
    leave the previous run's refusal behind to be read as upstream blockage."""
    import ast
    src = (PROGRAMS / "ic_release_docs_gen.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = [ast.unparse(st) for st in fn.body]
    clear_at = next(i for i, st in enumerate(body) if "_clear_refusal(project)" in st)
    audit_at = next(i for i, st in enumerate(body) if "_art.audit(project)" in st)
    assert clear_at < audit_at, (
        "the stale record is cleared AFTER the first predicate can raise; a crash "
        "there leaves the previous run's refusal on disk")


def test_the_record_is_named_a_pointer_in_the_report(tmp_path):
    """A reader must be able to see that the record did not decide anything."""
    _blocked_upstream(tmp_path)
    _refusal(tmp_path, reasons=("STA_NO_SLACK [sta] ...",))
    summary = CHK.run_audit(tmp_path, "ic").summary
    assert summary["producer_refusal_is_evidence"] is False
    assert summary["producer_refusal"] == CHK.PRODUCER_REFUSAL_REL
    # and the reasons published are the ones OBSERVED, not the record's text
    assert summary["producer_refusals"], summary
    assert not any("I say it is blocked" in r for r in summary["producer_refusals"])
