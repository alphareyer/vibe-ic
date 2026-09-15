"""A P0 gate whose SUBJECT the design declares absent is N/A, not blocked.

MEASURED 2026-09-15 (lane icspm3, R-0915-19) on `spm` x gf180mcuD — a
serial-parallel multiplier with no analog track, no opcodes, no reject rules
and no OTP content. NINE P0 sub-gates were booked `BLOCKED_BY_UPSTREAM`::

    analog_corner_sweep_check          examined nothing (reason: no_analog_dir)
    analog_netlist_pdk_check           examined nothing (reason: no_analog_dir)
    analog_pre_vs_post_layout_check    examined nothing (reason: no_analog_dir)
    analog_hw_tb_de10lite_budget_check NO_DATA: no .qsf file found
    l3_opcode_response_template_check  no opcode override doc found
                                       (REVISED by R-0915-37 -- see below)
    l11_sequence_covers_l6_reject…     no L6.reject_rules to check
    assertion_covers_l3_constraints…   no L3 constraints to enforce
    otp_image_layer_consistency_check  examined nothing (reason: no_l11)
    deliverable_verdict_consistency…   no deliverable at <project>/RESULT.md

That class is not skip-eligible, so P0 stayed INCOMPLETE and the whole cascade
with it.

`_BLOCKED_RE` KEEPS ITS JOB AND ITS PLACE, and no regex is reordered: a
producer that OWED the document and did not write it IS a cascade and stays
BLOCKED. What changes is that the DESIGN IS ASKED FIRST, through the roster
that already exists for exactly this question, and each entry names the
declaration that answers it.

BOTH DIRECTIONS FOR EVERY GATE, and the negative controls are the point: a
design that DECLARES the subject with its artefact missing keeps the gate live
and stays BLOCKED_BY_UPSTREAM. A document that cannot be READ is never a
declaration of absence — it leaves its `*_state` malformed and the gate runs.

AND ONE THAT IS NOT A DECLARATION. `deliverable_verdict_consistency_check`
reads `RESULT.md`, which the runner authors at the END of the run, so a P0
sub-gate asking for it mid-run asks before the producer has run. Its own
message names whose question the presence is. That is a FLOW-ORDER fact: N/A
while the run is in flight, live the moment the deliverable exists.

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

#: gate -> (context key it is registered against, how a design DECLARES it)
SUBJECTS = {
    "analog_corner_sweep_check": "analog_track",
    "analog_netlist_pdk_check": "analog_track",
    "analog_pre_vs_post_layout_check": "analog_track",
    "l11_sequence_covers_l6_reject_rules_check": "l6_reject_rules",
    "assertion_covers_l3_constraints_check": "l3_constraints",
    "otp_image_layer_consistency_check": "l11_otp",
    "deliverable_verdict_consistency_check": "deliverable_record",
}
ALL_GATES = tuple(SUBJECTS) + ("analog_hw_tb_de10lite_budget_check",)

#: REVISED BY R-0915-37 (2026-09-15 23:17). This roster previously held
#: `l3_opcode_response_template_check` as deliberately-unregistered, on the
#: reading that its subject is an OVERRIDE DOC and a document nobody wrote
#: declares nothing. The orchestrator revised that: the gate's subject is
#: opcode RESPONSE TEMPLATES, and the override doc only matters once opcodes
#: EXIST -- so the gate is now keyed on L3's declared opcode POPULATION.
#:
#: THE PROTECTION THAT TEST GAVE IS NOT DROPPED, it is re-pointed: the case
#: below now asserts the direction that actually protects the run -- a design
#: that DECLARES an opcode and has no override doc keeps the gate live and
#: blocking, which is the shape the old test existed to defend.
STILL_BLOCKING_WITH_A_SUBJECT = ("l3_opcode_response_template_check",)


def _docs(tmp_path):
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    return gd


def _silent_design(tmp_path):
    """A project whose own documents declare NONE of the nine subjects."""
    gd = _docs(tmp_path)
    (gd / "L3_CMD_PROTOCOL.json").write_text(json.dumps(
        {"opcodes": [], "constraints": None}))
    (gd / "L6_CONTROL_LOGIC.json").write_text(json.dumps(
        {"reject_rules": None}))
    (gd / "L11_OTP_CONTENT.json").write_text(json.dumps(
        {"schema_version": 2, "doc_class": "otp_content", "ic_name": "x",
         "otp_bytes": [], "content_hex": None, "otp_layout": None,
         "depth": None, "width_bits": None, "no_otp_layout_in_input": True}))
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "ic_class.json").write_text(json.dumps(
        {"has_analog": False}))
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "top.v").write_text("module top(); endmodule\n")
    return tmp_path, rtl


# ── direction 1: the design says it has no such thing ─────────────────────

@pytest.mark.parametrize("gate", ALL_GATES)
def test_a_subject_the_design_does_not_have_makes_the_gate_NA(tmp_path, gate):
    proj, rtl = _silent_design(tmp_path)
    why = F._p0_contract_na_reason(gate, proj, rtl)
    assert why is not None, f"{gate} is still invoked on a design without its subject"
    assert "fabricated" in why


def test_every_one_of_the_nine_is_registered():
    for gate in ALL_GATES:
        assert F._P0_GATE_REQUIRED_CONTEXT.get(gate), gate


def test_that_disposition_is_skip_eligible():
    assert R.DESIGN_DECLARED_NA in R.SKIP_ELIGIBLE
    assert R.BLOCKED_BY_UPSTREAM not in R.SKIP_ELIGIBLE


# ── direction 2: a design that DECLARES it stays BLOCKED ──────────────────

def test_an_analog_design_keeps_its_analog_gates_live(tmp_path):
    """Declared by a block list with a block — the artefact is still missing,
    so this design's analog gates must still cascade."""
    proj, rtl = _silent_design(tmp_path)
    bl = proj / "phase3" / "analog"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "analog_block_list.json").write_text(json.dumps(
        {"blocks": [{"name": "ldo", "confidence": "high"}]}))
    for gate in ("analog_corner_sweep_check", "analog_netlist_pdk_check",
                 "analog_pre_vs_post_layout_check"):
        assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


def test_a_design_with_no_class_verdict_keeps_its_analog_gates_live(tmp_path):
    """FAIL-CLOSED: absence of a class verdict is not a declaration of
    absence."""
    proj, rtl = _silent_design(tmp_path)
    (proj / "reports" / "ic_class.json").unlink()
    assert F._p0_contract_context(proj, rtl)["analog_track"]
    for gate in ("analog_corner_sweep_check", "analog_netlist_pdk_check"):
        assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("gate", STILL_BLOCKING_WITH_A_SUBJECT)
def test_a_declared_opcode_with_no_override_doc_keeps_the_gate_live(
        tmp_path, gate):
    """R-0915-37. A template can only be owed for an opcode that EXISTS, so
    the population is L3's opcode list. Declare one opcode and write no
    override doc: the gate must stay live and blocking, exactly as it did
    when it was unregistered."""
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / "L3_CMD_PROTOCOL.json").write_text(json.dumps(
        {"opcodes": [{"hex": "0x74", "name": "READ"}],
         "no_opcodes_in_input": False, "constraints": None}))
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("gate", STILL_BLOCKING_WITH_A_SUBJECT)
def test_a_design_with_no_opcodes_at_all_answers_that_gate_NA(tmp_path, gate):
    """The other half of R-0915-37, and the reason it is a revision: with a
    PRESENT L3 declaring zero opcodes there is no response template to owe."""
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / "L3_CMD_PROTOCOL.json").write_text(json.dumps(
        {"opcodes": [], "no_opcodes_in_input": True, "constraints": None}))
    assert F._p0_zero_population_evidence(proj, gate) is not None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is not None, gate


@pytest.mark.parametrize("gate", STILL_BLOCKING_WITH_A_SUBJECT)
def test_an_L3_that_is_not_there_still_keeps_that_gate_live(tmp_path, gate):
    """R-0915-19's correction is untouched: a document nobody wrote declares
    nothing."""
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / "L3_CMD_PROTOCOL.json").unlink()
    assert F._p0_zero_population_evidence(proj, gate) is None, gate
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


@pytest.mark.parametrize("doc,key,gate", [
    ("L3_CMD_PROTOCOL.json", "l3_constraints",
     "assertion_covers_l3_constraints_check"),
    ("L6_CONTROL_LOGIC.json", "l6_reject_rules",
     "l11_sequence_covers_l6_reject_rules_check"),
    ("L11_OTP_CONTENT.json", "l11_otp",
     "otp_image_layer_consistency_check"),
])
def test_a_declaring_document_that_is_NOT_THERE_keeps_the_gate_live(
        tmp_path, doc, key, gate):
    """THE CORRECTION. A document that does not exist has declared nothing;
    only `state == valid` plus an empty subject is the design speaking."""
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / doc).unlink()
    assert F._p0_contract_context(proj, rtl)[key], key
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, (doc, gate)


def test_a_design_that_declares_reject_rules_keeps_its_gate_live(tmp_path):
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / "L6_CONTROL_LOGIC.json").write_text(json.dumps(
        {"reject_rules": [{"when": "crc_bad", "then": "NACK"}]}))
    assert F._p0_contract_na_reason(
        "l11_sequence_covers_l6_reject_rules_check", proj, rtl) is None


def test_a_design_that_declares_l3_constraints_keeps_its_gate_live(tmp_path):
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / "L3_CMD_PROTOCOL.json").write_text(json.dumps(
        {"opcodes": [], "constraints": [{"id": "C1", "text": "t_su >= 2 ns"}]}))
    assert F._p0_contract_na_reason(
        "assertion_covers_l3_constraints_check", proj, rtl) is None


@pytest.mark.parametrize("field,value", [
    ("otp_bytes", [1, 2, 3]),
    ("content_hex", "deadbeef"),
    ("otp_layout", {"rows": 4}),
    ("depth", 256),
    ("width_bits", 32),
])
def test_any_declared_OTP_content_keeps_the_OTP_gate_live(tmp_path, field,
                                                          value):
    proj, rtl = _silent_design(tmp_path)
    doc = json.loads((_docs(proj) / "L11_OTP_CONTENT.json").read_text())
    doc[field] = value
    (_docs(proj) / "L11_OTP_CONTENT.json").write_text(json.dumps(doc))
    assert F._p0_contract_na_reason(
        "otp_image_layer_consistency_check", proj, rtl) is None, field


def test_the_document_WRAPPER_is_not_the_subject(tmp_path):
    """The mistake this ruling is about, one level down: spm's L11 is a REAL
    document (schema_version, doc_class, ic_name) that declares NO otp
    content. Keying on the document would answer 'the subject exists'."""
    proj, rtl = _silent_design(tmp_path)
    ctx = F._p0_contract_context(proj, rtl)
    assert ctx["l11_otp"] is None, ctx["l11_otp"]


# ── a document that cannot be READ is not a declaration of absence ────────

@pytest.mark.parametrize("doc,gate", [
    ("L3_CMD_PROTOCOL.json", "assertion_covers_l3_constraints_check"),
    ("L6_CONTROL_LOGIC.json", "l11_sequence_covers_l6_reject_rules_check"),
    ("L11_OTP_CONTENT.json", "otp_image_layer_consistency_check"),
])
def test_an_unreadable_declaration_keeps_the_gate_live(tmp_path, doc, gate):
    proj, rtl = _silent_design(tmp_path)
    (_docs(proj) / doc).write_text("{ this is not json")
    assert F._p0_contract_na_reason(gate, proj, rtl) is None, (doc, gate)


# ── the flow-ORDER one ────────────────────────────────────────────────────

def test_the_deliverable_gate_is_NA_in_flight_and_live_once_it_exists(
        tmp_path):
    proj, rtl = _silent_design(tmp_path)
    assert F._p0_contract_na_reason(
        "deliverable_verdict_consistency_check", proj, rtl) is not None
    (proj / "RESULT.md").write_text("# RESULT\n\nverdict: PASS\n")
    assert F._p0_contract_na_reason(
        "deliverable_verdict_consistency_check", proj, rtl) is None


def test_the_deliverable_gate_names_whose_question_presence_is():
    """The premise: that gate itself says presence is another gate's
    question, which is why requiring the artefact is not hiding anything."""
    src = (PROGRAMS / "deliverable_verdict_consistency_check.py").read_text(
        errors="replace")
    assert "run_output_completeness_check" in src


# ── nothing else moved ────────────────────────────────────────────────────

def test_a_gate_with_no_requirement_is_untouched(tmp_path):
    proj, rtl = _silent_design(tmp_path)
    for gate in ("waiver_staleness_check", "analog_flow_compliance_check"):
        assert F._P0_GATE_REQUIRED_CONTEXT.get(gate) is None
        assert F._p0_contract_na_reason(gate, proj, rtl) is None, gate


def test_the_blocked_recogniser_is_unchanged():
    """No regex was reordered: a real cascade sentence still reads BLOCKED."""
    for msg in ("required output absent",
                "missing producer for reports/phase3/x.json",
                "blocked by upstream step 21"):
        assert R.infer_nonverdict_reason(message=msg) == R.BLOCKED_BY_UPSTREAM
