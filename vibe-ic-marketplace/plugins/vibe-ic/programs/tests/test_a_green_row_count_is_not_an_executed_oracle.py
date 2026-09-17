"""Ten green JUnit rows over nine empty scaffolds is not functional verification.

R-0915-87(2). MEASURED on the subservient tapeout run r27 (lane icsub2,
2026-09-16, gf180mcuD). `cpu_functional_oracle_waiver_check` — a BLOCKING gate —
returned

    PASS: the record's functional_verified=true is SUBSTANTIATED by
    phase2/stage1/sim_professional/l10_unit_tb/results.xml:
    tests=10 passed=10 failures=0 errors=0

while NINE of those ten declared L10 cases never executed an oracle at all.
The nine testbenches exist and are green, but each is the SUBSTANCE FLOOR the
generator writes when no oracle is derivable: it asserts only that no output
stays X/Z after reset, and says so in its own machine-readable header,
`VIBEIC_TB_ORACLE: NONE (substance floor only)`. The single real oracle was
`reset_n_cycle_instruction` (reset-to-first-bus-activity latency).

So a CPU whose instruction set was never exercised — no RV32I suite, no
compressed, no M-extension multiply/divide, no Zicsr, no fences — stood one
gate away from a published PASS, and the gate said the claim was SUBSTANTIATED.

THE JUNIT PREDICATE IS NECESSARY AND WAS NEVER SUFFICIENT. A transcript proves
a suite ran and did not fail; it cannot prove WHAT it checked. Every refusal
the gate already made — a failing transcript, a vacuous zero-test one, a
dangling pointer, a hand-edited flag — is untouched and pinned below. What is
added is the question the transcript cannot answer: did the declared case's own
oracle RUN?

ONE GUARD, BOTH PASS ROUTES. The gate reaches PASS two ways — the
substantiated-claim route and the professional-TB result slot — and on r27 BOTH
rested on the same ten rows. A predicate applied at one of two doors is not a
predicate.

chip-AGNOSTIC: synthetic projects in tmp_path, plus r27's own execution record
vendored as the fixture the ruling asked for.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import cpu_functional_oracle_waiver_check as C  # noqa: E402
import _l10_execution as _l10x  # noqa: E402

#: r27's real shape: ten declared cases, ONE of which executed its oracle.
R27_CASES = [
    "blinky_hex", "hello_hex", "i_rst_glitch_instruction_fetch_race",
    "plugin_c_16_bit_compressed", "plugin_m_mul_div",
    "plugin_zicsr_csr_access_timer_irq", "reset_assert_sram",
    "reset_n_cycle_instruction", "rv32i_40", "zifencei",
]
R27_EXECUTED = "reset_n_cycle_instruction"
R27_SCAFFOLD_DETAIL = ("simulator ran the substance-floor scaffold, but the "
                       "declared L10 case oracle was not executed")


def _project(tmp_path: Path, executed=(R27_EXECUTED,), cases=R27_CASES,
             record=True) -> Path:
    proj = tmp_path / "p"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(json.dumps(
        {"fields": {"test_cases": [
            {"name": c, "kind": "functional_vector"} for c in cases]}}))
    if record:
        # r27's real record shape, key for key — `cases` is a LIST of rows and
        # the reader binds it to the declaration's own sha256.
        rows = [
            {"id": c, "verdict": "PASS", "sim_executed": True}
            if c in executed else
            {"id": c, "verdict": "NOT_EXECUTED", "sim_executed": False,
             "detail": R27_SCAFFOLD_DETAIL}
            for c in cases
        ]
        out = proj / "reports" / "phase2" / "sim"
        out.mkdir(parents=True)
        (out / "l10_execution.json").write_text(json.dumps({
            "schema": _l10x.SCHEMA,
            "cases": rows,
            "producer": "testbench_gen.run_unit_tbs",
            "tb_dir": str(proj / "phase2" / "stage1" / "sim" / "tb"),
            "source_junit": "results.xml",
        }))
    return proj


# ── the refusal: r27's own numbers ──────────────────────────────────────────
def test_r27s_one_of_ten_is_refused_by_name(tmp_path):
    proj = _project(tmp_path)
    ran = C._oracles_that_actually_ran(proj)
    assert ran["declared_count"] == 10
    assert ran["executed_count"] == 1
    assert ran["executed"] == [R27_EXECUTED]
    assert ran["not_executed_count"] == 9

    refusal = C._oracle_execution_refusal(proj, "results.xml")
    assert refusal is not None
    assert "only 1 of 10 declared L10 case(s) EXECUTED their own oracle" in refusal
    # NAMED, not merely counted: a reviewer must be able to act on it. The
    # list is capped at six and the remainder is DISCLOSED rather than
    # dropped, so the sentence stays readable without hiding anything.
    named = [c for c in R27_CASES if c in refusal]
    assert len(named) == 6, named
    assert "(+3 more)" in refusal
    assert ran["not_executed_count"] == len(named) + 3


def test_the_refusal_says_what_the_green_rows_were(tmp_path):
    """The sentence has to explain why a passing transcript is not enough, or
    the next reader re-grants the waiver from the same file."""
    refusal = C._oracle_execution_refusal(_project(tmp_path), "results.xml")
    assert "green row count over substance-floor scaffolds" in refusal
    assert "not functional verification" in refusal


# ── the other direction: every oracle ran ───────────────────────────────────
def test_all_ten_executed_is_not_refused(tmp_path):
    proj = _project(tmp_path, executed=tuple(R27_CASES))
    assert C._oracle_execution_refusal(proj, "results.xml") is None


def test_one_case_short_still_refuses(tmp_path):
    """Nine of ten is still not ten. There is no majority rule here."""
    proj = _project(tmp_path, executed=tuple(R27_CASES[:-1]))
    refusal = C._oracle_execution_refusal(proj, "results.xml")
    assert refusal is not None and "9 of 10" in refusal
    assert R27_CASES[-1] in refusal


# ── fail-closed: every unreviewable shape refuses ───────────────────────────
def test_a_missing_execution_record_refuses(tmp_path):
    proj = _project(tmp_path, record=False)
    refusal = C._oracle_execution_refusal(proj, "results.xml")
    assert refusal is not None
    assert "0 of 10" in refusal


def test_an_unreadable_execution_record_refuses(tmp_path):
    proj = _project(tmp_path)
    rec = proj / "reports" / "phase2" / "sim" / "l10_execution.json"
    rec.write_text("{ this is not json")
    assert C._oracle_execution_refusal(proj, "results.xml") is not None


def test_a_schema_mismatch_refuses(tmp_path):
    proj = _project(tmp_path)
    rec = proj / "reports" / "phase2" / "sim" / "l10_execution.json"
    rec.write_text(json.dumps({"schema": "something.else", "available": True,
                               "rows": {c: {"verdict": "PASS",
                                            "sim_executed": True}
                                        for c in R27_CASES}}))
    assert C._oracle_execution_refusal(proj, "results.xml") is not None


def test_a_pass_row_not_backed_by_sim_executed_refuses(tmp_path):
    """The exact forgery this closes, in miniature: a row that CLAIMS PASS
    without having run. `_l10_execution` already refuses it; this proves the
    gate inherits that refusal rather than re-deciding it."""
    proj = _project(tmp_path, executed=())
    rec = proj / "reports" / "phase2" / "sim" / "l10_execution.json"
    doc = json.loads(rec.read_text())
    for row in doc["cases"]:
        row["verdict"], row["sim_executed"] = "PASS", False
    rec.write_text(json.dumps(doc))
    refusal = C._oracle_execution_refusal(proj, "results.xml")
    assert refusal is not None and "0 of 10" in refusal


def test_a_case_absent_from_the_record_refuses(tmp_path):
    proj = _project(tmp_path, executed=tuple(R27_CASES))
    rec = proj / "reports" / "phase2" / "sim" / "l10_execution.json"
    doc = json.loads(rec.read_text())
    doc["cases"] = [r for r in doc["cases"] if r["id"] != "rv32i_40"]
    rec.write_text(json.dumps(doc))
    refusal = C._oracle_execution_refusal(proj, "results.xml")
    assert refusal is not None and "rv32i_40" in refusal


def test_an_empty_declaration_is_not_this_guards_question(tmp_path):
    """SCOPE, stated as a test. This guard compares EXECUTED against DECLARED;
    over an empty declaration there is nothing to compare, and the gate's own
    denominator path already reports "0 functional tests ran for N declared
    row(s)". I first refused here as well, which is scope the ruling did not
    ask for — it turned four landed fixtures red for a fact none of them is
    about. Narrowed, and pinned here so it does not creep back."""
    proj = _project(tmp_path, cases=[], executed=())
    assert C._oracle_execution_refusal(proj, "results.xml") is None


# ── the guard is at BOTH doors ──────────────────────────────────────────────
def test_both_pass_routes_are_guarded():
    """The gate reaches PASS two ways and on r27 both rested on the same ten
    rows. Read from the source so a third route added later is visible as an
    unguarded one."""
    src = Path(C.__file__).read_text()
    calls = src.count("_oracle_execution_refusal(project") - src.count(
        "def _oracle_execution_refusal(project")
    assert calls == 2, (
        "every transcript-backed PASS route must consult the guard")


def test_the_execution_question_is_asked_through_the_shared_reader():
    """`professional_tb_check` and `l10_tb_conformance_check` read the same
    module. A second reader here would be a second answer."""
    src = Path(C.__file__).read_text()
    assert "import _l10_execution as _l10x" in src
    assert "_l10x.case_state" in src
    ran = C._oracles_that_actually_ran(Path("/nonexistent-project"))
    assert ran["asked_through"] == "_l10_execution.case_state"


# ── R-0915-96: a DV checklist row is not a case that owes an oracle ──────────
#
# MEASURED, opentitan_aes run_aes_h (e322992a6): "only 8 of 111 declared L10
# case(s) EXECUTED their own oracle" -- where the 111 were 8 known_answer_vector
# rows (all EXECUTED, all PASS) and 103 verification_checklist rows harvested
# from the vendor's DV checklist ("DV checklist item SPEC_COMPLETE -- Done").
# This gate already keeps that kind out of its denominator (`_split_executable`);
# the executed-oracle guard asked over every row and re-opened the failure.

def _aes_project(tmp_path: Path, kav_executed=8, hidden_functional=None) -> Path:
    proj = tmp_path / "aes"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    kav = [f"kav_{i}" for i in range(8)]
    rows = [{"name": c, "kind": "known_answer_vector"} for c in kav]
    rows += [{"name": f"dv_item_{i}", "kind": "verification_checklist",
              "stimulus": f"DV checklist item ITEM_{i} -- Done"}
             for i in range(103)]
    if hidden_functional:
        rows.insert(50, {"name": hidden_functional, "kind": "functional_vector"})
    (gd / "L10_TEST_CASES.json").write_text(json.dumps(
        {"fields": {"test_cases": rows}}))
    rec = [{"id": c, "verdict": "PASS", "sim_executed": True}
           for c in kav[:kav_executed]]
    out = proj / "reports" / "phase2" / "sim"
    out.mkdir(parents=True)
    (out / "l10_execution.json").write_text(json.dumps({
        "schema": _l10x.SCHEMA, "cases": rec,
        "producer": "testbench_gen.run_unit_tbs",
        "tb_dir": str(proj / "phase2" / "stage1" / "sim" / "tb"),
        "source_junit": "results.xml"}))
    return proj


def test_aes_checklist_rows_do_not_owe_an_oracle(tmp_path):
    """RED on 5fc0a5593..e322992a6: refused 8 of 111."""
    proj = _aes_project(tmp_path)
    ran = C._oracles_that_actually_ran(proj)
    assert ran["declared_count"] == 8, ran["declared_count"]
    assert ran["not_executed_count"] == 0
    assert C._oracle_execution_refusal(proj, "results.xml") is None


def test_a_functional_row_hidden_among_checklist_rows_is_still_demanded(tmp_path):
    """CONTROL behind the mechanism: the narrowing must not blind the guard."""
    proj = _aes_project(tmp_path, hidden_functional="aes_gcm_tag_check")
    refusal = C._oracle_execution_refusal(proj, "results.xml")
    # Both arms: the hidden functional row is refused BY NAME. (The count in
    # the sentence differs by arm, so it is not what this control pins.)
    assert refusal is not None
    ran = C._oracles_that_actually_ran(proj)
    assert "aes_gcm_tag_check" in [r["case"] for r in ran["not_executed"]]


def test_a_known_answer_vector_that_did_not_run_is_still_refused(tmp_path):
    """CONTROL: an executable kind short of its oracle still refuses."""
    proj = _aes_project(tmp_path, kav_executed=7)
    assert C._oracle_execution_refusal(proj, "results.xml") is not None
    ran = C._oracles_that_actually_ran(proj)
    assert "kav_7" in [r["case"] for r in ran["not_executed"]]
