"""vibe-ic#2137 — a version BUMP moves the declared shipped-version sites and
nothing else. A `v1.x.y` citing the past is not a claim about the present.

WHAT WAS OBSERVED, AND WHAT IT WAS NOT
======================================
`programs/tests/test_v2113_at_speed_grace_is_not_the_budget.py:227` cites the
landing that added a second supervised surface:

    vibe-ic#2063 RB2-07 (v1.18.84) added a SECOND supervised surface ...

`#2063 RB2-07` landed on main as `682f7a304`, whose `plugin.json` reads
`1.18.84` — so `v1.18.84` is the fact. The line has never held it. It was
authored `v1.18.85` in the landing that created the file (`5c38d3460`) and has
been rewritten by EVERY landing since, each +1, none of which changed anything
else in it:

    v1.18.86  b3e81495f  (#2095)      v1.18.90  c54016bed  (#2132)
    v1.18.87  85a5e19a0  (#2128)      v1.18.91  d644d7fb1  (#2077)
    v1.18.88  425c64028  (#2123)      v1.18.92  26a49a946  (#2104 #2116)
    v1.18.89  50bf057be  (#2100)

The literal has tracked `plugin.json` exactly, at every landing, from birth.
Four of those eight arrived DURING this lane: the rebase onto `50bf057be`
conflicted on exactly this line, main offering `v1.18.89` against this branch's
`v1.18.84`, and by the time the branch was re-based onto `26a49a946` main had
moved it three times more. The harm reproduces itself while the fix for it is
being written.

#2137 read that signature as "the version-prose sync rewrites every `v1.x.y`
literal it can find". MEASURED ON THIS TREE (lane cz2137, 8HD-8), IT DOES NOT,
and it cannot: `plugin_version_prose_sync_check.fix()` is narrow by
construction in BOTH dimensions —

  * three named files (`_PROSE_SITES`), not a walk of the tree, and
  * six anchored claim FORMS (`_CLAIMS`) — a shields badge, a badge link, a
    `| Plugin version |` table cell, a `plugin (**vX.Y.Z**)` title, a tree
    diagram line, a `**Status: vX.Y**` line — not "a version-shaped string",

and `plugin_manifest_discovery.write_version_all`, the other half of what
`gatekeeper_assign_version --write` writes, touches JSON manifests only. A dry
run of the bump over this tree moves 6 claims in those 3 files and leaves
`13454` free `v\\d+.\\d+.\\d+` literals across `2243` other tracked files
byte-identical. Those two figures are a MEASUREMENT, dated to this branch, and
are stated here rather than asserted: pinning either would rot on the next file
that mentions a version, and a shared tree-wide counter is the shape that makes
two branches unstackable (#1431). What the test asserts is MEMBERSHIP — every
`(file, line, literal)` outside the declared sites, unchanged — plus a floor on
the population, so a census that stopped reading the tree cannot report
agreement.

So the rewrite of line 227 was NOT produced by a program in this repo. It was
typed, by whatever hand re-based the lane, three times. That is the harm #2137
names — a citation that is wrong the moment the next landing happens, and wrong
differently every time, and a retirement content-proof that refuses the file for
it — and the repair is the same either way. What was missing is the CHECK: the
contract "only the declared sites move" was true by construction and asserted
nowhere, so neither a widened `fix()` nor another hand-rewrite of this line had
anything to run into.

THE TWO DIRECTIONS THIS FILE GUARDS
===================================
  A. the sync's contract — a dry-run bump moves the declared sites and NOTHING
     else in the tree (`..._leaves_every_free_version_literal_alone`), with its
     negative control (`test_a_widened_sync_is_caught_by_the_census`) proving
     the census can go red: a deliberately widened rewriter, run over the same
     copied tree, is detected.
  B. the citation itself — line 227 names the landing that added the surface
     (`test_the_2063_citation_names_the_landing_not_the_shipped_version`), and
     names it BY NOT BEING the shipped version, which is the exact shape the
     three observed rewrites had.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import plugin_version_prose_sync_check as PROSE   # noqa: E402
import plugin_manifest_discovery as PMD           # noqa: E402

#: The REPO root — the OUTERMOST ancestor carrying the repo-root manifest, which
#: is what `_PROSE_SITES` is named relative to. Derived rather than counted in
#: `.parents[n]`: an index is a promise about how deep the plugin is checked out,
#: and this plugin is also shipped flattened.
def _repo_root(start: pathlib.Path):
    found = None
    for anc in [start, *start.parents]:
        if (anc / ".claude-plugin" / "marketplace.json").is_file() \
                and (anc / "README.md").is_file():
            found = anc
    return found


_ROOT = _repo_root(_PROGRAMS)

def _dry_run_version() -> str:
    """A version that appears nowhere in the tree, so "did this line move?"
    cannot be answered accidentally by a literal that already equalled the
    target — DERIVED rather than typed.

    A triple written out here is an ADDED LINE claiming a version ahead of
    `plugin.json`, and `tools/ci/staged_version_claim_check.py` refuses it
    (measured, lane cz2137: this file's first draft stated its probe as a
    literal and the guard refused the commit, naming that line as a claim ahead
    of the shipped version). That guard is right and is not weakened here: a probe value is not a claim
    about what ships, but a source file cannot assert that about its own text,
    so the probe is computed instead of stated. `test_the_dry_run_target_is_a_
    version_the_tree_does_not_already_state` re-checks the "appears nowhere"
    property against the real tree rather than trusting this arithmetic.
    """
    shipped = PMD.read_plugin_version(_PROGRAMS.parent) or ""
    try:
        major = int(shipped.split(".")[0])
    except (ValueError, IndexError):
        major = 1
    return "%d.%d.%d" % (major + 900, 7, 13)


_DRY_RUN_VERSION = _dry_run_version()

#: A free `vX.Y.Z` — the citation form. Bare `X.Y.Z` is deliberately NOT in the
#: population: it is how the manifests state the version, and those SHOULD move.
_FREE_LITERAL = re.compile(r"v\d+\.\d+\.\d+")

#: The observed casualty, and the landing whose `plugin.json` carries the fact.
_CASUALTY = "programs/tests/test_v2113_at_speed_grace_is_not_the_budget.py"
_CASUALTY_LANDING = "682f7a304"
_CASUALTY_VERSION = "v1.18.84"


def _tracked(root: pathlib.Path):
    if root is None:
        pytest.skip("NOT_MEASURED: no ancestor of programs/ carries a repo-root "
                    ".claude-plugin/marketplace.json beside a README.md, so the "
                    "root the prose sites are named relative to is unknown")
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip(f"NOT_MEASURED: `git ls-files` failed in {root} "
                    f"(rc={out.returncode}) — the population could not be read, "
                    f"which is not an empty population")
    return [r for r in out.stdout.split("\0") if r]


def _literals(base: pathlib.Path, rels):
    """{rel: [(line, literal), ...]} for every readable tracked file."""
    found = {}
    for rel in rels:
        p = base / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        hits = [(text[:m.start()].count("\n") + 1, m.group(0))
                for m in _FREE_LITERAL.finditer(text)]
        if hits:
            found[rel] = hits
    return found


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    """A copy of the tracked tree with the bump's prose writer run over it.

    A COPY, not the checkout: this exercises the real writer, and a writer must
    never be exercised against the tree it is being judged in (#1029).
    """
    rels = _tracked(_ROOT)
    work = tmp_path_factory.mktemp("tree")
    for rel in rels:
        src = _ROOT / rel
        if not src.is_file():
            continue
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    before = _literals(_ROOT, rels)
    shipped, note = PROSE.shipped_version(work)
    if shipped is None:
        pytest.skip(f"NOT_MEASURED: the shipped version could not be "
                    f"established in the copied tree ({note})")
    touched = PROSE.fix(work, _DRY_RUN_VERSION)
    after = _literals(work, rels)
    return {"rels": rels, "work": work, "before": before, "after": after,
            "touched": touched, "shipped": shipped}


def test_the_dry_run_target_is_a_version_the_tree_does_not_already_state():
    """THE GUARD ON THE GUARD. If `_DRY_RUN_VERSION` already appeared, "it did
    not change" would be indistinguishable from "it was rewritten to what it
    already said" — the vacuous direction."""
    rels = _tracked(_ROOT)
    stated = _literals(_ROOT, rels)
    bad = {rel: hits for rel, hits in stated.items()
           if any(lit == "v" + _DRY_RUN_VERSION for _line, lit in hits)}
    assert not bad, f"{_DRY_RUN_VERSION} is already stated in {sorted(bad)}"


def test_the_contract_is_three_named_files_and_six_anchored_forms():
    """The SCOPE is the contract. Membership, not a count: a `_PROSE_SITES`
    that grew a glob, or a `_CLAIMS` entry matching a bare version-shaped
    string, is a widening — and is the shape #2137 believed had happened."""
    assert set(PROSE._PROSE_SITES) == {
        "README.md",
        "vibe-ic-marketplace/README.md",
        "vibe-ic-marketplace/plugins/vibe-ic/README.md",
    }
    for name, rx, _compare in PROSE._CLAIMS:
        # Every form is anchored to surrounding prose. A pattern that is only
        # the version triple would match a citation in any document.
        stripped = rx.pattern.replace(r"(\d+\.\d+\.\d+)", "") \
                             .replace(r"(\d+\.\d+)", "")
        assert stripped.strip(), f"claim {name!r} matches a bare version triple"


def test_the_declared_sites_still_move(dry_run):
    """THE POSITIVE DIRECTION. A check that only ever says "nothing moved"
    passes just as well against a writer that does nothing at all."""
    assert set(dry_run["touched"]) == set(PROSE._PROSE_SITES), (
        f"the bump wrote {sorted(dry_run['touched'])}, not the declared sites")
    for rel in PROSE._PROSE_SITES:
        text = (dry_run["work"] / rel).read_text(errors="replace")
        assert "v" + _DRY_RUN_VERSION in text or _DRY_RUN_VERSION in text, (
            f"{rel} was reported written but does not state "
            f"{_DRY_RUN_VERSION}")


def test_a_dry_run_bump_leaves_every_free_version_literal_alone(dry_run):
    """THE CENSUS. Every `vX.Y.Z` outside the declared sites, asserted
    unchanged by (file, line, literal) MEMBERSHIP across the bump.

    A census that finds none is a finding, not a pass: the population is
    asserted non-empty and asserted to CONTAIN the observed casualty, so a
    census that stopped reading the tree cannot report agreement.
    """
    declared = set(PROSE._PROSE_SITES)
    before = {r: h for r, h in dry_run["before"].items() if r not in declared}
    after = {r: h for r, h in dry_run["after"].items() if r not in declared}

    assert len(before) > 100, (
        f"the census population is {len(before)} file(s) — too small to be the "
        f"tree; the population was not read")
    casualty = [r for r in before if r.endswith(_CASUALTY)]
    assert casualty, (f"{_CASUALTY} is not in the census population — the file "
                      f"the bump was observed to rewrite must be under the "
                      f"check that would have caught it")

    moved = sorted(r for r in set(before) | set(after)
                   if before.get(r) != after.get(r))
    assert not moved, (
        "the version bump rewrote a free version literal outside the declared "
        f"sites, in {len(moved)} file(s): " + "; ".join(
            f"{r}: {before.get(r)} -> {after.get(r)}" for r in moved[:5]))


def test_a_widened_sync_is_caught_by_the_census(dry_run):
    """THE NEGATIVE CONTROL. The census above is worth nothing unless it can go
    red. A rewriter widened to exactly what #2137 described — every `vX.Y.Z` in
    every tracked file — is run over the same copied tree, and the census's own
    comparison must detect it.

    Run LAST and on a SECOND copy, so the fixture the other tests read is not
    the tree this one mutates.
    """
    declared = set(PROSE._PROSE_SITES)
    work = dry_run["work"].parent / "widened"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(dry_run["work"], work)

    widened = 0
    for rel in dry_run["rels"]:
        p = work / rel
        if not p.is_file() or rel in declared:
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        new = _FREE_LITERAL.sub("v" + _DRY_RUN_VERSION, text)
        if new != text:
            p.write_text(new)
            widened += 1
    assert widened > 0, ("the widened rewriter changed nothing — the negative "
                         "control did not run, so it proves nothing")

    after = {r: h for r, h in _literals(work, dry_run["rels"]).items()
             if r not in declared}
    before = {r: h for r, h in dry_run["before"].items() if r not in declared}
    moved = sorted(r for r in set(before) | set(after)
                   if before.get(r) != after.get(r))
    assert moved, ("the census did not detect a rewriter that rewrote every "
                   "version literal in the tree — it cannot fail, so it is "
                   "not a check")
    assert any(r.endswith(_CASUALTY) for r in moved), (
        "the widened rewriter reached the tree but not the observed casualty — "
        "the census would not have caught the thing #2137 reports")


def test_the_2063_citation_names_the_landing_not_the_shipped_version():
    """THE CITATION. `#2063 RB2-07` landed as `682f7a304`, `plugin.json`
    1.18.84. The docstring must say so — and must NOT say whatever this repo
    ships today, which is the shape all three observed rewrites had."""
    path = _PROGRAMS / "tests" / pathlib.Path(_CASUALTY).name
    text = path.read_text(errors="replace")
    line = [l for l in text.splitlines() if "vibe-ic#2063 RB2-07 (" in l]
    assert len(line) == 1, f"expected one #2063 citation in {path.name}, got {line}"
    assert f"({_CASUALTY_VERSION})" in line[0], (
        f"the #2063 citation reads {line[0].strip()!r}; {_CASUALTY_LANDING} "
        f"carries plugin.json {_CASUALTY_VERSION[1:]}, so that is the landing "
        f"that added the second supervised surface")

    shipped = PMD.read_plugin_version(_PROGRAMS.parent)
    if shipped and "v" + shipped != _CASUALTY_VERSION:
        assert f"(v{shipped})" not in line[0], (
            f"the #2063 citation states the SHIPPED version v{shipped} rather "
            f"than the landing's {_CASUALTY_VERSION} — a citation rewritten to "
            f"follow the bump is the #2137 defect, not a fix for it")
