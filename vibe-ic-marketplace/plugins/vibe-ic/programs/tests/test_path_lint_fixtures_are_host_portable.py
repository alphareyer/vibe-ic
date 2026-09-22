#!/usr/bin/env python3
"""Shipped path-lint fixtures name no one's home directory (R1 pin).

`shipped_path_portability_check` R1 refuses a personal home path anywhere in
shipped source, for the same reason benchmark-data PR#4 was refused: a literal
`/home/<someone>/...` pins ONE machine's layout into a tree everybody else
clones, and it reads as a real value rather than as an example.

The two path-lint fixtures below each needed a path that is genuinely OUTSIDE
the project root, and each reached for a fictional home to get one. They do not
need a home directory for that — a sibling directory of the test's own
`tmp_path`, built for real, is outside containment on every host. That is what
R1's remedy means by "resolve from the caller's project argument".

The first test is the narrow RED-without/GREEN-with pin on those two files; the
second is the general sweep, so the next fixture that reaches for a home dir is
caught here and not only in the hygiene shard.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import shipped_path_portability_check as P  # noqa: E402

PLUGIN = PROGRAMS.parent
TESTS = Path(__file__).resolve().parent

#: The two fixtures the v1.14.71 hygiene shard named.
_THE_TWO = (
    "test_path_lint_accepts_project_staged_pdk.py",
    "test_path_lint_staged_verify_uses_real_root.py",
)


def _scan(path: Path):
    return P.scan_file(path, path.relative_to(PLUGIN))


def test_the_two_named_path_lint_fixtures_carry_no_personal_home() -> None:
    offenders = {}
    for name in _THE_TWO:
        f = TESTS / name
        assert f.is_file(), f"{name} moved — repoint this pin"
        hits = _scan(f)
        if hits:
            offenders[name] = [(h.line, h.path) for h in hits]
    assert offenders == {}, f"personal home path in shipped source: {offenders}"


def test_no_shipped_test_fixture_names_a_personal_home() -> None:
    """General sweep over the shipped test tree, not just the two known ones."""
    offenders = {}
    for f in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        hits = _scan(f)
        if hits:
            offenders[f.name] = [(h.line, h.path) for h in hits]
    assert offenders == {}, (
        "personal home path(s) in shipped test source — resolve from tmp_path, "
        f"an env var, or the project argument: {offenders}")


def test_the_fixtures_still_test_a_path_outside_containment() -> None:
    """The portable rewrite must not have made the path project-INTERNAL.

    A fixture that silences R1 by moving the foreign path inside the project
    root would leave both files green and the rung they pin untested, so this
    asserts the replacement path is still built beside the project, not in it.
    """
    for name in _THE_TWO:
        src = (TESTS / name).read_text()
        assert "outside_the_project" in src, (
            f"{name} no longer builds a path outside the project root")


def test_the_guard_still_bites_when_a_personal_home_is_put_back(tmp_path) -> None:
    """THE CONTROL, and without it "the tree is clean" is indistinguishable from
    "the scanner stopped scanning".

    Every repair in this family removes the literal the guard looks for, so a
    green sweep is exactly what a broken scanner also produces. This puts one back
    -- in a copy, never in the tree -- and requires the scanner to name it.
    """
    victim = TESTS / "test_r0915_88b_the_inventory_is_read_where_the_run_read_it.py"
    assert victim.is_file(), f"{victim.name} moved — repoint this control"
    assert _scan(victim) == [], (
        "the file this control mutates is already red; fix that first, or the "
        "arm below cannot tell a caught literal from an existing one")
    # ASSEMBLED, NOT WRITTEN. The sweep above scans THIS file too, so spelling a
    # personal home path here would make the control redden the very sweep it
    # exists to validate — measured: it did, on the first cut of this arm, at the
    # two lines that carried the literal. The probe's CONTENT carries it, which is
    # what the scanner reads; this file's source does not.
    home = "/" + "home" + "/somebody"
    probe = tmp_path / "probe.py"
    probe.write_text(victim.read_text().replace(
        'outside_the_project = tmp_path.parent / f"outside_{tmp_path.name}_lane_other"',
        f'outside_the_project = Path("{home}/_lane_other")', 1))
    hits = P.scan_file(probe, Path("probe.py"))
    assert hits, (
        "a literal personal home path was put back and the scanner did not name "
        "it — the sweep above proves nothing")
    assert any(home in h.path or h.path.startswith(home) for h in hits), (
        [h.path for h in hits])


def test_the_repaired_fixtures_still_point_outside_the_project(tmp_path) -> None:
    """The claim-preservation arm for THIS repair, the same property
    `test_the_fixtures_still_test_a_path_outside_containment` pins for the other
    two files: a rewrite that silenced R1 by moving the foreign path INSIDE the
    project would leave the re-anchoring rung untested.
    """
    src = (TESTS / "test_r0915_88b_the_inventory_is_read_where_the_run_read_it.py"
           ).read_text()
    assert "outside_the_project" in src, (
        "the re-anchoring fixtures no longer build a path outside the project")
    assert src.count("assert not recorded.is_relative_to(tmp_path)") >= 1, (
        "the fixtures must ASSERT the recorded path is outside the project, not "
        "merely look as if it is")


def test_a_lane_relative_citation_is_not_a_personal_path(tmp_path) -> None:
    """The other repair shape in this change: a MEASURED provenance note that
    cited an absolute home path now cites `<lane root>` plus the lane, run, size,
    net count and time. The measurement survives; the machine layout does not."""
    probe = tmp_path / "note.py"
    probe.write_text(
        "# MEASURED on spm run16L (lane icspm5): the boundary wrote\n"
        "#   <lane root>/unrouted_after_postroute_spef_extract.txt\n"
        "# -- 734 bytes, 6 nets, at 03:07:20.\n")
    assert P.scan_file(probe, Path("note.py")) == []
