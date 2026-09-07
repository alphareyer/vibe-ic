"""Unit tests for nested_progress_pin_check.py (vibe-ic#2138).

THE DEFECT, in one sentence: `HERMETIC_TEST_PROGRESS` pins the exact collection
denominator of four protected test files, `f5474d758` added a 38th item to one
of them and left the pin at 37, and the only guard that knew lives minutes away
in the full tier — so the stale pin reached MAIN and fired there.

Pinned here in the order the gate can be wrong:

  * BOTH DIRECTIONS ON THE SAME BYTES: one fixture tree, green with the pin at
    its live count and RED with a test appended and the pin unmoved. The red
    names the file and BOTH numbers, because a landing operator who is told only
    "stale" has to re-derive the population by hand;
  * a pin that moves the OTHER way — a test deleted, the pin left high — is red
    too. That is the ratchet the pin exists for and it must not be one-sided;
  * a table key it cannot resolve, a row with no integer `items`, an absent
    table and an EMPTY table are all rc 2 (NOT_MEASURED) and never rc 0: a gate
    that examined nothing must not be indistinguishable from a clean one;
  * a collection that did not complete is rc 2, not rc 1 — a partial collection
    under-counts, and an under-count spent as a verdict reports a fresh pin as
    stale;
  * `pinned_items` never IMPORTS the schedule. The real one is a protected path
    that executes two sibling imports at module scope, and a gate should not
    have to run its subject to read a literal out of it — so the fixtures here
    are deliberately files that would fail to import.

The fixture trees are synthetic on purpose: driving the gate against the real
schedule would make every one of these cases move whenever the real population
does, which is the very coupling the gate exists to break.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
GATE = PROGRAMS / "nested_progress_pin_check.py"

_SPEC = importlib.util.spec_from_file_location(
    "_probe_nested_progress_pin_check", GATE)
assert _SPEC and _SPEC.loader
N = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = N
_SPEC.loader.exec_module(N)


def _schedule(tmp_path: Path, body: str, *, preamble: str = "") -> Path:
    """A file shaped like the real schedule — and deliberately unimportable.

    The `from __vibeic_absent__ import *` is the point: every test here would
    still pass if the gate imported the file, right up until the day the real
    schedule grew an import the gate's environment does not have, at which
    point a landing gate would refuse a correct tree. Reading it by `ast`
    cannot acquire that failure mode, and this line is what proves the gate
    does not.
    """
    path = tmp_path / "schedule.py"
    path.write_text(
        "from __vibeic_absent__ import *  # noqa: F401,F403\n"
        + preamble + "\n" + body, encoding="utf-8")
    return path


def _plugin(tmp_path: Path, items: int, *, rel: str = "programs/tests/test_f.py",
            extra: str = "") -> Path:
    root = tmp_path / "plugin"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join("def test_n%d():\n    assert True\n\n\n" % i
                   for i in range(items))
    path.write_text(body + extra, encoding="utf-8")
    return root


_ONE_FILE = '''
OWNER = "programs/tests/test_f.py"
HERMETIC_TEST_PROGRESS = {
    OWNER: {
        "items": %d,
        "producer_items_without_schedule": (),
        "producer_profiles": (("replay_many",),),
        "domains": (),
    },
}
'''


def _run(schedule: Path, plugin_root: Path):
    return subprocess.run(
        [sys.executable, str(GATE), "--schedule", str(schedule),
         "--plugin-root", str(plugin_root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


# ── the two directions, on the same fixture bytes ──────────────────────────

def test_a_pin_that_equals_live_collection_passes(tmp_path):
    schedule = _schedule(tmp_path, _ONE_FILE % 3)
    proc = _run(schedule, _plugin(tmp_path, 3))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 item(s) collected" in proc.stderr


def test_a_test_added_without_moving_the_pin_is_refused(tmp_path):
    """The measured defect: a landing adds an item and leaves the count."""
    schedule = _schedule(tmp_path, _ONE_FILE % 3)
    proc = _run(schedule, _plugin(tmp_path, 4))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    # THE FILE AND BOTH NUMBERS. Asserted as three separate substrings rather
    # than one sentence, so a re-worded message that still carries the facts
    # does not redden this, and a message that drops one of them does.
    assert "programs/tests/test_f.py" in proc.stderr
    assert "pins 3 item(s)" in proc.stderr
    assert "gives 4" in proc.stderr


def test_a_test_deleted_without_moving_the_pin_is_refused_too(tmp_path):
    """The ratchet direction — the reason the pin is not simply derived."""
    schedule = _schedule(tmp_path, _ONE_FILE % 4)
    proc = _run(schedule, _plugin(tmp_path, 3))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "pins 4 item(s)" in proc.stderr
    assert "gives 3" in proc.stderr


def test_the_green_arm_is_not_green_by_looking_at_nothing(tmp_path):
    """A pass states its denominator, so a vacuous one is not readable as clean."""
    proc = _run(_schedule(tmp_path, _ONE_FILE % 2), _plugin(tmp_path, 2))
    assert proc.returncode == 0
    assert "1 declared file(s) examined" in proc.stderr


# ── everything that is NOT a verdict about the subject ─────────────────────

def test_an_empty_table_is_not_a_pass(tmp_path):
    schedule = _schedule(tmp_path, "HERMETIC_TEST_PROGRESS = {}\n")
    proc = _run(schedule, _plugin(tmp_path, 1))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "NOT_MEASURED" in proc.stderr


def test_an_absent_table_is_not_a_pass(tmp_path):
    schedule = _schedule(tmp_path, "SOMETHING_ELSE = {}\n")
    proc = _run(schedule, _plugin(tmp_path, 1))
    assert proc.returncode == 2
    assert "declares no module-level" in proc.stderr


def test_an_unresolvable_key_is_refused_and_not_skipped(tmp_path):
    """A row the gate cannot name is a file it would stop checking in silence."""
    body = _ONE_FILE % 3
    schedule = _schedule(tmp_path, body.replace("OWNER:", "OWNER.upper():"))
    proc = _run(schedule, _plugin(tmp_path, 3))
    assert proc.returncode == 2
    assert "neither a string literal nor a module-level string constant" \
        in proc.stderr


def test_a_row_with_no_integer_items_is_refused(tmp_path):
    body = (_ONE_FILE % 3).replace('"items": 3,', '"items": "3",')
    proc = _run(_schedule(tmp_path, body), _plugin(tmp_path, 3))
    assert proc.returncode == 2
    assert "carries no integer" in proc.stderr


def test_a_collection_that_does_not_complete_is_rc2_not_rc1(tmp_path):
    """An under-count must not reach a reader as a stale pin."""
    root = _plugin(tmp_path, 3, extra="this is not python\n")
    proc = _run(_schedule(tmp_path, _ONE_FILE % 3), root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "the live population was never established" in proc.stderr.replace(
        "\n", " ").replace("  ", " ")


def test_a_missing_plugin_root_is_a_usage_error_not_a_finding(tmp_path):
    proc = _run(_schedule(tmp_path, _ONE_FILE % 3), tmp_path / "absent")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "USAGE_ERROR:" in proc.stderr


# ── the unit surface the landing test also calls ───────────────────────────

def test_pinned_items_reads_a_constant_key_without_importing(tmp_path):
    schedule = _schedule(tmp_path, _ONE_FILE % 7)
    with pytest.raises(ModuleNotFoundError):
        # THE CONTROL for the docstring claim above: this file genuinely cannot
        # be imported, so `pinned_items` reading it proves it does not import.
        spec = importlib.util.spec_from_file_location("_unimportable", schedule)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    assert N.pinned_items(schedule) == {"programs/tests/test_f.py": 7}


def test_collect_nodeids_keeps_definition_order(tmp_path):
    """An `ordinal` in the schedule indexes this list, so its order is load-bearing."""
    root = tmp_path / "plugin"
    path = root / "programs/tests/test_f.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def test_zulu():\n    assert True\n\n\n"
        "def test_alpha():\n    assert True\n", encoding="utf-8")
    got = N.collect_nodeids(root, ["programs/tests/test_f.py"])
    assert got["programs/tests/test_f.py"] == [
        "programs/tests/test_f.py::test_zulu",
        "programs/tests/test_f.py::test_alpha"]


def test_disagreements_names_every_offender_not_only_the_first():
    findings = N.disagreements(
        {"a.py": 1, "b.py": 2, "c.py": 3},
        {"a.py": ["x"], "b.py": [], "c.py": ["x", "y", "z"]})
    assert len(findings) == 1 and "b.py" in findings[0]
    findings = N.disagreements(
        {"a.py": 1, "b.py": 2}, {"a.py": [], "b.py": []})
    assert len(findings) == 2
