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

def test_absent_documents_with_no_refusal_record_is_still_a_fail(tmp_path):
    """THE ARM THAT MUST NOT MOVE. This gate exists for a die signed off with no
    document set and no explanation; that is still a FAIL on content."""
    _signed_off_die(tmp_path)
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "FAIL", result.summary
    assert result.passed is False
    assert [f.rule for f in result.findings] == ["RELEASE_DOCUMENTATION_ABSENT"]
    assert result.summary.get("reason_class") in (None, ""), result.summary


def test_absent_documents_with_a_stated_refusal_is_not_measured(tmp_path):
    _signed_off_die(tmp_path)
    _refusal(tmp_path, reasons=(
        "STA_NO_SLACK [sta] `reports/phase3/sta/post_route_summary.json`: no slack",
        "POWER_NO_TOTAL [power] `reports/phase3/power.json`: no power number"))
    result = CHK.run_audit(tmp_path, "ic")
    assert result.verdict_tier == "NOT_MEASURED", result.summary
    assert result.passed is False, "a blocked step is not a pass"
    assert result.summary["reason_class"] == _T.BLOCKED_BY_UPSTREAM
    assert result.summary["reason"] == "release_docs_producer_refused"
    assert len(result.summary["producer_refusals"]) == 2, result.summary
    assert any("post_route_summary" in r
               for r in result.summary["producer_refusals"])


def test_the_class_keeps_the_step_owed(tmp_path):
    """The load-bearing property of the class chosen: BLOCKED_BY_UPSTREAM is in
    INCOMPLETE and NOT in SKIP_ELIGIBLE, so nothing reads the step as satisfied.
    If this ever flips, a blocked release-docs step becomes a silent skip."""
    assert _T.BLOCKED_BY_UPSTREAM in _T.INCOMPLETE
    assert _T.BLOCKED_BY_UPSTREAM not in _T.SKIP_ELIGIBLE


def test_the_exit_is_the_non_verdict_code_and_not_a_vacuous_pass(tmp_path):
    """rc 2 carries "this is not a verdict"; the VACUOUS_PASS sentinel would be
    promoted to a pass tier by the audit, so it must NOT be printed here."""
    _signed_off_die(tmp_path)
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
    # and the absent-documents case still exits 1
    (tmp_path / CHK.PRODUCER_REFUSAL_REL).unlink()
    r2 = subprocess.run(
        [sys.executable, str(PROGRAMS / "release_docs_check.py"), str(tmp_path),
         "--arm", "ic"], capture_output=True, text=True, timeout=600)
    assert r2.returncode == 1, (r2.returncode, r2.stdout[-300:])


# ── the record is read strictly ────────────────────────────────────────────

def test_a_refusal_naming_no_reason_is_not_evidence(tmp_path):
    _signed_off_die(tmp_path)
    _refusal(tmp_path, reasons=(), blockers=())
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_another_programs_document_is_not_this_producers_refusal(tmp_path):
    _signed_off_die(tmp_path)
    _refusal(tmp_path, program="somebody_else_gen")
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_a_record_that_does_not_say_refused_is_not_a_refusal(tmp_path):
    _signed_off_die(tmp_path)
    _refusal(tmp_path, verdict="PASS")
    assert CHK.producer_refusal(tmp_path) is None
    assert CHK.run_audit(tmp_path, "ic").verdict_tier == "FAIL"


def test_an_unreadable_record_is_not_a_refusal(tmp_path):
    _signed_off_die(tmp_path)
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
        # and the gate reads exactly this document
        assert CHK.producer_refusal(tmp_path) is not None
        assert CHK.run_audit(tmp_path, "ic").verdict_tier == "NOT_MEASURED"
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
