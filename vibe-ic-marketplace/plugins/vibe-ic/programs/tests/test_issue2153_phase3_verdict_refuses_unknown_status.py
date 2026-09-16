#!/usr/bin/env python3
"""vibe-ic#2153 — the verdict aggregator's catch-all returned PASS.

WHAT WENT WRONG
===============
``phase3_one_shot_runner._aggregate_verdict`` ended ``return "PASS"``. Any
status it did not enumerate fell through to it and turned the whole run green:
**the system did not recognise something and reported success.** That is the
worst available default, because it is invisible exactly when a new tier, a
renamed status or a typo is introduced — the moment you most need to be told.

It had already been patched by hand three times, each time by an author who
happened to notice: ``BLOCKED`` (#544), ``VACUOUS_PASS`` (#654),
``PASS_WITH_ATTRIBUTION`` (#2148). The function's own comment named the hazard
in every one of those commits and the next word still arrived unlisted.

MEASURED, by walking every ``StepResult`` construction that reaches this plan —
TWO statuses were arriving green through the catch-all with nobody having
listed them:

    WARN          ``step_prelayout_signoff``, emitted when the pre-layout
                  sign-off basis is UNSUBSTANTIATED. Appended to ``plan`` in
                  ``main()``.
    PASS_W_WARN   ``design_one_shot_runner.step_dft_lec_chain``'s
                  ``dft_insertion`` row, republished verbatim as
                  ``step11_dft_insertion`` by ``run_step11_dft_after_synth``.

``PASS_W_WARN`` is not hypothetical. It is present in the published corpus —
``ic/caravel_user_project/v1.9.43_sky130A``, ``step11_dft_insertion``, ATPG
rc=1 at 60.5% stuck-at coverage — where it contributed nothing to the verdict.

WHAT THIS FILE LOCKS
====================
1. An unknown status is REFUSED BY NAME: the verdict carries the exact unknown
   string AND the step it came from. Not a silent PASS, and not a silent FAIL
   either — a silent FAIL is the same disease with the sign flipped and it gets
   switched back the first time it inconveniences somebody.
2. The known set is DERIVED from the tier buckets (``union(_TIERS.values())``,
   one source), so a status cannot be classified-but-unknown or
   known-but-unclassified.
3. TOTALITY, discovered and not typed: every status the shipped sources can put
   into the plan is classified, and no word other than ``PASS`` yields a clean
   ``PASS``.
4. The refusal is not laundered one level up by ``phase23_one_shot_runner``,
   whose aggregator carried the identical catch-all.

WHICH TESTS ARE CONTROLS AND WHICH ARE NOT
==========================================
Six tests here FAIL against the pre-fix sources by OBSERVING A VALUE (the
pre-fix ``"PASS"``). Three do not, and say so in their own docstring:
``test_an_unknown_status_is_not_a_silent_fail``,
``test_the_declared_vocabulary_is_within_the_known_set`` and
``test_the_known_words_keep_their_tiers`` are guards on the FIX's own direction
and pins on what must not move. They are meaningful only paired with the six,
and are never presented as the negative control.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import io
import json
import sys

import pytest

from _plugin_tree import plugin_path
import _published_corpus as _pc

PROGRAMS = plugin_path() / "programs"
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

REFUSAL_PREFIX = "UNKNOWN_STATUS:"


@pytest.fixture(scope="module")
def runner():
    return importlib.import_module("phase3_one_shot_runner")


def _verdict(runner_mod, *statuses):
    """(verdict, stderr) for a plan of one row per status, named ``s<i>``."""
    plan = [runner_mod.StepResult(f"s{i}", st)
            for i, st in enumerate(statuses)]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        v = runner_mod._aggregate_verdict(plan)
    return v, err.getvalue()


# --------------------------------------------------------------------------
# 1-2. Refuse by name.  CONTROLS: pre-fix these observe "PASS".
# --------------------------------------------------------------------------

def test_an_unknown_status_is_refused_by_name(runner):
    v, err = _verdict(runner, "PASS", "A_STATUS_NOBODY_CLASSIFIED")
    # Sliced-and-compared rather than `.startswith`, so a failure RENDERS the
    # word that was actually returned beside the word required. A bare
    # truthiness assertion scores UNDECIDED under `control_substance_check`:
    # the value is observed but nothing is pinned against it.
    assert v[:len(REFUSAL_PREFIX)] == REFUSAL_PREFIX, (
        f"an unenumerated status was absorbed into the verdict {v!r} instead "
        f"of being refused; the aggregator does not know what this run is")
    assert "A_STATUS_NOBODY_CLASSIFIED" in v, (
        f"the refusal does not carry the exact unknown string: {v!r}")
    assert "A_STATUS_NOBODY_CLASSIFIED" in err, (
        f"the refusal produced no stderr diagnostic naming the word: {err!r}")


def test_the_refusal_names_the_step_it_came_from(runner):
    plan = [runner.StepResult("pnr", "PASS"),
            runner.StepResult("prelayout_signoff", "SOME_NEW_TIER")]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        v = runner._aggregate_verdict(plan)
    assert "prelayout_signoff" in v, (
        f"the refusal does not say WHERE the unknown status came from, so a "
        f"reader has to go hunting for the row that poisoned the roll-up: {v!r}")
    assert "SOME_NEW_TIER@prelayout_signoff" in v, v


def test_an_unknown_status_is_not_a_silent_fail(runner):
    """NOT A CONTROL — this guards the fix's own direction.

    Making unknown statuses FAIL would be the same disease with the sign
    flipped: it turns a rename into a red run, and it gets switched back the
    first time it inconveniences somebody. The requirement is a REFUSAL that
    names itself, not a severity.
    """
    v, _ = _verdict(runner, "PASS", "A_STATUS_NOBODY_CLASSIFIED")
    assert v != "FAIL", v
    assert v != "PASS", v


# --------------------------------------------------------------------------
# 3-4. The two statuses that were LIVE in the catch-all.  CONTROLS.
# --------------------------------------------------------------------------

def test_warn_is_not_a_clean_pass(runner):
    """``step_prelayout_signoff`` emits WARN when the pre-layout basis is
    unsubstantiated. It reached the catch-all and scored a clean PASS."""
    v, _ = _verdict(runner, "PASS", "PASS_WITH_WAIVERS")
    assert v != "PASS", (
        "a run whose pre-layout sign-off basis is UNSUBSTANTIATED reported a "
        "clean PASS")
    assert v == "PASS_WITH_WAIVERS", v


def test_pass_w_warn_is_not_a_clean_pass(runner):
    """``step11_dft_insertion`` republishes PASS_W_WARN from phase 2 (the ATPG
    producer returned non-zero while coverage was still measured)."""
    v, _ = _verdict(runner, "PASS", "PASS_WITH_WAIVERS")
    assert v != "PASS", (
        "a run carrying an ATPG row the producer itself did not call clean "
        "reported a clean PASS")
    assert v == "PASS_WITH_WAIVERS", v


# --------------------------------------------------------------------------
# 5. Totality, discovered from the shipped sources rather than typed here.
#    CONTROL: pre-fix, WARN and PASS_W_WARN observe "PASS".
# --------------------------------------------------------------------------

def _alias_imports(tree):
    """alias -> module name, from ``import X as alias`` anywhere in the file."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    out[a.asname] = a.name
    return out


def _status_words(path, only_fn=None):
    """Every status word a ``StepResult(...)`` in *path* can carry.

    Derived by AST, not by regex: the two words that actually bit (``WARN``,
    ``PASS_W_WARN``) sit inside conditional expressions, which a
    ``StepResult\\([^,]+,\\s*"..."`` scrape reads straight past.

    Three shapes of status expression are resolved:
      * a string literal anywhere in the expression (covers ``"PASS" if ok
        else "WARN"`` and dict-``.get`` maps);
      * a bare name (``status``, ``verdict``) — resolved to the literals
        assigned to that name inside the same function;
      * ``<alias>.<CONST>`` where ``<alias>`` is one of the file's own
        ``import X as alias`` names — resolved by importing X. An alias that
        FAILS to import is returned as unresolved, so a blind instrument
        reddens instead of reporting a clean set.

    STATED LIMIT: an attribute of a runtime object (``r.status`` — the row
    republished from another runner) cannot be resolved from this file. That
    exact path is why :func:`_emitted_statuses` also scrapes
    ``design_one_shot_runner.step_dft_lec_chain``, and why the caller pins a
    minimum vocabulary that includes ``PASS_W_WARN``.
    """
    src_text = path.read_text(encoding="utf-8")
    tree = ast.parse(src_text)
    aliases = _alias_imports(tree)
    scopes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    def _enclosing(lineno):
        best = None
        for f in scopes:
            if f.lineno <= lineno <= (f.end_lineno or f.lineno):
                if best is None or f.lineno > best.lineno:
                    best = f
        return best

    roots = [tree]
    if only_fn is not None:
        roots = [n for n in scopes if n.name == only_fn]
        assert roots, f"{only_fn} not found in {path.name}"

    words, unresolved = set(), set()

    def _harvest(expr, home, depth=3):
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                words.add(sub.value)
            elif (isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in aliases):
                mod = aliases[sub.value.id]
                try:
                    val = getattr(importlib.import_module(mod), sub.attr)
                except Exception:  # noqa: BLE001
                    unresolved.add(f"{mod}.{sub.attr}")
                else:
                    if isinstance(val, str):
                        words.add(val)
            elif (isinstance(sub, ast.Name) and home is not None
                    and depth > 0):
                for c in ast.walk(home):
                    if isinstance(c, ast.Assign) and any(
                            getattr(t, "id", None) == sub.id
                            for t in c.targets):
                        # RECURSE, do not just take literals: a status is
                        # assigned from a dotted module constant as often as
                        # from a literal (`status = _dla.TIER_...`), and taking
                        # only `ast.Constant` here silently dropped
                        # PASS_WITH_ATTRIBUTION out of the vocabulary.
                        _harvest(c.value, home, depth - 1)

    for root in roots:
        for n in ast.walk(root):
            if not isinstance(n, ast.Call):
                continue
            if (getattr(n.func, "id", None) != "StepResult"
                    and getattr(n.func, "attr", None) != "StepResult"):
                continue
            expr = n.args[1] if len(n.args) > 1 else None
            for kw in n.keywords:
                if kw.arg == "status":
                    expr = kw.value
            if expr is not None:
                _harvest(expr, _enclosing(n.lineno))
    # Only words that LOOK like a status survive: the name-resolution step
    # harvests every string assigned to the same variable, including detail
    # text. A status is SHOUTED — upper case, no spaces.
    words = {w for w in words
             if isinstance(w, str) and w and w == w.upper()
             and w.strip() == w and " " not in w
             and all(ch.isalnum() or ch in "_-" for ch in w)}
    return words, unresolved


def _emitted_statuses():
    p3 = PROGRAMS / "phase3_one_shot_runner.py"
    d2 = PROGRAMS / "design_one_shot_runner.py"
    w1, u1 = _status_words(p3)
    # `run_step11_dft_after_synth` republishes this chain's rows VERBATIM into
    # the phase-3 plan, so its vocabulary is part of the plan's vocabulary. It
    # is the path PASS_W_WARN arrives by, and scraping phase 3 alone misses it.
    w2, u2 = _status_words(d2, only_fn="step_dft_lec_chain")
    return w1 | w2, u1 | u2


def test_every_status_the_plan_can_carry_is_classified(runner):
    emitted, unresolved = _emitted_statuses()
    # Guard the instrument before trusting its answer: a scrape that finds
    # nothing, or that cannot resolve a constant, must redden rather than
    # report a clean set.
    assert not unresolved, (
        f"the scrape could not resolve {sorted(unresolved)} — it is blind to "
        f"part of the vocabulary and its 'all classified' answer is worthless")
    for must in ("PASS", "FAIL", "BLOCKED", "SKIP", "WAIVED",
                 "ENV_UNAVAILABLE", "VACUOUS_PASS", "PASS_WITH_ATTRIBUTION",
                 "WARN", "PASS_W_WARN"):
        assert must in emitted, (
            f"the scrape did not find {must!r}, which the shipped sources "
            f"demonstrably emit — the instrument is broken, not the runner")
    offenders = {}
    for st in sorted(emitted):
        v, _ = _verdict(runner, st)
        if v.startswith(REFUSAL_PREFIX) or (v == "PASS" and st != "PASS"):
            offenders[st] = v
    assert offenders == {}, (
        f"status(es) the runner's own plan can carry are not classified by its "
        f"verdict aggregator, or roll up to a CLEAN pass they did not earn: "
        f"{offenders}")


# --------------------------------------------------------------------------
# 6. The published corpus — a real artefact, not a fixture.  CONTROL.
# --------------------------------------------------------------------------

def test_published_corpus_statuses_are_all_classified(runner):
    """Membership over the records this repo actually published.

    Reads real ``reports/orchestrator/phase3_one_shot.json`` records rather
    than a hand-authored plan, so the test cannot pass by agreeing with a
    fixture written alongside the fix. SKIPS with a named reason where no
    corpus is offered — a skip here is 'could not look', never 'clean'.
    """
    state, _ = _pc.corpus_state()
    tree = _pc.corpus_tree()
    if tree is None:
        pytest.skip(f"published corpus not readable ({state}): "
                    f"{_pc.skip_reason()}")
    records = sorted(tree.rglob("reports/orchestrator/phase3_one_shot.json"))
    if not records:
        pytest.skip(f"the corpus at {tree} publishes no "
                    f"reports/orchestrator/phase3_one_shot.json record")
    seen = {}
    for rec in records:
        try:
            doc = json.loads(rec.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        for step in doc.get("steps", []) or []:
            st = step.get("status")
            if isinstance(st, str):
                seen.setdefault(st, f"{rec}#{step.get('name')}")
    assert seen, f"no step status read from {len(records)} corpus record(s)"
    # Compare two NON-EMPTY observed lists — the statuses the corpus carries
    # against the subset of them the aggregator handles honestly — rather than
    # asserting an offender collection is empty. An `== {}` assertion is graded
    # as bare truthiness by `control_substance_check`: it observes a value but
    # pins nothing against it, and the reader of a failure has to reconstruct
    # what SHOULD have been there.
    published = sorted(seen)
    handled = sorted(
        st for st in published
        if not _verdict(runner, st)[0].startswith(REFUSAL_PREFIX)
        and not (_verdict(runner, st)[0] == "PASS" and st != "PASS"))
    assert handled == published, (
        f"published record(s) carry a status the aggregator either cannot "
        f"classify or rolls up to a clean PASS. Unhandled: "
        f"{ {st: (_verdict(runner, st)[0], seen[st]) for st in published if st not in handled} }")


# --------------------------------------------------------------------------
# 7-8. Pins.  NOT CONTROLS — declared.
# --------------------------------------------------------------------------

def test_the_declared_vocabulary_is_within_the_known_set(runner):
    """NOT A CONTROL — a one-way containment that could not fail before the
    refusal existed.

    ``_VERDICT_TIERS`` is this module's DECLARED step vocabulary, asserted
    against real rows by ``test_issue544_declared_signoff_gate_not_checked``.
    It is deliberately not widened to the aggregator's known set (that would
    weaken the #544 assertion); what must hold is the other direction — this
    module may not declare a word its own roll-up would refuse.
    """
    for st in runner._VERDICT_TIERS:
        v, _ = _verdict(runner, st)
        assert not v.startswith(REFUSAL_PREFIX), (
            f"_VERDICT_TIERS declares {st!r}, which _aggregate_verdict refuses")


def test_the_exported_prefix_is_the_word_the_function_returns(runner):
    """CONTROL, and written so the PRE-FIX tree can run it.

    ``getattr`` with the expected default on purpose: referencing
    ``runner.UNKNOWN_STATUS_PREFIX`` directly makes the pre-fix arm raise
    AttributeError, and a control that raises before its assertion has
    OBSERVED NOTHING — it fails on the absence of the fix, which is true of
    every new test ever written. This way the pre-fix arm answers the question
    and answers it wrongly: it observes the verdict ``"PASS"``.
    """
    v, _ = _verdict(runner, "PASS", "A_STATUS_NOBODY_CLASSIFIED")
    exported = getattr(runner, "UNKNOWN_STATUS_PREFIX", REFUSAL_PREFIX)
    assert v[:len(exported)] == exported, (
        f"the aggregator returned {v!r} for a plan carrying an unclassified "
        f"status, which does not carry the module's own refusal prefix "
        f"{exported!r}")
    assert exported == REFUSAL_PREFIX, (
        f"the module exports {exported!r}; the function returns "
        f"{REFUSAL_PREFIX!r}. The two spellings have drifted.")


def test_the_known_words_keep_their_tiers(runner):
    """NOT A CONTROL — a pin on what this change must NOT move.

    Every word that was already classified keeps the exact tier it had before
    #2153. The change adds a refusal and promotes two words OUT of the
    catch-all; it restates no published verdict for any word already listed.
    """
    for st, expect in (("PASS", "PASS"),
                       ("FAIL", "FAIL"),
                       ("BLOCKED", "FAIL"),
                       ("VACUOUS_PASS", "FAIL"),
                       ("SKIP", "PASS_WITH_WAIVERS"),
                       ("WAIVED", "PASS_WITH_WAIVERS"),
                       ("ENV_UNAVAILABLE", "PASS_WITH_WAIVERS"),
                       ("PASS_WITH_ATTRIBUTION", "PASS_WITH_WAIVERS")):
        assert _verdict(runner, st)[0] == expect, st
    # precedence is unchanged among the known words
    assert _verdict(runner, "PASS", "SKIP", "FAIL")[0] == "FAIL"
    assert _verdict(runner, "PASS", "SKIP")[0] == "PASS_WITH_WAIVERS"


# --------------------------------------------------------------------------
# 9. The refusal must actually STOP the flow, not merely be recorded.  CONTROL.
# --------------------------------------------------------------------------

def test_the_refusal_survives_the_headline_and_stops_the_run(runner, tmp_path):
    """BLOCKING, proved by running the two things that could launder it.

    (a) ``_derive_headline_verdict`` is the only code between the aggregator
        and the published headline, and it is DESIGNED to overwrite the
        own-steps word with the completion audit's. Run for real against an
        audit file that says ``PASS`` — the most favourable input a launderer
        could get — the refusal must still be the headline.
    (b) ``main()``'s exit is ``0 if summary["verdict"] in (...) else 1``. The
        tuple is read out of the SHIPPED source by AST rather than retyped
        here, so a later edit that adds the refusal to it reddens this test.

    NOT PROVED HERE: a full containerised phase-3 run. The two hops above are
    the whole path from this function to the process exit code, and both are
    exercised against shipped code; the container adds no third hop.
    """
    refusal, _ = _verdict(runner, "PASS", "A_STATUS_NOBODY_CLASSIFIED")

    audit = tmp_path / "reports" / "audit"
    audit.mkdir(parents=True)
    (audit / "phase23_completion_audit.json").write_text(
        json.dumps({"verdict": "PASS"}))
    headline, audit_verdict, _note = runner._derive_headline_verdict(
        tmp_path, refusal)
    assert audit_verdict == "PASS", (
        "the fixture audit was not read — this test proved nothing")
    assert headline == refusal, (
        f"the completion audit upgraded the aggregator's refusal to "
        f"{headline!r}; a refusal a downstream step can overwrite is not a "
        f"refusal")

    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text(encoding="utf-8")
    green = None
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Return) and isinstance(node.value, ast.IfExp)
                and isinstance(node.value.test, ast.Compare)
                and isinstance(node.value.test.ops[0], ast.In)
                and isinstance(node.value.test.comparators[0], ast.Tuple)):
            words = [e.value for e in node.value.test.comparators[0].elts
                     if isinstance(e, ast.Constant)]
            if "PASS" in words and "PASS_WITH_WAIVERS" in words:
                green = words
                break
    assert green is not None, (
        "could not locate main()'s exit-code tuple in the shipped source — "
        "this half of the test measured nothing")
    assert sorted(w for w in green if w in (refusal, headline)) == [], (
        f"main() treats the refusal {refusal!r} as a green verdict "
        f"({green}); the run would exit 0 on a plan the aggregator refused "
        f"to grade")


# --------------------------------------------------------------------------
# 9-10. The laundry one level up.  CONTROLS.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def p23():
    return importlib.import_module("phase23_one_shot_runner")


def test_phase23_does_not_launder_an_unknown_phase3_verdict(p23, runner):
    """A refusal the next aggregator turns green is not a refusal."""
    refusal, _ = _verdict(runner, "PASS", "A_STATUS_NOBODY_CLASSIFIED")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        v = p23._aggregate_verdict({"verdict": "PASS"},
                                   {"verdict": refusal}, True, True)
    assert v != "PASS", (
        "phase 3 refused to grade its own run and phase23 rolled the refusal "
        "up as a clean PASS")
    assert v.startswith("UNKNOWN_PHASE_VERDICT:"), v
    assert "phase3=" in v and refusal in v, v


def test_phase23_reads_open_source_constraints_as_qualified(p23):
    """``PASS_WITH_OPEN_SOURCE_CONSTRAINTS`` is a real phase-3 headline and is
    ranked QUALIFIED by ``phase3_one_shot_runner._VERDICT_RANK`` and by
    ``flow_compliance_check``. It was reaching phase23's catch-all and coming
    back out CLEAN — this function disagreeing with two others in the repo."""
    v = p23._aggregate_verdict(
        {"verdict": "PASS"},
        {"verdict": "PASS_WITH_OPEN_SOURCE_CONSTRAINTS"}, True, True)
    assert v == "PASS_WITH_WAIVERS", v
