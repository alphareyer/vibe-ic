"""ORGANIC #428 [reporting honesty] — `reports/final_summary.md` carried TWO
verdict roll-ups over the SAME step universe and they disagreed on the
BLOCKING-FAILURE count, with nothing marking either as counting a
different thing.

MEASURED (real repo artefact — `benchmark-data/ic/spm/v1.5.65_sky130A`,
spm x sky130A):

  quoted flow_compliance_check tally  PASS=35 FAIL=0 MISSING=0 WAIVED-DEFERRED=3
  `### Verdict roll-up` table         PASS=31          MISSING=5 WAIVED-DEFERRED=2

`reports/audit/phase23_completion_audit.json[step_counts]` agrees with the
tally exactly. Both roll-ups print `Total 63`, the deltas cancel, and each
is internally plausible on its own — which is why neither looks wrong.

ROOT CAUSE (not two questions — one parse gap):
`_parse_verdicts` enumerated only the `<n>` / `A<n>` / `M<n>` / `P0`
step-id shapes, so the flow's other lettered ids matched NOTHING and
`.get(sid, "FAIL")` booked each of them as the compliance verdict
MISSING. On a live `--strict` audit of that same cell the five
unreadable ids and their REAL verdicts were:

    D1 -> VACUOUS-PASS, FS1 -> PASS, DT1 -> PASS,
    DT2 -> SKIPPED-CONDITION, DT3 -> PASS

i.e. 5 steps moved out of PASS / VACUOUS-PASS / SKIPPED-CONDITION and
into MISSING. Net zero, total preserved, FAIL count silently wrong.

WHAT LANDED
  1. Generic step-id matching, so an id shape added to the flow tomorrow
     is READ, not silently reclassified.
  2. A NAMED `NO-VERDICT-IN-AUDIT` bucket. "The renderer could not read a
     verdict" and "a required output is absent" are different questions
     and no longer share a bucket — so a parse gap can never again wear
     MISSING's name.
  3. The roll-up prints EVERY populated bucket (the old fixed 6-tuple
     dropped DEFERRED-BY-UPSTREAM / SKIPPED-SETUP-REQUIRED, so the rows
     stopped summing to the Total printed beneath them).
  4. In-render reconciliation against the checker's own tally, read out
     of the SAME audit text — disagreement is NAMED per bucket at the top
     of the report, never resolved by adjusting a count.
  5. `final_summary_rollup_consistency_check.py` (repo mode wired into
     tools/ci/repo_hygiene_gates.sh).

BIDIRECTIONAL, stated so it cannot become a rubber stamp: with the
renderer's counts differing from the checker tally the gate MUST FAIL;
with them equal it MUST PASS. Both directions are asserted below, and the
FAIL direction is anchored on the real archived artefact.

chip-AGNOSTIC: step ids, verdict buckets and markdown structure only.
"""
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PLUGIN / "programs"))
import final_report_generate as F  # noqa: E402
import final_summary_rollup_consistency_check as C  # noqa: E402

REPO_ROOT = PLUGIN.parent.parent.parent
# The real artefact the issue was measured on.
REPRO_CELL = REPO_ROOT / "benchmark-data" / "ic" / "spm" / "v1.5.65_sky130A"

# The exact step-id alternation shipped before this fix, kept so the test
# states the defect rather than merely asserting the cure.
PRE_FIX_STEP_ID_RE = r"[0-9]+|[AM][0-9]+|P0"


# ─── helpers: audit text in the exact shape flow_compliance_check prints ──

def _verdict_line(sid, label):
    return f"  x [{label:<17}] Step {str(sid):>2}: some step  (stage3)"


def _audit_text(verdicts, tally=None):
    """`verdicts` = {step_id: label}. `tally` = {tally-label: n} or None."""
    lines = ["", "=== Vibe-IC phase1_phase2_phase3 compliance ===",
             "Project: /tmp/p", "Flow def: /tmp/f.yaml",
             f"Steps: {len(verdicts)} total (0/0 executed PASS, 0 DEFERRED via waiver)"]
    if tally is not None:
        lines.append("  " + "  ".join(f"{k}={v}" for k, v in tally.items()))
    lines += [_verdict_line(sid, lab) for sid, lab in verdicts.items()]
    lines.append("Overall: PASS  (strict=True)")
    return "\n".join(lines)


def _flow(step_ids, stage="stage3"):
    return {"steps": [{"id": s, "name": f"step {s}", "stage": stage}
                      for s in step_ids]}


def _real_flow():
    import yaml
    return yaml.safe_load((PLUGIN / "flow" / "phase1_phase2_phase3.yaml")
                          .read_text(encoding="utf-8"))


# ─── 1. the parse gap itself ─────────────────────────────────────────────

def test_every_declared_flow_step_id_is_readable():
    """POSITIVE: the live parser reads a verdict line for EVERY step id
    the real flow declares."""
    ids = [str(s["id"]) for s in _real_flow()["steps"]]
    assert ids, "flow declares no steps"
    parsed = F._parse_verdicts(_audit_text({i: "PASS" for i in ids}))
    assert set(parsed) == set(ids)


def test_pre_fix_alternation_missed_lettered_ids_that_the_flow_declares():
    """NEGATIVE / defect statement: the shipped-before alternation could
    not read the lettered ids the flow actually declares, and there is at
    least one — so this is a live gap, not a hypothetical one."""
    import re
    ids = [str(s["id"]) for s in _real_flow()["steps"]]
    old = re.compile(r"^(?:" + PRE_FIX_STEP_ID_RE + r")$")
    unreadable_before = [i for i in ids if not old.match(i)]
    assert unreadable_before, (
        "the flow no longer declares any id the pre-fix pattern missed; "
        "this test's premise must be re-derived, not deleted")
    # And every one of them is readable NOW.
    new = re.compile(r"^(?:" + F.STEP_ID_RE + r")$")
    assert all(new.match(i) for i in unreadable_before)


@pytest.mark.parametrize("sid", ["1", "44", "A1", "M4", "P0", "D1", "FS1",
                                 "DT1", "DT2", "DT3"])
def test_parse_verdicts_reads_each_id_shape(sid):
    assert F._parse_verdicts(_audit_text({sid: "FAIL"})) == {sid: "FAIL"}


# ─── 2. unreadable != MISSING ────────────────────────────────────────────

def test_unreadable_step_lands_in_named_bucket_not_missing():
    """NEGATIVE control for the exact defect: a step with NO verdict line
    must NOT be counted as the compliance verdict MISSING."""
    flow = _flow(["1", "2"])
    rollup, total = F._verdict_rollup(flow, {"1": "PASS"})
    assert total == 2
    assert rollup.get("FAIL", 0) == 0, (
        "an unreadable verdict was booked as the blocking-artefact bucket")
    assert rollup[F.NO_VERDICT] == 1


def test_real_missing_verdict_still_lands_in_missing():
    """POSITIVE pair: a genuine MISSING verdict line is still MISSING —
    the fix must not empty the bucket it stopped over-filling."""
    flow = _flow(["1", "2"])
    rollup, _ = F._verdict_rollup(flow, {"1": "PASS", "2": "FAIL"})
    assert rollup["FAIL"] == 1
    assert F.NO_VERDICT not in rollup


def test_counts_snapshot_separates_no_verdict_from_a_failed_step():
    """R-0915-85 — the pair this test exists for is UNCHANGED and one of its
    two words moved. `no_verdict` is "the report carries no row for this step";
    a step that HAS a row saying its declared output is absent is
    `FAIL(missing_artefact)`. The `missing` snapshot key is kept at 0 so a
    reader diffing an old published summary sees a number, not a hole."""
    flow = _flow(["1", "2", "3"])
    rollup, total = F._verdict_rollup(flow, {"1": "FAIL"})
    snap = F._counts_snapshot(rollup, total, flow=flow,
                              verdicts={"1": "FAIL"})
    assert snap["fail"] == 1
    assert snap["missing"] == 0
    assert snap["no_verdict"] == 2


# ─── 3. the checker tally reader ─────────────────────────────────────────

def test_parse_audit_tally_reads_the_quoted_line():
    # R-0915-85 — the tally line in the five, with the ONE report-side label
    # the parser still resolves: `WAIVED-DEFERRED` is the sentence four parsers
    # key on and it resolves to the bucket `PASS_WITH_WAIVERS`.
    text = _audit_text({"1": "PASS"},
                       tally={"PASS": 35, "FAIL": 0,
                              "PASS_WITH_WAIVERS": 3, "WAIVED-DEFERRED": 3,
                              "NOT_APPLICABLE": 22, "NOT_MEASURED": 3})
    assert F._parse_audit_tally(text) == {
        "PASS": 35, "FAIL": 0, "PASS_WITH_WAIVERS": 3,
        "NOT_APPLICABLE": 22, "NOT_MEASURED": 3}


def test_parse_audit_tally_returns_none_when_absent():
    """An audit that produced no tally must not be reported as agreeing."""
    assert F._parse_audit_tally("Overall: AUDIT_TIMEOUT\n(did not finish)") is None


def test_parse_audit_tally_ignores_the_reports_own_prose_bullet():
    """The report restates its own counts as a prose bullet. If the tally
    reader matched THAT, reconciliation would compare the roll-up against
    a restatement of itself and agree by construction — the one outcome a
    consistency check must never be able to produce.

    BOTH spellings are pinned. 2026-07-28, adversarial finding (LOW): the
    bullet was rewritten when VACUOUS-PASS left the numerator, and this guard
    kept feeding the RETIRED string — so it went on green-lighting a format
    the renderer no longer emits while the LIVE one went unexercised. A guard
    against a self-referential match has to be pointed at the string the
    program actually produces; the historical form is kept beside it because
    an old report is still a real input to this reader.
    """
    # The LIVE bullet — `final_report_generate` ~1607, verified against a
    # rendered report rather than transcribed from the source.
    assert F._parse_audit_tally(
        "- PASS=1 → executed PASS=1 — every canonical step that MEASURED "
        "something passed deterministically. VACUOUS-PASS=1 is NOT included: "
        "those gates ran and found no input to audit.\n"
        "- WAIVED-DEFERRED=2 — deferred via documented waiver.") is None
    # The RETIRED bullet, as emitted before VACUOUS-PASS left the numerator.
    assert F._parse_audit_tally(
        "- PASS=31 (+VACUOUS-PASS=3 → executed PASS=34) — all passed.\n"
        "- WAIVED-DEFERRED=2 — deferred via documented waiver.") is None


def test_parse_audit_tally_survives_the_blocked_by_upstream_parenthetical():
    """The producer annotates a bucket in parentheses; the parser reads the
    number, not the sentence. R-0915-85 — the annotated bucket is `FAIL`, whose
    reason says `missing_artefact`; the old `MISSING=` label is gone with the
    word."""
    text = ("  PASS=1  PASS_WITH_WAIVERS=0  "
            "FAIL=2 (1 blocked-by-upstream of step 7)  "
            "NOT_MEASURED=0  NOT_APPLICABLE=0")
    assert F._parse_audit_tally(text)["FAIL"] == 2


# ─── 4. reconciliation: both directions ──────────────────────────────────

def test_reconcile_silent_when_they_agree():
    assert F._reconcile_rollup({"PASS": 3, "FAIL": 1},
                               {"PASS": 3, "FAIL": 1}) == {}


def test_reconcile_names_every_disagreeing_bucket():
    diff = F._reconcile_rollup(
        {"PASS": 28, "FAIL": 3, "NOT_MEASURED": 6},
        {"PASS": 30, "FAIL": 4, "NOT_MEASURED": 1})
    assert diff == {"PASS": (28, 30), "FAIL": (3, 4), "NOT_MEASURED": (6, 1)}


def test_reconcile_flags_a_bucket_the_tally_never_names():
    diff = F._reconcile_rollup({"PASS": 3, "FAIL": 2}, {"PASS": 3})
    assert diff["FAIL"] == (2, 0)


def test_reconcile_reports_nothing_when_there_is_no_tally():
    """Absence of a tally is not agreement — the caller must distinguish
    the two, so `_reconcile_rollup` may not manufacture a clean verdict."""
    assert F._reconcile_rollup({"PASS": 3}, None) == {}


# ─── 5. the roll-up must not drop a populated bucket ─────────────────────

def test_rollup_order_covers_every_bucket_the_checker_can_emit():
    emitted = set(F._TALLY_LABEL_TO_BUCKET.values()) | {F.NO_VERDICT}
    assert emitted <= set(F.ROLLUP_ORDER), (
        f"buckets with no print slot: {sorted(emitted - set(F.ROLLUP_ORDER))}")


def _rendered_stage_step_ids(n):
    """`n` step ids from ONE stage the Stage-breakdown table actually renders.

    Taken from `F.STAGE_TITLE`, not from the head of the flow: the flow's first
    two steps are `stage_phase1`, which that table has no row for, so marking
    "the first n steps" marks steps the table never prints and the assertion
    below would fail for a reason that is not the one under test.
    """
    steps = _real_flow()["steps"]
    for stage, _title in F.STAGE_TITLE:
        ids = [str(s["id"]) for s in steps if s.get("stage") == stage]
        if len(ids) >= n:
            return ids[:n]
    raise AssertionError(f"no rendered stage carries {n} steps")


def _audit_for_real_flow_with(word, marked_ids):
    """Verdicts for every real flow step, with `marked_ids` wearing `word` and
    the rest PASS, and a tally that stays TRUE to the per-step lines."""
    steps = _real_flow()["steps"]
    marked = set(marked_ids)
    labels = {str(s["id"]): (word if str(s["id"]) in marked else "PASS")
              for s in steps}
    tally = {"PASS": sum(1 for v in labels.values() if v == "PASS"),
             "FAIL": 0, "PASS_WITH_WAIVERS": 0,
             "NOT_APPLICABLE": 0, "NOT_MEASURED": 0}
    tally[word] = sum(1 for v in labels.values() if v == word)
    return _audit_text(labels, tally=tally)


def _stage_breakdown_counts(md, word):
    """How many steps the Stage-breakdown table's `Other` column attributes to
    `word`, summed over its rows. That table is the second of the renderer's
    two ROLLUP_ORDER walks and the one with NO fallback branch."""
    sym = F.VERDICT_SYM.get(word, word)
    body = md.split("## Stage breakdown", 1)[1].split("\n## ", 1)[0]
    total = 0
    for ln in body.splitlines():
        if not ln.startswith("| ") or "---" in ln:
            continue
        for bit in ln.rsplit("|", 2)[1].split():
            k, _, v = bit.partition("=")
            if k == sym and v.isdigit():
                total += int(v)
    return total


def test_a_populated_not_measured_bucket_reaches_the_stage_breakdown(
        monkeypatch, tmp_path):
    """A PRINT SLOT IS THE POINT, NOT MEMBERSHIP OF A TUPLE.

    The test above asks whether the word is IN `ROLLUP_ORDER`. That is the
    necessary half. This is the sufficient half, and it is a different loop:
    the Verdict roll-up table appends an unknown bucket after everything else,
    but the **Stage-breakdown** table walks `ROLLUP_ORDER` and has no fallback
    at all, so a populated bucket with no slot there is not mis-ordered — it is
    ABSENT, and the stage rows stop accounting for the steps they count.

    MEASURED on 0fe74ebc325a (tree 98f2cf217d76), the tree that shipped
    `NOT-MEASURED` in `verdict.PRODUCER_STATUSES` and not in
    `ROLLUP_ORDER`: this render attributed 0 of its 3 NOT-MEASURED steps to any
    stage row.
    """
    marked = _rendered_stage_step_ids(3)
    md = _render_with(monkeypatch, tmp_path,
                      _audit_for_real_flow_with("NOT_MEASURED", marked))
    assert C.parse_rollup_table(md).get("NOT_MEASURED") == 3
    assert _stage_breakdown_counts(md, "NOT_MEASURED") == 3, (
        "3 steps wear NOT-MEASURED and the Stage-breakdown table attributes "
        "none of them to it; that loop walks ROLLUP_ORDER and has no fallback")


def test_the_not_measured_slot_sits_with_the_non_green_words():
    """WHERE the slot is, asserted against the classification rather than
    against a remembered index — AND THE ANSWER MOVED.

    Under `_flow_verdict_tiers`, `NOT-MEASURED` was in neither `EXCUSED` nor
    `NON_GREEN`, so by that module's derivation it was a QUALIFIED DONE-CLAIM
    and this test demanded a slot ABOVE the non-green run: printing it beside
    FAIL would have said, in the one table a reader opens, that the step
    failed.

    R-0915-85 decided the opposite, deliberately: a step nobody measured is not
    a claim that it is done. `NOT_MEASURED` IS in `NON_GREEN` now, so the slot
    belongs there, and printing it beside FAIL says what the word adjudicates
    instead of contradicting it. The test still reads the classification rather
    than an index, so it moves with the module and not with anyone's memory.
    """
    import verdict as T
    assert not T.is_qualified_done("NOT_MEASURED")
    assert T.is_non_green("NOT_MEASURED")
    order = list(F.ROLLUP_ORDER)
    excused = [order.index(w) for w in T.EXCUSED if w in order]
    # Excused first, then the non-green run; NOT_MEASURED is in the latter.
    assert order.index("NOT_MEASURED") > max(excused)
    # And it sits ABOVE `FAIL`: a hole is not a measured defect, and the
    # ladder reads worst-last.
    assert order.index("NOT_MEASURED") < order.index("FAIL")


# ─── 6. end-to-end render: the two roll-ups in one document ──────────────

def _render_with(monkeypatch, tmp_path, audit_text):
    # #1969: the renderer no longer re-parses human stdout.  Model what the
    # real checker does by writing its canonical snapshot during `_run_audit`.
    # When verdict lines were withheld, `step_counts` stays complete while
    # `steps[]` is incomplete — an internally torn artifact, which is now the
    # only condition the reconciliation banner is allowed to name.
    tally = F._parse_audit_tally(audit_text)
    parsed = F._parse_verdicts(audit_text)

    def _fake_run(*_args, **_kwargs):
        if tally is not None:
            p = (tmp_path / "reports" / "audit" /
                 "phase23_completion_audit.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "run_status": "PASS",
                "step_counts": tally,
                "steps": [
                    {"id": sid, "status": status,
                     "name": f"step {sid}", "stage": "stage1"}
                    for sid, status in parsed.items()
                ],
            }), encoding="utf-8")
        return audit_text, "PASS"

    monkeypatch.setattr(F, "_run_audit", _fake_run)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    return F._render(tmp_path, run_audit=True)


def _full_audit_for_real_flow(drop_ids=()):
    """Verdicts for every real flow step, with a tally that stays TRUE to
    the per-step lines. `drop_ids` removes verdict LINES only — the tally
    still counts those steps, which is exactly the pre-fix situation."""
    steps = _real_flow()["steps"]
    labels = {}
    for i, s in enumerate(steps):
        labels[str(s["id"])] = ("FAIL" if i % 17 == 0 else
                                "NOT_APPLICABLE" if i % 5 == 0 else "PASS")
    tally = {"PASS": sum(1 for v in labels.values() if v == "PASS"),
             "FAIL": sum(1 for v in labels.values() if v == "FAIL"),
             "PASS_WITH_WAIVERS": 0,
             "NOT_MEASURED": 0,
             "NOT_APPLICABLE": sum(1 for v in labels.values()
                                   if v == "NOT_APPLICABLE")}
    emitted = {k: v for k, v in labels.items() if k not in drop_ids}
    return _audit_text(emitted, tally=tally)


def test_rendered_report_has_one_consistent_rollup(monkeypatch, tmp_path):
    """POSITIVE: every step readable -> the rendered roll-up table equals
    the checker tally quoted in the same file, no reconciliation warning,
    and the standalone gate PASSes the run dir."""
    md = _render_with(monkeypatch, tmp_path, _full_audit_for_real_flow())
    assert C.RECONCILIATION_FAILED_MARKER not in md
    (tmp_path / "reports" / "final_summary.md").write_text(md, encoding="utf-8")
    ok, notes = C.check_project(tmp_path)
    assert ok, "\n".join(notes)
    table = C.parse_rollup_table(md)
    # Zero-count buckets are printed by the tally and omitted by the
    # table, so compare bucket-by-bucket with an implicit 0 — the same
    # comparison the gate makes.
    assert C.compare_buckets(table, F._parse_audit_tally(md)) == {}
    assert sum(table.values()) == C.parse_rollup_total(md)


def test_rendered_report_names_the_disagreement_when_verdicts_are_unreadable(
        monkeypatch, tmp_path):
    """NEGATIVE: a JSON snapshot missing five step rows is torn.

    The producer-owned `step_counts` remains the rendered global roll-up; the
    absent rows affect only the per-step view and fire the tripwire.
    """
    dropped = ("D1", "FS1", "DT1", "DT2", "DT3")
    md = _render_with(monkeypatch, tmp_path,
                      _full_audit_for_real_flow(drop_ids=dropped))
    assert C.RECONCILIATION_FAILED_MARKER in md
    table = C.parse_rollup_table(md)
    assert table.get(F.NO_VERDICT, 0) == 0
    # R-0915-85 — the property is unchanged and is now stated over the bucket
    # that survived: the dropped per-step rows must not move the
    # producer-owned global tally, so the rendered FAIL equals the tally's.
    assert table.get("FAIL", 0) == F._parse_audit_tally(
        _full_audit_for_real_flow(drop_ids=dropped))["FAIL"], (
        "missing per-step rows changed the producer-owned global tally")
    (tmp_path / "reports" / "final_summary.md").write_text(md, encoding="utf-8")
    ok, notes = C.check_project(tmp_path)
    assert not ok
    assert any("reconciliation" in n.lower() for n in notes)


def test_rendered_report_says_so_when_there_is_no_tally_to_check(
        monkeypatch, tmp_path):
    md = _render_with(monkeypatch, tmp_path,
                      "Overall: AUDIT_TIMEOUT\n(did not finish)")
    assert "Roll-up reconciliation: not possible" in md
    (tmp_path / "reports" / "final_summary.md").write_text(md, encoding="utf-8")
    ok, _ = C.check_project(tmp_path)
    assert not ok, "an uncheckable report must not report as verified"


# ─── 7. the gate, repo mode ──────────────────────────────────────────────

def test_gate_repo_mode_passes_on_the_real_flow():
    ok, notes = C.check_repo(PLUGIN / "flow" / "phase1_phase2_phase3.yaml")
    assert ok, "\n".join(notes)


def test_gate_repo_mode_fails_on_an_unreadable_id_shape(tmp_path):
    """The gate must fire when a step id the parser cannot read is added —
    which is the moment the two roll-ups start to diverge."""
    f = tmp_path / "flow.yaml"
    f.write_text("steps:\n  - id: '1'\n  - id: 'STEP-X'\n", encoding="utf-8")
    ok, notes = C.check_repo(f)
    assert not ok
    assert any("STEP-X" in n for n in notes)


def test_gate_repo_mode_cli_exit_codes(tmp_path):
    assert C.main([]) == 0
    f = tmp_path / "flow.yaml"
    f.write_text("steps:\n  - id: 'ZZ-1'\n", encoding="utf-8")
    assert C.main(["--flow", str(f)]) == 1


# ─── 8. real-artefact anchor ─────────────────────────────────────────────

@pytest.mark.skipif(not (REPRO_CELL / "reports" / "final_summary.md").is_file(),
                    reason="archived reproduction cell not present")
def test_gate_fires_on_the_archived_repro_cell():
    """The gate must fire on the artefact the issue was measured on. A
    check that is quiet everywhere is not measuring anything."""
    ok, notes = C.check_project(REPRO_CELL)
    assert not ok
    blob = "\n".join(notes)
    assert "MISSING: table=5 checker=0" in blob
    assert "PASS: table=31 checker=35" in blob


@pytest.mark.skipif(
    not (REPRO_CELL / "reports" / "audit" /
         "phase23_completion_audit.json").is_file(),
    reason="archived reproduction cell not present")
def test_archived_audit_json_agrees_with_the_quoted_tally_not_the_table():
    """States plainly WHICH roll-up was right: the audit JSON and the
    quoted checker tally are the same measurement; the recomputed table is
    the outlier."""
    cell = REPRO_CELL / "reports"
    md = (cell / "final_summary.md").read_text(encoding="utf-8", errors="replace")
    raw = json.loads((cell / "audit" / "phase23_completion_audit.json")
                     .read_text(encoding="utf-8", errors="replace"))["step_counts"]
    js = {C._AUDIT_JSON_KEY_TO_BUCKET[k]: v for k, v in raw.items()
          if k in C._AUDIT_JSON_KEY_TO_BUCKET}
    tally = F._parse_audit_tally(md)
    # The audit JSON and the tally quoted in the summary are the SAME
    # measurement, bucket for bucket …
    assert C.compare_buckets(js, tally) == {}
    # … and the recomputed table is the one that disagrees with both.
    assert C.compare_buckets(C.parse_rollup_table(md), tally) != {}
