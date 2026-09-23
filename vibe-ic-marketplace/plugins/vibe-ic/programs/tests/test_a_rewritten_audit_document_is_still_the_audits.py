"""A document the audit wrote stays the audit's after the audit rewrites it.

EVERY ARM HERE DRIVES THE REAL `check_step` / `_check_program_exit_zero` ON A
STAGED TREE. A previous cut of this file re-implemented the audit's own
computation in the test (`_audit_pass`, the gate faked as a boolean), so its arms
passed on the base and on the fix alike and a reviewer had to find the regression
by reading. A rule a test can only paraphrase is a rule nothing guards.

THE REGRESSION, reproduced through the real code path. The first cut of R-0915-141's
re-stamp fix stamped the authorship note for `_absent_before_gate` ONLY. MEASURED
on a staged tree whose step declares two outputs -- a sibling the run produced and
one its own gate writes -- with the gate being a real nested
`flow_compliance_check` invocation, i.e. #2518's republish path, which by design
does NOT receipt-redirect and therefore rewrites its target on every pass:

    narrow rule (`absent` only)          the fix (`absent` UNION `prior`)
    pass 1  REFUSED, note match=True     pass 1  REFUSED, note match=True
    pass 2  REFUSED, note match=FALSE    pass 2  REFUSED, note match=True
            (the republish moved the             (the note followed the bytes)
             bytes; the note did not)
    pass 3  CREDITED  <-- the audit's    pass 3  REFUSED, note match=True
            own document accepted as
            run evidence
    pass 4  CREDITED                     pass 4  REFUSED, note match=True

"MISSING twice, PASS forever." The union is what closes it: when the bytes in
front of this pass's gate were already the audit's own, whatever this pass writes
over them is also the audit's, and the note must follow those bytes.

WHERE THE SEQUENCE CAN START, measured rather than assumed. A step ALL of whose
declared outputs are missing never reaches this code: the early return refuses to
run a gate that would manufacture its own completion ("PRODUCER GAP: ... The
auditor will not run that gate to manufacture completion evidence"). So the
reachable entry is the PARTIAL case -- one declared output already present -- which
is the shape every arm below stages, and which the module's own comment names.

AND THE HALF THAT MUST NOT COME BACK: the CONTENT-ONLY branch is still excluded
from the re-stamp. A path that is in `_audit_produced` only because the document
self-identifies as a gate verdict document is refused on this pass but earns no
note, so a producer rewrite can still lift it. That is the run22 defect, where the
note carried `tapeout_checklist_gen`'s own bytes (size=16738,
mtime_ns=1790124683557869658 -- byte-identical to the live file) and the refusal
therefore stood for ever.

WHAT THE RE-STAMP FIX DOES NOT DO, stated plainly because a previous version of
this docstring implied otherwise: it does NOT by itself lift run22's steps 36 and
38. MEASURED through the real `check_step`, with the real producer's document on
disk and run22's real step shape (the step declaring the CHECKER, not the writer):
the producer's own document is NOT credited, because
`_is_gate_verdict_document` falls through to its final `return True` when the
document's stamp names a program the step does not declare. The refusal there is
carried by the CONTENT branch and is independent of the note. The union stops it
being permanent; only naming the producer -- R-0915-141's path split, in the commit
after this one -- lifts it. Both directions are arms below.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

CHECKLIST_REL = "reports/audit/tapeout_checklist.json"
SIBLING_REL = "reports/zzsibling.json"

#: The shipped step 2 clause that names TWO receipt flags: `--report <input the
#: gate reads>` BEFORE `--json <where it writes its verdict>`.
CROSSLAYER_CMD = (
    "crosslayer_rewrite_equivalence_check . "
    "--report reports/crosslayer/rewrite_equivalence.json "
    "--baseline-marker reports/crosslayer/baseline_rtl "
    "--search-space reports/crosslayer/search_space.json "
    "--json reports/crosslayer/rewrite_equivalence_check.json")
CROSSLAYER_INPUT = "reports/crosslayer/rewrite_equivalence.json"
CROSSLAYER_RECEIPT = "reports/crosslayer/rewrite_equivalence_check.json"


@pytest.fixture()
def project(tmp_path_factory):
    return Path(tempfile.mkdtemp(prefix="restamp_",
                                 dir=str(tmp_path_factory.mktemp("r"))))


def _sibling(project: Path) -> None:
    """One declared output the RUN produced, so the early return does not fire."""
    p = project / SIBLING_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"program": "zz_producer", "ok": true}\n')


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _note(project: Path):
    d = project / FCC._AUDIT_AUTHORSHIP_DIR
    notes = list(d.glob("*.json")) if d.is_dir() else []
    return json.loads(notes[0].read_text()) if len(notes) == 1 else None


def _refused(result) -> bool:
    return any("SELF-CERTIFIED EVIDENCE EXCLUDED" in str(r)
               for r in (result.reasons or []))


# ── the three-pass sequence, through the real audit and a real rewriter ─────

def test_a_republished_report_is_refused_on_every_pass(project):
    """THE #2518 REPUBLISH SHAPE, with a real nested `flow_compliance_check` as the
    gate: it rewrites its target every pass, which is what made the pass-1 note
    stale under the narrow rule.

    Four real passes, each a full `check_step`. The note must match the live file
    after every one of them, and the document must be refused every time.
    """
    _sibling(project)
    tiny = project / "zzflow.yaml"
    tiny.write_text("flow: zzflow\nsteps:\n"
                    "  - id: '1'\n    name: trivial\n    stage: stage1\n"
                    f"    required_outputs: ['{SIBLING_REL}']\n"
                    "    blocks_on: []\n")
    rel = "reports/zzstage_compliance.json"
    step = {"id": "zzrep", "name": "republish", "stage": "stage4",
            "programs": ["flow_compliance_check"],
            "required_outputs": [SIBLING_REL, rel],
            "gate": {"all_of": [{"program_exit_zero":
                                 f"flow_compliance_check . --flow-def {tiny} "
                                 f"--json {rel}"}]}}
    seen = []
    for n in (1, 2, 3, 4):
        result = FCC.check_step(project, dict(step), {})
        live = project / rel
        assert live.is_file(), f"pass {n}: the gate wrote no report"
        rec = _note(project)
        seen.append(os.stat(live).st_mtime_ns)
        assert _refused(result), (
            f"pass {n} did NOT refuse the audit's own document. Under the narrow "
            f"re-stamp rule this flips at pass 3: the pass-2 republish moves the "
            f"bytes, the pass-1 note goes stale, and the auditor's own report is "
            f"credited as run evidence — MISSING twice, PASS forever")
        assert rel not in (result.evidence or []), f"pass {n} credited it"
        assert rec is not None, f"pass {n}: the note is gone"
        assert rec["mtime_ns"] == os.stat(live).st_mtime_ns, (
            f"pass {n}: the note describes bytes that are no longer there, so the "
            f"NEXT pass reads it as stale and credits the auditor's own document")
    assert len(set(seen)) == 4, (
        f"fixture defect: the gate did not rewrite its target on every pass "
        f"({seen}) — without a real rewriter this arm cannot see the regression")


def test_a_step_with_no_evidence_at_all_never_runs_its_own_gate(project):
    """THE BOUNDARY, measured: where the sequence above can NOT start. A step all
    of whose declared outputs are missing takes the early return and the auditor
    refuses to run the gate at all, so no note is ever created that way."""
    step = {"id": "zzgap", "name": "gap", "stage": "stage4",
            "programs": ["tapeout_checklist_gen"],
            "required_outputs": [CHECKLIST_REL],
            "gate": {"all_of": [{"program_exit_zero":
                                 f"tapeout_checklist_gen . --json {CHECKLIST_REL}"}]}}
    result = FCC.check_step(project, dict(step), {})
    assert result.status == "FAIL"
    assert not (project / CHECKLIST_REL).exists(), (
        "the auditor ran a gate whose only purpose here would be to manufacture "
        "this step's completion evidence")
    assert any("PRODUCER GAP" in str(r) for r in (result.reasons or []))


# ── run22's real shape, and what the re-stamp fix does NOT do ──────────────

def _live_step(sid: str) -> dict:
    doc = yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())
    step = dict(next(s for s in doc["steps"]
                     if isinstance(s, dict) and str(s.get("id")) == sid))
    for k in ("blocks_on", "closed_loop", "condition"):
        step.pop(k, None)
    return step


def _producer_wrote_the_checklist(project: Path) -> None:
    """The RUN's producer writes its document, exactly as run22's mtimes show."""
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "tapeout_checklist_gen.py"), str(project),
         "--json", str(project / CHECKLIST_REL)],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0 and (project / CHECKLIST_REL).is_file(), r.stderr[-300:]
    assert json.loads((project / CHECKLIST_REL).read_text())["program"] == \
        "tapeout_checklist_gen"


def test_run22s_real_shape_does_not_credit_the_producers_document(project):
    """RUN22's ACTUAL SHAPE: the step declares the CHECKER under `programs:` while
    the document is written by the PRODUCER. The stamp then matches neither the
    gate set nor the producer set, `_is_gate_verdict_document` falls through to its
    final `return True`, and the run's own document is excluded.

    This is why the re-stamp fix alone cannot lift run22: the refusal here is on
    the CONTENT branch and does not involve the note at all.
    """
    _producer_wrote_the_checklist(project)
    step = _live_step("36")
    step["programs"] = ["tapeout_signoff_check"]
    step["required_outputs"] = [CHECKLIST_REL]
    step["gate"] = {"all_of": [{"program_exit_zero":
                                f"tapeout_signoff_check . --mode tapeout "
                                f"--json {CHECKLIST_REL}"}]}
    result = FCC.check_step(project, step, {})
    assert CHECKLIST_REL not in (result.evidence or []), (
        "in run22's own shape the producer's document must NOT be credited — if it "
        "is, this arm is no longer measuring the state run22 was in")
    assert _refused(result), [str(r)[:160] for r in (result.reasons or [])]


def test_the_shipped_step_36_credits_the_producers_document(project):
    """THE SPLIT, on the SHIPPED step: the same producer's document, the same tree,
    and now it IS run evidence — because step 36 declares the program that writes
    it. Reddens on any tree where that declaration is reverted."""
    _producer_wrote_the_checklist(project)
    result = FCC.check_step(project, _live_step("36"), {})
    assert CHECKLIST_REL in (result.evidence or []), (
        f"step 36 does not credit its producer's document; reasons: "
        f"{[str(r)[:160] for r in (result.reasons or [])]}")
    assert not _refused(result), [str(r)[:160] for r in (result.reasons or [])]


# ── every receipt flag, through the real gate ──────────────────────────────

def test_no_run_document_is_overwritten_by_a_two_receipt_clause(project):
    """THE SHIPPED step-2 crosslayer clause, run for real.

    It names `--report <input>` BEFORE `--json <receipt>`. The scanner used to
    RETURN at the first flag, so the gate's `--json` still pointed at the run's own
    document and overwrote it — while the disclosure said the opposite.

    MEASURED at origin/main 1622ff87d with this same fixture:
        reports/crosslayer/rewrite_equivalence.json        UNCHANGED
        reports/crosslayer/rewrite_equivalence_check.json  OVERWRITTEN BY THE AUDIT
    and on this tip both are UNCHANGED.
    """
    for rel, body in ((CROSSLAYER_INPUT,
                       {"program": "crosslayer_rewrite_fidelity", "rewrites": []}),
                      (CROSSLAYER_RECEIPT,
                       {"program": "crosslayer_rewrite_fidelity",
                        "verdict": "PASS"})):
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body) + "\n")
    before = {r: _digest(project / r)
              for r in (CROSSLAYER_INPUT, CROSSLAYER_RECEIPT)}

    outcome = FCC._check_program_exit_zero(project, CROSSLAYER_CMD)

    for rel, was in before.items():
        assert _digest(project / rel) == was, (
            f"the audit overwrote the run's own document at {rel}; R-0915-126 is "
            f"'producer writes, gate reads' and this clause names two receipt "
            f"flags, so BOTH have to be redirected")
    # `_check_program_exit_zero` answers a tuple whose second element is the
    # snippet the audit publishes; read it that way rather than by attribute.
    note = str(outcome[1] if isinstance(outcome, tuple)
               else getattr(outcome, "output", "") or "")
    assert "RECEIPT REDIRECTED" in note
    assert "2 path(s)" in note, note[:300]
    assert "--report" in note and "--json" in note, note[:300]


def test_the_receipt_is_the_flag_the_gate_wrote_not_the_first_one(project):
    """WHICH of the two is the gate's receipt is decided by what it WROTE.

    The reader takes the gate's structured verdict out of the receipt. Taking the
    first flag meant reading a document the gate had only read.
    """
    for rel in (CROSSLAYER_INPUT, CROSSLAYER_RECEIPT):
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"program": "crosslayer_rewrite_fidelity"}\n')
    argv = CROSSLAYER_CMD.split()
    moved, disclosure, keep = FCC._receipt_off_a_produced_document(argv, project)
    assert len(keep.records) == 2, keep.records
    assert [r["flag"] for r in keep.records] == ["--report", "--json"]
    # the gate writes only its receipt; simulate exactly that and ask the module
    rec_json = next(r for r in keep.records if r["flag"] == "--json")
    Path(rec_json["scratch"]).write_text('{"program": "x", "verdict": "FAIL"}\n')
    written = FCC._written_receipts(keep)
    assert [r["flag"] for r in written] == ["--json"], written
    # and the one it only read is no longer offered to a reader as a receipt
    rec_report = next(r for r in keep.records if r["flag"] == "--report")
    assert rec_report["key"] not in FCC._RECEIPT_REDIRECTS
    assert rec_json["key"] in FCC._RECEIPT_REDIRECTS


# ── one source pin, on the rule a revert would inline again ────────────────

def test_the_shipped_restamp_set_is_the_union():
    """AST, not a text slice: `check_step` must route its re-stamp through
    `restamp_set`, and that rule must use BOTH sets and NOT the content branch."""
    tree = ast.parse((PROGRAMS / "flow_compliance_check.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_record_audit_created"]
    routed = [ast.unparse(c.args[2]) for c in calls
              if len(c.args) >= 3 and "restamp_set(" in ast.unparse(c.args[2])]
    assert routed, ("check_step no longer routes its re-stamp through "
                    "`restamp_set`; the rule is back to an inline comprehension "
                    "no test can drive")
    for src in routed:
        assert "_absent_before_gate" in src and "_prior_created" in src, src
        assert "_is_gate_verdict_document" not in src, src
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "restamp_set")
    body = ast.unparse(fn)
    assert "absent" in body and "prior" in body, body
