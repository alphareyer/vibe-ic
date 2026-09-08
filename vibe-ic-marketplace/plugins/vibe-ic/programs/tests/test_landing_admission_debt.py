"""Admission preserves red debt and refuses incomplete or lost evidence."""
import pytest
import json

import test_inherited_red_deadline as D
import test_landing_merge_verdict as B

V = B.V


def hygiene(**over):
    record = dict(status="CLEAN", introduced=[], carried=[], cleared=[],
                  candidate_findings=0, base_findings=0, declared=1)
    record.update(over)
    return record


def test_expired_debt_allows_admission_without_turning_red_green():
    finding = ("FAIL", "repo hygiene: a blocking gate", "")
    v = D._verdict(carried=[finding], ledger=[D._row(since="since-old")],
                   age=D._age)
    assert v.ok is True, v.reasons
    assert v.reasons == []
    assert v.debt["hygiene"] == [finding]
    assert any("THE DEADLINE ON AN INHERITED RED HAS PASSED" in reason
               for reason in v.debt["deadline_diagnostics"])


def test_unowned_debt_allows_admission():
    v = D._verdict(carried=[("FAIL", "old gate", "")], ledger=[], age=D._age)
    assert v.ok is True, v.reasons
    assert "NO OWNER" in v.debt["deadline_diagnostics"][0]


@pytest.mark.parametrize("outcome", ["skipped", "absent"])
def test_failed_to_unasked_is_never_repair(outcome):
    candidate = {} if outcome == "absent" else {"m::red": outcome}
    candidate["m::green"] = "passed"
    delta = V.failed_set_delta({"m::red": "failed", "m::green": "passed"}, candidate)
    v = B._decide(delta=delta)
    assert v.ok is False
    assert delta.fixed == []
    assert delta.silenced == ["m::red"]


def test_new_identity_blocks_even_when_failure_counts_match():
    delta = V.failed_set_delta({"m::old": "failed", "m::new": "passed"},
                              {"m::old": "passed", "m::new": "failed"})
    v = B._decide(delta=delta)
    assert v.ok is False
    assert delta.new_failures == ["m::new"]


@pytest.mark.parametrize("record", [{}, {"status": "CLEAN"},
                                     {"status": "INTRODUCED"}, [], "CLEAN"])
def test_malformed_check_result_cannot_admit(record):
    v = B._decide(hygiene=record)
    assert v.ok is False
    assert v.unmeasurable is True


def test_missing_hygiene_result_cannot_admit():
    v = B._decide(hygiene=None)
    assert v.ok is False


def test_unmeasured_on_both_sides_is_incomplete():
    v = B._decide(hygiene=hygiene(no_verdict_either_side=["required gate"]))
    assert v.ok is False


def test_invalid_outcome_cannot_be_credited_as_a_fix():
    delta = V.failed_set_delta({"m::red": "failed"}, {"m::red": "probably fine"})
    assert delta.fixed == []
    assert B._decide(delta=delta).ok is False


def test_real_ledger_preserves_inherited_red_and_admission():
    from _hostpaths import require_repo
    rows = json.loads(require_repo("tools", "ci", "gate_red_since.json").read_text())["acknowledged"]
    findings = [("FAIL", "repo hygiene: a blocking gate", "")]
    v = D._verdict(carried=findings, ledger=rows,
                   age=lambda sha: D.G.MAX_BOUND_DAYS + 1)
    assert v.ok is True, v.reasons
    assert v.debt["hygiene"] == findings
    assert len(v.debt["deadline_diagnostics"]) == 1


def test_owned_program_passes_scoped_chip_agnostic_audit(tmp_path):
    import source_chip_agnostic_check as guard
    target = tmp_path / "programs"
    target.mkdir()
    (target / B._PROG.name).write_bytes(B._PROG.read_bytes())
    verdict, findings = guard.audit(tmp_path)
    assert verdict == "PASS", findings
    assert guard.SCAN_CENSUS["files_read"] == 1


def test_passing_gate_lost_coverage_blocks():
    base = V.parse_land_log(B._GOOD_LOG)
    candidate = V.parse_land_log(B._GOOD_LOG.replace("  PASS  repo hygiene gates", "  SKIP  repo hygiene gates"))
    v = B._decide(base_land=base, land=candidate)
    assert v.ok is False
    assert any("A PASSING GATE WAS WEAKENED" in reason for reason in v.reasons)


@pytest.mark.parametrize("mutation", ["missing", "malformed", "selection",
                                      "gate_missing", "gate_malformed", "gate_partial"])
def test_cli_missing_evidence_returns_incomplete_record(tmp_path, mutation):
    def damage(path):
        if mutation == "missing":
            path.unlink()
        elif mutation == "malformed":
            path.write_text("<broken")

    extra = ("--base-selection", str(tmp_path / "missing-selection")) if mutation == "selection" else ()
    if mutation.startswith("gate_"):
        base_log = tmp_path / "explicit_base_gate.log"
        if mutation == "gate_malformed":
            base_log.write_text("not a gate record\n")
        elif mutation == "gate_partial":
            base_log.write_text("=== gatekeeper landing gates ===\n  PASS  one gate\n")
        extra = ("--base-land-log", str(base_log))
    result, doc = B._cli(tmp_path, B._GOOD_LOG, B._CASE_OK, B._CASE_OK, B._SEL,
                         base_mutator=damage, extra=extra)
    assert result.returncode == 2, result.stdout + result.stderr
    assert doc["verdict"] == "REFUSE"
    assert doc["admission"] == "INCOMPLETE"
    assert doc["incomplete"] != []


def test_cli_admission_does_not_rewrite_red_test_result(tmp_path):
    result, doc = B._cli(tmp_path, B._RED_TEST_TIER_LOG, B._CASE_RED, B._CASE_RED, B._SEL)
    assert result.returncode == 0, result.stdout + result.stderr
    assert doc["admission"] == "READY"
    assert doc["delta"]["preexisting"] == [
        "pytest_aggregate.programs.tests.test_thing::a",
        "pytest_aggregate_process::whole_selection::process_exit"]
    assert doc["land"]["test_tier_failed"] is True
    assert doc["debt"]["tests"] == doc["delta"]["preexisting"]


def test_cli_nameless_check_cannot_admit(tmp_path):
    def damage(path):
        tree = B.ET.parse(path)
        for tc in V._aggregate_testcases(tree.getroot()):
            tc.attrib.pop("name", None)
        tree.write(path)

    result, doc = B._cli(tmp_path, B._GOOD_LOG, B._CASE_OK, B._CASE_OK, B._SEL,
                         candidate_mutator=damage)
    assert result.returncode == 2, result.stdout + result.stderr
    assert doc["admission"] == "INCOMPLETE"


@pytest.mark.parametrize("contents", [None, "{broken"])
def test_bad_debt_ledger_is_disclosed_without_blocking_admission(tmp_path, contents):
    ledger = tmp_path / "ledger.json"
    if contents is not None:
        ledger.write_text(contents)
    result, doc = B._cli(tmp_path, B._GOOD_LOG, B._CASE_OK, B._CASE_OK, B._SEL,
                         extra=("--red-since-ledger", str(ledger)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert doc["admission"] == "READY"
    assert doc["debt"]["deadline_evaluated"] is False
    assert any("DEBT LEDGER UNMEASURED" in item for item in doc["debt"]["deadline_diagnostics"])
