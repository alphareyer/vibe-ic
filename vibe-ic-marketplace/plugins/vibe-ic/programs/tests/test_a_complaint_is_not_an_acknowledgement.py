"""F43 — a complaint that X was not acknowledged is not an acknowledgement of X.

`step_internal_fail_bubble_up_check` credits a leaf FAIL as handled when a
waivers.json entry names it, or when a TOP-LEVEL audit records the matching
failure (that is the "bubble-up"). Its acknowledgement corpus is built from
`reports/audit/**/*.json` -- which includes the completion audit, and the
completion audit records THIS GATE's own failure, detail and all. That detail
NAMES the very leaf reports the gate said were unacknowledged, so:

    run N    the gate reports "report X declares FAIL and nothing acknowledges
             it"; the audit records the complaint, putting X on a FAIL line
    run N+1  the corpus carries that line, X matches, X reads as ACKNOWLEDGED,
             the gate passes, and the audit records no complaint
    run N+2  the complaint is gone, X is unacknowledged again

A two-cycle oscillator, and icspm3 measured it as 3,2,3 / 2,3,2 across arms.
The masked state is the dangerous one: a REAL unacknowledged FAIL reads as
handled on every other run.

The fix scopes the exclusion to THIS GATE's own records and nothing else, which
is what these cases pin: a genuine bubble-up of some OTHER gate still counts,
and it still counts when the gate's own complaint sits beside it.
"""
import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import step_internal_fail_bubble_up_check as G  # noqa: E402

LEAF = "reports/phase3/widget_check.json"


def _project(tmp_path, audit=None, waivers=None, leaf_verdict="FAIL"):
    p = tmp_path / "proj"
    (p / "reports" / "audit").mkdir(parents=True)
    (p / "reports" / "phase3").mkdir(parents=True)
    (p / LEAF).write_text(json.dumps(
        {"program": "widget_check", "verdict": leaf_verdict}))
    if audit is not None:
        (p / "reports" / "audit" / "phase23_completion_audit.json").write_text(
            json.dumps(audit, indent=1))
    if waivers is not None:
        (p / "waivers.json").write_text(json.dumps(waivers, indent=1))
    return p


def _self_complaint():
    """What the completion audit records when THIS GATE fails: its own name,
    and a detail that quotes the leaf it is complaining about."""
    return {"verdict": "FAIL",
            "failed_gates": ["step_internal_fail_bubble_up_check"],
            "findings": [{"gate": "step_internal_fail_bubble_up_check",
                          "severity": "ERROR",
                          "detail": (f"report {LEAF} declares verdict=FAIL but "
                                     f"no waivers.json entry references it")}]}


def _genuine_bubble_up():
    """What a real bubble-up looks like: the TOP-LEVEL audit records the LEAF's
    own failure, so the failure reached the project's verdict."""
    return {"verdict": "FAIL", "failed_gates": ["widget_check"],
            "findings": [{"gate": "widget_check", "severity": "ERROR",
                          "detail": "widget_check FAILED"}]}


# ----------------------------------------------------- the masked state, closed

def test_the_gates_own_complaint_does_not_acknowledge_the_leaf(tmp_path):
    """THE OSCILLATOR. The audit's record of this gate's own FAIL names the
    leaf; that must not read as the leaf being handled."""
    v, findings, _ = G.audit(_project(tmp_path, audit=_self_complaint()))
    assert v == "FAIL", v
    assert len(findings) == 1
    assert findings[0].report_file == LEAF


def test_it_is_still_unacknowledged_however_the_record_spells_this_gate(tmp_path):
    """The record may key the gate under any of the names an audit uses."""
    for key in ("gate", "program", "check", "name", "rule"):
        audit = {"verdict": "FAIL", "findings": [
            {key: "step_internal_fail_bubble_up_check",
             "detail": f"report {LEAF} declares verdict=FAIL, unacknowledged"}]}
        v, findings, _ = G.audit(_project(tmp_path / key, audit=audit))
        assert v == "FAIL", (key, v)
        assert len(findings) == 1, key


def test_a_cmd_row_naming_this_gate_is_also_its_own_record(tmp_path):
    """The gate-execution ledger keys on the COMMAND, not on a bare name."""
    audit = {"verdict": "FAIL", "gate_execution_ledger": [
        {"cmd": "step_internal_fail_bubble_up_check .", "rc": 1,
         "verdict": "FAIL",
         "detail": f"{LEAF} declares FAIL and nothing acknowledges it"}]}
    v, findings, _ = G.audit(_project(tmp_path, audit=audit))
    assert v == "FAIL", v
    assert len(findings) == 1


# ----------------------------------------------------- what must STILL count

def test_a_genuine_bubble_up_of_another_gate_still_counts(tmp_path):
    """THE NEGATIVE CONTROL, and the reason the exclusion is scoped to this
    gate alone: a top-level audit recording some OTHER gate's FAIL is exactly
    the acknowledgement this check exists to credit."""
    v, findings, _ = G.audit(_project(tmp_path, audit=_genuine_bubble_up()))
    assert v == "PASS", v
    assert findings == []


def test_the_genuine_bubble_up_wins_when_both_records_are_present(tmp_path):
    """The realistic shape: the audit records BOTH the leaf's failure and this
    gate's complaint about it. Pruning the complaint must not take the real
    acknowledgement with it."""
    audit = _genuine_bubble_up()
    audit["failed_gates"].append("step_internal_fail_bubble_up_check")
    audit["findings"].append(
        {"gate": "step_internal_fail_bubble_up_check",
         "detail": f"{LEAF} unacknowledged FAIL"})
    v, findings, _ = G.audit(_project(tmp_path, audit=audit))
    assert v == "PASS", v
    assert findings == []


def test_a_waiver_still_acknowledges(tmp_path):
    """The other acknowledgement route is untouched: this gate reads
    waivers.json, and the corpus change does not go near it."""
    waivers = {"waived_steps": [
        {"id": 36, "reason": "widget_check is deferred to the integrator",
         "ticket": "ORGANIC-x", "review_required": True}]}
    v, findings, _ = G.audit(
        _project(tmp_path, audit=_self_complaint(), waivers=waivers))
    assert v == "PASS", v
    assert findings == []


# ----------------------------------------------------- the pruning itself

def test_pruning_removes_the_record_and_not_the_document():
    audit = _genuine_bubble_up()
    audit["findings"].append({"gate": "step_internal_fail_bubble_up_check",
                              "detail": "secret_leaf_name FAILED"})
    out = G._without_this_gates_own_records(json.dumps(audit))
    assert "widget_check" in out
    assert "secret_leaf_name" not in out
    assert "step_internal_fail_bubble_up_check" not in out


def test_an_unparseable_corpus_file_falls_back_to_a_line_filter():
    """A file that is not JSON can still be read; the fallback can only ever
    remove SELF-reference, because a line that does not name this gate is
    untouched."""
    txt = ("widget_check FAILED\n"
           "step_internal_fail_bubble_up_check: other_leaf FAILED\n"
           "{not json\n")
    out = G._without_this_gates_own_records(txt)
    assert "widget_check FAILED" in out
    assert "other_leaf" not in out


def test_a_clean_leaf_is_never_a_finding(tmp_path):
    """The gate only ever speaks about a report that declares a FAIL."""
    v, findings, _ = G.audit(
        _project(tmp_path, audit=_self_complaint(), leaf_verdict="PASS"))
    assert findings == []
    assert v in ("PASS", "NOT_EXAMINED"), v
