"""v1.0.38 — #651 (P0 honesty blocker): a DRC-waived tapeout
(signoff_audit verdict_tier=PASS_WITH_WAIVERS) must NOT collapse onto a
bare PASS at the flow gate. CLAUDE.md rule 11: PASS_WITH_WAIVERS must
NEVER be conflated with bare PASS.

ROOT CAUSE (source): `signoff_audit.main()` returned `0 if result.passed
else 1`; for verdict_tier=='PASS_WITH_WAIVERS' `result.passed` is True →
rc 0, identical to a clean PASS. The Step-36 tapeout flow gate
(`tapeout_signoff_check` = signoff_audit --mode tapeout) is an rc-only
`program_exit_zero` predicate, so the WITH_WAIVERS distinction was lost.

FIX (two-sided, self-consistent):
  (a) signoff_audit.main() returns a DISTINCT, documented exit code
      (WAIVER_EXIT_CODE=3) when verdict_tier=='PASS_WITH_WAIVERS' and prints
      a `PASS_WITH_WAIVERS:` stdout sentinel. It does not approve or write
      a Step-36 waiver. Clean PASS stays rc 0; FAIL stays rc 1.
  (b) flow_compliance_check._check_program_exit_zero recognises rc 3 +
      the sentinel and bubbles a `__WAIVER_HINT__` with sign-off credit
      details. check_step requires an accepted owner record before a waiver
      affects its verdict. A bare rc 3 with NO sentinel is refused.

chip-AGNOSTIC: the distinction is carried by the verdict_tier + structural
Step-36 id + the rc/sentinel convention — no chip/vendor/SKU literal. Any
tapeout with a waived DRC step hits this.

Result objects / JSON are built synthetically (unit-level scope — #651 is
source-verified, not benchmark-reproduced).
"""
from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gdsii  # noqa: E402
import _si_signoff_fixture  # noqa: E402
import _tapeout_timing_fixture  # noqa: E402

# 2026-07-27 (review follow-up): the tape-out GDS slot credits ONLY the flow's
# declared stream-out artefact (phase3/stage4/gds/*.gds), and only when it
# carries real GDSII substance. This file's subject is not the GDS slot; it
# just needs that slot satisfied, so its tape-out artefact is now a real
# minimal GDSII stream at the declared path rather than a text placeholder.

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import signoff_audit as sa  # noqa: E402
import flow_compliance_check as fc  # noqa: E402
from _ai_judgement_fixture import sign as _sign_ai_fixture  # noqa: E402

SIGNOFF = PROGRAMS / "signoff_audit.py"


# ---------------------------------------------------------------------------
# Synthetic project builders
# ---------------------------------------------------------------------------
def _report_db(category_counts: dict) -> str:
    """Minimal KLayout DRC report-database XML, N <item>s per rule category."""
    items = []
    for rule, n in category_counts.items():
        for _ in range(n):
            items.append(
                f"  <item>\n"
                f"   <category>'{rule}'</category>\n"
                f"   <cell>'top'</cell>\n"
                f"   <values><value>box: (0,0;1,1)</value></values>\n"
                f"  </item>")
    body = "\n".join(items)
    cat_defs = "\n".join(
        f"  <category><name>{r}</name></category>" for r in category_counts)
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<report-database>\n"
        " <categories>\n" + cat_defs + "\n </categories>\n"
        " <items>\n" + body + "\n </items>\n"
        "</report-database>\n")


def _write_ready_checklist(project: Path) -> None:
    """Step 36's declared output as its producer writes it (setup only):
    `tapeout_checklist_gen`, READY_FOR_TAPEOUT, every blocker present. The
    step-36 gate now READS this document (R-0929-U14-OWNER-WAIVER)."""
    checklist = project / "reports/audit/tapeout_checklist.json"
    checklist.parent.mkdir(parents=True, exist_ok=True)
    checklist.write_text(json.dumps({
        "program": "tapeout_checklist_gen", "verdict": "READY_FOR_TAPEOUT",
        "summary": {"blockers_total": 1, "blockers_present": 1,
                    "blockers_missing": 0},
        "items": [{"name": "gds", "severity": "blocker", "present": True}],
        "reviewer_todo": ["Review the synthetic DRC waiver"],
    }))


def _base_project(tmp_path: Path) -> Path:
    """gds + netlist + timing + genuine-match LVS always present; DRC
    supplied per-test.

    2026-07-27: tapeout mode gained a fifth pillar (LVS). This suite pins the
    rc/sentinel/waiver-accounting CONTRACT, not the pillar set, so the base
    fixture now carries a genuine netgen match — without it every case here
    would collapse onto FAIL and stop discriminating the three tiers."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    _gdsii.write_declared_streamout(tmp_path, "chip_top.gds")
    (tmp_path / "chip_top_synth.v").write_text("module chip_top();endmodule\n")
    (tmp_path / "sta_timing.rpt").write_text("slack 0.1\n")
    (tmp_path / "reports" / "phase3").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports/phase3/lvs.rpt").write_text(
        "Netlists match uniquely.\nFinal result: Circuits match uniquely.\n")
    _write_ready_checklist(tmp_path)
    # 2026-07-28: tape-out mode gained an SI (crosstalk-delay) blocking
    # condition. This fixture is about the rc / sentinel / waiver-accounting
    # contract, so it carries a PROVED SI verdict — without one every case
    # here would collapse onto the SI refusal and stop discriminating what it
    # exists to pin.
    _si_signoff_fixture.write_proved_si_report(tmp_path)
    # U17: the timing slot credits Step 23/32 PASS verdicts, not a report.
    _tapeout_timing_fixture.write_timing_signoff_pass(tmp_path)
    return tmp_path


def _waiver_project(tmp_path: Path) -> Path:
    """Tapeout-ready EXCEPT the only DRC items are stdcell-library-internal
    (raw>0, design-level 0) → verdict_tier=PASS_WITH_WAIVERS."""
    p = _base_project(tmp_path)
    (p / "drc_signoff.rpt").write_text(
        _report_db({"li.3": 500, "li.5": 300, "ct.2": 200, "li.1": 14}))
    return p


def _clean_project(tmp_path: Path) -> Path:
    """Tapeout-ready with a genuinely clean DRC (0 violations) → bare PASS."""
    p = _base_project(tmp_path)
    (p / "drc_signoff.rpt").write_text(_report_db({}))  # zero items
    return p


def _fail_project(tmp_path: Path) -> Path:
    """No DRC report at all → 4 of 5 evidence → FAIL."""
    return _base_project(tmp_path)


def _run_signoff(proj: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SIGNOFF), str(proj), "--mode", "tapeout"],
        capture_output=True, text=True)


def _signed_waiver_project(path: Path) -> Path:
    project = _waiver_project(path)
    assert _run_signoff(project).returncode == sa.WAIVER_EXIT_CODE
    _write_ready_checklist(project)
    _sign_ai_fixture(project, "36")
    return project


def _approve_tapeout(project: Path) -> dict:
    (project / "waivers.json").write_text(json.dumps({"waived_steps": [{
        "id": 36, "reason": "Owner accepts this specific library DRC deferral",
        "approver": "reyerchu", "approved_at": "2026-09-28",
        "owner_statement": "I approve this specific tapeout deferral for this run.",
        "ticket": "owner-review-36", "review_required": True,
    }]}))
    _sign_ai_fixture(project, "36")
    return fc._load_waivers(project)


# ---------------------------------------------------------------------------
# Layer 1 — signoff_audit exit code is verdict-tier-aware (#651 acceptance)
# ---------------------------------------------------------------------------
def test_constants_are_distinct_and_documented():
    # rc 3 must not collide with bare-PASS(0), FAIL(1), or vacuous/skip(2).
    assert sa.WAIVER_EXIT_CODE == 3
    assert sa.WAIVER_EXIT_CODE not in (0, 1, 2)
    assert sa.WAIVER_STDOUT_SENTINEL == "PASS_WITH_WAIVERS:"


def test_clean_tapeout_returns_bare_pass_rc0(tmp_path):
    rc = sa.main([str(_clean_project(tmp_path)), "--mode", "tapeout"])
    assert rc == 0  # bare/absolute PASS — unchanged


def test_waived_tapeout_returns_distinct_waiver_rc(tmp_path):
    p = _waiver_project(tmp_path)
    # pre-condition: the auditor itself rates this PASS_WITH_WAIVERS.
    r = sa._check_tapeout(p)
    assert r.passed is True
    assert r.summary["verdict_tier"] == "PASS_WITH_WAIVERS"
    # the WHOLE point of #651: distinct rc, NOT 0.
    rc = sa.main([str(p), "--mode", "tapeout"])
    assert rc == sa.WAIVER_EXIT_CODE
    assert rc != 0


def test_real_fail_still_returns_rc1(tmp_path):
    rc = sa.main([str(_fail_project(tmp_path)), "--mode", "tapeout"])
    assert rc == 1  # NO-LEAK: a real FAIL still FAILs (never 0, never 3)


def test_waiver_run_prints_sentinel_line(tmp_path):
    proc = _run_signoff(_waiver_project(tmp_path))
    assert proc.returncode == sa.WAIVER_EXIT_CODE
    first = proc.stdout.lstrip().splitlines()[0]
    assert first.startswith(sa.WAIVER_STDOUT_SENTINEL)


def test_clean_run_does_not_print_waiver_sentinel(tmp_path):
    proc = _run_signoff(_clean_project(tmp_path))
    assert proc.returncode == 0
    assert sa.WAIVER_STDOUT_SENTINEL not in proc.stdout


# ---------------------------------------------------------------------------
# Layer 1b — waivers.json step entry is emitted + schema-valid + idempotent
# ---------------------------------------------------------------------------
def test_waiver_run_discloses_tier_without_issuing_waivers_json(tmp_path):
    p = _waiver_project(tmp_path)
    sa.main([str(p), "--mode", "tapeout"])
    wfile = p / "waivers.json"
    assert not wfile.exists()
    assert sa._check_tapeout(p).summary["verdict_tier"] == "PASS_WITH_WAIVERS"


def test_repeated_waiver_tier_never_creates_approval(tmp_path):
    p = _waiver_project(tmp_path)
    sa.main([str(p), "--mode", "tapeout"])
    sa.main([str(p), "--mode", "tapeout"])
    assert not (p / "waivers.json").exists()
    assert sa._check_tapeout(p).summary["evidence"]["drc"] == "library_internal_waived"


def test_handauthored_waiver_takes_precedence(tmp_path):
    p = _waiver_project(tmp_path)
    import json
    (p / "waivers.json").write_text(json.dumps({"waived_steps": [
        {"id": sa._TAPEOUT_STEP_ID, "reason": "human reviewed this tapeout",
         "approver": "alice", "review_required": True, "ticket": "HUMAN-1"}]}))
    sa.main([str(p), "--mode", "tapeout"])
    data = json.loads((p / "waivers.json").read_text())
    step36 = [w for w in data["waived_steps"]
              if str(w["id"]) == str(sa._TAPEOUT_STEP_ID)]
    assert len(step36) == 1
    assert step36[0]["approver"] == "alice"  # auto-entry did NOT clobber it


# ---------------------------------------------------------------------------
# Layer 2 — the flow gate carries the distinction (rc-3 + sentinel → WAIVED)
# ---------------------------------------------------------------------------
def test_flow_gate_program_exit_zero_three_way(tmp_path):
    cmd = "tapeout_signoff_check . --mode tapeout"
    waiver_p = _waiver_project((tmp_path / "w"))
    clean_p = _clean_project((tmp_path / "c"))
    fail_p = _fail_project((tmp_path / "f"))

    wp, wo = fc._check_program_exit_zero(waiver_p, cmd)
    cp, co = fc._check_program_exit_zero(clean_p, cmd)
    fp, fo = fc._check_program_exit_zero(fail_p, cmd)

    # waiver: passed, and tagged with the waiver hint (→ WAIVED-DEFERRED).
    assert wp is True
    assert wo.startswith(fc._WAIVER_HINT_PREFIX)
    # clean: passed, NO waiver hint (→ bare PASS).
    assert cp is True
    assert not co.startswith(fc._WAIVER_HINT_PREFIX)
    assert not co.startswith(fc._VACUOUS_HINT_PREFIX)
    # fail: not passed.
    assert fp is False
    assert not fo.startswith(fc._WAIVER_HINT_PREFIX)


def test_check_step_three_way_status(tmp_path):
    """Step 36 distinguishes owner waiver, clean PASS, and gate FAIL."""
    step = {"id": 36, "name": "Tapeout checklist", "stage": "stage4",
            "gate": {"program_exit_zero":
                     "tapeout_signoff_check . --mode tapeout"}}
    waiver_p = _signed_waiver_project((tmp_path / "w"))
    owner_waivers = _approve_tapeout(waiver_p)
    clean_p = _clean_project((tmp_path / "c"))
    # The step's evidence now includes its declared checklist, which the gate
    # reads; the clean arm signs its judgement over it as the waiver arm does.
    _sign_ai_fixture(clean_p, "36")
    fail_p = _fail_project((tmp_path / "f"))

    assert fc.check_step(waiver_p, step, waivers=owner_waivers).status == "PASS_WITH_WAIVERS"
    assert fc.check_step(clean_p, step, waivers={}).status == "PASS"
    assert fc.check_step(fail_p, step, waivers={}).status == "FAIL"


def test_tapeout_gate_hint_without_owner_record_cannot_waive_step(tmp_path):
    project = _signed_waiver_project(tmp_path)
    assert not (project / "waivers.json").exists()
    step = {"id": 36, "name": "Tapeout checklist", "stage": "stage4",
            "gate": {"program_exit_zero":
                     "tapeout_signoff_check . --mode tapeout "
                     "--json reports/audit/tapeout_signoff.json"}}
    row = fc.check_step(project, step, waivers={})
    assert row.status == "FAIL", (row.status, row.reasons)
    assert any("owner" in reason.lower() and "PASS_WITH_WAIVERS" in reason
               and "library_internal_waived" in reason
               for reason in row.reasons), row.reasons
    report = json.loads((project / "reports/audit/tapeout_signoff.json").read_text())
    assert report["summary"]["verdict_tier"] == "PASS_WITH_WAIVERS"
    assert report["summary"]["evidence"]["drc"] == "library_internal_waived"


def test_tapeout_gate_hint_with_owner_record_is_a_reviewable_waiver(tmp_path):
    project = _signed_waiver_project(tmp_path)
    waivers = _approve_tapeout(project)
    assert 36 in waivers
    row = fc.check_step(project, {
        "id": 36, "name": "Tapeout checklist", "stage": "stage4",
        "gate": {"program_exit_zero":
                 "tapeout_signoff_check . --mode tapeout"}}, waivers)
    assert row.status == "PASS_WITH_WAIVERS", (row.status, row.reasons)
    assert any("approver: reyerchu" in reason for reason in row.reasons)


def test_waived_status_is_not_bare_pass(tmp_path):
    """The honesty invariant: the waived tapeout step's status is NEVER
    'PASS'. A WAIVED step drives Overall → PASS_WITH_WAIVERS (counts toward
    the WAIVED-DEFERRED bucket, not the executed-PASS bucket)."""
    step = {"id": 36, "name": "Tapeout checklist", "stage": "stage4",
            "gate": {"program_exit_zero":
                     "tapeout_signoff_check . --mode tapeout"}}
    project = _signed_waiver_project((tmp_path / "w"))
    r = fc.check_step(project, step, waivers=_approve_tapeout(project))
    assert r.status != "PASS"
    assert r.status == "PASS_WITH_WAIVERS"
    # The accepted owner record, rather than the gate hint, grants this tier.
    assert any("approver: reyerchu" in reason
               for reason in r.reasons)


# ---------------------------------------------------------------------------
# NO-LEAK — a stray rc 3 WITHOUT the sentinel is NOT silently waived
# ---------------------------------------------------------------------------
def test_bare_rc3_without_sentinel_is_not_waived(tmp_path):
    """A program that merely exits 3 but prints NO PASS_WITH_WAIVERS sentinel
    must FAIL the gate, not be silently promoted to a waiver."""
    prog = tmp_path / "rc3_no_sentinel.py"
    prog.write_text("import sys\nprint('some unrelated output')\nsys.exit(3)\n")
    passed, out = fc._check_program_exit_zero(
        tmp_path, f"{prog} {tmp_path}")
    assert passed is False
    assert not out.startswith(fc._WAIVER_HINT_PREFIX)


def test_stdout_signals_waiver_helper():
    assert fc._stdout_signals_waiver("PASS_WITH_WAIVERS: blah") is True
    assert fc._stdout_signals_waiver("   PASS_WITH_WAIVERS: indented") is True
    assert fc._stdout_signals_waiver("line1\nPASS_WITH_WAIVERS: x\n") is True
    assert fc._stdout_signals_waiver("PASS\nno waiver here") is False
    assert fc._stdout_signals_waiver("") is False
