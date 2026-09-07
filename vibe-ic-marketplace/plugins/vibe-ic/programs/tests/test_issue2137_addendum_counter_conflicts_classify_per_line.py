#!/usr/bin/env python3
"""vibe-ic#2137 addendum — a conflict on nothing but GENERATED COUNTERS is
resolvable even when the counters live in a hand-authored document.

WHAT WAS MEASURED (lane czrailmeas / #2077, reproduced here in lane cz2137)
==========================================================================
`tools/resolve_generated_conflicts.sh` classified per PATH. The two READMEs are
hand-authored prose carrying the tree-wide counters `gen_program_inventory`
writes, so they are not in the whole-file registry — and every conflict in them
was reported `CONFLICT (real)`, refusing the WHOLE resolution including the
`INDEX.md` / `PROGRAM_INVENTORY.json` half that was resolvable.

Reproduced on this tree by merging a branch that adds one program with a branch
that adds two programs and a test:

    4 conflicted path(s)
      CONFLICT (real)  vibe-ic-marketplace/README.md
      CONFLICT (real)  vibe-ic-marketplace/plugins/vibe-ic/README.md
    [REFUSED] ... rc=1

and every conflicting line in both was a counter — `**1389**`/`**1390**` top
level, `1286`/`1287` catalogued, `4867`/`4869` *.py at any depth. Fail-closed,
so nothing wrong was ever committed; the cost is that every such rebase is
hand-resolved, and the one tool written for this conflict class did not cover
the two files its own docstring names first.

After the fix, the same merge resolves and the count it writes is **1391** —
NEITHER side's number (1389, 1390) but the merged tree's true one. That is the
proof this regenerates rather than picking a side, and it is asserted below in
`test_the_resolved_counter_is_the_merged_answer_not_either_side`.

WHY PER LINE IS NOT PER-LINE-REGEX
==================================
Two of the writer's own claim forms SPAN A NEWLINE:

    It is \\*\\*([\\d,]+) top-level Python\\nprograms\\*\\*
    the other\\n([\\d,]+) are helper modules and shims

Applying the patterns line by line finds neither, calls both halves unowned,
and refuses a conflict that is nothing but counters — measured in this lane:
the first cut of the fix still refused `plugins/vibe-ic/README.md` on exactly
those two forms. So the patterns are matched against the WHOLE document, the way
the writer matches them, and the matches are then mapped down to lines.
`test_a_claim_form_spanning_a_newline_is_still_owned` pins that.

WHAT THE BOUNDARY DOES NOT SEE, stated rather than discovered later
==================================================================
The write boundary is a `git diff --name-only`, so it names TRACKED paths the
generator modified. A generator that created a brand-new UNTRACKED file would
not appear in it. That is unchanged from the whole-file pass this extends, it is
not what #2137's addendum is about, and it is written down here so the next
reader does not have to infer the scope from the absence of a test.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generated_artifact_conflict_resolve as G   # noqa: E402
import gen_program_inventory as GPI               # noqa: E402
import _progress_run as _pr                       # noqa: E402


# ─── a synthetic partial artefact, so the unit tests stay fast ──────
#: A fake WRITER's claim forms. Typed here because this is a fake writer; the
#: REAL registry is asserted to be derived, never typed, in
#: `test_the_owned_forms_are_derived_from_the_real_writer`.
_FAKE_OWNED = (
    re.compile(r"^Total: (\d+)$", re.M),
    re.compile(r"It holds \*\*(\d+) items\*\*\nin all", re.M),
)
_FAKE = G.PartialArtifact(
    path="DOC.md", generator="gen.py",
    regenerate=("python3", "gen.py"), check=("python3", "gen.py", "--check"),
    owned=_FAKE_OWNED,
)


def _conflict(ours: list[str], theirs: list[str], *,
              before: list[str] = (), after: list[str] = ()) -> str:
    return "\n".join([*before, "<<<<<<< HEAD", *ours, "=======", *theirs,
                      ">>>>>>> branch", *after]) + "\n"


# THE SHAPE THE ADDENDUM NAMES: a counter line and a second counter line with
# PROSE IDENTICAL ON BOTH SIDES between them, all inside one hunk. git puts
# unchanged prose inside a conflict region whenever it lies between two lines
# that differ, and per-path (or per-hunk) classification reads that prose as
# disagreement.
_PROSE = "The catalogue is maintained by hand except for the counts."
COUNT_IN_A_PROSE_HUNK = _conflict(
    ["Total: 3400", _PROSE, "It holds **1286 items**", "in all."],
    ["Total: 3401", _PROSE, "It holds **1287 items**", "in all."],
)

# The same shape with ONE line the writer does not own, differing.
REAL_DISAGREEMENT = _conflict(
    ["Total: 3400", "License: Apache-2.0 (A)"],
    ["Total: 3401", "License: Apache-2.0 (B)"],
)


# ─── the classifier ─────────────────────────────────────────────────
def test_a_counter_sharing_a_hunk_with_identical_prose_is_resolvable():
    """THE MEASURED SHAPE. Prose identical on both sides is not disagreement."""
    assert G.conflict_is_generated_only(COUNT_IN_A_PROSE_HUNK, _FAKE_OWNED) is True
    assert _PROSE not in G.disputed_lines(
        *G.parse_conflict_hunks(COUNT_IN_A_PROSE_HUNK)[0]), (
        "the prose is identical on both sides and must not be disputed")


def test_a_real_disagreement_beside_a_counter_still_refuses():
    """THE HALF THAT HAS TO HOLD. A classifier that resolves everything is a
    silent `checkout --ours` with better manners."""
    assert G.conflict_is_generated_only(REAL_DISAGREEMENT, _FAKE_OWNED) is False


def test_an_unparseable_conflict_is_refused_and_is_not_the_same_as_refused():
    """`None`, not `False`: "I could not read this conflict" and "this conflict
    is a real disagreement" both refuse, but only one is a fact about the file.
    """
    torn = "<<<<<<< HEAD\nTotal: 1\n=======\nTotal: 2\n"        # no closing marker
    assert G.conflict_is_generated_only(torn, _FAKE_OWNED) is None
    assert G.parse_conflict_hunks(torn) is None


def test_a_claim_form_spanning_a_newline_is_still_owned():
    """The regression this lane actually hit: a per-line regex sees neither
    half of a two-line claim and refuses a counters-only conflict."""
    two_line_only = _conflict(
        ["It holds **1286 items**", "in all."],
        ["It holds **1287 items**", "in all."],
    )
    assert G.conflict_is_generated_only(two_line_only, _FAKE_OWNED) is True
    # ...and the writer really does ship such a form, so this is not a
    # hypothetical the fixture invented.
    # `\\n` as the two characters backslash-n: the writer stores its claim
    # forms as regex SOURCE, so the line break is an escape, not a newline
    # character. `re` matches it across a real line break all the same, which
    # is exactly why a per-line application of these patterns finds nothing.
    spanning = [pattern for _rel, _key, pattern in GPI._CLAIMS
                if "\\n" in pattern]
    assert spanning, ("no claim form spans a line break any more — if the "
                      "writer changed, this test's premise needs re-measuring, "
                      "not deleting")
    import re as _re
    for pattern in spanning:
        first, second = pattern.split("\\n", 1)
        assert not _re.compile(pattern).search(first.replace("\\", "")), (
            f"{pattern!r} matches its own first line alone; it does not "
            f"demonstrate the spanning case")


def test_classifying_the_whole_hunk_instead_of_its_disputed_lines_refuses_it():
    """THE MUTATION. Re-apply the rule this fix replaced — any line in the hunk
    that is not a counter condemns the hunk — and the measured shape must go
    back to REFUSED. A check that cannot fail is not a check."""
    hunks = G.parse_conflict_hunks(COUNT_IN_A_PROSE_HUNK)
    assert hunks, "the fixture must parse, or the mutation proves nothing"
    whole_hunk_verdict = all(
        any(rx.search(line) for rx in _FAKE_OWNED)
        for ours, theirs in hunks for line in [*ours, *theirs]
    )
    assert whole_hunk_verdict is False, (
        "the whole-hunk rule accepted the fixture, so this fixture does not "
        "discriminate between the two rules and proves nothing")
    assert G.conflict_is_generated_only(COUNT_IN_A_PROSE_HUNK,
                                        _FAKE_OWNED) is True


# ─── the decision ───────────────────────────────────────────────────
def test_the_default_call_still_refuses_a_counter_document():
    """THE DEFAULT IS THE OLD BEHAVIOUR. Widening must be asked for: with no
    partial registry and no conflict text, a counter document is foreign and
    the whole resolution is refused, exactly as before this existed."""
    v = G.decide(["DOC.md"], ())
    assert v.code == 1, f"{v.code}: {v.reason}"
    assert "DOC.md" in v.foreign and not v.partial


def test_a_counter_document_is_partial_only_when_its_conflict_says_so():
    resolvable = G.decide(["DOC.md"], (), (_FAKE,),
                          {"DOC.md": COUNT_IN_A_PROSE_HUNK})
    assert resolvable.code == 0, f"{resolvable.code}: {resolvable.reason}"
    assert resolvable.partial == ("DOC.md",) and not resolvable.foreign

    refused = G.decide(["DOC.md"], (), (_FAKE,),
                       {"DOC.md": REAL_DISAGREEMENT})
    assert refused.code == 1, f"{refused.code}: {refused.reason}"
    assert refused.foreign == ("DOC.md",) and not refused.partial

    unreadable = G.decide(["DOC.md"], (), (_FAKE,), {"DOC.md": None})
    assert unreadable.code == 1, f"{unreadable.code}: {unreadable.reason}"
    assert unreadable.foreign == ("DOC.md",)


def test_one_real_disagreement_still_refuses_the_whole_resolution():
    """No partial resolution: the resolvable half must not hide the other."""
    v = G.decide(["DOC.md", "OTHER.md"], (), (_FAKE,),
                 {"DOC.md": COUNT_IN_A_PROSE_HUNK})
    assert v.code == 1, f"{v.code}: {v.reason}"
    assert "OTHER.md" in v.foreign


# ─── the real registry is DERIVED, not typed ────────────────────────
def test_the_owned_forms_are_derived_from_the_real_writer():
    """MEMBERSHIP against `gen_program_inventory._CLAIMS`, never a count.

    A claim form added to the writer is inside this boundary automatically;
    a regex typed in the resolver would be a second implementation of the rule
    the writer owns, which is the failure that file already records.
    """
    want: dict[str, set[str]] = {}
    for rel, _key, pattern in GPI._CLAIMS:
        want.setdefault("vibe-ic-marketplace/" + rel, set()).add(pattern)
    got = {a.path: {rx.pattern for rx in a.owned} for a in G.PARTIAL_REGISTRY}
    assert got == want, "the partial registry is not the writer's own claim set"

    # The two documents the reproduction refused are exactly these.
    assert set(got) == {
        "vibe-ic-marketplace/README.md",
        "vibe-ic-marketplace/plugins/vibe-ic/README.md",
    }

    # `_NOT_A_POPULATION_COUNT` is deliberately EXCLUDED: those are numbers the
    # writer states it does not own, so a disagreement on one is real.
    excluded = {p for _r, _k, p in GPI._NOT_A_POPULATION_COUNT}
    owned_all = {p for pats in got.values() for p in pats}
    assert not (excluded & owned_all), sorted(excluded & owned_all)


def test_every_partial_entry_points_at_a_file_and_a_generator_that_exist():
    # programs/tests/<this> -> tests, programs, vibe-ic, plugins,
    # vibe-ic-marketplace, <repo root>. Derived rather than trusted: the
    # registry paths are repo-relative, so the root is the ancestor they
    # resolve under, and a wrong index here reads as a skip — a NOT_MEASURED
    # dressed as a pass.
    here = Path(__file__).resolve()
    root = next((a for a in here.parents
                 if (a / G.PARTIAL_REGISTRY[0].path).is_file()), None) \
        if G.PARTIAL_REGISTRY else None
    if root is None:
        pytest.skip("NOT_MEASURED: no ancestor holds the registry's paths — "
                    "this is the flattened plugin cache, where the partial "
                    "registry is empty by design")
    for a in G.PARTIAL_REGISTRY:
        assert (root / a.path).is_file(), a.path
        assert (root / a.generator).is_file(), a.generator
        # The prose-writing run, not --artifact-only: these lines ARE prose.
        assert "--artifact-only" not in a.regenerate, a.regenerate
        assert "--check" in a.check, a.check


# ─── end to end, on a real git conflict ─────────────────────────────
GEN_SRC = '''#!/usr/bin/env python3
"""Synthetic writer: owns the two counters in DOC.md, nothing else."""
import re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DOC = ROOT / "DOC.md"


def counts():
    n = len(list((ROOT / "items").glob("*.txt")))
    return n, n * 2


def render(text):
    n, m = counts()
    text = re.sub(r"^Total: \\d+$", f"Total: {n}", text, flags=re.M)
    text = re.sub(r"It holds \\*\\*\\d+ items\\*\\*\\nin all",
                  f"It holds **{m} items**\\nin all", text)
    return text


def main():
    have = DOC.read_text()
    want = render(have)
    if "--check" in sys.argv:
        if have != want:
            print("DOC.md counters are stale", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(want)
    return 0


raise SystemExit(main())
'''

DOC_SRC = """# The catalogue

Total: 1

The catalogue is maintained by hand except for the counts.

It holds **2 items**
in all.

License: Apache-2.0
"""


def _git(repo: Path, *args: str):
    return _pr.run(("git", "-C", str(repo)) + args, capture_output=True,
                   text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "items").mkdir(parents=True)
    (r / "gen.py").write_text(GEN_SRC)
    (r / "DOC.md").write_text(DOC_SRC)
    (r / "items" / "a.txt").write_text("a\n")
    assert _git(r, "init", "-q", "-b", "main").returncode == 0
    _git(r, "config", "user.email", "t@localhost")
    _git(r, "config", "user.name", "t")
    assert _git(r, "add", "-A").returncode == 0
    assert _git(r, "commit", "-q", "-m", "base").returncode == 0
    return r


def _branch(repo: Path, name: str, items: list[str], license_note=None):
    assert _git(repo, "checkout", "-q", "-B", name, "main").returncode == 0
    for i in items:
        (repo / "items" / f"{i}.txt").write_text(f"{i}\n")
    if license_note is not None:
        p = repo / "DOC.md"
        p.write_text(p.read_text().replace(
            "License: Apache-2.0", f"License: Apache-2.0 ({license_note})"))
    cp = _pr.run(("python3", "gen.py"), cwd=str(repo), capture_output=True,
                 text=True)
    assert cp.returncode == 0, cp.stderr
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", name).returncode == 0


def _conflicted_merge(repo: Path, other: str) -> None:
    """Merge `other` into the current branch and REQUIRE that it conflicted."""
    cp = _git(repo, "merge", other)
    assert cp.returncode != 0, (
        "the merge did not conflict, so this test would pass for the wrong "
        f"reason: {cp.stdout}")


def test_the_resolved_counter_is_the_merged_answer_not_either_side(repo: Path):
    """THE PROOF IT REGENERATES. Each side wrote its own count; the resolved
    file must hold NEITHER, but the count the merged tree actually has."""
    _branch(repo, "one", ["b"])                 # 2 items  -> Total: 2
    _branch(repo, "two", ["c", "d"])            # 3 items  -> Total: 3
    assert _git(repo, "checkout", "-q", "one").returncode == 0
    _conflicted_merge(repo, "two")

    v = G.resolve(repo, (), partial_registry=(_FAKE,))
    assert v.code == 0, f"{v.code}: {v.reason}"
    assert v.partial == ("DOC.md",) and "DOC.md" in v.staged

    text = (repo / "DOC.md").read_text()
    assert "<<<<<<<" not in text and ">>>>>>>" not in text
    assert "Total: 4" in text, text        # a + b + c + d, neither 2 nor 3
    assert "It holds **8 items**" in text
    assert not [p for p in (_git(repo, "diff", "--name-only",
                                 "--diff-filter=U").stdout.splitlines()) if p]


def test_a_real_prose_disagreement_in_the_same_document_is_refused(repo: Path):
    """Same merge, plus both sides editing one line the writer does not own."""
    _branch(repo, "one", ["b"], license_note="A")
    _branch(repo, "two", ["c", "d"], license_note="B")
    assert _git(repo, "checkout", "-q", "one").returncode == 0
    _conflicted_merge(repo, "two")

    before = (repo / "DOC.md").read_text()
    v = G.resolve(repo, (), partial_registry=(_FAKE,))
    assert v.code == 1, f"{v.code}: {v.reason}"
    assert v.foreign == ("DOC.md",)
    assert (repo / "DOC.md").read_text() == before, (
        "a REFUSED resolution must leave the tree exactly as it found it")


def test_a_write_outside_the_declared_surfaces_is_refused(repo: Path):
    """THE BOUNDARY (#1029). A preparation step inside a resolution path is a
    way for the resolver to edit its own subject; anything the generator writes
    that is not a declared surface stops the resolution and is named."""
    _branch(repo, "one", ["b"])
    _branch(repo, "two", ["c", "d"])
    assert _git(repo, "checkout", "-q", "one").returncode == 0
    _conflicted_merge(repo, "two")

    # The generator gains a stray write to an ALREADY-TRACKED file nobody
    # declared. Tracked, because the boundary is a `git diff` and an untracked
    # stray would not appear in one — see the note in the module docstring.
    # Edited in place, WITHOUT `git add`, because staging during a conflicted
    # merge clears the unmerged state and there would be nothing to resolve.
    gen = repo / "gen.py"
    gen.write_text(gen.read_text().replace(
        "    DOC.write_text(want)",
        "    DOC.write_text(want)\n"
        "    (ROOT / 'items' / 'a.txt').write_text('clobbered\\n')"))

    v = G.resolve(repo, (), partial_registry=(_FAKE,))
    assert v.code == 2, f"{v.code}: {v.reason}"
    assert "items/a.txt" in v.reason, v.reason


def test_an_unrelated_dirty_file_does_not_refuse_a_clean_resolution(repo: Path):
    """The boundary asks what THIS RUN wrote, not what was dirty beforehand.

    Measured while proving this pass: a worktree diff taken only AFTER the
    generator ran named a file the OPERATOR had edited, and refused a
    resolvable conflict for it.
    """
    _branch(repo, "one", ["b"])
    _branch(repo, "two", ["c", "d"])
    assert _git(repo, "checkout", "-q", "one").returncode == 0
    _conflicted_merge(repo, "two")

    (repo / "gen.py").write_text((repo / "gen.py").read_text()
                                 + "\n# an unrelated local edit\n")

    v = G.resolve(repo, (), partial_registry=(_FAKE,))
    assert v.code == 0, f"{v.code}: {v.reason}"
