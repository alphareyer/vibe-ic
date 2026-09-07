#!/usr/bin/env python3
"""vibe-ic#2128 — L19 must record WHICH design documents its emitter read.

THE MEASURED CONFUSION THIS CLOSES
----------------------------------
`l19_constraint_token_emit` already reads every input document
(`l_doc_consumer_contract.input_doc_texts` globs `input/docs/*` alongside
`phase1/input_doc/*`), and on the published corpus 699 emitted records cite
an `input/docs/` path. But it records only the documents that YIELDED a
record, and when a design states nothing in an admitted shape it writes
nothing at all. So a layer whose every record happens to cite the prompt is,
FROM THE LAYER, indistinguishable from one whose emitter never opened the
design documents.

Measured on `benchmark-data/ic/opentitan_aes` at this base: the emitter opens
all eight design documents under `input/docs/`, they yield zero constraint
declarations, and all three implementation-context records cite the prompt.
That output was read as "the design docs are never opened" and filed as
vibe-ic#2128. The read set is the one field that separates the two readings.

BOTH DIRECTIONS, and neither is decorative:
  * a fact stated ONLY in a design document reaches L19 with its own
    provenance — the direct refutation of "the docs are never opened", and a
    regression test for a capability that had none;
  * a fact stated NOWHERE stays absent — the emitter never invents a default
    to fill a field. The mutation that would make this red is the one the
    issue asked for: have the emitter guess.

Every fixture is synthesized neutral text; the configuration key is invented
so the extractor is exercised on the SHAPE of a setting, not on any tool's
variable names. chip-AGNOSTIC.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import l19_constraint_token_emit as EMIT        # noqa: E402

_L19 = "L19_CONSTRAINTS_PDK.json"

_DOC_STATES_IT = """# Floorplan and Placement

The block is placed at a fixed core utilisation.

CORE_UTIL_TARGET = 45
"""

_DOC_STATES_NOTHING = """# Overview

The block converts one representation of the payload into another and
raises a completion indication when it is done.
"""

_PROMPT = "Build the block described in the accompanying documents.\n"


def _project(tmp_path: Path, doc_text: str, *, prompt: str = _PROMPT,
             l19: dict | None = None) -> Path:
    root = tmp_path / "proj"
    docs = root / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "design_notes.md").write_text(doc_text, encoding="utf-8")
    inp = root / "phase1" / "input_doc"
    inp.mkdir(parents=True)
    (inp / "phase1_prompt.txt").write_text(prompt, encoding="utf-8")
    gen = root / "phase1" / "generated_docs"
    gen.mkdir(parents=True)
    (gen / _L19).write_text(json.dumps(
        l19 if l19 is not None
        else {"layer": "L19", "extraction_status": "NOT_YET_EXTRACTED",
              "fields": {}}), encoding="utf-8")
    return root


def _l19(project: Path) -> dict:
    return json.loads(
        (project / "phase1" / "generated_docs" / _L19).read_text(
            encoding="utf-8"))


def _consulted(doc: dict) -> dict:
    rows = doc.get("documents_consulted")
    assert isinstance(rows, list), f"documents_consulted absent: {sorted(doc)}"
    return {r["source"]: r for r in rows}


# ─────────────────────────────────────────────────────────────────────
# Direction 1 — a fact stated ONLY in a design document reaches L19
# ─────────────────────────────────────────────────────────────────────

def test_a_fact_stated_only_in_a_doc_reaches_l19_with_its_provenance(
        tmp_path: Path) -> None:
    project = _project(tmp_path, _DOC_STATES_IT)
    rep = EMIT.run(project)

    tokens = [r for r in rep["emitted"] if r.get("token") == "CORE_UTIL_TARGET"]
    assert tokens, (
        "a setting stated only in input/docs/ did not reach L19: "
        f"{rep['emitted']}")
    rec = tokens[0]
    assert rec["value"] == "45"
    # Provenance is the document, project-relative, with its own line.
    assert rec["source"] == "input/docs/design_notes.md", rec
    assert rec["line"] == 5, rec
    assert (_DOC_STATES_IT.splitlines()[rec["line"] - 1].strip()
            in rec["evidence"])

    doc = _l19(project)
    assert "input/docs/design_notes.md" in doc["source_documents"]


def test_the_read_set_names_every_document_and_which_ones_yielded(
        tmp_path: Path) -> None:
    project = _project(tmp_path, _DOC_STATES_IT)
    EMIT.run(project)
    rows = _consulted(_l19(project))

    # MEMBERSHIP, not a count: the prompt AND the document, each named.
    assert set(rows) == {"phase1/input_doc/phase1_prompt.txt",
                         "input/docs/design_notes.md"}, sorted(rows)
    assert rows["input/docs/design_notes.md"]["yielded"] is True
    assert rows["phase1/input_doc/phase1_prompt.txt"]["yielded"] is False


def test_a_document_that_states_nothing_is_still_recorded_as_read(
        tmp_path: Path) -> None:
    """The whole point: 'read and it states nothing' must be sayable."""
    project = _project(tmp_path, _DOC_STATES_NOTHING)
    EMIT.run(project)
    rows = _consulted(_l19(project))
    assert "input/docs/design_notes.md" in rows, sorted(rows)
    assert rows["input/docs/design_notes.md"]["yielded"] is False


# ─────────────────────────────────────────────────────────────────────
# Direction 2 — a fact stated NOWHERE is never invented
# ─────────────────────────────────────────────────────────────────────

def test_a_fact_absent_everywhere_is_not_invented(tmp_path: Path) -> None:
    project = _project(tmp_path, _DOC_STATES_NOTHING)
    rep = EMIT.run(project)

    assert rep["constraint_emitted_count"] == 0, rep["emitted"]
    fields = _l19(project).get("fields", {})
    assert "constraint_declarations" not in fields, fields
    # The falsehood the layer used to state is not restated as a truth
    # either: with no evidence, the presence flag is not set at all.
    assert "constraints_present" not in fields, fields


def test_the_read_set_never_advances_the_extraction_status(
        tmp_path: Path) -> None:
    """Reading a document is not extracting from it."""
    project = _project(
        tmp_path, _DOC_STATES_NOTHING,
        # `clock_target` already owned, so the read set is the ONLY write.
        l19={"layer": "L19", "extraction_status": "NOT_YET_EXTRACTED",
             "fields": {"clock_target": {"status": "NOT_STATED"}}})
    EMIT.run(project)
    doc = _l19(project)
    assert doc["extraction_status"] == "NOT_YET_EXTRACTED", doc
    assert doc["fields"] == {"clock_target": {"status": "NOT_STATED"}}, doc
    assert "source_documents" not in doc, sorted(doc)
    assert _consulted(doc)  # ... and the read set IS there.


def test_the_read_set_is_idempotent_and_dry_run_writes_nothing(
        tmp_path: Path) -> None:
    project = _project(tmp_path, _DOC_STATES_IT)
    EMIT.run(project)
    first = _l19(project)
    assert EMIT.run(project)["doc_written"] is None
    assert _l19(project) == first

    fresh = _project(tmp_path / "b", _DOC_STATES_IT)
    rep = EMIT.run(fresh, dry_run=True)
    assert rep["documents_consulted_count"] == 2, rep["documents_consulted"]
    assert "documents_consulted" not in _l19(fresh)


def test_a_foreign_non_list_documents_consulted_is_never_reshaped(
        tmp_path: Path) -> None:
    project = _project(
        tmp_path, _DOC_STATES_NOTHING,
        l19={"layer": "L19", "extraction_status": "NOT_YET_EXTRACTED",
             "documents_consulted": "owned by another producer",
             "fields": {"clock_target": {"status": "NOT_STATED"}}})
    EMIT.run(project)
    assert _l19(project)["documents_consulted"] == "owned by another producer"


def test_the_read_set_is_derived_from_the_same_read_the_collectors_use(
        tmp_path: Path) -> None:
    """It cannot drift from the read it describes."""
    from l_doc_consumer_contract import input_doc_texts
    project = _project(tmp_path, _DOC_STATES_IT)
    rep = EMIT.run(project)
    assert ([r["source"] for r in rep["documents_consulted"]]
            == [str(p.relative_to(project)) for p, _ in
                input_doc_texts(project)])
