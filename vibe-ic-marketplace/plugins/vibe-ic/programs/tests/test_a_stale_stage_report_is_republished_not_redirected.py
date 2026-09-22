"""R-0915-138 — the later, correct compliance pass publishes over the stale one.

THE DEFECT, measured read-only on spm run21 and reproduced here. Step 31's ninth
declared output was judged MISSING five seconds before the declared producer wrote
it:

    17:59:13.831  reports/phase3/gates/stage3_compliance.json  step 31 FAIL
                     "required_outputs missing: ['reports/phase3/perc_sweep.json']
                      (satisfied: 8/9)", step 32 NOT_MEASURED/upstream_failed
    17:59:19.052  reports/phase3/perc_sweep.json               produced
    17:59:19.072  reports/audit/flow_declared_producer_run.json
                     step 31 perc_corpus_sweep: why "target absent", rc 0, 3.55 s,
                     target_exists_after TRUE
    18:07:33.338  reports/audit/phase23_completion_audit.json  step 31 has NO
                     missing-output reason, sweep_reach_check rc=0 PASS, step 32
                     PASS

There is no producer gap: `perc_corpus_sweep` ran and succeeded. The FINAL pass
already computed the right answer -- and it was REDIRECTED to a scratch path by
R-0915-126's receipt redirect, so the run's published stage-3 evidence stayed the
17:59:13 document. Its mtime never moved, and it has a reader: step 37's gate
passes `--compliance reports/phase3/gates/stage3_compliance.json` to
`stage_on_pass_review`.

R-0915-126 PROTECTS PRODUCER DOCUMENTS AND IS UNTOUCHED. A compliance report is
the auditor's own; the negative arm below is what that claim is worth.

THREE ARMS, MEASURED ON A COPY OF run21 THROUGH THE REAL CODE PATH
(`__check_program_exit_zero`, which is where the redirect lives):

    PASS 1, producer has not run
        31 = FAIL / missing_artefact  "required_outputs missing:
             ['reports/phase3/perc_sweep.json'] (satisfied: 8/9 ...)"
        32 = NOT_MEASURED
        published_by = None                     (a first publication)
    PASS 2, after the declared producer wrote perc_sweep.json
        31 = NOT_MEASURED / partial_population  (the provenance_check reader item
             only -- the missing-output reason is GONE)
        32 = PASS
        canonical mtime MOVED; stage3_compliance.superseded-1.json kept beside it
        published_by = {pass 2, at ..., supersedes {pass 1, verdict FAIL, mtime
             2026-09-23T07:23:29.944, kept_at .../stage3_compliance.superseded-1.json}}
    NEGATIVE, a PRODUCER's document
        reports/phase3/die_finishing.json (written by die_finishing_gen):
        sha256 UNCHANGED, no published_by stamp, no superseded copy

AND THE CLASS TOOK THREE CUTS TO GET RIGHT, which is why the predicate is pinned
here and not only in a comment:
  * cut 1 asked "is this a gate verdict document?" with an empty producer set, and
    published over `die_finishing.json`;
  * cut 2 passed the step's declared `programs:` as producers, which protects that
    file -- and declines the compliance report too, because step 37 declares
    `stage3_compliance` under `programs:`, so the gate IS the declared producer and
    content alone cannot separate them;
  * cut 3, this one, identifies the class by its EMITTER: a compliance report
    self-identifies as `program: flow_compliance_check` and carries per-step rows.
    No producer document and no other gate's verdict document does, so the negative
    arm holds by construction instead of by a set somebody has to keep right.

THE EMITTER STAMP IS NEW, and the file it was missing from said so itself. This
module's own comment on `_AUDIT_AUTHORSHIP_DIR` records:
"`stage_phase1_compliance.json` is written by `flow_compliance_check --json`, whose
document carries NONE of `_GATE_DOCUMENT_IDENTITY_KEYS`, so pass 2 read it as the
run's and CREDITED what pass 1 refused". The compliance report was the one gate
document in the flow with no provenance stamp; it has one now.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402
import stage_on_pass_review as SOPR                          # noqa: E402


def _report(tmp_path: Path, rel: str, **over) -> Path:
    """A compliance report as `flow_compliance_check --json` writes one."""
    doc = {"program": "flow_compliance_check", "flow": "f.yaml",
           "project": str(tmp_path), "overall": "FAIL",
           "steps": [{"id": "31", "stage": "stage3", "status": "FAIL"}]}
    doc.update(over)
    out = tmp_path / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return out


# ── the class: decided by the emitter, on content ──────────────────────────

def test_a_compliance_report_is_recognised_as_the_audits_own():
    rel = "reports/phase3/gates/stage3_compliance.json"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _report(Path(td), rel)
        assert FCC._is_the_audits_own_compliance_report(p) is True


def test_a_producers_document_at_a_gate_path_is_not(tmp_path):
    """THE NEGATIVE ARM, as a predicate. `die_finishing.json` is written by
    `die_finishing_gen` and stamped by its checker as `check`; publishing over it
    is what cut 1 of this change did, and it must never happen."""
    p = tmp_path / "reports/phase3/die_finishing.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"check": "die_finishing_check",
                             "producer": "die_finishing_gen",
                             "seal_ring": {"state": "PASS"}}) + "\n")
    assert FCC._is_the_audits_own_compliance_report(p) is False


def test_a_stamp_without_step_rows_is_not_a_stage_report(tmp_path):
    """A stamp alone would also match a future `flow_compliance_check` document of
    some other shape. The per-step rows are what a later pass can have a better
    answer ABOUT."""
    p = _report(tmp_path, "reports/x.json")
    doc = json.loads(p.read_text()); doc.pop("steps")
    p.write_text(json.dumps(doc) + "\n")
    assert FCC._is_the_audits_own_compliance_report(p) is False


def test_the_name_alone_decides_nothing(tmp_path):
    """Content, never the name — this repo's own self-document guard is
    content-based "not name-based", and a name-based reading here would publish
    over any file a lane happened to call `*_compliance.json`."""
    p = tmp_path / "reports/phase3/gates/stage3_compliance.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"produced": "by the run", "steps": []}) + "\n")
    assert FCC._is_the_audits_own_compliance_report(p) is False


# ── the publication: preserved, ordinal, and named ─────────────────────────

def test_the_stale_publication_is_kept_and_its_verdict_named(tmp_path):
    rel = "reports/phase3/gates/stage3_compliance.json"
    _report(tmp_path, rel, overall="FAIL")
    argv = ["python3", "stage3_compliance.py", ".", "--json", rel]
    prov = FCC._publish_over_the_audits_own_document(argv, tmp_path)
    assert prov is not None, "the audit's own compliance report must be publishable"
    assert prov["pass"] == 2
    assert prov["supersedes"]["pass"] == 1
    assert prov["supersedes"]["verdict"] == "FAIL", prov
    kept = tmp_path / prov["supersedes"]["kept_at"]
    assert kept.is_file(), "the superseded document must be kept, never lost"
    assert json.loads(kept.read_text())["overall"] == "FAIL"


def test_the_verdict_is_read_under_either_of_its_two_names(tmp_path):
    """A compliance report spells it `overall`; other gate documents spell it
    `verdict`. Reading only one recorded `null` for exactly the report this ruling
    is about — measured on the first cut."""
    assert FCC._stale_verdict({"overall": "FAIL"}) == "FAIL"
    assert FCC._stale_verdict({"verdict": "PASS"}) == "PASS"
    assert FCC._stale_verdict({"counts": {}}) is None


def test_the_pass_is_an_ordinal_and_climbs(tmp_path):
    """The ruling asks which pass wrote it, "mid-run vs final". The audit cannot
    know it is the final one — nothing tells a compliance invocation that no
    further invocation follows — so recording "final" would be a claim this
    program cannot support. The ordinal is derived from the kept copies."""
    rel = "reports/phase3/gates/stage3_compliance.json"
    _report(tmp_path, rel)
    argv = ["python3", "stage3_compliance.py", ".", "--json", rel]
    first = FCC._publish_over_the_audits_own_document(argv, tmp_path)
    second = FCC._publish_over_the_audits_own_document(argv, tmp_path)
    assert (first["pass"], second["pass"]) == (2, 3), (first, second)
    assert second["supersedes"]["pass"] == 2
    kept = sorted(p.name for p in (tmp_path / "reports/phase3/gates").glob(
        "stage3_compliance.superseded-*.json"))
    assert kept == ["stage3_compliance.superseded-1.json",
                    "stage3_compliance.superseded-2.json"], kept


def test_the_stamp_is_written_into_the_republished_document(tmp_path):
    rel = "reports/phase3/gates/stage3_compliance.json"
    out = _report(tmp_path, rel)
    argv = ["python3", "stage3_compliance.py", ".", "--json", rel]
    prov = FCC._publish_over_the_audits_own_document(argv, tmp_path)
    # the gate re-writes the document with its later answer
    _report(tmp_path, rel, overall="PASS",
            steps=[{"id": "31", "stage": "stage3", "status": "NOT_MEASURED"}])
    FCC._stamp_publication(prov)
    doc = json.loads(out.read_text())
    assert doc["overall"] == "PASS", "the later verdict must survive the stamp"
    pub = doc["published_by"]
    assert pub["pass"] == 2 and pub["supersedes"]["verdict"] == "FAIL"
    assert pub["supersedes"]["kept_at"].endswith("superseded-1.json")


def test_stamping_never_raises_on_an_unwritable_document(tmp_path):
    """Provenance must not be able to kill a run: a document the gate did not
    rewrite, or one that is not JSON, keeps whatever stamp it had."""
    FCC._stamp_publication(None)
    FCC._stamp_publication({"path": str(tmp_path / "gone.json"), "pass": 2,
                            "supersedes": {}})
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    FCC._stamp_publication({"path": str(bad), "pass": 2, "supersedes": {}})
    assert bad.read_text() == "not json"


# ── the reader names the publication it read ───────────────────────────────

def test_the_reader_names_the_pass_it_read(tmp_path):
    rel = "reports/phase3/gates/stage3_compliance.json"
    p = _report(tmp_path, rel, overall="PASS",
                steps=[{"id": "31", "stage": "stage3", "status": "PASS"}],
                published_by={"pass": 2, "at": "2026-09-23T07:24:20.968",
                              "supersedes": {"pass": 1, "verdict": "FAIL",
                                             "mtime": "2026-09-23T07:23:29.944",
                                             "kept_at": "kept.json"}})
    v = SOPR.stage_passed(p, "stage3", None)
    assert v["passed"] is True, v
    assert "read from publication pass 2" in v["why"], v["why"]
    assert "supersedes pass 1 verdict FAIL" in v["why"], v["why"]
    assert v["publication"]["pass"] == 2


def test_the_reader_says_first_publication_when_there_is_no_stamp(tmp_path):
    """The overwhelmingly common case, stated rather than left blank — a reader
    that says nothing about which publication it read cannot be checked."""
    p = _report(tmp_path, "reports/phase3/gates/stage3_compliance.json",
                overall="PASS",
                steps=[{"id": "31", "stage": "stage3", "status": "PASS"}])
    v = SOPR.stage_passed(p, "stage3", None)
    assert "read from the first publication" in v["why"], v["why"]
    assert v["publication"] == {"pass": 1, "supersedes": None}


def test_the_emitter_stamps_its_own_compliance_report():
    """The stamp the whole class depends on, asserted at the source: without it
    `_is_the_audits_own_compliance_report` cannot recognise the document and the
    stale report stays published. Measured on the first cut of this change."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i = src.index('out = {')
    assert '"program": "flow_compliance_check",' in src[i:i + 2000], (
        "the compliance report must carry its emitter's identity")
