#!/usr/bin/env python3
"""A duplicate key in a register dict is INVISIBLE at runtime.

MEASURED on main `758ac4516`: `prose_polarity_consulted_check._NOT_PROSE`
carries `"crosslayer_rewrite_equivalence::module_ports"` TWICE -- once at line
~927 and again at line ~1124, a duplicate left by an earlier merge. Python
evaluates both and keeps the LAST, so the first entry's argument -- which was
the only place naming the non-ANSI port form `module m(a, b); input [7:0] a;`,
the `_pad_ring::parse_def` precedent, and "a call that can never fire is a
green light rather than a check" -- silently vanished. The file read as though
it held 65 exemptions; the module held 64.

WHY THIS NEEDS `ast` AND CANNOT BE DONE AT RUNTIME. By the time the dict
object exists the duplicate is already gone: `len()` reports the collapsed
count, `in` finds the survivor, and every value equals the last literal. There
is no runtime observation that distinguishes "written once" from "written
twice and one silently discarded". Only the SOURCE carries that fact, so the
only instrument that can see it is a parse of the source.

SCOPE: every module-level dict literal in the file, so a register added later
is covered without anyone remembering to list it here. `_NOT_PROSE` and
`_OFFENDER_REGISTER` are named explicitly as well, so that deleting or renaming
one of them cannot quietly empty this test's population.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
TARGET = PROG / "prose_polarity_consulted_check.py"


def _module_level_dicts(path: Path):
    """{name: [key literals in SOURCE ORDER]} for module-level dict literals."""
    tree = ast.parse(path.read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = [
                    (k.value, k.lineno) for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
    return out


def test_the_file_parses_and_the_population_is_not_empty():
    """NEGATIVE CONTROL. A test whose population is empty passes for free; this
    one refuses to be vacuous, so a rename or a refactor that takes the
    registers out of module scope reddens here instead of going quiet."""
    dicts = _module_level_dicts(TARGET)
    assert dicts, f"no module-level dict literals found in {TARGET.name}"
    for name in ("_NOT_PROSE", "_OFFENDER_REGISTER"):
        assert name in dicts, (
            f"{name} is no longer a module-level dict literal in "
            f"{TARGET.name}; this test's population silently lost a register")
    assert len(dicts["_NOT_PROSE"]) > 50, (
        "the _NOT_PROSE population collapsed -- a duplicate check over a few "
        "entries would pass for reasons that have nothing to do with the rule")


@pytest.mark.parametrize("register", ("_NOT_PROSE", "_OFFENDER_REGISTER"))
def test_the_named_registers_declare_each_key_once(register):
    keys = _module_level_dicts(TARGET)[register]
    seen = collections.defaultdict(list)
    for key, lineno in keys:
        seen[key].append(lineno)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dupes, (
        f"{register} in {TARGET.name} declares the same key more than once: "
        + "; ".join(f"{k!r} at lines {v}" for k, v in sorted(dupes.items()))
        + ". Python keeps the LAST literal, so every earlier entry's argument "
          "is discarded with no runtime trace -- the file claims an exemption "
          "it does not hold. Merge them into one entry that keeps both "
          "arguments' substance; do not simply delete one.")


def test_every_module_level_dict_declares_each_key_once():
    """The same rule over EVERY module-level dict, so a register added after
    this test was written is covered without an edit here."""
    offences = []
    for name, keys in _module_level_dicts(TARGET).items():
        seen = collections.defaultdict(list)
        for key, lineno in keys:
            seen[key].append(lineno)
        for key, lines in sorted(seen.items()):
            if len(lines) > 1:
                offences.append(f"{name}: {key!r} at lines {lines}")
    assert not offences, (
        "duplicate keys in module-level dict literals, invisible at runtime:\n"
        + "\n".join(offences))


def test_the_instrument_can_actually_see_a_duplicate():
    """CONTROL FOR THE INSTRUMENT ITSELF. A duplicate-key check that cannot
    detect a planted duplicate would pass this file for the wrong reason --
    which is exactly the failure mode being fixed. Plant one and require the
    parser to report it."""
    planted = ast.parse('_R = {"a": "one", "b": "two", "a": "three"}\n')
    node = planted.body[0]
    keys = [k.value for k in node.value.keys]
    dupes = [k for k, c in collections.Counter(keys).items() if c > 1]
    assert dupes == ["a"], (
        "the ast-based duplicate detector failed on a planted duplicate")
    # and the runtime view really is blind to it, which is why ast is used
    scope: dict = {}
    exec(compile(planted, "<planted>", "exec"), scope)          # noqa: S102
    assert list(scope["_R"]) == ["a", "b"] and scope["_R"]["a"] == "three", (
        "the runtime dict unexpectedly preserved the duplicate -- if this ever "
        "holds, the premise of this whole test file needs re-deriving")
