"""R-0915-86 (1) — the shared table reader and the direction rule.

`_step_verdict_table` is the ONE place `real_ic_gate` and `audit_replay` turn a
compliance artefact into a per-step table and decide which way a step moved.
Two properties have to hold or both programs lie in the same way:

  1. BOTH artefact shapes read as the SAME table. `flow_compliance_check` writes
     `verdict`/`step_counts`/`command_argv` into
     `reports/audit/phase23_completion_audit.json` and `overall`/`counts` into
     the path given to `--json`, with an element-for-element identical `steps[]`
     (MEASURED 2026-09-16 on the frozen subservient_r26 snapshot: 69 steps,
     identical 18-key records, identical counts). A reader that knew one shape
     would read the other as a table with no verdict — i.e. would rank it
     NON_GREEN and report a catastrophic regression that never happened.

  2. The direction rule adds NO vocabulary. It is `_flow_verdict_tiers`'
     classification, which adjudicates BY SUBTRACTION so a word invented
     tomorrow lands somewhere without anyone remembering to come here. The
     load-bearing test below plants a word registered NOWHERE and requires a
     direction for it.

CALIBRATION (R-0915-86 (3)) is the pair of tests at the end: the differ must
FIRE on a planted flip and stay SILENT on an identical table. An instrument that
has not been shown to do both may not judge.
"""
from __future__ import annotations

import importlib

S = importlib.import_module("_step_verdict_table")
T = importlib.import_module("_flow_verdict_tiers")


def _audit(steps, **kw):
    doc = {"steps": [{"id": i, "name": "step %s" % i, "stage": "stage1",
                      "status": s} for i, s in steps]}
    doc.update(kw)
    return doc


# ── 1. the two artefact shapes are one table ─────────────────────────────────

def test_completion_audit_and_json_report_read_as_the_same_table():
    """The measured property: only the two top-level key names differ."""
    steps = [("2", "PASS"), ("7", "FAIL"), ("15", "SKIPPED-CONDITION")]
    a = S.table_from_audit(_audit(steps, verdict="FAIL",
                                  step_counts={"PASS": 1},
                                  command_argv=["flow_compliance_check.py",
                                                "/p", "--strict"]))
    b = S.table_from_audit(_audit(steps, overall="FAIL", counts={"PASS": 1}))
    assert a["steps"] == b["steps"]
    assert a["verdict"] == b["verdict"] == "FAIL"
    assert a["verdict_key"] == "verdict" and b["verdict_key"] == "overall"


def test_a_document_with_no_steps_list_is_unreadable_not_empty():
    """An empty table diffs as 'every step removed'. That fabricated
    catastrophe is exactly what a lenient reader would print."""
    try:
        S.table_from_audit({"verdict": "PASS"})
    except S.TableUnreadable as exc:
        assert "no `steps` list" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("a document with no steps[] must be UNREADABLE")


def test_a_refusal_is_ranked_below_a_judgement():
    """`INSUFFICIENT_DATA` means the audit measured nothing. Ranking it as a
    done-claim (which is what `_flow_verdict_tiers` does with any unregistered
    word, correctly, for STEPS) would let a run that refuses read as an
    improvement over a run that judged. The artefact's OWN
    `verdict_refusal_reason` field is what distinguishes them."""
    t = S.table_from_audit(_audit([("2", "PASS")], verdict="INSUFFICIENT_DATA",
                                  verdict_refusal_reason="0 of 246 gates"))
    assert t["verdict_rank"] == 0


# ── 2. the direction rule is derived, not enumerated ─────────────────────────

def test_an_unregistered_word_still_gets_a_direction():
    """THE LOAD-BEARING PROPERTY. A word in neither EXCUSED nor NON_GREEN is a
    done-claim by subtraction, so it ranks 2 and PASS -> it is a REGRESSION —
    without this module carrying a list anybody has to remember to extend."""
    word = "A-TIER-INVENTED-TOMORROW"
    assert word not in T.PRODUCER_STATUSES
    assert S.rank(word) == 2
    assert S.direction("PASS", word) == S.REGRESSION
    assert S.direction("FAIL", word) == S.IMPROVEMENT


def test_pass_to_skip_is_a_regression_not_a_lateral():
    """The SKIP laundering R-0915-85 names by name: a step that used to run and
    pass, now not run at all. EXCUSED ranks BELOW a qualified done-claim on
    purpose — ranking 'not run' above 'ran and measured little' would make
    laundering the cheapest route to a green table."""
    assert S.direction("PASS", "SKIPPED-CONDITION") == S.REGRESSION
    assert S.rank("SKIPPED-CONDITION") < S.rank("INCOMPLETE")


def test_a_rename_at_the_same_rank_is_lateral():
    """R-0915-85 is deleting 23 words down to 5 AT THE PRODUCERS. The gate's job
    during that reform is to tell a rename from a judgement change."""
    assert S.direction("VACUOUS_PASS", "PARTIALLY-VACUOUS") == S.LATERAL
    assert S.direction("VACUOUS_PASS", "VACUOUS-PASS") == S.UNCHANGED


# ── comparability: the refusal that keeps 69-vs-9 off the page ───────────────

def test_a_different_scope_is_not_comparable():
    """MEASURED: both frozen snapshots' recorded audits were written by a
    STAGE-SCOPED invocation (9 steps) while a full --strict pass yields 69.
    Diffing them prints 60 REMOVED steps and reads as a catastrophe."""
    ref = S.table_from_audit(_audit([("2", "PASS")], verdict="FAIL",
                                    command_argv=["stage4_compliance.py", ".",
                                                  "--exclude-step", "39"]))
    cur = S.table_from_audit(_audit([("2", "PASS")], verdict="FAIL",
                                    command_argv=["flow_compliance_check.py",
                                                  "/p", "--strict"]))
    ok, why = S.comparability(ref, cur)
    assert ok is False and "different producer" in why
    assert S.diff_tables(ref, cur)["comparable"] is False


def test_the_same_program_at_a_different_scope_is_not_comparable_either():
    ref = S.table_from_audit(_audit([("2", "PASS")], verdict="PASS",
                                    command_argv=["flow_compliance_check.py",
                                                  "/p", "--stage-id",
                                                  "stage_analog", "--strict"]))
    cur = S.table_from_audit(_audit([("2", "PASS")], verdict="PASS",
                                    command_argv=["flow_compliance_check.py",
                                                  "/q", "--strict"]))
    ok, why = S.comparability(ref, cur)
    assert ok is False and "different scope" in why


def test_a_different_project_path_is_still_comparable():
    """The subject is held fixed by the caller; the project path is expected to
    differ (a replay works on a copy). Refusing on it would refuse everything."""
    ref = S.table_from_audit(_audit([("2", "PASS")], verdict="PASS",
                                    command_argv=["flow_compliance_check.py",
                                                  "/a/spm", "--strict"]))
    cur = S.table_from_audit(_audit([("2", "PASS")], verdict="PASS",
                                    command_argv=["flow_compliance_check.py",
                                                  "/b/spm", "--strict"]))
    assert S.comparability(ref, cur)[0] is True


def test_a_table_with_no_recorded_argv_is_refused():
    """An unknown scope is not a matching scope. `--json` reports carry no
    `command_argv`, so one used as a reference must refuse rather than be
    assumed full-scope."""
    ref = S.table_from_audit(_audit([("2", "PASS")], overall="PASS"))
    cur = S.table_from_audit(_audit([("2", "PASS")], verdict="PASS",
                                    command_argv=["flow_compliance_check.py",
                                                  "/p", "--strict"]))
    ok, why = S.comparability(ref, cur)
    assert ok is False and "no `command_argv`" in why


# ── CALIBRATION (R-0915-86 (3)): fires on a positive, silent on a negative ───

_ARGV = ["flow_compliance_check.py", "/p", "--strict"]


def _pair(ref_steps, cur_steps, ref_v="PASS_WITH_WAIVERS",
          cur_v="PASS_WITH_WAIVERS"):
    return (S.table_from_audit(_audit(ref_steps, verdict=ref_v,
                                      command_argv=_ARGV)),
            S.table_from_audit(_audit(cur_steps, verdict=cur_v,
                                      command_argv=_ARGV)))


def test_KNOWN_NEGATIVE_an_identical_table_reports_nothing():
    steps = [("2", "PASS"), ("7", "PASS"), ("15", "SKIPPED-CONDITION")]
    d = S.diff_tables(*_pair(steps, steps))
    assert d["comparable"] is True
    assert d["regressed"] is False
    assert d["changed"] == [] and d["added"] == [] and d["removed"] == []
    assert d["unchanged_step_count"] == 3


def test_KNOWN_POSITIVE_one_flipped_step_is_reported_and_named():
    ref = [("2", "PASS"), ("7", "PASS"), ("15", "SKIPPED-CONDITION")]
    cur = [("2", "PASS"), ("7", "FAIL"), ("15", "SKIPPED-CONDITION")]
    d = S.diff_tables(*_pair(ref, cur))
    assert d["regressed"] is True
    assert [e["id"] for e in d["regressions"]] == ["7"]
    assert d["regressions"][0]["reference"] == "PASS"
    assert d["regressions"][0]["current"] == "FAIL"


def test_an_improvement_is_listed_and_is_not_a_regression():
    ref = [("2", "FAIL")]
    cur = [("2", "PASS")]
    d = S.diff_tables(*_pair(ref, cur))
    assert d["regressed"] is False
    assert [e["id"] for e in d["improvements"]] == ["2"]


def test_a_step_the_reference_measured_and_this_table_does_not_is_a_regression():
    """The laundering shape seen from the other side: drop the step and the
    table gets greener. Held as a regression when the reference's word was a
    done-claim."""
    d = S.diff_tables(*_pair([("2", "PASS"), ("7", "PASS")], [("2", "PASS")]))
    assert d["regressed"] is True
    assert [e["id"] for e in d["removed"]] == ["7"]
    assert d["removed"][0]["direction"] == S.REGRESSION


def test_a_removed_step_that_was_already_non_green_is_not_a_regression():
    """Direction, not volume: losing a step that was FAILing is not the table
    getting worse, and calling it one would make the gate cry wolf on every
    flow-yaml edit that retires a red step."""
    d = S.diff_tables(*_pair([("2", "PASS"), ("7", "FAIL")], [("2", "PASS")]))
    assert d["regressed"] is False
    assert d["removed"][0]["direction"] == S.LATERAL


def test_a_new_step_that_arrives_red_is_a_regression():
    d = S.diff_tables(*_pair([("2", "PASS")], [("2", "PASS"), ("9", "FAIL")]))
    assert d["regressed"] is True
    assert [e["id"] for e in d["added"]] == ["9"]


def test_the_top_level_verdict_falling_is_a_regression_on_its_own():
    steps = [("2", "PASS")]
    d = S.diff_tables(*_pair(steps, steps, ref_v="PASS_WITH_WAIVERS",
                             cur_v="FAIL"))
    assert d["verdict"]["direction"] == S.REGRESSION
    assert d["regressed"] is True


def test_the_top_level_verdict_rising_is_not_a_regression():
    """A landing that makes the IC better must not be blocked by the gate that
    watches it. The caller flags the reference as stale instead."""
    steps = [("2", "PASS")]
    d = S.diff_tables(*_pair(steps, steps, ref_v="FAIL", cur_v="PASS"))
    assert d["verdict"]["direction"] == S.IMPROVEMENT
    assert d["regressed"] is False


# ── the chip-AGNOSTIC declaration these three files make, ENFORCED ──────────
#
# `source_chip_agnostic_check` READS a `CHIP_AGNOSTIC:` declaration and
# DISCLOSES it — its own docstring says so in as many words: "It does NOT
# enforce it: the program's own test is the lane that can refuse, and
# duplicating the refusal here would be two lanes with one rule between them."
# All three files of this change declare `strict-logic`, so this is that lane.
# It covers all three together because they land as one change and the middle
# one is the module the other two import.

_THIS_CHANGE = ("_step_verdict_table.py", "real_ic_gate.py", "audit_replay.py")


def _module_logic(path):
    """The file with its MODULE docstring stripped and nothing else — the exact
    scope `strict-logic` names. Every nested docstring and comment STAYS, which
    is where a literal would really hide."""
    import ast
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    body = tree.body[1:] if (tree.body and isinstance(tree.body[0], ast.Expr)
                             and isinstance(tree.body[0].value, ast.Constant)
                             and isinstance(tree.body[0].value.value, str)
                             ) else tree.body
    return "\n".join(ast.get_source_segment(text, n) or "" for n in body)


def test_all_three_files_DECLARE_strict_logic_inside_the_binding_window():
    """RETURNED VALUE, not a grep. `declared_strictness` is None for a
    declaration below the 4000-byte window — a rule no reader meets — so a
    declaration that drifted down the docstring during an edit fails here
    rather than going quietly unenforced."""
    import pathlib
    chk = importlib.import_module("source_chip_agnostic_check")
    progs = pathlib.Path(__file__).resolve().parents[1]
    for name in _THIS_CHANGE:
        text = (progs / name).read_text(encoding="utf-8")
        assert chk.declared_strictness(text) == "strict-logic", (
            "%s must declare CHIP_AGNOSTIC: strict-logic inside the binding "
            "window (site: %r)" % (name, chk.declared_strictness_site(text)))


def test_no_chip_vendor_or_SKU_token_appears_in_the_LOGIC_of_any_of_them():
    """The refusal itself, using the GATE'S OWN token table rather than a list
    re-typed here: a hand-written copy of "the forbidden names" is stale the
    day the gate learns a new one."""
    import pathlib
    chk = importlib.import_module("source_chip_agnostic_check")
    progs = pathlib.Path(__file__).resolve().parents[1]
    # The gate's own tokens (the NDA table), PLUS the names this change was
    # measured on and the OPEN PDKs. The open PDK names are not NDA-forbidden
    # anywhere in the repo and are perfectly legal in a docstring — but a gate
    # whose subject is "whatever --ic and --pdk name at the call site" may not
    # carry one in its logic, so the three files of this change hold themselves
    # to that. MEASURED: without this extension a planted
    # `SPM_DEFAULT_PDK = "gf180mcuD"` survived the test (falsref mutant M8 was
    # INERT), because `SPM_` is not a word boundary and the PDK name is not in
    # the NDA table. The mutant was right and the test's scope was too narrow.
    token_re = chk._build_token_re(
        ["spm", "subservient", "sha256_run16_pass2",
         "gf180mcud", "sky130a", "ihp-sg13g2", "nangate45", "asap7"])
    for name in _THIS_CHANGE:
        logic = _module_logic(progs / name)
        hits = sorted(set(m.group(1).lower() for m in token_re.finditer(logic)))
        assert not hits, (
            "%s declares strict-logic and its LOGIC names %s — the subject must "
            "come from the call site, never from a literal" % (name, hits))


def test_the_guard_above_can_actually_see_a_planted_literal():
    """CALIBRATION of the guard itself (R-0915-86 (3)): an instrument that
    cannot fire proves nothing about the files it passed. A literal planted in
    a LOGIC position must be found, and the same literal in the module
    docstring must NOT be — that difference IS `strict-logic`."""
    chk = importlib.import_module("source_chip_agnostic_check")
    token_re = chk._build_token_re(["spm"])
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp(prefix="agn_"))
    planted = d / "m.py"
    planted.write_text('"""doc mentioning spm."""\nX = "spm"\n', encoding="utf-8")
    assert token_re.search(_module_logic(planted)), \
        "the guard cannot see a literal in the logic — it proves nothing"
    docstring_only = d / "n.py"
    docstring_only.write_text('"""doc mentioning spm."""\nX = 1\n',
                              encoding="utf-8")
    assert not token_re.search(_module_logic(docstring_only)), \
        "strict-logic must permit the docstring to name what it measured on"
