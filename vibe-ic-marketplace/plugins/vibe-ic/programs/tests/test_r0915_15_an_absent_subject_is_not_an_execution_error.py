"""A gate that RAN and found its subject absent from the design is N/A, not an
execution error.

MEASURED 2026-09-15 (lane icspm3, R-0915-15) on `spm` x gf180mcuD — a
serial-parallel multiplier — at main f64df0596. P0's umbrella carried SIXTEEN
sub-gates that executed, read the design, found their subject is not in it,
said so, and were booked `EXECUTION_ERROR`. That class is not skip-eligible, so
P0 went INCOMPLETE, stage1 went non-green, `stage_on_pass_review` declined,
steps 2/7/15/37 went INCOMPLETE and `stage2/3/4_compliance` FAILed. **A
multiplier was held INCOMPLETE for having no CRC, no arbiter, no BRAM, no
register map, no DFT and no security assets.**

BOTH DIRECTIONS, over populations DERIVED FROM THE TREE rather than invented:

  * `MUST_BE_DESIGN_NA` — the 16 measured P0 messages plus the shipped
    `skipped_reason` literals of the same shape. Each names a DESIGN ELEMENT
    the gate looked for inside something it READ.
  * `MUST_NOT_BE_DESIGN_NA` — every shipped `skipped_reason` literal about the
    gate's OWN SOURCE being absent, empty or unparseable, plus the three
    ZERO_DENOMINATOR sentences from the same run. A gate whose input it could
    not read has established nothing about the design, and the conservative
    direction keeps it non-skip-eligible.

The second list is the one that matters: `infer_nonverdict_reason`'s own
docstring forbids laundering "a zero denominator or failed producer … into a
design N/A merely because its sentence also contains the word `no`", and this
file is where that refusal is held to.

chip-AGNOSTIC: every sentence below is a literal this tree ships or a message
this flow emitted; none names a chip, a PDK or a vendor.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import _flow_reason_taxonomy as R  # noqa: E402


#: The sixteen P0 sub-gate messages measured on the spm run, verbatim.
P0_MEASURED = (
    "SKIP @ :0: L3 does not indicate break-delimited framing.",
    "SKIP @ :0: No break signals detected — not a break-based protocol.",
    "SKIP @ :0: No TX modules detected.",
    "[skipped] no toggle-divider patterns found",
    "[skipped] no cross-module 1-cycle pulse races detected",
    "[skipped] no rx_* modules found",
    "[skipped] no CRC module found in RTL",
    "[skipped] no arbitration patterns found",
    "[skipped] no inferred BRAM / megafunction wrappers with registered "
    "outputs detected",
    "no staged HDL input declares an address-valued typedef enum, and no "
    "documentation this gate opened declares a register row either",
    "L4 declares no registers[] (no_registers_in_input=False, "
    "register_map_present=False) — nothing for the register-block emitter "
    "to build",
    "no testable constraints in L3",
    "no DFT requirement derivable from the design's own inputs and L20 "
    "asserts none",
    "design declares no security-relevant asset and L23 asserts none",
    "1/1 L24_SIGNOFF layer(s) assert no sign-off verdict (inert / N/A) — "
    "nothing to certify",
    "1/1 L25_RELIABILITY_MISSION_PROFILE layer(s) inert or N/A — no consumer "
    "exists and nothing is claimed",
)

#: Shipped `skipped_reason` literals of the same shape.
SHIPPED_SUBJECT_ABSENT = (
    "no $readmemh / $readmemb in RTL",
    "no CRC module found in RTL",
    "no arbitration patterns found",
    "no rx_* modules found",
    "no toggle-divider patterns found",
)

MUST_BE_DESIGN_NA = P0_MEASURED + SHIPPED_SUBJECT_ABSENT

#: Shipped literals about the gate's OWN source, and the run's three
#: zero-denominator sentences. None of these establishes anything about the
#: design.
MUST_NOT_BE_DESIGN_NA = (
    "base TCL has no `extract` line to inject before",
    "could not detect clock frequency",
    "no FPGA top module found",
    "no KLayout DRC artefacts found",
    "no L*.json under generated_docs/",
    "no L7 document in project",
    "no L8_RTL_CONSTANTS.json",
    "no QSF/XDC constraint file found",
    "no RTL files",
    "no RTL files found",
    "no RTL files found in project",
    "no RTL files in project",
    "no SPECIALNETS in DEF — cannot seed power labels",
    "no module found in netlist",
    "no modules parseable",
    "no testbench files found",
    "no top-level modules found",
    "spm_l7.txt is empty or unparseable",
    "no L2 timing ranges to verify",
    "no L2 timing spec to derive bounds",
    "L2 has no ibt_us[1] / ibt_max_us",
    "L2 missing ibt_us[1] or tSRS_min_us",
    "warn_days <= 0 (gate disabled)",
    "signaltap config present (alternative observability)",
    "examined 0 A1-A9 step obligation(s) evaluated — no analog_block_list.json",
    "K5 census: docs loaded NONE; 0/13 checks examined anything; 0 unit(s) "
    "examined in total.",
    "2 of 2 open waiver entries carry no parseable approved_at, so NONE could "
    "be aged",
)


# ── direction 1: an honest absence is N/A-by-declaration ──────────────────

#: A record that carries a DECLARED BASIS. The 2026-09-15 correction: the
#: sentence is a clue, never its own basis — see `_declared_basis`.
WITH_BASIS = {"skip_kind": "declaration-not-present"}


@pytest.mark.parametrize("message", MUST_BE_DESIGN_NA)
def test_a_subject_the_design_does_not_have_is_design_declared_na(message):
    got = R.infer_nonverdict_reason(message=message, evidence=WITH_BASIS)
    assert got == R.DESIGN_DECLARED_NA, (got, message)


@pytest.mark.parametrize("message", MUST_BE_DESIGN_NA[:6])
def test_the_same_sentence_WITHOUT_a_basis_stays_incomplete(message):
    """THE #1978 CONTRACT, and the correction to this file's first landing.
    A banner-only rc-2 — a gate that merely scanned and found nothing,
    `skip_kind: input-missing` — is INCOMPLETE / EXECUTION_ERROR, never an
    unearned N/A. The sentence was a better clue than the old default; a clue
    is not a declaration."""
    got = R.infer_nonverdict_reason(
        verdict="SKIP", message=message,
        evidence={"exit_code": 2, "skip_kind": "input-missing"})
    assert got == R.EXECUTION_ERROR, (got, message)
    assert got not in R.SKIP_ELIGIBLE


@pytest.mark.parametrize("kind", sorted(R.DECLARED_ABSENCE_SKIP_KINDS))
def test_every_declared_absence_kind_is_a_basis(kind):
    assert R._declared_basis({"skip_kind": kind}) is True


def test_input_missing_is_not_a_basis():
    assert R._declared_basis({"skip_kind": "input-missing"}) is False
    assert R._declared_basis({}) is False
    assert R._declared_basis(None) is False
    assert R._declared_basis({"declared_absence_basis":
                              "L20 declares dft_present false"}) is True


def test_and_that_class_is_skip_eligible_so_P0_can_reach_PASS():
    """The consequence, asserted rather than assumed: the tier is what the
    cascade turned on."""
    assert R.DESIGN_DECLARED_NA in R.SKIP_ELIGIBLE
    assert R.p0_tier_for_reason_classes(
        [R.DESIGN_DECLARED_NA] * len(MUST_BE_DESIGN_NA)) == "PASS"
    assert R.record_verdict(R.DESIGN_DECLARED_NA) == "SKIP"


# ── direction 2: a fault is still a fault ─────────────────────────────────

@pytest.mark.parametrize("message", MUST_NOT_BE_DESIGN_NA)
def test_a_gate_that_could_not_read_its_own_source_is_not_design_na(message):
    got = R.infer_nonverdict_reason(message=message, evidence=WITH_BASIS)
    assert got != R.DESIGN_DECLARED_NA, (got, message)
    assert got not in R.SKIP_ELIGIBLE, (got, message)


@pytest.mark.parametrize("verdict", ["CRASHED", "STALLED", "NOT_FOUND",
                                     "INVOCATION_ERROR", "NOT_INVOCABLE"])
def test_a_crash_or_a_timeout_is_still_an_execution_error(verdict):
    """And the message cannot talk it out of that: the verdict branch is read
    before any prose."""
    got = R.infer_nonverdict_reason(
        verdict=verdict, message="no CRC module found in RTL",
        evidence=WITH_BASIS)
    assert got == R.EXECUTION_ERROR, (verdict, got)


def test_a_traceback_is_an_execution_error():
    got = R.infer_nonverdict_reason(
        message="Traceback (most recent call last): KeyError: 'modules'",
        evidence=WITH_BASIS)
    assert got == R.EXECUTION_ERROR, got


def test_a_zero_denominator_stays_a_zero_denominator():
    """A gate that examined nothing has not established that the design lacks
    anything; ZERO_DENOMINATOR is checked before this pattern and is not
    skip-eligible."""
    for m in ("K5 census: docs loaded NONE; 0/13 checks examined anything",
              "examined 0 A1-A9 step obligation(s) evaluated",
              "0 of 0 examined"):
        got = R.infer_nonverdict_reason(message=m, evidence=WITH_BASIS)
        assert got == R.ZERO_DENOMINATOR, (got, m)
        assert got not in R.SKIP_ELIGIBLE


def test_branch_owned_evidence_still_outranks_the_prose():
    """The contract this pattern is placed under: an explicit class, and a
    `skip_kind` the gate itself emitted, both win."""
    assert R.infer_nonverdict_reason(
        message="no CRC module found in RTL",
        explicit=R.EXECUTION_ERROR) == R.EXECUTION_ERROR
    assert R.infer_nonverdict_reason(
        message="no CRC module found in RTL",
        evidence={"skip_kind": "invocation-error"}) == R.EXECUTION_ERROR
    assert R.infer_nonverdict_reason(
        message="no CRC module found in RTL",
        evidence={"reason_class": R.BLOCKED_BY_UPSTREAM}
    ) == R.BLOCKED_BY_UPSTREAM


# ── the populations are real ──────────────────────────────────────────────

def test_the_populations_are_non_empty_and_disjoint():
    """A denominator of zero would make both directions vacuously true."""
    assert len(MUST_BE_DESIGN_NA) >= 20
    assert len(MUST_NOT_BE_DESIGN_NA) >= 20
    assert not (set(MUST_BE_DESIGN_NA) & set(MUST_NOT_BE_DESIGN_NA))


def test_every_MUST_NOT_sentence_is_a_literal_this_tree_ships_or_emitted():
    """The refusing population is DERIVED, not invented: each of the shipped
    `skipped_reason` literals below is greppable in programs/."""
    shipped = [m for m in MUST_NOT_BE_DESIGN_NA
               if m in ("no RTL files found", "no modules parseable",
                        "no top-level modules found", "no testbench files found",
                        "could not detect clock frequency",
                        "no L7 document in project", "no module found in netlist",
                        "no KLayout DRC artefacts found")]
    assert len(shipped) >= 6
    hay = "\n".join(
        p.read_text(errors="replace") for p in PROGRAMS.glob("*.py")
        if not p.name.startswith("test_"))
    for m in shipped:
        assert re.search(re.escape(m), hay), m
