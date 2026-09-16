"""A generated document cannot declare what the design never said.

R-0915-64. MEASURED on the subservient tapeout run r27 (lane icsub2,
2026-09-16, gf180mcuD): FIVE steps — 11 (DFT insertion), 12 (post-DFT
optimisation), DT1, DT2 and DT3 (the ATPG steps) — stood down as
`SKIPPED-CONDITION` citing

    design-declared NOT_APPLICABLE: phase1/generated_docs/
    L20_DFT_SCAN_TOPOLOGY.json records bist_mbist=[], dft_present=False,
    jtag_tap=None, scan_chains=[], so this step's outputs are not owed by
    this design.

That document is a SKELETON, and its own body says so: `extraction_status:
NOT_YET_EXTRACTED`, `source_documents: []`, `extraction_evidence: {}`,
`emitted_by: phase1_post_process.emit_l_doc_skeleton`. All four field values
the citation quotes are the skeleton's INITIALISERS. Nobody read the design's
input; five steps' worth of outputs stopped being owed on the strength of a
default.

THE FIX IS A SECOND NECESSARY CONDITION, NOT A REPLACEMENT. The L-doc fields
stay exactly as load-bearing as they were — one field asserting a scan chain, a
TAP or a BIST block still makes the outputs owed. What is ADDED is the fact the
record was missing: the design's OWN INPUT was searched for this layer's
subject and carried none of it, with the documents (by path), the terms, and
the zero hits recorded — the R-0915-36 shape, asked through R-0915-36's OWN
scanner so the phase-1 emitter and the audit cannot disagree about one design's
input.

STRICTLY CONSERVATIVE: this can only REFUSE an N/A that used to be granted,
never grant one that used to be refused.

chip-AGNOSTIC: synthetic input corpora in tmp_path, plus the real subservient
input documents when they are present on the host.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import phase1_post_process as P  # noqa: E402
import flow_compliance_check as F  # noqa: E402

#: The condition the flow declares at all five DFT step sites.
SPEC = {
    "l_doc": "L20",
    "all_absent": {
        "dft_present": False,
        "scan_chains": [],
        "bist_mbist": [],
        "jtag_tap": None,
    },
}

#: The skeleton r27 actually carried, field for field.
SKELETON = {
    "doc_id": "L20",
    "doc_name": "L20_DFT_SCAN_TOPOLOGY",
    "applicability": "APPLICABLE",
    "extraction_status": "NOT_YET_EXTRACTED",
    "source_documents": [],
    "extraction_evidence": {},
    "fields": {
        "scan_chains": [],
        "test_compression": None,
        "bist_mbist": [],
        "jtag_tap": None,
        "dft_present": False,
        "notes": "Spec does not specify DFT/scan topology.",
    },
}


def _project(tmp_path: Path, docs: dict, l20=SKELETON) -> Path:
    proj = tmp_path / "proj"
    (proj / "input" / "docs").mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (proj / "input" / "docs" / name).write_text(text, encoding="utf-8")
    gen = proj / "phase1" / "generated_docs"
    gen.mkdir(parents=True, exist_ok=True)
    if l20 is not None:
        (gen / "L20_DFT_SCAN_TOPOLOGY.json").write_text(json.dumps(l20))
    return proj


NO_DFT = {
    "L1_product_metadata.md": "# Part\nA small RISC-V core.\n",
    "L2_architecture.md": "It fetches, decodes and executes.\n",
    "L3_external_interface.md": "Pins: i_clk, i_rst, o_gpio.\n",
}


# ── the repair: the record now rests on the INPUT ────────────────────────────
def test_the_declaration_cites_the_input_not_the_skeleton(tmp_path):
    proj = _project(tmp_path, NO_DFT)
    got = F._l_doc_declares_absence(proj, SPEC)
    assert got is not None, "an input with no DFT term must still reach N/A"
    cited, detail, evidence = got
    # The L-doc corroboration is kept — it is still necessary.
    assert cited.endswith("L20_DFT_SCAN_TOPOLOGY.json")
    assert "dft_present=False" in detail
    # ...and the INPUT-level fact is now present, by path.
    assert evidence["documents_matching"] == 0
    assert evidence["documents_scanned_count"] == 3
    assert evidence["documents_scanned"] == [
        "input/docs/L1_product_metadata.md",
        "input/docs/L2_architecture.md",
        "input/docs/L3_external_interface.md",
    ]
    assert [t.lower() for t in evidence["terms_searched"]] == [
        "scan", "dft", "bist", "jtag", "atpg", "test mode"]


# ── the other direction: an input that DOES mention DFT refuses the N/A ──────
@pytest.mark.parametrize("sentence", [
    "The part provides a scan chain for production test.",
    "A DFT wrapper is required.",
    "Memory is covered by BIST.",
    "Debug is over JTAG.",
    "Production test uses ATPG vectors.",
    "Asserting TEST MODE gates the functional clock.",
])
def test_one_mention_in_one_input_document_runs_the_step(tmp_path, sentence):
    """The skeleton is IDENTICAL in every case — it always says no DFT. Only
    the input differs, which is the whole point: the input decides."""
    docs = dict(NO_DFT)
    docs["L7_verification_plan.md"] = sentence + "\n"
    proj = _project(tmp_path, docs)
    assert F._l_doc_declares_absence(proj, SPEC) is None, sentence


def test_the_skeleton_alone_is_no_longer_enough(tmp_path):
    """The exact r27 shape with NO readable input corpus. Before R-0915-64
    this granted the N/A on the skeleton's initialisers alone."""
    proj = tmp_path / "bare"
    gen = proj / "phase1" / "generated_docs"
    gen.mkdir(parents=True)
    (gen / "L20_DFT_SCAN_TOPOLOGY.json").write_text(json.dumps(SKELETON))
    assert F._l_doc_declares_absence(proj, SPEC) is None


def test_an_empty_input_corpus_is_not_a_declaration(tmp_path):
    """A scan with no denominator declares nothing."""
    proj = _project(tmp_path, {})
    assert F._l_doc_declares_absence(proj, SPEC) is None


# ── the L-doc half must keep every refusal it already had ────────────────────
@pytest.mark.parametrize("field,value", [
    ("dft_present", True),
    ("scan_chains", [{"name": "chain0"}]),
    ("bist_mbist", ["mbist0"]),
    ("jtag_tap", {"ir_width": 4}),
])
def test_an_asserting_l_doc_field_still_owes_the_outputs(tmp_path, field, value):
    """NEGATIVE CONTROL for the half that already worked: even with an input
    that mentions nothing, a document that ASSERTS the capability runs the
    step. R-0915-64 added a condition; it removed none."""
    doc = json.loads(json.dumps(SKELETON))
    doc["fields"][field] = value
    proj = _project(tmp_path, NO_DFT, l20=doc)
    assert F._l_doc_declares_absence(proj, SPEC) is None, field


def test_a_missing_l_doc_still_runs_the_step(tmp_path):
    proj = _project(tmp_path, NO_DFT, l20=None)
    assert F._l_doc_declares_absence(proj, SPEC) is None


def test_an_unregistered_l_doc_has_no_corroboration_and_so_no_na(tmp_path):
    """FAIL-CLOSED BY CONSTRUCTION. A layer with no registered term set cannot
    be corroborated against the input, so it cannot stand a step down."""
    doc = json.loads(json.dumps(SKELETON))
    doc["doc_id"] = "L21"
    doc["doc_name"] = "L21_POWER_INTENT"
    proj = _project(tmp_path, NO_DFT)
    gen = proj / "phase1" / "generated_docs"
    (gen / "L21_POWER_INTENT.json").write_text(json.dumps(doc))
    assert P._DECLARATION_CORROBORATION_TERMS.get("L21") is None
    assert F._l_doc_declares_absence(
        proj, dict(SPEC, l_doc="L21")) is None


# ── the terms are the layer's own, not this file's opinion ───────────────────
def test_the_corroboration_terms_come_from_the_layers_own_hints():
    hints = " ".join(P._extraction_hints_for("L20")).lower()
    for term in P._DECLARATION_CORROBORATION_TERMS["L20"]:
        assert term.lower() in hints, (term, hints)


def test_there_is_one_scanner_not_two():
    """Both consumers reach the same function, so the phase-1 emitter and the
    audit cannot disagree about one design's input."""
    assert P.declaration_corroboration.__module__ == P.__name__
    assert P._input_carries_subject.__module__ == P.__name__
    src = Path(P.__file__).read_text()
    assert src.count("def scan_input_for_terms(") == 1


# ── and on the real design's own input, vendored so it always runs ──────────
#
# THE FIRST VERSION OF THIS SECTION WAS WRONG, and it is worth saying how. It
# read `<home>/benchmark-data/...` behind
# `@pytest.mark.skipif(not REAL.is_dir())`. That is an environment-gated skip on
# a path that exists on exactly one machine: everywhere else the case did not
# run and the file still reported green, which is the one thing a measuring
# host must never be told. `benchmark-data` is not in this repo at all — not a
# directory, not a submodule entry — so the gate would have been open on every
# host but the one it was written on.
#
# So the corpus is VENDORED, under this test's own directory, and the case
# always runs. INPUT ONLY: these are the design's own specification documents,
# which is what the scanner reads and what §4.05 permits. No oracle, no
# harness, no golden.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "subservient_input_docs"

#: What the REAL corpus measured, 2026-09-16, at benchmark-data origin/main
#: 98662b7419765432028b929323b42eccd9f0a507. Each excerpt carries its source
#: document's full sha256 and line count in its own header, so the vendoring is
#: re-derivable rather than asserted. Re-derive with:
#:     git -C benchmark-data show origin/main:ic/subservient/input/docs/<name>
_REAL_CORPUS = {
    "L1_product_metadata.md": 63,
    "L2_architecture.md": 84,
    "L3_external_interface.md": 74,
    "L4_command_protocol.md": 23,
    "L5_register_map.md": 24,
    "L6_calibration.md": 20,
    "L7_verification_plan.md": 123,
    "L8_submodule_integration.md": 84,
    "L9_constraints_floorplan.md": 101,
}


def _vendored_project(tmp_path: Path) -> Path:
    """The design's nine input documents, staged as a project."""
    import shutil
    proj = tmp_path / "subservient"
    (proj / "input").mkdir(parents=True)
    shutil.copytree(FIXTURE, proj / "input" / "docs")
    return proj


def test_the_vendored_corpus_is_the_designs_nine_documents():
    """A missing fixture is a RED, not a skip. If this directory is gone the
    case below is measuring nothing, and it must say so rather than pass."""
    assert FIXTURE.is_dir(), f"vendored input corpus is missing: {FIXTURE}"
    present = sorted(p.name for p in FIXTURE.glob("*.md"))
    assert present == sorted(_REAL_CORPUS), present
    for name in present:
        header = (FIXTURE / name).read_text(encoding="utf-8")[:400]
        # Provenance is part of the fixture, not a comment about it.
        assert "benchmark-data origin/main" in header, name
        assert f"ic/subservient/input/docs/{name}" in header, name
        assert "sha256 :" in header, name
        assert f"document is {_REAL_CORPUS[name]} line(s)" in header, name


def test_the_real_subservient_input_earns_its_na(tmp_path):
    """The design this ruling came from. Its N/A is EARNED — nine documents
    scanned, six terms, zero hits — which is exactly why the old citation was
    never noticed: the conclusion was right and the basis was not."""
    proj = _vendored_project(tmp_path)
    ev = P.declaration_corroboration("L20", proj)
    assert ev is not None
    assert ev["documents_matching"] == 0
    assert ev["documents_scanned_count"] == 9
    assert ev["documents_scanned"] == [
        f"input/docs/{name}" for name in sorted(_REAL_CORPUS)]


def test_the_real_corpus_does_not_even_contain_the_terms_as_substrings():
    """MEASURED on the real documents at benchmark-data origin/main: zero
    occurrences of any of the six terms, as SUBSTRINGS, across all 596 lines —
    so this design's silence about DFT does not depend on the word-boundary
    rule. Held over the vendored excerpts, which must inherit that silence or
    they are not a faithful stand-in."""
    terms = P._DECLARATION_CORROBORATION_TERMS["L20"]
    for doc in sorted(FIXTURE.glob("*.md")):
        body = doc.read_text(encoding="utf-8").split("-->", 1)[-1].lower()
        for term in terms:
            assert term.lower() not in body, (doc.name, term)


def test_the_whole_declaration_holds_on_the_real_input(tmp_path):
    """End to end on the design's own documents: the record the five DFT steps
    would carry, built from this corpus."""
    proj = _vendored_project(tmp_path)
    gen = proj / "phase1" / "generated_docs"
    gen.mkdir(parents=True)
    (gen / "L20_DFT_SCAN_TOPOLOGY.json").write_text(json.dumps(SKELETON))
    got = F._l_doc_declares_absence(proj, SPEC)
    assert got is not None
    _cited, _detail, ev = got
    assert ev["documents_scanned_count"] == 9
    assert ev["documents_matching"] == 0

    # ...and ONE DFT sentence added to ONE of those nine documents refuses it.
    l7 = proj / "input" / "docs" / "L7_verification_plan.md"
    l7.write_text(l7.read_text(encoding="utf-8")
                  + "\nProduction test uses a scan chain.\n", encoding="utf-8")
    assert F._l_doc_declares_absence(proj, SPEC) is None
