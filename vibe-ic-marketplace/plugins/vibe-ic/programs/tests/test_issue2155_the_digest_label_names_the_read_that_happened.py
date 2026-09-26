#!/usr/bin/env python3
"""`digest_kind` is a CLAIM ABOUT A READ, and it must name the read that happened.

WHAT WAS MEASURED — vibe-ic#2155, 2026-09-07 on 8hd-3
=====================================================
With nothing local::

    j = judged_image(env={}, allow_pull=True)
    j.digest_kind -> 'registry-manifest'
    j.source      -> 'registry'
    calls made    -> ['local_repo_digests', 'local_version_label',
                      'registry_version_label']

`registry_digest` is not in that list. It was never called. `image_digest`
short-circuits on a reference that already carries a digest — and
`_eda_pin.image_reference()` ALWAYS carries one — so the digest came out of the
string the rung had just composed, and the rung threw the callee's answer away
(`remote, _kind, _why = …`) and asserted `registry-manifest` of its own accord.
Every report out of that rung claimed a registry round-trip that had not
happened, and nothing in the report could contradict it.

THE RULE THIS FILE PINS
=======================
1. Each read spells its OWN kind, at the one place it happens, out of the
   `_eda_image.DIGEST_KINDS` table (section 1: one test per label).
2. `judged_image` REPORTS the kind it was handed and never invents one
   (section 2), so a rung cannot describe a read it did not perform.
3. The vocabulary is CLOSED and is derived FROM THE TREE, not from a list
   written here — a new literal that nobody declared is caught (section 3).

`source` is a different field and is deliberately unchanged: it names the RUNG
that answered ("override" / "pinned" / "registry" / ""), which is a fact about
control flow. `digest_kind` names the READ. Conflating them is what #2155 was.

chip-AGNOSTIC: image identity only. No design, PDK, vendor or host literal.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import _eda_image as M  # noqa: E402
import _eda_pin as _pin  # noqa: E402

_A = "sha256:" + "a" * 64
_B = "sha256:" + "b" * 64


def _no_docker_at_all(monkeypatch, calls):
    """Every route to the daemon RECORDS itself, and the registry REFUSES.

    The same discipline as `test_the_eda_image_is_resolved_not_remembered.
    _nothing_local`: a rung that reaches the daemon by a route this model does
    not describe trips a refusal that names its argv, rather than quietly
    answering out of whatever this host happens to hold.
    """
    def _undeclared(where):
        def leaf(*argv, **_k):
            pytest.fail(f"{where} was reached by a route this test does not "
                        f"describe: {list(argv)}")
        return leaf

    def _record(name, answer):
        def leaf(*_a, **_k):
            calls.append(name)
            return answer
        return leaf

    monkeypatch.setattr(M, "_run", _undeclared("_eda_image._run"))
    monkeypatch.setattr(_pin, "_docker", _undeclared("_eda_pin._docker"))
    monkeypatch.setattr(M, "registry_digest", _record("registry_digest", None))
    monkeypatch.setattr(M, "local_digest",
                        _record("local_digest", (None, "", "nothing local")))
    monkeypatch.setattr(_pin, "local_repo_digests",
                        _record("local_repo_digests", ((), "nothing local")))
    # THE DIGEST-WIDE RUNG (#2170). `pinned_image_present` asks a second
    # question of this host -- "does any local image carry the pinned digest,
    # under any repository" -- because the repository half is configuration and
    # not identity. It is modelled here for the same reason every other leaf
    # is: unmodelled, it answers out of whatever this host happens to hold.
    monkeypatch.setattr(_pin, "local_references_for_digest",
                        _record("local_references_for_digest", ((), "")))
    monkeypatch.setattr(M, "image_version",
                        _record("image_version", (None, "", "nothing local")))
    return calls


def _state_the_pin(monkeypatch, digest):
    """THE PIN IS A STATED PRECONDITION of section 2, not a read.

    `judged_image` composes `_pin.image_reference(env)`, and that presumes an
    identity. Left to `resolved_image_digest` it is resolved FROM THIS HOST
    (b4b098acc) by `docker image ls --digests`: under `_no_docker_at_all` a
    route the model refuses by design, so the test went red whenever no
    earlier test in the process had warmed the resolve cache and green whenever
    one had; without that model, it answered out of whatever this host holds
    and escaped as `ImageNotResolvable` on a host with no docker at all.
    """
    monkeypatch.setattr(_pin, "resolved_image_digest",
                        lambda env=None, *, allow_pull=False: digest)


# ── 1. one test per label: the kind names the read that produced the digest ──

def test_a_reference_that_carries_its_digest_is_labelled_reference_digest(
        monkeypatch):
    """NOTHING IS READ on this arm, and the label says so. This is the arm
    #2155 was mislabelled from."""
    calls: list = []
    _no_docker_at_all(monkeypatch, calls)
    digest, kind, why = M.image_digest(f"{M.IMAGE_REPO}@{_B}")
    assert (digest, kind, why) == (_B, "reference-digest", "")
    assert calls == [], (
        f"the digest was in the reference, so nothing had to be read — but "
        f"these reads were made: {calls}")


def test_a_digest_read_from_local_metadata_is_labelled_repo_digest(monkeypatch):
    monkeypatch.setattr(M, "local_digest",
                        lambda ref: (_A, "repo-digest", ""))
    monkeypatch.setattr(M, "registry_digest",
                        lambda *a, **k: pytest.fail(
                            "the local read answered; the registry must not be "
                            "asked"))
    assert M.image_digest(f"{M.IMAGE_REPO}:1.2.3") == (_A, "repo-digest", "")


def test_a_digest_read_from_the_local_image_id_is_labelled_image_id(monkeypatch):
    """A weaker true claim beats a stronger false one: an Id identifies these
    bytes on THIS host and nowhere else, and the label is what tells a reader
    the verdict cannot be replayed elsewhere."""
    monkeypatch.setattr(M, "local_digest", lambda ref: (_A, "image-id", ""))
    assert M.image_digest(f"{M.IMAGE_REPO}:1.2.3") == (_A, "image-id", "")


def test_only_a_registry_read_is_labelled_registry_manifest(monkeypatch):
    """The label is owed EXACTLY when `registry_digest` ran and answered."""
    asked: list = []

    def _registry(repo, tag="latest"):
        asked.append((repo, tag))
        return _A

    monkeypatch.setattr(M, "local_digest",
                        lambda ref: (None, "", "not present on this host"))
    monkeypatch.setattr(M, "registry_digest", _registry)
    digest, kind, why = M.image_digest(f"{M.IMAGE_REPO}:1.2.3")
    assert (digest, kind, why) == (_A, "registry-manifest", "")
    # NOT VACUOUS: the read the label claims has to have been made.
    assert asked == [(M.IMAGE_REPO, "1.2.3")], asked


# ── 2. judged_image REPORTS the kind; it does not invent one ────────────────

def test_allow_pull_over_a_digest_pinned_reference_says_reference_digest(
        monkeypatch):
    """THE #2155 CASE ITSELF, end to end.

    Nothing local, `--allow-pull` given, and the pinned reference already
    carries its digest — so `image_digest` short-circuits and the registry is
    never consulted. The report must say `reference-digest`. If it says
    `registry-manifest`, it is asserting a round-trip that this very test proves
    did not happen.
    """
    calls: list = []
    _no_docker_at_all(monkeypatch, calls)
    _state_the_pin(monkeypatch, _B)
    j = M.judged_image(env={}, allow_pull=True)
    assert j.ref == _pin.image_reference({})
    assert j.digest == _B
    assert j.digest == _pin.IMAGE_DIGEST
    assert j.digest_kind == "reference-digest", (
        f"the report labels its digest {j.digest_kind!r}; the reads actually "
        f"made were {calls}")
    assert "registry_digest" not in calls, (
        f"`registry-manifest` may only be claimed when `registry_digest` ran; "
        f"the reads made were {calls}")
    # `source` is the RUNG and is deliberately untouched by this change.
    assert j.source == "registry"


@pytest.mark.parametrize("kind", sorted(M.DIGEST_KINDS))
def test_judged_image_reports_the_kind_it_was_handed(monkeypatch, kind):
    """Whatever `image_digest` answers, the rung relays. A rung that hardcodes
    a kind is describing a read it did not perform, and that is true of every
    entry in the table, not only of the one that was measured."""
    monkeypatch.setattr(_pin, "local_repo_digests",
                        lambda ref: ((), "nothing local"))
    # The digest-wide rung of `pinned_image_present` (#2170); without it this
    # parametrisation reads the host's real image list and reports whatever
    # kind that produced.
    monkeypatch.setattr(_pin, "local_references_for_digest",
                        lambda digest: ((), ""))
    _state_the_pin(monkeypatch, _A)
    monkeypatch.setattr(M, "image_digest",
                        lambda ref, **kw: (_pin.IMAGE_DIGEST, kind, ""))
    monkeypatch.setattr(M, "image_version",
                        lambda ref: (None, "", "nothing local"))
    j = M.judged_image(env={}, allow_pull=True)
    assert j.digest_kind == kind, (
        f"`image_digest` answered {kind!r} and the report says "
        f"{j.digest_kind!r}")


# ── 3. the vocabulary is CLOSED, and is derived from the tree ───────────────

def _kinds_by_shape() -> dict:
    """`{shape: set(kinds)}` — every string this module can put in a
    `digest_kind` slot, read from its own source, KEPT APART BY THE SHAPE IT
    CAME FROM.

    The shapes are reported separately because a scan with two halves can lose
    one of them silently. Measured while writing this file (mutation N4):
    blinding the function-scoped half left the whole test GREEN, because the
    `JudgedImage(...)` half still found `repo-digest` and the guard only asked
    whether ANYTHING had been found. Half a broken instrument reports clean.

    DERIVED, NOT LISTED. A list written here would omit whatever the module
    grows next, which is the failure mode this file exists to close. Two shapes
    carry a kind and both are collected:

      * the middle element of a three-tuple `return` from a function whose name
        says it answers about a DIGEST — the same `(value, kind, why)` shape
        `local_digest` and `image_digest` share;
      * the third positional argument of a `JudgedImage(...)` construction.

    THE FIRST SHAPE IS SCOPED BY THE FUNCTION, and the scope is a predicate
    rather than a list of two names. Measured while writing this file: an
    unscoped walk also collected `local-label` and `registry-label` — the
    VERSION source vocabulary, which travels in an identically shaped tuple out
    of `image_version` and is a different field entirely. An instrument that
    cannot tell two vocabularies apart reports the wrong one as undeclared.
    """
    tree = ast.parse((_PROGRAMS / "_eda_image.py").read_text(encoding="utf-8"))
    by_shape = {"return-tuple": set(), "judged-image": set()}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "digest" not in fn.name:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) \
                    and len(node.value.elts) == 3:
                second = node.value.elts[1]
                if isinstance(second, ast.Constant) \
                        and isinstance(second.value, str):
                    by_shape["return-tuple"].add(second.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) \
                and getattr(node.func, "id", "") == "JudgedImage" \
                and len(node.args) >= 3:
            third = node.args[2]
            if isinstance(third, ast.Constant) and isinstance(third.value, str):
                by_shape["judged-image"].add(third.value)
    return by_shape


def _kinds_the_module_can_emit() -> set:
    """The union of both shapes."""
    return set().union(*_kinds_by_shape().values())


def test_every_kind_the_module_can_emit_is_declared():
    """A kind nobody declared is a claim nobody defined."""
    by_shape = _kinds_by_shape()
    # THE INSTRUMENT MUST BE ABLE TO SEE — BOTH HALVES OF IT. A scan that finds
    # nothing has not proved the module is clean, it has proved the scan is
    # broken, and a scan whose halves are checked only in aggregate can lose one
    # of them and still report clean (measured: mutation N4).
    #
    # NON-EMPTINESS PER SHAPE, AND NOT THE PRESENCE OF ONE PARTICULAR LITERAL.
    # This guard first read `assert "reference-digest" in emitted`, and mutation
    # N2 — relabelling the short-circuit arm as a registry read — turned it red
    # ALONGSIDE the two label tests that are what N2 is actually about. A guard
    # that fires for a second reason tells the reader the wrong thing: whether
    # the scan can see is a property of the SCAN, and which labels the module
    # emits is what the label tests are for.
    for shape, kinds in sorted(by_shape.items()):
        assert kinds, (
            f"the {shape} half of the scan found no kind literal — half a "
            f"broken instrument still reports clean, so its silence means "
            f"nothing")
    emitted = _kinds_the_module_can_emit()
    undeclared = {k for k in emitted if k and k not in M.DIGEST_KINDS}
    assert not undeclared, (
        f"these kinds are emitted but not declared in _eda_image.DIGEST_KINDS, "
        f"so nothing says what read they claim: {sorted(undeclared)}")


def test_the_table_says_what_each_kind_claims_was_read():
    """A table of bare names would let two of them mean the same thing. Each
    entry states the read, and `registry-manifest` is the only one that names
    the registry — that distinction is the whole issue."""
    assert set(M.DIGEST_KINDS) == {"reference-digest", "repo-digest",
                                   "image-id", "registry-manifest"}
    # The phrase is exact on purpose: `repo-digest` also mentions the registry
    # (it is registry-PORTABLE), and a substring match on the word alone called
    # it a registry read while writing this file. What separates the entries is
    # the CALL that was made, so that is what the table is asked about.
    claim_a_registry_read = {k for k, v in M.DIGEST_KINDS.items()
                             if "`registry_digest` ran" in v}
    assert claim_a_registry_read == {"registry-manifest"}, claim_a_registry_read
    assert "NOTHING was read" in M.DIGEST_KINDS["reference-digest"]


def test_the_old_spelling_is_gone_from_the_module():
    """`given` named WHO SUPPLIED the digest, not what was read, and a caller
    relabelling it had no word to contradict. One vocabulary, one spelling."""
    assert "given" not in _kinds_the_module_can_emit()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
