"""R-0915-34(b): P0 sub-gates whose population an L-doc declares zero.

MEASURED 2026-09-15 (lane icspm3) on `spm` x gf180mcuD, a serial-parallel
multiplier with no register map, no DFT and no security asset. Four P0
sub-gates exited rc=2 and the umbrella booked all four ``EXECUTION_ERROR``::

    $ python3 programs/l4_regmap_declared_register_coverage_check.py <run>
    [SKIP] ... the input states no register-map denominator for L4 to be
           measured against                                            rc=2
    $ python3 programs/l4_regmap_phase2_emitter_contract_check.py <run>
    [SKIP] L4 declares no registers[] (no_registers_in_input=False,
           register_map_present=False)                                 rc=2
    $ python3 programs/l20_dft_scan_topology_actionable_check.py <run>
    [SKIP] no DFT requirement derivable ... and L20 asserts none        rc=2
    $ python3 programs/l23_security_requirements_typed_check.py <run>
    [SKIP] design declares no security-relevant asset and L23 asserts none rc=2

``EXECUTION_ERROR`` says the PROGRAM failed. None of them did: each read the
design, found no instance of its subject, and said so. rc=2 carries both
meanings in this flow, and the umbrella was resolving the ambiguity the wrong
way.

R-0915-34(b) is the rule applied here: a zero population is a design
declaration only when THE DECLARING DOCUMENT EXISTS, parses, and positively
states it. So the disposition is bound to evidence, not to a reason token --
``applicability_evidence`` of kind ``design-declared-zero-population``, naming
the document, the fields counted, the count, and the assertions that make it a
statement rather than an empty scan. It is RE-DERIVED FROM THE BYTES each time
it is asked, so it cannot be asserted or go stale.

BOTH DIRECTIONS FOR EVERY GATE. A document declaring ONE instance keeps its
gate live; so does a document that is missing, that will not parse, or that
lists nothing but never says so.

AND THE READER IS PART OF THE CONTRACT. MEASURED: these layers ship in TWO
SHAPES -- L4 writes `registers` at the top level, L20 and L23 nest theirs under
`fields` -- so a reader that knows only the flat shape sees an EMPTY L20 and
would hand a scan-chained design the N/A. The fields are read through
`l_doc_consumer_contract.l_doc_fields`, the reader every other consumer of
these layers already uses, and the evidence NAMES it so the count can be
re-run against the same bytes.

chip-AGNOSTIC: synthetic projects in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402

#: gate -> (document basename, the field a design sets to DECLARE the subject,
#:          a value that declares ONE instance)
DECLARING = {
    "l4_regmap_declared_register_coverage_check":
        ("L4_REGMAP.json", "registers", [{"name": "CTRL", "offset": 0}]),
    "l4_regmap_phase2_emitter_contract_check":
        ("L4_REGMAP.json", "internal_registers", [{"name": "shadow"}]),
    "l20_dft_scan_topology_actionable_check":
        ("L20_DFT_SCAN_TOPOLOGY.json", "scan_chains", [{"name": "chain0"}]),
    "l23_security_requirements_typed_check":
        ("L23_SECURITY_REQUIREMENTS.json", "attack_surface", ["debug port"]),
    "l10_test_cases_cover_l3_constraints_check":
        ("L3_CMD_PROTOCOL.json", "opcodes", [{"hex": "0x01"}]),
    "bram_pdob_combinational_check":
        ("L9_INTEGRATION_SPEC.json", "memory_candidates", [{"name": "buf"}]),
}
GATES = tuple(DECLARING)

#: gate -> (the assertion field that makes its document a STATEMENT, the value
#: of that field which DENIES the absence). The polarity differs by layer --
#: L4/L20/L23 assert `<subject>_present: false`, while L3 and L9 assert
#: `no_<subject>_in_input: true` -- so the denial is carried per gate rather
#: than assumed, and every negative control below flips the right way.
STATED_BY = {
    "l4_regmap_declared_register_coverage_check": ("register_map_present", True),
    "l4_regmap_phase2_emitter_contract_check": ("register_map_present", True),
    "l20_dft_scan_topology_actionable_check": ("dft_present", True),
    "l23_security_requirements_typed_check":
        ("security_requirements_present", True),
    "l10_test_cases_cover_l3_constraints_check": ("no_opcodes_in_input", False),
    "bram_pdob_combinational_check": ("no_memories_in_input", False),
}


def _docs(proj):
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    return gd


def _write(proj, name, payload):
    (_docs(proj) / name).write_text(json.dumps(payload))


def _declares_none(tmp_path):
    """A project whose L4/L20/L23 each positively declare a zero population.

    L20 and L23 are written NESTED under `fields`; L3, L4 and L9 FLAT, which
    is how these layers actually ship on spm.
    """
    proj = tmp_path
    _write(proj, "L4_REGMAP.json", {
        "doc_class": "regmap", "registers": [], "internal_registers": [],
        "register_map_present": False, "no_registers_in_input": False})
    _write(proj, "L20_DFT_SCAN_TOPOLOGY.json", {
        "doc_id": "L20", "fields": {
            "dft_present": False, "scan_chains": [], "bist_mbist": [],
            "jtag_tap": None, "test_compression": None}})
    _write(proj, "L23_SECURITY_REQUIREMENTS.json", {
        "doc_id": "L23", "fields": {
            "security_requirements_present": False, "secure_boot": False,
            "attack_surface": [], "key_handling": {},
            "side_channel_mitigation": []}})
    _write(proj, "L3_CMD_PROTOCOL.json", {
        "doc_class": "cmd_protocol", "opcodes": [],
        "no_opcodes_in_input": True, "crc_parameters": None})
    _write(proj, "L9_INTEGRATION_SPEC.json", {
        "doc_class": "integration_spec", "top_module": "top",
        "memories": [], "memory_candidates": [], "memory_map": [],
        "no_memories_in_input": True, "no_memory_candidates_in_input": True,
        "no_memory_map_in_input": True})
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "top.v").write_text("module top(); endmodule\n")
    return proj, rtl


def _patch(proj, gate, **changes):
    """Rewrite one gate's document, preserving the shape it ships in."""
    name = DECLARING[gate][0]
    doc = json.loads((_docs(proj) / name).read_text())
    target = doc["fields"] if isinstance(doc.get("fields"), dict) else doc
    target.update(changes)
    _write(proj, name, doc)


# ── direction 1: the design declares the population is zero ───────────────

@pytest.mark.parametrize("gate", GATES)
def test_a_population_the_design_declares_zero_is_NA(tmp_path, gate):
    proj, rtl = _declares_none(tmp_path)
    assert F._p0_contract_na_reason(gate, proj, rtl) is not None, gate


@pytest.mark.parametrize("gate", GATES)
def test_that_NA_carries_the_declaration_it_rests_on(tmp_path, gate):
    proj, _ = _declares_none(tmp_path)
    ev = F._p0_zero_population_evidence(proj, gate)
    assert isinstance(ev, dict), gate
    assert ev["kind"] == "design-declared-zero-population"
    assert ev["declared_population"] == 0
    assert ev["declaration_reader"] == "l_doc_consumer_contract.l_doc_fields"
    rel = Path(ev["declaration_path"])
    assert not rel.is_absolute()
    assert (proj / rel).is_file(), ev["declaration_path"]
    assert rel.name == DECLARING[gate][0]
    assert ev["population_paths"], gate
    assert any(a["path"] == STATED_BY[gate][0] for a in ev["assertions"]), gate


@pytest.mark.parametrize("gate", GATES)
def test_the_evidence_is_re_derived_not_remembered(tmp_path, gate):
    """Declare an instance AFTER the first call: the answer must change."""
    proj, rtl = _declares_none(tmp_path)
    assert F._p0_zero_population_evidence(proj, gate) is not None
    field, value = DECLARING[gate][1], DECLARING[gate][2]
    _patch(proj, gate, **{field: value})
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


def test_that_disposition_is_skip_eligible():
    assert R.DESIGN_DECLARED_NA in R.SKIP_ELIGIBLE
    assert R.EXECUTION_ERROR not in R.SKIP_ELIGIBLE


@pytest.mark.parametrize("gate", GATES)
def test_every_registered_gate_is_on_both_rosters(gate):
    assert F._P0_GATE_REQUIRED_CONTEXT.get(gate), gate
    assert F._P0_GATE_ZERO_POPULATION.get(gate), gate


# ── direction 2: a design that HAS the subject keeps its gate live ────────

@pytest.mark.parametrize("gate", GATES)
def test_a_document_declaring_one_instance_keeps_the_gate_live(tmp_path, gate):
    proj, rtl = _declares_none(tmp_path)
    field, value = DECLARING[gate][1], DECLARING[gate][2]
    _patch(proj, gate, **{field: value})
    assert F._p0_zero_population_evidence(proj, gate) is None, (gate, field)
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, (gate, field)


@pytest.mark.parametrize("gate", GATES)
def test_a_document_that_is_not_there_keeps_the_gate_live(tmp_path, gate):
    """FAIL-CLOSED. A document nobody wrote has declared nothing."""
    proj, rtl = _declares_none(tmp_path)
    (_docs(proj) / DECLARING[gate][0]).unlink()
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("gate", GATES)
def test_a_document_that_will_not_parse_keeps_the_gate_live(tmp_path, gate):
    proj, rtl = _declares_none(tmp_path)
    (_docs(proj) / DECLARING[gate][0]).write_text("{not json")
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("gate", GATES)
def test_a_document_that_lists_nothing_but_says_nothing_keeps_it_live(
        tmp_path, gate):
    """THE POINT OF R-0915-19 AS CORRECTED: an empty scan is not a
    declaration. Drop only the field that STATES the absence and the same
    empty lists no longer earn the N/A."""
    proj, rtl = _declares_none(tmp_path)
    name = DECLARING[gate][0]
    doc = json.loads((_docs(proj) / name).read_text())
    target = doc["fields"] if isinstance(doc.get("fields"), dict) else doc
    target.pop(STATED_BY[gate][0])
    _write(proj, name, doc)
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("gate", GATES)
def test_a_document_that_says_the_subject_IS_present_keeps_it_live(
        tmp_path, gate):
    """A design that SAYS it has the subject while listing nothing has an
    unmet obligation, not an absent subject -- its gate must run. The saying
    is `*_present: true` on L4/L20/L23 and `no_*_in_input: false` on L3/L9."""
    proj, rtl = _declares_none(tmp_path)
    field, denial = STATED_BY[gate]
    _patch(proj, gate, **{field: denial})
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


def test_a_nested_layer_is_read_not_seen_as_an_empty_document(tmp_path):
    """The reader is load-bearing. A reader that knows only the flat shape
    sees NO `scan_chains` key in L20 at all -- an absent key is empty, so it
    would hand a SCAN-CHAINED design the N/A. Declare the chain in the shape
    L20 actually ships and the gate must stay live."""
    proj, rtl = _declares_none(tmp_path)
    gate = "l20_dft_scan_topology_actionable_check"
    raw = json.loads((_docs(proj) / DECLARING[gate][0]).read_text())
    assert "scan_chains" not in raw, "fixture flattened the document"
    assert raw["fields"]["scan_chains"] == []
    assert F._p0_zero_population_evidence(proj, gate) is not None

    _patch(proj, gate, scan_chains=[{"name": "chain0"}], dft_present=True)
    nested = json.loads((_docs(proj) / DECLARING[gate][0]).read_text())
    assert nested["fields"]["scan_chains"], "fixture wrote to the wrong level"
    assert F._p0_zero_population_evidence(proj, gate) is None
    assert F._p0_contract_na_reason(gate, proj, rtl) is None
