#!/usr/bin/env python3
"""nested_progress_pin_check.py — the LANDING refuses a stale nested-progress
item pin, so a stale one can never reach main.

THIS GATE BLOCKS (rc=1).

THE DEFECT (vibe-ic#2138), MEASURED ON MAIN
===========================================
`tools/ci/trusted_test_selection.py` carries `HERMETIC_TEST_PROGRESS`, a
BASE-owned table whose `items` is "the exact collection denominator of the
protected file".  On `425c6402841d` it pinned 37 items for
`programs/tests/test_matrix_artefact_mutation_channel.py` while live collection
gave 38: `f5474d758` (2026-09-07) added
`test_an_absent_corpus_is_refused_by_name_and_not_by_errno` at ordinal 34 and
the pin was not moved with it.  The landing did not refuse, so the guard fired
on MAIN instead -- `tools/ci/test_trusted_test_selection.py::
test_nested_progress_schedule_matches_live_pytest_collection`, red in the pinned
image on both env arms, measured on 8HD-9.

A hand-fed count guarding a population that changes on every landing MUST go
stale.  "Every count current" is not a state anybody can hold; it is a state a
LANDING can be required to establish, one landing at a time.  That is what this
gate is: the same fact, asked of the tree that ships, at the moment somebody can
still fix it.

WHY THE PIN IS KEPT AND NOT DERIVED
===================================
The obvious alternative is to delete the hand-fed count and DERIVE it from live
collection at landing time.  It was considered and refused, for a reason that is
in the first line of the file that owns the table:

    "The selector implementation comes from the already raw-attested runtime
     state, never from an unreviewed subject checkout."

`trusted_test_selection.main()` spends `items` in `progress_plan()` for BOTH
arms -- `scope="pytest:A1"` over the BASE selection and `scope="pytest:B1"` over
the CANDIDATE selection -- and the plan is the stall lease the outer runner
judges the arm by.  Deriving the number would mean COLLECTING THE CANDIDATE'S
OWN TESTS to build the denominator the candidate is then judged against, in the
landing process, outside the hermetic arm.  A candidate that deleted items would
shrink its own denominator and the plan would agree with it; the ratchet against
silent test loss would be gone in the same move that removed the staleness.

So the pin stays BASE-owned and hand-fed, and the staleness is closed at the
other end: a landing may not install a tree whose pin disagrees with what that
tree collects.  The register keeps its ratchet AND cannot rot, because the only
way onto main is through a check that re-derives it.

THE CONSUMER THAT ALREADY KNEW, AND TURNED IT INTO A STALL
==========================================================
`programs/pytest_per_file_junit.py::_HermeticAggregateProgress.observe` already
compares live collection against `spec["items"]` and, on a mismatch, sets

    "parent-owned pytest item denominator differs for <file>"

which stops the progress relay.  A stopped relay is spent by the watchdog as a
STALLED arm (rc 199) -- so on a stale pin the information exists, and arrives as
a statement about the host rather than about the tree, naming neither number.
This gate turns the same fact into a verdict, before the arms start, with both
numbers in the text.

WHAT IS COMPARED, AND HOW
=========================
The pin is read from `--schedule` by `ast` and NEVER by import: the file it
names is a protected path that imports two siblings at module scope, and a gate
should not have to execute its subject to read a literal out of it.

The live count comes from ONE `pytest --collect-only -q` over all the declared
files, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`
-- byte-for-byte the command
`test_nested_progress_schedule_matches_live_pytest_collection` issues, because
two commands are two definitions of "the collection denominator" and they can
disagree.  That test now calls `collect_nodeids` here rather than keeping its
own copy.

BOTH SIDES COME FROM THE SUBJECT TREE, NOT FROM THE INSTRUMENT.  The question is
whether the tree that ships is self-consistent, so a landing that adds a test AND
moves the pin is green and a landing that adds one WITHOUT moving it is red.
Reading the pin from the base instead would refuse the very landing that repairs
it.  The candidate cannot cheat by moving the pin freely: the schedule is a
protected path and only PREPARE/ACTIVATE can move it.

EXIT CODES
==========
    0  every declared file's live collection equals its pin (the count is stated)
    1  at least one disagrees -- the file and BOTH numbers are named
    2  the gate could not look (unreadable schedule, empty table, a collection
       that did not complete).  "I could not read it" is never a pass, and an
       empty declared population is not a clean one.
    3  usage.

chip-AGNOSTIC: repository landing machinery only. No design, PDK, vendor or SKU.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _progress_run as _pr  # noqa: E402
import _gate_usage_exit as _usage  # noqa: E402

#: The table this gate reads, and the key inside each row that carries the
#: exact collection denominator. Named once so a rename of either is one edit.
TOOL = "nested_progress_pin_check"
TABLE = "HERMETIC_TEST_PROGRESS"
ITEMS_KEY = "items"


class Refusal(RuntimeError):
    """The gate could not reach a verdict about the subject (rc 2)."""


def pinned_items(schedule: Path) -> Dict[str, int]:
    """`{test-file: pinned item count}` read out of `schedule` by `ast`.

    The table's keys are module-level string CONSTANTS (`HERMETIC_ARTEFACT_FILE`
    and friends), so a bare `literal_eval` of the dict fails on the first key.
    Module-level `NAME = "literal"` assignments are resolved first and nothing
    else is: a key this function cannot resolve is a REFUSAL, never a skipped
    row -- a silently dropped row is a file this gate stops checking.
    """
    try:
        text = schedule.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refusal(f"cannot read the schedule {schedule}: {exc}") from exc
    try:
        tree = ast.parse(text, filename=str(schedule))
    except SyntaxError as exc:
        raise Refusal(f"cannot parse the schedule {schedule}: {exc}") from exc

    constants: Dict[str, str] = {}
    table: Optional[ast.Dict] = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            name = getattr(target, "id", "")
            if not name:
                continue
            if (isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and name not in constants):
                constants[name] = node.value.value
            elif name == TABLE and isinstance(node.value, ast.Dict):
                table = node.value
    if table is None:
        raise Refusal(
            f"{schedule} declares no module-level `{TABLE} = {{...}}`; this "
            f"gate reads that table and has nothing to compare without it")

    pinned: Dict[str, int] = {}
    for key, value in zip(table.keys, table.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            test_file = key.value
        elif isinstance(key, ast.Name) and key.id in constants:
            test_file = constants[key.id]
        else:
            raise Refusal(
                f"a {TABLE} key in {schedule} is neither a string literal nor a "
                f"module-level string constant this gate can resolve "
                f"({ast.dump(key)[:120]}); a row this gate cannot name is a "
                f"file it would stop checking in silence")
        if not isinstance(value, ast.Dict):
            raise Refusal(f"the {TABLE} row for {test_file} is not a dict")
        found = None
        for row_key, row_value in zip(value.keys, value.values):
            if (isinstance(row_key, ast.Constant)
                    and row_key.value == ITEMS_KEY):
                found = row_value
                break
        if not isinstance(found, ast.Constant) or type(found.value) is not int:
            raise Refusal(
                f"the {TABLE} row for {test_file} carries no integer "
                f"'{ITEMS_KEY}' literal")
        pinned[test_file] = found.value
    if not pinned:
        raise Refusal(
            f"{TABLE} in {schedule} is empty; a gate that compared zero files "
            f"would pass for the same reason a broken one does")
    return pinned


def collect_nodeids(plugin_root: Path, files: Sequence[str], *,
                    python: Optional[str] = None) -> Dict[str, List[str]]:
    """`{test-file: [nodeid, ...]}` from ONE live `pytest --collect-only`.

    In file/definition order, which is what an `ordinal` in the schedule
    indexes. Raises `Refusal` when the collection did not complete: a partial
    collection under-counts, and an under-count read as a verdict is a stale
    pin reported as a fresh one.
    """
    files = list(files)
    if not files:
        raise Refusal("no declared file to collect")
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc, stalled = _pr.run_or_undetermined(
        [python or sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", *files],
        cwd=str(plugin_root), env=env, capture_output=True, text=True)
    if proc is None:
        raise Refusal(f"the collection stopped making progress: {stalled}")
    if proc.returncode != 0:
        raise Refusal(
            "`pytest --collect-only` exited %d in %s, so the live population "
            "was never established. Its output follows.\n%s"
            % (proc.returncode, plugin_root,
               (proc.stdout or "") + (proc.stderr or "")))
    collected: Dict[str, List[str]] = {name: [] for name in files}
    for line in proc.stdout.splitlines():
        nodeid = line.strip()
        for name in files:
            if nodeid.startswith(name + "::"):
                collected[name].append(nodeid)
                break
    return collected


def disagreements(pinned: Mapping[str, int],
                  collected: Mapping[str, Sequence[str]]) -> List[str]:
    """One line per declared file whose live count differs from its pin."""
    findings: List[str] = []
    for test_file in sorted(pinned):
        live = len(collected.get(test_file, ()))
        if live != pinned[test_file]:
            findings.append(
                "%s: the schedule pins %d item(s), live collection gives %d"
                % (test_file, pinned[test_file], live))
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    ap = _usage.GateArgumentParser(
        prog=TOOL,
        description="refuse a landing whose nested-progress item pins "
                    "disagree with live pytest collection")
    ap.add_argument("--schedule", type=Path, required=True,
                    help="the file declaring %s (the tree that ships)" % TABLE)
    ap.add_argument("--plugin-root", type=Path, required=True,
                    help="the root the declared test files are relative to")
    args = ap.parse_args(argv)
    if not args.plugin_root.is_dir():
        return _usage.usage_error(
            TOOL, f"--plugin-root {args.plugin_root} is not a directory")

    try:
        pinned = pinned_items(args.schedule)
        collected = collect_nodeids(args.plugin_root, sorted(pinned))
    except Refusal as exc:
        print("[NOT_MEASURED] nested_progress_pin: %s" % exc, file=sys.stderr)
        print("  This gate did not examine the pins, so it has NOT found them "
              "correct. rc 2 says it could not look.", file=sys.stderr)
        return 2

    findings = disagreements(pinned, collected)
    for line in findings:
        print("[FAIL] nested_progress_pin: %s" % line, file=sys.stderr)
    if findings:
        print("[FAIL] nested_progress_pin: %d of %d declared file(s) carry a "
              "stale item pin in %s. Move the pin in the same landing that "
              "moved the population -- that file is a protected path, so the "
              "move goes through tools/ci/protected_landing_prepare.sh."
              % (len(findings), len(pinned), args.schedule), file=sys.stderr)
        return 1
    total = sum(len(nodes) for nodes in collected.values())
    print("[PASS] nested_progress_pin: %d declared file(s) examined, %d item(s) "
          "collected; every pin equals its live collection."
          % (len(pinned), total), file=sys.stderr)
    return 0


if __name__ == "__main__":
    # A stall is not a verdict about the subject: it reaches the exit code as
    # rc 2 (UNDETERMINED), announced, never as a finding.
    raise SystemExit(_pr.exit_undetermined_on_stall(main))
