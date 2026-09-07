#!/usr/bin/env python3
"""A stale census now says WHICH published figure moved (vibe-ic#2142).

WHAT WENT WRONG
===============
`gen_flow_matrix_census.py --check` refused a drifted block with one sentence::

    .../flow_matrix/README.md census block is stale; re-run
    `python3 tools/gen_flow_matrix_census.py --fix`

and nothing else. MEASURED on 2026-09-07 at main `d644d7fb1`, in the pinned
image, corpus withheld: ONE cell of 621 had left the ENFORCED-undeclared column
for ENFORCED-CONTRADICTED — `undeclared 405 -> 404`, `contradicted 0 -> 1` — and
the refusal named neither column, neither number and neither dimension. The
reader's only way to learn what moved was to re-derive the census a second time
by hand, which is ~9 minutes of nested pytest on this repo, so the refusal cost
more to READ than the check cost to run.

A refusal that cannot say what it found is the shape this repository already
refuses everywhere else ("a verdict names what it looked at"). `census_drift`
reads BOTH blocks back into figures and prints one line per figure that moved.

WHAT THIS FILE LOCKS, both directions for each
==============================================
1. Two identical blocks produce NO lines — the reader is never handed a drift
   report over a fresh tree.
2. A block with one moved figure produces EXACTLY that figure's line, naming the
   row, the column, the committed value and the live one.
3. A block whose PROSE moved and whose figures did not still produces a line —
   the fallback. A drift report that is empty over two blocks that differ would
   be this file's own subject reached from the other side.
4. The real CLI, over a synthetic census, prints those lines on stderr and still
   exits 1. A helper that works while the caller never calls it is not a fix.

Run::

    cd .../plugins/vibe-ic && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \\
      python3 -m pytest programs/tests/test_issue2142_census_refusal_names_what_moved.py -q
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from _plugin_tree import repo_path_or_missing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

GEN = repo_path_or_missing("tools", "gen_flow_matrix_census.py")


def _gen_or_skip() -> Path:
    if not GEN.exists():
        pytest.skip(
            f"generator not present at {GEN} (mirror tree); the refusal's "
            f"wording is enforced in the source-of-truth tree only")
    return GEN


def _generator():
    """The generator, IMPORTED rather than launched, and never byte-compiled.

    `dont_write_bytecode` is not tidiness: an import writes `tools/__pycache__/`
    into the checkout, and `gates are host-independent` publishes the count of
    ignored paths a fresh worktree does not carry. A test may not enlarge the
    tree it is auditing.
    """
    spec = importlib.util.spec_from_file_location(
        "_gen_census_2142", str(_gen_or_skip()))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


#: A census small enough to read, shaped exactly like the real one: three
#: dimensions of four cells each, every cell in exactly one column so that
#: `render`'s own partition guards are satisfied by construction rather than by
#: a tolerance.
def _synthetic(undeclared_d2: int = 4, contradicted_d2: int = 0):
    rows = []
    for dim, name in ((1, "wiring"), (2, "falsifiable"), (3, "deps_correct")):
        row = {"dim": dim, "name": name, "question": f"question {dim}?",
               "own": 0, "substituted": 0, "undeclared": 4,
               "contradicted": 0, "not_measured": 0, "waived": 0, "na": 0}
        if dim == 2:
            row["undeclared"] = undeclared_d2
            row["contradicted"] = contradicted_d2
        rows.append(row)
    undeclared = sum(r["undeclared"] for r in rows)
    contradicted = sum(r["contradicted"] for r in rows)
    totals = {
        "cells": 12, "cells_per_dim": 4,
        "own": 0, "substituted": 0, "undeclared": undeclared,
        "contradicted": contradicted, "not_measured": 0, "waived": 0, "na": 0,
        "enforced": undeclared,
        "not_measured_agreed": 0, "waived_contradicted": 0,
        "na_contradicted": 0, "enforced_skipped": 0, "waived_skipped": 0,
        "na_skipped": 0,
    }
    return rows, totals


def test_two_identical_blocks_report_no_drift():
    gen = _generator()
    block = gen.render(*_synthetic())
    assert gen.census_drift(block, block) == [], (
        "a fresh block produced drift lines; a reader handed a drift report "
        "over an unmoved tree learns to ignore the next one")


def test_one_moved_figure_is_named_with_its_row_column_and_both_values():
    """THE MEASURED SHAPE: one cell leaves ENFORCED-undeclared for CONTRADICTED."""
    gen = _generator()
    committed = gen.render(*_synthetic(undeclared_d2=4, contradicted_d2=0))
    live = gen.render(*_synthetic(undeclared_d2=3, contradicted_d2=1))
    assert committed != live, "the mutation did not change the rendered block"
    lines = gen.census_drift(committed, live)
    assert "  d2.undeclared: committed 4 -> live 3" in lines, lines
    assert "  d2.contradicted: committed 0 -> live 1" in lines, lines
    assert "  total.undeclared: committed 12 -> live 11" in lines, lines
    assert "  total.contradicted: committed 0 -> live 1" in lines, lines
    # And it names the HEADLINE too, which is the figure a reader quotes.
    assert any(ln.startswith("  headline ENFORCED:") for ln in lines), lines
    # MEMBERSHIP, not a count: nothing that did NOT move may be named, because a
    # drift report that lists every figure is a report that names none of them.
    assert not any(".waived:" in ln or ".na:" in ln or ".own:" in ln
                   for ln in lines), lines
    assert not any(ln.startswith("  d1.") or ln.startswith("  d3.")
                   for ln in lines), lines


def test_a_drift_with_no_moved_figure_still_says_something():
    gen = _generator()
    committed = gen.render(*_synthetic())
    live = committed.replace("COVERAGE SHAPE", "COVERAGE-SHAPE")
    assert live != committed, "the prose mutation did not apply"
    lines = gen.census_drift(committed, live)
    assert len(lines) == 1 and "no PUBLISHED FIGURE moved" in lines[0], lines


def test_the_reader_refuses_a_row_it_does_not_understand():
    """`table_figures` reads ROWS, never every pipe-delimited line.

    The header and the alignment rule are pipe-delimited and carry the column
    names; a reader that took them would publish a row keyed `dim` with
    non-numeric figures and then compare it against a real one.
    """
    gen = _generator()
    figures = gen.table_figures(gen.render(*_synthetic()))
    assert set(figures) == {"1", "2", "3", "total"}, figures
    assert all(isinstance(v, int)
               for row in figures.values() for v in row.values()), figures


#: The real `main()`, over a SYNTHETIC census, in a subprocess — the same
#: interception `test_the_generator_cli_can_go_red_and_green` uses, so what is
#: measured is the shipped `--check` path and not a helper called by a test.
_CLI_PROBE = r"""
import runpy
import sys
import types

gen_path, out_path = sys.argv[1], sys.argv[2]


class _Verdict:
    def __init__(self, label):
        self.label = label


def _dims():
    from flow_matrix.cells import DIMENSIONS
    return DIMENSIONS


def enforcement_census():
    return {(step, dim): _Verdict(label)
            for dim in _dims()
            for step, label in (("1", "ENFORCED"),
                                ("2", "WAIVED"),
                                ("3", "NA"))}


def enforcement_census_with_record():
    return enforcement_census(), ()


def norecord_foreign_red_reason(foreign_reds):
    return "stub NORECORD: %r" % (list(foreign_reds)[:8],)


def substitution_census():
    from flow_matrix import substitution as SUB
    return {("1", dim): SUB.OWN_MECHANISM for dim in _dims()}


stub = types.ModuleType("test_flow_matrix_coverage")
stub.enforcement_census = enforcement_census
stub.enforcement_census_with_record = enforcement_census_with_record
stub.norecord_foreign_red_reason = norecord_foreign_red_reason
stub.substitution_census = substitution_census
sys.modules["test_flow_matrix_coverage"] = stub

sys.argv = ["gen_flow_matrix_census.py", "--check", "--out", out_path]
runpy.run_path(gen_path, run_name="__main__")
"""

BEGIN = ("<!-- BEGIN GENERATED CENSUS — tools/gen_flow_matrix_census.py — "
         "DO NOT EDIT BY HAND -->")
END = "<!-- END GENERATED CENSUS -->"


def test_the_check_cli_prints_the_named_figures_on_stderr_and_still_exits_1(
        tmp_path):
    gen = _gen_or_skip()
    readme = tmp_path / "README.md"
    readme.write_text(f"before\n{BEGIN}\nplaceholder\n{END}\nafter\n",
                      encoding="utf-8")

    # Write the block the stubbed census produces, so the tree is FRESH first.
    write = _pr.run([sys.executable, "-c",
                     _CLI_PROBE.replace('"--check", ', ""),
                     str(gen), str(readme)],
                    capture_output=True, text=True)
    assert write.returncode == 0, write.stdout + write.stderr
    fresh = readme.read_text(encoding="utf-8")

    green = _pr.run([sys.executable, "-c", _CLI_PROBE, str(gen), str(readme)],
                    capture_output=True, text=True)
    assert green.returncode == 0, (
        f"`--check` refuses a block it just wrote, so a 0 from it means "
        f"nothing.\n{green.stdout}\n{green.stderr}")

    # THE MUTATION: one figure of the total row, nothing else.
    m = re.search(r"\| \*\*(\d+)\*\* \|", fresh)
    assert m, fresh
    was = int(m.group(1))
    # THE DIGITS ONLY. `m.group(1)` is the number inside `| **N** |`, so the
    # surrounding `**` and the cell walls must survive: a splice that eats them
    # changes the row's CELL COUNT, `table_figures` then refuses the row it no
    # longer understands, and the drift is reported as `committed absent` for
    # every column — which is this file passing for the wrong reason.
    readme.write_text(
        fresh[:m.start(1)] + str(was + 1) + fresh[m.end(1):], encoding="utf-8")

    red = _pr.run([sys.executable, "-c", _CLI_PROBE, str(gen), str(readme)],
                  capture_output=True, text=True)
    assert red.returncode == 1, (
        f"`--check` returned {red.returncode} over a hand-edited figure.\n"
        f"{red.stdout}\n{red.stderr}")
    named = [ln for ln in red.stderr.splitlines()
             if "committed" in ln and "-> live" in ln]
    assert named, (
        f"`--check` refused without naming a single figure — the whole of "
        f"vibe-ic#2142.\n{red.stderr}")
    assert any(f"committed {was + 1} -> live {was}" in ln for ln in named), (
        f"the named figures do not include the one that was edited "
        f"({was + 1} -> {was}):\n" + "\n".join(named))
