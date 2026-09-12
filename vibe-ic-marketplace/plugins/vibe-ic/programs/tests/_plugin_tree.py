"""programs/tests/_plugin_tree.py — shared plugin-root resolver (flow #486).

TWO-TREE POSTURE
================
The Vibe-IC plugin is exercised from two physically different directory
shapes, and the test suite must behave correctly in BOTH:

  1. SOURCE tree (authoritative, full suite)
     .../AI_IC_design/                         <- repo root (monorepo)
       tools/ci/...                            <- repo-root tooling (CI only)
       docs/architecture/...                   <- repo-root docs
       benchmark_phase1/ , benchmark_ic/ ...   <- private benchmark corpora
       vibe-ic-marketplace/
         plugins/
           .gitignore
           vibe-ic/                            <- PLUGIN ROOT
             .claude-plugin/plugin.json        <- the manifest (anchor)
             agents/ programs/ skills/ flow/ tools/phase1_engine ...

  2. CACHE tree (the installed plugin — a FLATTENED shipped subset)
     ~/.claude/plugins/cache/vibe-ic-marketplace/vibe-ic/<ver>/   <- PLUGIN ROOT
       .claude-plugin/plugin.json              <- the manifest (anchor)
       agents/ programs/ skills/ flow/ tools/phase1_engine ...

In the cache tree the plugin root has NO monorepo ancestors: there is no
``vibe-ic-marketplace/plugins/vibe-ic`` path to re-descend into, no
repo-root ``tools/ci`` / ``docs/`` / ``benchmark_*`` directories, and no
``plugins/.gitignore``. Tests that hard-coded the source-monorepo path
shape (``Path(__file__).resolve().parents[5] / "vibe-ic-marketplace" /
"plugins" / "vibe-ic" / ...``) therefore raise ``IndexError`` /
``FileNotFoundError`` on the cache tree.

THE CONTRACT
============
* SOURCE tree  = authoritative full suite. Everything runs.
* CACHE  tree  = shipped subset. Resources that are genuinely NOT shipped
  (repo-root ``tools/ci``, ``tools/<repo-level>``, ``docs/``, external
  benchmark dirs, ``plugins/.gitignore``) yield a *named* ``pytest.skip``
  with the reason marker below — NEVER an environmental ERROR.

This module finds the plugin root by walking UP from the caller until it
hits the directory that contains ``.claude-plugin/plugin.json`` (the
plugin manifest), which is the ONE anchor present and identical in both
trees. From that anchor it exposes lookups for in-plugin resources and a
``require_or_skip`` helper for resources that may be absent on the cache.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

# The single named reason for every "this resource isn't in the installed
# cache" skip. Tests use it so the posture is greppable + uniform.
NOT_SHIPPED_REASON = (
    "not shipped in the installed cache — two-tree posture, flow #486"
)

_MANIFEST_REL = Path(".claude-plugin") / "plugin.json"


def plugin_root(start: Optional[Path] = None) -> Path:
    """Return the plugin root: the nearest ancestor of ``start`` (default:
    this module's own location) that contains ``.claude-plugin/plugin.json``.

    Works identically on the source monorepo and the flattened install
    cache because the manifest is the one anchor present in both. Raises
    ``RuntimeError`` only if no manifest is found on the way up — that is a
    genuinely broken tree, not a two-tree-posture condition.
    """
    here = (start or Path(__file__)).resolve()
    # If ``start`` is a file, begin at its directory; if a dir, use it.
    cur = here if here.is_dir() else here.parent
    for cand in (cur, *cur.parents):
        if (cand / _MANIFEST_REL).is_file():
            return cand
    raise RuntimeError(
        f"plugin manifest {_MANIFEST_REL} not found walking up from {here}; "
        f"this is not a valid plugin tree"
    )


def plugin_path(*parts: str) -> Path:
    """Resolve a path RELATIVE TO the plugin root, e.g.
    ``plugin_path("agents", "defaults", "class_reference.yaml")``.

    The path is returned whether or not it exists; existence is the
    caller's concern. Use :func:`require_or_skip` for resources that may be
    absent on the cache tree.
    """
    return plugin_root().joinpath(*parts)


def require_or_skip(*parts: str) -> Path:
    """Resolve a plugin-relative resource and ``pytest.skip`` with the
    canonical :data:`NOT_SHIPPED_REASON` if it is absent.

    Use this for in-plugin resources that *should* exist on a complete
    tree but may be trimmed from the shipped cache subset, so the test
    degrades to a NAMED SKIP instead of an ERROR.
    """
    p = plugin_path(*parts)
    if not p.exists():
        pytest.skip(f"{'/'.join(parts)}: {NOT_SHIPPED_REASON}")
    return p


def repo_root_of(plugin_dir: Path) -> Optional[Path]:
    """:func:`repo_root` for a GIVEN plugin root — the BOUNDED search ceiling.

    WHY THIS IS A SEPARATE, PARAMETERISED FUNCTION
    ==============================================
    Three test modules resolved a corpus subject by walking
    ``[<plugin root>, *<plugin root>.parents]`` — every ancestor **up to the
    filesystem root** — for a ``benchmark-data/`` directory. Each says so in its
    own docstring ("up to the filesystem root", "resolve by STRUCTURE, not a
    hard-coded parent count"). The structure part was right; the ceiling was
    missing, and without a ceiling the walk leaves the repository.

    MEASURED on ``f91aaa391``, one file
    (``test_l9_memory_generator_macro_identifier.py``), same commit, same host,
    same interpreter, ONLY the checkout's LOCATION different — the operator's
    ``$HOME`` happens to hold a clone of ``vibeic/benchmark-data``::

        tree under <account-home>/...  58 passed,  0 skipped
        tree under /tmp/...             39 passed, 19 skipped   (git archive, same sha)
        inside the pinned image         39 passed, 19 skipped   (only the tree mounted)

    Both readings are wrong. The skip is capability-shaped and is not about a
    capability; the pass is a green taken against an **undeclared corpus of
    unknown version** that no run record names. A subject reachable from outside
    the repository is not this repository's subject.

    The ceiling is the repository the plugin belongs to. This function is the
    single place that rule is written, and it takes the plugin root as an
    ARGUMENT so a test can steer it — a bound whose failure path cannot be
    exercised is a bound nothing checks.
    """
    try:
        if (plugin_dir.parent.name == "plugins"
                and plugin_dir.parent.parent.name == "vibe-ic-marketplace"):
            return plugin_dir.parent.parent.parent
    except Exception:
        return None
    return None


def corpus_search_roots(plugin_dir: Path) -> tuple:
    """The ONLY directories an in-repo corpus subject may be resolved under.

    The plugin itself, and the repository it belongs to. Never an ancestor of
    that repository, and never the filesystem root. Callers keep their own
    ``benchmark-data/...`` relative path and their own absent-subject skip; all
    that changes is where they are allowed to look.
    """
    ceiling = repo_root_of(plugin_dir)
    return (plugin_dir,) if ceiling is None else (plugin_dir, ceiling)


def repo_root() -> Optional[Path]:
    """Return the monorepo root (the ``vibe-ic-marketplace`` parent) when
    running on the SOURCE tree, else ``None`` on the flattened cache.

    Identified by: plugin_root is ``<repo>/vibe-ic-marketplace/plugins/vibe-ic``.
    On the cache tree there is no such ancestor chain, so this returns
    ``None`` and callers should ``pytest.skip(...NOT_SHIPPED_REASON...)``.
    """
    return repo_root_of(plugin_root())


def _reroute_moved(parts) -> Optional[Path]:
    """`_published_corpus.reroute_moved_path`, lazily and never fatally."""
    import sys as _sys
    here = str(Path(__file__).resolve().parent)
    if here not in _sys.path:
        _sys.path.insert(0, here)
    try:
        import _published_corpus as _pc
        return _pc.reroute_moved_path(*parts)
    except Exception:
        return None


def repo_path_or_missing(*parts: str) -> Path:
    """Resolve a path relative to the REPO ROOT, returning a guaranteed
    NON-EXISTENT path when running on the flattened cache (no repo root).

    Module-level safe: never raises. Callers that already guard on
    ``.is_dir()`` / ``.exists()`` (e.g. ``@pytest.mark.skipif(not BP.is_dir())``)
    keep working unchanged — on the cache tree the returned path simply
    does not exist, so the existing skip fires instead of an ``IndexError``
    from a hard-coded ``parents[N]``.
    """
    # `benchmark-data/...` IS NOT UNDER THE REPO ROOT ANY MORE. It left at
    # `c5d7f2d00` (`git ls-tree -r HEAD -- benchmark-data` matches nothing), so
    # every caller that asked for it here got a path no host could satisfy and
    # skipped on the guard below — reading as "absent on this checkout", which
    # no checkout could change. `_published_corpus.reroute_moved_path` answers
    # from the pointer when one is set, and returns None otherwise, so the
    # flattened-cache behaviour and the plain in-repo behaviour are untouched.
    rerouted = _reroute_moved(parts)
    if rerouted is not None:
        return rerouted
    rr = repo_root()
    if rr is None:
        # A path under the plugin root that cannot exist, so .is_dir() is
        # False everywhere on the cache tree.
        return plugin_root() / "__NOT_SHIPPED_repo_root__" / Path(*parts)
    return rr.joinpath(*parts)


def repo_resource_or_skip(*parts: str, required_on_source: bool = False) -> Path:
    """Resolve a path relative to the REPO ROOT (monorepo-only resources
    such as ``tools/ci/...``, ``docs/...``, ``benchmark_phase1/...``) and
    ``pytest.skip`` with :data:`NOT_SHIPPED_REASON` when either the repo
    root is unavailable (cache tree) or the resource is absent.

    flow #488 — skip-reason SPLIT: a resource that the SOURCE tree is
    expected to carry must not silently dormant-skip with the misleading
    not-shipped wording when its path is simply MISPLACED. Callers that
    know the resource always exists on the source tree pass
    ``required_on_source=True``: on the source tree (repo root resolved)
    a missing path then FAILs with a path-misplaced diagnosis instead of
    skipping; the flattened cache tree keeps the legitimate named skip.
    Default stays ``False`` (plain skip) for resources that may honestly
    be absent on some source checkouts (e.g. benchmark dirs).
    """
    rr = repo_root()
    if rr is None:
        pytest.skip(f"{'/'.join(parts)} (repo-root resource): {NOT_SHIPPED_REASON}")
    p = rr.joinpath(*parts)
    if not p.exists():
        if required_on_source:
            pytest.fail(
                f"{'/'.join(parts)}: expected on the SOURCE tree but absent "
                f"— resource path misplaced? (flow #488; this is NOT the "
                f"two-tree not-shipped case)")
        pytest.skip(f"{'/'.join(parts)} (repo-root resource): {NOT_SHIPPED_REASON}")
    return p
