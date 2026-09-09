"""vibe-ic#2183 — a declaration root Step 5 CANNOT LIST is not a design that declared nothing.

MEASURED (lane cy2183, 8HD-4, 2026-09-09, `origin/main` 610cae2cc2b9, image
`192.168.1.112:5000/vibeic-eda@sha256:89a8fd7295…` read from
`_eda_pin.IMAGE_DIGEST`).

#2183 reported a `property_contract.json` carrying

    property_denominator 3, authored_property_count 0
    missing_declarations ["L3","L6","L8"]
    each row: status DECLARATION_MISSING,
      "<L> declaration is absent; applicability and property are unknown"

for a project whose L3/L6/L8 declarations are present. Its own candidate cause
— that Step 5 read an ephemeral `vibeic-rtl-step-*` staging root instead of the
project — is REFUTED twice on this base, and the refutation is why this file
exists rather than a path-plumbing change:

  * STRUCTURAL: `formal_harness_gen.generate` is reached only from
    `design_one_shot_runner.step_emit_phase2_manifests`, whose only caller is
    `main()`, where `project = args.project.resolve()` is assigned once and
    never reassigned; the front door passes the canonical root. The staging
    root exists only inside `step_rtl_gen`, which has returned by then.
  * MEASURED: driving the real `step_rtl_gen` in the WAIVE shape, the stage is
    a FULL copy of the project — it carried `phase1/generated_docs` with all
    five `L*.json`. Reading it would have yielded the same obligations, so it
    is not the negative-control shape the issue assumed.

What DOES reproduce the reported artefact, byte for byte, from a project whose
declarations are present and individually openable BY NAME, is a declaration
root this process may not LIST:

    os.chmod(phase1/generated_docs, 0o300)      # write+execute, no read
    ->  missing_declarations ['L3','L6','L8']   obligations 0
        declaration_root_present True           unreadable_declarations []
        row L3.declaration_missing  DECLARATION_MISSING
            "L3 declaration is absent; applicability and property are unknown"

`Path.glob` answers a directory it cannot read with an empty iterator and no
exception. That is a failed read reported as an empty result — the same defect
#2183's first repair removed from the per-FILE read (`except: continue`) while
leaving it in place one level up, on the directory. This flow runs its steps
across a container boundary and across users, so a declaration tree the flow
wrote and this process cannot enumerate is a reachable state, not a contrived
one.

FALSIFICATION, two-tree, MEASURED: with this file copied into a pristine
610cae2cc2b9 worktree and nothing else changed, `5 failed, 5 passed`. The five
`ROOT_UNREADABLE` tests fail there — four because the base reports
`DECLARATION_MISSING`, and the stat one because the base lets `PermissionError`
escape `_read_l_docs` entirely. The three `CONTROL` tests, the presence control
and the selection guard pass on BOTH trees, so the fix cannot be satisfied by a
function that simply stops emitting `DECLARATION_MISSING`. Every assertion
reads a value that exists on both trees (contract keys and status STRINGS,
never a new module symbol), so a pre-fix failure is a behaviour disagreement
and never an AttributeError.

NEVER GREENER: the verdict stays INCOMPLETE, the row count and the
`property_denominator` are identical before and after, and no population is
narrowed. One row CHANGES ITS NAME; none is removed.

chip-AGNOSTIC: neutral layer/file names and a generic declaration shape; no
vendor, PDK, SKU or design literal.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import formal_harness_gen as F  # noqa: E402

MISSING = "DECLARATION_MISSING"
ROOT_ABSENT = "DECLARATION_ROOT_ABSENT"
ROOT_UNREADABLE = "DECLARATION_ROOT_UNREADABLE"

#: A directory this process may write and traverse but NOT enumerate. Running
#: as uid 0 defeats every permission arm, so those tests are skipped rather
#: than passing vacuously.
_NO_LIST = 0o300
_needs_permissions = pytest.mark.skipif(
    os.geteuid() == 0, reason="uid 0 ignores the read bit; the arm is vacuous")

_L3 = json.dumps({"no_opcodes_in_input": False,
                  "opcodes": [{"name": "op_a", "purpose": "declared behavior"}]})
_L6 = json.dumps({"no_fsm_in_input": False,
                  "fsm_states": [{"name": "s_idle", "transitions": ["s_run"]}]})
_L8 = json.dumps({"clock_and_reset_waveform": {
    "resets": [{"name": "rst_a", "polarity": "active_high"}]}})
_DOCS = {"L3_A.json": _L3, "L6_B.json": _L6, "L8_C.json": _L8}


def _project(tmp_path: Path, docs=_DOCS) -> Path:
    """`phase2/stage1/rtl` exists and is EMPTY — the tree #2183's run was in
    (its `rtl_gen` had WAIVED to an author), and the branch of `generate()`
    that wrote the reported contract."""
    project = tmp_path / "proj"
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    gd = project / "phase1/generated_docs"
    gd.mkdir(parents=True)
    for name, body in (docs or {}).items():
        (gd / name).write_text(body)
    return project


def _docs_dir(project: Path) -> Path:
    return project / "phase1/generated_docs"


def _contract(project: Path) -> dict:
    return json.loads(
        (project / "phase2/stage1/formal/property_contract.json").read_text())


@pytest.fixture
def unlistable(tmp_path):
    """A project whose declarations are PRESENT and whose root cannot be listed.

    Restores the mode afterwards so the tmp tree can be cleaned up.
    """
    project = _project(tmp_path)
    gd = _docs_dir(project)
    os.chmod(gd, _NO_LIST)
    try:
        yield project
    finally:
        os.chmod(gd, 0o700)


# ── the reported artefact, from a root whose declarations are present ───────

@_needs_permissions
def test_ROOT_UNREADABLE_is_not_reported_as_an_absent_declaration(unlistable):
    """The headline. Same bytes on disk, only the root's read bit removed."""
    decl = F.declaration_obligations(unlistable)

    rows = F._unresolved_declaration_rows(decl)
    assert [r["status"] for r in rows] == [ROOT_UNREADABLE] * 3, rows
    assert MISSING not in {r["status"] for r in rows}


@_needs_permissions
def test_ROOT_UNREADABLE_row_says_the_read_failed_and_names_the_root(unlistable):
    F.generate(project=unlistable, top="dut")

    rows = _contract(unlistable)["unresolved_obligations"]
    assert {r["source"] for r in rows} == {str(_docs_dir(unlistable))}
    for row in rows:
        # the sentence a reader acts on must not say the design declared
        # nothing, and must not read as "there is no such directory" either
        assert "declaration is absent" not in row["description"]
        assert "does not exist" not in row["description"]
        assert "could not be read" in row["description"]


@_needs_permissions
def test_ROOT_UNREADABLE_contract_carries_the_error_it_hit(unlistable):
    out = F.generate(project=unlistable, top="dut")
    assert out["verdict"] == "INCOMPLETE"              # never greener

    contract = _contract(unlistable)
    assert contract.get("declaration_root") == str(_docs_dir(unlistable))
    err = contract.get("declaration_root_error")
    assert err and "Error" in err, err


@_needs_permissions
def test_ROOT_UNREADABLE_handoff_carries_it_too(unlistable):
    """The hand-off is what an expert is asked to answer. #2183's harm was a
    request to author properties for declarations that are not missing."""
    F.generate(project=unlistable, top="dut")

    request = json.loads(
        (unlistable / "phase2/stage1/formal" / F.AUTHORING_REQUEST).read_text())
    assert request.get("declaration_root") == str(_docs_dir(unlistable))
    assert request.get("declaration_root_error")
    assert {r["status"] for r in request["unresolved_obligations"]} == {
        ROOT_UNREADABLE}


@_needs_permissions
def test_ROOT_UNREADABLE_the_declarations_really_were_there(unlistable):
    """The positive control for the arm itself: this is not an empty directory
    dressed up. Every declaration is still openable BY NAME while the run
    reports it unread — which is exactly why the empty listing was a lie."""
    gd = _docs_dir(unlistable)
    for name, body in _DOCS.items():
        assert (gd / name).read_text() == body

    with pytest.raises(OSError):
        os.listdir(gd)


@_needs_permissions
def test_ROOT_UNREADABLE_a_stat_that_fails_is_not_an_absent_root(tmp_path):
    """The second failed-read door into the same function: `os.stat` raising
    EACCES because an ANCESTOR cannot be traversed.

    This one was never silent — `Path.is_dir()` propagates EACCES (pathlib
    ignores only ENOENT/ENOTDIR/ELOOP/EBADF), so pre-fix the exception escaped
    `declaration_obligations` and the RUNNER's blanket handler wrote a
    `formal.authoring_program_error` row carrying a `repr()`. Step 5 named no
    root and no layer. Post-fix the same condition is three rows that name the
    directory, the error and the three layers nobody could read — a HIGHER
    denominator, same INCOMPLETE verdict."""
    project = _project(tmp_path)
    phase1 = project / "phase1"
    os.chmod(phase1, 0o600)                      # readable, not traversable
    try:
        decl = F.declaration_obligations(project)
        rows = F._unresolved_declaration_rows(decl)
        assert [r["status"] for r in rows] == [ROOT_UNREADABLE] * 3, rows
    finally:
        os.chmod(phase1, 0o700)


# ── CONTROL: the states this must NOT swallow ──────────────────────────────

def test_CONTROL_a_genuinely_undeclared_layer_is_still_DECLARATION_MISSING(
        tmp_path):
    """Without this, "stop emitting DECLARATION_MISSING" would satisfy the
    tests above and destroy the finding they exist to preserve."""
    project = _project(tmp_path, docs={})
    decl = F.declaration_obligations(project)

    assert decl["declaration_root_present"] is True
    assert decl.get("declaration_root_error") is None
    assert [r["status"] for r in F._unresolved_declaration_rows(decl)] == \
        [MISSING] * 3


def test_CONTROL_a_root_that_is_really_absent_is_still_ROOT_ABSENT(tmp_path):
    """The two failed-read states stay distinct: "there is no such directory"
    is a different finding from "I could not read the directory"."""
    project = _project(tmp_path)
    os.rename(_docs_dir(project), project / "phase1/moved_away")
    decl = F.declaration_obligations(project)

    assert decl.get("declaration_root_error") is None
    assert [r["status"] for r in F._unresolved_declaration_rows(decl)] == \
        [ROOT_ABSENT] * 3


def test_CONTROL_a_readable_root_still_yields_its_obligations(tmp_path):
    """The behaviour that must not move: a normal project is unaffected."""
    project = _project(tmp_path)
    decl = F.declaration_obligations(project)

    assert decl["missing_declarations"] == []
    assert decl.get("declaration_root_error") is None
    assert len(decl["obligations"]) >= 3
    assert F._unresolved_declaration_rows(decl) == []


# ── the refactor's own guard ───────────────────────────────────────────────

def test_the_listing_selects_exactly_what_the_glob_selected(tmp_path):
    """`os.listdir` + prefix/suffix replaced `Path.glob("L*.json")`. If the two
    ever disagree the fix would silently change the DENOMINATOR — a far worse
    outcome than the row it was written to rename — and nothing else here would
    notice, because every other test uses names both forms obviously accept."""
    project = _project(tmp_path, docs={})
    gd = _docs_dir(project)
    body = json.dumps({"fields": {}})
    for name in ("L3.json", "L3_a.json", "L3-b.json", "L3.json.bak",
                 "L30_c.json", "l3_lower.json", "L3", "xL3.json",
                 "L3_d.JSON", "L6_e.json", "L8_f.json", "L22_g.json",
                 "L3_h.json.json", ".L3_hidden.json"):
        (gd / name).write_text(body)
    (gd / "L3_dir.json").mkdir()

    for layer in F.DECLARATION_LAYERS:
        by_glob = sorted(p.name for p in gd.glob(f"{layer}*.json"))
        by_list = sorted(n for n in os.listdir(gd)
                         if n.startswith(layer) and n.endswith(".json"))
        assert by_glob == by_list, (layer, by_glob, by_list)

    # and the real reader agrees with both, including on the directory named
    # like a declaration — it is READ and FAILS, never silently dropped.
    # Unpacked by INDEX so this guard runs on the pre-fix 4-tuple too: the
    # selection it pins is the half that must NOT have moved.
    result = F._read_l_docs(project)
    docs, root, present, unreadable = result[:4]
    assert (root, present) == (gd, True)
    read = {p.name for pairs in docs.values() for p, _ in pairs}
    assert read == set(sorted(n for n in os.listdir(gd)
                              if any(n.startswith(x) for x in F.DECLARATION_LAYERS)
                              and n.endswith(".json"))) - {"L3_dir.json"}
    assert [r["path"] for r in unreadable] == [str(gd / "L3_dir.json")]
