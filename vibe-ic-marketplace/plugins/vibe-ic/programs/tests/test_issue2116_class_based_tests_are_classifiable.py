"""vibe-ic#2116(1) — a test METHOD inside a class can be credited.

`classify_module` enumerates tests with `ast.walk`, so a method inside a class
was always LISTED as a test; it built `funcs` — the call graph — from
`tree.body` alone, so that same method was never a NODE of the graph.
`reaches(name)` returned False at `name not in funcs` before it examined a
single call, and a class-based module was reported entirely SYNTHETIC no
matter what it read. The lane that filed this worked around it by writing a
module-level test, which is the wrong direction: the classifier exists to tell
a reviewer what the suite really reads.

Every case here is PAIRED, because a classifier that answered REAL for
everything would satisfy the reproducers on its own:

  * a class method that reaches a real accessor            -> REAL
  * the SAME class with the artefact read removed          -> SYNTHETIC
  * a class method reached through a class FIXTURE          -> REAL
  * `self` / `cls` are not fixture requests
  * the module-scope behaviour this change must not disturb

MEASURED blast radius on the tree at the time of the fix: `classify_module`
was run over all 3609 `test_*.py` files in the repo before and after, and the
per-module (real, synthetic) name sets are IDENTICAL — today's suite contains
no class-based test method that reaches a real accessor. The defect is real
and the fix changes no existing verdict; the reproducers below are what prove
it, not a population delta.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import real_artefact_test_backing_check as C  # noqa: E402


def _mod(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "test_sample.py"
    p.write_text(body)
    return p


def _names(entries):
    return sorted(e.split(" [")[0] for e in entries)


# ---------------------------------------------------------------------------
# 1. THE REPRODUCER AND ITS MUTATION ARM
# ---------------------------------------------------------------------------
_CLASS_WITH_HELPER = (
    "from _hostpaths import require_repo\n"
    "class TestCorpus:\n"
    "    def _corpus(self):\n"
    "        return require_repo('vibe-ic-marketplace')\n"
    "    def test_a(self):\n"
    "        assert self._corpus().is_dir()\n"
)

#: The SAME module with the one artefact read removed. Nothing else changes —
#: same class, same method names, same call shape — so a classifier that
#: credited the case above for any reason OTHER than the read it performs
#: fails here.
_CLASS_WITHOUT_READ = (
    "class TestCorpus:\n"
    "    def _corpus(self):\n"
    "        return {}\n"
    "    def test_a(self):\n"
    "        assert self._corpus() == {}\n"
)


def test_a_class_method_reaching_the_helper_is_real(tmp_path):
    r = C.classify_module(_mod(tmp_path, _CLASS_WITH_HELPER))
    assert r["tests"] == ["test_a"]
    assert _names(r["real"]) == ["test_a"] and r["synthetic"] == []
    assert "[helper]" in r["real"][0]


def test_the_same_class_without_the_artefact_read_is_synthetic(tmp_path):
    r = C.classify_module(_mod(tmp_path, _CLASS_WITHOUT_READ))
    assert r["tests"] == ["test_a"]
    assert r["real"] == [] and r["synthetic"] == ["test_a"]


def test_a_class_method_calling_the_helper_directly_is_real(tmp_path):
    """`reaches` bailed out on the method name before looking at its body, so
    even a DIRECT accessor call in a method went uncredited."""
    r = C.classify_module(_mod(tmp_path, (
        "from _hostpaths import require_repo\n"
        "class TestCorpus:\n"
        "    def test_a(self):\n"
        "        assert require_repo('vibe-ic-marketplace').is_dir()\n")))
    assert _names(r["real"]) == ["test_a"] and r["synthetic"] == []


def test_a_method_reached_through_a_class_fixture_is_real(tmp_path):
    r = C.classify_module(_mod(tmp_path, (
        "import pytest\n"
        "from _hostpaths import require_repo\n"
        "class TestCorpus:\n"
        "    @pytest.fixture\n"
        "    def corpus(self):\n"
        "        return require_repo('vibe-ic-marketplace')\n"
        "    def test_a(self, corpus):\n"
        "        assert corpus\n")))
    assert _names(r["real"]) == ["test_a"] and r["synthetic"] == []
    # Reported as `[helper]`, not `[fixture]`: once class bodies are walked,
    # the fixture METHOD is itself a node of the call graph and `test_a`
    # reaches it by name before the fixture-parameter branch is consulted.
    # The verdict is what this test pins; the label is a reader's hint.
    assert r["real"][0].startswith("test_a [")


# ---------------------------------------------------------------------------
# 2. `self` / `cls` ARE THE RECEIVER, NOT A FIXTURE REQUEST
# ---------------------------------------------------------------------------
def test_a_self_named_fixture_does_not_back_every_method(tmp_path):
    """Without the receiver exclusion, a module that defines a fixture called
    `self` would credit every method in the file through its own first
    parameter — a REAL verdict nothing in the test bodies earns."""
    r = C.classify_module(_mod(tmp_path, (
        "import pytest\n"
        "from _hostpaths import require_repo\n"
        "@pytest.fixture\n"
        "def self():\n"
        "    return require_repo('vibe-ic-marketplace')\n"
        "class TestCorpus:\n"
        "    def test_a(self):\n"
        "        assert True\n")))
    assert r["real"] == [] and r["synthetic"] == ["test_a"]


# ---------------------------------------------------------------------------
# 2b. A METHOD RESOLVES IN ITS OWN CLASS, NOT ACROSS THE MODULE
# ---------------------------------------------------------------------------
def test_a_helper_in_another_class_does_not_back_this_one(tmp_path):
    """`_calls_in` reports attribute TAILS, so `self._corpus()` arrives as the
    bare name `_corpus`. A flat table of every method in the file would let
    `TestReal._corpus` answer for `TestFake._corpus` and report a test REAL
    that reads nothing — the over-claim direction the module docstring names
    as the failure this program exists to prevent."""
    r = C.classify_module(_mod(tmp_path, (
        "from _hostpaths import require_repo\n"
        "class TestReal:\n"
        "    def _corpus(self):\n"
        "        return require_repo('vibe-ic-marketplace')\n"
        "    def test_real(self):\n"
        "        assert self._corpus().is_dir()\n"
        "class TestFake:\n"
        "    def _corpus(self):\n"
        "        return {}\n"
        "    def test_fake(self):\n"
        "        assert self._corpus() == {}\n")))
    assert _names(r["real"]) == ["test_real"]
    assert r["synthetic"] == ["test_fake"]


def test_a_module_level_helper_still_backs_a_method(tmp_path):
    """The pair to the case above: class scope ADDS to module scope, it does
    not replace it."""
    r = C.classify_module(_mod(tmp_path, (
        "from _hostpaths import require_repo\n"
        "def _corpus():\n"
        "    return require_repo('vibe-ic-marketplace')\n"
        "class TestC:\n"
        "    def test_a(self):\n"
        "        assert _corpus().is_dir()\n")))
    assert _names(r["real"]) == ["test_a"] and r["synthetic"] == []


# ---------------------------------------------------------------------------
# 3. THE MODULE-SCOPE BEHAVIOUR THIS CHANGE MUST NOT DISTURB
# ---------------------------------------------------------------------------
def test_a_module_level_function_test_is_unchanged(tmp_path):
    r = C.classify_module(_mod(tmp_path, (
        "from _hostpaths import require_repo\n"
        "def _corpus():\n"
        "    return require_repo('vibe-ic-marketplace')\n"
        "def test_a():\n"
        "    assert _corpus().is_dir()\n")))
    assert _names(r["real"]) == ["test_a"] and r["synthetic"] == []


def test_a_pure_fixture_class_is_still_synthetic(tmp_path):
    r = C.classify_module(_mod(tmp_path, (
        "class TestScratch:\n"
        "    def test_a(self, tmp_path):\n"
        "        (tmp_path / 'x.json').write_text('{}')\n"
        "        assert True\n")))
    assert r["real"] == [] and r["synthetic"] == ["test_a"]


def test_both_module_and_class_tests_are_enumerated(tmp_path):
    """MEMBERSHIP, not count: the fix must not drop a test from the report."""
    r = C.classify_module(_mod(tmp_path, (
        "def test_top():\n"
        "    assert True\n"
        "class TestC:\n"
        "    def test_inner(self):\n"
        "        assert True\n")))
    assert sorted(r["tests"]) == ["test_inner", "test_top"]
    assert sorted(r["synthetic"]) == ["test_inner", "test_top"]
