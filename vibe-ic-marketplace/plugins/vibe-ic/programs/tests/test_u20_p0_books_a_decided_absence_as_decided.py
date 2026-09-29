#!/usr/bin/env python3
"""U20 (IC_BLOCKER_AUDIT §2) — P0 books a structural absence, or a class-declared
analog N/A, as N/A and never as EXECUTION_ERROR.

MEASURED, both on the IC path (deliverable DIE), both read-only copies:

subservient v4 (8HD-3, `subic3_ic_v4_20260929`, plugin 1.26.26), the whole-flow
audit's P0 row, four times over:

    INCOMPLETE: arbiter_starvation_check — reason_class=EXECUTION_ERROR:
      [NOT_APPLICABLE_BY_STRUCTURE] arbiter_starvation_check: enumerated 24 RTL
      file(s) staged for this design and found 0 — this design has no such
      subject, so the question is ANSWERED, not unmeasured — no …

and P0 read NOT_MEASURED (partial_population). The sentence carries the class,
the scanned count and `found 0` — the exact shape `_structural_absence.sentence()`
writes and the taxonomy's own second channel accepts. The FIRST classification
(`infer_nonverdict_reason` on the line, no explicit class) returned
NOT_APPLICABLE_BY_STRUCTURE; `_p0_gate_record` then classified AGAIN with that
class as `explicit=` and the umbrella's evidence (exit code, skip kind — no
enumeration record), and `_guard_structural` refused the class it had been handed
one line earlier. R-0915-119(2)'s tests pin the first call only, so they stayed
green while every real record read EXECUTION_ERROR.

spm v5 (8HD-4, `spmic5/run_v5`), stage1_compliance.json, P0:

    INCOMPLETE: spice_correlation_check — reason_class=EXECUTION_ERROR: examined
      nothing (reason: analog_not_applicable_for_class:digital_arithmetic_primitive)

The gate keyed that answer off the class registry's own `analog_applicable=false`
for the design's registry-matched class — a declaration, the same fact the
umbrella itself turns into DESIGN_DECLARED_NA for the other analog gates
(`N/A for class 'digital_arithmetic_primitive': analog_applicable=false`) — but it
never STATED a class, so the umbrella inferred one from prose and fail-closed.

Both directions are pinned: a sentence without an enumeration, below the scanned
floor, or with a non-zero `found` stays EXECUTION_ERROR; an unknown class keeps the
SPICE gate live and FAILing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _flow_reason_taxonomy as T        # noqa: E402
import _structural_absence as SA         # noqa: E402
import flow_compliance_check as F        # noqa: E402

NABS = SA.NOT_APPLICABLE_BY_STRUCTURE

#: The v4 record's message, verbatim up to the 200-character cut the umbrella
#: applies (`_p0_skip_reason_from_output`).
V4_LINE = ("[NOT_APPLICABLE_BY_STRUCTURE] cross_module_1cycle_handshake_check: "
           "enumerated 23 module(s) parsed from the staged RTL and found 0 — "
           "this design has no such subject, so the question is ANSWERED, not "
           "un")
#: The spm v5 record's message, verbatim.
V5_SPICE_LINE = ("examined nothing (reason: analog_not_applicable_for_class:"
                 "digital_arithmetic_primitive); this is NOT a pass over the "
                 "design")

PLAIN_RTL = """\
module spm (input clk, input rst, input y, output p);
  reg [31:0] acc;
  always @(posedge clk) acc <= rst ? 32'b0 : {acc[30:0], y};
  assign p = acc[31];
endmodule
"""


def _project(tmp_path: Path, ic_class: str = "digital_arithmetic_primitive",
             routed: bool = True) -> Path:
    """A post-route tree: RTL, SPEF, STA and the routed DEF the SPICE gate's
    producer check reads, and the design's persisted class record."""
    p = tmp_path / "proj"
    (p / "phase2/stage1/rtl").mkdir(parents=True)
    (p / "phase2/stage1/rtl/spm.v").write_text(PLAIN_RTL)
    (p / "reports").mkdir()
    (p / "reports/ic_class.json").write_text(json.dumps({"ic_class": ic_class}))
    (p / "phase3/stage3/extracted").mkdir(parents=True)
    (p / "phase3/stage3/extracted/spm.spef").write_text('*SPEF "IEEE 1481"\n')
    (p / "phase3/stage3/sta").mkdir(parents=True)
    (p / "phase3/stage3/sta/spm.rpt").write_text("Startpoint: y\n")
    if routed:
        (p / "phase3/stage3/pnr").mkdir(parents=True)
        (p / "phase3/stage3/pnr/routed.def").write_text("VERSION 5.8 ;\n")
    return p


def _p0_record(project: Path, gate: str, monkeypatch) -> dict:
    """The record the REAL umbrella builds for one registered gate."""
    monkeypatch.setattr(F, "_STRUCTURAL_RTL_GATES", (gate,))
    records: list = []
    F._run_structural_rtl_gates(project, records_out=records)
    assert len(records) == 1, records
    return records[0]


# ── 1. the structural absence, through the record boundary ────────────────
def test_the_v4_line_survives_the_record_boundary():
    """The recorded defect in one call: the class the line established must
    still be the class after `_p0_gate_record` re-classifies it."""
    ev = {"exit_code": 2, "skip_kind": "input-missing"}
    first = T.infer_nonverdict_reason(verdict="SKIP", message=V4_LINE,
                                      evidence=ev)
    assert first == NABS
    rec = F._p0_gate_record("cross_module_1cycle_handshake_check",
                            T.record_verdict(first), V4_LINE, ev,
                            reason_class=first)
    assert (rec["verdict"], rec["reason_class"]) == ("SKIP", NABS), rec


@pytest.mark.parametrize("gate", [
    "arbiter_starvation_check",
    "cross_module_1cycle_handshake_check",
])
def test_the_umbrella_books_a_structural_absence_as_decided(
        tmp_path, monkeypatch, gate):
    """End to end: the real checker on real RTL, through the real umbrella."""
    rec = _p0_record(_project(tmp_path), gate, monkeypatch)
    assert (rec["verdict"], rec["reason_class"]) == ("SKIP", NABS), rec
    assert "enumerated" in rec["message"] and "found 0" in rec["message"], rec


@pytest.mark.parametrize("line", [
    # the class token and no enumeration at all
    "[NOT_APPLICABLE_BY_STRUCTURE] arbiter_starvation_check: nothing here",
    # below the scanned floor
    "[NOT_APPLICABLE_BY_STRUCTURE] arbiter_starvation_check: enumerated 0 RTL "
    "file(s) and found 0",
    # the subject was FOUND: a zero denominator, not an absence
    "[NOT_APPLICABLE_BY_STRUCTURE] arbiter_starvation_check: enumerated 4 RTL "
    "file(s) and found 2",
])
def test_an_explicit_class_without_its_enumeration_is_still_refused(line):
    """Guard (i) and (ii) at the record boundary: handing the token in as
    `explicit=` admits nothing the line does not itself establish."""
    rec = F._p0_gate_record("arbiter_starvation_check", "INCOMPLETE", line,
                            {"exit_code": 2, "skip_kind": "input-missing"},
                            reason_class=NABS)
    assert rec["reason_class"] == T.EXECUTION_ERROR, rec
    assert rec["verdict"] == "INCOMPLETE", rec


def test_an_invalid_enumeration_record_is_not_rescued_by_the_line():
    """A record that says the subject WAS found outranks a sentence that says
    it was not: the line is a transport for an absent record, never an
    override of a present one."""
    ev = {"exit_code": 2, "skip_kind": "declared-by-gate",
          SA.EVIDENCE_KEY: {"population": "RTL files", "scanned": 4,
                            "found": 2}}
    assert T.infer_nonverdict_reason(message=V4_LINE, evidence=ev,
                                     explicit=NABS) == T.EXECUTION_ERROR


# ── 2. the class-declared analog N/A ───────────────────────────────────────
def test_the_umbrella_books_the_class_declared_spice_na_as_declared(
        tmp_path, monkeypatch):
    rec = _p0_record(_project(tmp_path), "spice_correlation_check", monkeypatch)
    assert (rec["verdict"], rec["reason_class"]) == \
        ("SKIP", T.DESIGN_DECLARED_NA), rec


def test_the_spice_gate_states_its_class_and_its_basis(tmp_path):
    """The gate that knows WHY states it, in the report the umbrella reads."""
    proj = _project(tmp_path)
    out = proj / "reports/spice.json"
    cp = subprocess.run([sys.executable, str(PROG / "spice_correlation_check.py"),
                         str(proj), "--json", str(out)],
                        capture_output=True, text=True, cwd=str(proj))
    assert cp.returncode == 2, (cp.returncode, cp.stdout, cp.stderr)
    rep = json.loads(out.read_text())
    assert T.report_reason_class(rep) == T.DESIGN_DECLARED_NA, rep["summary"]
    basis = rep["summary"].get("declared_absence_basis") or {}
    assert basis.get("ic_class") == "digital_arithmetic_primitive", basis
    assert basis.get("analog_applicable") is False, basis


def test_an_unregistered_class_keeps_the_spice_gate_live(tmp_path, monkeypatch):
    """The other direction: no class record that declares the design digital,
    so the missing deck is still a FAIL and nothing is declared."""
    rec = _p0_record(_project(tmp_path, ic_class="unknown"),
                     "spice_correlation_check", monkeypatch)
    assert rec["verdict"] == "FAIL", rec
    assert rec["reason_class"] != T.DESIGN_DECLARED_NA, rec


def test_the_v5_line_alone_is_still_not_a_declaration():
    """The prose is not the basis: the same sentence with no stated class stays
    fail-closed, exactly as before. Only the gate's own statement moves it."""
    assert T.infer_nonverdict_reason(
        verdict="SKIP", message=V5_SPICE_LINE,
        evidence={"exit_code": 2, "skip_kind": "input-missing"}) == \
        T.EXECUTION_ERROR
