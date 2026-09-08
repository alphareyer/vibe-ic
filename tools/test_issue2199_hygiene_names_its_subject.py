#!/usr/bin/env python3
"""vibe-ic#2199 — the dispatcher must NAME the tree it wants judged.

THE LOAD-BEARING HALF OF THE FIX IS ON THIS SIDE
================================================
The three programs now refuse when nobody tells them what to measure, and that
refusal is only useful if the caller that has the answer actually passes it.
``tools/ci/repo_hygiene_gates.sh`` did not, for::

    checker execution wiring            checker_execution_wiring_audit.py
    gates are wired to something        gate_is_wired_check.py
    declaration scans strip comments    hdl_declaration_scan_strips_comments_check.py

so each defaulted to a tree derived from its own ``__file__`` — the RUNTIME —
and published the answer as a verdict about ``$ROOT``. Where the two are one
directory that is invisible; where they differ it is a confident verdict about
a tree nobody asked about.

AND IT IS WHY NONE OF THE THREE COULD EVER HAVE A FIXTURE
=========================================================
``gate_mutation_fixtures.invoke`` builds a mutant subject and redirects the gate
at it by substituting ``$ROOT`` / ``$PLUGIN`` in the DECLARED ARGV (``_resolve_
argv``); ``$PG`` and ``$RUNTIME_ROOT`` deliberately keep pointing at the real
programs tree, because a fixture must drive the gate that lands. A row that
names no subject token therefore has nothing to substitute: both arms of a
fixture pair would run against the same real tree and return the same answer,
and the pair would execute forever without discriminating. All three of these
rows sit in ``tools/ci/gate_fixture_debt.json`` as ``NOT_WRITTEN_YET``; until
this change, writing them was not merely undone but impossible.

That is what the second test below asserts, and it asserts it the way the
engine does — by running the engine's own resolver, not by reading the shell
text with a second parser that could drift from it.

chip-AGNOSTIC: dispatcher plumbing only.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ci"))

import gate_mutation_fixtures as F  # noqa: E402

#: ``label -> (subject flag, the token that row must pass it)``. The token is
#: part of the claim: ``$PG`` and ``$RUNTIME_ROOT`` name the INSTRUMENT and are
#: exactly the values that would re-arm the defect while looking like a fix.
SUBJECT_ROWS = {
    "checker execution wiring": ("--repo-root", "$ROOT"),
    "gates are wired to something": ("--root", "$PLUGIN"),
    "declaration scans strip comments": ("--root", "$PLUGIN"),
}


def _decl(label: str):
    for d in F.declarations():
        if d.label == label:
            return d
    raise AssertionError(
        f"no gate labelled {label!r} in the dispatcher. If the row was renamed, "
        f"rename it here in the same commit: a test that cannot find its "
        f"subject silently stops asserting anything.")


@pytest.mark.parametrize("label", sorted(SUBJECT_ROWS), ids=lambda s: s)
def test_the_row_passes_a_subject_token(label):
    """The declaration hands the gate the SUBJECT, spelled as a subject."""
    flag, token = SUBJECT_ROWS[label]
    cmd = _decl(label).cmd
    assert flag in cmd, (
        f"{label!r} passes no {flag}, so the gate is left to default to its "
        f"own tree (vibe-ic#2199):\n    {cmd}")
    assert token in cmd, (
        f"{label!r} must pass {token} — the SUBJECT. $PG and $RUNTIME_ROOT "
        f"name the instrument, and handing one of those to the subject "
        f"argument is the same defect spelled out loud:\n    {cmd}")


@pytest.mark.parametrize("label", sorted(SUBJECT_ROWS), ids=lambda s: s)
def test_a_fixture_could_redirect_this_row(label, tmp_path):
    """The engine's own resolver must be able to point the row at a subject.

    `_resolve_argv` is what a mutation fixture uses to aim a gate at its mutant
    tree. If the resolved argv does not mention the subject anywhere, the two
    arms of a fixture pair are the same command over the same tree and the pair
    cannot discriminate however carefully it is written.
    """
    subject = tmp_path / "mutant-subject"
    subject.mkdir()
    argv = F._resolve_argv(_decl(label).cmd, subject)
    assert any(str(subject) in tok for tok in argv), (
        f"{label!r} resolves to an argv that never mentions the subject:\n"
        f"    {argv}\n"
        f"Both arms of a fixture pair would run this identical command against "
        f"the real tree (vibe-ic#2199).")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
