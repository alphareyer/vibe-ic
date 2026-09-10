"""A corpus subject resolved from ABOVE the repository is not this repo's subject.

THE DEFECT
==========
Three test modules located an in-repo corpus by walking ``[<plugin root>,
*<plugin root>.parents]`` — every ancestor up to ``/`` — for a
``benchmark-data/`` directory. Each said so in its own docstring ("up to the
filesystem root"). ``benchmark-data`` left this repository at ``c5d7f2d00``
(``git ls-files benchmark-data`` matches nothing), so on a machine whose
``$HOME`` holds a clone of ``vibeic/benchmark-data`` the walk left the checkout
and bound the subject to that clone.

MEASURED on ``f91aaa391``, ``test_l9_memory_generator_macro_identifier.py``,
same commit, same host, same interpreter, ONLY the checkout's LOCATION moved::

    tree under /home/reyerchu/...   58 passed,  0 skipped
    tree under /tmp/...             39 passed, 19 skipped   (git archive, same sha)
    inside the pinned image         39 passed, 19 skipped   (only the tree mounted)

Nineteen of fifty-eight, and 21 across two of the three files. Both readings are
wrong, and in opposite directions: the skip is capability-shaped and names no
capability, and the pass is a green taken against an **undeclared corpus of
unknown version** that no run record names. A published pointer exists for
exactly this (``VIBE_IC_BENCHMARK_DATA``); an ancestor walk is not it.

EVERY ASSERTION BELOW IS ADDRESSED TO CODE THAT EXISTS ON BOTH TREES
====================================================================
The resolvers (``_real_docs_dir``, ``_candidate_benchmark_dirs``, ``_repo_roots``)
are the subject, and they are present before and after the repair, so the
pre-fix tree ANSWERS — wrongly — instead of raising ``AttributeError``. A control
that dies on a missing name has observed nothing about the defect; it has only
observed that the fix is absent, which the branch already tells you.

BOTH DIRECTIONS ARE ASSERTED. A ceiling that simply found nothing would satisfy
the negative half and delete the feature, so the positive half plants the same
directory INSIDE the repository and requires it to still resolve.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
for _p in (str(_PROGRAMS), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MARKETPLACE = ("vibe-ic-marketplace", "plugins", "vibe-ic")

#: (module file, the name of its plugin-root constant, the resolver, what a hit
#: looks like). Each resolver returns either a path or a list of paths; the
#: helper below normalises that so one table drives every case.
_RESOLVERS = (
    ("test_l9_memory_generator_macro_identifier.py", "PLUGIN",
     "_real_docs_dir", ("ic", "edge_llm_accel", "input", "docs")),
    ("test_v0_2_97_issue466_real_input_fixture.py", "_PLUGIN_ROOT",
     "_candidate_benchmark_dirs", ("ic",)),
    ("test_ic_class_stability_fixtures.py", "_PLUGIN_ROOT",
     "_repo_roots", ("ic",)),
)


def _load(filename: str):
    name = "_corpus_subject_probe_" + filename[:-3]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _synthetic_tree(tmp_path: Path):
    """(outside, repo_root, plugin_root) with the real marketplace nesting.

    ``outside`` is the parent OF the repository — the position a developer's
    ``$HOME`` occupies relative to a checkout underneath it.
    """
    outside = tmp_path / "outside"
    repo = outside / "repo"
    plugin = repo.joinpath(*_MARKETPLACE)
    plugin.mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text("{}\n")
    return outside, repo, plugin


def _plant(root: Path) -> Path:
    """A `benchmark-data` tree deep enough for every resolver in the table."""
    d = root / "benchmark-data" / "ic" / "edge_llm_accel" / "input" / "docs"
    d.mkdir(parents=True)
    (d / "L1.md").write_text("# planted\n")
    (root / "benchmark-data" / "evaluation" / "phase1_parity").mkdir(parents=True)
    return d


def _hits(mod, const: str, resolver: str, plugin: Path, monkeypatch) -> list:
    """Every path the module's own resolver would bind, as a list.

    The pointer is unset for the duration so the in-repo branch — the one under
    test — is the branch that decides. A resolver that consults
    ``VIBE_IC_BENCHMARK_DATA`` first is doing the right thing and must not be
    credited for it here.
    """
    monkeypatch.delenv("VIBE_IC_BENCHMARK_DATA", raising=False)
    monkeypatch.setattr(mod, const, plugin, raising=True)
    out = getattr(mod, resolver)()
    if out is None:
        return []
    return list(out) if isinstance(out, (list, tuple)) else [out]


@pytest.mark.parametrize("filename,const,resolver,tail", _RESOLVERS)
def test_a_corpus_above_the_repository_is_never_the_subject(
        filename, const, resolver, tail, tmp_path, monkeypatch):
    """The defect, rebuilt: the clone sits one level above the checkout."""
    outside, _repo, plugin = _synthetic_tree(tmp_path)
    planted = _plant(outside)
    assert planted.is_dir(), "the probe planted nothing; it would prove nothing"
    hits = _hits(_load(filename), const, resolver, plugin, monkeypatch)
    outsiders = [h for h in hits if outside in (h, *h.parents)
                 and _repo_not_under(h, plugin)]
    assert not outsiders, (
        f"{filename}::{resolver} bound its subject to {outsiders}, which is "
        f"OUTSIDE the repository {plugin.parent.parent.parent}. On a host whose "
        f"$HOME holds a clone of vibeic/benchmark-data this makes the verdict a "
        f"function of where the checkout happens to sit. Resolve through the "
        f"declared pointer, or through `_plugin_tree.corpus_search_roots`.")


def _repo_not_under(hit: Path, plugin: Path) -> bool:
    """True when `hit` is not inside the repository the plugin belongs to."""
    repo = plugin.parent.parent.parent
    return repo not in (hit, *hit.parents)


@pytest.mark.parametrize("filename,const,resolver,tail", _RESOLVERS)
def test_a_corpus_inside_the_repository_is_still_the_subject(
        filename, const, resolver, tail, tmp_path, monkeypatch):
    """The positive half: the ceiling must not be a way of finding nothing."""
    _outside, repo, plugin = _synthetic_tree(tmp_path)
    planted = _plant(repo)
    assert planted.is_dir()
    hits = _hits(_load(filename), const, resolver, plugin, monkeypatch)
    assert hits, (
        f"{filename}::{resolver} found NOTHING with a benchmark-data planted at "
        f"the repository root {repo} — the ceiling has become a blindfold")
    assert all(repo in (h, *h.parents) for h in hits), hits


def test_no_test_module_resolves_a_corpus_by_walking_to_the_filesystem_root():
    """The register, so the construct cannot come back in a fourth module.

    Keyed on the CONSTRUCT — a starred ``.parents`` used as a search space in a
    function that also names ``benchmark-data`` — and not on the three file
    names. A list of the files that had it is a list that goes stale the first
    time a fourth one is written.
    """
    import ast

    offenders = []
    for path in sorted(_HERE.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dumped = ast.dump(fn)
            if "benchmark-data" not in dumped and "benchmark_data" not in dumped:
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Starred)
                        and isinstance(node.value, ast.Attribute)
                        and node.value.attr == "parents"):
                    offenders.append(f"{path.name}:{node.lineno} in {fn.name}()")
    assert not offenders, (
        "a corpus subject is resolved by an UNBOUNDED ancestor walk, which "
        "reaches outside the repository and binds the test to whatever clone "
        "happens to sit above the checkout: "
        + ", ".join(offenders)
        + ". Use `_plugin_tree.corpus_search_roots(<plugin root>)`.")
