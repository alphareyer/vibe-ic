"""No test climbs out of `programs/tests/` to read a fixture.

WHY THIS EXISTS, and it is my own miss. vibe-ic#1391 re-homed 14 intake fixtures
from `plugins/vibe-ic/tests/phase1_fixtures/` -- a tree outside
`pytest.ini`'s `testpaths = programs/tests` -- into
`programs/tests/fixtures/phase1_local/`. I found the consumers with a grep whose
output I had truncated with `head -20`, saw three of the eight, and left FIVE
test files reading a path that no longer exists:

    test_l22_acceptance_table_targets.py        acceptance_targets_local
    test_scalar_reset_binding.py                scalar_reset_binding_local
    test_named_register_fields.py               named_register_fields_local
    test_reset_current_design_semantics.py      reset_historical_comparison
    test_explicit_top_subject.py                explicit_top_context_local

each failing with FileNotFoundError on a path ending
`plugins/vibe-ic/tests/phase1_fixtures/...`. A grep is not a population, and the
fix for that is not a more careful grep -- it is a check that DERIVES the
population and cannot be truncated.

WHAT IS FORBIDDEN, narrowly: a `parents[2]` (or wider) climb combined with a
`tests/` path segment, in a file under `programs/tests/`. That is the shape that
reaches the phantom tree. A test may still climb to reach the PROGRAMS directory
(`parents[1]`), which nearly every file here does to import its subject, and it
may read anything under its own `fixtures/`.
"""
import ast
import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: `parents[N]` for N >= 2 in the same expression as a `tests/`-prefixed literal.
_CLIMB = re.compile(r"parents\[([2-9])\]")


def _offenders():
    out = []
    for f in sorted(TESTS.glob("test_*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:                                  # pragma: no cover
            continue
        # Only STRING LITERALS count, so a docstring that merely discusses the
        # old layout (several do, deliberately, as history) is not an offender.
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            if not node.value.startswith("tests/"):
                continue
            line = src.splitlines()[node.lineno - 1]
            if _CLIMB.search(line):
                out.append((f.name, node.lineno, node.value[:60]))
    return out


def test_the_population_is_not_empty():
    """Non-vacuity: a glob that found nothing would make this pass forever."""
    assert len(list(TESTS.glob("test_*.py"))) > 400, len(
        list(TESTS.glob("test_*.py")))


def test_no_test_climbs_out_of_the_testpath_to_read_a_fixture():
    offenders = _offenders()
    assert offenders == [], (
        "these test(s) reach outside programs/tests for a fixture, which is the "
        "shape vibe-ic#1391 removed: "
        + "; ".join(f"{n}:{ln} -> {v!r}" for n, ln, v in offenders))


def test_the_re_homed_corpus_is_where_the_consumers_now_look():
    """The other half: the destination really holds the 14 files, so a green
    result above cannot mean the corpus vanished instead."""
    home = TESTS / "fixtures" / "phase1_local"
    assert home.is_dir(), home
    files = sorted(p.relative_to(home).as_posix()
                   for p in home.rglob("*") if p.is_file())
    assert len(files) == 14, files
    assert "_pending.json" in files
    for project in ("acceptance_targets_local", "scalar_reset_binding_local",
                    "named_register_fields_local", "reset_historical_comparison",
                    "explicit_top_context_local"):
        assert any(f.startswith(project + "/") for f in files), project
