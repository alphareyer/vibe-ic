"""R-0915-137 — the two mechanisms that keep an audit clause from being the only
writer of a declared output, PINNED against the measurement rather than argued.

R-0915-136 says an audit-created output is never run evidence. Two mechanisms
carry that: `flow_declared_producer_run` runs the declared producer so the RUN is
the writer, and `flow_compliance_check`'s authorship note records the cases where
the auditor got there first. R-0915-137 asked for both to be proven by mtimes on a
run21 copy, in all three branches of the producer runner's own rule and in the
negative direction.

MEASURED ON A COPY OF spm run21, and the measurement RETRACTS a claim I made on
the icslot62 page. I had read run21's mtimes — the two documents written 17:56:31,
`flow_declared_producer_run` at 17:59:19, the authorship notes at 18:06 — as
evidence that the note arrives ten minutes after the file and that the producer
runner's second trigger ("or when the audit's own authorship note claims the
file") is therefore unreachable inside one run. Running it says otherwise:

    the note is born WITH the file
        file mtime          06:54:17.443
        note's mtime_ns     06:54:17.443   (the exact stat, recorded IN the note)
        note file mtime     06:54:17.557   -> +0.114 s
    branch 1, target ABSENT
        step 38  foundry_handoff_package_check  rc=0 (target absent)   -> produced
    branch 2, present but AUDIT-CLAIMED
        step 38  foundry_handoff_package_check  rc=0 (present, but the audit's own
                 authorship note claims it: the only writer so far is the auditor)
                 -> RE-produced
    branch 3, the RUN wrote it and no note claims it
        24 already produced by the run, 0 owed, 0 executed
        the document's mtime is UNCHANGED -> left byte-for-byte alone

So both mechanisms work today, and run21's 18:06 note mtimes are a LATER audit
pass re-stamping what it refused — `_record_audit_created` is called once per step
evaluation, immediately after `_evaluate_gate`, not at the end of the audit. The
lesson for me is the one my own notes already carried: I inferred a mechanism from
mtimes instead of running it.

WHAT THIS FILE IS FOR, THEN. Neither property is enforced anywhere: the note's
birth-with-the-file is an ordering inside one function, and the three-branch rule
is prose in a docstring. Both are exactly the kind of thing a later refactor moves
without noticing — and the cost of moving either is a declared output the audit
refuses forever while a producer sits wired and useless. They are pinned here, on
synthetic trees, so nothing depends on a run tree being present.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402
import flow_declared_producer_run as FD                      # noqa: E402


def _clause(target: str, sibling: str) -> dict:
    """One declared-producer row in the shape `declared_producer_clauses` emits.

    `siblings` is load-bearing: `owed` uses it to answer "did the run perform
    this step at all", and a clause with no surviving sibling is deliberately
    left alone.
    """
    return {"step": "zz", "program": "zz_check",
            "command": f"zz_check . --json {target}",
            "target": target, "siblings": [sibling]}


def _ran_step(project: Path, sibling: str) -> None:
    p = project / sibling
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("the step's other declared output, so the step counts as run\n")


# ── the note is born with the file it claims ───────────────────────────────

def test_the_note_records_the_stat_of_the_file_it_claims(tmp_path):
    """The note carries the file's OWN mtime_ns, not the moment the note was
    written. That is what lets a later reader tell "this is the same bytes the
    auditor wrote" from "the run has since replaced it"."""
    rel = "reports/audit/zz_verdict.json"
    doc = tmp_path / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('{"gate": "zz_check", "verdict": "PASS"}\n')
    stat_ns = os.stat(doc).st_mtime_ns

    FCC._record_audit_created(tmp_path, "zz", [rel])

    notes = list((tmp_path / FCC._AUDIT_AUTHORSHIP_DIR).glob("*.json"))
    assert len(notes) == 1, notes
    rec = json.loads(notes[0].read_text())
    assert rec["rel"] == rel and str(rec["step"]) == "zz", rec
    assert rec["mtime_ns"] == stat_ns, (
        f"the note recorded {rec['mtime_ns']} for a file whose stat is "
        f"{stat_ns}; a note that does not carry the file's own stat cannot tell "
        f"a later pass whether the bytes it refused are still there")
    assert rec["size"] == doc.stat().st_size


def test_the_note_is_claimed_only_while_the_bytes_it_recorded_are_there(tmp_path):
    """THE PROPERTY THE RECORDED STAT BUYS, and the negative direction of it: a
    note whose file has since been rewritten by the run must stop claiming it,
    or the producer runner would re-produce a document the run had just made."""
    rel = "reports/audit/zz_verdict.json"
    doc = tmp_path / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('{"gate": "zz_check", "verdict": "PASS"}\n')
    FCC._record_audit_created(tmp_path, "zz", [rel])
    assert FD._audit_claims(tmp_path, "zz", rel) is True

    # The RUN replaces it. Same path, different bytes and stat.
    os.utime(doc, ns=(os.stat(doc).st_mtime_ns + 10 ** 9,) * 2)
    assert FD._audit_claims(tmp_path, "zz", rel) is False, (
        "the note still claims a file the run has rewritten; the producer "
        "runner would re-produce a document the run already made")


def test_the_note_is_written_by_the_same_pass_that_evaluates_the_gate():
    """SOURCE-level, because the ORDER is the property and it lives inside one
    function: the note is recorded in the same block that evaluates the gate,
    immediately after it, so the note exists as soon as the auditor's write
    does. A refactor that defers it to the end of the audit is what I mistakenly
    believed had already happened."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text()
    i_eval = src.index("passed, reasons = _evaluate_gate(project, gate")
    i_note = src.index("_record_audit_created(project, sid, _audit_produced)")
    i_next_step = src.index("def _check_step", i_eval) if "def _check_step" in src[i_eval:] else len(src)
    assert i_eval < i_note < i_next_step, (
        f"the authorship note is recorded at {i_note}, outside the gate "
        f"evaluation block that starts at {i_eval}; a note that arrives later "
        f"than the write it describes cannot be read by anything that runs "
        f"between the two")


# ── the producer runner's three branches, all reachable ────────────────────

def test_an_absent_target_is_owed(tmp_path):
    _ran_step(tmp_path, "reports/zz/sibling.json")
    to_run, skipped = FD.owed(tmp_path, [_clause("reports/zz/target.json",
                                                 "reports/zz/sibling.json")])
    assert [r["why"] for r in to_run] == ["target absent"], (to_run, skipped)


def test_a_present_but_audit_claimed_target_is_owed(tmp_path):
    """BRANCH 2, the one I wrongly called unreachable. It is reached whenever the
    auditor wrote the document first — which is exactly the state R-0915-136
    refuses to accept as run evidence."""
    rel = "reports/zz/target.json"
    _ran_step(tmp_path, "reports/zz/sibling.json")
    doc = tmp_path / rel
    doc.write_text('{"gate": "zz_check", "verdict": "PASS"}\n')
    FCC._record_audit_created(tmp_path, "zz", [rel])
    to_run, _ = FD.owed(tmp_path, [_clause(rel, "reports/zz/sibling.json")])
    assert len(to_run) == 1, to_run
    assert "the audit's own authorship note claims it" in to_run[0]["why"]


def test_a_document_the_run_wrote_is_left_alone(tmp_path):
    """THE NEGATIVE ARM. The producer runner must never rewrite the run's own
    work — that would make the auditor's repair pass a writer of run evidence,
    which is the defect it exists to remove, inverted."""
    rel = "reports/zz/target.json"
    _ran_step(tmp_path, "reports/zz/sibling.json")
    (tmp_path / rel).write_text('{"produced": "by the run"}\n')
    to_run, skipped = FD.owed(tmp_path, [_clause(rel, "reports/zz/sibling.json")])
    assert to_run == [], to_run
    assert [r["why"] for r in skipped] == ["the run already produced it"]


def test_a_step_the_run_never_performed_is_not_owed(tmp_path):
    """The conservative direction the runner's own comment records: without this
    the pass manufactured work for steps a HARDMACRO delivery does not have, and
    turned three MISSING steps into three FAILs."""
    to_run, skipped = FD.owed(tmp_path, [_clause("reports/zz/target.json",
                                                 "reports/zz/sibling.json")])
    assert to_run == [], to_run
    assert "the run did not perform this step" in skipped[0]["why"]
