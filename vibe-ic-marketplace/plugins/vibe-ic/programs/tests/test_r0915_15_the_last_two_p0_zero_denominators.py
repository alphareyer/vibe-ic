"""The two P0 reasons that were the flow's OWN emptiness, not the design's.

MEASURED 2026-09-15 (lane icspm3, R-0915-15) on `spm` x gf180mcuD, after the
tiering fix took P0's INCOMPLETE reasons from ~20 to two. Both remaining ones
were ZERO_DENOMINATOR, and neither was a hole in what the gate could see.

`waiver_staleness_check` — "2 of 2 open waiver entries carry no parseable
`approved_at`, so NONE could be aged". **Both entries are
`waivers_materialize.py`'s own ENV_UNAVAILABLE deferrals.** The gate's own skip
message already said such an entry "legitimately carries none and is tracked by
its `ticket` instead" — and then counted it as part of the coverage hole. The
flow was failing a gate for the absence of a signature the flow itself refuses
to forge, because "a machine-written approval date would be a self-approval".

`analog_flow_compliance_check` — "examined 0 A1-A9 step obligation(s) … no
analog_block_list.json" on a serial multiplier whose own
`reports/ic_class.json` records `has_analog: false`. The gate HAS a class-N/A
branch, but it is guarded by `analog_class_is_na` =
`_ic_class_says_non_analog AND _all_blocks_low_confidence`, and the second
conjunct exists to stop a CONFIDENT analog block from being skipped. With zero
blocks declared there is no block for it to guard, so it answered False for
want of a subject and a positively non-analog IC took the conservative branch.

BOTH DIRECTIONS FOR BOTH GATES, and the conservative direction is the one
these cases exist for: a HUMAN-approved waiver missing its signature is still
the coverage hole; a project with no class verdict, or one classified analog,
still gets the unclassified reading.

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

import waiver_staleness_check as W  # noqa: E402
import analog_flow_compliance_check as A  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402


def _waivers(tmp_path, entries):
    (tmp_path / "waivers.json").write_text(json.dumps({"waived_steps": entries}))
    return tmp_path


MACHINE = {"id": 39, "reason": "ENV_UNAVAILABLE (fpga-board cap-gap)",
           "approver": "field-agent-attest (fpga-board cap-gap tier)",
           "ticket": "fpga-board-prototype-capgap-v1.0.18",
           "verdict_tier": "ENV_UNAVAILABLE", "review_required": True,
           "auto_synthesized": True}
HUMAN = {"id": 7, "reason": "deferred by review", "approver": "a reviewer",
         "ticket": "T-1"}


# ── waiver_staleness_check ────────────────────────────────────────────────

def test_a_machine_attestation_is_recognised_structurally():
    """The marker is a FIELD the producer writes, never a word in a sentence."""
    assert W._machine_attested(MACHINE) is True
    assert W._machine_attested({"_autogen": True}) is True
    assert W._machine_attested(HUMAN) is False
    assert W._machine_attested({}) is False
    assert W._machine_attested("not a dict") is False


def test_waivers_materialize_is_the_producer_of_that_marker():
    """The claim this rests on, read from the producer rather than assumed."""
    src = (PROGRAMS / "waivers_materialize.py").read_text(errors="replace")
    assert 'entry["auto_synthesized"] = True' in src


def test_a_file_of_only_machine_attestations_declares_its_class(tmp_path):
    proj = _waivers(tmp_path, [MACHINE, dict(MACHINE, id=6)])
    _findings, summary = W.inspect(proj)
    assert summary["entries_machine_attested"] == 2
    assert summary["entries_unageable"] == 0
    assert summary["reason_class"] == R.DESIGN_DECLARED_NA
    assert "no human signature to age" in summary["skipped_reason"]
    assert R.report_reason_class({"summary": summary}) == R.DESIGN_DECLARED_NA


def test_a_HUMAN_waiver_with_no_signature_is_still_the_coverage_hole(tmp_path):
    """The conservative direction. This is what the gate exists to report."""
    proj = _waivers(tmp_path, [MACHINE, HUMAN])
    _findings, summary = W.inspect(proj)
    assert summary["entries_unageable"] == 1
    assert summary.get("reason_class") is None
    assert "no parseable `approved_at`" in summary["skipped_reason"]


def test_a_signed_waiver_is_still_aged(tmp_path):
    proj = _waivers(tmp_path, [MACHINE, dict(HUMAN, approved_at="2020-01-01")])
    findings, summary = W.inspect(proj)
    assert summary["entries_examined"] == 1
    assert summary.get("reason_class") is None
    assert any(f.rule.startswith("WAIVER_STALE") for f in findings), findings


# ── analog_flow_compliance_check ──────────────────────────────────────────

def _project(tmp_path, ic_class):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    if ic_class is not None:
        (tmp_path / "reports" / "ic_class.json").write_text(json.dumps(ic_class))
    return tmp_path


def test_a_declared_digital_ic_with_no_analog_block_is_design_na(tmp_path):
    proj = _project(tmp_path, {"has_analog": False,
                               "ic_class": "digital_arithmetic_primitive"})
    res = A.run_audit(proj)
    assert res.verdict == "VACUOUS_PASS"
    assert res.summary["reason_class"] == R.DESIGN_DECLARED_NA
    assert res.summary["reason"] == "no_analog_blocks_declared_digital"
    assert R.report_reason_class(res.summary) == R.DESIGN_DECLARED_NA


@pytest.mark.parametrize("ic_class", [
    None,                                        # no class verdict at all
    {"is_pure_analog": True},                    # positively analog
    {"is_mixed_signal": True},                   # positively mixed-signal
    {"ic_class": "unknown_protocol_class"},      # no positive signal either way
])
def test_anything_but_a_positive_non_analog_verdict_keeps_the_old_reading(
        tmp_path, ic_class):
    """FAIL-CLOSED, unchanged: the skip must not become a blanket bypass."""
    proj = _project(tmp_path / str(abs(hash(str(ic_class)))), ic_class)
    res = A.run_audit(proj)
    assert res.verdict == "VACUOUS_PASS"
    assert res.summary["reason"] == "no_analog_blocks", res.summary
    assert res.summary.get("reason_class") is None
    assert "has NOT been checked" in "".join(
        f.message for f in res.findings)


def test_neither_gate_claims_a_sign_off(tmp_path):
    """Both new sentences must still say what they are NOT."""
    proj = _project(tmp_path, {"has_analog": False})
    res = A.run_audit(proj)
    assert "NOT a sign-off" in "".join(f.message for f in res.findings)
    _f, summary = W.inspect(_waivers(tmp_path, [MACHINE]))
    assert "NOT a sign-off" in summary["skipped_reason"]
