#!/usr/bin/env python3
"""refuse a push of a contributor branch that carries its own version bump —
the version is assigned by the lander, at landing, and a branch that states one
either collides with that assignment or silently rolls it back.

WHAT WENT WRONG (vibe-ic#2096)
------------------------------
Measured by lane czstarve4, 2026-09-07: a version bump rode into two of its own
commits. Running the plugin test suite WRITES the version-bearing files, and
``git add -A`` swept them into the commit. Nothing refused it. The lane noticed
by hand, before pushing, and restored them; the next lane will not.

Reproduced here on the frozen base cf316de71840 (v1.18.52) by driving the real
``tools/git-hooks/pre-push`` against a ``next/*`` branch whose delta bumps
1.18.52 -> 1.18.53 across all six files ``gatekeeper_assign_version --write``
touches:

    hook rc = 0        <- the branch pushes

Both directions are damage, and they are not the same damage:

  * a FORWARD bump claims a number that is not the branch's to assign. Two
    branches in flight claim the same one, and the lander's own assignment then
    has to fight the branch's.
  * a BACKWARDS version is worse, because it is silent: when the branch's delta
    is applied the shipped version ROLLS BACK, ``/plugin update`` sees a version
    it has already seen, and every installed copy stays on a stale cache.

WHAT WAS ALREADY GUARDED, AND WHAT WAS NOT
------------------------------------------
``version_bump_monotonic_check --version-by-gatekeeper`` (which the hook already
runs off-main) refuses a REGRESSION and passes an unchanged version — that is
the documented contributor path and it is correct. It says nothing about a
forward bump, which it must PASS, because on the landing path a forward bump is
exactly what is wanted. And it reads only ``plugin.json`` and one
``marketplace.json``: the three shipped READMEs state the version in prose and
were not read at all.

So the hole is: forward bump, anywhere; and any bump at all in prose.

THE RULE, IN ONE SENTENCE
-------------------------
For every version-bearing site, if the HEAD of the branch declares a version
value that the fork point with ``main`` did not declare at that site, the push
is refused and the finding names the site, both values, and who assigns the
version.

Consequences of stating it that way, each deliberate:

  * a branch that touches ``README.md`` without touching its version claim is
    NOT refused. A guard that fires on any edit to a version-bearing file would
    refuse ordinary prose work, and a hook that refuses ordinary work is a hook
    that gets ``--no-verify``'d.
  * a branch that REMOVES a claim is not refused. That is not a version
    assertion; ``plugin_version_prose_sync_check`` owns claim sync.
  * a branch that re-states the SAME number it forked from is not refused.
  * the comparison is against the MERGE BASE with ``main``, not against main's
    tip. A branch cut from an older main declares an older version by
    inheritance and has asserted nothing; comparing to the tip would report that
    inheritance as a rollback on every stale branch.

WHERE THE FILE LIST COMES FROM — NOT FROM A LIST IN THIS FILE
--------------------------------------------------------------
The sites are derived from the SAME two helpers that ``gatekeeper_assign_version
--write`` writes through: ``plugin_manifest_discovery.find_plugin_and_manifests``
for the manifests and ``plugin_version_prose_sync_check`` for the prose claims.
A hand-copied list here would be correct on the day it was typed and would go
stale the first time the writer learned a seventh site — the guard would then
pass exactly the bump it exists to refuse, and nothing would say so.
``tests/test_issue2096_branch_version_bump_guard.py`` drives the real writer and
asserts the two sets are equal, so the two cannot drift apart silently.

WHO IS EXEMPT
-------------
``refs/heads/main`` and the landing branch (``refs/heads/land/<sha>``, the shape
``tools/ci/gatekeeper_protect_main.sh`` documents; ``land_*`` is accepted too).
Those carry the lander's own assignment and must state a version. Everything
else is guarded, including a destination this program could not parse — the
unsafe direction of an unknown ref is to let it through.

Exit codes
----------
    0   PASS — no site declares a version the fork point did not, or the
            destination is exempt, or the repo declares no version at all
            (a determination, printed as such, not a fail-open).
    1   FAIL — the branch declares a version. The finding names every site.
    2   ERROR — the question could not be put: not a git repo, no resolvable
            base, or a tracked site that could not be read at a revision.

chip-AGNOSTIC.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_PROGRAMS_DIR = Path(__file__).resolve().parent
if str(_PROGRAMS_DIR) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS_DIR))

import plugin_manifest_discovery as _pmd            # noqa: E402
import plugin_version_prose_sync_check as _prose    # noqa: E402
import gatekeeper_assign_version as _gav            # noqa: E402
import _prose_polarity as _pol                      # noqa: E402  (#712 vocabulary)

TOOL = "branch_version_bump_guard"
RC_OK, RC_FINDINGS, RC_CANNOT_CHECK = 0, 1, 2

#: Destinations that MAY state a version, because the lander assigns it there.
#: `main` is exact; the landing branch is `refs/heads/land/<short-sha>` per
#: `tools/ci/gatekeeper_protect_main.sh`, and `land_` is accepted as the same
#: intent spelled with an underscore (the wording vibe-ic#2096 uses).
_EXEMPT_EXACT = ("refs/heads/main",)
_EXEMPT_PREFIXES = ("refs/heads/land/", "refs/heads/land_")

#: Tried in order when no `--base` resolves. The fork point is taken against
#: whichever of these exists; a branch is always cut from one of them.
_DEFAULT_BASES = ("origin/main", "main")


def destination_is_exempt(dest_ref: Optional[str]) -> bool:
    """True iff `dest_ref` is a destination that may carry a version.

    An absent or unparsed destination is NOT exempt: the safe reading of
    "I do not know where this is going" is the one that still asks the question.
    """
    if not dest_ref:
        return False
    if dest_ref in _EXEMPT_EXACT:
        return True
    return any(dest_ref.startswith(p) for p in _EXEMPT_PREFIXES)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def _rev_parse(repo: Path, rev: str) -> Optional[str]:
    p = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    out = p.stdout.strip()
    return out if p.returncode == 0 and out else None


def resolve_fork_point(repo: Path, head: str,
                       base: Optional[str]) -> Tuple[Optional[str], str]:
    """``(sha, note)`` — the commit the branch was cut from.

    `base` (or, absent one, the first of `_DEFAULT_BASES` that resolves) is the
    integration branch; the answer is `git merge-base` of it with `head`, NOT
    its tip. See the module docstring: a branch cut from an older main inherits
    an older version and has asserted nothing.
    """
    head_sha = _rev_parse(repo, head)
    if head_sha is None:
        return None, f"head {head!r} does not resolve to a commit"
    candidates: Sequence[str] = (base,) if base else _DEFAULT_BASES
    tried: List[str] = []
    for cand in candidates:
        if cand is None:
            continue
        tried.append(cand)
        cand_sha = _rev_parse(repo, cand)
        if cand_sha is None:
            continue
        mb = _git(repo, "merge-base", cand_sha, head_sha)
        if mb.returncode == 0 and mb.stdout.strip():
            return mb.stdout.strip(), f"merge-base({cand}, head)"
        # An unrelated history has no merge base. The integration branch's own
        # tip is then the only defensible reference, and saying which was used
        # is the point of the note.
        return cand_sha, f"{cand} (no merge base with head)"
    return None, ("no base resolves: tried " + ", ".join(tried))


# ── the version-bearing sites, derived from the writer's own helpers ────────

def _repo_rel(repo: Path, p: Path) -> Optional[str]:
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def version_sites(repo: Path) -> Tuple[List[str], List[str]]:
    """``(manifest_sites, prose_sites)`` as repo-relative posix paths.

    Both come from the helpers `gatekeeper_assign_version._write_version` writes
    through, so the guard's scope IS the writer's scope by construction.
    """
    plugin_root = _gav._plugin_root(repo)
    pj, manifests = _pmd.find_plugin_and_manifests(plugin_root)
    # `_plugin_root` RETURNS A CANDIDATE PATH whether or not it exists — it is a
    # resolver, not a probe. Without this the empty answer below is unreachable:
    # a tree with no plugin at all still yields one site (a plugin.json that is
    # not there), which reads at every revision as "absent", so the guard would
    # go on to resolve a base it does not need and report rc 2 — "could not
    # look" — for a tree it looked at perfectly well.
    if not pj.is_file() or _repo_rel(repo, pj) is None:
        return [], []
    manifest_sites: List[str] = []
    for p in [pj, *manifests]:
        rel = _repo_rel(repo, p)
        if rel is not None and rel not in manifest_sites:
            manifest_sites.append(rel)

    # `_prose_root` is the OUTERMOST ancestor carrying a manifest that references
    # this plugin, and `find_plugin_and_manifests` ascends sixteen levels — so it
    # can in principle land ABOVE the repo. Those files cannot be read at a
    # revision of THIS repo, and rebasing their names onto the repo root would
    # read different files than the writer writes, which is the one thing this
    # program is built not to do. Unreachable in the shipped layout (measured:
    # the only manifests above the plugin are the repo's own two), and kept for
    # the same reason the hook keeps its unreachable stamp-tail branch: the
    # failure DIRECTION matters, and reading nothing is honest where reading the
    # wrong file is not.
    prose_root = _gav._prose_root(manifests)
    prose_sites: List[str] = []
    prose_rel = None if prose_root is None else _repo_rel(repo, prose_root)
    if prose_rel is not None:
        prefix = "" if prose_rel == "." else f"{prose_rel}/"
        for name in _prose._PROSE_SITES:
            prose_sites.append(f"{prefix}{name}")
    return manifest_sites, prose_sites


def _blob_at(repo: Path, rev: str, rel: str) -> Tuple[Optional[str], bool]:
    """``(text, existed)`` for `rel` at `rev`. `existed` False means the path is
    simply not in that tree — a fact, not a failure. `text` None with
    `existed` True means git refused to read a path it lists."""
    ls = _git(repo, "ls-tree", "-r", "--name-only", rev, "--", rel)
    if ls.returncode != 0:
        return None, True          # could not even list -> caller reports rc 2
    if not ls.stdout.strip():
        return None, False
    show = _git(repo, "show", f"{rev}:{rel}")
    if show.returncode != 0:
        return None, True
    return show.stdout, True


def _manifest_claim(text: str, rel: str, plugin_rel: str) -> Tuple[str, ...]:
    """Version values a manifest/plugin.json blob declares for this plugin."""
    try:
        doc = json.loads(text)
    except Exception:
        return ()
    if rel == plugin_rel:
        v = doc.get("version")
        return (v,) if isinstance(v, str) else ()
    # a marketplace.json: the entries whose source resolves to this plugin.
    # Resolution is done on the PATH SHAPE rather than on disk, because the
    # blob is being read at a revision where the tree may differ.
    mkt_dir = Path(rel).parent.parent            # strip .claude-plugin/
    want = Path(plugin_rel).parent.parent        # strip .claude-plugin/
    out: List[str] = []
    for entry in doc.get("plugins", []) or []:
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if isinstance(src, dict):
            src = src.get("path") or src.get("source")
        if not isinstance(src, str):
            continue
        if _norm((mkt_dir / src).as_posix()) != _norm(want.as_posix()):
            continue
        v = entry.get("version")
        if isinstance(v, str):
            out.append(v)
    return tuple(out)


def _norm(p: str) -> str:
    """Collapse `a/b/../c` textually — the paths are repo-relative and the
    revision's tree is not on disk to resolve against."""
    parts: List[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def claims_at(repo: Path, rev: str, manifest_sites: Sequence[str],
              prose_sites: Sequence[str]
              ) -> Tuple[Dict[Tuple[str, str], Tuple[str, ...]], List[str],
                         List[Tuple[str, str, str, str]]]:
    """``(claims, unreadable, denied)``.

    `claims` maps ``(repo-relative path, claim name)`` to the version values
    declared there at `rev`. `unreadable` names paths git lists but refuses to
    show — those make the question unanswerable, never clean. `denied` is every
    prose match this function READ AND DECLINED to treat as a declaration
    because the sentence around it denies the value: ``(path, claim, value,
    the denial word)``.

    POLARITY, ON THE PROSE SIDE ONLY (vibe-ic#712). A README sentence can print
    the number and take it back in the same breath — "the vibe-ic plugin
    (**v1.18.53**) is not shipped from this branch" — and a reader that takes the
    regex capture as a declaration would refuse a push over a version the
    document denies. The vocabulary and the sentence window both come from
    `_prose_polarity`, never from a private copy of "words that mean no": the
    two defects that module was written for were two private copies drifting.

    The MANIFEST side is deliberately not polarity-checked. JSON is a formal
    grammar with no negation form; `{"version": "1.18.53"}` cannot be denied by
    the object around it, and asking would be theatre.

    `denied` is RETURNED rather than swallowed because the quiet failure here is
    the opposite one: publishing a denied value is loud once someone looks, and
    retracting a value nothing denied is silent — the caller prints what it
    declined so a reader can see the guard read a number and chose not to act
    on it.
    """
    claims: Dict[Tuple[str, str], Tuple[str, ...]] = {}
    unreadable: List[str] = []
    denied: List[Tuple[str, str, str, str]] = []
    plugin_rel = manifest_sites[0] if manifest_sites else ""
    for rel in manifest_sites:
        text, existed = _blob_at(repo, rev, rel)
        if text is None:
            if existed:
                unreadable.append(rel)
            continue
        name = "plugin.json version" if rel == plugin_rel else "marketplace entry version"
        claims[(rel, name)] = _manifest_claim(text, rel, plugin_rel)
    for rel in prose_sites:
        text, existed = _blob_at(repo, rev, rel)
        if text is None:
            if existed:
                unreadable.append(rel)
            continue
        for name, rx, _compare in _prose._CLAIMS:
            kept: List[str] = []
            for m in rx.finditer(text):
                # `LINE_END_BREAKS` is exactly this input: shipped READMEs are
                # line-wrapped prose, so a sentence ends at ".\n" as often as at
                # ". ", and a window that does not stop there reaches back into
                # the sentence above and borrows its polarity.
                lo, hi = _pol.sentence_scope(text, m.start(), m.end(),
                                             extra_breaks=_pol.LINE_END_BREAKS)
                word = _pol.is_denied(text[lo:hi])
                if word:
                    denied.append((rel, name, m.group(1), word))
                    continue
                kept.append(m.group(1))
            if kept:
                claims[(rel, name)] = tuple(kept)
    return claims, unreadable, denied


def evaluate(base_claims: Dict[Tuple[str, str], Tuple[str, ...]],
             head_claims: Dict[Tuple[str, str], Tuple[str, ...]]
             ) -> List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]]:
    """Sites where HEAD declares a value the base did not.

    Returns ``(path, claim name, base values, newly declared values)``.
    """
    out = []
    for key, head_vals in sorted(head_claims.items()):
        base_vals = base_claims.get(key, ())
        added = tuple(v for v in dict.fromkeys(head_vals) if v not in base_vals)
        if added:
            out.append((key[0], key[1], base_vals, added))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="refuse a contributor branch that carries a version bump")
    ap.add_argument("--repo", type=Path, default=Path("."),
                    help="repo root (default: cwd)")
    ap.add_argument("--head", default="HEAD",
                    help="the commit being pushed (default: HEAD)")
    ap.add_argument("--base", default=None,
                    help="integration branch to take the fork point against "
                         "(default: origin/main, then main)")
    ap.add_argument("--dest-ref", default=None,
                    help="destination ref of the push, e.g. refs/heads/next/x. "
                         "refs/heads/main and refs/heads/land/* are exempt; an "
                         "absent or unrecognised destination is NOT exempt.")
    args = ap.parse_args(list(argv) if argv is not None else None)

    repo = args.repo
    if _git(repo, "rev-parse", "--show-toplevel").returncode != 0:
        print(f"[ERROR] {TOOL}: not a git repo: {repo}", file=sys.stderr)
        return RC_CANNOT_CHECK

    if destination_is_exempt(args.dest_ref):
        print(f"[PASS] {TOOL}: {args.dest_ref} assigns the version at landing "
              f"— exempt")
        return RC_OK

    manifest_sites, prose_sites = version_sites(repo)
    if not manifest_sites:
        # A DETERMINATION, not a fail-open: this tree declares no plugin
        # version anywhere, so no branch cut from it can carry a bump. Said out
        # loud so a PASS reports how much it looked at.
        print(f"[PASS] {TOOL}: this repo declares no plugin version "
              f"(no .claude-plugin/plugin.json under {repo}); 0 sites read")
        return RC_OK

    base_sha, note = resolve_fork_point(repo, args.head, args.base)
    if base_sha is None:
        print(f"[ERROR] {TOOL}: cannot determine the fork point — {note}. "
              f"A guard that could not look has not passed.", file=sys.stderr)
        return RC_CANNOT_CHECK

    head_sha = _rev_parse(repo, args.head)
    base_claims, base_bad, base_denied = claims_at(
        repo, base_sha, manifest_sites, prose_sites)
    head_claims, head_bad, head_denied = claims_at(
        repo, head_sha, manifest_sites, prose_sites)
    if base_bad or head_bad:
        for rel in sorted(set(base_bad) | set(head_bad)):
            print(f"[ERROR] {TOOL}: {rel} is in the tree but could not be read",
                  file=sys.stderr)
        print(f"{TOOL}: {len(set(base_bad) | set(head_bad))} version-bearing "
              f"file(s) unreadable — the question could not be put",
              file=sys.stderr)
        return RC_CANNOT_CHECK

    # DISCLOSED, NEVER SILENT. A value dropped for polarity is a value this
    # guard READ; saying so is what separates "the sentence denied it" from
    # "the pattern never matched", which otherwise look identical to a reader.
    for rel, name, value, word in head_denied:
        print(f"[INFO] {TOOL}: {rel}: {name} prints {value} in a sentence that "
              f"denies it ({word!r}) — read, not counted as a declaration")

    findings = evaluate(base_claims, head_claims)
    n_sites = len(manifest_sites) + len(prose_sites)
    if not findings:
        print(f"[PASS] {TOOL}: no version declared that {base_sha[:9]} "
              f"({note}) did not; {n_sites} version-bearing file(s) read")
        return RC_OK

    for path, name, base_vals, added in findings:
        was = ", ".join(base_vals) if base_vals else "<no claim>"
        print(f"[FAIL] {path}")
        print(f"    x {name}: this branch declares "
              f"{', '.join(added)} where {base_sha[:9]} declares {was}")
    for path in dict.fromkeys(f[0] for f in findings):
        print(f"    x restore: git checkout {base_sha[:9]} -- {path}")
    # THE LAST LINE, DELIBERATELY. `_gate_excerpt` in tools/git-hooks/pre-push
    # prints at most twelve finding-shaped lines and then ALWAYS the last line,
    # so the sentence a reader has to act on — who assigns the version — must
    # be there and not in the twelfth `x` of an eight-file bump, where it is
    # exactly the sentence that gets cut.
    n_files = len({f[0] for f in findings})
    print(f"{TOOL}: {len(findings)} version claim(s) on {n_files} of {n_sites} "
          f"version-bearing file(s) changed since {base_sha[:9]} ({note}) — "
          f"THE LANDER ASSIGNS THE VERSION at landing; restore these files.")
    return RC_FINDINGS


if __name__ == "__main__":
    sys.exit(main())
