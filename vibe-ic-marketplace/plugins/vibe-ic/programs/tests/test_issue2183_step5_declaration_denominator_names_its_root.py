"""vibe-ic#2183 — Step 5's declaration denominator must say WHAT IT READ.

MEASURED (lane cz2183, 8HD-6, 2026-09-07, base e2b3c08170b5 / tree
75d478b34c17, image `192.168.1.112:5000/vibeic-eda@sha256:89a8fd7295…`
read from `_eda_pin.IMAGE_DIGEST`; project `spm` x `gf180mcuD` driven through
`vibe_ic_one_shot_runner.py`):

#2183 reported `property_contract.json` carrying `property_denominator 3`,
`authored_property_count 0` and three rows reading

    "<L> declaration is absent; applicability and property are unknown"

for a project whose L3/L6/L8 declarations are present and yield FOUR
obligations. The artefact named no directory, so the run could not name its
own mechanism and the issue had to guess at one.

`declaration_obligations` collapsed THREE different input states into that one
sentence:

  1. the Phase-1 declaration ROOT does not exist — nothing was read;
  2. the root exists and holds an `L*.json` that could not be opened or parsed
     — `_read_l_docs` swallowed the exception with a bare `continue`, so an
     unparseable declaration and an undeclared layer printed the same row;
  3. the root exists, every file was read, and the design declares nothing.

Only (3) is "absent". (1) and (2) are "I could not read it", which this repo
holds apart from "I read it and it was empty" everywhere else. This file pins
that they are held apart here, and that every artefact Step 5 writes NAMES the
root it globbed.

FALSIFICATION, two-tree and MEASURED on this host: the five tests whose names
carry `ROOT_ABSENT` / `UNREADABLE` FAIL against the pre-fix
`formal_harness_gen.py` taken from `e2b3c08170b5`; the two `CONTROL` tests
pass on BOTH trees. Every assertion below reads a value that exists on both
trees (contract keys and status STRINGS, not new module symbols), so a pre-fix
failure is a behaviour disagreement and never an AttributeError.

NEVER GREENER: every state below is still an UNRESOLVED row on an INCOMPLETE
verdict. An unreadable declaration ADDS a row where it used to add silence; no
population is narrowed and no threshold moved.

chip-AGNOSTIC: neutral layer/file names and a generic declaration shape; no
vendor, PDK, SKU or design literal.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import formal_harness_gen as F  # noqa: E402

ROOT_ABSENT = "DECLARATION_ROOT_ABSENT"
UNREADABLE = "DECLARATION_UNREADABLE"
MISSING = "DECLARATION_MISSING"

_L3 = json.dumps({"no_opcodes_in_input": False,
                  "opcodes": [{"name": "op_a", "purpose": "declared behavior"}]})
_L6 = json.dumps({"no_fsm_in_input": False,
                  "fsm_states": [{"name": "s_idle", "transitions": ["s_run"]}]})
_L8 = json.dumps({"clock_and_reset_waveform": {
    "resets": [{"name": "rst_a", "polarity": "active_high"}]}})


def _project(tmp_path: Path, docs) -> Path:
    """`docs` None => no `phase1/generated_docs` directory at all (state 1).

    `phase2/stage1/rtl` is created and left EMPTY on purpose: that is the tree
    #2183's run was measured in (its `rtl_gen` had WAIVED to an author), and it
    is the branch of `generate()` that wrote the reported contract.
    """
    project = tmp_path / "proj"
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    if docs is not None:
        gd = project / "phase1/generated_docs"
        gd.mkdir(parents=True)
        for name, body in docs.items():
            (gd / name).write_text(body)
    return project


def _contract(project: Path) -> dict:
    return json.loads(
        (project / "phase2/stage1/formal/property_contract.json").read_text())


# ── (1) the declaration root does not exist ────────────────────────────────

def test_ROOT_ABSENT_is_reported_as_unread_not_as_undeclared(tmp_path):
    project = _project(tmp_path, None)
    decl = F.declaration_obligations(project)

    assert decl.get("declaration_root_present") is False
    assert decl.get("declaration_root") == str(
        project / "phase1/generated_docs")


def test_ROOT_ABSENT_property_contract_NAMES_the_root_it_measured(tmp_path):
    project = _project(tmp_path, None)
    out = F.generate(project=project, top="dut")

    assert out["verdict"] == "INCOMPLETE"            # never greener
    contract = _contract(project)
    assert contract.get("declaration_root") == str(
        project / "phase1/generated_docs")
    assert contract.get("declaration_root_present") is False


def test_ROOT_ABSENT_rows_do_not_claim_the_design_declared_nothing(tmp_path):
    project = _project(tmp_path, None)
    F.generate(project=project, top="dut")

    rows = _contract(project)["unresolved_obligations"]
    assert {r["status"] for r in rows} == {ROOT_ABSENT}
    # each row points at the directory that was globbed, so a reader following
    # the citation lands on the phantom root instead of on a phantom design.
    assert {r["source"] for r in rows} == {
        str(project / "phase1/generated_docs")}


def test_ROOT_ABSENT_authoring_request_NAMES_the_root(tmp_path):
    project = _project(tmp_path, None)
    F.generate(project=project, top="dut")

    request = json.loads(
        (project / "phase2/stage1/formal" / F.AUTHORING_REQUEST).read_text())
    assert request.get("declaration_root_present") is False
    assert request.get("declaration_root") == str(
        project / "phase1/generated_docs")


# ── (2) the declaration exists and could not be read ───────────────────────

def test_UNREADABLE_declaration_is_not_reported_as_absent(tmp_path):
    project = _project(tmp_path, {"L3_X.json": "{ this is not json",
                                  "L6_Y.json": _L6, "L8_Z.json": _L8})
    F.generate(project=project, top="dut")
    contract = _contract(project)

    # the layer was READ and it failed; it is not an undeclared layer.
    assert "L3" not in contract["missing_declarations"]
    unreadable = contract.get("unreadable_declarations") or []
    assert [r["layer"] for r in unreadable] == ["L3"]
    assert unreadable[0]["path"] == str(
        project / "phase1/generated_docs/L3_X.json")
    assert "JSONDecodeError" in unreadable[0]["error"]
    rows = [r for r in contract["unresolved_obligations"]
            if r["status"] == UNREADABLE]
    assert len(rows) == 1
    assert rows[0]["source"] == unreadable[0]["path"]


# ── CONTROLS — these pass on BOTH trees ────────────────────────────────────

def test_CONTROL_genuinely_undeclared_layers_still_read_declaration_missing(
        tmp_path):
    project = _project(tmp_path, {"L9_Z.json": "{}"})
    F.generate(project=project, top="dut")
    contract = _contract(project)

    assert contract["missing_declarations"] == ["L3", "L6", "L8"]
    rows = contract["unresolved_obligations"]
    assert [r["status"] for r in rows] == [MISSING] * 3
    assert [r["id"] for r in rows] == ["L3.declaration_missing",
                                       "L6.declaration_missing",
                                       "L8.declaration_missing"]


def test_CONTROL_present_declarations_still_yield_their_obligations(tmp_path):
    project = _project(tmp_path, {"L3_A.json": _L3, "L6_B.json": _L6,
                                  "L8_C.json": _L8})
    decl = F.declaration_obligations(project)

    assert decl["missing_declarations"] == []
    assert len(decl["obligations"]) >= 3
    F.generate(project=project, top="dut")
    contract = _contract(project)
    assert contract["property_denominator"] == len(decl["obligations"])
    assert {r["status"] for r in contract["unresolved_obligations"]} == {
        "UNAUTHORED"}
