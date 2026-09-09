#!/usr/bin/env python3
"""vibe-ic#2227 — the zero-denominator gate could not see the advisory censuses.

`gate_zero_denominator_refuses_check` exists to stop ONE shape: a program that
read NOTHING and exited 0. Its population came entirely from
`gate_discloses_denominator_check.project_population`, which selects two ways
and is blind to this class both times — by name it takes five checker suffixes
(`*_census.py` is not one), and by behaviour it takes a `[PASS]`/`[FAIL]`
banner, which a census does not print.

MEASURED on live main 9c653d47f: population 716 (662 by suffix + 56 by
behaviour), 10 `*_census.py` in the tree, 4 inside the population, SIX outside.

And the class is the one that most needs it: a census's declared contract is
rc 0 WITH findings, so the zero-population refusal is its ONLY red — the entire
difference between a measurement and a sentence.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _PROGRAMS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _empty():
    return tempfile.TemporaryDirectory(prefix="issue2227_empty_")


# ===========================================================================
# The instance: a census must not answer over a population it never read
# ===========================================================================
def test_the_census_refuses_an_empty_root_even_with_a_stale_inventory_row():
    """THE DEFECT, driven rather than described.

    `explicit_argument_outranks_the_environment_pointer_census` placed its
    zero-population refusal under `if rc == 0:`. `rc` is set by the FINDINGS
    branches, one of which counts inventory rows that match nothing. A stale row
    is a datum from the inventory FILE and says nothing about the tree — yet it
    set rc=1, the refusal was skipped, and `if rc and not a.strict` returned 0.

    MEASURED on live main 9c653d47f with `--root <empty dir>`:

        modules parsed:            0
        inventory rows applied:    1
        [CENSUS] 1 inventory row(s) match nothing: ...
        >>> RC=0

    Zero read, exit 0. The shipped inventory carries exactly such a row, so this
    test needs no fixture to reproduce it — the tree does.
    """
    prog = _PROGRAMS / "explicit_argument_outranks_the_environment_pointer_census.py"
    with _empty() as d:
        r = subprocess.run([sys.executable, str(prog), "--root", d],
                           capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, (
        "the census parsed 0 modules and still exited 0 — a count over an empty "
        f"population is not a count:\n{out[-1500:]}")
    assert "CANNOT DETERMINE" in out, (
        f"it refused, but did not say it could not determine:\n{out[-1500:]}")


# THE PLACEMENT PIN THIS FILE ONCE CARRIED IS GONE, AND ON PURPOSE.
#
# It asserted source ORDER by text --
#     src.index("0 modules were parsed") < src.index("    if rc == 0:")
# -- to say "a refusal about the DENOMINATOR must not sit under a branch
# conditioned on the FINDINGS". The invariant is right. The instrument was a
# `str.index` over the module's source, so it measured a fixture rather than an
# execution, and it pinned ONE way of satisfying the invariant.
#
# While this branch waited, main closed the same root cause a better way
# (v1.20.19 onward): it made the refusal UNCONDITIONAL and deleted the
# `if rc == 0:` branch outright, rather than hoisting the refusal above it. The
# text pin then raised ValueError -- red against a STRICTER implementation of
# the very thing it was defending.
#
# Nothing is relaxed by dropping it: the case above,
# `test_the_census_refuses_an_empty_root_even_with_a_stale_inventory_row`,
# DRIVES the defect end to end. It runs the census on an empty root while the
# shipped inventory carries a row that matches nothing -- the exact input that
# set rc=1 and switched the refusal off -- and requires rc!=0 with
# CANNOT DETERMINE. Any placement that re-subordinates the denominator refusal
# to the findings fails it, including the one the text pin allowed.


def test_a_real_population_still_censuses_normally():
    """DIRECTION-2: the fix must change the EMPTY case and nothing else.

    Over the real tree this census reports and exits 0 — that is its declared
    contract. A repair that made it refuse here would be a different program.
    """
    prog = _PROGRAMS / "explicit_argument_outranks_the_environment_pointer_census.py"
    root = _PROGRAMS.parents[2]
    r = subprocess.run([sys.executable, str(prog), "--root", str(root)],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "modules parsed:" in (r.stdout or "")


# ===========================================================================
# The root cause: the gate can now reach the class
# ===========================================================================
def test_the_gate_has_a_second_population_and_it_is_not_empty():
    gz = _load("gate_zero_denominator_refuses_check")
    gdd = _load("gate_discloses_denominator_check")
    already = {p.name for p in gdd.project_check_programs(_PROGRAMS)}
    pop = gz.advisory_census_population(_PROGRAMS, already)
    assert pop, (
        "the advisory-census population is empty — the arm would probe nothing "
        "and its silence would read as approval")
    names = {p.name for p in pop}
    assert not (names & already), (
        "the second population overlaps the first; a program would be probed "
        "twice under two different contracts")


def test_the_population_is_structural_not_filename_shaped():
    """A population chosen by a NAME is the defect this repo already named.

    So the selector must take a file that prints a census banner whatever it is
    called, and must NOT take a `*_census.py` that prints none.
    """
    gz = _load("gate_zero_denominator_refuses_check")
    with tempfile.TemporaryDirectory(prefix="issue2227_pop_") as d:
        tree = Path(d)
        (tree / "not_named_census.py").write_text(
            'print("[CENSUS] 3 things")\nif __name__ == "__main__":\n    pass\n')
        (tree / "silent_census.py").write_text(
            'print("nothing to declare")\nif __name__ == "__main__":\n    pass\n')
        got = {p.name for p in gz.advisory_census_population(tree, set())}
    assert "not_named_census.py" in got, (
        "a program that prints a census banner was skipped because of its name")
    assert "silent_census.py" not in got, (
        "a program was selected for its FILENAME while printing no banner")


def test_a_census_that_exits_zero_over_an_empty_subject_is_a_finding():
    """THE VACUITY GUARD. A widened population proves nothing if no input can
    redden it, so an offender is constructed and the arm must refuse it."""
    gz = _load("gate_zero_denominator_refuses_check")
    with tempfile.TemporaryDirectory(prefix="issue2227_bad_") as d:
        tree = Path(d)
        (tree / "offender_census.py").write_text(
            "import argparse, sys\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--root', default=None)\n"
            "ap.parse_args()\n"
            "print('[CENSUS] 0 site(s) classified')\n"
            "sys.exit(0)\n"
            'if __name__ == "__main__":\n    pass\n')
        findings, stats = gz.audit_advisory_censuses(tree, set(), timeout=120)
    kinds = {f["kind"] for f in findings}
    assert "CENSUS_OVER_AN_EMPTY_POPULATION_EXITS_ZERO" in kinds, (
        f"a census that read nothing and exited 0 was not flagged: {stats}")


def test_a_census_that_refuses_is_not_a_finding():
    """The other direction: refusing must be accepted, or the arm refuses
    everything and passing it means nothing."""
    gz = _load("gate_zero_denominator_refuses_check")
    with tempfile.TemporaryDirectory(prefix="issue2227_good_") as d:
        tree = Path(d)
        (tree / "good_census.py").write_text(
            "import argparse, sys\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--root', default=None)\n"
            "ap.parse_args()\n"
            "print('[CANNOT DETERMINE] nothing was read. NOT a pass.')\n"
            "sys.exit(2)\n"
            'if __name__ == "__main__":\n    pass\n')
        findings, stats = gz.audit_advisory_censuses(tree, set(), timeout=120)
    assert not findings, findings
    assert stats["censuses_refused"] == 1, stats


def test_an_undrivable_census_is_NOT_MEASURED_and_never_silently_clean():
    """A class the gate could not reach must not read as one it approved."""
    gz = _load("gate_zero_denominator_refuses_check")
    with tempfile.TemporaryDirectory(prefix="issue2227_nm_") as d:
        tree = Path(d)
        (tree / "undrivable_census.py").write_text(
            "import argparse, sys\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('subject')\n"
            "ap.add_argument('second')\n"
            "ap.parse_args()\n"
            "print('[CENSUS] x')\n"
            'if __name__ == "__main__":\n    pass\n')
        findings, stats = gz.audit_advisory_censuses(tree, set(), timeout=120)
    assert stats["censuses_not_measured"] == 1, stats
    assert stats["censuses_refused"] == 0, (
        "an unmeasured census was counted as one that refused")
    assert any("undrivable_census" in n
               for n in stats["censuses_not_measured_names"]), stats
    assert not findings, "NOT_MEASURED must not be reported as a defect either"
