"""R-0915-39 — a declared L10 case that did not execute is refused BY NAME.

`professional_tb_check` used to collapse the whole answer to one sentence:

    "one or more declared L10 cases were not executed"

A block with no subject. A reader could not tell WHICH case, or why — and the
JUnit sitting beside it says `tests=10 failures=0 errors=0 skipped=0`, so every
log reads PASS.

MEASURED on `subservient` x gf180mcuD (lane icsub2, r16): all TEN declared
cases have rows and all ten RAN, and NINE carry

    NOT_EXECUTED — simulator ran the substance-floor scaffold, but the
    declared L10 case oracle was not executed

with the producer saying it in its own per-case log:

    [TB rv32i_40] SUBSTANCE_OK — DUT subservient driven, 5 output(s) resolved
    (case oracle not yet written)

The execution record already knows this per case. Collapsing it threw away the
only part that makes the refusal actionable, and left a real producer gap —
nine unwritten oracles — reading as an opaque upstream block.
"""
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import json  # noqa: E402
import professional_tb_check as P  # noqa: E402
import _path_layout as _pl  # noqa: E402
import _l10_execution as _l10x  # noqa: E402

IDS = ["alpha", "beta", "gamma"]

_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
<testsuite name="l10_unit_tb" tests="3" failures="0" errors="0" skipped="0">
  <testcase classname="l10_unit_tb" name="alpha" time="1.0"></testcase>
  <testcase classname="l10_unit_tb" name="beta"  time="1.0"></testcase>
  <testcase classname="l10_unit_tb" name="gamma" time="1.0"></testcase>
</testsuite>
</testsuites>
"""


def _project(tmp_path):
    """A project with the JUnit and the L10 declaration the track reads."""
    p = tmp_path / "proj"
    junit = p / P._L10_UNIT_JUNIT_REL
    junit.parent.mkdir(parents=True)
    junit.write_text(_JUNIT)
    docs = _pl.generated_docs_dir(p)
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L10_TEST_CASES.json").write_text(json.dumps(
        {"test_cases": [{"name": i} for i in IDS]}))
    rec = p / "reports" / "phase2" / "sim"
    rec.mkdir(parents=True)
    (rec / "l10_execution.json").write_text(json.dumps(
        {"source_junit": P._L10_UNIT_JUNIT_REL.as_posix()}))
    return p


def _wire(monkeypatch, states, project):
    record = {"available": True, "malformed": [],
              "rows": {i: {} for i in IDS},
              "path": str(project / "reports/phase2/sim/l10_execution.json")}
    monkeypatch.setattr(_l10x, "load_record", lambda *a, **k: record)
    monkeypatch.setattr(_l10x, "case_state", lambda cid, rec: states[cid])
    return record


def _states(**kw):
    return {cid: kw.get(cid, (_l10x.PASS, "execution record row (verdict=PASS)"))
            for cid in IDS}


def test_every_unexecuted_case_is_named_with_its_own_reason(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    states = _states(
        beta=("NOT_EXECUTED", "the declared L10 case oracle was not executed"),
        gamma=("NOT_EXECUTED", "no hex image was built for this case"))
    _wire(monkeypatch, states, proj)
    out = P.l10_unit_tb_track(proj)
    assert out["verdict"] == "NOT_CHECKED", out
    named = {r["case"]: r for r in out["cases_not_executed"]}
    assert set(named) == {"beta", "gamma"}, named
    assert named["gamma"]["reason"] == "no hex image was built for this case"
    assert out["declared_case_count"] == 3
    assert "2 of 3" in out["reason"]
    assert "beta" in out["reason"] and "one or more" not in out["reason"]


def test_all_executed_still_passes(tmp_path, monkeypatch):
    """THE NEGATIVE CONTROL: a track where every declared case really ran must
    still PASS, and must carry no refusal list."""
    proj = _project(tmp_path)
    _wire(monkeypatch, _states(), proj)
    out = P.l10_unit_tb_track(proj)
    assert out["verdict"] == "PASS", out
    assert "cases_not_executed" not in out


def test_a_failed_case_is_still_a_FAIL_not_a_named_refusal(tmp_path, monkeypatch):
    """A case that ran and FAILED is a different answer and keeps its own
    branch: the naming change must not turn a failure into a block."""
    proj = _project(tmp_path)
    _wire(monkeypatch, _states(beta=(_l10x.FAIL, "assertion fired")), proj)
    out = P.l10_unit_tb_track(proj)
    assert out["verdict"] == "FAIL", out
    assert "cases_not_executed" not in out


def test_the_reason_never_says_only_one_or_more():
    """The sentence this ruling exists to remove must not come back."""
    src = (PROG / "professional_tb_check.py").read_text()
    assert "one or more declared L10 cases were not executed" not in src
