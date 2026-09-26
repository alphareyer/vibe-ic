#!/usr/bin/env python3
"""vibe-ic#2141 — `gate_is_wired` credited a gate for LIBRARY use of its module.

WHAT WAS MEASURED ON THE FROZEN BASE (577fb50d3831, v1.19.24)
=============================================================
`gate_is_wired_check.py` under `invocation.v1` credited a gate as WIRED for ANY
reference to ANY symbol its module exports. So a module that is BOTH a library
AND a gate read as wired on the strength of its library use alone, and the
register could not tell "imported as a library" from "run as a gate". That is
vibe-ic#2080's shape one rung up: #2080 removed credit-by-declaration and
credit-by-prose; this is credit-by-import.

THE ISSUE'S NAMED INSTANCE DOES NOT REPRODUCE, AND THAT IS RECORDED HERE
========================================================================
#2141 names `lesson_consumption_check`, whose credit was
`design_one_shot_runner` reaching `parse_digest`, `match_sections`,
`build_scoring_record` and `SCORING_RECORD_NAME`. On the frozen base that is no
longer its only credit: vibe-ic#2103 landed `step_lesson_consumption`, which
calls `_lesson_consumed.main(argv)` at `design_one_shot_runner.py:5373`. The
gate IS wired today and stays credited under the new rule — as
`verdict entry (main)` rather than the bare `imported+referenced` it had
before. The DEFECT is the resolver, not that one gate, and the resolver
reproduces on the frozen base for eight other gates (see the commit body).

THE RULE, `invocation.v2` — WHO PRODUCES THE VERDICT
====================================================
The first draft of this fix counted STAGES: one stage credited, two or more was
re-implementation. That proxy could not be defended at its own boundary — it
called `def_manufacturing_grid_check` and `ppa_area_threshold_check` library
use, and both of those callers demonstrably run the gate and read its answer.
The question is not how many of a gate's functions a caller touches; it is WHO
PRODUCES THE VERDICT.

An import credits a gate when:

  * `main` is referenced — the verdict entry point itself; or
  * a STAGE is referenced and the caller CONSUMES the gate's answer: the
    result is returned or exited with, handed to a driver that calls it, bound
    to a verdict-named name, or read through a verdict-named field; or
  * the gate defines NO `main` at all — its public API is the whole check
    surface, it composes nothing, and there is nothing to re-implement.

And it does NOT credit when the caller CHAINS two or more of the gate's stages
— feeding one stage's return into another stage of the same gate — and never
reads a verdict out of any of them. That is the caller reproducing `main`'s
composition and judging for itself, which is what `design_one_shot_runner` did
to `lesson_consumption_check` before #2103 and what `regression_issue_intake_check`
does to `acceptance_evidence_in_fix_comment_check` today. A constant, a
`_`-private helper and a bare module handed to `getattr` are not stages at all.

THE MIGRATION IS IN THE PROGRAM, NOT IN A HUMAN (owner ruling, #2141)
=====================================================================
A rule that gets stricter and then reports failure until somebody presses
`--write-baseline` is itself the defect: that flag can ADD **and REMOVE**.
`--migrate-rule-id` re-derives the register under the new rule id and REFUSES
unless the new set is a strict SUPERSET of the old — a name may be added, never
removed — and prints every addition. Both directions are tested below.
"""
import ast
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import gate_is_wired_check as G                      # noqa: E402


#: A gate that COMPOSES its verdict out of three stages, exactly the shape
#: `lesson_consumption_check` has.
COMPOSING_GATE = '''
SCORING_RECORD_NAME = "record.json"


def parse_digest(text):
    return [text]


def match_sections(spec, sections):
    return []


def build_scoring_record(matches):
    return {"matches": matches}


def main(argv=None):
    sections = parse_digest("x")
    matches = match_sections("y", sections)
    build_scoring_record(matches)
    return 0
'''

#: A gate with NO `main`: its public API is its whole verdict surface, the
#: shape `url_oracle_guard` and `spec_conformance_gate` have.
LIBRARY_GATE = '''
def url_allowed(url, self_repo):
    return True, "ok"


def parse_project_self_repo(text):
    return None
'''


def _stages(src):
    st, composed = G.verdict_path(src)
    return {"demo_check": (st, composed)}


def test_a_caller_that_reaches_main_credits_the_gate():
    """GREEN DIRECTION — the verdict entry point is reached."""
    caller = ("import demo_check as _d\n"
              "def step():\n"
              "    return _d.main(['--project', 'p'])\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert "demo_check" in found, found
    assert "verdict entry (main)" in found["demo_check"], found


def test_a_caller_that_only_uses_the_library_credits_nothing():
    """RED BEFORE THE FIX — this is vibe-ic#2141 itself.

    Under `invocation.v1` this asserted an empty dict against
    `{'demo_check': 'imported+referenced'}` and failed.
    """
    caller = ("import demo_check as _d\n"
              "def step(project):\n"
              "    sections = _d.parse_digest('x')\n"
              "    matches = _d.match_sections('y', sections)\n"
              "    strong = [m['section'] for m in matches if m['strong']]\n"
              "    rec = _d.build_scoring_record(matches)\n"
              "    _write_json(project / _d.SCORING_RECORD_NAME, rec)\n"
              "    return strong\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert found == {}, (
        "library use of a gate's module is not an invocation of the gate: "
        f"{found}")


def test_one_stage_handed_to_a_driver_still_credits():
    """The `benchmark/cvdp_gate` shape must NOT be accused.

    `_structural_finding_gate` is handed `check_text` and BLOCKS on any
    ERROR-severity finding it returns. That is the gate producing a verdict,
    through one function, with no Call node of its own.
    """
    caller = ("from demo_check import parse_digest as _f\n"
              "def gate(completion):\n"
              "    return _driver(_f, completion)\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert "demo_check" in found, found
    assert "verdict consumed" in found["demo_check"], found


def test_input_prep_plus_a_core_whose_verdict_is_read_credits():
    """RULING 2, and the shape the first draft got wrong.

    `phase3_one_shot_runner.py:36652` — `read_mfg_grid_um` prepares an input,
    `classify_def` produces the answer, and the runner reads
    `_src.get("verdict")`. Two stages, and the GATE produced the verdict.
    """
    caller = ("import demo_check as _d\n"
              "def step(lef, text):\n"
              "    grid = _d.parse_digest(lef)\n"
              "    src = _d.match_sections(text, grid)\n"
              "    return src.get('verdict')\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert "demo_check" in found, found
    assert "verdict consumed" in found["demo_check"], found


def test_a_verdict_named_binding_is_consumption():
    """`verdict, rep = ag.evaluate(...)` — a binding is a statement about what
    the value IS. MEASURED: `signoff_ladder_run` writes exactly that for
    `aging_derate_sta_check`."""
    caller = ("import demo_check as _d\n"
              "def tier(x):\n"
              "    verdict, rep = _d.match_sections(x, [])\n"
              "    return {'PASS': 'PASS'}.get(verdict, 'FAIL'), rep\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert "demo_check" in found, found


def test_two_scopes_naming_their_results_the_same_do_not_cross_credit():
    """Per-SCOPE, never whole-file. `signoff_ladder_run` binds
    `verdict, rep = <gate>.evaluate(...)` in several tiers; a file-wide map is
    last-writer-wins and attributed one tier's `verdict ==` branch to another
    gate, leaving a gate the ladder runs on every signoff reading as unwired."""
    caller = ("import demo_check as _d\n"
              "def tier_a(x):\n"
              "    rec = _d.parse_digest(x)\n"
              "    out = _d.match_sections(x, rec)\n"
              "    _write(out)\n"
              "def tier_b(y):\n"
              "    verdict = _other.evaluate(y)\n"
              "    return verdict\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert found == {}, (
        "tier_b's `verdict` belongs to another module; it must not credit "
        f"demo_check for tier_a's chained library use: {found}")


def test_a_gate_with_no_main_is_credited_by_its_public_api():
    """A gate that composes nothing cannot be re-implemented by its caller."""
    caller = ("import demo_check as _d\n"
              "def parse(readme, url):\n"
              "    repo = _d.parse_project_self_repo(readme)\n"
              "    return _d.url_allowed(url, repo)\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(LIBRARY_GATE))
    assert "demo_check" in found, found
    assert "library gate API" in found["demo_check"], found


def test_a_bare_module_reference_credits_nothing():
    """`getattr(_hpa, "_CRITERIA")` reads a PRIVATE registry — library use.

    Measured on the frozen base: that one line in `l13_bringup_contract_check`
    was the ONLY credit `hardware_pass_attestation_check` had anywhere.
    """
    caller = ("import demo_check as _d\n"
              "def names():\n"
              "    return sorted(getattr(_d, '_CRITERIA', {}))\n")
    found = G.py_invocations(caller, {"demo_check"}, _stages(COMPOSING_GATE))
    assert found == {}, found


def test_a_dead_import_still_credits_nothing():
    """Unchanged from `invocation.v1`, and asserted so a refactor keeps it."""
    found = G.py_invocations("import demo_check\n", {"demo_check"},
                             _stages(COMPOSING_GATE))
    assert found == {}, found


def test_the_rule_id_records_which_question_the_register_answers():
    """The register's `measured_under` stamp is the only thing that makes a
    POPULATION CHANGE distinguishable from a debt that grew."""
    assert G._RULE_ID == "invocation.v2"


def test_verdict_path_excludes_constants_and_private_helpers():
    stages, composed = G.verdict_path(COMPOSING_GATE)
    assert composed is True
    assert stages == {"parse_digest", "match_sections", "build_scoring_record"}
    stages, composed = G.verdict_path(LIBRARY_GATE)
    assert composed is False
    assert stages == {"url_allowed", "parse_project_self_repo"}


def test_the_shipped_resolver_is_the_one_the_gate_uses():
    """`wiring()` must pass the stage map through — a resolver nothing calls
    is this gate's own defect, one level in."""
    src = (PROGRAMS / "gate_is_wired_check.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "wiring")
    calls = {c.func.id for c in ast.walk(fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "verdict_path" in calls
    assert "py_invocations" in calls


# ── THE RULE-ID MIGRATION, BOTH DIRECTIONS (owner ruling, #2141) ────────────
# `--write-baseline` can ADD and REMOVE, so it is the wrong instrument for a
# rule that got stricter: it would let a real tightening — a gate that stopped
# being unwired — leave the register unremarked, inside a migration where it is
# invisible among the additions. `--migrate-rule-id` may only ADD, and these
# two tests are the proof that the asymmetry is in the PROGRAM and not in a
# human's judgement at landing time.

import json                                            # noqa: E402
import subprocess                                      # noqa: E402
import sys as _sys                                     # noqa: E402

GATE = PROGRAMS / "gate_is_wired_check.py"
REGISTER = PROGRAMS / "gate_is_wired_baseline.json"


def _run(baseline, *args):
    return subprocess.run(
        # `--root` NAMED (vibe-ic#2199): these arms drive the register over the
        # SHIPPED plugin, and the gate no longer guesses that from its own path.
        [_sys.executable, str(GATE), "--root", str(PROGRAMS.parent),
         "--baseline", str(baseline), *args],
        capture_output=True, text=True)


def _register():
    return json.loads(REGISTER.read_text(encoding="utf-8"))


@pytest.mark.consistency
def test_the_shipped_register_is_stamped_with_the_new_rule():
    """The migration was RUN, not deferred to a human at landing time."""
    assert _register().get("measured_under") == G._RULE_ID


def test_a_migration_that_would_ADD_is_accepted(tmp_path):
    """GREEN DIRECTION. A register stamped with an older rule id, holding a
    strict SUBSET of what this build measures, migrates and prints the added
    names."""
    doc = dict(_register())
    doc["measured_under"] = "invocation.v0-synthetic"
    dropped = sorted(doc["unwired"])[:2]
    doc["unwired"] = [n for n in doc["unwired"] if n not in dropped]
    p = tmp_path / "register.json"
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    r = _run(p, "--migrate-rule-id")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[MIGRATE]" in r.stdout, r.stdout
    for n in dropped:
        assert f"+ {n}" in r.stdout, (n, r.stdout)
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after["measured_under"] == G._RULE_ID
    assert set(dropped) <= set(after["unwired"])


def test_a_migration_that_would_REMOVE_is_refused(tmp_path):
    """RED DIRECTION, and the whole reason this flag exists rather than
    `--write-baseline`. A name in the register that this build calls WIRED is a
    TIGHTENING; it belongs to `--record-shrink`, which can only remove, and a
    migration must refuse to swallow it."""
    doc = dict(_register())
    doc["measured_under"] = "invocation.v0-synthetic"
    doc["unwired"] = sorted(set(doc["unwired"]) | {"zz_synthetic_gate_check"})
    p = tmp_path / "register.json"
    before = json.dumps(doc, indent=2)
    p.write_text(before, encoding="utf-8")
    r = _run(p, "--migrate-rule-id")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "NOT a superset" in r.stderr, r.stderr
    assert "- zz_synthetic_gate_check" in r.stderr, r.stderr
    assert p.read_text(encoding="utf-8") == before, "the register was written"


def test_a_migration_with_the_rule_id_already_current_records_nothing(tmp_path):
    """Same rule id -> a difference is a DEBT that moved, and this flag
    deliberately cannot record one. The door closes the moment it is used."""
    doc = dict(_register())
    doc["unwired"] = [n for n in doc["unwired"] if n != sorted(doc["unwired"])[0]]
    p = tmp_path / "register.json"
    before = json.dumps(doc, indent=2)
    p.write_text(before, encoding="utf-8")
    r = _run(p, "--migrate-rule-id")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nothing to migrate" in r.stdout, r.stdout
    assert p.read_text(encoding="utf-8") == before, "the register was written"


def test_omitting_the_stage_map_is_refused_and_not_answered_with_an_empty_dict():
    """vibe-ic#2189, closed at the RULE rather than at one caller.

    The defect was never that one caller forgot the map — it was that
    forgetting it produced a well-formed, wrong, SILENT answer: `{}`, which is
    indistinguishable from "this source invokes nothing". Both directions:

      * omitted        -> TypeError naming the omission;
      * declared       -> the old permissive behaviour, on purpose, for a
                          population of names that are not gates.

    The declared arm matters as much as the refusal: a guard that only refuses
    would push the next caller into inventing a fake map, which is the same
    wrong answer with a longer path to it.
    """
    caller = ("import demo_check\n"
              "def go():\n"
              "    return demo_check.main()\n")
    with pytest.raises(TypeError) as exc:
        G.py_invocations(caller, {"demo_check"})
    assert "vibe-ic#2189" in str(exc.value)

    declared = G.py_invocations(caller, {"demo_check"},
                                _allow_no_stage_map=True)
    assert declared, (
        "the declared escape must still ANSWER — a `main()` caller credits "
        "under the default map, and if this is empty the escape is useless "
        "and the refusal above is the only behaviour left")


def test_every_shipped_call_site_passes_the_stage_map():
    """The refusal above is a rule; this is the sweep that says it holds NOW.

    Derived from the tree, not from a list: every `py_invocations(...)` call in
    every python file under the plugin must carry a third argument or the
    explicit keyword. A hand-written list of call sites goes stale silently.
    """
    root = PROGRAMS.parent
    offenders = []
    for f in sorted(root.rglob("*.py")):
        if f.name == "gate_is_wired_check.py":
            continue                              # the definition itself
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except SyntaxError:
            continue
        # A CALL INSIDE `with pytest.raises(...)` IS THE REFUSAL BEING TESTED.
        # Derived from the syntax, not excused by file or line number, so the
        # next such arm is covered and a real caller never is.
        proving = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            for item in node.items:
                c = item.context_expr
                if (isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "raises"):
                    proving.update(
                        range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else None)
            if name != "py_invocations" or node.lineno in proving:
                continue
            kw = {k.arg for k in node.keywords}
            if len(node.args) < 3 and not ({"stages", "_allow_no_stage_map"} & kw):
                offenders.append(f"{f.relative_to(root)}:{node.lineno}")
    assert not offenders, (
        "these call sites would now raise, and before vibe-ic#2189's rule "
        f"landed they silently got `{{}}`: {offenders}")


def test_the_call_site_sweep_can_actually_see_an_offender():
    """The sweep above is a ZERO, and a zero from an instrument nobody proved
    can see is not a measurement (this repo's own
    `gate_zero_denominator_refuses_check`, #564).

    The same walk, run over source that carries one offender and one proving
    arm, must return exactly the offender.
    """
    src = ("import pytest\n"
           "def a():\n"
           "    return G.py_invocations(text, names)\n"
           "def b():\n"
           "    with pytest.raises(TypeError):\n"
           "        G.py_invocations(text, names)\n"
           "def c():\n"
           "    return G.py_invocations(text, names, stage_map)\n")
    tree = ast.parse(src)
    proving = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            c = item.context_expr
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "raises"):
                proving.update(
                    range(node.lineno, (node.end_lineno or node.lineno) + 1))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute) else None)
        if name != "py_invocations" or node.lineno in proving:
            continue
        if len(node.args) < 3 and not (
                {"stages", "_allow_no_stage_map"} & {k.arg for k in node.keywords}):
            hits.append(node.lineno)
    assert hits == [3], hits
