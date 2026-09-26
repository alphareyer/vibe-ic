#!/usr/bin/env python3
"""The env-pointer debt register may only shrink, and a row that matches
nothing has already stopped describing anything.

`env_pointer_override_inventory.json` records sites where an ENVIRONMENT
POINTER overrules a location the CALLER NAMED. Its own header states the
contract in as many words: "MAY ONLY SHRINK; a row matching nothing is rc 1."

Nothing enforced the second half. The register carried exactly one row —
`benchmark_evidence_structure_check.py::tree` — and the site it names was
REPAIRED: the shape it records, `if _env_tree and args.tree: args.tree =
_env_tree`, no longer exists. Live main now only NOTES the disagreement and
scans the explicitly named `--tree`:

    if _env_tree and args.tree:
        ...
        if not _same:
            print("note: --tree ... is explicit and readable, so it outranks
                   ... THE TWO DISAGREE ... scanning --tree ...")

No assignment. The `elif _env_tree and not args.tree` branch fills an ABSENT
location, which the register's own comment already calls "the correct shape
and correctly not flagged".

So the row describes a defect that is gone, and the census says so on every
run — MEASURED on live main b6a73c0aa3:

    inventory rows applied:            1
    [CENSUS] 1 inventory row(s) match nothing:
       vibe-ic-marketplace/plugins/vibe-ic/programs/benchmark_evidence_structure_check.py::tree

A debt register whose rows outlive their sites stops being a record of debt
and becomes a record of history. This test is the enforcement the header
already claimed.
"""
from __future__ import annotations
import pytest

import importlib.util
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
_ROOT = _PROGRAMS.parents[2]
sys.path.insert(0, str(_PROGRAMS))

_CENSUS = "explicit_argument_outranks_the_environment_pointer_census"


def _census():
    spec = importlib.util.spec_from_file_location(
        _CENSUS, _PROGRAMS / f"{_CENSUS}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _register(mod) -> list:
    path = _PROGRAMS / mod._INVENTORY_NAME
    return json.loads(path.read_text(encoding="utf-8")).get("known", [])


@pytest.mark.consistency
def test_no_register_row_matches_nothing():
    """THE ARM. Every recorded row must still name a site the scanner finds.

    This is the census's own `known - seen` computation, run as an assertion
    instead of as a printed note, so the register cannot quietly accumulate
    rows about repairs that already happened.
    """
    mod = _census()
    findings, denom = mod.scan(_ROOT)
    assert denom["modules_parsed"] > 0, (
        f"the scan parsed 0 modules under {_ROOT} — nothing was read, so this "
        "test would be asserting over an empty population, which is not an "
        "assertion at all")
    seen = {mod._key(f) for f in findings}
    known = {r["key"] for r in _register(mod)}
    stale = sorted(known - seen)
    assert not stale, (
        "the MAY-ONLY-SHRINK register carries row(s) that match nothing — the "
        "site each names has been repaired, so the row records history, not "
        f"debt. Delete them: {stale}")


def test_the_register_is_still_a_register():
    """DIRECTION-2. Emptying it must not be achieved by breaking its shape:
    the file must still parse, still declare `known` as a list, and still
    carry the contract its enforcement rests on."""
    mod = _census()
    doc = json.loads((_PROGRAMS / mod._INVENTORY_NAME).read_text(
        encoding="utf-8"))
    assert isinstance(doc.get("known"), list)
    assert "MAY ONLY SHRINK" in doc.get("_comment", ""), (
        "the contract this test enforces was removed from the register's own "
        "header; the enforcement and the claim must not drift apart")


def test_the_scanner_still_reports_a_live_site():
    """VACUITY GUARD, and it must not be a skip.

    The arm above computes `known - seen`. If `scan` ever stopped reporting
    anything, `seen` would be empty and the arm would demand the deletion of
    every row — including live debt — while looking like a clean result. On
    this tree `seen` IS empty, which is precisely the state in which that
    failure mode is invisible.

    So the scanner is driven against a CONSTRUCTED tree carrying the exact
    shape the register records — an environment read assigned onto a named
    argument under a guard that requires the argument to be PRESENT — and must
    report it. An earlier version of this test skipped here when the real tree
    had no live site; a skip proves nothing and left the arm unguarded.
    """
    mod = _census()
    import tempfile
    with tempfile.TemporaryDirectory(prefix="env_pointer_live_") as d:
        root = Path(d)
        progs = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
        progs.mkdir(parents=True)
        (progs / "offender.py").write_text(
            "import os\n"
            "def main(args):\n"
            "    _env = os.environ.get('VIBEIC_CORPUS')\n"
            "    if _env and args.tree:\n"
            "        args.tree = _env\n",
            encoding="utf-8")
        findings, denom = mod.scan(root)
    assert denom["modules_parsed"] == 1, denom
    assert findings, (
        "the scanner reported nothing for a module carrying the exact shape "
        "the register records — `seen` can therefore be empty for a reason "
        "that has nothing to do with the tree, and the arm above would read "
        "that as `every row is stale`")
    assert mod._key(findings[0]).endswith("::tree"), findings
