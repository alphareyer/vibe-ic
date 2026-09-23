"""A document the audit wrote stays the audit's after the audit rewrites it.

THE REGRESSION THIS REFUSES, confirmed by a pre-landing adversarial review of the
first cut of R-0915-141's re-stamp fix (2 reviewers + skeptics, read-only). That
cut stamped the authorship note for `_absent_before_gate` ONLY, which left this
sequence green -- and it is reachable inside ONE run:

    pass 1  the declared path is absent; this step's own gate clause writes it;
            the note is stamped with the gate's bytes.        REFUSED, correctly.
    pass 2  the path is present and the note still matches, so it is refused --
            but the gate REWRITES its own target during this pass. The bytes move;
            the note keeps pass 1's (size, mtime_ns).
    pass 3  `_prior_audit_created` finds the note STALE and drops the path. It is
            not absent either. Only the content branch is left, and for the 22
            steps whose gate program is ALSO listed under `programs:` that branch
            answers "the run's" by design -- so the auditor's own document is
            CREDITED. "MISSING twice, PASS forever."

TWO LANDED REWRITERS REACH IT, which is why this is not theoretical:
  * #2518's republish path — the `stage_*_compliance` reports of steps 2, 14, 15
    and 37 carry the `flow_compliance_check` program stamp, so they are
    REPUBLISHED (not receipt-redirected) and re-stamped on every pass;
  * the receipt redirect returning at the FIRST `--json`/`--report` flag, so a
    clause naming two receipts was decided entirely by the first one and step 2's
    crosslayer clause kept rewriting its target. Fixed in the same change, and
    pinned in the last two arms of this file.
A pass 2 can be the nested stage1_compliance inside step 7's own gate, so a
single run reaches pass 3.

THE FIX: the re-stamp set is `_absent_before_gate UNION _prior_created`. When the
bytes in front of this gate were already the audit's own, whatever this pass
writes over them is also the audit's, and the note follows those bytes.

AND THE HALF THAT MUST NOT COME BACK: the CONTENT-ONLY branch is still excluded.
A path that is neither absent-before-gate nor carried by a prior note, and is in
`_audit_produced` only because the document self-identifies as a gate verdict
document, is refused on this pass but gets NO note — so when a producer rewrites
it the refusal lifts. That is the run22 defect (step 36's note carried
size=16738 mtime=08:51:23.557, exactly `tapeout_checklist_gen`'s own write) and
the last arms here hold it in place from the other side.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

GATE = "zz_stage_check"
PRODUCER = "zz_gen"


def _write(project: Path, rel: str, stamp: str, payload: str) -> None:
    """Write the document and give it a stat STRICTLY LATER than its last one.

    THE BUMP IS MONOTONIC AGAINST THE FILE'S OWN PREVIOUS MTIME, not against
    "now", and that is not fussiness. MEASURED while proving these arms bite: this
    filesystem reports mtime in ~64 ms granules, so two writes inside one granule
    produced BYTE-IDENTICAL (size, mtime_ns) pairs -- pass 1 and pass 2 got the
    same stat, the pass-1 note still matched the pass-2 document by accident, and
    `test_the_note_follows_the_bytes_it_describes` passed under the very mutation
    it exists to catch. An arm that green-lights the defect on a fast machine is
    worse than no arm.
    """
    f = project / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    prev = os.stat(f).st_mtime_ns if f.exists() else 0
    f.write_text(json.dumps({"program": stamp, "verdict": "PASS",
                             "payload": payload}) + "\n")
    nxt = max(os.stat(f).st_mtime_ns, prev) + 10 ** 9
    os.utime(f, ns=(nxt, nxt))


def _audit_pass(project: Path, sid: str, rel: str, *,
                gate_rewrites: bool,
                declared_programs: frozenset,
                stamp: str = GATE,
                payload: str = "") -> bool:
    """One audit pass over one declared self-written path. Returns: refused?

    Mirrors `check_step`'s audit-created block by calling that block's OWN
    helpers, so the arms below measure the shipped rule rather than a paraphrase
    of it. The expression the block actually passes to `_record_audit_created` is
    pinned separately, by AST, in `test_the_shipped_restamp_set_is_the_union`.
    """
    absent_before = [] if (project / rel).exists() else [rel]
    prior = FCC._prior_audit_created(project, sid, [rel])
    if gate_rewrites:
        _write(project, rel, stamp, payload or f"pass-{len(prior)}")
    produced = []
    if (project / rel).exists() and (
            rel in absent_before or rel in prior
            or FCC._is_gate_verdict_document(
                project / rel, frozenset({GATE}), declared_programs)):
        produced = [rel]
    # THE SHIPPED RULE, CALLED — not restated. An earlier cut of these arms
    # re-implemented the comprehension here and therefore passed on the base and
    # on the fix alike; `restamp_set` exists so a test can drive the real decision.
    FCC._record_audit_created(
        project, sid, FCC.restamp_set(produced, absent_before, prior))
    FCC._drop_audit_created_note(
        project, sid, [r for r in [rel] if r not in produced])
    return bool(produced)


# ── the three-pass sequence, both rewriter shapes ──────────────────────────

def test_a_republished_compliance_report_is_refused_on_every_pass(tmp_path):
    """THE #2518 REPUBLISH SHAPE. The step's gate program is also its declared
    producer (steps 2, 14, 15, 37 all are), so the content branch cannot help and
    the note is the only thing standing between the auditor and its own document.
    """
    rel = "reports/phase1/gates/stage_phase1_compliance.json"
    declared = frozenset({GATE})          # gate program IS a declared producer
    assert _audit_pass(tmp_path, "2", rel, gate_rewrites=True,
                       declared_programs=declared) is True, "pass 1"
    assert _audit_pass(tmp_path, "2", rel, gate_rewrites=True,
                       declared_programs=declared) is True, "pass 2"
    assert _audit_pass(tmp_path, "2", rel, gate_rewrites=True,
                       declared_programs=declared) is True, (
        "pass 3 CREDITED the auditor's own document: the pass-2 rewrite made the "
        "pass-1 note stale and nothing re-stamped it — MISSING twice, PASS "
        "forever")
    # and it does not stop at three
    for n in range(4, 8):
        assert _audit_pass(tmp_path, "2", rel, gate_rewrites=True,
                           declared_programs=declared) is True, f"pass {n}"


def test_the_crosslayer_clause_shape_is_refused_on_every_pass(tmp_path):
    """THE SECOND REWRITER: a clause whose target is rewritten on every pass
    because the receipt redirect never looked at its flag. Same three passes,
    different document."""
    rel = "reports/crosslayer/rewrite_equivalence_check.json"
    declared = frozenset({GATE})
    for n in (1, 2, 3, 4):
        assert _audit_pass(tmp_path, "2", rel, gate_rewrites=True,
                           declared_programs=declared) is True, f"pass {n}"


def test_the_note_follows_the_bytes_it_describes(tmp_path):
    """WHY the union works: after a pass that rewrote the document, the note must
    carry THAT pass's stat — not pass 1's, and not a stat of nothing."""
    rel = "reports/phase1/gates/stage_phase1_compliance.json"
    declared = frozenset({GATE})
    _audit_pass(tmp_path, "2", rel, gate_rewrites=True, declared_programs=declared)
    _audit_pass(tmp_path, "2", rel, gate_rewrites=True, declared_programs=declared)
    live = os.stat(tmp_path / rel)
    notes = list((tmp_path / FCC._AUDIT_AUTHORSHIP_DIR).glob("*.json"))
    assert len(notes) == 1, notes
    rec = json.loads(notes[0].read_text())
    assert rec["mtime_ns"] == live.st_mtime_ns, (
        "the note describes bytes that are no longer there, so the next pass "
        "will read it as stale and credit the auditor's own document")
    assert rec["size"] == live.st_size


# ── and the run22 defect must NOT come back ────────────────────────────────

def test_a_producer_rewrite_between_passes_lifts_the_refusal(tmp_path):
    """THE RUN22 SHAPE, the direction the union must not break: the gate is first
    to write the path, the PRODUCER then rewrites it with its own measurement, and
    the refusal must LIFT — a producer that runs late is still a producer.

    This is the arm the first cut of the fix bought correctly and the arm a
    stamp-everything rule destroys: on run22 the note was re-stamped onto
    `tapeout_checklist_gen`'s own bytes and the refusal became permanent.
    """
    rel = "reports/audit/tapeout_checklist.json"
    declared = frozenset({PRODUCER})
    assert _audit_pass(tmp_path, "36", rel, gate_rewrites=True,
                       declared_programs=declared) is True, "pass 1"
    # the RUN's producer rewrites the document with its own stamp
    _write(tmp_path, rel, PRODUCER, "the producer's own measurement")
    assert _audit_pass(tmp_path, "36", rel, gate_rewrites=False,
                       declared_programs=declared) is False, (
        "the producer rewrote the document and the audit still refuses it; no "
        "producer can ever reclaim a path the audit touched first")
    # and it stays credited on later passes
    assert _audit_pass(tmp_path, "36", rel, gate_rewrites=False,
                       declared_programs=declared) is False, "pass 3"


def test_a_content_only_refusal_is_not_stamped(tmp_path):
    """The excluded branch, on its own: a document that only LOOKS like a gate
    verdict document is refused, but earns no note — so a later producer rewrite
    can still lift it."""
    rel = "reports/phase3/zz_only_content.json"
    _write(tmp_path, rel, GATE, "written by nobody this pass")
    assert _audit_pass(tmp_path, "9", rel, gate_rewrites=False,
                       declared_programs=frozenset({PRODUCER})) is True
    notes = list((tmp_path / FCC._AUDIT_AUTHORSHIP_DIR).glob("*.json"))
    assert notes == [], (
        "a content-only refusal was stamped; that is the run22 defect, and it "
        "makes the refusal survive a producer rewrite")


def test_a_document_the_run_produced_first_is_never_refused(tmp_path):
    """The baseline that must stay true: producer writes, audit reads, audit
    credits."""
    rel = "reports/audit/tapeout_checklist.json"
    _write(tmp_path, rel, PRODUCER, "produced before any audit")
    assert _audit_pass(tmp_path, "36", rel, gate_rewrites=False,
                       declared_programs=frozenset({PRODUCER})) is False


# ── the shipped expressions, pinned where a refactor would move them ───────

def test_the_shipped_restamp_set_is_the_union():
    """AST, not a text slice: find the `_record_audit_created` call inside
    `check_step` and assert its third argument is the UNION comprehension. An
    earlier anchor of mine on this exact call broke on a rename, so this pins the
    call by NAME and reads the argument's structure."""
    tree = ast.parse((PROGRAMS / "flow_compliance_check.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_record_audit_created"]
    assert calls, "the re-stamp call is gone"
    routed = []
    for c in calls:
        if len(c.args) < 3:
            continue
        src = ast.unparse(c.args[2])
        if "restamp_set(" in src:
            routed.append(src)
    assert routed, (
        "check_step no longer routes its re-stamp through `restamp_set`; the rule "
        "is back to being an inline comprehension no test can drive")
    for src in routed:
        assert "_absent_before_gate" in src and "_prior_created" in src, src
        assert "_is_gate_verdict_document" not in src, src

    # and the shipped rule itself uses BOTH sets
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "restamp_set")
    body = ast.unparse(fn)
    assert "absent" in body and "prior" in body, body


def test_the_redirect_and_the_republish_consider_every_receipt_flag():
    """Both scanners used to `return` at the first `--json`/`--report`, so a
    clause naming two receipts was decided entirely by the first. Pinned on the
    loop body: the early exits inside the flag loop must be `continue`."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef)}
    for name in ("_publish_over_the_audits_own_document",
                 "_receipt_off_a_produced_document"):
        fn = fns[name]
        loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
        assert loops, name
        flag_loop = loops[0]
        # The `is_file()` guard and the class guard sit directly in the loop body.
        returns_in_guards = [
            n for n in ast.walk(flag_loop)
            if isinstance(n, ast.Return)
            and any(isinstance(p, (ast.If, ast.Try))
                    for p in ast.walk(flag_loop))]
        # Structural, not a count: no `return` may be the handler of the
        # not-a-file / not-our-class guards, because that abandons later flags.
        for node in ast.walk(flag_loop):
            if isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp):
                body = node.body
                assert not any(isinstance(b, ast.Return) for b in body), (
                    f"{name}: a guard inside the receipt-flag loop still returns, "
                    f"so only the FIRST receipt flag is ever considered")
        assert returns_in_guards is not None       # the walk above is the check
