"""vibe-ic#2195 — a real signal-net LVS mismatch was published as a PASS.

THE DEFECT (measured on the shipped module at e2b3c08170b5, on a real report in
this fleet's run corpus). netgen reported, for one design:

    Number of devices: 333                     |Number of devices: 333
    Number of nets: 365 **Mismatch**           |Number of nets: 367 **Mismatch**
    VGND                                       |VPWR **Mismatch**
    VPWR                                       |VGND **Mismatch**
    Final result: Top level cell failed pin matching.

Two nets apart, and the power rails matched to each OTHER. `mismatch_class()`
returned **POWER_PIN_ONLY** — the benign reviewed-waiver class that
`phase3_one_shot_runner` converts to `LVS_MATCH_POWER_AWARE`, a PASS. A design
was cleared that should not have been.

It reached the benign class because the module read only WHICH names appear
(`(no matching pin)` rows, `disconnected node: V*` lines) and never read
netgen's summary COUNTS. `POWER_PIN_ONLY` is a claim about WHICH nets differ —
that both sides hold the same set of nets and only the power/tie ports are
named differently or absent. A difference in the COUNTS falsifies that claim
outright: it says the two sides do not hold the same number of things, which is
structural and cannot be a pin-naming difference.

THE DIRECTION THAT MATTERS: a gate that wrongly FAILS is found the same day,
because someone is blocked and comes looking. A gate that wrongly PASSES is
found only if somebody sweeps the corpus. So the negative assertions below are
the load-bearing half — and so is the paranoia control: refusing every mismatch
would "fix" this by blinding the benign class the other way, which is why
`test_a_genuine_power_pin_only_difference_is_still_benign` and
`test_a_report_with_no_count_row_is_left_where_it_was` are here.

MEASURED, so the fix is not merely plausible (host 8hd-3, 5025-report corpus):
  * 818 reports carry a differing count pair; ALL 818 already classify MISMATCH.
  * 0 reports classified MATCH carry one — the rule cannot fire on a clean compare.
  * Of 318 genuinely benign-shaped reports that print totals, 316 have totals
    that AGREE exactly. netgen counts the power nets on BOTH sides, so the
    power-unaware-netlist artifact does not move these numbers.
  * mismatch_class() changes on exactly 1 of 5025 reports, in one direction.

chip-AGNOSTIC: synthetic generic-device transcripts only; no PDK content.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
T = importlib.import_module("lvs_verdict_tokens")


def _counts_disagree(blob):
    """Ask the module, via `getattr`, so the PRE-FIX module can ANSWER instead
    of raising. A control that dies of AttributeError against the old code has
    observed nothing: the old module's honest answer here is "no disagreement
    seen", which is exactly what these two controls must accept."""
    return getattr(T, "_text_counts_disagree", lambda _b: False)(blob)


# ── fixtures — synthetic, mirroring the SHAPE netgen prints ────────────────
_HEAD = "Final result: Top level cell failed pin matching.\n"
# The benign artifact: the power-unaware netlist has no such port AT ALL.
_POWER_ABSENCE = ("VGND                          |(no matching pin)\n"
                  "VPWR                          |(no matching pin)\n")


def _counts(nets_a, nets_b, dev_a=8, dev_b=8, mark=""):
    """netgen's summary row-pair, with or without its `**Mismatch**` marker."""
    return ("Number of devices: %d%s           |Number of devices: %d%s\n"
            "Number of nets: %d%s           |Number of nets: %d%s\n"
            % (dev_a, mark, dev_b, mark, nets_a, mark, nets_b, mark))


def e1(verdict="mismatch", **over):
    """A minimal well-formed E1 structured report (netgen fork `-json`)."""
    summary = {"devices": {"ckt1": 8, "ckt2": 8},
               "nets": {"ckt1": 6, "ckt2": 6},
               "unmatched_nets": {"ckt1": 0, "ckt2": 0},
               "unmatched_devices": {"ckt1": 0, "ckt2": 0},
               "property_error_count": 0,
               "property_error_cells": [],
               "failed_subcells": []}
    summary.update(over)
    return {"verdict": verdict, "verdict_reason": "synthetic",
            "summary": summary, "cells": []}


# ── THE DEFECT: the exact shape that was published as a PASS ───────────────
def test_a_net_count_difference_is_refused_the_benign_class():
    """The reproduction, in the module's own vocabulary. Pre-fix: POWER_PIN_ONLY."""
    blob = _HEAD + _counts(365, 367, 333, 333, mark=" **Mismatch**") + _POWER_ABSENCE
    assert T.mismatch_class(blob) == "SIGNAL_NET_MISMATCH"


def test_a_device_count_difference_is_refused_too():
    """Devices are as structural as nets; neither can be a pin-naming difference."""
    blob = _HEAD + _counts(6, 6, 333, 341) + _POWER_ABSENCE
    assert T.mismatch_class(blob) == "SIGNAL_NET_MISMATCH"


def test_the_rule_reads_the_numbers_not_netgens_marker():
    """The counts are the OBSERVABLE; `**Mismatch**` is wording.

    A netgen build that prints the totals without its marker must be read the
    same way — this module's own rule is to decide on an observable outcome and
    use wording only to explain. This is the case a marker-matching rule cannot
    see at all.
    """
    blob = _HEAD + _counts(365, 367, mark="") + _POWER_ABSENCE
    assert "**Mismatch**" not in blob
    assert T.mismatch_class(blob) == "SIGNAL_NET_MISMATCH"


# ── THE PARANOIA CONTROL: the benign class must NOT be blinded ─────────────
def test_a_genuine_power_pin_only_difference_is_still_benign():
    """Counts AGREE and the only evidence is absent power ports — the real
    power-unaware-netlist artifact. It must stay the waiver CANDIDATE class.
    Without this, the fix would merely fail everything and call it a cure."""
    blob = _HEAD + _counts(6, 6, 8, 8) + _POWER_ABSENCE
    assert T.mismatch_class(blob) == "POWER_PIN_ONLY"


def test_a_report_with_no_count_row_is_left_where_it_was():
    """Absence of a count row is NOT a disagreement — it is no answer at all.
    Three real reports in this fleet's corpus are exactly this shape."""
    blob = _HEAD + _POWER_ABSENCE
    assert "Number of nets" not in blob
    assert T.mismatch_class(blob) == "POWER_PIN_ONLY"


def test_a_nets_row_is_never_compared_against_a_devices_row():
    """The row-pair regex backreferences the label. 6 nets and 8 devices on a
    consistent report must not read as a 6-vs-8 disagreement."""
    blob = _HEAD + _counts(6, 6, 8, 8) + _POWER_ABSENCE
    assert _counts_disagree(blob) is False
    assert T.mismatch_class(blob) == "POWER_PIN_ONLY"


# ── THE JSON PATH: where no marker exists for any wording rule to find ─────
def test_e1_totals_that_disagree_are_a_real_defect():
    """`unmatched_nets` counts nets with NO counterpart. Two netlists can hold
    different numbers of nets while every net examined was paired — each to the
    WRONG one, which is precisely a swapped rail — and then every field the
    benign test reads is zero. The totals are the only record of it, and an E1
    report carries no `**Mismatch**` text at all."""
    obj = e1(nets={"ckt1": 365, "ckt2": 367})
    assert obj["summary"]["unmatched_nets"] == {"ckt1": 0, "ckt2": 0}
    assert T._e1_says_real_defect(obj) is True
    blob = _HEAD + _POWER_ABSENCE
    assert T.mismatch_class(blob, json_report=obj) == "SIGNAL_NET_MISMATCH"


def test_e1_totals_that_agree_do_not_disturb_the_benign_class():
    """The other direction: a well-formed E1 whose totals agree must leave the
    benign classification exactly where the text evidence puts it."""
    obj = e1()
    assert T._e1_says_real_defect(obj) is False
    blob = _HEAD + _POWER_ABSENCE
    assert T.mismatch_class(blob, json_report=obj) == "POWER_PIN_ONLY"


def test_absent_e1_totals_are_not_themselves_a_defect():
    """A shipped E1 report in this repo's own corpus carries `devices` and no
    `nets`. Absence is not disagreement — the pre-existing `unmatched_*`
    requirements already guard that report, and this rule must not widen into
    a dimension the fork did not emit."""
    obj = e1()
    del obj["summary"]["nets"]
    assert T._e1_says_real_defect(obj) is False


def test_an_unreadable_e1_total_is_never_benign():
    """Totals we can SEE and cannot READ follow this function's stated rule:
    the benign bucket is earned from counts we actually read."""
    for broken in ("CORRUPT", {"ckt1": 6}, {"ckt1": "6", "ckt2": "6"}):
        obj = e1(nets=broken)
        assert T._e1_says_real_defect(obj) is True, broken


# ── THE VERDICT IS UNTOUCHED: this is triage metadata only ─────────────────
def test_the_authoritative_verdict_is_not_moved_by_any_of_this():
    """`classify()` is the sign-off verdict; this change is sub-classification.
    Measured: classify() changed on 0 of 5025 reports in the corpus A/B."""
    differing = _HEAD + _counts(365, 367) + _POWER_ABSENCE
    agreeing = _HEAD + _counts(6, 6) + _POWER_ABSENCE
    assert T.classify(differing) == "MISMATCH"
    assert T.classify(agreeing) == "MISMATCH"
    clean = "Netlists match uniquely.\nFinal result: Circuits match uniquely.\n"
    assert T.classify(clean) == "MATCH"
    assert T.mismatch_class(clean) == "NONE"


def test_a_clean_compare_is_never_touched_by_the_count_rule():
    """The 2015-report negative control, in one assertion: a clean report
    carries agreeing totals, so the rule has nothing to fire on."""
    clean = ("Netlists match uniquely.\n" + _counts(6, 6, 8, 8)
             + "Final result: Circuits match uniquely.\n")
    assert _counts_disagree(clean) is False
    assert T.classify(clean) == "MATCH"
    assert T.mismatch_class(clean) == "NONE"
