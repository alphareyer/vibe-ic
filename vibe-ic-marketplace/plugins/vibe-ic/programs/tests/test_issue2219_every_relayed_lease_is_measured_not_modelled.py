"""EVERY relayed lease is measured, and the wrong model is unreachable
(vibe-ic#2219, second half).

WHAT THE FIRST HALF LEFT. `17d0813b9` built `relay_window` — a MEASURED reading
of the nested relay lane — and moved ONE call site onto it,
`test_nested_validated_progress_is_relayed_to_the_outer_session`. It left
`stall_window(nominal, starts=N)` in place with the docstring "Prefer
`relay_window` wherever the events that renew the lease arrive through
`--progress-relay`". Two call sites did not prefer it, and they kept the defect
the issue was filed for.

REPRODUCED 2026-09-10 on 8HD-6, live main `9c653d47f` (v1.20.18), pinned image
`vibeic-eda@sha256:89a8fd72…`, `--cpus=1 --memory=8g --pids-limit=1024`.
Ten interleaved reps of the test #2219 NAMES — five on main and five on
`b9747f50e`, the commit before the repair — were 10/10 GREEN, because this box
read `trivial_session_s = 1.0736` and the old expression `max(2.5, 4 x session)`
lifts to 4.29 s there. The colour is the machine's, which is the whole issue,
so the box was PINNED to the 0.60 s reading #2219 itself recorded instead of
the die being re-rolled. At that reading, in one container:

    test_nested_validated_progress_is_relayed_to_the_outer_session  PASSED
    test_nested_collect_progress_is_relayed_to_the_outer_session    FAILED
        WATCHDOG_STALLED ... did not advance for > 2.4s ...
            since_last_progress_s=2.419 elapsed_s=5.946
        PROGRESS_PROTOCOL_INCOMPLETE: m.39.1.jsonl: terminal event missing
            (stage=running)
        assert (199 == 0)

the issue's own signature, on the call site it did not name. That call site was
the MORE exposed of the two: its window is `max(0.8, 4 x session)`, which at
session 0.60 is **2.4 s** against the repaired sibling's 2.5 s floor.

ONE ROOT CAUSE, THREE CALL SITES, ONE REPAIRED. So the repair here is to the
CAUSE: `starts > 1` is refused, which makes the measured-wrong model
unreachable rather than merely unfashionable, and both remaining call sites
move onto `relay_window`.

AND THE MIRROR IS RECEIVED, NOT RECOMPUTED. The nested node mirrors its
driver's lease. Recomputing `relay_window` there puts the calibration IN SERIES
with the silence that lease is watching — MEASURED at the same pinned reading:
the lease grew to 5.431 s and the silence grew to 5.479 s, so the naive swap
was still RED, for a reason the repair itself had introduced. The driver
publishes its lease through `OUTER_LEASE_ENV` and the node reads it.

    FIXED, same container, same pinned 0.60 reading:   2 passed
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _session_floor as _floor            # noqa: E402

_TESTS = Path(__file__).resolve().parent
_PROGRAMS = _TESTS.parent


def _calls_in(path):
    """{function name: set of called names} for one module, from its AST.

    Prose is not code. Every guard below reads what the file CALLS, so it can
    be neither greened by deleting a comment nor reddened by writing one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        out[fn.name] = names
    return out


def test_the_measured_wrong_model_is_UNREACHABLE_not_merely_deprecated():
    """THE CAUSE. `starts > 1` said "model this relayed lane as N interpreter
    start-ups", and #2219 measured that model wrong: the lane is 2.6x an
    interpreter start and four of its five terms are not start-up at all.

    A docstring preferring `relay_window` is advice; two call sites ignored it
    for five versions. A refusal cannot be ignored, and it names the
    replacement at the point of use. `starts=1` is untouched — a subject that
    IS a pytest is still modelled by its own start-up, which is what
    `trivial_session_s` measures and what it is correct for.
    """
    assert _floor.stall_window(0.1, starts=1) == _floor.stall_window(0.1)
    for bad in (2, 3, 7):
        with pytest.raises(ValueError, match="relay_window"):
            _floor.stall_window(0.8, starts=bad)
    # The refusal names the MEASUREMENT, not just the rule, so the next reader
    # can check it rather than take it on faith. Through `pytest.raises` and
    # not `try/except`, deliberately: the census below tells a call that PROVES
    # the refusal from a call that BUILDS a lease by looking for exactly this
    # construct, and a discriminator widened to every `try` would exempt far
    # more than it was asked to.
    with pytest.raises(ValueError) as excinfo:
        _floor.stall_window(0.8, starts=2)
    assert "2.6x" in str(excinfo.value), str(excinfo.value)
    assert "--progress-relay" in str(excinfo.value), str(excinfo.value)


def test_NO_lease_in_the_tree_still_models_a_relayed_lane():
    """THE POPULATION, not the two names I happened to fix.

    #2219's first half repaired one call site by hand and the census was never
    taken, so two survivors kept the defect. This asserts over every shipped
    `.py` in the plugin: nothing constructs a lease with `starts=` greater than
    one. A future call site that tries gets a ValueError at run time and this
    at review time.
    """
    offenders = []
    scanned = 0
    for path in sorted(_PROGRAMS.rglob("*.py")):
        if path.name == "_session_floor.py":
            continue                      # the definition, not a call site
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        scanned += 1
        # A call made to PROVE the refusal is not a call that builds a lease.
        # The discriminator is structural, not a name exemption: everything
        # lexically inside a `with pytest.raises(...)` block is asserting that
        # the call fails, so by construction it cannot be holding a subject
        # under the result.
        proving = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if not any(
                    isinstance(i.context_expr, ast.Call)
                    and isinstance(i.context_expr.func, ast.Attribute)
                    and i.context_expr.func.attr == "raises"
                    for i in node.items):
                continue
            for inner in ast.walk(node):
                proving.add(id(inner))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in proving:
                continue
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else None)
            if name != "stall_window":
                continue
            for kw in node.keywords:
                if (kw.arg == "starts"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, int)
                        and kw.value.value > 1):
                    offenders.append(
                        f"{path.relative_to(_PROGRAMS)}:{node.lineno}")
    # OVER THE AST, NEVER OVER THE TEXT. A text scan reddens on its own
    # citation: the first half's comment at `test_pytest_per_file_junit.py:798`
    # says "NOT `stall_window(2.5, starts=2)`" and this file's own docstring
    # quotes the defect throughout. MEASURED while writing this: a regex
    # version reported NINE offenders of which SEVEN were prose and one was
    # itself, and it would have been greened by deleting the explanation.
    assert scanned > 100, scanned      # the scan really walked the tree
    assert not offenders, (
        "these leases still model a relayed lane as interpreter start-ups, "
        "which vibe-ic#2219 measured wrong: " + ", ".join(offenders))


def test_BOTH_relayed_call_sites_take_a_measured_window():
    """THE CALL SITES, by name, because a census passes on an empty tree too.

    The two tests whose leases are renewed through `--progress-relay` must ask
    `relay_window` for the number. Restoring either to `stall_window` reddens
    here as well as raising at run time.
    """
    called = _calls_in(_TESTS / "test_pytest_per_file_junit.py")
    for name in ("test_nested_validated_progress_is_relayed_to_the_outer_session",
                 "test_nested_collect_progress_is_relayed_to_the_outer_session"):
        assert "relay_window" in called[name], (name, sorted(called[name]))
        assert "stall_window" not in called[name], (name, sorted(called[name]))


def test_the_nested_mirror_RECEIVES_the_lease_instead_of_recomputing_it():
    """THE MEASUREMENT THAT MADE THE NAIVE FIX FAIL.

    `relay_window` takes its reading by really spawning a driver and a pytest.
    Inside the nested node that is in series with the silence the outer lease
    watches, so recomputing it there defeats the repair. The producer publishes
    and the consumer reads; both spell the key through `_session_floor` so a
    rename cannot leave them passing each other in the dark.
    """
    assert _floor.OUTER_LEASE_ENV == "VIBEIC_OUTER_LEASE_S"

    producer = _calls_in(_TESTS / "test_pytest_per_file_junit.py")[
        "test_nested_collect_progress_is_relayed_to_the_outer_session"]
    assert "OUTER_LEASE_ENV" in producer, sorted(producer)
    assert "setenv" in producer, sorted(producer)

    mirror = _calls_in(_TESTS / "test_flow_matrix_coverage.py")[
        "test_live_collection_relays_finite_semantic_progress_past_old_bound"]
    assert "OUTER_LEASE_ENV" in mirror, sorted(mirror)
    # ... and the standalone fallback is the SAME derivation the producer uses,
    # so the mirror cannot drift from the thing it mirrors.
    assert "relay_window" in mirror, sorted(mirror)
    assert "stall_window" in mirror, sorted(mirror)   # `old_fixed_bound`, starts=1


def test_the_mirror_generates_enough_work_for_a_bound_it_did_not_choose():
    """A LITERAL COUNT SILENTLY STOPS CROSSING A BOUND THAT MOVED.

    `files = 21` encoded `3.5 x old_fixed_bound` against an outer bound that was
    always exactly `2 x old_fixed_bound` — a 1.75x overshoot. `relay_window`
    reads a different quantity, so that ratio is no longer fixed: on a box whose
    relay lane is slow relative to its interpreter start the literal stops
    crossing, and the test's own precondition fires instead of the count moving.

    Worked at the reading #2219 recorded — session 0.60, relay 2.2041, so
    `old_fixed_bound` 1.2 and `file_seconds` 0.2 against an outer bound of
    4.408: 21 files buy 4.2 s, which does NOT cross it, and the derived count
    does.
    """
    import test_flow_matrix_coverage as _mirror_mod
    assert _mirror_mod._OUTER_CROSSING_MARGIN == 1.75
    names = _calls_in(_TESTS / "test_flow_matrix_coverage.py")[
        "test_live_collection_relays_finite_semantic_progress_past_old_bound"]
    # The count is DERIVED: it reads the margin and rounds up against the bound.
    assert "_OUTER_CROSSING_MARGIN" in names, sorted(names)
    assert "ceil" in names, sorted(names)

    # The arithmetic the count has to satisfy, stated here so the derivation is
    # checked rather than merely present.
    import math
    old_fixed_bound, outer_bound = 1.2, 4.408
    file_seconds = old_fixed_bound / 6
    assert 21 * file_seconds < outer_bound          # the literal fails
    files = max(21, math.ceil(1.75 * outer_bound / file_seconds))
    assert files * file_seconds > outer_bound       # the derivation does
